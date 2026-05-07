"""goldapi：interval → kline period 映射契约。"""
from __future__ import annotations

import unittest

from analysis.gold_api import GOLDAPI_INTERVAL_TO_PERIOD_MIN, interval_to_gold_kline_period_minutes


class TestGoldApiIntervals(unittest.TestCase):
    def test_mapping_covers_feishu_intervals(self) -> None:
        for iv in ("15m", "30m", "1h", "4h", "1d"):
            self.assertIn(iv, GOLDAPI_INTERVAL_TO_PERIOD_MIN)

    def test_period_minutes(self) -> None:
        self.assertEqual(interval_to_gold_kline_period_minutes("4h"), 240)
        self.assertEqual(interval_to_gold_kline_period_minutes("1d"), 1440)
        self.assertEqual(interval_to_gold_kline_period_minutes("1day"), 1440)

    def test_unknown_raises(self) -> None:
        with self.assertRaises(ValueError):
            interval_to_gold_kline_period_minutes("1w")


if __name__ == "__main__":
    unittest.main()
