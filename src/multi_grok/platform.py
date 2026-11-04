"""默认路径。一期只支持 macOS 与 Linux。"""

import os
import sys

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
