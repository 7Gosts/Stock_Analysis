from __future__ import annotations

import unittest
from unittest.mock import patch

from app.feishu_bot_service import feishu_reply_chunks, format_fixed_template_reply


class TestFeishuNarrativeReply(unittest.TestCase):
    _payload = {
        "analysis_result": {
            "symbol": "BTC_USDT",
            "interval": "4h",
            "decision_source": "rules",
            "fixed_template": {
                "综合倾向": "偏多",
                "关键位(Fib)": "0.5",
                "触发条件": "x",
                "失效条件": "y",
                "风险点": ["r"],
                "下次复核时间": "t",
            },
        },
        "risk_flags": ["normal"],
        "evidence_sources": [{"source_path": "/tmp/a.json", "source_type": "kline"}],
    }

    def test_narrative_disabled_uses_template(self) -> None:
        with patch("app.feishu_bot_service._feishu_narrative_enabled", return_value=False):
            chunks = feishu_reply_chunks(self._payload, user_question="看下")
        self.assertTrue(chunks)
        self.assertIn("━━ BTC_USDT 4h ━━", chunks[0])
        ref = format_fixed_template_reply(self._payload, user_question="看下").strip()
        got = "\n\n".join(chunks).strip()
        self.assertEqual(got, ref)

    def test_narrative_enabled_calls_generate(self) -> None:
        with patch("app.feishu_bot_service._feishu_narrative_enabled", return_value=True):
            with patch(
                "app.feishu_bot_service.generate_feishu_narrative",
                return_value="第一段口语结论。\n\n第二段。",
            ):
                chunks = feishu_reply_chunks(self._payload, user_question="看下")
        self.assertEqual(len(chunks), 1)
        self.assertIn("口语", chunks[0])

    def test_narrative_failure_falls_back_template(self) -> None:
        with patch("app.feishu_bot_service._feishu_narrative_enabled", return_value=True):
            with patch(
                "app.feishu_bot_service.generate_feishu_narrative",
                side_effect=RuntimeError("api down"),
            ):
                chunks = feishu_reply_chunks(self._payload, user_question="看下")
        self.assertTrue(chunks)
        self.assertIn("━━ BTC_USDT", chunks[0])


if __name__ == "__main__":
    unittest.main()
