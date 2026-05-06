from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.orchestrator import build_trade_journal_entry
from analysis import journal_policy


def _sample_stats() -> dict:
    return {
        "last": 105.0,
        "trend": "偏多",
        "time_stop_v1": {"max_wait_bars": 5, "rule": "demo"},
        "wyckoff_123_v1": {
            "aligned": True,
            "background": {"bias": "long_only"},
            "selected_setup": {
                "side": "long",
                "triggered": False,
                "entry": 104.0,
                "stop": 100.0,
                "tp1": 110.5,
                "tp2": 114.5,
            },
            "setups": {},
        },
        "mtf_v1": {"enabled": True, "aligned": True},
        "structure_filters_v1": {"flags": ["normal"]},
    }


class TestTradeJournalSchema(unittest.TestCase):
    def test_tactical_entry_schema_and_gates(self) -> None:
        idea = build_trade_journal_entry(
            now_utc=datetime.now(timezone.utc),
            asset={"symbol": "TEST.SZ", "name": "测试", "market": "CN", "tags": ["测试"]},
            provider="tickflow",
            interval="1d",
            stats=_sample_stats(),
        )
        self.assertIsNotNone(idea)
        assert idea is not None
        required = {
            "idea_id",
            "created_at_utc",
            "symbol",
            "market",
            "provider",
            "interval",
            "plan_type",
            "direction",
            "entry_zone",
            "entry_price",
            "stop_loss",
            "take_profit_levels",
            "status",
        }
        self.assertTrue(required.issubset(set(idea.keys())))
        self.assertIn(idea["plan_type"], {"tactical", "swing"})
        self.assertIn(idea["direction"], {"long", "short"})
        self.assertIsInstance(idea["entry_zone"], list)
        self.assertEqual(len(idea["entry_zone"]), 2)
        self.assertIsInstance(idea["take_profit_levels"], list)
        self.assertGreaterEqual(len(idea["take_profit_levels"]), 1)

        ok, _reason = journal_policy.idea_passes_journal_append_gates(idea)
        self.assertIsInstance(ok, bool)


if __name__ == "__main__":
    unittest.main()

