"""先算出完整动作列表，有冲突就一条都不执行。

计划阶段只读文件系统。真正的写入都放在 Action.run 里，执行阶段才调用。
"""

import sys
from typing import Callable, List, Optional

CREATE = "create"
UPDATE = "update"
DELETE = "delete"
UNCHANGED = "unchanged"
CONFLICT = "conflict"
SKIP = "skip"

LOG_PREFIX = "[multi-grok]"


class Action(object):
    def __init__(self, status: str, kind: str, path: str, reason: str = "",
                 run: Optional[Callable[[], None]] = None) -> None:
        self.status = status
        self.kind = kind
        self.path = path
        self.reason = reason
        self.run = run

    @property
    def changes(self) -> bool:
        return self.status in (CREATE, UPDATE, DELETE)


def has_conflict(actions: List[Action]) -> bool:
    return any(action.status == CONFLICT for action in actions)


def print_action(action: Action, dry_run: bool = False) -> None:
    status = action.status + (" (dry-run)" if dry_run and action.changes else "")
    line = "{} {} {} {}".format(LOG_PREFIX, status, action.kind, action.path)
    if action.reason:
        line += " ({})".format(action.reason)
    stream = sys.stderr if action.status == CONFLICT else sys.stdout
    print(line, file=stream)


def warn(message: str) -> None:
    print("{} warning: {}".format(LOG_PREFIX, message), file=sys.stderr)


def error(message: str) -> None:
    print("{} error: {}".format(LOG_PREFIX, message), file=sys.stderr)
