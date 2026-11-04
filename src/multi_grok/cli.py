"""命令行入口。

写命令的顺序固定：加锁、按 config.json 算出动作、有冲突就什么都不写、
没有冲突才按顺序执行。`list`、`doctor`、`login` 不加锁，也不写 config.json。
`login` 不读取 auth.json，只带上该账号的 GROK_HOME 去执行 `grok login`。
"""

import argparse
import os
import shutil
import subprocess
import sys
from typing import List, Optional, Sequence

from . import __version__
from .accounts import describe, plan
from .actions import error, has_conflict, print_action, warn
from .config import (PROXY_INHERIT, Account, Config, ConfigError, config_path, load_config, normalize_proxy,
                     parse_config, validate_env_key, validate_name, validate_value)
from .doctor import diagnose
from .fsutil import KIND_DIR, entry_kind, expand, read_text
from .launcher import build_env, launcher_path, trailing_option_warning
from .lock import LockBusyError, WriteLock
from .platform import is_supported

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CONFLICT = 3
EXIT_BUSY = 4


def main(argv: Optional[Sequence[str]] = None) -> int:
    """安装后的控制台脚本要退出进程。测试调用 run，避免 sys.exit 关掉测试进程。"""
    return run(argv)


def run(argv: Optional[Sequence[str]] = None) -> int:
    if not is_supported():
        error("this operating system is not supported; use macOS or Linux")
        return EXIT_ERROR
    raw = list(sys.argv[1:] if argv is None else argv)
    raw, forwarded = _split_forwarded(raw)
    parser = _parser()
    try:
        args = parser.parse_args(raw)
    except SystemExit as exc:
        code = exc.code
        return EXIT_USAGE if code is None else int(code)
    if forwarded is not None and args.command not in ("args", "login"):
        error("unexpected --")
        return EXIT_USAGE
    try:
        return _dispatch(args, forwarded)
    except ConfigError as exc:
        error(str(exc))
        return EXIT_ERROR
    except LockBusyError as exc:
        error("another multi-grok is writing the configuration (pid {})".format(exc.holder_pid or "unknown"))
        return EXIT_BUSY
    except OSError as exc:
        error(str(exc))
        return EXIT_ERROR


def _split_forwarded(argv: List[str]) -> tuple:
    """`args` 与 `login` 在第一个 `--` 之后的内容不交给 argparse。

    argparse 的 REMAINDER 在可选参数和子命令混用时会把 `--settings` 当成账号名。
    """
    if not argv or argv[0] not in ("args", "login") or "--" not in argv:
        return argv, None
    index = argv.index("--")
    return argv[:index], argv[index + 1:]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multi-grok",
        description="Keep one Grok environment per account under ~/.grok-accounts, "
                    "and start it with grok-<name> (for example grok-home).",
    )
    parser.add_argument("--version", action="version", version="multi-grok {}".format(__version__))
    sub = parser.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="print actions and write nothing")
    common.add_argument("--verbose", action="store_true", help="also print unchanged paths")

    init = sub.add_parser("init", parents=[common], help="set the account root and launcher directory")
    init.add_argument("--root", help="directory that holds one subdirectory per account "
                                      "(default: ~/.grok-accounts)")
    init.add_argument("--bin-dir", help="where grok-<name> commands are written (default: ~/.local/bin)")

    add = sub.add_parser("add", parents=[common], help="register an account and write grok-<name>")
    add.add_argument("name")
    add.add_argument("--proxy", help="inherit, off, a port, or an http(s) URL")
    isolate = add.add_mutually_exclusive_group()
    isolate.add_argument("--isolate", dest="isolate", action="store_true", default=None,
                         help="write a managed config.toml that turns off Claude/Cursor compatibility scans")
    isolate.add_argument("--no-isolate", dest="isolate", action="store_false",
                         help="do not write that config.toml")

    remove = sub.add_parser("remove", parents=[common],
                            help="unregister an account and delete its launcher; the directory stays")
    remove.add_argument("name")

    proxy = sub.add_parser("proxy", parents=[common], help="set one account's proxy")
    proxy.add_argument("name")
    proxy.add_argument("value", help="inherit, off, a port, or an http(s) URL")

    env = sub.add_parser("env", parents=[common], help="set extra environment variables")
    env.add_argument("name", nargs="?", help="account name; omit with --defaults")
    env.add_argument("--defaults", action="store_true", help="change the variables inherited by every account")
    env.add_argument("--unset", action="append", default=[], help="remove one variable; repeatable")
    env.add_argument("pairs", nargs="*", help="KEY=value")

    args_cmd = sub.add_parser("args", parents=[common], help="replace fixed arguments; put them after --")
    args_cmd.add_argument("name", nargs="?", help="account name; omit with --defaults")
    args_cmd.add_argument("--defaults", action="store_true")

    apply_cmd = sub.add_parser("apply", parents=[common],
                               help="converge the filesystem; with -f, replace accounts from a JSON file")
    apply_cmd.add_argument("-f", "--file", help="JSON file in the same shape as config.json")

    sub.add_parser("list", help="show accounts, launchers and whether auth.json exists")
    login = sub.add_parser("login", help="run `grok login` inside one account")
    login.add_argument("name")
    sub.add_parser("doctor", help="check PATH, directories, launchers and logins")
    return parser


