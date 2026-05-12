from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from persistence.journal_repository_factory import get_journal_repository, load_journal_entries_for_stats
from persistence.journal_repository_pg import PostgresJournalRepository


def test_get_journal_repository_returns_postgres(tmp_path: Path) -> None:
    with patch("persistence.journal_repository_pg.PostgresJournalRepository") as cls:
        cls.return_value = MagicMock()
        r = get_journal_repository(tmp_path / "journal")
        cls.assert_called_once_with()
        assert r is cls.return_value


def test_load_journal_entries_for_stats_uses_postgres(tmp_path: Path) -> None:
    with patch("persistence.journal_repository_pg.PostgresJournalRepository") as cls:
        inst = MagicMock()
        inst.list_entries = MagicMock(return_value=[{"idea_id": "p1"}])
        cls.return_value = inst
        out = load_journal_entries_for_stats(tmp_path / "journal")
        cls.assert_called_once_with()
    assert out == [{"idea_id": "p1"}]


def test_journal_service_process_persists_via_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone

    from app import journal_service

    store: dict[str, list] = {"entries": []}

    class _MemRepo:
        def list_entries(self) -> list:
            return store["entries"]

        def save_entries(self, entries: list) -> None:
            store["entries"] = entries

        def append_event(self, *_a: object, **_k: object) -> None:
            pass

    monkeypatch.setattr("app.journal_service.get_journal_repository", lambda _p: _MemRepo())
    monkeypatch.setattr("analysis.journal_policy.idea_passes_journal_append_gates", lambda *_a, **_k: (True, None))
    monkeypatch.setattr("app.journal_service.write_latest_stats", lambda *_a, **_k: tmp_path / "s.md")

    out = tmp_path / "out"
    journal_path = out / "journal"
    idea = {
        "idea_id": "n1",
        "symbol": "QQQ",
        "interval": "1d",
        "direction": "long",
        "plan_type": "tactical",
        "status": "pending",
        "market": "US",
        "provider": "tickflow",
        "entry_zone": [99.0, 101.0],
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit_levels": [105.0],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "valid_until_utc": datetime.now(timezone.utc).isoformat(),
    }
    u, c, jp, stats, new_e = journal_service.process_journal(
        out_base=out,
        journal_candidates=[idea],
        latest_rows_by_symbol={},
        now_utc=datetime.now(timezone.utc),
    )
    assert c == 1 and u == 0
    assert jp == journal_path
    assert len(store["entries"]) == 1
    assert store["entries"][0].get("idea_id") == "n1"


def test_pg_save_entries_does_not_clear_event_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = MagicMock()
    conn.execute.side_effect = [
        [("keep1",), ("drop1",)],
        None,
        None,
    ]
    begin_ctx = MagicMock()
    begin_ctx.__enter__.return_value = conn
    begin_ctx.__exit__.return_value = False
    engine = MagicMock()
    engine.begin.return_value = begin_ctx

    monkeypatch.setattr("persistence.journal_repository_pg.get_sqlalchemy_engine", lambda: engine)
    repo = PostgresJournalRepository()
    repo._upsert_idea = MagicMock()

    repo.save_entries(
        [
            {
                "idea_id": "keep1",
                "symbol": "ETH_USDT",
                "interval": "4h",
                "direction": "long",
                "plan_type": "tactical",
                "status": "filled",
                "market": "CRYPTO",
                "provider": "gateio",
            }
        ]
    )

    sql_calls = [str(call.args[0]) for call in conn.execute.call_args_list]
    assert all("DELETE FROM journal_events" not in sql for sql in sql_calls)
    assert any("DELETE FROM journal_ideas WHERE idea_id = :idea_id" in sql for sql in sql_calls)
    repo._upsert_idea.assert_called_once()
