"""启动前网络检查（docs/feature/feature-network-check.md）。

职责：按账号设置检查代理端口、出口国家与 Clash 状态，决定是否放行这一次 Grok 启动。
只读网络状态，不改任何配置；不缓存结果，每次启动重新检查。

局限：只在启动那一刻检查一次；Grok 运行中代理断开时可能直连，本模块不能、也不声称能防止。
只支持 http:// 代理做出口国家检查（CONNECT 隧道）；本轮只支持 macOS / Linux，命名管道不在此列。

只用标准库：HTTP 用 http.client（它不读环境变量代理，所以“直连”请求真的不走代理）。
"""

import http.client
import json
import os
import socket
import ssl
import sys
import time
import urllib.parse
from typing import List, NamedTuple, Optional, Tuple

from . import __version__
from .config import NETCHECK_SKIP_ENV, Account, Config

TRACE_HOST = "cli-chat-proxy.grok.com"
TRACE_PORT = 443
TRACE_PATH = "/cdn-cgi/trace"
# 单位：秒。端口与 Clash 都在本机，3 s 足够；trace 要出国际网络，单次连接 / 读写给 8 s。
# 这些是每次连接 / 读写各自的超时，四项全开时按单次计约 3 + 3 + 8 + 8 = 22 s；
# DNS 解析不受 socket 超时约束、多次读写各自计时，所以这不是严格上限。
PORT_TIMEOUT_SEC = 3
TRACE_TIMEOUT_SEC = 8
CLASH_TIMEOUT_SEC = 3
# trace 响应正常不到 1 KB；读满上限仍找不到 loc 就判失败，防止对方返回大文件。
MAX_RESPONSE_BYTES = 64 * 1024

# 跳过变量：去掉首尾空白后等于 "1" 时本次跳过并提示一行。
SKIP_ENV = NETCHECK_SKIP_ENV
SKIP_PRINT = "1"
# 测试注入（只在测试里设置）：trace 目标改为本地假服务器 HOST:PORT；TLS 校验改用指定的 CA 文件。
TEST_TRACE_ENV = "MULTI_GROK_TEST_TRACE"
TEST_CA_ENV = "MULTI_GROK_TEST_CA"

ITEM_CLASH = "clash"
ITEM_PORT = "proxy-port"
ITEM_DIRECT = "direct-country"
ITEM_PROXY = "proxy-country"

USER_AGENT = "multi-grok/" + __version__


class CheckResult(NamedTuple):
    """一项检查的结果；detail 是给人看的说明（失败时含错误分类），不含 Clash secret。"""

    item: str
    ok: bool
    detail: str
    elapsed_ms: int


class CheckFailure(Exception):
    """检查项未通过；消息即对外的失败说明。

    kind 是失败类别，只有读取 Clash 接口（read_clash_state）会设置：unreachable 连不上 / 超时，auth 被拒（401），
    http 其它非 200，not-clash 回包不像 mihomo，other 其它。启动检查只用消息文字；自动查找（clashfind）
    按 kind 写每个候选的结果说明，不靠匹配文字。
    """

    def __init__(self, message: str, kind: str = "other") -> None:
        super().__init__(message)
        self.kind = kind


def skip_requested() -> bool:
    """跳过变量去掉首尾空白后是否等于 1；其它值（含 0、空）一律不跳过。"""
    return os.environ.get(SKIP_ENV, "").strip() == SKIP_PRINT


def scope_text(account: Account) -> str:
    """提示行括号里的检查范围：basic / `strict, 国家`，开了 Clash 再加一段；只开 Clash 时只有这一段。

    Clash 段：两项都开写 `clash`，只开一项写 `clash tun` / `clash global`。
    """
    check = account.net_check
    parts = []
    if check.level == "basic":
        parts.append("basic")
    elif check.level == "strict":
        parts.extend(["strict", check.country])
    if check.clash_tun and check.clash_global:
        parts.append("clash")
    elif check.clash_tun:
        parts.append("clash tun")
    elif check.clash_global:
        parts.append("clash global")
    return ", ".join(parts)