def _dispatch(args: argparse.Namespace, forwarded: Optional[List[str]]) -> int:
    command = args.command
    if command is None:
        return cmd_list()
    if command == "list":
        return cmd_list()
    if command == "doctor":
        return cmd_doctor()
    if command == "login":
        return cmd_login(args.name, forwarded or [])
    if command == "init":
        return cmd_init(args)
    if command == "add":
        return cmd_add(args)
    if command == "remove":
        return cmd_remove(args)
    if command == "proxy":
        return cmd_proxy(args)
    if command == "env":
        return cmd_env(args)
    if command == "args":
        if forwarded is None:
            error("args requires an explicit -- before the fixed arguments (an empty list clears them)")
            return EXIT_USAGE
        return cmd_args(args, forwarded)
    if command == "apply":
        return cmd_apply(args)
    error("unknown command")
    return EXIT_USAGE


def cmd_init(args: argparse.Namespace) -> int:
    config, _exists = load_config()
    if args.root is not None:
        if config.accounts and expand(args.root) != expand(config.root):
            error("cannot change root while accounts exist")
            return EXIT_ERROR
        config.root = args.root
    if args.bin_dir is not None:
        if not args.bin_dir:
            error("--bin-dir must be a non-empty path")
            return EXIT_ERROR
        config.bin_dir = args.bin_dir
    return _commit(config, args.dry_run, args.verbose)


def cmd_add(args: argparse.Namespace) -> int:
    try:
        validate_name(args.name)
    except ValueError as exc:
        error(str(exc))
        return EXIT_USAGE
    config, _exists = load_config()
    account = config.find(args.name)
    if account is None:
        account = Account(args.name)
        config.accounts[args.name] = account
    if args.proxy is not None:
        try:
            account.proxy = normalize_proxy(args.proxy)
        except ValueError as exc:
            error(str(exc))
            return EXIT_USAGE
    if args.isolate is not None:
        account.isolate = args.isolate
    code = _commit(config, args.dry_run, args.verbose)
    if code == EXIT_OK and not args.dry_run:
        _warn_path(config.bin_dir)
        warning = trailing_option_warning(config.effective_args(account))
        if warning:
            warn(warning)
    return code


def cmd_remove(args: argparse.Namespace) -> int:
    config, _exists = load_config()
    account = config.find(args.name)
    if account is None:
        error("unknown account {!r}".format(args.name))
        return EXIT_ERROR
    del config.accounts[account.name]
    return _commit(config, args.dry_run, args.verbose)


def cmd_proxy(args: argparse.Namespace) -> int:
    config, _exists = load_config()
    account = config.find(args.name)
    if account is None:
        error("unknown account {!r}".format(args.name))
        return EXIT_ERROR
    try:
        account.proxy = normalize_proxy(args.value)
    except ValueError as exc:
        error(str(exc))
        return EXIT_USAGE
    return _commit(config, args.dry_run, args.verbose)


def cmd_env(args: argparse.Namespace) -> int:
    pairs = list(args.pairs)
    # `--defaults KEY=value` 时，可选的位置参数 name 会把第一对 KEY=value 吃掉。
    # 这个修正必须发生在“账号名和 --defaults 二选一”的判断之前。
    if args.name and "=" in args.name:
        if not args.defaults:
            error("expected an account name or --defaults, got {!r}".format(args.name))
            return EXIT_USAGE
        pairs.insert(0, args.name)
        args.name = None
    if args.defaults == bool(args.name):
        error("pass either an account name or --defaults")
        return EXIT_USAGE
    updates = {}
    for pair in pairs:
        if "=" not in pair or pair.startswith("-"):
            error("expected KEY=value, got {!r}".format(pair))
            return EXIT_USAGE
        key, value = pair.split("=", 1)
        try:
            validate_env_key(key)
            validate_value(value)
        except ValueError as exc:
            error(str(exc))
            return EXIT_USAGE
        updates[key] = value
    for key in args.unset:
        try:
            validate_env_key(key)
        except ValueError as exc:
            error(str(exc))
            return EXIT_USAGE
    if not updates and not args.unset:
        error("pass KEY=value or --unset KEY")
        return EXIT_USAGE
    config, _exists = load_config()
    target = _env_target(config, args.name, args.defaults)
    if target is None:
        return EXIT_ERROR
    target.update(updates)
    for key in args.unset:
        target.pop(key, None)
    return _commit(config, args.dry_run, args.verbose)


