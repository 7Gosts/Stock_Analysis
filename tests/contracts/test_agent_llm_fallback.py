from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agent_service import TaskRunner


class TestAgentLlmFallback(unittest.TestCase):
    def test_use_rules_when_llm_disabled(self) -> None:
        runner = TaskRunner()
        run_result = {
            "session_dir": "/tmp/output/gateio/CRYPTO/2026-05-07",
            "symbols_processed": ["BTC_USDT"],
            "overview_path": "/tmp/output/gateio/CRYPTO/2026-05-07/ai_overview.json",
        }
        item = {
            "symbol": "BTC_USDT",
            "name": "Bitcoin",
            "provider": "gateio",
            "interval": "1d",
            "stats": {
                "trend": "偏多",
                "last": 80000.0,
                "price_vs_fib_zone": "0.618~0.786",
                "market_regime": {"id": "trend_up", "label": "趋势上行", "confidence": 78},
                "structure_filters_v1": {"flags": ["normal"]},
                "mtf_v1": {"enabled": True, "aligned": True},
                "wyckoff_123_v1": {
                    "selected_setup": {
                        "entry": 79000.0,
                        "stop": 76000.0,
                        "tp1": 83000.0,
                        "tp2": 85000.0,
                        "triggered": False,
                    }
                },
                "time_stop_v1": {"rule": "mock-rule"},
            },
        }
        with patch("app.agent_service._llm_enabled", return_value=False):
            payload = runner._to_agent_payload(
                run_result=run_result,
                item=item,
                question="BTC 要不要开多？",
                use_rag=False,
                rag_top_k=5,
                use_llm_decision=True,
            )
        self.assertEqual(payload["analysis_result"]["decision_source"], "rules")
        self.assertNotIn("llm_decision", payload["analysis_result"])


if __name__ == "__main__":
    unittest.main()
