"""config.json 是唯一权威源。启动命令和隔离用的 config.toml 都从它推导。

本模块只校验和读写这份 JSON，不创建账号目录。
"""

import json
import os
import re
import urllib.parse
from typing import Dict, List, Optional, Tuple

from . import platform
from .fsutil import atomic_write, expand

CONFIG_VERSION = 1
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,63}$")
# grok-auto 占用这个名字。账号不能再叫 auto，否则启动命令和路由入口是同一个文件。
RESERVED_NAMES = ("auto",)
# 用户指南里按 $GROK_HOME 扫描的目录。sessions、memory-v2、logs 是账号自己的状态，不能共享。
SHARED_ITEM_NAMES = ("skills", "commands", "agents", "plugins", "workflows", "hooks", "rules", "personas")
DEFAULT_SHARED_ITEMS = ("skills", "commands", "agents")
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PROXY_INHERIT = "inherit"
PROXY_OFF = "off"
PROXY_SCHEMES = ("http", "https")
PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
              "https_proxy", "http_proxy", "all_proxy", "no_proxy")

# 这些键决定“用哪一套目录 / 哪一份登录”。写进启动命令会把账号隔离悄悄拆掉。
_IDENTITY_KEYS = ("GROK_HOME", "GROK_AUTH", "GROK_AUTH_PATH", "GROK_CONFIG", "GROK_CONFIG_PATH",
                  "HOME", "USER")
_CREDENTIAL_KEYS = ("XAI_API_KEY", "GROK_CODE_XAI_API_KEY")
# 整段相等才算。MAX_THINKING_TOKENS 里的 TOKENS 不能误伤，所以不把 TOKEN 的子串当凭据。
_CREDENTIAL_SEGMENTS = ("TOKEN", "SECRET", "PASSWORD", "PASSPHRASE")


class ConfigError(Exception):
    """配置内容不合法。对应退出码 1。"""


class RouteRule(object):
    def __init__(self, path: str, account: str) -> None:
        self.path = path
        self.account = account

    def copy(self) -> "RouteRule":
        return RouteRule(self.path, self.account)


class Account(object):
    """一个已登记的账号。改名只改启动命令，目录名留在 dir 里。"""

    def __init__(self, name: str, proxy: str = PROXY_INHERIT, isolate: bool = True,
                 env: Optional[Dict[str, str]] = None, args: Optional[List[str]] = None,
                 shared: bool = False, shared_exclude: Optional[List[str]] = None,
                 managed_links: Optional[List[str]] = None, directory: Optional[str] = None) -> None:
        self.name = name
        self.proxy = proxy
        self.isolate = isolate
        self.env = dict(env or {})
        self.args = list(args or [])
        self.shared = shared
        self.shared_exclude = list(shared_exclude or [])
        self.managed_links = list(managed_links or [])
        self.dir = directory or name

    def copy(self) -> "Account":
        return Account(self.name, self.proxy, self.isolate, dict(self.env), list(self.args),
                       self.shared, list(self.shared_exclude), list(self.managed_links), self.dir)

    def to_dict(self) -> dict:
        # 缺省字段不写回去。0.1 的 config.json 在没有这些字段时，doctor 才不会把它们当成漂移。
        data = {"proxy": self.proxy, "isolate": self.isolate,
                "env": dict(self.env), "args": list(self.args)}
        if self.shared:
            data["shared"] = True
        if self.shared_exclude:
            data["shared_exclude"] = list(self.shared_exclude)
        if self.managed_links:
            data["managed_links"] = list(self.managed_links)
        if self.dir != self.name:
            data["dir"] = self.dir
        return data


