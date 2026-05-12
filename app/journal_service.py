from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from analysis import journal_policy
from analysis.ledger_stats import write_latest_stats
from analysis.trade_journal import has_active_idea, update_idea_with_rows

from app import paper_trade_service
from app.journal_repository_factory import get_journal_repository

_TRACKED_SNAPSHOT = (
    "status",
    "exit_status",
    "filled_at_utc",
    "fill_price",
    "updated_at_utc",
    "closed_at_utc",
    "closed_price",
    "realized_pnl_pct",
    "unrealized_pnl_pct",
)


def _snapshot_tracked(idea: dict[str, Any]) -> dict[str, Any]:
    return {k: idea.get(k) for k in _TRACKED_SNAPSHOT}


def _infer_journal_event(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    bs, a_s = str(before.get("status") or ""), str(after.get("status") or "")
    bex, aex = str(before.get("exit_status") or ""), str(after.get("exit_status") or "")
    if bs in {"watch", "pending"} and a_s == "filled":
        return "filled", {"from_status": bs}
    if bs in {"watch", "pending"} and a_s == "expired":
        return "expired", {"from_status": bs}
    if bs == "filled" and a_s == "closed":
        if aex == "tp":
            return "closed_tp", {}
        if aex == "sl":
            return "closed_sl", {}
        return "status_changed", {"exit_status": aex, "note": "closed_other"}
    if a_s == "filled":
        if before.get("unrealized_pnl_pct") != after.get("unrealized_pnl_pct") or bex != aex:
            return "mark_to_market_updated", {"from_exit": bex, "exit_status": aex}
    return "status_changed", {"before_status": bs, "after_status": a_s}


def process_journal(
    *,
    out_base: Path,
    journal_candidates: list[dict[str, Any]],
    latest_rows_by_symbol: dict[str, list[dict[str, Any]]],
    now_utc: datetime,
) -> tuple[int, int, Path, Path | None, list[dict[str, Any]]]:
    journal_path = out_base / "trade_journal.jsonl"
    repo = get_journal_repository(journal_path)
    journal_entries = repo.list_entries()
    journal_updated = 0
    pending_events: list[tuple[str, str, dict[str, Any]]] = []
    for e in journal_entries:
        sym = str(e.get("symbol") or "").upper()
        rows = latest_rows_by_symbol.get(sym)
        if not rows:
            continue
        before = _snapshot_tracked(e)
        if not update_idea_with_rows(e, rows, now_utc):
            continue
        journal_updated += 1
        idea_id = str(e.get("idea_id") or "")
        if not idea_id:
            continue
        evt, payload = _infer_journal_event(before, _snapshot_tracked(e))
        pending_events.append((idea_id, evt, payload))

    journal_created = 0
    journal_new_entries: list[dict[str, Any]] = []
    for idea in journal_candidates:
        if has_active_idea(
            journal_entries,
            symbol=str(idea.get("symbol") or ""),
            interval=str(idea.get("interval") or ""),
            direction=str(idea.get("direction") or ""),
            plan_type=str(idea.get("plan_type") or "tactical"),
        ):
            continue
        ok, _rej = journal_policy.idea_passes_journal_append_gates(idea)
        if not ok:
            continue
        journal_entries.append(idea)
        journal_new_entries.append(idea)
        journal_created += 1

    stats_md_path: Path | None = None
    if journal_created or journal_updated:
        repo.save_entries(journal_entries)
        for idea_id, evt, payload in pending_events:
            repo.append_event(idea_id, evt, payload)
        for idea in journal_new_entries:
            iid = str(idea.get("idea_id") or "")
            if iid:
                repo.append_event(iid, "idea_created", {"source": "journal_service"})
        idea_by_id = {str(e.get("idea_id") or ""): e for e in journal_entries if str(e.get("idea_id") or "")}
        for idea_id, evt, _payload in pending_events:
            idea = idea_by_id.get(idea_id)
            if not idea:
                continue
            if evt == "filled":
                paper_trade_service.create_entry_order_and_fill(idea, now_utc=now_utc)
            elif evt == "closed_tp":
                paper_trade_service.create_exit_fill(idea, close_reason="tp", now_utc=now_utc)
            elif evt == "closed_sl":
                paper_trade_service.create_exit_fill(idea, close_reason="sl", now_utc=now_utc)
        stats_md_path = write_latest_stats(journal_path)
    return journal_updated, journal_created, journal_path, stats_md_path, journal_new_entries
