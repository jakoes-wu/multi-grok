"""docs/feature/feature-network-check.md §8：启动前网络检查的回归。

本地起假服务代替真实网络：TLS 假 trace、假 CONNECT 代理、假 mihomo 外部控制接口（TCP 与 unix 套接字）。
全部在临时 HOME 下跑，不碰真实 ~/.grok、~/.grok-accounts 与本机 Clash（进程列表与 Clash Verge 配置都用测试注入）。
"""

import http.server
import json
import os
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock

import helpers
from multi_grok import clashfind, cli, netcheck
from multi_grok.cli import run
from multi_grok.config import ConfigError, config_path, load_config, parse_config
from multi_grok.launcher import render
from multi_grok.platform import split_ps_command

DATA = os.path.join(helpers.ROOT, "tests", "data")
CERT = os.path.join(DATA, "netcheck-cert.pem")
KEY = os.path.join(DATA, "netcheck-key.pem")
SKIP = netcheck.SKIP_ENV


def capture(argv, stdin=None):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        if stdin is None:
            code = run(list(argv))
        else:
            with mock.patch("sys.stdin", StringIO(stdin)):
                code = run(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


# ---------------------------------------------------------------- 假服务


class _Quiet(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):  # 不往测试输出里写访问日志；unix 套接字下 address_string 也会出错
        pass

    def reply(self, status, body):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _TraceHandler(_Quiet):
    def do_GET(self):
        server = self.server
        if self.path != "/cdn-cgi/trace":
            self.reply(404, "not found")
        elif server.status != 200:
            self.reply(server.status, "error")
        else:
            lines = ["fl=1", "h=cli-chat-proxy.grok.com", "ip=203.0.113.9"]
            if server.loc is not None:
                lines.append("loc=" + server.loc)
            self.reply(200, "\n".join(lines) + "\n")


class _ThreadingHTTP(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class TraceServer(object):
    """本地 TLS 假 trace 服务；loc / status 可随时改。"""

    def __init__(self):
        self.httpd = _ThreadingHTTP(("127.0.0.1", 0), _TraceHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(CERT, KEY)
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.httpd.loc = "US"
        self.httpd.status = 200
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _relay(left, right):
    def pump(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    thread = threading.Thread(target=pump, args=(right, left), daemon=True)
    thread.start()
    pump(left, right)
    thread.join(10)
    left.close()
    right.close()


class _ConnectHandler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                return  # 端口检查：连上就关
            head += chunk
        line = head.split(b"\r\n", 1)[0].decode("ascii")
        method, target, _ = line.split(" ", 2)
        self.server.targets.append(target)
        if method != "CONNECT" or self.server.refuse:
            sock.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        host, _, port = target.rpartition(":")
        upstream = socket.create_connection((host, int(port)), 5)
        sock.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        _relay(sock, upstream)


class _ThreadingTCP(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class ProxyServer(object):
    """假 http 代理（CONNECT）；refuse 为真时回 403，targets 记录请求目标。"""

    def __init__(self):
        self.server = _ThreadingTCP(("127.0.0.1", 0), _ConnectHandler)
        self.server.targets = []
        self.server.refuse = False
        self.port = self.server.server_address[1]
        self.url = "http://127.0.0.1:{}".format(self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class _MihomoHandler(_Quiet):
    def do_GET(self):
        server = self.server
        if server.secret and self.headers.get("Authorization") != "Bearer " + server.secret:
            self.reply(401, '{"message":"Unauthorized"}')
            return
        if self.path != "/configs":
            self.reply(404, "{}")
            return
        self.reply(200, json.dumps(server.payload))


class MihomoServer(object):
    """假 mihomo 外部控制接口（GET /configs）；unix_path 不为空时监听 unix 套接字。"""

    def __init__(self, unix_path=None):
        if unix_path:
            class _UnixHTTP(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
                daemon_threads = True
            self.server = _UnixHTTP(unix_path, _MihomoHandler)
            self.address = "unix:" + unix_path
        else:
            self.server = _ThreadingHTTP(("127.0.0.1", 0), _MihomoHandler)
            self.address = "127.0.0.1:{}".format(self.server.server_address[1])
        self.server.secret = ""
        self.server.payload = {"mode": "global", "tun": {"enable": True, "stack": "mixed"}, "port": 7890}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def state(self, mode, tun):
        self.server.payload = {"mode": mode, "tun": {"enable": tun}}

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def make_config(proxy="inherit", level="off", country="US", clash_tun=False, clash_global=False, clash_api=None):
    data = {"version": 1, "accounts": {"home": {"proxy": proxy, "net_check": {
        "level": level, "country": country, "clash_tun": clash_tun, "clash_global": clash_global}}}}
    if clash_api is not None:
        data["clash_api"] = clash_api
    return parse_config(json.dumps(data), "test")


# ---------------------------------------------------------------- 进程内：检查项本身


class ChecksTest(unittest.TestCase):
    """N7–N11：检查项本身。假服务都在本机，trace 目标经测试注入改到本地。"""

    def setUp(self):
        self.ctx = helpers.isolated_home()
        self.ctx.__enter__()
        self.trace = TraceServer()
        os.environ[netcheck.TEST_TRACE_ENV] = "127.0.0.1:{}".format(self.trace.port)
        os.environ[netcheck.TEST_CA_ENV] = CERT

    def tearDown(self):
        self.trace.stop()
        self.ctx.__exit__(None, None, None)

    def results(self, config):
        return {result.item: result for result in netcheck.run_checks(config, config.accounts["home"], False)}

    def test_port_open_and_refused(self):
        proxy = ProxyServer()
        try:
            self.assertTrue(self.results(make_config(proxy.url, "basic"))["proxy-port"].ok)
        finally:
            proxy.stop()
        dead = self.results(make_config("http://127.0.0.1:{}".format(closed_port()), "basic"))["proxy-port"]
        self.assertFalse(dead.ok)
        self.assertIn("connection refused", dead.detail)

    def test_inherit_proxy_fails_at_launch(self):
        result = self.results(make_config("inherit", "basic"))["proxy-port"]
        self.assertFalse(result.ok)
        self.assertIn("account proxy is inherit", result.detail)

    def test_direct_country(self):
        proxy = ProxyServer()
        try:
            config = make_config(proxy.url, "strict")
            self.assertTrue(self.results(config)["direct-country"].ok)
            self.trace.httpd.loc = "JP"
            failed = self.results(config)["direct-country"]
            self.assertFalse(failed.ok)
            self.assertIn("loc is JP, expected US (direct)", failed.detail)
            self.trace.httpd.loc = None
            self.assertIn("has no loc= line", self.results(config)["direct-country"].detail)
            self.trace.httpd.loc = "US"
            self.trace.httpd.status = 500
            self.assertIn("returned HTTP 500", self.results(config)["direct-country"].detail)
        finally:
            proxy.stop()

    def test_certificate_not_trusted(self):
        os.environ.pop(netcheck.TEST_CA_ENV)
        result = self.results(make_config("http://127.0.0.1:1", "strict"))["direct-country"]
        self.assertFalse(result.ok)
        self.assertIn("TLS certificate verification failed", result.detail)

    def test_via_http_proxy(self):
        proxy = ProxyServer()
        try:
            config = make_config(proxy.url, "strict")
            result = self.results(config)["proxy-country"]
            self.assertTrue(result.ok, result.detail)
            self.assertIn("127.0.0.1:{}".format(self.trace.port), proxy.server.targets)
            proxy.server.refuse = True
            refused = self.results(config)["proxy-country"]
            self.assertFalse(refused.ok)
            self.assertIn("proxy refused CONNECT", refused.detail)
        finally:
            proxy.stop()

    def test_https_proxy_fails_at_launch(self):
        result = self.results(make_config("https://127.0.0.1:1", "strict"))["proxy-country"]
        self.assertFalse(result.ok)
        self.assertIn("not supported by the strict check", result.detail)

    def test_clash_states(self):
        mihomo = MihomoServer()
        try:
            api = {"address": mihomo.address}
            cases = [
                # (mode, tun, need_tun, need_global, ok)
                ("global", True, True, True, True),
                ("rule", True, True, True, False),
                ("global", False, True, True, False),
                ("rule", True, True, False, True),
                ("direct", True, True, False, False),
                ("global", False, False, True, True),
                ("rule", False, False, True, False),
            ]
            for mode, tun, need_tun, need_global, ok in cases:
                mihomo.state(mode, tun)
                config = make_config(clash_tun=need_tun, clash_global=need_global, clash_api=api)
                result = self.results(config)["clash"]
                self.assertEqual(result.ok, ok, (mode, tun, need_tun, need_global, result.detail))
            mihomo.server.secret = "s3cret"
            denied = self.results(make_config(clash_tun=True, clash_api=api))["clash"]
            self.assertIn("401", denied.detail)
            self.assertNotIn("s3cret", denied.detail)
            mihomo.state("rule", True)
            allowed = self.results(make_config(clash_tun=True, clash_api=dict(api, secret="s3cret")))["clash"]
            self.assertTrue(allowed.ok, allowed.detail)
            self.assertNotIn("s3cret", allowed.detail)
        finally:
            mihomo.stop()

    def test_clash_not_set_or_down(self):
        self.assertIn("not set", self.results(make_config(clash_tun=True))["clash"].detail)
        down = self.results(make_config(clash_tun=True, clash_api={
            "address": "127.0.0.1:{}".format(closed_port())}))["clash"]
        self.assertIn("connection refused", down.detail)

    def test_clash_unix_socket(self):
        folder = tempfile.mkdtemp(prefix="mg-sock-", dir="/tmp")  # unix 套接字路径有长度上限，放短路径下
        path = os.path.join(folder, "m.sock")
        mihomo = MihomoServer(path)
        try:
            mihomo.server.secret = ""
            result = self.results(make_config(clash_tun=True, clash_api={"address": mihomo.address}))["clash"]
            self.assertTrue(result.ok, result.detail)
        finally:
            mihomo.stop()
            os.unlink(path)
            os.rmdir(folder)

    def test_stop_on_failure_and_failure_text(self):
        config = make_config("http://127.0.0.1:{}".format(closed_port()), "strict", clash_tun=True)
        account = config.accounts["home"]
        results = netcheck.run_checks(config, account, True)
        self.assertEqual([result.item for result in results], ["clash"])
        text = netcheck.failure_text(account, results, "grok-home")
        self.assertIn("network check failed for account home (strict, US, clash tun): clash:", text)
        self.assertIn("skip once: MULTI_GROK_SKIP_NETCHECK=1 grok-home", text)
        self.assertIn("turn off:  multi-grok netcheck home off --clash off", text)

    def test_scope_text(self):
        def scope(**kwargs):
            config = make_config("http://127.0.0.1:1", **kwargs)
            return netcheck.scope_text(config.accounts["home"])
        self.assertEqual(scope(level="basic"), "basic")
        self.assertEqual(scope(level="strict", country="JP"), "strict, JP")
        self.assertEqual(scope(clash_tun=True, clash_global=True), "clash")
        self.assertEqual(scope(level="basic", clash_global=True), "basic, clash global")


# ---------------------------------------------------------------- 配置与解析


class ConfigTest(unittest.TestCase):
    def test_inactive_account_unchanged(self):
        # N1：未开启时 config.json 不写 net_check / clash_api，启动命令与不传参数时逐字节相同。
        config = parse_config(json.dumps({"version": 1, "accounts": {"home": {}}}), "t")
        data = config.to_dict()
        self.assertNotIn("net_check", data["accounts"]["home"])
        self.assertNotIn("clash_api", data)
        plain = render("home", "~/.grok-accounts", "inherit", {}, [])
        self.assertEqual(render("home", "~/.grok-accounts", "inherit", {}, [], net_check=False), plain)
        self.assertNotIn("netgate", plain)
        checked = render("home", "~/.grok-accounts", "inherit", {}, [], net_check=True)
        self.assertIn("(cd / && exec multi-grok netgate home) || exit $?", checked)
        self.assertEqual(os.system("sh -n /dev/stdin <<'EOF'\n{}EOF".format(checked)), 0)

    def test_parse_rules(self):
        # N15d
        def parsed(net_check=None, clash_api=None):
            account = {} if net_check is None else {"net_check": net_check}
            data = {"version": 1, "accounts": {"home": account}}
            if clash_api is not None:
                data["clash_api"] = clash_api
            return parse_config(json.dumps(data), "t")
        check = parsed({"level": "basic"}).accounts["home"].net_check
        self.assertEqual((check.level, check.country, check.clash_tun, check.clash_global), ("basic", "US", False, False))
        self.assertEqual(parsed({"level": "basic"}).to_dict()["accounts"]["home"]["net_check"],
                         {"level": "basic", "country": "US", "clash_tun": False, "clash_global": False})
        for bad in ({"level": "max"}, {"clash": True}, {"country": "USA"}, {"clash_tun": "yes"}, []):
            with self.assertRaises(ConfigError, msg=bad):
                parsed(bad)
        for bad in ({}, {"address": "a/b"}, {"address": "127.0.0.1:1", "secret": 1},
                    {"address": "127.0.0.1:1", "x": 1}, "127.0.0.1:1"):
            with self.assertRaises(ConfigError, msg=bad):
                parsed(clash_api=bad)
        api = parsed(clash_api={"address": "127.0.0.1:9097"}).clash_api
        self.assertEqual(api.address, "http://127.0.0.1:9097")

    def test_split_ps_and_yaml(self):
        # N21
        self.assertEqual(split_ps_command("/a b/mihomo -d /x y -ext-ctl-unix /s.sock"),
                         ["/a b/mihomo", "-d", "/x y", "-ext-ctl-unix", "/s.sock"])
        self.assertEqual(clashfind._yaml_scalar('"a\\"b" # c'), 'a"b')
        self.assertEqual(clashfind._yaml_scalar("'it''s'"), "it's")
        self.assertEqual(clashfind._yaml_scalar("127.0.0.1:9097 # local"), "127.0.0.1:9097")
        self.assertEqual(clashfind._yaml_scalar("# only"), "")


# ---------------------------------------------------------------- 命令行


FAKE_GROK = """#!/bin/sh
{
  printf 'args:'
  printf ' %s' "$@"
  printf '\\n'
  printf 'skip=%s\\n' "${MULTI_GROK_SKIP_NETCHECK-unset}"
} > "$MULTI_GROK_TEST_OUTPUT"
"""


class CliBase(unittest.TestCase):
    def setUp(self):
        self.ctx = helpers.isolated_home()
        self.env = self.ctx.__enter__()
        self.output = os.path.join(self.env["tmp"], "grok-out")
        os.environ["MULTI_GROK_TEST_OUTPUT"] = self.output
        helpers.write_executable(os.path.join(self.env["bin"], "grok"), FAKE_GROK)
        self.trace = TraceServer()
        os.environ[netcheck.TEST_TRACE_ENV] = "127.0.0.1:{}".format(self.trace.port)
        os.environ[netcheck.TEST_CA_ENV] = CERT
        self.write_processes([])  # 默认空进程列表：不去找本机真实运行的 Clash
        self.assertEqual(capture(["add", "home"])[0], 0)

    def tearDown(self):
        self.trace.stop()
        self.ctx.__exit__(None, None, None)

    def launcher(self):
        with open(os.path.join(self.env["bin"], "grok-home"), encoding="utf-8") as handle:
            return handle.read()

    def saved(self):
        with open(config_path(), encoding="utf-8") as handle:
            return json.load(handle)

    def called(self):
        return os.path.exists(self.output)

    def write_processes(self, processes):
        path = os.path.join(self.env["tmp"], "processes.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(processes, handle)
        os.environ["MULTI_GROK_TEST_CLASH_PROCESSES"] = path
        os.environ[clashfind.TEST_CLASH_CONFIGS_ENV] = ""

    def enable_dead_check(self):
        """开启 basic 检查，代理指向一个关掉的端口：检查必然失败。"""
        self.assertEqual(capture(["proxy", "home", str(closed_port())])[0], 0)
        code, _out, err = capture(["netcheck", "home", "basic"])
        self.assertEqual(code, 0, err)


class NetCheckCommandTest(CliBase):
    def test_validation(self):
        before = self.saved()
        self.assertEqual(capture(["netcheck", "home", "basic"])[0], 2)  # N2
        self.assertEqual(self.saved(), before)
        self.assertEqual(capture(["proxy", "home", "https://127.0.0.1:7890"])[0], 0)
        self.assertEqual(capture(["netcheck", "home", "strict"])[0], 2)  # N4
        self.assertEqual(capture(["netcheck", "home", "--clash", "on", "--clash-tun", "off"])[0], 2)  # N5
        self.assertEqual(capture(["netcheck", "home", "--country", "USA"])[0], 2)

    def test_enable_rewrites_launcher_and_disable_restores(self):
        # N3
        plain = self.launcher()
        self.assertEqual(capture(["proxy", "home", "7890"])[0], 0)
        code, out, err = capture(["netcheck", "home", "strict", "--country", "jp"])
        self.assertEqual(code, 0, err)
        self.assertIn("network check of home: strict, JP", out)
        check = self.saved()["accounts"]["home"]["net_check"]
        self.assertEqual((check["level"], check["country"]), ("strict", "JP"))
        self.assertIn("netgate home", self.launcher())
        code, out, err = capture(["netcheck", "home", "off"])
        self.assertEqual(code, 0, err)
        self.assertIn("network check of home: off", out)
        self.assertNotIn("net_check", self.saved()["accounts"]["home"])
        self.assertNotIn("netgate", self.launcher())
        self.assertEqual(capture(["proxy", "home", "inherit"])[0], 0)
        self.assertEqual(self.launcher(), plain)

    def test_country_warning_and_global_without_tun(self):
        code, _out, err = capture(["netcheck", "home", "--country", "JP"])
        self.assertEqual(code, 0)
        self.assertIn("country is only used by strict", err)
        self.assertIn("not saved", err)
        code, _out, err = capture(["netcheck", "home", "--clash-global", "on"])  # N6
        self.assertEqual(code, 0, err)
        self.assertIn("global mode without TUN", err)
        self.assertIn("Clash controller is not set", err)
        self.assertTrue(self.saved()["accounts"]["home"]["net_check"]["clash_global"])

    def test_show(self):
        # N16
        code, out, _err = capture(["netcheck", "home"])
        self.assertEqual((code, out.strip()), (0, "network check is off for home"))
        self.enable_dead_check()
        code, out, _err = capture(["netcheck", "home"])
        self.assertEqual(code, 5)
        self.assertIn("network check of home: basic", out)
        self.assertIn("fail proxy-port", out)

    def test_list_stale_then_apply(self):
        # N15c
        data = self.saved()
        data["accounts"]["home"]["proxy"] = "http://127.0.0.1:7890"
        data["accounts"]["home"]["net_check"] = {"level": "basic"}
        with open(config_path(), "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        self.assertIn("launcher=stale", capture(["list"])[1])
        self.assertEqual(capture(["apply"])[0], 0)
        self.assertIn("launcher=ok", capture(["list"])[1])
        self.assertIn("netgate home", self.launcher())

    def test_clash_api_manual(self):
        # N17
        code, _out, err = capture(["clash-api", "127.0.0.1:9097", "--secret-stdin"], stdin="s3cret\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.saved()["clash_api"], {"address": "http://127.0.0.1:9097", "secret": "s3cret"})
        code, out, _err = capture(["clash-api"])
        self.assertEqual(out.strip(), "Clash controller: http://127.0.0.1:9097 (secret set)")
        self.assertNotIn("s3cret", out)
        self.assertEqual(capture(["clash-api", "a/b"])[0], 2)
        self.assertEqual(capture(["clash-api", "off", "--secret-stdin"])[0], 2)
        self.assertEqual(capture(["clash-api", "127.0.0.1:9097", "--secret-stdin"], stdin="\n")[0], 2)
        self.assertEqual(capture(["clash-api", "\\\\.\\pipe\\mihomo"])[0], 2)
        self.assertEqual(capture(["clash-api", "off"])[0], 0)
        self.assertNotIn("clash_api", self.saved())
        self.assertEqual(capture(["clash-api"])[1].strip(), "Clash controller is not set")

    def test_clash_api_auto(self):
        # N18
        mihomo = MihomoServer()
        mihomo.server.secret = "s3"
        try:
            self.write_processes([["/usr/bin/mihomo", "-ext-ctl", mihomo.address, "-secret", "s3"],
                                  ["/bin/zsh"]])
            code, _out, err = capture(["clash-api", "auto"])
            self.assertEqual(code, 0, err)
            self.assertIn("found Clash controller http://" + mihomo.address, err)
            self.assertNotIn("s3", err.replace(mihomo.address, ""))
            self.assertEqual(self.saved()["clash_api"], {"address": "http://" + mihomo.address, "secret": "s3"})
        finally:
            mihomo.stop()
        self.write_processes([])
        code, _out, err = capture(["clash-api", "auto"])
        self.assertEqual(code, 1)
        self.assertIn("no working Clash", err)
        self.assertEqual(capture(["clash-api", "auto", "--secret-stdin"], stdin="x\n")[0], 2)

    def test_auto_config_file_and_remote_skipped(self):
        mihomo = MihomoServer()
        try:
            conf = os.path.join(self.env["tmp"], "verge.yaml")
            with open(conf, "w", encoding="utf-8") as handle:
                handle.write("mixed-port: 7897\nexternal-controller: '{}'\nsecret: ''\n".format(mihomo.address))
            self.write_processes([["/opt/clash", "-ext-ctl", "192.0.2.1:9090"]])
            os.environ[clashfind.TEST_CLASH_CONFIGS_ENV] = conf
            code, _out, err = capture(["clash-api", "auto", "--verbose"])
            self.assertEqual(code, 0, err)
            self.assertIn("not a local address, skipped", err)
            self.assertEqual(self.saved()["clash_api"]["address"], "http://" + mihomo.address)
        finally:
            mihomo.stop()

    def test_netcheck_clash_finds_controller(self):
        # N19
        mihomo = MihomoServer()
        try:
            self.write_processes([["mihomo", "-ext-ctl", mihomo.address]])
            code, _out, err = capture(["netcheck", "home", "--clash", "on"])
            self.assertEqual(code, 0, err)
            self.assertEqual(self.saved()["clash_api"]["address"], "http://" + mihomo.address)
        finally:
            mihomo.stop()
        capture(["clash-api", "off"])
        capture(["netcheck", "home", "--clash", "off"])
        self.write_processes([])
        code, _out, err = capture(["netcheck", "home", "--clash-tun", "on"])
        self.assertEqual(code, 0, err)
        self.assertIn("launches of home will be refused", err)
        self.assertTrue(self.saved()["accounts"]["home"]["net_check"]["clash_tun"])

    def test_find_does_not_overwrite_concurrent_change(self):
        # N19：查找期间另一条命令改了代理，结果保留该改动。
        def finder(verbose, required):
            self.assertEqual(capture(["proxy", "home", "7899"])[0], 0)
            return None, 1
        with mock.patch.object(cli, "_find_clash", side_effect=finder):
            code, _out, err = capture(["netcheck", "home", "--clash-tun", "on"])
        self.assertEqual(code, 0, err)
        account = self.saved()["accounts"]["home"]
        self.assertEqual(account["proxy"], "http://127.0.0.1:7899")
        self.assertTrue(account["net_check"]["clash_tun"])

    def test_apply_ignores_invalid_clash_api_in_file(self):
        data = self.saved()
        data["clash_api"] = {"address": "a/b", "extra": 1}
        path = os.path.join(self.env["tmp"], "bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        code, _out, err = capture(["apply", "-f", path])
        self.assertEqual(code, 0, err)
        self.assertIn("is ignored", err)
        self.assertNotIn("clash_api", self.saved())

    def test_find_when_clash_already_on(self):
        # 账号原本开着 Clash 检测、接口未设置：本次只改档位也要自动查找。
        self.write_processes([])
        capture(["netcheck", "home", "--clash-tun", "on"])
        mihomo = MihomoServer()
        try:
            self.write_processes([["mihomo", "-ext-ctl", mihomo.address]])
            self.assertEqual(capture(["proxy", "home", "7890"])[0], 0)
            code, _out, err = capture(["netcheck", "home", "basic"])
            self.assertEqual(code, 0, err)
            self.assertEqual(self.saved()["clash_api"]["address"], "http://" + mihomo.address)
        finally:
            mihomo.stop()

    def test_apply_ignores_clash_api_in_file(self):
        # N20
        capture(["clash-api", "127.0.0.1:9097"])
        data = self.saved()
        data["clash_api"] = {"address": "127.0.0.1:1"}
        path = os.path.join(self.env["tmp"], "other.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        code, _out, err = capture(["apply", "-f", path])
        self.assertEqual(code, 0, err)
        self.assertIn("'clash_api' in {} is ignored".format(path), err)
        self.assertEqual(self.saved()["clash_api"]["address"], "http://127.0.0.1:9097")


class GateTest(CliBase):
    """N12–N15b：启动入口在检查失败时拒绝，跳过变量与未开启账号照常运行。"""

    def wrapper(self):
        """PATH 最前面放指向源码的 multi-grok，不用本机已装的版本。"""
        helpers.write_executable(os.path.join(self.env["bin"], "multi-grok"),
                                 "#!/bin/sh\nPYTHONPATH={} exec {} -m multi_grok \"$@\"\n".format(
                                     os.path.join(helpers.ROOT, "src"), sys.executable))

    def launch(self, command, extra_env=None):
        env = dict(os.environ, **(extra_env or {}))
        proc = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, cwd=self.env["tmp"], timeout=60)
        return proc.returncode, proc.stderr.decode("utf-8", "replace")

    def test_launcher_end_to_end(self):
        # N12
        self.wrapper()
        self.enable_dead_check()
        launcher = os.path.join(self.env["bin"], "grok-home")
        code, err = self.launch([launcher, "hi"])
        self.assertEqual(code, 5, err)
        self.assertIn("network check failed for account home (basic): proxy-port", err)
        self.assertIn("skip once: MULTI_GROK_SKIP_NETCHECK=1 grok-home", err)
        self.assertFalse(self.called())
        code, err = self.launch([launcher, "hi"], {SKIP: "1"})
        self.assertEqual(code, 0, err)
        self.assertIn("network check skipped for this run", err)
        with open(self.output, encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("args: hi", body)
        self.assertIn("skip=unset", body)
        proxy = ProxyServer()
        try:
            self.assertEqual(capture(["proxy", "home", proxy.url])[0], 0)
            os.unlink(self.output)
            code, err = self.launch([launcher])
            self.assertEqual(code, 0, err)
            self.assertIn("network check passed for account home (basic;", err)
            self.assertTrue(self.called())
        finally:
            proxy.stop()

    def test_launcher_without_multi_grok(self):
        # N13：PATH 里只有假 grok，没有 multi-grok。
        self.enable_dead_check()
        path = self.env["bin"] + os.pathsep + "/usr/bin:/bin"
        code, err = self.launch([os.path.join(self.env["bin"], "grok-home")], {"PATH": path})
        self.assertEqual(code, 127, err)
        self.assertIn("multi-grok not found in PATH", err)
        self.assertFalse(self.called())

    def test_python_entries_refused(self):
        # N14 / N15a
        helpers.write_executable(os.path.join(self.env["bin"], "code"), FAKE_GROK)
        self.enable_dead_check()
        for argv in (["run", "home", "--", "grok", "x"], ["run", "home", "--"], ["login", "home"],
                     ["code", "home"]):
            code, _out, err = capture(argv)
            self.assertEqual(code, 5, (argv, err))
            self.assertIn("network check failed", err)
            self.assertFalse(self.called(), argv)
        code, _out, err = capture(["login", "home"])
        self.assertIn("skip once: MULTI_GROK_SKIP_NETCHECK=1 multi-grok login home", err)
        os.environ[SKIP] = "1"
        code, _out, err = capture(["run", "home", "--", "grok", "x"])
        self.assertEqual(code, 0, err)
        self.assertNotIn(SKIP, os.environ)
        with open(self.output, encoding="utf-8") as handle:
            self.assertIn("skip=unset", handle.read())

    def test_inactive_and_unchecked_entries_run(self):
        # N14 未开启照常；N15 mcp / usage 不检查
        code, _out, err = capture(["run", "home", "--", "grok", "x"])
        self.assertEqual(code, 0, err)
        self.enable_dead_check()
        for argv in (["mcp", "home", "list"], ["usage", "home", "SESSION"]):
            if self.called():
                os.unlink(self.output)
            code, _out, err = capture(argv)
            self.assertEqual(code, 0, (argv, err))
            self.assertTrue(self.called(), argv)

    def test_grok_auto(self):
        # N15b
        self.wrapper()
        self.assertEqual(capture(["add", "work"])[0], 0)
        self.enable_dead_check()
        project = os.path.join(self.env["tmp"], "proj")
        os.makedirs(project)
        self.assertEqual(capture(["route", project, "home"])[0], 0)
        self.assertEqual(capture(["route", "--default", "work"])[0], 0)
        auto = os.path.join(self.env["bin"], "grok-auto")
        proc = subprocess.run([auto], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=dict(os.environ), cwd=project, timeout=60)
        self.assertEqual(proc.returncode, 5, proc.stderr)
        self.assertFalse(self.called())
        code, err = self.launch([auto])
        self.assertEqual(code, 0, err)
        self.assertTrue(self.called())

    def test_netgate_usage(self):
        self.assertEqual(capture(["netgate"])[0], 2)
        self.assertEqual(capture(["netgate", "nobody"])[0], 1)
        self.assertEqual(capture(["netgate", "home"])[0], 0)


if __name__ == "__main__":
    unittest.main()
