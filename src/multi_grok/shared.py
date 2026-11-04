"""把共享目录里的技能、命令等条目软链进账号目录。

Grok 的用户指南写明：技能目录在 `$GROK_HOME/skills`，并且会识别 `SKILL.md` 软链
（08-skills.md）。`GROK_HOME` 本身必须是真实目录（18-sandbox.md），共享条目是它下面的软链。
链接上带不了受管标记，所以“是不是本工具建的”只记在账号的 managed_links 里。
用户自己已经指向同一位置的软链，关闭共享时不删。
"""

import os
from typing import List, Optional

from .actions import CONFLICT, CREATE, DELETE, SKIP, UNCHANGED, UPDATE, Action
from .config import Account, Config
from .fsutil import KIND_LINK, KIND_MISSING, entry_kind, expand, same_target, unlink_link


def plan_shared(config: Config, account: Account, account_dir: str,
                old_shared_dir: Optional[str]) -> List[Action]:
    """为一个账号生成共享链接动作，并就地更新 account.managed_links。"""
    actions: List[Action] = []
    desired = {}
    if account.shared:
        if not config.shared_dir:
            return [Action(CONFLICT, "shared-link", account_dir,
                           "shared.dir is not set; run `multi-grok init --shared-dir DIR` or `add --shared DIR`")]
        shared_root = expand(config.shared_dir)
        for item in config.shared_items:
            if item in account.shared_exclude:
                continue
            source = os.path.join(shared_root, item)
            if entry_kind(source) == KIND_MISSING:
                continue
            desired[item] = source

    old_root = expand(old_shared_dir) if old_shared_dir else None
    kept: List[str] = []
    for item, source in desired.items():
        link = os.path.join(account_dir, item)
        kind = entry_kind(link)
        if kind == KIND_MISSING:
            actions.append(Action(CREATE, "shared-link", link, "-> " + source, _create_link(source, link)))
            kept.append(item)
        elif kind == KIND_LINK and same_target(link, source):
            # 没有 --adopt 时，用户原先就指向这里的软链只保留，不记进 managed_links，关闭共享时不删。
            if item in account.managed_links or getattr(account, "_adopt", False):
                status = UNCHANGED if item in account.managed_links else UPDATE
                actions.append(Action(status, "shared-link", link, "" if status == UNCHANGED else "adopted"))
                kept.append(item)
            else:
                actions.append(Action(UNCHANGED, "shared-link", link))
        elif kind == KIND_LINK and item in account.managed_links and _points_at(link, old_root, item):
            actions.append(Action(UPDATE, "shared-link", link, "-> " + source, _replace_link(source, link)))
            kept.append(item)
        else:
            actions.append(Action(CONFLICT, "shared-link", link,
                                  "already exists and is not a link to the shared item"))

    for item in list(account.managed_links):
        if item in desired:
            continue
        link = os.path.join(account_dir, item)
        kind = entry_kind(link)
        if kind == KIND_MISSING:
            continue
        if kind == KIND_LINK and (_points_at(link, old_root, item) or _points_at(link, expand(config.shared_dir) if config.shared_dir else None, item)):
            actions.append(Action(DELETE, "shared-link", link, "sharing disabled for this item", _unlink(link)))
        else:
            actions.append(Action(SKIP, "shared-link", link, "no longer the link multi-grok created; left untouched"))

    order = {item: index for index, item in enumerate(config.shared_items)}
    account.managed_links = sorted(set(kept), key=lambda item: order.get(item, len(order)))
    return actions


def plan_remove_links(account: Account, account_dir: str, shared_dir: Optional[str]) -> List[Action]:
    """注销账号时，只删除仍指向共享目录的受管软链。账号目录本身不动。"""
    actions: List[Action] = []
    root = expand(shared_dir) if shared_dir else None
    for item in account.managed_links:
        link = os.path.join(account_dir, item)
        if entry_kind(link) == KIND_LINK and _points_at(link, root, item):
            actions.append(Action(DELETE, "shared-link", link, "account removed", _unlink(link)))
    return actions


def _points_at(link: str, root: Optional[str], item: str) -> bool:
    if not root:
        return False
    return same_target(link, os.path.join(root, item))


def _create_link(source: str, link: str):
    def run() -> None:
        os.symlink(source, link)
    return run


def _replace_link(source: str, link: str):
    def run() -> None:
        unlink_link(link)
        os.symlink(source, link)
    return run


def _unlink(link: str):
    def run() -> None:
        unlink_link(link)
    return run
