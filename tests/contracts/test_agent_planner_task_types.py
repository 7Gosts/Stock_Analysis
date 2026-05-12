from __future__ import annotations

import unittest

from app.evaluation import forbidden_internal_field_leak_rate, task_match_rate
from app.guardrails import validate_facts_bundle
from app.planner import infer_task_type_from_text


class TestAgentPlannerTaskTypes(unittest.TestCase):
    def test_infer_quote_multi(self) -> None:
        self.assertEqual(
            infer_task_type_from_text(
                "这三个现价多少",
                legacy_action="analyze_multi",
                symbol_count=3,
                with_research=False,
            ),
            "quote",
        )

    def test_infer_compare_multi(self) -> None:
        self.assertEqual(
            infer_task_type_from_text(
                "ETH 和 SOL 谁更强",
                legacy_action="analyze_multi",
                symbol_count=2,
                with_research=False,
            ),
            "compare",
        )

    def test_infer_analysis_multi_default(self) -> None:
        self.assertEqual(
            infer_task_type_from_text(
                "看下 BTC ETH 4h K线结构",
                legacy_action="analyze_multi",
                symbol_count=2,
                with_research=False,
            ),
            "analysis",
        )

    def test_infer_research_single(self) -> None:
        self.assertEqual(
            infer_task_type_from_text(
                "机构怎么看黄金",
                legacy_action="analyze",
                symbol_count=1,
                with_research=True,
            ),
            "research",
        )

    def test_task_match_rate(self) -> None:
        self.assertEqual(task_match_rate(expected="quote", actual="quote"), 1.0)
        self.assertEqual(task_match_rate(expected="quote", actual="analysis"), 0.0)

    def test_forbidden_leak_score(self) -> None:
        self.assertGreater(forbidden_internal_field_leak_rate("triggered=None 与 entry=None"), 0.0)
        self.assertEqual(forbidden_internal_field_leak_rate("正常中文结论"), 0.0)

    def test_validate_facts_bundle_quote(self) -> None:
        fb = {
            "task_type": "quote",
            "response_mode": "quick",
            "symbols": ["BTC_USDT"],
            "user_question": "现价",
            "market_facts": {"items": [{"symbol": "BTC_USDT", "last_price": 1}]},
            "risk_flags": ["normal"],
            "evidence_sources": [{"source_path": "/tmp/x", "source_type": "kline"}],
        }
        self.assertEqual(validate_facts_bundle(fb), [])
