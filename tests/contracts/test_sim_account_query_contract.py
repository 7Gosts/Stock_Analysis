from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.capabilities.sim_account_capability import view_sim_account_state
from app.query_engine.base import CapabilityResult
from app.query_engine.registry import get_query_spec


class TestSimAccountQueryContract(unittest.TestCase):
    def test_query_schemas_accept_supported_filters(self) -> None:
        open_positions = get_query_spec("account.open_positions").params_schema(
            account_id="USD",
            symbol="BTC_USDT",
            limit=5,
        )
        recent_orders = get_query_spec("account.recent_orders").params_schema(
            symbol="BTC_USDT",
            limit=5,
        )
        recent_fills = get_query_spec("account.recent_fills").params_schema(
            symbol="BTC_USDT",
            limit=5,
        )
        order_health = get_query_spec("account.order_health").params_schema(
            symbol="BTC_USDT",
        )

        self.assertEqual(
            open_positions.model_dump(),
            {"account_id": "USD", "symbol": "BTC_USDT", "limit": 5},
        )
        self.assertEqual(
            recent_orders.model_dump(),
            {"symbol": "BTC_USDT", "limit": 5},
        )
        self.assertEqual(
            recent_fills.model_dump(),
            {"symbol": "BTC_USDT", "limit": 5},
        )
        self.assertEqual(order_health.model_dump(), {"symbol": "BTC_USDT"})

    def test_orders_scope_forwards_symbol_filter(self) -> None:
        cap_result = CapabilityResult(domain="sim_account", intent="orders", summary="ok")
        spec = SimpleNamespace(
            params_schema=get_query_spec("account.recent_orders").params_schema,
            formatter=MagicMock(return_value=cap_result),
        )
        rows = [{"symbol": "BTC_USDT", "status": "filled"}]

        with (
            patch("app.capabilities.sim_account_capability._get_spec", return_value=spec),
            patch("app.capabilities.sim_account_capability.execute_named_query", return_value=rows) as mocked_exec,
        ):
            result = view_sim_account_state(scope="orders", symbol="BTC_USDT", account_id="USD", limit=5)

        mocked_exec.assert_called_once_with(
            "account.recent_orders",
            {"symbol": "BTC_USDT", "limit": 5},
        )
        spec.formatter.assert_called_once_with(rows, {"symbol": "BTC_USDT", "limit": 5})
        self.assertIs(result, cap_result)

    def test_positions_scope_forwards_account_and_symbol_filters(self) -> None:
        cap_result = CapabilityResult(domain="sim_account", intent="positions", summary="ok")
        spec = SimpleNamespace(
            params_schema=get_query_spec("account.open_positions").params_schema,
            formatter=MagicMock(return_value=cap_result),
        )
        rows = [{"account_id": "USD", "symbol": "BTC_USDT"}]

        with (
            patch("app.capabilities.sim_account_capability._get_spec", return_value=spec),
            patch("app.capabilities.sim_account_capability.execute_named_query", return_value=rows) as mocked_exec,
        ):
            result = view_sim_account_state(scope="positions", symbol="BTC_USDT", account_id="USD", limit=8)

        mocked_exec.assert_called_once_with(
            "account.open_positions",
            {"account_id": "USD", "symbol": "BTC_USDT", "limit": 8},
        )
        spec.formatter.assert_called_once_with(
            rows,
            {"account_id": "USD", "symbol": "BTC_USDT", "limit": 8},
        )
        self.assertIs(result, cap_result)