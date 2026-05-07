from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .beijing_time import to_beijing


def parse_iso_utc(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso_local(dt: datetime) -> str:
    return to_beijing(dt).replace(microsecond=0).isoformat()


def load_journal(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def save_journal(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(x, ensure_ascii=False) for x in entries)
    path.write_text((body + "\n") if body else "", encoding="utf-8")


def has_active_idea(
    entries: list[dict[str, Any]],
    *,
    symbol: str,
    interval: str,
    direction: str,
    plan_type: str,
) -> bool:
    for e in entries:
        if str(e.get("symbol") or "") != symbol:
            continue
        if str(e.get("interval") or "") != interval:
            continue
        if str(e.get("direction") or "") != direction:
            continue
        if str(e.get("plan_type") or "tactical") != plan_type:
            continue
        if str(e.get("status") or "") in {"watch", "pending", "filled"}:
            return True
    return False


def _parse_rows(rows: list[dict[str, Any]]) -> list[tuple[datetime, float, float, float]]:
    parsed: list[tuple[datetime, float, float, float]] = []
    for r in rows:
        t = parse_iso_utc(str(r.get("time") or ""))
        if t is None:
            continue
        low = r.get("low")
        high = r.get("high")
        close = r.get("close")
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)) or not isinstance(close, (int, float)):
            continue
        parsed.append((t, float(low), float(high), float(close)))
    parsed.sort(key=lambda x: x[0])
    return parsed


def _transition_watch_pending_to_filled_or_expired(
    *,
    idea: dict[str, Any],
    parsed_rows: list[tuple[datetime, float, float, float]],
    created_at: datetime,
    valid_until: datetime,
    now_utc: datetime,
    entry_low: float,
    entry_high: float,
    entry_mid: float,
) -> tuple[bool, str]:
    """
    状态机（阶段一）：
    watch/pending -> filled / expired / unchanged
    """
    status = str(idea.get("status") or "pending")
    if status not in {"watch", "pending"}:
        return False, status
    for t, low, high, _ in parsed_rows:
        if t < created_at:
            continue
        if low <= entry_high and high >= entry_low:
            idea["status"] = "filled"
            idea["filled_at_utc"] = to_iso_local(t)
            idea["fill_price"] = round(entry_mid, 6)
            idea["exit_status"] = None
            return True, "filled"
    if now_utc > valid_until:
        idea["status"] = "expired"
        idea["exit_status"] = "time_expired"
        idea["updated_at_utc"] = to_iso_local(now_utc)
        return True, "expired"
    return False, status


def _transition_filled_to_closed_or_float(
    *,
    idea: dict[str, Any],
    parsed_rows: list[tuple[datetime, float, float, float]],
    filled_at: datetime,
    fill_price: float,
    direction: str,
    stop_px: float,
    tp1: float | None,
) -> bool:
    """
    状态机（阶段二）：
    filled -> closed(tp/sl) / float_*
    """
    changed = False
    for t, low, high, _ in parsed_rows:
        if t <= filled_at:
            continue
        sl_hit = (low <= stop_px) if direction == "long" else (high >= stop_px)
        tp_hit = False
        if tp1 is not None:
            tp_hit = (high >= tp1) if direction == "long" else (low <= tp1)
        if sl_hit and tp_hit:
            tp_hit = False  # 保守：同根双击按先止损
        if sl_hit:
            pnl_pct = (stop_px - fill_price) / max(fill_price, 1e-12) * 100.0
            if direction == "short":
                pnl_pct = -pnl_pct
            idea["status"] = "closed"
            idea["exit_status"] = "sl"
            idea["closed_at_utc"] = to_iso_local(t)
            idea["closed_price"] = round(stop_px, 6)
            idea["realized_pnl_pct"] = round(pnl_pct, 3)
            return True
        if tp_hit and tp1 is not None:
            pnl_pct = (tp1 - fill_price) / max(fill_price, 1e-12) * 100.0
            if direction == "short":
                pnl_pct = -pnl_pct
            idea["status"] = "closed"
            idea["exit_status"] = "tp"
            idea["closed_at_utc"] = to_iso_local(t)
            idea["closed_price"] = round(tp1, 6)
            idea["realized_pnl_pct"] = round(pnl_pct, 3)
            return True

    if str(idea.get("status") or "") == "filled":
        last_close = parsed_rows[-1][2]
        pnl = (last_close - fill_price) / max(fill_price, 1e-12) * 100.0
        if direction == "short":
            pnl = -pnl
        idea["unrealized_pnl_pct"] = round(pnl, 3)
        idea["exit_status"] = "float_profit" if pnl >= 0 else "float_loss"
        changed = True
    return changed


def update_idea_with_rows(idea: dict[str, Any], rows: list[dict[str, Any]], now_utc: datetime) -> bool:
    """
    基于最新 K 线驱动台账状态：
    watch/pending -> filled/expired；filled -> closed(tp/sl) 或 float_*。
    """
    changed = False
    status = str(idea.get("status") or "pending")
    if status not in {"watch", "pending", "filled"}:
        return False
    zone = idea.get("entry_zone")
    if not (isinstance(zone, list) and len(zone) == 2):
        return False
    entry_low = float(min(zone))
    entry_high = float(max(zone))
    entry_mid = float(idea.get("entry_price") or (entry_low + entry_high) / 2.0)
    stop_loss = idea.get("stop_loss")
    if not isinstance(stop_loss, (int, float)):
        return False
    stop_px = float(stop_loss)
    tps = idea.get("take_profit_levels") or []
    tp1 = float(tps[0]) if isinstance(tps, list) and tps and isinstance(tps[0], (int, float)) else None
    direction = str(idea.get("direction") or "long")

    created_at = parse_iso_utc(str(idea.get("created_at_utc") or "")) or now_utc
    valid_until = parse_iso_utc(str(idea.get("valid_until_utc") or "")) or now_utc
    filled_at = parse_iso_utc(str(idea.get("filled_at_utc") or "")) if idea.get("filled_at_utc") else None
    parsed = _parse_rows(rows)
    if not parsed:
        return False

    changed_1, status_after_1 = _transition_watch_pending_to_filled_or_expired(
        idea=idea,
        parsed_rows=parsed,
        created_at=created_at,
        valid_until=valid_until,
        now_utc=now_utc,
        entry_low=entry_low,
        entry_high=entry_high,
        entry_mid=entry_mid,
    )
    if changed_1:
        changed = True
    status = status_after_1
    if status == "filled":
        filled_at = parse_iso_utc(str(idea.get("filled_at_utc") or "")) or created_at

    if status != "filled":
        if changed:
            idea["updated_at_utc"] = to_iso_local(now_utc)
        return changed

    fill_price = float(idea.get("fill_price") or entry_mid)
    if filled_at is None:
        filled_at = created_at

    changed_2 = _transition_filled_to_closed_or_float(
        idea=idea,
        parsed_rows=parsed,
        filled_at=filled_at,
        fill_price=fill_price,
        direction=direction,
        stop_px=stop_px,
        tp1=tp1,
    )
    if changed_2:
        changed = True

    if changed:
        idea["updated_at_utc"] = to_iso_local(now_utc)
    return changed
