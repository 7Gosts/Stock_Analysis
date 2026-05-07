from __future__ import annotations

import unittest

from app.guardrails import validate_agent_response


class TestAgentResponseSchema(unittest.TestCase):
    def test_valid_payload_passes(self) -> None:
        payload = {
            "analysis_result": {
                "symbol": "BTC_USDT",
                "trend": "偏多",
                "fib_zone": "0.618~0.786",
                "fixed_template": {
                    "综合倾向": "偏多",
                    "关键位(Fib)": "0.618~0.786",
                    "触发条件": "entry=80000",
                    "失效条件": "stop=76000",
                    "风险点": ["低流动性假突破"],
                    "下次复核时间": "下一根4hK线收盘后复核",
                },
            },
            "risk_flags": ["normal"],
            "evidence_sources": [
                {"source_path": "/tmp/ai_overview.json", "source_type": "kline"},
            ],
        }
        errors = validate_agent_response(payload, check_paths=False)
        self.assertEqual(errors, [])

    def test_forbidden_claim_is_blocked(self) -> None:
        payload = {
            "analysis_result": {
                "symbol": "BTC_USDT",
                "comment": "该策略已成交，主力资金净流入明显",
                "fixed_template": {
                    "综合倾向": "偏多",
                    "关键位(Fib)": "0.618~0.786",
                    "触发条件": "entry=80000",
                    "失效条件": "stop=76000",
                    "风险点": ["低流动性假突破"],
                    "下次复核时间": "下一根4hK线收盘后复核",
                },
            },
            "risk_flags": ["normal"],
            "evidence_sources": [
                {"source_path": "/tmp/ai_overview.json", "source_type": "kline"},
            ],
        }
        errors = validate_agent_response(payload, check_paths=False)
        self.assertTrue(any("禁止口径" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
