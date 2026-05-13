from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from analysis import journal_policy
from analysis.ledger_stats import write_latest_stats
from analysis.position_sizing import calculate_qty_for_idea, map_market_to_currency
from persistence import account_service
from config.runtime_config import get_account_system_config
from analysis.trade_journal import has_active_idea, update_idea_with_rows

from persistence import paper_trade_service
from persistence.journal_repository_factory import get_journal_repository

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
    journal_path = out_base / "journal"
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
                currency = map_market_to_currency(idea.get("market"))
                qty, sizing_detail = calculate_qty_for_idea(idea)
                idea["calculated_qty"] = qty
                idea["_position_sizing_detail"] = sizing_detail
                logger.info(
                    "[PositionSizing] symbol={} market={} currency={} entry_ref={} stop={} "
                    "max_loss_amount={} qty={} fallback={} skip_reason={}",
                    idea.get("symbol"),
                    idea.get("market"),
                    sizing_detail.get("currency"),
                    sizing_detail.get("entry_ref"),
                    sizing_detail.get("stop_ref"),
                    sizing_detail.get("max_loss_amount"),
                    qty,
                    sizing_detail.get("fallback"),
                    sizing_detail.get("skip_reason"),
                )
                # skip if sizing indicates too small
                if qty <= 0:
                    logger.warning("[PositionSizing] skip entry: qty={} idea_id={}", qty, idea.get("idea_id"))
                else:
                    paper_trade_service.create_entry_order_and_fill(idea, now_utc=now_utc)
                    try:
                        order_id = paper_trade_service.stable_order_id(str(idea.get("idea_id") or ""))
                        fill_price = idea.get("fill_price") or idea.get("entry_price")
                        account_service.open_position(
                            currency=currency,
                            idea=idea,
                            fill_qty=qty,
                            fill_price=float(fill_price),
                            order_id=order_id,
                            now_utc=now_utc,
                        )
                    except Exception:
                        logger.exception(
                            "[JournalService] account_service.open_position failed for idea_id={}",
                            idea.get("idea_id"),
                        )
            elif evt == "closed_tp":
                paper_trade_service.create_exit_fill(idea, close_reason="tp", now_utc=now_utc)
                try:
                    fill_qty = idea.get("fill_qty") or idea.get("calculated_qty", 1.0)
                    entry_price = idea.get("fill_price") or idea.get("entry_price")
                    exit_price = idea.get("closed_price")
                    if entry_price and exit_price:
                        _pnl, realized_pnl_pct = account_service.close_position(
                            idea_id=str(idea.get("idea_id") or ""),
                            fill_qty=float(fill_qty),
                            entry_price=float(entry_price),
                            exit_price=float(exit_price),
                            close_reason="tp",
                            now_utc=now_utc,
                        )
                        idea["realized_pnl_pct"] = realized_pnl_pct
                except Exception:
                    logger.exception(
                        "[JournalService] account_service.close_position failed for idea_id={}",
                        idea.get("idea_id"),
                    )
            elif evt == "closed_sl":
                paper_trade_service.create_exit_fill(idea, close_reason="sl", now_utc=now_utc)
                try:
                    fill_qty = idea.get("fill_qty") or idea.get("calculated_qty", 1.0)
                    entry_price = idea.get("fill_price") or idea.get("entry_price")
                    exit_price = idea.get("closed_price")
                    if entry_price and exit_price:
                        _pnl, realized_pnl_pct = account_service.close_position(
                            idea_id=str(idea.get("idea_id") or ""),
                            fill_qty=float(fill_qty),
                            entry_price=float(entry_price),
                            exit_price=float(exit_price),
                            close_reason="sl",
                            now_utc=now_utc,
                        )
                        idea["realized_pnl_pct"] = realized_pnl_pct
                except Exception:
                    logger.exception(
                        "[JournalService] account_service.close_position failed for idea_id={}",
                        idea.get("idea_id"),
                    )
        stats_md_path = write_latest_stats(journal_path)
    return journal_updated, journal_created, journal_path, stats_md_path, journal_new_entries
