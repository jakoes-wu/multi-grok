"""从 config.json 算出需要对文件系统做的动作。

计划阶段不写盘。有一个 conflict，调用方就整批放弃。
"""

import os
import stat
from typing import Dict, List, Optional

from .actions import CONFLICT, CREATE, DELETE, SKIP, UNCHANGED, UPDATE, Action
from .config import Config, account_dir, config_path, dump_config, save_config
from .fsutil import (KIND_DIR, KIND_FILE, KIND_LINK, KIND_MISSING, atomic_write, entry_kind, expand,
                     is_under, read_text, unlink_file)
from .launcher import launcher_path, managed_account, render, scan_managed
from .platform import default_grok_home
from .routes import plan_router
from .shared import plan_shared

# 第一行是受管标记。用户改过、或不是我们写的 config.toml，一律不覆盖。
ISOLATE_MARKER = "# managed-by: multi-grok account="

# Grok 的兼容扫描默认全开，而且路径固定在 ~/.claude 与 ~/.cursor，不随 GROK_HOME 改变
# （用户指南 05-configuration.md「Harness compatibility」）。关掉之后，这个账号只使用自己目录里的文件。
_ISOLATE_BODY = """\
# template: 1
# Grok scans ~/.claude and ~/.cursor unless these cells are off.
# Those paths do not follow GROK_HOME. This file keeps the account on its own tree.
# multi-grok rewrites this file only while the managed-by marker on the first line remains.

[compat.claude]
skills = false
rules = false
agents = false
mcps = false
hooks = false
sessions = false

[compat.cursor]
skills = false
rules = false
agents = false
mcps = false
hooks = false
sessions = false
"""


def isolate_text(name: str) -> str:
    return ISOLATE_MARKER + name + "\n" + _ISOLATE_BODY


def clashes_with_default_home(path: str) -> bool:
    """账号目录不能是 ~/.grok，不能放进 ~/.grok，也不能把 ~/.grok 包在自己里面。

    ~/.grok 是直接运行 grok 时的数据目录。放进那里会和默认账号的会话、登录混在一起。
    """
    default_home = expand(default_grok_home())
    path = os.path.abspath(path)
    return path == default_home or is_under(path, default_home) or is_under(default_home, path)


def plan(config: Config, old: Optional[Config] = None) -> List[Action]:
    """列出收敛动作。共享链接会改 managed_links，所以 config.json 的动作放在这些修改之后再生成。

    执行时仍然先写 config.json。中途失败后再跑一次 apply，能按新配置把缺的链接补上。
    """
    actions: List[Action] = []
    root = expand(config.root)
    root_kind = entry_kind(root)
    if root_kind == KIND_LINK:
        actions.append(Action(CONFLICT, "account-root", root,
                              "account root is a symlink; GROK_HOME must be a real directory"))
    elif root_kind not in (KIND_MISSING, KIND_DIR):
        actions.append(Action(CONFLICT, "account-root", root, "account root is not a directory"))
    old_shared = old.shared_dir if old is not None else config.shared_dir
    for name in sorted(config.accounts):
        actions.extend(_account_actions(config, name, block_root=root_kind == KIND_LINK, old_shared=old_shared))
    actions.extend(plan_router(config))
    actions.extend(_orphan_launcher_actions(config))
    return [_config_action(config)] + actions


def _config_action(config: Config) -> Action:
    path = config_path()
    new_text = dump_config(config)
    kind = entry_kind(path)
    if kind not in (KIND_MISSING, KIND_FILE):
        return Action(CONFLICT, "config", path, "config.json is not a regular file")
    current = read_text(path) if kind == KIND_FILE else None
    if current == new_text:
        return Action(UNCHANGED, "config", path)

    def write() -> None:
        # 再写一次是为了走 save_config 的 0600；内容与计划时的 dump 相同，因为计划之后不再改 config。
        if dump_config(config) != new_text:
            raise OSError("config changed while planning; nothing else was written")
        save_config(config)

    status = CREATE if current is None else UPDATE
    return Action(status, "config", path, run=write)


