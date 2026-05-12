from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from analysis.trade_journal import update_idea_with_rows


def _bar(ts: datetime, low: float, high: float, close: float) -> dict:
    return {"time": ts.isoformat(), "low": low, "high": high, "close": close}


class TestTradeJournalStateMachine(unittest.TestCase):
    def test_long_pending_to_filled_then_closed_tp(self) -> None:
        now = datetime.now(timezone.utc)
        idea = {
            "status": "pending",
            "direction": "long",
            "entry_zone": [99.0, 101.0],
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "take_profit_levels": [105.0],
            "created_at_utc": (now - timedelta(hours=3)).isoformat(),
            "valid_until_utc": (now + timedelta(hours=6)).isoformat(),
        }
        rows = [
            _bar(now - timedelta(hours=2), 98.9, 101.2, 100.3),  # fill
            _bar(now - timedelta(hours=1), 100.1, 105.4, 105.1),  # tp
        ]
        changed = update_idea_with_rows(idea, rows, now)
        self.assertTrue(changed)
        self.assertEqual(idea.get("status"), "closed")
        self.assertEqual(idea.get("exit_status"), "tp")

    def test_short_pending_to_filled_then_closed_sl(self) -> None:
        now = datetime.now(timezone.utc)
        idea = {
            "status": "pending",
            "direction": "short",
            "entry_zone": [99.0, 101.0],
            "entry_price": 100.0,
            "stop_loss": 103.0,
            "take_profit_levels": [95.0],
            "created_at_utc": (now - timedelta(hours=3)).isoformat(),
            "valid_until_utc": (now + timedelta(hours=6)).isoformat(),
        }
        rows = [
            _bar(now - timedelta(hours=2), 99.2, 100.8, 99.8),  # fill
            _bar(now - timedelta(hours=1), 99.7, 103.3, 102.9),  # sl
        ]
        changed = update_idea_with_rows(idea, rows, now)
        self.assertTrue(changed)
        self.assertEqual(idea.get("status"), "closed")
        self.assertEqual(idea.get("exit_status"), "sl")

    def test_filled_marks_to_market_with_last_close_not_last_high(self) -> None:
        now = datetime.now(timezone.utc)
        idea = {
            "status": "filled",
            "direction": "long",
            "entry_zone": [99.0, 101.0],
            "entry_price": 100.0,
            "fill_price": 100.0,
            "filled_at_utc": (now - timedelta(hours=2)).isoformat(),
            "stop_loss": 95.0,
            "take_profit_levels": [120.0],
            "created_at_utc": (now - timedelta(hours=3)).isoformat(),
            "valid_until_utc": (now + timedelta(hours=6)).isoformat(),
        }
        rows = [
            _bar(now - timedelta(hours=1), 99.5, 110.0, 101.0),
        ]
        changed = update_idea_with_rows(idea, rows, now)
        self.assertTrue(changed)
        self.assertEqual(idea.get("status"), "filled")
        self.assertEqual(idea.get("exit_status"), "float_profit")
        self.assertEqual(idea.get("unrealized_pnl_pct"), 1.0)


if __name__ == "__main__":
    unittest.main()

