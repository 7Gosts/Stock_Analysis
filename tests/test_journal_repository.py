from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from analysis.trade_journal import load_journal, save_journal
from app.journal_repository_dualwrite import DualWriteJournalRepository
from app.journal_repository_factory import get_journal_repository, load_journal_entries_for_stats
from app.journal_repository_jsonl import JsonlJournalRepository
from app.journal_repository_pg import PostgresJournalRepository


def test_jsonl_repository_append_update_event(tmp_path: Path) -> None:
    p = tmp_path / "trade_journal.jsonl"
    r = JsonlJournalRepository(p)
    idea: dict = {
        "idea_id": "t1",
        "symbol": "AAA",
        "interval": "1d",
        "direction": "long",
        "plan_type": "tactical",
        "status": "pending",
        "market": "US",
        "provider": "tickflow",
    }
    r.append_idea(idea)
    r.append_event("t1", "filled", {"x": 1})
    entries = r.list_entries()
    assert len(entries) == 1
    ev = entries[0].get("_journal_events")
    assert isinstance(ev, list) and len(ev) >= 2
    assert ev[0].get("type") == "idea_created"
    assert ev[-1].get("type") == "filled"


def test_load_journal_entries_for_stats_matches_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.runtime_config.get_database_backend", lambda: "jsonl")
    p = tmp_path / "trade_journal.jsonl"
    save_journal(p, [{"idea_id": "a", "symbol": "S", "status": "pending", "interval": "1d", "direction": "long", "market": "US", "provider": "tickflow"}])
    a = load_journal_entries_for_stats(p)
    b = load_journal(p)
    assert a == b


def test_get_journal_repository_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.runtime_config.get_database_backend", lambda: "jsonl")
    r = get_journal_repository(tmp_path / "j.jsonl")
    assert isinstance(r, JsonlJournalRepository)


def test_dualwrite_pg_failure_does_not_raise_on_append_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "trade_journal.jsonl"
    mock_pg = MagicMock()
    mock_pg.save_entries = MagicMock()
    mock_pg.append_idea = MagicMock()
    mock_pg.update_idea = MagicMock()
    mock_pg.append_event = MagicMock(side_effect=RuntimeError("pg down"))
    mock_pg.list_entries = MagicMock(return_value=[])
    mock_pg.has_active_idea = MagicMock(return_value=False)
    monkeypatch.setattr("config.runtime_config.get_dualwrite_rollback_jsonl_on_pg_failure", lambda: False)
    with patch("app.journal_repository_dualwrite.PostgresJournalRepository", return_value=mock_pg):
        dw = DualWriteJournalRepository(p)
        dw._jsonl.save_entries(
            [
                {
                    "idea_id": "x1",
                    "symbol": "Z",
                    "interval": "1d",
                    "direction": "long",
                    "plan_type": "tactical",
                    "status": "pending",
                    "market": "US",
                    "provider": "tickflow",
                }
            ]
        )
        dw.append_event("x1", "filled", {})
    ev = dw._jsonl.list_entries()[0].get("_journal_events")
    assert isinstance(ev, list)
    assert any(x.get("type") == "filled" for x in ev)


def test_journal_service_process_persists_via_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.runtime_config.get_database_backend", lambda: "jsonl")
    from datetime import datetime, timezone

    from app import journal_service

    out = tmp_path / "out"
    journal_path = out / "trade_journal.jsonl"
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
    monkeypatch.setattr("analysis.journal_policy.idea_passes_journal_append_gates", lambda *_a, **_k: (True, None))
    monkeypatch.setattr("analysis.ledger_stats.write_latest_stats", lambda *_a, **_k: tmp_path / "s.md")
    u, c, jp, stats, new_e = journal_service.process_journal(
        out_base=out,
        journal_candidates=[idea],
        latest_rows_by_symbol={},
        now_utc=datetime.now(timezone.utc),
    )
    assert c == 1 and u == 0
    assert jp == journal_path
    rows = load_journal(journal_path)
    assert len(rows) == 1
    ev = rows[0].get("_journal_events")
    assert isinstance(ev, list) and any(x.get("type") == "idea_created" for x in ev)


def test_load_journal_entries_for_stats_postgres_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.runtime_config.get_database_backend", lambda: "postgres")
    with patch("app.journal_repository_pg.PostgresJournalRepository") as cls:
        inst = MagicMock()
        inst.list_entries = MagicMock(return_value=[{"idea_id": "p1"}])
        cls.return_value = inst
        out = load_journal_entries_for_stats(tmp_path / "trade_journal.jsonl")
    assert out == [{"idea_id": "p1"}]


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

    monkeypatch.setattr("app.journal_repository_pg.get_sqlalchemy_engine", lambda: engine)
    repo = PostgresJournalRepository(tmp_path / "trade_journal.jsonl")
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
