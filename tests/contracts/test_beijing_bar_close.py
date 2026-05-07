from __future__ import annotations

import unittest
from datetime import datetime

from analysis.beijing_time import (
    BEIJING,
    default_review_time_for_interval,
    next_utc_aligned_bar_close_beijing,
    review_time_has_explicit_clock,
)


class TestBeijingBarClose(unittest.TestCase):
    def test_next_4h_close_after_noon_beijing(self) -> None:
        # 2026-05-07 12:00 CST = 2026-05-07 04:00 UTC，下一根 4h UTC 收盘为 08:00 UTC = 16:00 CST
        fixed = datetime(2026, 5, 7, 12, 0, 0, tzinfo=BEIJING)
        dt = next_utc_aligned_bar_close_beijing(interval="4h", now_bj=fixed)
        assert dt is not None
        self.assertEqual(dt.strftime("%Y-%m-%d %H:%M"), "2026-05-07 16:00")

    def test_default_review_contains_clock_for_4h(self) -> None:
        label = default_review_time_for_interval("4h")
        self.assertRegex(label, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
        self.assertIn("下一根4h收盘", label)

    def test_default_review_1d_is_calendar_phrase(self) -> None:
        s = default_review_time_for_interval("1d")
        self.assertIn("交易日", s)

    def test_review_time_has_explicit_clock(self) -> None:
        self.assertTrue(review_time_has_explicit_clock("2026-05-08 04:00（北京时间）"))
        self.assertFalse(review_time_has_explicit_clock("下一根4h收盘后"))
        self.assertFalse(review_time_has_explicit_clock("下个交易日收盘后复核（北京时间）"))


if __name__ == "__main__":
    unittest.main()
