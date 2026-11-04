"""从每个账号目录的 summary.json 列出会话，并按用户要求把一个会话目录复制到另一个账号。

只读 summary.json 里的索引字段（用户指南 17-sessions.md「Session Metadata」）。
不打开 updates.jsonl、chat_history.jsonl，也不打开 auth.json。
`grok usage` 是读取用量的官方入口；本模块不解析 signals.json。
"""

import json
import os
import shutil
import stat
from typing import Dict, List, Optional

from .fsutil import KIND_DIR, KIND_FILE, KIND_MISSING, entry_kind


class SessionError(Exception):
    """会话路径不合法，或同一个 id 对上了多处目录。"""


def list_account(account_name: str, account_directory: str) -> List[dict]:
    """列出一个账号目录里能读到 summary.json 的会话。坏文件跳过，不让整份列表失败。"""
    root = os.path.join(account_directory, "sessions")
    if entry_kind(root) != KIND_DIR:
        return []
    found: List[dict] = []
    try:
        groups = os.listdir(root)
    except OSError:
        return []
    for group in groups:
        group_path = os.path.join(root, group)
        if entry_kind(group_path) != KIND_DIR:
            continue
        try:
            names = os.listdir(group_path)
        except OSError:
            continue
        for name in names:
            session_path = os.path.join(group_path, name)
            summary_path = os.path.join(session_path, "summary.json")
            if entry_kind(session_path) != KIND_DIR or entry_kind(summary_path) != KIND_FILE:
                continue
            row = _read_summary(account_name, session_path, summary_path, name)
            if row is not None:
                found.append(row)
    return found


def find_one(account_directory: str, session_id: str) -> str:
    """按会话 id 找到唯一的会话目录。id 只能是单层目录名。"""
    _check_id(session_id)
    root = os.path.join(account_directory, "sessions")
    matches: List[str] = []
    if entry_kind(root) != KIND_DIR:
        raise SessionError("no sessions directory in {}".format(account_directory))
    for group in os.listdir(root):
        group_path = os.path.join(root, group)
        if entry_kind(group_path) != KIND_DIR:
            continue
        session_path = os.path.join(group_path, session_id)
        summary = os.path.join(session_path, "summary.json")
        if entry_kind(session_path) == KIND_DIR and entry_kind(summary) == KIND_FILE:
            matches.append(session_path)
    if not matches:
        raise SessionError("session {!r} was not found".format(session_id))
    if len(matches) > 1:
        raise SessionError("session {!r} exists in {} directories; refuse to guess".format(
            session_id, len(matches)))
    return matches[0]


def handoff(source_dir: str, target_account_dir: str, session_id: str, force: bool) -> str:
    """把会话目录复制到目标账号的同一相对路径下。不跟随软链。

    失败时只删除本次新建的目标目录。源目录和目标账号里的其它文件不动。
    """
    source = find_one(source_dir, session_id)
    relative = os.path.relpath(source, source_dir)
    target = os.path.join(target_account_dir, relative)
    if os.path.commonpath([os.path.abspath(target), os.path.abspath(target_account_dir)]) != os.path.abspath(target_account_dir):
        raise SessionError("refusing to write outside the target account")
    kind = entry_kind(target)
    if kind not in (KIND_MISSING, KIND_DIR):
        raise SessionError("target path is not a directory: {}".format(target))
    if kind == KIND_DIR and not force:
        raise SessionError("target already has this session; pass --force to replace that session directory")
    parent = os.path.dirname(target)
    if entry_kind(parent) == KIND_MISSING:
        os.makedirs(parent, mode=0o700)
    # 先复制到旁边的目录。失败时只删这份未完成的副本，目标里原来的会话还在。
    partial = target + ".partial"
    if entry_kind(partial) != KIND_MISSING:
        raise SessionError("a partial copy is already at {}; remove it before retrying".format(partial))
    try:
        _copy_regular_tree(source, partial)
    except BaseException:
        if entry_kind(partial) == KIND_DIR:
            shutil.rmtree(partial)
        raise
    backup = None
    if kind == KIND_DIR:
        backup = target + ".bak"
        os.rename(target, backup)
    try:
        os.rename(partial, target)
    except BaseException:
        if backup is not None and entry_kind(target) == KIND_MISSING:
            os.rename(backup, target)
        if entry_kind(partial) == KIND_DIR:
            shutil.rmtree(partial)
        raise
    if backup is not None:
        shutil.rmtree(backup)
    return target


def _copy_regular_tree(source: str, target: str) -> None:
    """只复制普通文件和真实目录。软链、设备文件会中断复制，避免把链接指到账号外面。"""
    os.mkdir(target)
    mode = stat.S_IMODE(os.lstat(source).st_mode)
    os.chmod(target, mode)
    for name in os.listdir(source):
        src = os.path.join(source, name)
        dst = os.path.join(target, name)
        kind = entry_kind(src)
        if kind == KIND_DIR:
            _copy_regular_tree(src, dst)
        elif kind == KIND_FILE:
            shutil.copyfile(src, dst)
            os.chmod(dst, stat.S_IMODE(os.lstat(src).st_mode))
        else:
            raise SessionError("refusing to copy {}: not a regular file or directory".format(src))


def _read_summary(account: str, session_path: str, summary_path: str, fallback_id: str) -> Optional[dict]:
    try:
        with open(summary_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    info = data.get("info") if isinstance(data.get("info"), dict) else {}
    session_id = info.get("id") if isinstance(info.get("id"), str) and info.get("id") else fallback_id
    cwd = info.get("cwd") if isinstance(info.get("cwd"), str) else ""
    title = ""
    if data.get("title_is_manual") and isinstance(data.get("title"), str):
        title = data["title"]
    elif isinstance(data.get("generated_title"), str) and data.get("generated_title"):
        title = data["generated_title"]
    elif isinstance(data.get("session_summary"), str):
        title = data["session_summary"]
    title = " ".join(title.split())
    if len(title) > 80:
        title = title[:79] + "…"
    model = data.get("current_model_id") if isinstance(data.get("current_model_id"), str) else ""
    updated = data.get("updated_at") if isinstance(data.get("updated_at"), str) else ""
    return {
        "account": account,
        "id": session_id,
        "cwd": cwd,
        "title": title,
        "model": model,
        "updated": updated,
        "path": session_path,
    }


def _check_id(session_id: str) -> None:
    if not session_id or session_id in (".", "..") or "/" in session_id or "\\" in session_id or "\0" in session_id:
        raise SessionError("invalid session id {!r}".format(session_id))


def matches(row: Dict[str, str], needle: str) -> bool:
    folded = needle.casefold()
    haystack = " ".join([row.get("account", ""), row.get("id", ""), row.get("cwd", ""),
                         row.get("title", ""), row.get("model", "")]).casefold()
    return folded in haystack
