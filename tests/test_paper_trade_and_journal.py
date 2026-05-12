from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


def test_stable_order_fill_ids_fit_varchar64() -> None:
    from persistence.paper_trade_service import stable_fill_id, stable_order_id

    long_id = "x" * 200
    oid = stable_order_id(long_id)
    fid = stable_fill_id(long_id, 1)
    assert len(oid) <= 64
    assert len(fid) <= 64
    assert oid == stable_order_id(long_id)


def test_fetch_paper_monitor_none_when_no_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("persistence.paper_trade_service.get_sqlalchemy_engine", lambda: None)
    from persistence.paper_trade_service import fetch_paper_trade_monitor

    assert fetch_paper_trade_monitor() is None


def test_build_stats_payload_paper_monitor_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "persistence.paper_trade_service.fetch_paper_trade_monitor",
        lambda: {
            "paper_order_count": 2,
            "paper_fill_count": 3,
            "filled_idea_without_entry_fill_count": 0,
            "closed_idea_without_exit_fill_count": 1,
        },
    )
    from analysis.ledger_stats import build_stats_payload

    p = build_stats_payload([], now_utc=datetime.now(timezone.utc))
    assert p.get("paper_trade_monitor", {}).get("paper_order_count") == 2
    assert p["paper_trade_monitor"]["closed_idea_without_exit_fill_count"] == 1


def test_render_markdown_includes_paper_section() -> None:
    from analysis.ledger_stats import render_markdown

    now = datetime.now(timezone.utc)
    md = render_markdown(
        now,
        {
            "week_7d": {"candidate_total": 0, "hit_rate_pct": None, "tp_rate_pct": None, "sl_rate_pct": None, "avg_rr": None},
            "month_30d": {"candidate_total": 0, "hit_rate_pct": None, "tp_rate_pct": None, "sl_rate_pct": None, "avg_rr": None},
            "by_symbol_30d": [],
            "by_market_30d": [],
            "breakdown_7d": {},
            "breakdown_30d": {},
            "paper_trade_monitor": {
                "paper_order_count": 1,
                "paper_fill_count": 2,
                "filled_idea_without_entry_fill_count": 0,
                "closed_idea_without_exit_fill_count": 0,
            },
        },
    )
    assert "模拟成交对账" in md
    assert "paper_orders" in md


def test_journal_service_calls_paper_on_filled_and_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def fake_entry(idea: dict, **kwargs: object) -> None:
        calls.append(("entry", str(idea.get("idea_id") or "")))

    def fake_exit(idea: dict, *, close_reason: str, **kwargs: object) -> None:
        calls.append((close_reason, str(idea.get("idea_id") or "")))

    monkeypatch.setattr("persistence.paper_trade_service.create_entry_order_and_fill", fake_entry)
    monkeypatch.setattr("persistence.paper_trade_service.create_exit_fill", fake_exit)
    monkeypatch.setattr("app.journal_service.write_latest_stats", lambda *_a, **_k: tmp_path / "s.md")
    monkeypatch.setattr("analysis.journal_policy.idea_passes_journal_append_gates", lambda *_a, **_k: (True, None))

    store: dict[str, list] = {"entries": []}

    class _MemRepo:
        def list_entries(self) -> list:
            return store["entries"]

        def save_entries(self, entries: list) -> None:
            store["entries"] = entries

        def append_event(self, *_a: object, **_k: object) -> None:
            pass

    monkeypatch.setattr("app.journal_service.get_journal_repository", lambda _p: _MemRepo())

    now = datetime.now(timezone.utc)
    filled_idea = {
        "idea_id": "idea-filled-1",
        "symbol": "ETH",
        "interval": "4h",
        "direction": "long",
        "plan_type": "tactical",
        "status": "filled",
        "market": "CRYPTO",
        "provider": "gateio",
        "entry_zone": [100.0, 102.0],
        "entry_price": 101.0,
        "stop_loss": 98.0,
        "take_profit_levels": [105.0],
        "created_at_utc": now.isoformat(),
        "valid_until_utc": now.isoformat(),
        "fill_price": 101.0,
        "filled_at_utc": now.isoformat(),
    }
    store["entries"] = [filled_idea]

    def to_closed_tp(e: dict, rows: list, nu: datetime) -> bool:
        if str(e.get("status")) != "filled":
            return False
        e["status"] = "closed"
        e["exit_status"] = "tp"
        e["closed_at_utc"] = now.isoformat()
        e["closed_price"] = 105.0
        return True

    monkeypatch.setattr("app.journal_service.update_idea_with_rows", to_closed_tp)

    from app import journal_service

    journal_service.process_journal(
        out_base=tmp_path,
        journal_candidates=[],
        latest_rows_by_symbol={"ETH": [{"time": now.isoformat(), "low": 100.0, "high": 106.0, "close": 105.0}]},
        now_utc=now,
    )
    assert ("tp", "idea-filled-1") in calls

    calls.clear()
    pending_idea = {
        "idea_id": "idea-pend-1",
        "symbol": "BTC",
        "interval": "1d",
        "direction": "long",
        "plan_type": "tactical",
        "status": "pending",
        "market": "CRYPTO",
        "provider": "gateio",
        "entry_zone": [50.0, 52.0],
        "entry_price": 51.0,
        "stop_loss": 48.0,
        "take_profit_levels": [55.0],
        "created_at_utc": now.isoformat(),
        "valid_until_utc": now.isoformat(),
    }
    store["entries"] = [pending_idea]

    def to_filled(e: dict, rows: list, nu: datetime) -> bool:
        if str(e.get("status")) != "pending":
            return False
        e["status"] = "filled"
        e["fill_price"] = 51.0
        e["filled_at_utc"] = now.isoformat()
        return True

    monkeypatch.setattr("app.journal_service.update_idea_with_rows", to_filled)
    journal_service.process_journal(
        out_base=tmp_path,
        journal_candidates=[],
        latest_rows_by_symbol={"BTC": [{"time": now.isoformat(), "low": 50.5, "high": 51.5, "close": 51.0}]},
        now_utc=now,
    )
    assert ("entry", "idea-pend-1") in calls
