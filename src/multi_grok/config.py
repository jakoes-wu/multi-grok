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


class Account(object):
    """一个已登记的账号。目录名等于账号名，不支持改名后仍指向旧目录。"""

    def __init__(self, name: str, proxy: str = PROXY_INHERIT, isolate: bool = True,
                 env: Optional[Dict[str, str]] = None, args: Optional[List[str]] = None) -> None:
        self.name = name
        self.proxy = proxy
        self.isolate = isolate
        self.env = dict(env or {})
        self.args = list(args or [])

    def copy(self) -> "Account":
        return Account(self.name, self.proxy, self.isolate, dict(self.env), list(self.args))

    def to_dict(self) -> dict:
        return {"proxy": self.proxy, "isolate": self.isolate,
                "env": dict(self.env), "args": list(self.args)}


class Config(object):
    def __init__(self, root: str, bin_dir: str, accounts: Dict[str, Account],
                 defaults_env: Optional[Dict[str, str]] = None,
                 defaults_args: Optional[List[str]] = None) -> None:
        self.root = root
        self.bin_dir = bin_dir
        self.accounts = accounts
        self.defaults_env = dict(defaults_env or {})
        self.defaults_args = list(defaults_args or [])

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
                      dict(self.defaults_env), list(self.defaults_args))

    def to_dict(self) -> dict:
        return {
            "version": CONFIG_VERSION,
            "root": self.root,
            "bin_dir": self.bin_dir,
            "defaults": {"env": dict(self.defaults_env), "args": list(self.defaults_args)},
            "accounts": {name: account.to_dict() for name, account in self.accounts.items()},
        }


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
    unknown = set(data) - {"version", "root", "bin_dir", "defaults", "accounts"}
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
    return Config(root, bin_dir, accounts, defaults_env, defaults_args)


def _parse_account(name: str, body: object, source: str) -> Account:
    where = "{} account {}".format(source, name)
    if not isinstance(body, dict):
        raise ConfigError("{} must be an object".format(where))
    unknown = set(body) - {"proxy", "isolate", "env", "args"}
    if unknown:
        raise ConfigError("{} has unknown keys: {}".format(where, ", ".join(sorted(unknown))))
    try:
        proxy = normalize_proxy(body.get("proxy", PROXY_INHERIT))
    except ValueError as exc:
        raise ConfigError("{}: {}".format(where, exc))
    isolate = body.get("isolate", True)
    if not isinstance(isolate, bool):
        raise ConfigError("{} field isolate must be true or false".format(where))
    env = _parse_env(body.get("env", {}), "{} env".format(where))
    args = _parse_args(body.get("args", []), "{} args".format(where))
    return Account(name, proxy, isolate, env, args)


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
    """账号目录的绝对路径。不解析软链，调用方再用 lstat 判断它是不是真实目录。"""
    return os.path.join(expand(config.root), name)