def _account_actions(config: Config, name: str, block_root: bool, old_shared: Optional[str]) -> List[Action]:
    account = config.accounts[name]
    path = account_dir(config, name)
    actions: List[Action] = []
    if clashes_with_default_home(path):
        actions.append(Action(CONFLICT, "account-dir", path,
                              "this path overlaps ~/.grok; choose another root"))
        return actions
    if block_root:
        return actions
    kind = entry_kind(path)
    if kind == KIND_LINK:
        actions.append(Action(CONFLICT, "account-dir", path,
                              "GROK_HOME must be a real directory, not a symlink"))
        return actions
    if kind not in (KIND_MISSING, KIND_DIR):
        actions.append(Action(CONFLICT, "account-dir", path, "not a directory"))
        return actions
    if kind == KIND_MISSING:
        actions.append(Action(CREATE, "account-dir", path, run=_mkdir_private(path)))
    else:
        actions.append(Action(UNCHANGED, "account-dir", path))
    actions.append(_isolate_action(account.isolate, name, os.path.join(path, "config.toml")))
    actions.append(_launcher_action(config, name))
    actions.extend(plan_shared(config, account, path, old_shared))
    return actions


def _mkdir_private(path: str):
    def run() -> None:
        root = os.path.dirname(path)
        if entry_kind(root) == KIND_MISSING:
            os.makedirs(root, exist_ok=True)
            os.chmod(root, 0o700)
        elif entry_kind(root) != KIND_DIR:
            raise OSError("cannot create a directory under {}".format(root))
        os.mkdir(path)
        # makedirs / mkdir 受 umask 影响，这里把账号目录收成仅本人可进出。
        os.chmod(path, 0o700)
    return run


def _isolate_action(isolate: bool, name: str, path: str) -> Action:
    kind = entry_kind(path)
    marker = ISOLATE_MARKER + name
    text = isolate_text(name)
    if kind == KIND_LINK:
        return Action(CONFLICT, "isolate-config", path,
                      "config.toml is a symlink; multi-grok will not follow or replace it")
    if not isolate:
        if kind == KIND_FILE and _first_line(path) == marker:
            # 只有仍等于模板的文件才删。Grok 会在标记还在时往文件末尾追加段落，
            # 按标记整份删除会把那些段落一起删掉。
            if read_text(path) == text:
                return Action(DELETE, "isolate-config", path, run=_unlink(path))
            return Action(SKIP, "isolate-config", path,
                          "managed file was edited after it was written; left untouched")
        if kind == KIND_MISSING:
            return Action(UNCHANGED, "isolate-config", path, "isolate is off")
        return Action(SKIP, "isolate-config", path, "isolate is off; left the existing file")
    if kind == KIND_MISSING:
        return Action(CREATE, "isolate-config", path, run=_write_private(path, text))
    if kind != KIND_FILE:
        return Action(CONFLICT, "isolate-config", path, "not a regular file")
    current = read_text(path)
    if current is None:
        return Action(CONFLICT, "isolate-config", path, "cannot read config.toml")
    if _first_line(path) != marker:
        return Action(SKIP, "isolate-config", path,
                      "existing config.toml is not managed by multi-grok; left untouched")
    if current == text:
        return Action(UNCHANGED, "isolate-config", path)
    # 标记还在、正文已经不是模板时，不再写回。写回会清掉 Grok 追加的段落。
    return Action(SKIP, "isolate-config", path,
                  "managed file was edited after it was written; left untouched")


