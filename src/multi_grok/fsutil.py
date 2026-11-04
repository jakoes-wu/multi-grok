"""与业务无关的文件操作。

调用方决定何时写、如何报告。本模块不读配置，也不打印日志。
删除只针对单个普通文件或软链，不提供递归删除目录：账号目录里有登录信息，
不能从这里误删整棵目录。
"""

import os
import stat
import tempfile
from typing import Optional

KIND_MISSING = "missing"
KIND_LINK = "link"
KIND_DIR = "dir"
KIND_FILE = "file"
KIND_SPECIAL = "special"


def expand(path: str) -> str:
    """展开 `~` 并转成绝对路径，不解析软链。"""
    return os.path.abspath(os.path.expanduser(path))


def is_under(path: str, root: str) -> bool:
    """path 等于 root，或位于 root 的某一级子目录下。

    按路径段比较。用字符串前缀会把 `~/.grok-accounts` 误判成位于 `~/.grok` 之下。
    """
    root = root.rstrip(os.sep) or os.sep
    if path == root:
        return True
    prefix = root if root.endswith(os.sep) else root + os.sep
    return path.startswith(prefix)


def entry_kind(path: str) -> str:
    """用 lstat 判断目录项类型，不跟随软链。断开的软链仍是 KIND_LINK。"""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return KIND_MISSING
    if stat.S_ISLNK(st.st_mode):
        return KIND_LINK
    if stat.S_ISDIR(st.st_mode):
        return KIND_DIR
    if stat.S_ISREG(st.st_mode):
        return KIND_FILE
    return KIND_SPECIAL


def atomic_write(path: str, content: str, mode: int = 0o644) -> None:
    """先写同目录临时文件并 fsync，再用 os.replace 换上。

    进程在中途被杀掉时，path 要么仍是旧内容，要么是完整的新内容。
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".multi-grok-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
    _fsync_dir(directory)


def _fsync_dir(directory: str) -> None:
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def read_text(path: str) -> Optional[str]:
    """读取 UTF-8 文本。不存在、不可读或不是文本时返回 None。跟随软链。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def unlink_file(path: str) -> None:
    """删除一个普通文件。软链、目录和其它类型一律不动，避免删到账号数据。"""
    if entry_kind(path) == KIND_FILE:
        os.unlink(path)


def unlink_link(path: str) -> None:
    """只删除软链。真实目录里可能有登录和会话，这里不能删。"""
    if entry_kind(path) == KIND_LINK:
        os.unlink(path)


def same_target(link: str, source: str) -> bool:
    """软链的目标是否就是 source。不解析目标本身是不是另一层软链。"""
    try:
        current = os.readlink(link)
    except OSError:
        return False
    return os.path.abspath(current) == os.path.abspath(source)
