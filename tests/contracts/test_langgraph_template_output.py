from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from app.langgraph_flow import REQUIRED_TEMPLATE_KEYS, _normalize_fixed_template, run_graph


class _FakeLLM:
    def __init__(self) -> None:
        self._tools = []

    def bind_tools(self, tools):
        self._tools = tools
        return self

    def invoke(self, messages):
        has_tool = any(isinstance(m, ToolMessage) for m in messages)
        if not has_tool:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "fetch_analysis_bundle",
                        "args": {
                            "symbol": "BTC_USDT",
                            "provider": "gateio",
                            "interval": "1d",
                            "limit": 120,
                            "question": "test",
                        },
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="")


def _fake_tools(*, repo_root: Path):
    @tool
    def fetch_analysis_bundle(
        symbol: str,
        provider: str = "gateio",
        interval: str = "1d",
        limit: int = 180,
        out_dir: str | None = None,
        question: str | None = None,
        rag_top_k: int = 5,
        analysis_style: str = "auto",
    ) -> dict:
        """返回测试用分析快照。"""
        return {
            "analysis_result": {
                "symbol": symbol,
                "name": "Bitcoin",
                "provider": provider,
                "interval": interval,
                "trend": "偏多",
                "last_price": 80000.0,
                "fib_zone": "0.618~0.786",
                "trigger_conditions": {"entry": 80000},
                "invalidation_conditions": {"stop": 76000},
                "risk_points": ["低流动性风险"],
                "decision_source": "rules",
                "fixed_template": {
                    "综合倾向": "偏多",
                    "关键位(Fib)": "0.618~0.786",
                    "触发条件": "entry=80000",
                    "失效条件": "stop=76000",
                    "风险点": ["低流动性风险"],
                    "下次复核时间": "下一根4hK线收盘后",
                },
            },
            "risk_flags": ["normal"],
            "evidence_sources": [{"source_path": "/tmp/mock.json", "source_type": "kline"}],
            "meta": {"session_dir": "/tmp/output"},
        }

    return [fetch_analysis_bundle]


class TestLangGraphTemplateOutput(unittest.TestCase):
    def test_fixed_template_keys_are_complete(self) -> None:
        with (
            patch("app.langgraph_flow._build_llm", return_value=_FakeLLM()),
            patch("app.langgraph_flow.make_tools", side_effect=_fake_tools),
        ):
            out = run_graph(
                repo_root=Path("/tmp"),
                symbol="BTC_USDT",
                provider="gateio",
                interval="1d",
                question="按模板输出",
            )
        tpl = out["analysis_result"]["fixed_template"]
        self.assertTrue(all(k in tpl for k in REQUIRED_TEMPLATE_KEYS))
        self.assertIsInstance(tpl["风险点"], list)
        self.assertEqual(out["analysis_result"]["decision_source"], "rules")

    def test_overview_fixed_template_overrides_llm_on_levels_trigger(self) -> None:
        fb = {
            "综合倾向": "偏多",
            "关键位(Fib)": "0.382~0.5",
            "触发条件": "entry=2400，tp1=2450，triggered=false",
            "失效条件": "stop=2350",
            "风险点": ["流动性"],
            "下次复核时间": "2030-01-15 20:00（北京时间，下一根4h收盘）",
        }
        llm = {
            "综合倾向": "观望",
            "关键位(Fib)": "未提供具体价格数值",
            "触发条件": "未提供明确触发条件",
            "失效条件": "未提供",
            "风险点": ["模型写的风险"],
            "下次复核时间": "明日",
        }
        out = _normalize_fixed_template(llm_template=llm, fallback=fb)
        self.assertEqual(out["关键位(Fib)"], fb["关键位(Fib)"])
        self.assertEqual(out["触发条件"], fb["触发条件"])
        self.assertEqual(out["失效条件"], fb["失效条件"])
        self.assertEqual(out["综合倾向"], "观望")
        self.assertEqual(out["下次复核时间"], fb["下次复核时间"])


if __name__ == "__main__":
    unittest.main()
