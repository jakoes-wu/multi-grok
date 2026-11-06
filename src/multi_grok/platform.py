"""默认路径与进程枚举。一期只支持 macOS 与 Linux。"""

import json
import os
import subprocess
import sys
from typing import List

# 在 shell 里导出后，会让尚未登录的账号共用同一把钥匙，或把登录指到别的文件。
# 启动命令一律清除。依据见用户指南：02-authentication.md（XAI_API_KEY 在没有会话令牌时生效）、
# 11-custom-models.md（GROK_CODE_XAI_API_KEY / GROK_AUTH 是凭据变量）、
# 26-config-reference.md（GROK_AUTH_PATH 与 GROK_AUTH 可以提供账号目录以外的登录身份）、
# 05-configuration.md（GROK_CONFIG 会盖过账号自己的 config.toml 里的一部分设置）。
ISOLATION_ENV = (
    "XAI_API_KEY",
    "GROK_CODE_XAI_API_KEY",
    "GROK_AUTH",
    "GROK_AUTH_PATH",
    "GROK_CONFIG",
    "GROK_CONFIG_PATH",
)


def is_supported() -> bool:
    return sys.platform == "darwin" or sys.platform.startswith("linux")


def default_root() -> str:
    """每个账号一个子目录。用户指定的位置，不使用 ~/.grok。"""
    return "~/.grok-accounts"


def default_grok_home() -> str:
    """未设置 GROK_HOME 时 Grok 自己的数据目录。本工具不迁移、不替换它。"""
    return "~/.grok"


def default_bin_dir() -> str:
    return "~/.local/bin"


def state_dir() -> str:
    """config.json 与锁文件的位置。设置了 XDG_CONFIG_HOME 时以它为准。"""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = xdg if xdg and os.path.isabs(xdg) else os.path.expanduser("~/.config")
    return os.path.join(base, "multi-grok")


# 测试用：指向一个 JSON 文件，内容是参数列表的数组（[["mihomo", "-ext-ctl", "127.0.0.1:9097"], ...]），
# 代替真实进程列表，开发机上真实运行的 Clash 不会被找到并写进测试配置。
TEST_CLASH_PROCESSES_ENV = "MULTI_GROK_TEST_CLASH_PROCESSES"


def list_process_argv() -> List[List[str]]:
    """列出本机所有进程的参数列表（参数 0 是程序），供 `clash-api auto` 找 Clash。

    读不到参数的进程（权限、已退出）跳过。枚举本身失败（ps 不存在、超时、退出码非 0）时抛 OSError，
    由调用方决定是否继续。只读，不影响任何进程。
    """
    listing = os.environ.get(TEST_CLASH_PROCESSES_ENV)
    if listing:
        with open(listing, "r", encoding="utf-8") as handle:
            items = json.load(handle)
        return [[str(part) for part in item] for item in items if isinstance(item, list) and item]
    if sys.platform.startswith("linux") and os.path.isdir("/proc/self"):
        return _proc_argv()
    return _ps_argv()


def _proc_argv() -> List[List[str]]:
    """Linux：/proc/<pid>/cmdline 以 NUL 分隔，就是精确的参数列表。"""
    result = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join("/proc", entry, "cmdline"), "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        argv = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if argv:
            result.append(argv)
    return result


def _ps_argv() -> List[List[str]]:
    """macOS：`ps -axww -o command=` 列出全部进程（含其它用户的）。"""
    try:
        proc = subprocess.run(["ps", "-axww", "-o", "command="], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=30)
    except subprocess.TimeoutExpired:
        raise OSError("ps did not list processes within 30 seconds")
    if proc.returncode != 0:
        raise OSError("ps exited with code {}".format(proc.returncode))
    lines = proc.stdout.decode("utf-8", "replace").splitlines()
    return [argv for argv in (split_ps_command(line) for line in lines) if argv]


def split_ps_command(line: str) -> List[str]:
    """把 ps 的一行命令拆成参数。

    ps 输出不带引号，含空格的路径（如 /Library/Application Support/…）无法准确还原，按约定近似：
    第一个以 `-` 开头的片段之前整段是程序；之后不以 `-` 开头的连续片段并回前一个值。
    例：`/a b/mihomo -d /x y -ext-ctl-unix /s.sock` → [`/a b/mihomo`, `-d`, `/x y`, `-ext-ctl-unix`, `/s.sock`]。
    拆错只会让候选地址不对，候选都要实际连一次验证，不会写错配置。
    """
    tokens = line.strip().split(" ")
    if not tokens or not tokens[0]:
        return []
    first_flag = next((index for index, token in enumerate(tokens) if index and token.startswith("-")), len(tokens))
    argv = [" ".join(tokens[:first_flag])]
    for token in tokens[first_flag:]:
        if not token and argv[-1].startswith("-"):
            continue  # 参数名后多出的空格不算进值；值内部的连续空格（路径里的双空格）照常保留
        if token.startswith("-") or argv[-1].startswith("-"):
            argv.append(token)
        else:
            argv[-1] += " " + token
    return argv