def run_checks(config: Config, account: Account, stop_on_failure: bool) -> List[CheckResult]:
    """按 clash → proxy-port → direct-country → proxy-country 的顺序检查账号开启的项。

    stop_on_failure=True 用于放行判断：第一项失败就停，尽快拒绝启动；
    `netcheck NAME` 诊断用 False，逐项列出全部结果。不重试：这是一次性的放行判断，重试只会拖慢启动。
    """
    check = account.net_check
    steps = []
    if check.clash:
        steps.append((ITEM_CLASH, lambda: _check_clash(config, check.clash_tun, check.clash_global)))
    if check.level in ("basic", "strict"):
        steps.append((ITEM_PORT, lambda: _check_port(account.proxy)))
    if check.level == "strict":
        steps.append((ITEM_DIRECT, lambda: _check_country(None, check.country)))
        steps.append((ITEM_PROXY, lambda: _check_country(account.proxy, check.country)))
    results = []
    for item, step in steps:
        start = time.monotonic()
        try:
            detail = step()
            ok = True
        except CheckFailure as exc:
            detail = str(exc)
            ok = False
        results.append(CheckResult(item, ok, detail, int((time.monotonic() - start) * 1000)))
        if not ok and stop_on_failure:
            break
    return results


def gate(config: Config, account: Account, hint: str) -> Optional[str]:
    """启动前的放行判断：返回 None 表示放行，否则返回拒绝说明（多行，调用方打印到 stderr 并退出 5）。

    未开启检查 → 直接放行，不读跳过变量。跳过变量为 1 → 放行并提示一行，同时从 os.environ 删掉它：
    经 Python 启动 grok 的路径（run / code / login）不让 grok 继承“跳过”。
    通过时向 stderr 打一行“passed”。hint 是用户本次运行的命令，用于拼出“本次跳过”的写法。
    KeyboardInterrupt 不在这里处理，由调用方转成退出码 130。
    """
    if not account.net_check.active:
        return None
    if skip_requested():
        os.environ.pop(SKIP_ENV, None)
        _print_stderr("multi-grok: network check skipped for this run ({}=1)".format(SKIP_ENV))
        return None
    os.environ.pop(SKIP_ENV, None)  # 值不是 1（如 0）时同样不传给 grok，避免它误以为被设置过
    start = time.monotonic()
    results = run_checks(config, account, stop_on_failure=True)
    if all(result.ok for result in results):
        _print_stderr("multi-grok: network check passed for account {} ({}; {} ms)".format(
            account.name, scope_text(account), int((time.monotonic() - start) * 1000)))
        return None
    return failure_text(account, results, hint)


def failure_text(account: Account, results: List[CheckResult], hint: str) -> str:
    """拼出检查不通过时给用户看的多行说明：第一行写账号、范围和第一个失败项及原因，
    之后依次是已通过的项、本次跳过的写法和关闭检查的命令。results 里至少要有一个失败项。"""
    failed = [result for result in results if not result.ok][0]
    lines = ["multi-grok: network check failed for account {} ({}): {}: {} ({} ms)".format(
        account.name, scope_text(account), failed.item, failed.detail, failed.elapsed_ms)]
    passed = [result.item for result in results if result.ok]
    if passed:
        lines.append("  passed: " + ", ".join(passed))
    lines.append("  skip once: {}=1 {}".format(SKIP_ENV, hint))
    turn_off = "multi-grok netcheck {} off".format(account.name)
    if account.net_check.clash:
        turn_off += " --clash off"
    lines.append("  turn off:  " + turn_off)
    return "\n".join(lines)


def _print_stderr(message: str) -> None:
    """提示写 stderr：stdout 可能被调用方当作 Grok 的输出读取。"""
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- 各检查项


def _proxy_endpoint(proxy: str) -> Tuple[str, str, int]:
    """账号代理 URL → (scheme, host, port)；host 去掉 IPv6 的方括号。代理为 inherit / off 时判失败。"""
    if "://" not in proxy:
        raise CheckFailure("account proxy is {}; set one with `multi-grok proxy NAME PORT` "
                           "or turn the check off".format(proxy))
    parts = urllib.parse.urlsplit(proxy)
    # 配置里的代理已由 normalize_proxy 校验过，hostname / port 一定存在。
    return parts.scheme, parts.hostname or "", parts.port or 0


