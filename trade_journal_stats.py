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

SCRIPT_DIR = Path(__file__).resolve().parent


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
    return dt.astimezone().strftime("%m-%d %H:%M")


def fmt_local_second(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


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
    return {
        "generated_at_utc": now.isoformat(),
        "week_7d": period_stats(entries, now_utc=now, days=7),
        "month_30d": period_stats(entries, now_utc=now, days=30),
        "by_symbol_30d": period_stats_by_symbol(entries, now_utc=now, days=30),
    }


def render_markdown(now_utc: datetime, week: dict[str, Any], month: dict[str, Any], by_symbol_30d: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append(f"# 股票交易台账统计（本机 {fmt_local_second(now_utc)}）\n\n")
    lines.append("| 统计窗口 | 候选单 | 命中率 | 止盈率 | 止损率 | 平均盈亏比 |\n")
    lines.append("|---|---:|---:|---:|---:|---:|\n")
    lines.append(
        f"| 近7天 | {week['candidate_total']} | {fmt_pct(week['hit_rate_pct'])} | {fmt_pct(week['tp_rate_pct'])} | {fmt_pct(week['sl_rate_pct'])} | {fmt_num(week['avg_rr'])} |\n"
    )
    lines.append(
        f"| 近30天 | {month['candidate_total']} | {fmt_pct(month['hit_rate_pct'])} | {fmt_pct(month['tp_rate_pct'])} | {fmt_pct(month['sl_rate_pct'])} | {fmt_num(month['avg_rr'])} |\n"
    )
    if by_symbol_30d:
        lines.append("\n## 分组统计（按标的，近30天）\n\n")
        lines.append("| 标的 | 候选单 | 命中率 | 止盈率 | 止损率 | 平均盈亏比 |\n")
        lines.append("|---|---:|---:|---:|---:|---:|\n")
        for row in by_symbol_30d:
            lines.append(
                f"| {row['symbol']} | {row['candidate_total']} | {fmt_pct(row['hit_rate_pct'])} | "
                f"{fmt_pct(row['tp_rate_pct'])} | {fmt_pct(row['sl_rate_pct'])} | {fmt_num(row['avg_rr'])} |\n"
            )
    return "".join(lines)


def render_readable_journal(now_utc: datetime, entries: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append(f"# 股票开单台账（本机 {fmt_local_second(now_utc)}）\n\n")
    if not entries:
        lines.append("暂无台账记录。\n")
        return "".join(lines)

    latest_rows = latest_entries_by_idea(entries)

    lines.append("| 创建时间 | 标的 | 方向 | 类型 | 入场点位 | 入场区间 | 止损 | 止盈1/2 | 状态 | 出场 | 已实现盈亏 |\n")
    lines.append("|---|---|---|---|---:|---|---:|---|---|---|---:|\n")
    for e in latest_rows:
        zone = e.get("entry_zone")
        zone_text = "—"
        if isinstance(zone, list) and len(zone) >= 2:
            zone_text = f"{fmt_px(zone[0])} ~ {fmt_px(zone[1])}"
        tps = e.get("take_profit_levels")
        tp_text = "—"
        if isinstance(tps, list) and tps:
            tp1 = fmt_px(tps[0])
            tp2 = fmt_px(tps[1]) if len(tps) > 1 else "—"
            tp_text = f"{tp1} / {tp2}"
        ex = str(e.get("exit_status") or "—").upper()
        lines.append(
            f"| {fmt_iso_local(e.get('created_at_utc'))} | {e.get('symbol', 'UNKNOWN')} | "
            f"{e.get('direction', '—')} | {e.get('entry_type', '—')} | "
            f"{fmt_px(e.get('fill_price') if e.get('filled_at_utc') else e.get('entry_price'))} | "
            f"{zone_text} | {fmt_px(e.get('stop_loss'))} | {tp_text} | "
            f"{e.get('status', '—')} | {ex} | {fmt_pct(e.get('realized_pnl_pct'))} |\n"
        )
    return "".join(lines)


def write_latest_stats(journal_path: Path) -> tuple[Path, Path, Path]:
    entries = load_journal(journal_path)
    now_utc = datetime.now(timezone.utc)
    payload = build_stats_payload(entries, now_utc=now_utc)
    payload["journal"] = str(journal_path.resolve())
    md_text = render_markdown(
        now_utc,
        payload["week_7d"],
        payload["month_30d"],
        payload["by_symbol_30d"],
    )
    readable_text = render_readable_journal(now_utc, entries)
    out_dir = journal_path.parent
    json_path = out_dir / "trade_journal_stats_latest.json"
    md_path = out_dir / "trade_journal_stats_latest.md"
    readable_path = out_dir / "trade_journal_readable.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    readable_path.write_text(readable_text, encoding="utf-8")
    return json_path, md_path, readable_path


def main() -> int:
    p = argparse.ArgumentParser(description="股票交易台账周/月统计")
    p.add_argument(
        "--journal",
        default=str(SCRIPT_DIR / "output" / "trade_journal.jsonl"),
        help="台账文件路径",
    )
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    journal_path = Path(args.journal).resolve()
    entries = load_journal(journal_path)
    now_utc = datetime.now(timezone.utc)
    payload = build_stats_payload(entries, now_utc=now_utc)
    payload["journal"] = str(journal_path)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            render_markdown(
                now_utc,
                payload["week_7d"],
                payload["month_30d"],
                payload["by_symbol_30d"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