class Config(object):
    def __init__(self, root: str, bin_dir: str, accounts: Dict[str, Account],
                 defaults_env: Optional[Dict[str, str]] = None,
                 defaults_args: Optional[List[str]] = None,
                 shared_dir: Optional[str] = None,
                 shared_items: Optional[List[str]] = None,
                 route_rules: Optional[List[RouteRule]] = None,
                 route_default: Optional[str] = None) -> None:
        self.root = root
        self.bin_dir = bin_dir
        self.accounts = accounts
        self.defaults_env = dict(defaults_env or {})
        self.defaults_args = list(defaults_args or [])
        self.shared_dir = shared_dir
        self.shared_items = list(DEFAULT_SHARED_ITEMS if shared_items is None else shared_items)
        self.route_rules = list(route_rules or [])
        self.route_default = route_default

    def routes_enabled(self) -> bool:
        return bool(self.route_rules) or self.route_default is not None

    def find(self, name: str) -> Optional[Account]:
        """大小写不敏感。macOS 默认文件系统上 Work 与 work 是同一个目录。"""
        folded = name.casefold()
        for account in self.accounts.values():
            if account.name.casefold() == folded:
                return account
        return None

    def effective_env(self, account: Account) -> Dict[str, str]:
        """全局 defaults.env 打底，账号自己的同名键覆盖它。"""
        merged = dict(self.defaults_env)
        merged.update(account.env)
        return merged

    def effective_args(self, account: Account) -> List[str]:
        """全局参数在前，账号参数在后。启动命令再把用户当次输入接在最后。"""
        return list(self.defaults_args) + list(account.args)

    def copy(self) -> "Config":
        return Config(self.root, self.bin_dir,
                      {key: value.copy() for key, value in self.accounts.items()},
                      dict(self.defaults_env), list(self.defaults_args),
                      self.shared_dir, list(self.shared_items),
                      [rule.copy() for rule in self.route_rules], self.route_default)

    def to_dict(self) -> dict:
        data = {
            "version": CONFIG_VERSION,
            "root": self.root,
            "bin_dir": self.bin_dir,
            "defaults": {"env": dict(self.defaults_env), "args": list(self.defaults_args)},
            "accounts": {name: account.to_dict() for name, account in self.accounts.items()},
        }
        shared = {}
        if self.shared_dir:
            shared["dir"] = self.shared_dir
        if list(self.shared_items) != list(DEFAULT_SHARED_ITEMS):
            shared["items"] = list(self.shared_items)
        if shared:
            data["shared"] = shared
        if self.routes_enabled():
            data["routes"] = {
                "default": self.route_default,
                "rules": [{"path": rule.path, "account": rule.account} for rule in self.route_rules],
            }
        return data


def default_config() -> Config:
    return Config(platform.default_root(), platform.default_bin_dir(), {})


def config_path() -> str:
    return os.path.join(platform.state_dir(), "config.json")


def dump_config(config: Config) -> str:
    return json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n"


def load_config() -> Tuple[Config, bool]:
    """返回 (配置, 文件是否已经存在)。文件不存在时给出默认配置，不立刻写盘。"""
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return default_config(), False
    except OSError as exc:
        raise ConfigError("cannot read {}: {}".format(path, exc))
    return parse_config(raw, path), True


def save_config(config: Config) -> None:
    atomic_write(config_path(), dump_config(config), mode=0o600)


def parse_config(raw: str, source: str) -> Config:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ConfigError("{} is not valid JSON: {}".format(source, exc))
    if not isinstance(data, dict):
        raise ConfigError("{} must be a JSON object".format(source))
    unknown = set(data) - {"version", "root", "bin_dir", "defaults", "accounts", "shared", "routes"}
    if unknown:
        raise ConfigError("{} has unknown keys: {}".format(source, ", ".join(sorted(unknown))))
    version = data.get("version", CONFIG_VERSION)
    if version != CONFIG_VERSION:
        raise ConfigError("{} has unsupported version {!r}".format(source, version))
    root = data.get("root", platform.default_root())
    bin_dir = data.get("bin_dir", platform.default_bin_dir())
    if not isinstance(root, str) or not root:
        raise ConfigError("{} field root must be a non-empty string".format(source))
    if not isinstance(bin_dir, str) or not bin_dir:
        raise ConfigError("{} field bin_dir must be a non-empty string".format(source))
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError("{} field defaults must be an object".format(source))
    extra_defaults = set(defaults) - {"env", "args"}
    if extra_defaults:
        raise ConfigError("{} defaults has unknown keys: {}".format(source, ", ".join(sorted(extra_defaults))))
    defaults_env = _parse_env(defaults.get("env", {}), "{} defaults.env".format(source))
    defaults_args = _parse_args(defaults.get("args", []), "{} defaults.args".format(source))
    raw_accounts = data.get("accounts", {})
    if not isinstance(raw_accounts, dict):
        raise ConfigError("{} field accounts must be an object".format(source))
    accounts: Dict[str, Account] = {}
    folded: Dict[str, str] = {}
    for name, body in raw_accounts.items():
        try:
            validate_name(name)
        except ValueError as exc:
            raise ConfigError("{} account: {}".format(source, exc))
        previous = folded.get(name.casefold())
        if previous is not None:
            raise ConfigError("{} has two accounts that differ only by case: {} and {}".format(
                source, previous, name))
        folded[name.casefold()] = name
        accounts[name] = _parse_account(name, body, source)
    dirs: Dict[str, str] = {}
    for account in accounts.values():
        previous = dirs.get(account.dir.casefold())
        if previous is not None:
            raise ConfigError("{} has two accounts using the same directory: {} and {}".format(
                source, previous, account.name))
        dirs[account.dir.casefold()] = account.name
    shared_dir, shared_items = _parse_shared(data.get("shared", {}), source)
    route_rules, route_default = _parse_routes(data.get("routes", {}), accounts, source)
    return Config(root, bin_dir, accounts, defaults_env, defaults_args,
                  shared_dir, shared_items, route_rules, route_default)


