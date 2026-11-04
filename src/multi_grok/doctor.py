"""只读检查。不修改配置，不打开 auth.json。"""

import os
import shutil
from typing import List, Tuple

from .accounts import describe, directory_mode, plan
from .actions import CONFLICT, has_conflict
from .config import Config, config_path
from .fsutil import expand
from .launcher import LAUNCHER_PREFIX, launcher_path, trailing_option_warning
from .platform import ISOLATION_ENV, is_supported
from .routes import router_status


def diagnose(config: Config) -> Tuple[List[str], List[str]]:
    """返回 (problems, warnings)。problems 非空时 doctor 以退出码 1 结束。"""
    problems: List[str] = []
    warnings: List[str] = []
    if not is_supported():
        problems.append("this operating system is not supported; use macOS or Linux")
    if shutil.which("grok") is None:
        problems.append("grok is not on PATH")
    bin_dir = expand(config.bin_dir)
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if bin_dir not in path_parts:
        problems.append("{} is not on PATH, so grok-<name> will not run. "
                        "Add it yourself; multi-grok does not edit shell startup files. "
                        "export PATH=\"{}:$PATH\"".format(bin_dir, bin_dir))
    for key in ISOLATION_ENV:
        if os.environ.get(key):
            warnings.append("{} is set in this shell. grok-<name> unsets it; a bare `grok` still sees it".format(key))
    actions = plan(config)
    if has_conflict(actions):
        for action in actions:
            if action.status == CONFLICT:
                problems.append("{} {}: {}".format(action.kind, action.path, action.reason))
    else:
        drifted = [action for action in actions if action.changes]
        # 还没有写过 config.json、也还没有账号时，不把“尚未初始化”当成故障。
        if not os.path.exists(config_path()):
            drifted = [action for action in drifted if action.kind != "config"]
        if drifted:
            problems.append("filesystem does not match config.json ({} change(s)); run `multi-grok apply`".format(
                len(drifted)))
    status = router_status(config)
    if status == "conflict":
        problems.append("grok-auto exists and is not the managed router")
    elif status in ("missing", "stale"):
        problems.append("grok-auto is {}; run `multi-grok apply`".format(status))
    root_mode = directory_mode(expand(config.root))
    if root_mode is not None and root_mode & 0o077:
        warnings.append("{} is readable by group or others; account names can be listed".format(expand(config.root)))
    for row in describe(config):
        mode = directory_mode(row["dir"])
        if mode is not None and mode & 0o077:
            problems.append("{} is readable by group or others; run chmod 700 {}".format(row["dir"], row["dir"]))
        if row["launcher"] == "conflict":
            problems.append("launcher for {} is not the managed grok-{} command".format(row["name"], row["name"]))
        # which 找不到时，上面的 bin 目录检查已经报过。这里只报“更靠前的同名文件”。
        expected = launcher_path(config.bin_dir, row["name"])
        found = shutil.which(LAUNCHER_PREFIX + row["name"])
        if found is not None and os.path.realpath(found) != os.path.realpath(expected):
            warnings.append("{} comes first on PATH and hides {}".format(found, expected))
        if row["login"] == "symlink":
            problems.append("{} auth.json is a symlink; it can point at another account's login".format(row["name"]))
        elif row["login"] == "missing":
            warnings.append("{} has no auth.json yet; run `multi-grok login {}`".format(row["name"], row["name"]))
        elif row["login"] == "other":
            problems.append("{} auth.json is not a regular file".format(row["name"]))
        if row["isolate"] == "symlink":
            problems.append("{} config.toml is a symlink".format(row["name"]))
        elif row["isolate"] == "user":
            warnings.append("{} keeps its own config.toml; ~/.claude and ~/.cursor may still be scanned".format(
                row["name"]))
        elif row["isolate"] == "missing":
            warnings.append("{} is missing the isolate config.toml; run `multi-grok apply`".format(row["name"]))
        account = config.accounts[row["name"]]
        warning = trailing_option_warning(config.effective_args(account))
        if warning:
            warnings.append("{}: {}".format(row["name"], warning))
    return problems, warnings
