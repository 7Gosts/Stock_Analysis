from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from analysis import journal_policy
from analysis.ledger_stats import write_latest_stats
from analysis.trade_journal import has_active_idea, load_journal, save_journal, update_idea_with_rows


def process_journal(
    *,
    out_base: Path,
    journal_candidates: list[dict[str, Any]],
    latest_rows_by_symbol: dict[str, list[dict[str, Any]]],
    now_utc: datetime,
) -> tuple[int, int, Path, Path | None]:
    journal_path = out_base / "trade_journal.jsonl"
    journal_entries = load_journal(journal_path)
    journal_updated = 0
    for e in journal_entries:
        sym = str(e.get("symbol") or "").upper()
        rows = latest_rows_by_symbol.get(sym)
        if rows and update_idea_with_rows(e, rows, now_utc):
            journal_updated += 1
    journal_created = 0
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
        journal_created += 1

    stats_md_path: Path | None = None
    if journal_created or journal_updated:
        save_journal(journal_path, journal_entries)
        stats_md_path, _readable_path = write_latest_stats(journal_path)
    return journal_updated, journal_created, journal_path, stats_md_path