def cmd_args(args: argparse.Namespace, forwarded: List[str]) -> int:
    if args.defaults == bool(args.name):
        error("pass either an account name or --defaults")
        return EXIT_USAGE
    for item in forwarded:
        try:
            validate_value(item)
        except ValueError as exc:
            error(str(exc))
            return EXIT_USAGE
    config, _exists = load_config()
    if args.defaults:
        config.defaults_args = list(forwarded)
        account = None
    else:
        account = config.find(args.name)
        if account is None:
            error("unknown account {!r}".format(args.name))
            return EXIT_ERROR
        account.args = list(forwarded)
    code = _commit(config, args.dry_run, args.verbose)
    if code == EXIT_OK and not args.dry_run and account is not None:
        warning = trailing_option_warning(config.effective_args(account))
        if warning:
            warn(warning)
    return code


def cmd_apply(args: argparse.Namespace) -> int:
    if args.file:
        raw = read_text(expand(args.file))
        if raw is None:
            error("cannot read {}".format(args.file))
            return EXIT_ERROR
        config = parse_config(raw, args.file)
    else:
        config, _exists = load_config()
    return _commit(config, args.dry_run, args.verbose)


def cmd_list() -> int:
    config, exists = load_config()
    if not exists or not config.accounts:
        print("no accounts. Create one with: multi-grok add home")
        return EXIT_OK
    for row in describe(config):
        print("{name}  launcher={launcher}  login={login}  proxy={proxy}  isolate={isolate}  dir={dir}".format(
            **row))
    return EXIT_OK


def cmd_login(name: str, extra: List[str]) -> int:
    config, _exists = load_config()
    account = config.find(name)
    if account is None:
        error("unknown account {!r}".format(name))
        return EXIT_ERROR
    directory = os.path.join(expand(config.root), account.name)
    if entry_kind(directory) != KIND_DIR:
        error("account directory is not a real directory; run `multi-grok apply`")
        return EXIT_ERROR
    if shutil.which("grok") is None:
        error("grok not found in PATH")
        return 127
    env = build_env(dict(os.environ), directory, account.proxy, config.effective_env(account))
    command = ["grok", "login"] + extra
    return subprocess.call(command, env=env)


def cmd_doctor() -> int:
    config, _exists = load_config()
    problems, warnings = diagnose(config)
    for message in warnings:
        warn(message)
    for message in problems:
        error(message)
    if problems:
        return EXIT_ERROR
    print("[multi-grok] ok  config={}".format(config_path() if os.path.exists(config_path()) else "(not written yet)"))
    return EXIT_OK


def _env_target(config: Config, name: Optional[str], defaults: bool) -> Optional[dict]:
    if defaults:
        return config.defaults_env
    account = config.find(name or "")
    if account is None:
        error("unknown account {!r}".format(name))
        return None
    return account.env


def _commit(config: Config, dry_run: bool, verbose: bool) -> int:
    with WriteLock():
        actions = plan(config)
        conflicted = has_conflict(actions)
        for action in actions:
            if action.status == "unchanged" and not verbose:
                continue
            if action.status == "skip" and not verbose and not conflicted:
                # 用户自己的 config.toml 被留下时，即使没有其它变化也要说一声。
                print_action(action, dry_run=False)
                continue
            if action.status == "unchanged":
                print_action(action, dry_run=dry_run)
                continue
            print_action(action, dry_run=dry_run)
        if conflicted:
            error("conflicts found; nothing was written")
            return EXIT_CONFLICT
        if dry_run:
            return EXIT_OK
        for action in actions:
            if action.changes and action.run is not None:
                action.run()
    return EXIT_OK


def _warn_path(bin_dir: str) -> None:
    expanded = expand(bin_dir)
    if expanded not in os.environ.get("PATH", "").split(os.pathsep):
        warn("{} is not on PATH. multi-grok does not edit your shell startup file. "
             "export PATH=\"{}:$PATH\"".format(expanded, expanded))
