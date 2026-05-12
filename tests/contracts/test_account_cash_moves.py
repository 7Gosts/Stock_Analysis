"""契约：显式充提调账写入 account_ledger。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from persistence import account_service


def _engine_with_conn(prev_row: tuple[float, float, float, float] | None):
    conn = MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    eng = MagicMock()
    eng.begin.return_value = ctx
    return eng, conn


@patch("persistence.account_service._engine")
def test_deposit_funds_appends_ledger(mock_engine) -> None:
    eng, conn = _engine_with_conn(None)
    mock_engine.return_value = eng
    sel = MagicMock()
    sel.first.return_value = (1000.0, 900.0, 100.0, 50.0)
    conn.execute.side_effect = [sel, MagicMock()]
    ok = account_service.deposit_funds("USD", 100.0, note="test")
    assert ok is True
    assert conn.execute.call_count == 2


@patch("persistence.account_service._engine")
def test_withdraw_funds_rejects_insufficient(mock_engine) -> None:
    eng, conn = _engine_with_conn(None)
    mock_engine.return_value = eng
    sel = MagicMock()
    sel.first.return_value = (1000.0, 50.0, 950.0, 0.0)
    conn.execute.side_effect = [sel]
    ok = account_service.withdraw_funds("USD", 100.0)
    assert ok is False
    assert conn.execute.call_count == 1


@patch("persistence.account_service._engine")
def test_adjust_funds_negative_allowed(mock_engine) -> None:
    eng, conn = _engine_with_conn(None)
    mock_engine.return_value = eng
    sel = MagicMock()
    sel.first.return_value = (1000.0, 500.0, 500.0, 0.0)
    conn.execute.side_effect = [sel, MagicMock()]
    ok = account_service.adjust_funds("CNY", -200.0, note="fee")
    assert ok is True
    assert conn.execute.call_count == 2


def test_deposit_zero_returns_false() -> None:
    assert account_service.deposit_funds("USD", 0.0) is False
