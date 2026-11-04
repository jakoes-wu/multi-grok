"""导出、导入账号里的技能和配置目录。

不打包 auth.json、sessions、memory-v2、logs、config.toml。
config.toml 里可以有 MCP 请求头，那些内容不进压缩包。
压缩包里只允许普通文件和目录，拒绝软链、绝对路径和 `..`。
"""

import os
import shutil
import tarfile
import tempfile
from typing import List

from .config import SHARED_ITEM_NAMES
from .fsutil import KIND_DIR, KIND_LINK, KIND_MISSING, entry_kind

FORMAT = "multi-grok-bundle"
VERSION = 1
ITEMS_DIR = "items"


class BundleError(Exception):
    """压缩包不是本工具写的，或路径不安全。"""


def export_account(account_directory: str, out_path: str) -> List[str]:
    """把存在的真实目录打进 out_path。软链条目跳过，避免把共享目录整棵拷走。"""
    exported = [item for item in SHARED_ITEM_NAMES
                if entry_kind(os.path.join(account_directory, item)) == KIND_DIR]
    parent = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".multi-grok-bundle-", suffix=".tar.gz", dir=parent)
    os.close(fd)
    try:
        with tarfile.open(tmp_path, "w:gz") as archive:
            _add_bytes(archive, "manifest.json", _manifest_bytes(exported))
            for item in exported:
                _add_tree(archive, os.path.join(account_directory, item), "{}/{}".format(ITEMS_DIR, item))
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, out_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
    return exported


def import_account(account_directory: str, archive_path: str, force: bool) -> List[str]:
    """把压缩包里的目录放进已有账号。已存在的真实目录在没有 --force 时拒绝。软链一律不覆盖。"""
    staging = tempfile.mkdtemp(prefix=".multi-grok-import-")
    try:
        manifest = _extract(archive_path, staging)
        items = manifest.get("items") or []
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise BundleError("bundle manifest items must be a list of names")
        planned: List[str] = []
        for item in items:
            if item not in SHARED_ITEM_NAMES:
                raise BundleError("bundle contains an item this tool does not import: {}".format(item))
            staged = os.path.join(staging, ITEMS_DIR, item)
            if entry_kind(staged) != KIND_DIR:
                raise BundleError("bundle item {} is not a directory".format(item))
            target = os.path.join(account_directory, item)
            kind = entry_kind(target)
            if kind == KIND_LINK:
                raise BundleError("{} is a shared link; exclude it from sharing before import".format(item))
            if kind not in (KIND_MISSING, KIND_DIR):
                raise BundleError("{} exists and is not a directory".format(target))
            if kind == KIND_DIR and not force:
                raise BundleError("{} already exists; pass --force to replace that directory".format(item))
            planned.append(item)
        for item in planned:
            target = os.path.join(account_directory, item)
            if entry_kind(target) == KIND_DIR:
                backup = target + ".bak"
                suffix = 1
                while os.path.lexists(backup):
                    suffix += 1
                    backup = "{}.bak.{}".format(target, suffix)
                os.rename(target, backup)
            shutil.move(os.path.join(staging, ITEMS_DIR, item), target)
        return planned
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging)


def _extract(archive_path: str, staging: str) -> dict:
    try:
        archive = tarfile.open(archive_path, "r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise BundleError("cannot read {}: {}".format(archive_path, exc))
    with archive:
        members = archive.getmembers()
        for member in members:
            _check_member(member)
        # filter='data' 在 3.12 才有。这里成员已经逐个检查过，再按成员解压。
        for member in members:
            archive.extract(member, staging)
    manifest_path = os.path.join(staging, "manifest.json")
    try:
        import json
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        raise BundleError("bundle has no readable manifest: {}".format(exc))
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
        raise BundleError("not a multi-grok bundle")
    return manifest


def _check_member(member: tarfile.TarInfo) -> None:
    name = member.name
    parts = name.split("/")
    if name.startswith("/") or "\\" in name or "\x00" in name or any(part in ("", ".", "..") for part in parts):
        raise BundleError("unsafe path in bundle: {!r}".format(name))
    if not (member.isfile() or member.isdir()):
        raise BundleError("unsupported entry in bundle: {!r}".format(name))
    if parts[0] == "manifest.json":
        if len(parts) != 1 or not member.isfile():
            raise BundleError("unexpected entry in bundle: {!r}".format(name))
        return
    if parts[0] != ITEMS_DIR or len(parts) < 2 or parts[1] not in SHARED_ITEM_NAMES:
        raise BundleError("unexpected entry in bundle: {!r}".format(name))


def _add_tree(archive: tarfile.TarFile, source: str, arcname: str) -> None:
    info = tarfile.TarInfo(arcname)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    archive.addfile(info)
    for root, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [name for name in dirs if entry_kind(os.path.join(root, name)) == KIND_DIR]
        rel = os.path.relpath(root, source)
        base = arcname if rel == "." else "{}/{}".format(arcname, rel.replace(os.sep, "/"))
        for name in files:
            path = os.path.join(root, name)
            if entry_kind(path) != "file":
                continue
            archive.add(path, "{}/{}".format(base, name), recursive=False)


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    import io
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(data))


def _manifest_bytes(items: List[str]) -> bytes:
    import json
    return (json.dumps({"format": FORMAT, "version": VERSION, "items": items}, indent=2) + "\n").encode("utf-8")