def _parse_account(name: str, body: object, source: str) -> Account:
    where = "{} account {}".format(source, name)
    if not isinstance(body, dict):
        raise ConfigError("{} must be an object".format(where))
    unknown = set(body) - {"proxy", "isolate", "env", "args", "shared", "shared_exclude",
                           "managed_links", "dir"}
    if unknown:
        raise ConfigError("{} has unknown keys: {}".format(where, ", ".join(sorted(unknown))))
    try:
        proxy = normalize_proxy(body.get("proxy", PROXY_INHERIT))
    except ValueError as exc:
        raise ConfigError("{}: {}".format(where, exc))
    isolate = body.get("isolate", True)
    if not isinstance(isolate, bool):
        raise ConfigError("{} field isolate must be true or false".format(where))
    shared = body.get("shared", False)
    if not isinstance(shared, bool):
        raise ConfigError("{} field shared must be true or false".format(where))
    env = _parse_env(body.get("env", {}), "{} env".format(where))
    args = _parse_args(body.get("args", []), "{} args".format(where))
    shared_exclude = _parse_name_list(body.get("shared_exclude", []), "{} shared_exclude".format(where))
    managed_links = _parse_name_list(body.get("managed_links", []), "{} managed_links".format(where))
    directory = body.get("dir", name)
    if not isinstance(directory, str):
        raise ConfigError("{} field dir must be a string".format(where))
    if directory != name:
        try:
            validate_dir_name(directory)
        except ValueError as exc:
            raise ConfigError("{}: {}".format(where, exc))
    return Account(name, proxy, isolate, env, args, shared, shared_exclude, managed_links, directory)


def _parse_env(value: object, where: str) -> Dict[str, str]:
    if not isinstance(value, dict):
        raise ConfigError("{} must be an object of KEY=value strings".format(where))
    parsed: Dict[str, str] = {}
    for key, item in value.items():
        try:
            validate_env_key(key)
            if not isinstance(item, str):
                raise ValueError("value of {} must be a string".format(key))
            validate_value(item)
        except ValueError as exc:
            raise ConfigError("{}: {}".format(where, exc))
        parsed[key] = item
    return parsed