def _check_port(proxy: str) -> str:
    _, host, port = _proxy_endpoint(proxy)
    try:
        sock = socket.create_connection((host, port), PORT_TIMEOUT_SEC)
    except OSError as exc:
        raise CheckFailure("{} not reachable: {}".format(proxy, _classify(exc)))
    sock.close()
    return "{} reachable".format(proxy)


def _trace_target() -> Tuple[str, int]:
    override = os.environ.get(TEST_TRACE_ENV)
    if override:
        host, _, port = override.rpartition(":")
        return host, int(port)
    return TRACE_HOST, TRACE_PORT


def _tls_context() -> ssl.SSLContext:
    cafile = os.environ.get(TEST_CA_ENV) or None
    return ssl.create_default_context(cafile=cafile)


def _check_country(proxy: Optional[str], country: str) -> str:
    """访问 trace 地址，要求 loc 等于 country。proxy 为 None 时直连（不读任何代理设置）。"""
    host, port = _trace_target()
    context = _tls_context()
    via = "direct"
    if proxy is None:
        conn = http.client.HTTPSConnection(host, port, timeout=TRACE_TIMEOUT_SEC, context=context)
    else:
        scheme, proxy_host, proxy_port = _proxy_endpoint(proxy)
        via = "via " + proxy
        if scheme != "http":
            # https:// 代理要 TLS 套 TLS，标准库做不到；socks 本轮不支持。设置时已拒绝，这里兜住手改的配置。
            raise CheckFailure("{} proxies are not supported by the strict check; use http://".format(scheme))
        # CONNECT 隧道：TLS 按隧道主机名握手，证书校验针对 trace 主机而不是代理。
        conn = http.client.HTTPSConnection(proxy_host, proxy_port, timeout=TRACE_TIMEOUT_SEC, context=context)
        conn.set_tunnel(host, port)
    try:
        try:
            conn.request("GET", TRACE_PATH, headers={"User-Agent": USER_AGENT, "Connection": "close"})
            response = conn.getresponse()
            body = response.read(MAX_RESPONSE_BYTES)
        except (OSError, http.client.HTTPException) as exc:
            raise CheckFailure("{} ({}): {}".format(TRACE_PATH, via, _classify(exc)))
    finally:
        conn.close()
    if response.status != 200:
        raise CheckFailure("{} ({}) returned HTTP {}".format(TRACE_PATH, via, response.status))
    loc = parse_loc(body)
    if loc is None:
        raise CheckFailure("{} ({}) has no loc= line".format(TRACE_PATH, via))
    if loc.upper() != country.upper():
        raise CheckFailure("loc is {}, expected {} ({})".format(loc, country, via))
    return "loc={} ({})".format(loc, via)


