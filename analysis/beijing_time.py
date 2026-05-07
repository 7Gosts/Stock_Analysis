"""北京时间（UTC+8，无夏令时）：展示与输出目录「交易日历日」口径。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# 与 IANA Asia/Shanghai 一致（中国标准时间，全年 UTC+8）
BEIJING = timezone(timedelta(hours=8))


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def to_beijing(dt: datetime) -> datetime:
    return as_utc(dt).astimezone(BEIJING)


def format_beijing(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return to_beijing(dt).strftime(fmt)


def now_beijing() -> datetime:
    return datetime.now(BEIJING)


def now_beijing_str(fmt: str = "%Y-%m-%d %H:%M") -> str:
    return now_beijing().strftime(fmt)


def beijing_calendar_day(dt: datetime) -> str:
    """用于 output/.../YYYY-MM-DD/ 分桶的日历日（按北京时间）。"""
    return to_beijing(dt).strftime("%Y-%m-%d")


# 与多数加密/贵金属 K 线一致的 UTC 整周期收盘（15m/30m/1h/4h）
_INTERVAL_MINUTES_UTC: dict[str, int] = {
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "60m": 60,
    "4h": 240,
    "240m": 240,
}


def next_utc_aligned_bar_close_beijing(*, interval: str, now_bj: datetime | None = None) -> datetime | None:
    """下一根 K 线 UTC 整周期「收盘」时刻，返回带 Asia/Shanghai 的 datetime。"""
    iv = (interval or "").strip().lower()
    step_min = _INTERVAL_MINUTES_UTC.get(iv)
    if step_min is None:
        return None
    now = now_bj or now_beijing()
    utc_now = now.astimezone(timezone.utc)
    step_sec = step_min * 60
    epoch = int(utc_now.timestamp())
    next_close_epoch = ((epoch // step_sec) + 1) * step_sec
    return datetime.fromtimestamp(next_close_epoch, tz=timezone.utc).astimezone(BEIJING)


def default_review_time_for_interval(interval: str) -> str:
    """规则链默认「下次复核时间」：子日线给出下一根 K 收盘的北京时间点（UTC 周期边界）。"""
    iv = (interval or "").strip().lower()
    if iv in {"1d", "1day"}:
        return "下个交易日收盘后复核（北京时间）"
    dt = next_utc_aligned_bar_close_beijing(interval=iv)
    if dt is None:
        return f"下一根{interval}收盘后复核（北京时间）"
    ts = dt.strftime("%Y-%m-%d %H:%M")
    return f"{ts}（北京时间，下一根{interval}收盘）"


_REVIEW_HAS_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_REVIEW_HAS_CLOCK = re.compile(r"\d{1,2}:\d{2}")


def review_time_has_explicit_clock(s: str) -> bool:
    """是否含「YYYY-MM-DD」与「H:mm」式时刻（用于判断规则层具体复核点是否应覆盖 LLM 模糊句）。"""
    t = (s or "").strip()
    if not t:
        return False
    return bool(_REVIEW_HAS_DATE.search(t) and _REVIEW_HAS_CLOCK.search(t))
