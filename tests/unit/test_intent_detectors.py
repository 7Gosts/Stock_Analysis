"""intent_detectors 单元测试。"""
from __future__ import annotations

import unittest

from app.intent_detectors import (
    apply_intent_pipeline,
    detect_display_preference,
    detect_ambiguous_market_intent,
)
from app.session_state import SessionState


class TestIntentDetectors(unittest.TestCase):
    def test_display_preference_requires_last_facts(self) -> None:
        st = SessionState(open_id="u1")
        self.assertIsNone(detect_display_preference("精确2位小数", st))
        st.last_facts_bundle = {"task_type": "sim_account", "sim_account_facts": {"domain": "sim_account"}}
        r = detect_display_preference("精确2位小数", st)
        self.assertIsNotNone(r)
        self.assertEqual(r.get("task_type"), "display_adjustment")

    def test_ambiguous_market_uses_last_symbols(self) -> None:
        st = SessionState(open_id="u1", last_symbols=["BTC_USDT"], last_interval="4h", last_provider="gateio")
        r = detect_ambiguous_market_intent("看下最新行情", st)
        self.assertIsNotNone(r)
        self.assertEqual(r.get("task_type"), "quote")

    def test_apply_pipeline_display_first(self) -> None:
        st = SessionState(open_id="u1", last_task_type="sim_account", last_symbols=["SOL_USDT"])
        st.last_facts_bundle = {"task_type": "sim_account", "sim_account_facts": {"metrics": {}}}
        r = apply_intent_pipeline("精确2位小数", st)
        self.assertIsNotNone(r)
        self.assertEqual(r.get("task_type"), "display_adjustment")
        self.assertIn("task_plan", r)
        self.assertEqual(r["task_plan"].get("task_type"), "display_adjustment")


if __name__ == "__main__":
    unittest.main()