def parse_loc(body: bytes) -> Optional[str]:
    """从 trace 响应（逐行 key=value）里取 loc 的值。"""
    for line in body.decode("utf-8", "replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "loc":
            return value.strip()
    return None


def _classify(exc: BaseException) -> str:
    """把底层异常转成带分类的说明；子类在前，ssl / timeout 都是 OSError 的子类。"""
    if isinstance(exc, socket.gaierror):
        return "DNS lookup failed ({})".format(exc)
    if isinstance(exc, ssl.SSLCertVerificationError):
        return ("TLS certificate verification failed ({}); Python may not find the system CA "
                "certificates".format(exc.verify_message or exc))
    if isinstance(exc, ssl.SSLError):
        return "TLS error ({})".format(exc)
    if isinstance(exc, socket.timeout):
        return "timed out"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused"
    if isinstance(exc, OSError) and str(exc).startswith("Tunnel connection failed"):
        return "proxy refused CONNECT ({})".format(exc)
    if isinstance(exc, http.client.HTTPException):
        return "bad HTTP response ({})".format(exc.__class__.__name__)
    return "{} ({})".format(exc.__class__.__name__, exc)


# ---------------------------------------------------------------- Clash / mihomo


def _check_clash(config: Config, need_tun: bool, need_global: bool) -> str:
    """按账号开启的两项判定 Clash 状态。只读，不修改 Clash 的任何设置。

    need_global：mode 必须是 global。need_tun：tun.enable 必须为真；只要求 TUN 时还要拒绝 direct 模式——
    direct 下 TUN 接住的流量照样直连出去，等于没开代理。只要求全局时不看 TUN（设置时已提醒其局限）。
    """
    api = config.clash_api
    if api is None:
        raise CheckFailure("Clash controller is not set; run `multi-grok clash-api auto` or "
                           "`multi-grok clash-api ADDRESS`")
    mode, tun_on = read_clash_state(api.address, api.secret)
    if need_global and mode != "global":
        raise CheckFailure("Clash mode is {}, expected global".format(mode))
    if need_tun and not tun_on:
        raise CheckFailure("Clash TUN is off")
    if need_tun and not need_global and mode == "direct":
        raise CheckFailure("Clash mode is direct; TUN would pass traffic through without the proxy")
    return "mode={}, tun {}".format(mode, "on" if tun_on else "off")


def read_clash_state(address: str, secret: str) -> Tuple[str, bool]:
    """GET /configs，返回 (mode 小写, tun.enable 是否为真)。

    TUN 启动失败时 mihomo 会把 tun.enable 写回 false，所以它反映的是实际状态。
    失败抛 CheckFailure 并按类别设 kind；启动检查与 `clash-api auto` 的候选验证共用这一个读法。
    """
    try:
        status, body = _clash_get(address, secret)
    except (OSError, http.client.HTTPException) as exc:
        raise CheckFailure("Clash controller {}: {}".format(address, _classify(exc)), "unreachable")
    except UnicodeError:
        # http.client 按 latin-1 编码请求头，secret 含中文等字符时在发送前就抛 UnicodeEncodeError；
        # 自动查找会从别人写的配置文件里读到这种 secret，不兜住会带 traceback 退出、后面的候选也不再试。
        # 说明里不带 secret 原文。
        raise CheckFailure("Clash controller {}: the secret has characters that cannot be sent in an HTTP "
                           "header".format(address), "other")
    if status == 401:
        raise CheckFailure("Clash controller {} rejected the request (401): secret missing or wrong; "
                           "set it with `multi-grok clash-api ADDRESS --secret-stdin`".format(address), "auth")
    if status != 200:
        raise CheckFailure("Clash controller {} returned HTTP {}".format(address, status), "http")
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError:
        data = None
    mode = data.get("mode") if isinstance(data, dict) else None
    tun = data.get("tun") if isinstance(data, dict) else None
    if not isinstance(mode, str) or not isinstance(tun, dict) or "enable" not in tun:
        raise CheckFailure("Clash controller {} gave an unexpected response (no mode / tun.enable); "
                           "is it a Clash / mihomo controller?".format(address), "not-clash")
    return mode.lower(), tun.get("enable") is True


def _clash_get(address: str, secret: str) -> Tuple[int, bytes]:
    headers = {"Connection": "close", "User-Agent": USER_AGENT}
    if address.startswith("unix:"):
        conn = _UnixHTTPConnection(address[len("unix:"):], CLASH_TIMEOUT_SEC)  # type: http.client.HTTPConnection
    else:
        parts = urllib.parse.urlsplit(address)
        conn = http.client.HTTPConnection(parts.hostname or "", parts.port, timeout=CLASH_TIMEOUT_SEC)
        # mihomo 只在 TCP 接口上校验 secret；unix 套接字不鉴权，不发送。
        if secret:
            headers["Authorization"] = "Bearer " + secret
    try:
        conn.request("GET", "/configs", headers=headers)
        response = conn.getresponse()
        return response.status, response.read(MAX_RESPONSE_BYTES)
    finally:
        conn.close()


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock
