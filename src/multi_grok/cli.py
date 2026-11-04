"""命令行入口。

写命令的顺序固定：加锁、按 config.json 算出动作、有冲突就什么都不写、
没有冲突才按顺序执行。`list`、`doctor`、`login` 不加锁，也不写 config.json。
`login` 不读取 auth.json，只带上该账号的 GROK_HOME 去执行 `grok login`。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import List, Optional, Sequence

from . import __version__
from .accounts import describe, plan
from .actions import error, has_conflict, print_action, warn
from .config import (PROXY_INHERIT, SHARED_ITEM_NAMES, Account, Config, ConfigError, account_dir, config_path,
                     load_config, normalize_proxy, parse_config, validate_env_key, validate_name,
                     validate_shared_item, validate_value)
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
    if forwarded is not None and args.command not in ("args", "login", "run", "mcp", "usage", "code"):
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
    """这些子命令在第一个 `--` 之后的内容不交给 argparse。

    argparse 的 REMAINDER 在可选参数和子命令混用时会把 `--settings` 当成账号名。
    `mcp` 的后续参数经常带 `--json`，所以名称之后的全部内容都原样转发。
    """
    if not argv:
        return argv, None
    if argv[0] == "mcp":
        if len(argv) < 2:
            return argv, []
        return argv[:2], argv[2:]
    if argv[0] in ("args", "login", "run", "usage", "code") and "--" in argv:
        index = argv.index("--")
        return argv[:index], argv[index + 1:]
    return argv, None


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
    init.add_argument("--shared-dir", help="directory whose items can be linked into accounts")
    init.add_argument("--shared-items", help="comma-separated items to share (skills, commands, agents, ...)")

    add = sub.add_parser("add", parents=[common], help="register an account and write grok-<name>")
    _add_account_flags(add)
    add.add_argument("--adopt", action="store_true",
                     help="record existing links that already point at the shared items")

    set_cmd = sub.add_parser("set", parents=[common], help="change proxy, isolate or sharing of an account")
    _add_account_flags(set_cmd)

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

    listing = sub.add_parser("list", help="show accounts, launchers and whether auth.json exists")
    listing.add_argument("--json", action="store_true", help="print the same facts as JSON")
    listing.add_argument("--names", action="store_true", help="print only account names")
    login = sub.add_parser("login", help="run `grok login` inside one account")
    login.add_argument("name")
    doctor = sub.add_parser("doctor", help="check PATH, directories, launchers and logins")
    doctor.add_argument("--json", action="store_true")

    route = sub.add_parser("route", parents=[common], help="choose an account by directory for grok-auto")
    route.add_argument("path", nargs="?", help="directory, or omit when using --default")
    route.add_argument("name", nargs="?", help="account that grok-auto should run in that directory")
    route.add_argument("--default", help="account used when no directory rule matches")
    route.add_argument("--no-default", action="store_true", help="clear the default account")
    route.add_argument("--remove", action="store_true", help="remove the rule for this directory")

    which = sub.add_parser("which", help="show which account grok-auto would use")
    which.add_argument("path", nargs="?", help="directory (default: the current directory)")

    rename = sub.add_parser("rename", parents=[common], help="rename an account and its launcher; the directory stays")
    rename.add_argument("old")
    rename.add_argument("new")

    run_cmd = sub.add_parser("run", help="run a command with one account's environment: run NAME -- COMMAND")
    run_cmd.add_argument("name", nargs="?", help="account; omit to use the grok-auto choice for this directory")

    path_cmd = sub.add_parser("path", help="print an account directory")
    path_cmd.add_argument("name")

    mcp = sub.add_parser("mcp", help="run `grok mcp ...` inside one account")
    mcp.add_argument("name")

    usage = sub.add_parser("usage", help="run `grok usage` inside one account")
    usage.add_argument("name")
    usage.add_argument("session", nargs="?", help="session id")
    usage.add_argument("turn", nargs="?", help="turn number")

    sessions = sub.add_parser("sessions", help="list sessions from each account's summary.json")
    sessions.add_argument("name", nargs="?", help="one account; omit for every account")
    sessions.add_argument("--grep", help="match account, id, directory, title or model")
    sessions.add_argument("--limit", type=int, default=50)
    sessions.add_argument("--cmd", action="store_true", help="also print the command that resumes the session")
    sessions.add_argument("--json", action="store_true")

    handoff = sub.add_parser("handoff", help="copy one session directory to another account")
    handoff.add_argument("target")
    handoff.add_argument("--from", dest="source")
    handoff.add_argument("--session", required=True)
    handoff.add_argument("--force", action="store_true")

    code = sub.add_parser("code", help="open VS Code with one account's environment (experimental)")
    code.add_argument("name")
    code.add_argument("path", nargs="?")

    export_cmd = sub.add_parser("export", help="write skills and related directories to a .tar.gz; no login or sessions")
    export_cmd.add_argument("name")
    export_cmd.add_argument("file")

    import_cmd = sub.add_parser("import", help="import a file written by export into an existing account")
    import_cmd.add_argument("file")
    import_cmd.add_argument("name")
    import_cmd.add_argument("--force", action="store_true", help="replace an existing directory after renaming it aside")

    completion = sub.add_parser("completion", help="print a shell completion script")
    completion.add_argument("shell", choices=("bash", "zsh", "fish"))
    return parser


def _add_account_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name")
    parser.add_argument("--proxy", help="inherit, off, a port, or an http(s) URL")
    isolate = parser.add_mutually_exclusive_group()
    isolate.add_argument("--isolate", dest="isolate", action="store_true", default=None,
                         help="write a managed config.toml that turns off Claude/Cursor compatibility scans")
    isolate.add_argument("--no-isolate", dest="isolate", action="store_false",
                         help="do not write that config.toml")
    shared = parser.add_mutually_exclusive_group()
    shared.add_argument("--shared", dest="shared_to", nargs="?", const=True, default=None, metavar="DIR",
                        help="link shared items into this account; with DIR, also set the shared directory")
    shared.add_argument("--no-shared", action="store_true", help="do not link shared items")
    parser.add_argument("--shared-exclude", action="append", default=[], metavar="ITEM")
    parser.add_argument("--shared-include", action="append", default=[], metavar="ITEM")


def _dispatch(args: argparse.Namespace, forwarded: Optional[List[str]]) -> int:
    command = args.command
    if command is None:
        return cmd_list(argparse.Namespace(names=False, json=False))
    if command == "list":
        return cmd_list(args)
    if command == "doctor":
        return cmd_doctor(args)
    if command == "login":
        return cmd_login(args.name, forwarded or [])
    if command == "set":
        return cmd_set(args)
    if command == "route":
        return cmd_route(args)
    if command == "which":
        return cmd_which(args)
    if command == "rename":
        return cmd_rename(args)
    if command == "run":
        return cmd_run(args.name, forwarded)
    if command == "path":
        return cmd_path(args.name)
    if command == "mcp":
        return cmd_mcp(args.name, forwarded or [])
    if command == "usage":
        return cmd_usage(args, forwarded)
    if command == "sessions":
        return cmd_sessions(args)
    if command == "handoff":
        return cmd_handoff(args)
    if command == "code":
        return cmd_code(args, forwarded or [])
    if command == "export":
        return cmd_export(args)
    if command == "import":
        return cmd_import(args)
    if command == "completion":
        return cmd_completion(args.shell)
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
    if args.shared_dir is not None:
        if not args.shared_dir:
            error("--shared-dir must be a non-empty path")
            return EXIT_USAGE
        config.shared_dir = args.shared_dir
    if args.shared_items is not None:
        try:
            config.shared_items = _shared_items(args.shared_items)
        except ValueError as exc:
            error(str(exc))
            return EXIT_USAGE
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
    try:
        _apply_share_flags(config, account, args)
    except ValueError as exc:
        error(str(exc))
        return EXIT_USAGE
    if args.adopt:
        if not account.shared:
            error("--adopt requires --shared")
            return EXIT_USAGE
        account._adopt = True
    code = _commit(config, args.dry_run, args.verbose)
    if code == EXIT_OK and not args.dry_run:
        _warn_path(config.bin_dir)
        warning = trailing_option_warning(config.effective_args(account))
        if warning:
            warn(warning)
    return code


def cmd_set(args: argparse.Namespace) -> int:
    try:
        validate_name(args.name)
    except ValueError as exc:
        error(str(exc))
        return EXIT_USAGE
    config, _exists = load_config()
    account = config.find(args.name)
    if account is None:
        error("unknown account {!r}".format(args.name))
        return EXIT_ERROR
    if args.proxy is not None:
        try:
            account.proxy = normalize_proxy(args.proxy)
        except ValueError as exc:
            error(str(exc))
            return EXIT_USAGE
    if args.isolate is not None:
        account.isolate = args.isolate
    try:
        _apply_share_flags(config, account, args)
    except ValueError as exc:
        error(str(exc))
        return EXIT_USAGE
    return _commit(config, args.dry_run, args.verbose)


def cmd_remove(args: argparse.Namespace) -> int:
    from .shared import plan_remove_links
    config, _exists = load_config()
    account = config.find(args.name)
    if account is None:
        error("unknown account {!r}".format(args.name))
        return EXIT_ERROR
    extras = plan_remove_links(account, account_dir(config, account.name), config.shared_dir)
    for rule in list(config.route_rules):
        if config.find(rule.account) is account:
            config.route_rules.remove(rule)
    if config.route_default and config.find(config.route_default) is account:
        config.route_default = None
    del config.accounts[account.name]
    return _commit(config, args.dry_run, args.verbose, extra=extras)


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


def cmd_list(args: argparse.Namespace) -> int:
    from .routes import router_status
    config, exists = load_config()
    rows = describe(config) if exists else []
    if args.names:
        for row in rows:
            print(row["name"])
        return EXIT_OK
    payload = {"accounts": rows, "router": router_status(config)}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not rows:
        print("no accounts. Create one with: multi-grok add home")
        return EXIT_OK
    for row in rows:
        print("{name}  launcher={launcher}  login={login}  proxy={proxy}  isolate={isolate}  "
              "shared={shared}  dir={dir}".format(**row))
    if payload["router"] is not None:
        print("router={}  default={}  rules={}".format(
            payload["router"], config.route_default or "-", len(config.route_rules)))
    return EXIT_OK


def cmd_login(name: str, extra: List[str]) -> int:
    config, _exists = load_config()
    account = config.find(name)
    if account is None:
        error("unknown account {!r}".format(name))
        return EXIT_ERROR
    directory = account_dir(config, account.name)
    if entry_kind(directory) != KIND_DIR:
        error("account directory is not a real directory; run `multi-grok apply`")
        return EXIT_ERROR
    if shutil.which("grok") is None:
        error("grok not found in PATH")
        return 127
    env = build_env(dict(os.environ), directory, account.proxy, config.effective_env(account))
    command = ["grok", "login"] + extra
    return subprocess.call(command, env=env)


def cmd_doctor(args: argparse.Namespace) -> int:
    config, _exists = load_config()
    problems, warnings = diagnose(config)
    if args.json:
        print(json.dumps({"ok": not problems, "problems": problems, "warnings": warnings,
                          "accounts": describe(config)}, ensure_ascii=False, indent=2))
        return EXIT_ERROR if problems else EXIT_OK
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


def _commit(config: Config, dry_run: bool, verbose: bool, extra: Optional[List] = None) -> int:
    with WriteLock():
        old, existed = load_config()
        actions = list(extra or []) + plan(config, old if existed else None)
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


def _shared_items(text: str) -> List[str]:
    items = [item.strip() for item in text.split(",") if item.strip()]
    if not items:
        raise ValueError("--shared-items must list at least one name")
    for item in items:
        validate_shared_item(item)
    return items


def _apply_share_flags(config: Config, account: Account, args: argparse.Namespace) -> None:
    if args.shared_to is not None:
        if args.shared_to is not True:
            if not args.shared_to:
                raise ValueError("--shared DIR must be a non-empty path")
            config.shared_dir = args.shared_to
        elif not config.shared_dir:
            config.shared_dir = "~/.grok-shared"
        account.shared = True
    elif args.no_shared:
        account.shared = False
    for item in args.shared_exclude:
        validate_shared_item(item)
        if item not in account.shared_exclude:
            account.shared_exclude.append(item)
    for item in args.shared_include:
        validate_shared_item(item)
        if item in account.shared_exclude:
            account.shared_exclude.remove(item)


def _prepare(name: str, need_grok: bool):
    """返回 (config, account, env)。失败时已经打印原因，返回 (None, None, code)。"""
    config, _exists = load_config()
    account = config.find(name)
    if account is None:
        error("unknown account {!r}".format(name))
        return None, None, EXIT_ERROR
    directory = account_dir(config, account.name)
    if entry_kind(directory) != KIND_DIR:
        error("account directory is not a real directory; run `multi-grok apply`")
        return None, None, EXIT_ERROR
    if need_grok and shutil.which("grok") is None:
        error("grok not found in PATH")
        return None, None, 127
    env = build_env(dict(os.environ), directory, account.proxy, config.effective_env(account))
    return config, account, env


def _exec_grok(name: str, grok_args: List[str], include_fixed: bool) -> int:
    config, account, env = _prepare(name, need_grok=True)
    if config is None:
        return env
    command = ["grok"]
    if include_fixed:
        command.extend(config.effective_args(account))
    command.extend(grok_args)
    return subprocess.call(command, env=env)


def cmd_route(args: argparse.Namespace) -> int:
    from .routes import normalize_path
    config, _exists = load_config()
    if args.default and args.no_default:
        error("pass only one of --default and --no-default")
        return EXIT_USAGE
    if args.default:
        account = config.find(args.default)
        if account is None:
            error("unknown account {!r}".format(args.default))
            return EXIT_ERROR
        config.route_default = account.name
    if args.no_default:
        config.route_default = None
    if args.remove or (args.path and args.name):
        if not args.path:
            error("pass the directory whose rule should change")
            return EXIT_USAGE
        path = normalize_path(args.path, os.getcwd())
        config.route_rules = [rule for rule in config.route_rules if rule.path != path]
        if args.name and not args.remove:
            account = config.find(args.name)
            if account is None:
                error("unknown account {!r}".format(args.name))
                return EXIT_ERROR
            from .config import RouteRule
            config.route_rules.append(RouteRule(path, account.name))
    elif args.path or args.name:
        error("pass both a directory and an account, or use --default / --remove")
        return EXIT_USAGE
    elif not args.default and not args.no_default:
        error("pass a directory and an account, or --default / --no-default / --remove")
        return EXIT_USAGE
    return _commit(config, args.dry_run, args.verbose)


def cmd_which(args: argparse.Namespace) -> int:
    from .routes import resolve
    config, _exists = load_config()
    directory = args.path or os.getcwd()
    account, rule = resolve(config, directory)
    if account is None:
        print("grok")
        print("no matching route")
        return EXIT_OK
    print(account.name)
    if rule is None:
        print("default")
    else:
        print("rule {}".format(rule.path))
    return EXIT_OK


def cmd_rename(args: argparse.Namespace) -> int:
    try:
        validate_name(args.old)
        validate_name(args.new)
    except ValueError as exc:
        error(str(exc))
        return EXIT_USAGE
    config, _exists = load_config()
    account = config.find(args.old)
    if account is None:
        error("unknown account {!r}".format(args.old))
        return EXIT_ERROR
    if config.find(args.new) is not None and config.find(args.new) is not account:
        error("account {!r} already exists".format(args.new))
        return EXIT_ERROR
    previous = account.name
    account.name = args.new
    del config.accounts[previous]
    config.accounts[account.name] = account
    for rule in config.route_rules:
        if rule.account.casefold() == previous.casefold():
            rule.account = account.name
    if config.route_default and config.route_default.casefold() == previous.casefold():
        config.route_default = account.name
    return _commit(config, args.dry_run, args.verbose)


def cmd_run(name: Optional[str], forwarded: Optional[List[str]]) -> int:
    from .routes import resolve
    if forwarded is None:
        error("run requires an explicit -- before the command")
        return EXIT_USAGE
    if not name:
        config, _exists = load_config()
        account, _rule = resolve(config, os.getcwd())
        if account is None:
            error("no account is routed to this directory; pass a name")
            return EXIT_ERROR
        name = account.name
    if not forwarded:
        return _exec_grok(name, [], include_fixed=True)
    _config, _account, env = _prepare(name, need_grok=False)
    if _config is None:
        return env
    return subprocess.call(list(forwarded), env=env)


def cmd_path(name: str) -> int:
    config, _exists = load_config()
    account = config.find(name)
    if account is None:
        error("unknown account {!r}".format(name))
        return EXIT_ERROR
    directory = account_dir(config, account.name)
    print(directory)
    if entry_kind(directory) != KIND_DIR:
        error("account directory does not exist: {}".format(directory))
        return EXIT_ERROR
    return EXIT_OK


def cmd_mcp(name: str, extra: List[str]) -> int:
    return _exec_grok(name, ["mcp"] + list(extra), include_fixed=False)


def cmd_usage(args: argparse.Namespace, forwarded: Optional[List[str]]) -> int:
    extra = list(forwarded or [])
    if not extra and args.session:
        extra = [args.session] + ([args.turn] if args.turn else [])
    if not extra:
        error("pass a session id: multi-grok usage NAME SESSION")
        return EXIT_USAGE
    # 用量以 `grok usage` 为准（用户指南 17-sessions.md）。这里不读 signals.json。
    return _exec_grok(args.name, ["usage"] + extra, include_fixed=False)


def cmd_sessions(args: argparse.Namespace) -> int:
    from . import sessions as session_index
    config, _exists = load_config()
    if args.name:
        account = config.find(args.name)
        if account is None:
            error("unknown account {!r}".format(args.name))
            return EXIT_ERROR
        selected = [account]
    else:
        selected = [config.accounts[name] for name in sorted(config.accounts)]
    rows = []
    for account in selected:
        rows.extend(session_index.list_account(account.name, account_dir(config, account.name)))
    if args.grep:
        rows = [row for row in rows if session_index.matches(row, args.grep)]
    rows.sort(key=lambda row: row.get("updated") or "", reverse=True)
    if args.limit is not None and args.limit >= 0:
        rows = rows[:args.limit]
    for row in rows:
        row["cmd"] = "cd {} && grok-{} --resume {}".format(
            _shell_quote(row["cwd"] or "."), row["account"], _shell_quote(row["id"]))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not rows:
        print("no sessions")
        return EXIT_OK
    for row in rows:
        print("{account}  {updated}  {model}  {id}  {cwd}  {title}".format(**row))
        if args.cmd:
            print(row["cmd"])
    return EXIT_OK


def cmd_handoff(args: argparse.Namespace) -> int:
    from . import sessions as session_index
    from .routes import resolve
    config, _exists = load_config()
    target = config.find(args.target)
    if target is None:
        error("unknown account {!r}".format(args.target))
        return EXIT_ERROR
    if args.source:
        source = config.find(args.source)
        if source is None:
            error("unknown account {!r}".format(args.source))
            return EXIT_ERROR
    else:
        source, _rule = resolve(config, os.getcwd())
        if source is None:
            error("pass --from NAME; no account is routed to this directory")
            return EXIT_ERROR
    if source.name == target.name:
        error("source and target are the same account")
        return EXIT_USAGE
    source_dir = account_dir(config, source.name)
    target_dir = account_dir(config, target.name)
    if entry_kind(source_dir) != KIND_DIR or entry_kind(target_dir) != KIND_DIR:
        error("both account directories must already exist")
        return EXIT_ERROR
    try:
        with WriteLock():
            copied = session_index.handoff(source_dir, target_dir, args.session, args.force)
    except session_index.SessionError as exc:
        error(str(exc))
        return EXIT_ERROR
    print(copied)
    print("cd into the session directory and run: grok-{} --resume {}".format(target.name, args.session))
    return EXIT_OK


def cmd_code(args: argparse.Namespace, extra: List[str]) -> int:
    warn("experimental: this sets GROK_HOME for a separate VS Code user-data directory. "
         "The user guide does not say whether the editor extension reads GROK_HOME.")
    config, account, env = _prepare(args.name, need_grok=False)
    if config is None:
        return env
    if shutil.which("code", path=env.get("PATH")) is None:
        error("code not found in PATH")
        return 127
    data_dir = os.path.join(expand(config.root), ".apps", account.dir, "vscode")
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    os.chmod(data_dir, 0o700)
    command = ["code", "--user-data-dir", data_dir]
    if args.path:
        command.append(args.path)
    command.extend(extra)
    return subprocess.call(command, env=env)


def cmd_export(args: argparse.Namespace) -> int:
    from .bundle import BundleError, export_account
    config, _exists = load_config()
    account = config.find(args.name)
    if account is None:
        error("unknown account {!r}".format(args.name))
        return EXIT_ERROR
    directory = account_dir(config, account.name)
    if entry_kind(directory) != KIND_DIR:
        error("account directory does not exist: {}".format(directory))
        return EXIT_ERROR
    try:
        exported = export_account(directory, expand(args.file))
    except (BundleError, OSError) as exc:
        error(str(exc))
        return EXIT_ERROR
    print("exported {} item(s) to {}".format(len(exported), expand(args.file)))
    if exported:
        print(" ".join(exported))
    return EXIT_OK


def cmd_import(args: argparse.Namespace) -> int:
    from .bundle import BundleError, import_account
    config, _exists = load_config()
    account = config.find(args.name)
    if account is None:
        error("unknown account {!r}".format(args.name))
        return EXIT_ERROR
    directory = account_dir(config, account.name)
    if entry_kind(directory) != KIND_DIR:
        error("account directory does not exist: {}".format(directory))
        return EXIT_ERROR
    try:
        imported = import_account(directory, expand(args.file), args.force)
    except (BundleError, OSError) as exc:
        error(str(exc))
        return EXIT_ERROR
    print("imported {}".format(" ".join(imported) if imported else "(nothing)"))
    return EXIT_OK


def cmd_completion(shell: str) -> int:
    from .completion import render
    sys.stdout.write(render(shell, _parser()))
    return EXIT_OK


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"