def _parse_args(value: object, where: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError("{} must be a list of strings".format(where))
    for item in value:
        try:
            validate_value(item)
        except ValueError as exc:
            raise ConfigError("{}: {}".format(where, exc))
    return list(value)


def validate_name(name: str) -> None:
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise ValueError(
            "invalid account name {!r}: use 1-64 characters from [A-Za-z0-9._@+-], "
            "starting with a letter or digit".format(name))
    if name.casefold() in RESERVED_NAMES:
        raise ValueError("account name {!r} is reserved for grok-auto".format(name))


def validate_dir_name(name: str) -> None:
    """目录名与账号名用同一套字符，但允许保留名 auto 作为改名后留下的目录。"""
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise ValueError(
            "invalid directory name {!r}: use 1-64 characters from [A-Za-z0-9._@+-], "
            "starting with a letter or digit".format(name))


def validate_shared_item(item: str) -> None:
    if item not in SHARED_ITEM_NAMES:
        raise ValueError(
            "unknown shared item {!r}; choose from {}".format(item, ", ".join(SHARED_ITEM_NAMES)))


def validate_env_key(key: str) -> None:
    """拒绝会破坏账号隔离、或会把秘密写进所有人可读的启动命令的变量名。"""
    if not isinstance(key, str) or not ENV_KEY_PATTERN.match(key):
        raise ValueError("invalid environment variable name {!r}".format(key))
    upper = key.upper()
    if upper in _IDENTITY_KEYS:
        raise ValueError("{} decides which login the account uses and cannot be set on a launcher".format(key))
    if upper in {var.upper() for var in PROXY_VARS}:
        raise ValueError("{} is managed by the proxy setting; use `multi-grok proxy`".format(key))
    if (upper in _CREDENTIAL_KEYS or upper == "API_KEY" or upper.endswith("_API_KEY")
            or any(segment in _CREDENTIAL_SEGMENTS for segment in upper.split("_"))):
        raise ValueError("{} looks like a credential; launchers are world-readable, so it is rejected".format(key))


def validate_value(value: str) -> None:
    if "\n" in value or "\r" in value or "\0" in value:
        raise ValueError("values must not contain line breaks or NUL characters")


def normalize_proxy(value: object) -> str:
    """把代理规范成 inherit、off，或 scheme://host:port。

    纯数字视为本机端口。带用户名或密码的 URL 拒绝：启动命令是 0755 明文文件。
    这里不判断 Grok 是否支持 SOCKS；本工具的启动命令只写 http 和 https。
    """
    if not isinstance(value, str) or not value:
        raise ValueError("proxy must be a port number, an http(s) URL, 'off' or 'inherit'")
    if value in (PROXY_INHERIT, PROXY_OFF):
        return value
    if value.isdigit():
        port = int(value)
        _check_port(port)
        return "http://127.0.0.1:{}".format(port)
    if "://" not in value:
        raise ValueError("proxy must be a port (e.g. 7890), an http(s) URL "
                         "(e.g. http://127.0.0.1:7890), off or inherit; got {!r}".format(value))
    parts = urllib.parse.urlsplit(value)
    if parts.scheme in ("socks5", "socks5h", "socks4", "socks4a", "socks"):
        raise ValueError("multi-grok only writes http and https proxy URLs into launchers; got {!r}".format(value))
    if parts.scheme not in PROXY_SCHEMES:
        raise ValueError("unsupported proxy scheme {!r}; use one of {}".format(
            parts.scheme, ", ".join(PROXY_SCHEMES)))
    if parts.username is not None or parts.password is not None:
        raise ValueError("proxy URL must not contain credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("proxy URL must not contain a path, query or fragment")
    host = parts.hostname
    if not host:
        raise ValueError("proxy URL must contain a host")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("proxy URL has an invalid port")
    if port is None:
        raise ValueError("proxy URL must contain a port")
    _check_port(port)
    if ":" in host:
        host = "[{}]".format(host)
    return "{}://{}:{}".format(parts.scheme, host, port)


def _check_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("proxy port must be between 1 and 65535, got {}".format(port))


def account_dir(config: Config, name: str) -> str:
    """账号目录的绝对路径。改名之后目录名在 account.dir，不再等于启动命令里的名字。"""
    account = config.find(name)
    directory = account.dir if account is not None else name
    return os.path.join(expand(config.root), directory)


def _parse_name_list(value: object, where: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError("{} must be a list of names".format(where))
    for item in value:
        try:
            validate_shared_item(item)
        except ValueError as exc:
            raise ConfigError("{}: {}".format(where, exc))
    return list(value)


def _parse_shared(value: object, source: str):
    if value in ({}, None):
        return None, None
    if not isinstance(value, dict):
        raise ConfigError("{} field shared must be an object".format(source))
    unknown = set(value) - {"dir", "items"}
    if unknown:
        raise ConfigError("{} shared has unknown keys: {}".format(source, ", ".join(sorted(unknown))))
    shared_dir = value.get("dir")
    if shared_dir is not None and (not isinstance(shared_dir, str) or not shared_dir):
        raise ConfigError("{} shared.dir must be a non-empty path".format(source))
    items = value.get("items")
    if items is None:
        return shared_dir, None
    parsed = _parse_name_list(items, "{} shared.items".format(source))
    return shared_dir, parsed


def _parse_routes(value: object, accounts: Dict[str, Account], source: str):
    if value in ({}, None):
        return [], None
    if not isinstance(value, dict):
        raise ConfigError("{} field routes must be an object".format(source))
    unknown = set(value) - {"default", "rules"}
    if unknown:
        raise ConfigError("{} routes has unknown keys: {}".format(source, ", ".join(sorted(unknown))))
    default = value.get("default")
    if default is not None:
        if not isinstance(default, str):
            raise ConfigError("{} routes.default must be an account name".format(source))
        if not any(account.name == default or account.name.casefold() == default.casefold()
                   for account in accounts.values()):
            raise ConfigError("{} routes.default {!r} is not an account".format(source, default))
    rules_raw = value.get("rules", [])
    if not isinstance(rules_raw, list):
        raise ConfigError("{} routes.rules must be a list".format(source))
    rules = []
    for rule in rules_raw:
        if not isinstance(rule, dict):
            raise ConfigError("{} routes.rules entries must be objects".format(source))
        extra = set(rule) - {"path", "account"}
        if extra:
            raise ConfigError("{} route has unknown keys: {}".format(source, ", ".join(sorted(extra))))
        path = rule.get("path")
        account = rule.get("account")
        if not isinstance(path, str) or not path:
            raise ConfigError("{} route path must be a non-empty string".format(source))
        if not isinstance(account, str) or not any(
                item.name == account or item.name.casefold() == account.casefold() for item in accounts.values()):
            raise ConfigError("{} route account {!r} is not an account".format(source, account))
        rules.append(RouteRule(path, account))
    return rules, default
