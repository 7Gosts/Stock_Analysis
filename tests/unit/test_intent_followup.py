"""intent_detectors 追问逻辑测试。"""
from __future__ import annotations

import unittest

from app.intent_detectors import looks_like_followup, resolve_followup_target
from app.session_state import SessionState


class TestIntentFollowup(unittest.TestCase):
    def test_resolve_requires_prior_symbol(self) -> None:
        st = SessionState(open_id="x", last_action="analysis", last_symbol="BTC_USDT")
        r = resolve_followup_target("这个入场怎么说", st)
        self.assertTrue(r.get("resolved"))

    def test_looks_like_followup_true(self) -> None:
        self.assertTrue(looks_like_followup("它的触发条件呢"))
        self.assertTrue(looks_like_followup("继续刚才的分析"))

    def test_looks_like_followup_false(self) -> None:
        self.assertFalse(looks_like_followup("分析 BTC"))
        self.assertFalse(looks_like_followup("看下 ETH 行情"))


if __name__ == "__main__":
    unittest.main()
