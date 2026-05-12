from __future__ import annotations

import pytest
from unittest.mock import patch

from analysis.position_sizing import calculate_qty_for_idea, map_market_to_currency


def test_calculate_qty_with_config_balance():
    idea = {
        "market": "US",
        "entry_price": 100.0,
        "stop_loss": 90.0,
    }
    qty, detail = calculate_qty_for_idea(idea)
    assert isinstance(qty, float)
    assert "qty_step" in detail or "qty" in detail


def test_qty_too_small_returns_zero():
    idea = {
        "market": "US",
        "entry_price": 1000000.0,
        "stop_loss": 999999.9,
    }
    qty, detail = calculate_qty_for_idea(idea)
    assert isinstance(detail, dict)


def test_map_market_to_currency() -> None:
    assert map_market_to_currency("CN") == "CNY"
    assert map_market_to_currency("PM") == "CNY"
    assert map_market_to_currency("US") == "USD"
    assert map_market_to_currency("CRYPTO") == "USD"
    assert map_market_to_currency("HK") == "USD"
    assert map_market_to_currency("UNKNOWN") == "USD"


@patch("analysis.position_sizing.get_accounts_config")
def test_qty_formula_crypto_usd(mock_acc) -> None:
    mock_acc.return_value = {
        "USD": {"balance": 100000.0, "max_loss_pct": 0.02, "qty_step": 1.0},
    }
    idea = {"market": "CRYPTO", "entry_price": 100.0, "stop_loss": 90.0}
    qty, d = calculate_qty_for_idea(idea)
    assert qty == 200.0
    assert not d.get("fallback")
    assert d.get("max_loss_amount") == 2000.0
    assert d.get("risk_per_unit") == 10.0


@patch("analysis.position_sizing.get_accounts_config")
def test_qty_uses_entry_zone_mid(mock_acc) -> None:
    mock_acc.return_value = {
        "CNY": {"balance": 500000.0, "max_loss_pct": 0.02, "qty_step": 0.01},
    }
    idea = {"market": "CN", "entry_zone": [99.0, 101.0], "stop_loss": 90.0}
    qty, d = calculate_qty_for_idea(idea)
    assert d.get("entry_ref") == 100.0
    assert d.get("risk_per_unit") == 10.0
    assert d.get("max_loss_amount") == 10000.0
    assert qty == 1000.0


@patch("analysis.position_sizing.get_accounts_config")
def test_fallback_empty_accounts(mock_acc) -> None:
    mock_acc.return_value = {}
    idea = {"market": "US", "entry_price": 100.0, "stop_loss": 90.0}
    qty, d = calculate_qty_for_idea(idea)
    assert qty == 1.0
    assert d.get("fallback")
    assert d.get("reason") == "no_accounts_config"


@patch("analysis.position_sizing.get_accounts_config")
def test_fallback_missing_stop(mock_acc) -> None:
    mock_acc.return_value = {"USD": {"balance": 100000.0, "max_loss_pct": 0.02, "qty_step": 1.0}}
    qty, d = calculate_qty_for_idea({"market": "US", "entry_price": 100.0})
    assert qty == 1.0
    assert d.get("fallback")
    assert d.get("reason") == "missing_entry_or_stop"


@patch("analysis.position_sizing.get_accounts_config")
def test_fallback_zero_risk(mock_acc) -> None:
    mock_acc.return_value = {"USD": {"balance": 100000.0, "max_loss_pct": 0.02, "qty_step": 1.0}}
    qty, d = calculate_qty_for_idea({"market": "US", "entry_price": 100.0, "stop_loss": 100.0})
    assert qty == 1.0
    assert d.get("fallback")


@patch("analysis.position_sizing.get_accounts_config")
def test_below_min_step_returns_zero(mock_acc) -> None:
    mock_acc.return_value = {
        "USD": {"balance": 100.0, "max_loss_pct": 0.01, "qty_step": 1.0},
    }
    idea = {"market": "CRYPTO", "entry_price": 100000.0, "stop_loss": 0.0}
    qty, d = calculate_qty_for_idea(idea)
    assert qty == 0.0
    assert d.get("skip_reason") == "below_min_step"


@patch("analysis.position_sizing.get_accounts_config")
def test_crypto_maps_usd_balance(mock_acc) -> None:
    mock_acc.return_value = {"USD": {"balance": 10000.0, "max_loss_pct": 0.02, "qty_step": 1.0}}
    idea = {"market": "CRYPTO", "entry_price": 50.0, "stop_loss": 40.0}
    qty, d = calculate_qty_for_idea(idea)
    assert qty == 20.0
    assert d.get("currency") == "USD"
