from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from app.langgraph_flow import run_graph


class _CycleLLM:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        has_tool = any(isinstance(m, ToolMessage) for m in messages)
        if not has_tool:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "cycle_1",
                        "name": "fetch_analysis_bundle",
                        "args": {
                            "symbol": "ETH_USDT",
                            "provider": "gateio",
                            "interval": "4h",
                            "limit": 120,
                            "question": "test",
                        },
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(
            content=json.dumps(
                {
                    "综合倾向": "震荡偏多",
                    "关键位(Fib)": "0.5~0.618",
                    "触发条件": "突破0.618",
                    "失效条件": "跌破0.5",
                    "风险点": ["震荡反复"],
                    "下次复核时间": "下一根4hK线",
                },
                ensure_ascii=False,
            )
        )


def _cycle_tools(*, repo_root: Path):
    @tool
    def fetch_analysis_bundle(
        symbol: str,
        provider: str = "gateio",
        interval: str = "4h",
        limit: int = 180,
        out_dir: str | None = None,
        question: str | None = None,
        rag_top_k: int = 5,
        analysis_style: str = "auto",
    ) -> dict:
        """返回测试用工具调用结果。"""
        return {
            "analysis_result": {
                "symbol": symbol,
                "name": "Ethereum",
                "provider": provider,
                "interval": interval,
                "trend": "震荡偏多",
                "last_price": 2340.0,
                "fib_zone": "0.5~0.618",
                "trigger_conditions": {"entry": None},
                "invalidation_conditions": {"stop": None},
                "risk_points": ["震荡反复"],
                "decision_source": "rules",
                "fixed_template": {
                    "综合倾向": "震荡偏多",
                    "关键位(Fib)": "0.5~0.618",
                    "触发条件": "突破0.618",
                    "失效条件": "跌破0.5",
                    "风险点": ["震荡反复"],
                    "下次复核时间": "下一根4hK线",
                },
            },
            "risk_flags": ["regime:transition"],
            "evidence_sources": [{"source_path": "/tmp/mock_overview.json", "source_type": "kline"}],
            "meta": {"session_dir": "/tmp/output"},
        }

    return [fetch_analysis_bundle]


class TestLangGraphReactToolCycle(unittest.TestCase):
    def test_react_cycle_records_tool_trace(self) -> None:
        with (
            patch("app.langgraph_flow._build_llm", return_value=_CycleLLM()),
            patch("app.langgraph_flow.make_tools", side_effect=_cycle_tools),
        ):
            out = run_graph(
                repo_root=Path("/tmp"),
                symbol="ETH_USDT",
                provider="gateio",
                interval="4h",
                question="给出模板结论",
            )
        trace = out.get("meta", {}).get("tool_trace") or []
        self.assertTrue(trace)
        self.assertIn("fetch_analysis_bundle", trace[0])
        self.assertEqual(out["analysis_result"]["decision_source"], "llm+rules")


if __name__ == "__main__":
    unittest.main()
