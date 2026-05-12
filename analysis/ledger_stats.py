#!/usr/bin/env python3
"""
股票交易台账统计（周/月）。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from analysis.beijing_time import format_beijing
from persistence.journal_repository_factory import load_journal_entries_for_stats
from config.runtime_config import get_journal_action_thresholds

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _action_thresholds() -> tuple[float, float]:
    return get_journal_action_thresholds()


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


def safe_pct(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(num / den * 100.0, 2)


def period_stats(entries: list[dict[str, Any]], *, now_utc: datetime, days: int) -> dict[str, Any]:
    start = now_utc - timedelta(days=days)
    scoped: list[dict[str, Any]] = []
    for e in entries:
        created = parse_iso_utc(str(e.get("created_at_utc") or ""))
        if created is None:
            continue
        if start <= created <= now_utc:
            scoped.append(e)

    total = len(scoped)
    hit = 0
    tp = 0
    sl = 0
    wins: list[float] = []
    losses: list[float] = []
    for e in scoped:
        status = str(e.get("status") or "")
        if status in {"filled", "closed"} or e.get("filled_at_utc"):
            hit += 1
        ex = str(e.get("exit_status") or "")
        if ex == "tp":
            tp += 1
        elif ex == "sl":
            sl += 1
        rp = e.get("realized_pnl_pct")
        if isinstance(rp, (int, float)):
            rv = float(rp)
            if rv > 0:
                wins.append(rv)
            elif rv < 0:
                losses.append(rv)

    closed_ts = tp + sl
    avg_win = (sum(wins) / len(wins)) if wins else None
    avg_loss_abs = (abs(sum(losses) / len(losses))) if losses else None
    avg_rr = None
    if avg_win is not None and avg_loss_abs and avg_loss_abs > 1e-12:
        avg_rr = round(avg_win / avg_loss_abs, 3)
    return {
        "days": days,
        "candidate_total": total,
        "hit_count": hit,
        "hit_rate_pct": safe_pct(hit, total),
        "tp_count": tp,
        "sl_count": sl,
        "tp_rate_pct": safe_pct(tp, closed_ts),
        "sl_rate_pct": safe_pct(sl, closed_ts),
        "avg_rr": avg_rr,
    }


def period_breakdown(entries: list[dict[str, Any]], *, now_utc: datetime, days: int) -> dict[str, Any]:
    """近 N 天创建条目的分层计数 + 时间止损过期仍挂单数。"""
    start = now_utc - timedelta(days=days)
    scoped: list[dict[str, Any]] = []
    for e in entries:
        created = parse_iso_utc(str(e.get("created_at_utc") or ""))
        if created is None or not (start <= created <= now_utc):
            continue
        scoped.append(e)

    stale = 0
    active_count = 0
    expired_count = 0
    by_symbol_active_expired: dict[str, dict[str, int]] = {}
    for e in scoped:
        status = str(e.get("status") or "")
        symbol = str(e.get("symbol") or "UNKNOWN")
        by_symbol_active_expired.setdefault(symbol, {"active": 0, "expired": 0})
        if status in {"watch", "pending", "filled"}:
            active_count += 1
            by_symbol_active_expired[symbol]["active"] += 1
        elif status == "expired":
            expired_count += 1
            by_symbol_active_expired[symbol]["expired"] += 1
        if status not in {"pending", "watch"}:
            continue
        ddl = parse_iso_utc(str(e.get("time_stop_deadline_utc") or ""))
        if ddl and ddl < now_utc:
            stale += 1

    def bucket(field: str) -> dict[str, int]:
        m: dict[str, int] = {}
        for e in scoped:
            k = str(e.get(field) or "unknown").strip() or "unknown"
            m[k] = m.get(k, 0) + 1
        return dict(sorted(m.items(), key=lambda kv: (-kv[1], kv[0])))

    return {
        "days": days,
        "candidate_total": len(scoped),
        "by_status": bucket("status"),
        "by_wyckoff_bias": bucket("wyckoff_bias"),
        "active_count": active_count,
        "expired_count": expired_count,
        "by_symbol_active_expired": dict(
            sorted(
                by_symbol_active_expired.items(),
                key=lambda kv: (-(kv[1]["active"] + kv[1]["expired"]), kv[0]),
            )
        ),
        "stale_time_stop_pending": stale,
    }


def period_stats_by_symbol(entries: list[dict[str, Any]], *, now_utc: datetime, days: int) -> list[dict[str, Any]]:
    start = now_utc - timedelta(days=days)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        created = parse_iso_utc(str(e.get("created_at_utc") or ""))
        if created is None or not (start <= created <= now_utc):
            continue
        symbol = str(e.get("symbol") or "UNKNOWN")
        grouped.setdefault(symbol, []).append(e)

    out: list[dict[str, Any]] = []
    for symbol, items in grouped.items():
        total = len(items)
        hit = 0
        tp = 0
        sl = 0
        wins: list[float] = []
        losses: list[float] = []
        for e in items:
            status = str(e.get("status") or "")
            if status in {"filled", "closed"} or e.get("filled_at_utc"):
                hit += 1
            ex = str(e.get("exit_status") or "")
            if ex == "tp":
                tp += 1
            elif ex == "sl":
                sl += 1
            rp = e.get("realized_pnl_pct")
            if isinstance(rp, (int, float)):
                rv = float(rp)
                if rv > 0:
                    wins.append(rv)
                elif rv < 0:
                    losses.append(rv)
        closed_ts = tp + sl
        avg_win = (sum(wins) / len(wins)) if wins else None
        avg_loss_abs = (abs(sum(losses) / len(losses))) if losses else None
        avg_rr = None
        if avg_win is not None and avg_loss_abs and avg_loss_abs > 1e-12:
            avg_rr = round(avg_win / avg_loss_abs, 3)
        out.append(
            {
                "symbol": symbol,
                "candidate_total": total,
                "hit_rate_pct": safe_pct(hit, total),
                "tp_rate_pct": safe_pct(tp, closed_ts),
                "sl_rate_pct": safe_pct(sl, closed_ts),
                "avg_rr": avg_rr,
            }
        )
    out.sort(key=lambda x: (-int(x["candidate_total"]), str(x["symbol"])))
    return out


def period_stats_by_market(entries: list[dict[str, Any]], *, now_utc: datetime, days: int) -> list[dict[str, Any]]:
    start = now_utc - timedelta(days=days)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        created = parse_iso_utc(str(e.get("created_at_utc") or ""))
        if created is None or not (start <= created <= now_utc):
            continue
        market = str(e.get("market") or "UNK").upper()
        grouped.setdefault(market, []).append(e)
    out: list[dict[str, Any]] = []
    for market, items in grouped.items():
        total = len(items)
        hit = 0
        tp = 0
        sl = 0
        for e in items:
            status = str(e.get("status") or "")
            if status in {"filled", "closed"} or e.get("filled_at_utc"):
                hit += 1
            ex = str(e.get("exit_status") or "")
            if ex == "tp":
                tp += 1
            elif ex == "sl":
                sl += 1
        closed_ts = tp + sl
        out.append(
            {
                "market": market,
                "candidate_total": total,
                "hit_rate_pct": safe_pct(hit, total),
                "tp_rate_pct": safe_pct(tp, closed_ts),
                "sl_rate_pct": safe_pct(sl, closed_ts),
            }
        )
    out.sort(key=lambda x: (-int(x["candidate_total"]), str(x["market"])))
    return out


def fmt_pct(v: float | None) -> str:
    return f"{v:.2f}%" if isinstance(v, (int, float)) else "—"


def fmt_num(v: float | None) -> str:
    return f"{v:.3f}" if isinstance(v, (int, float)) else "—"


def fmt_px(v: Any) -> str:
    if isinstance(v, (int, float)):
        fv = float(v)
        if abs(fv) >= 1000:
            return f"{fv:,.2f}"
        return f"{fv:.4f}" if abs(fv) < 1 else f"{fv:.2f}"
    return "—"


def fmt_iso_local(ts: Any) -> str:
    dt = parse_iso_utc(str(ts or ""))
    if not dt:
        return "—"
    return format_beijing(dt, fmt="%m-%d %H:%M")


def fmt_iso_local_full(ts: Any) -> str:
    dt = parse_iso_utc(str(ts or ""))
    if not dt:
        return "—"
    return format_beijing(dt, fmt="%m-%d %H:%M:%S")


def _calc_rr(e: dict[str, Any]) -> float | None:
    rv = e.get("rr")
    if isinstance(rv, (int, float)) and float(rv) > 0:
        return float(rv)
    entry = e.get("fill_price") if e.get("filled_at_utc") else e.get("entry_price")
    stop = e.get("stop_loss")
    tps = e.get("take_profit_levels")
    if not isinstance(entry, (int, float)) or not isinstance(stop, (int, float)):
        return None
    if not isinstance(tps, list) or not tps or not isinstance(tps[0], (int, float)):
        return None
    risk = abs(float(entry) - float(stop))
    reward = abs(float(tps[0]) - float(entry))
    if risk <= 1e-12 or reward <= 1e-12:
        return None
    return round(reward / risk, 4)


def _order_kind_cn(e: dict[str, Any]) -> str:
    v = str(e.get("order_kind_cn") or "").strip()
    if v:
        return v
    zone = e.get("entry_zone")
    sl = e.get("signal_last")
    if isinstance(zone, list) and len(zone) >= 2 and isinstance(sl, (int, float)):
        lo, hi = float(min(zone)), float(max(zone))
        return "实时单" if lo <= float(sl) <= hi else "挂单"
    return "—"


def _action_hint_cn(e: dict[str, Any]) -> str:
    status = str(e.get("status") or "")
    rr = _calc_rr(e)
    worth_rr, observe_rr = _action_thresholds()
    if status in {"expired", "closed"}:
        return "保守观望"
    if rr is None:
        return "再观察"
    if rr >= worth_rr and status in {"pending", "filled", "watch"}:
        return "值得做"
    if rr >= observe_rr:
        return "再观察"
    return "保守观望"


def fmt_local_second(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_beijing(dt)


def _entry_sort_ts(e: dict[str, Any]) -> datetime:
    for k in ("updated_at_utc", "closed_at_utc", "filled_at_utc", "created_at_utc"):
        dt = parse_iso_utc(str(e.get(k) or ""))
        if dt is not None:
            return dt
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def latest_entries_by_idea(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for i, e in enumerate(entries):
        # 采用稳定业务键聚合，避免历史随机 idea_id 导致可读版持续膨胀
        symbol = str(e.get("symbol") or "UNKNOWN")
        interval = str(e.get("interval") or "1d")
        plan_type = str(e.get("plan_type") or "tactical")
        direction = str(e.get("direction") or "long")
        idea_id = str(e.get("idea_id") or "").strip()
        group_key = "|".join([symbol, interval, plan_type, direction]) if symbol else idea_id
        if not group_key:
            group_key = f"__no_idea__{i}"
        ts = _entry_sort_ts(e)
        old = latest.get(group_key)
        if old is None or ts >= old[0]:
            latest[group_key] = (ts, e)
    rows = list(latest.values())
    rows.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in rows]


def build_stats_payload(entries: list[dict[str, Any]], now_utc: datetime | None = None) -> dict[str, Any]:
    now = now_utc or datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "generated_at_utc": now.isoformat(),
        "week_7d": period_stats(entries, now_utc=now, days=7),
        "month_30d": period_stats(entries, now_utc=now, days=30),
        "by_symbol_30d": period_stats_by_symbol(entries, now_utc=now, days=30),
        "by_market_30d": period_stats_by_market(entries, now_utc=now, days=30),
        "breakdown_7d": period_breakdown(entries, now_utc=now, days=7),
        "breakdown_30d": period_breakdown(entries, now_utc=now, days=30),
    }
    try:
        from persistence.paper_trade_service import fetch_paper_trade_monitor

        pm = fetch_paper_trade_monitor()
        if pm is not None:
            payload["paper_trade_monitor"] = pm
    except Exception:
        pass
    return payload


def _md_count_table(title: str, data: dict[str, int]) -> str:
    if not data:
        return f"**{title}**：无数据\n\n"
    lines = [f"**{title}**\n\n", "| 键 | 条数 |\n", "|---|---:|\n"]
    for k, v in data.items():
        lines.append(f"| {k} | {v} |\n")
    lines.append("\n")
    return "".join(lines)


def _md_symbol_active_expired(data: dict[str, dict[str, int]]) -> str:
    if not data:
        return ""
    lines = ["| 标的 | active(watch/pending/filled) | expired |\n", "|---|---:|---:|\n"]
    for symbol, row in data.items():
        lines.append(f"| {symbol} | {int(row.get('active', 0))} | {int(row.get('expired', 0))} |\n")
    lines.append("\n")
    return "".join(lines)


def render_markdown(now_utc: datetime, payload: dict[str, Any]) -> str:
    week = payload["week_7d"]
    month = payload["month_30d"]
    by_symbol_30d = payload["by_symbol_30d"]
    by_market_30d = payload.get("by_market_30d") or []
    lines: list[str] = []
    lines.append(f"# 股票交易台账统计（北京时间 {fmt_local_second(now_utc)}）\n\n")
    lines.append("| 统计窗口 | 候选单 | 命中率 | 止盈率 | 止损率 | 平均盈亏比 |\n")
    lines.append("|---|---:|---:|---:|---:|---:|\n")
    lines.append(
        f"| 近7天 | {week['candidate_total']} | {fmt_pct(week['hit_rate_pct'])} | {fmt_pct(week['tp_rate_pct'])} | {fmt_pct(week['sl_rate_pct'])} | {fmt_num(week['avg_rr'])} |\n"
    )
    lines.append(
        f"| 近30天 | {month['candidate_total']} | {fmt_pct(month['hit_rate_pct'])} | {fmt_pct(month['tp_rate_pct'])} | {fmt_pct(month['sl_rate_pct'])} | {fmt_num(month['avg_rr'])} |\n"
    )
    b7 = payload.get("breakdown_7d") or {}
    b30 = payload.get("breakdown_30d") or {}
    if b7 or b30:
        lines.append("\n## 分层与失效线索\n\n")
        lines.append(
            "说明：`stale_time_stop_pending` 为「创建落在窗口内、状态仍为 pending/watch、"
            "且已超过 time_stop_deadline_utc」的条数，用于提示应人工重评而非自动改状态。\n\n"
        )
        if b7:
            lines.append(f"### 近7天（候选 {b7.get('candidate_total', 0)}）\n\n")
            lines.append(f"- 时间止损已过期仍挂单：**{b7.get('stale_time_stop_pending', 0)}** 条\n\n")
            lines.append(
                f"- active（watch/pending/filled）：**{b7.get('active_count', 0)}**；"
                f"expired：**{b7.get('expired_count', 0)}**\n\n"
            )
            lines.append(_md_count_table("按 status", b7.get("by_status") or {}))
            lines.append(_md_count_table("按 wyckoff_bias", b7.get("by_wyckoff_bias") or {}))
            lines.append(_md_symbol_active_expired(b7.get("by_symbol_active_expired") or {}))
        if b30:
            lines.append(f"### 近30天（候选 {b30.get('candidate_total', 0)}）\n\n")
            lines.append(f"- 时间止损已过期仍挂单：**{b30.get('stale_time_stop_pending', 0)}** 条\n\n")
            lines.append(
                f"- active（watch/pending/filled）：**{b30.get('active_count', 0)}**；"
                f"expired：**{b30.get('expired_count', 0)}**\n\n"
            )
            lines.append(_md_count_table("按 status", b30.get("by_status") or {}))
            lines.append(_md_count_table("按 wyckoff_bias", b30.get("by_wyckoff_bias") or {}))
            lines.append(_md_symbol_active_expired(b30.get("by_symbol_active_expired") or {}))
    if by_symbol_30d:
        lines.append("\n## 分组统计（按标的，近30天）\n\n")
        lines.append("| 标的 | 候选单 | 命中率 | 止盈率 | 止损率 | 平均盈亏比 |\n")
        lines.append("|---|---:|---:|---:|---:|---:|\n")
        for row in by_symbol_30d:
            lines.append(
                f"| {row['symbol']} | {row['candidate_total']} | {fmt_pct(row['hit_rate_pct'])} | "
                f"{fmt_pct(row['tp_rate_pct'])} | {fmt_pct(row['sl_rate_pct'])} | {fmt_num(row['avg_rr'])} |\n"
            )
    if by_market_30d:
        lines.append("\n## 分组统计（按市场，近30天）\n\n")
        lines.append("| 市场 | 候选单 | 命中率 | 止盈率 | 止损率 |\n")
        lines.append("|---|---:|---:|---:|---:|\n")
        for row in by_market_30d:
            lines.append(
                f"| {row['market']} | {row['candidate_total']} | {fmt_pct(row['hit_rate_pct'])} | "
                f"{fmt_pct(row['tp_rate_pct'])} | {fmt_pct(row['sl_rate_pct'])} |\n"
            )
    pm = payload.get("paper_trade_monitor")
    if isinstance(pm, dict):
        lines.append("\n## 模拟成交对账（PostgreSQL）\n\n")
        lines.append(
            f"- `paper_orders` 条数：**{pm.get('paper_order_count', 0)}**；"
            f"`paper_fills` 条数：**{pm.get('paper_fill_count', 0)}**\n"
        )
        lines.append(
            f"- `filled` 但无入场 fill（fill_seq=1）：**{pm.get('filled_idea_without_entry_fill_count', 0)}**\n"
        )
        lines.append(
            f"- `closed`(tp/sl) 但无出场 fill（fill_seq=2）：**{pm.get('closed_idea_without_exit_fill_count', 0)}**\n\n"
        )
        lines.append("说明：仅 PostgreSQL 已配置且能连库时统计纸交易对账段；无引擎则无此项。\n")
    return "".join(lines)


def render_readable_journal_md(now_utc: datetime, entries: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append(f"# 股票开单台账（北京时间 {fmt_local_second(now_utc)}）\n\n")
    if not entries:
        lines.append("暂无台账记录。\n")
        return "".join(lines)
    lines.append(
        "| 创建时间 | 市场 | 标的 | 方向 | 开单 | 入场点位 | 止损 | 止盈1/2 | RR | 建议动作 | 时间止损截止 | 状态 | 出场 | 已实现盈亏 | 浮动盈亏 |\n"
    )
    lines.append("|---|---|---|---|---|---:|---:|---|---:|---|---|---|---|---:|---:|\n")
    for e in latest_entries_by_idea(entries):
        tps = e.get("take_profit_levels")
        tp_text = "—"
        if isinstance(tps, list) and tps:
            tp1 = fmt_px(tps[0])
            tp2 = fmt_px(tps[1]) if len(tps) > 1 else "—"
            tp_text = f"{tp1} / {tp2}"
        ex = str(e.get("exit_status") or "—").upper()
        ddl = fmt_iso_local_full(e.get("time_stop_deadline_utc"))
        rr = _calc_rr(e)
        act = _action_hint_cn(e)
        order_text = f"{str(e.get('entry_type') or '—')}/{_order_kind_cn(e)}"
        lines.append(
            f"| {fmt_iso_local_full(e.get('created_at_utc'))} | {e.get('market', 'UNK')} | {e.get('symbol', 'UNKNOWN')} | "
            f"{e.get('direction', '—')} | {order_text} | "
            f"{fmt_px(e.get('fill_price') if e.get('filled_at_utc') else e.get('entry_price'))} | "
            f"{fmt_px(e.get('stop_loss'))} | {tp_text} | {fmt_num(rr)} | {act} | {ddl} | "
            f"{e.get('status', '—')} | {ex} | {fmt_pct(e.get('realized_pnl_pct'))} | {fmt_pct(e.get('unrealized_pnl_pct'))} |\n"
        )
    return "".join(lines)


def write_latest_stats(journal_path: Path) -> Path:
    entries = load_journal_entries_for_stats(journal_path)
    now_utc = datetime.now(timezone.utc)
    payload = build_stats_payload(entries, now_utc=now_utc)
    payload["journal"] = str(journal_path.resolve())
    md_text = render_markdown(now_utc, payload)
    readable_md = render_readable_journal_md(now_utc, entries)
    out_dir = journal_path.parent
    md_path = out_dir / "trade_journal_stats_latest.md"
    readable_md_path = out_dir / "trade_journal_readable.md"
    # 仅保留 Markdown 统计文件；若历史 JSON 存在则清理掉。
    json_path = out_dir / "trade_journal_stats_latest.json"
    if json_path.exists():
        json_path.unlink()
    csv_legacy = out_dir / "trade_journal_readable.csv"
    if csv_legacy.exists():
        csv_legacy.unlink()
    md_path.write_text(md_text, encoding="utf-8")
    readable_md_path.write_text(readable_md, encoding="utf-8")
    return md_path


def main() -> int:
    p = argparse.ArgumentParser(description="股票交易台账周/月统计")
    p.add_argument(
        "--journal",
        default=str(_REPO_ROOT / "output" / "journal"),
        help="台账文件路径",
    )
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    journal_path = Path(args.journal).resolve()
    entries = load_journal_entries_for_stats(journal_path)
    now_utc = datetime.now(timezone.utc)
    payload = build_stats_payload(entries, now_utc=now_utc)
    payload["journal"] = str(journal_path)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(now_utc, payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
