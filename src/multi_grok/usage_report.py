"""把官方 `grok usage` 的 JSON 收成按日或按模型的数字合计。

只复制约定的数字字段。`turns` 里的文本留在原始对象里，调用方不得把它写进输出。
不读 signals.json、usage.json 或 jsonl。那些文件的入口是 `grok usage`（用户指南 17-sessions.md）。
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .sessions import parse_stamp

# grok 1.0.70 用量对象上的 camelCase 字段。状态栏文档的 snake_case 不是这份 JSON。
COUNT_KEYS = (
    "inputTokens",
    "outputTokens",
    "cacheCreationTokens",
    "reasoningTokens",
    "totalTokens",
    "modelCalls",
    "costUsdTicks",
)
_TICKS_PER_USD = 10 ** 10


class UsageReportError(Exception):
    """`grok usage` 的输出不是本模块能加总的 JSON 对象。"""


def prepare_rows(rows: List[dict], days: int, now: datetime) -> Tuple[List[dict], int, List[str]]:
    """按会话 id 去重，再按 summary.json 的 updated_at 做时间窗。

    days 为 0 时不截断。大于 0 时，没有可用时间的会话不进入窗口，只计入返回的个数。
    """
    if now.tzinfo is None:
        raise UsageReportError("history window requires a timezone")
    kept_by_id: Dict[str, dict] = {}
    order: List[str] = []
    duplicates: Dict[str, int] = {}
    for row in rows:
        session_id = row.get("id") if isinstance(row.get("id"), str) else ""
        if not session_id:
            continue
        if session_id not in kept_by_id:
            kept_by_id[session_id] = row
            order.append(session_id)
            continue
        duplicates[session_id] = duplicates.get(session_id, 1) + 1
        if _newer(row, kept_by_id[session_id]):
            kept_by_id[session_id] = row
    notes = [
        "session {} appears in {} directories; grok usage is called once".format(session_id, count)
        for session_id, count in sorted(duplicates.items())
    ]
    chosen = [kept_by_id[session_id] for session_id in order]
    if days == 0:
        chosen.sort(key=lambda row: row.get("id") or "")
        return chosen, 0, notes
    cutoff = now - timedelta(days=days)
    kept: List[dict] = []
    skipped = 0
    for row in chosen:
        stamp = parse_stamp(row.get("updated") or "")
        if stamp is None:
            skipped += 1
            continue
        if stamp >= cutoff:
            kept.append(row)
    kept.sort(key=lambda row: row.get("id") or "")
    return kept, skipped, notes


def fold_payload(payload: object, fallback_updated: str, by: str) -> dict:
    """从一个 `grok usage` 对象抽出可加总的桶。

    按日时，任一 turn 带有数字就只用 turn，避免和 session 合计加两次。
    turn 没有数字时，把 session 合计放到 fallback_updated 那天。
    """
    if not isinstance(payload, dict):
        raise UsageReportError("not an object")
    session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
    partial = _flag(session, "costIsPartial") or _flag(session, "usageIsIncomplete") or _flag(payload, "usageIsIncomplete")
    if by == "model":
        buckets = _model_buckets(session)
    elif by == "day":
        buckets = _day_buckets(payload, session, fallback_updated)
    else:
        raise UsageReportError("unknown grouping {!r}".format(by))
    return {"partial": partial, "buckets": buckets}


def combine(parts: List[dict]) -> dict:
    """把每个会话的桶加到一起。美元用整数刻度换算，避免浮点误差。"""
    table: Dict[str, Dict[str, int]] = {}
    partial = False
    for part in parts:
        partial = partial or bool(part.get("partial"))
        for key, counts in part.get("buckets") or []:
            slot = table.setdefault(key, _zeros())
            _add(slot, counts)
    rows = []
    for key in sorted(table, key=lambda item: (item.startswith("("), item)):
        counts = table[key]
        row = {"key": key}
        row.update(counts)
        row["costUsd"] = format_usd(counts["costUsdTicks"])
        rows.append(row)
    total = _zeros()
    for counts in table.values():
        _add(total, counts)
    total_row = {"key": "total"}
    total_row.update(total)
    total_row["costUsd"] = format_usd(total["costUsdTicks"])
    return {"partial": partial, "rows": rows, "total": total_row}


def format_usd(ticks: int) -> str:
    """10^10 个刻度等于 1 美元。返回不带指数的十进制字符串。"""
    sign = "-" if ticks < 0 else ""
    whole, frac = divmod(abs(int(ticks)), _TICKS_PER_USD)
    if frac == 0:
        return "{}{}".format(sign, whole)
    text = "{:010d}".format(frac).rstrip("0")
    return "{}{}.{}".format(sign, whole, text)


def format_counts(row: dict) -> str:
    parts = [str(row.get("key", ""))]
    for key in COUNT_KEYS:
        parts.append("{}={}".format(key, row.get(key, 0)))
    parts.append("costUsd={}".format(row.get("costUsd", "0")))
    return "  ".join(parts)


def _day_buckets(payload: dict, session: dict, fallback_updated: str) -> List[Tuple[str, Dict[str, int]]]:
    turns = payload.get("turns")
    buckets: List[Tuple[str, Dict[str, int]]] = []
    if isinstance(turns, list):
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            counts = _counts(turn)
            if not counts:
                continue
            stamp = _stamp_field(turn, "endedAt") or _stamp_field(turn, "updatedAt")
            buckets.append((_day(stamp) if stamp is not None else "(undated)", counts))
    if buckets:
        return buckets
    stamp = parse_stamp(fallback_updated)
    return [(_day(stamp) if stamp is not None else "(undated)", _counts(session))]


def _model_buckets(session: dict) -> List[Tuple[str, Dict[str, int]]]:
    model_usage = session.get("modelUsage")
    buckets: List[Tuple[str, Dict[str, int]]] = []
    if isinstance(model_usage, dict):
        for name, body in model_usage.items():
            if not isinstance(body, dict):
                continue
            counts = _counts(body)
            # 空对象不算一组，否则 session 上的合计会被丢掉。
            if not counts:
                continue
            buckets.append((_model_name(name), counts))
    if buckets:
        return buckets
    return [(_model_name(session.get("primaryModelId")), _counts(session))]


def _counts(value: object) -> Dict[str, int]:
    if not isinstance(value, dict):
        return {}
    found: Dict[str, int] = {}
    for key in COUNT_KEYS:
        number = _number(value.get(key))
        if number is not None:
            found[key] = number
    return found


def _number(value: object) -> Optional[int]:
    # bool 是 int 的子类，不能把 true 当成 1 个 token。
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    return int(value)


def _flag(obj: object, key: str) -> bool:
    return isinstance(obj, dict) and obj.get(key) is True


def _stamp_field(obj: dict, key: str) -> Optional[datetime]:
    value = obj.get(key)
    if not isinstance(value, str):
        return None
    return parse_stamp(value)


def _day(stamp: datetime) -> str:
    # parse_stamp 已经换成 UTC。这里再转一次，避免调用方传入别的时区时把日期算偏。
    return stamp.astimezone(timezone.utc).date().isoformat()


def _model_name(value: object) -> str:
    if not isinstance(value, str):
        return "(unknown)"
    text = value.strip()
    if not text or len(text) > 80 or any(ord(char) < 32 for char in text):
        return "(unknown)"
    return text


def _newer(left: dict, right: dict) -> bool:
    left_stamp = parse_stamp(left.get("updated") or "")
    right_stamp = parse_stamp(right.get("updated") or "")
    if left_stamp is not None and right_stamp is not None and left_stamp != right_stamp:
        return left_stamp > right_stamp
    return (left.get("updated") or "") > (right.get("updated") or "")


def _zeros() -> Dict[str, int]:
    return {key: 0 for key in COUNT_KEYS}


def _add(dst: Dict[str, int], src: Dict[str, int]) -> None:
    for key in COUNT_KEYS:
        dst[key] += src.get(key, 0)
