"""goldapi：official history → OHLCV 聚合契约。"""
from __future__ import annotations

from argparse import Namespace
import unittest
from unittest.mock import patch

from analysis.gold_api import GOLDAPI_SUPPORTED_INTERVALS, fetch_ohlcv_goldapi, normalize_gold_history_interval
from app.orchestrator import resolve_mtf_interval_effective
from tools.common.errors import ProviderError


def _sample_rows(n: int = 40) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for idx in range(n):
        rows.append(
            {
                "time": f"2026-01-{(idx % 28) + 1:02d}T00:00:00+00:00",
                "open": 1000.0 + idx,
                "high": 1001.0 + idx,
                "low": 999.0 + idx,
                "close": 1000.5 + idx,
                "volume": 1.0,
            }
        )
    return rows


class TestGoldApiIntervals(unittest.TestCase):
    def test_mapping_covers_feishu_intervals(self) -> None:
        for iv in ("1h", "4h", "1d"):
            self.assertIn(iv, GOLDAPI_SUPPORTED_INTERVALS)

    def test_interval_normalization(self) -> None:
        self.assertEqual(normalize_gold_history_interval("1h"), "1h")
        self.assertEqual(normalize_gold_history_interval("4h"), "4h")
        self.assertEqual(normalize_gold_history_interval("1day"), "1d")

    def test_unknown_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_gold_history_interval("15m")

    def test_history_success_returns_rows(self) -> None:
        rows = _sample_rows()
        with patch("analysis.gold_api.gold_api_appkey", return_value="test-key"), patch(
            "analysis.gold_api.resolve_gold_id",
            return_value="1053",
        ), patch(
            "analysis.gold_api.fetch_history",
            return_value={"data": rows},
        ), patch(
            "analysis.gold_api._rows_from_history_result",
            return_value=rows,
        ), patch(
            "analysis.gold_api._finalize_gold_rows",
            return_value=rows,
        ):
            out = fetch_ohlcv_goldapi(ticker="Au9999", market="PM", interval="4h", limit=60)

        self.assertEqual(out, rows)

    def test_history_failure_raises(self) -> None:
        with patch("analysis.gold_api.gold_api_appkey", return_value="test-key"), patch(
            "analysis.gold_api.resolve_gold_id",
            return_value="1053",
        ), patch(
            "analysis.gold_api.fetch_history",
            side_effect=ProviderError("history failed"),
        ):
            with self.assertRaises(ProviderError) as ctx:
                fetch_ohlcv_goldapi(ticker="Au9999", market="PM", interval="1d", limit=60)

        self.assertIn("goldapi history 失败", str(ctx.exception))

    def test_goldapi_daily_auto_mtf_is_disabled(self) -> None:
        args = Namespace(no_mtf=False, mtf_interval="auto", interval="1d", provider="goldapi")
        self.assertEqual(
            resolve_mtf_interval_effective(args, "PM"),
            (None, "goldapi_auto_mtf_disabled"),
        )


if __name__ == "__main__":
    unittest.main()