def _launcher_action(config: Config, name: str) -> Action:
    account = config.accounts[name]
    path = launcher_path(config.bin_dir, name)
    expected = render(name, config.root, account.proxy, config.effective_env(account),
                      config.effective_args(account))
    kind = entry_kind(path)
    if kind == KIND_MISSING:
        return Action(CREATE, "launcher", path, run=_write_launcher(path, expected))
    if kind != KIND_FILE:
        return Action(CONFLICT, "launcher", path, "not a regular file; not overwritten")
    owner = managed_account(path)
    if owner is None:
        return Action(CONFLICT, "launcher", path, "file exists and is not managed by multi-grok")
    if owner != name:
        return Action(CONFLICT, "launcher", path, "file is managed for account {}".format(owner))
    current = read_text(path)
    if current == expected:
        return Action(UNCHANGED, "launcher", path)
    return Action(UPDATE, "launcher", path, run=_write_launcher(path, expected))


def _orphan_launcher_actions(config: Config) -> List[Action]:
    actions: List[Action] = []
    for account_name, path in scan_managed(config.bin_dir).items():
        expected_base = "grok-" + account_name
        if os.path.basename(path) != expected_base:
            actions.append(Action(CONFLICT, "launcher", path,
                                  "filename does not match the managed account name"))
            continue
        if config.find(account_name) is None:
            actions.append(Action(DELETE, "launcher", path, "account is no longer registered",
                                  run=_unlink(path)))
    return actions


def _write_private(path: str, text: str):
    def run() -> None:
        atomic_write(path, text, mode=0o600)
    return run


def _write_launcher(path: str, text: str):
    def run() -> None:
        directory = os.path.dirname(path)
        if entry_kind(directory) == KIND_MISSING:
            os.makedirs(directory, exist_ok=True)
        atomic_write(path, text, mode=0o755)
    return run


def _unlink(path: str):
    def run() -> None:
        unlink_file(path)
    return run


def _first_line(path: str) -> Optional[str]:
    content = read_text(path)
    if content is None:
        return None
    return content.split("\n", 1)[0]


def directory_mode(path: str) -> Optional[int]:
    if entry_kind(path) != KIND_DIR:
        return None
    return stat.S_IMODE(os.lstat(path).st_mode)


def describe(config: Config) -> List[Dict[str, str]]:
    """list 用的只读状态。不打开 auth.json，只看它是不是普通文件。

    last 来自 summary.json 的 updated_at，不写入 config.json。
    """
    from .sessions import latest_updated, list_account
    rows = []
    for name in sorted(config.accounts):
        account = config.accounts[name]
        directory = account_dir(config, name)
        launcher = launcher_path(config.bin_dir, name)
        rows.append({
            "name": name,
            "dir": directory,
            "proxy": account.proxy,
            "launcher": _launcher_state(config, account.name, launcher),
            "login": _login_state(os.path.join(directory, "auth.json")),
            "isolate": _isolate_state(account.isolate, name, os.path.join(directory, "config.toml")),
            "shared": "on" if account.shared else "off",
            "last": latest_updated(list_account(name, directory)),
        })
    return rows


def _launcher_state(config: Config, name: str, path: str) -> str:
    kind = entry_kind(path)
    if kind == KIND_MISSING:
        return "missing"
    if kind != KIND_FILE:
        return "conflict"
    owner = managed_account(path)
    if owner != name:
        return "conflict"
    account = config.accounts[name]
    expected = render(name, config.root, account.proxy, config.effective_env(account),
                      config.effective_args(account))
    current = read_text(path)
    if current == expected:
        return "ok"
    return "stale"


def _login_state(path: str) -> str:
    kind = entry_kind(path)
    if kind == KIND_MISSING:
        return "missing"
    if kind == KIND_FILE:
        return "file"
    if kind == KIND_LINK:
        return "symlink"
    return "other"


def _isolate_state(isolate: bool, name: str, path: str) -> str:
    kind = entry_kind(path)
    if kind == KIND_LINK:
        return "symlink"
    if not isolate:
        return "off"
    if kind == KIND_MISSING:
        return "missing"
    if kind != KIND_FILE:
        return "other"
    if _first_line(path) == ISOLATE_MARKER + name and read_text(path) == isolate_text(name):
        return "managed"
    return "user"
