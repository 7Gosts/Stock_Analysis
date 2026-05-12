from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.trade_journal import has_active_idea as _has_active_idea
from analysis.trade_journal import load_journal, save_journal


def _event_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class JsonlJournalRepository:
    """JSONL 台账：对外单条语义；对内读改写整文件。"""

    def __init__(self, journal_path: Path) -> None:
        self._path = journal_path.resolve()

    @property
    def path(self) -> Path:
        return self._path

    def list_entries(self) -> list[dict[str, Any]]:
        return load_journal(self._path)

    def save_entries(self, entries: list[dict[str, Any]]) -> None:
        save_journal(self._path, entries)

    def append_idea(self, idea: dict[str, Any]) -> None:
        row = dict(idea)
        ev = row.setdefault("_journal_events", [])
        if isinstance(ev, list):
            ev.append(
                {
                    "type": "idea_created",
                    "time_utc": _event_now_iso(),
                    "payload": {},
                }
            )
        entries = load_journal(self._path)
        entries.append(row)
        save_journal(self._path, entries)

    def update_idea(self, idea_id: str, patch: dict[str, Any]) -> None:
        entries = load_journal(self._path)
        for e in entries:
            if str(e.get("idea_id") or "") == idea_id:
                for k, v in patch.items():
                    e[k] = v
                break
        save_journal(self._path, entries)

    def append_event(self, idea_id: str, event_type: str, payload: dict[str, Any]) -> None:
        entries = load_journal(self._path)
        for e in entries:
            if str(e.get("idea_id") or "") != idea_id:
                continue
            ev = e.setdefault("_journal_events", [])
            if isinstance(ev, list):
                ev.append(
                    {
                        "type": event_type,
                        "time_utc": _event_now_iso(),
                        "payload": dict(payload or {}),
                    }
                )
            break
        save_journal(self._path, entries)

    def has_active_idea(
        self,
        *,
        symbol: str,
        interval: str,
        direction: str,
        plan_type: str,
    ) -> bool:
        entries = load_journal(self._path)
        return _has_active_idea(
            entries,
            symbol=symbol,
            interval=interval,
            direction=direction,
            plan_type=plan_type,
        )
