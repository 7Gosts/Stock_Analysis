from __future__ import annotations

import unittest

from tools.llm.client import (
    LLMClientError,
    _feishu_router_tool_definitions,
    _tool_calls_to_routed_dict,
)


class TestLlmRouteContract(unittest.TestCase):
    def test_router_tool_definitions_do_not_expose_legacy_clarify(self) -> None:
        tool_names = [
            tool.get("function", {}).get("name")
            for tool in _feishu_router_tool_definitions()
            if isinstance(tool, dict)
        ]
        self.assertNotIn("ask_clarify", tool_names)
        self.assertIn("reply_chat", tool_names)

    def test_reply_chat_tool_call_maps_to_chat_route(self) -> None:
        routed = _tool_calls_to_routed_dict(
            [
                {
                    "function": {
                        "name": "reply_chat",
                        "arguments": '{"message": "请告诉我标的和周期。"}',
                    }
                }
            ]
        )

        self.assertEqual(
            routed,
            {"action": "chat", "chat_reply": "请告诉我标的和周期。"},
        )

    def test_legacy_clarify_tool_call_is_rejected(self) -> None:
        with self.assertRaises(LLMClientError) as ctx:
            _tool_calls_to_routed_dict(
                [
                    {
                        "function": {
                            "name": "ask_clarify",
                            "arguments": '{"message": "缺少标的"}',
                        }
                    }
                ]
            )

        self.assertIn("未知路由工具", str(ctx.exception))

    def test_multi_tool_calls_merge_analyze_and_research(self) -> None:
        routed = _tool_calls_to_routed_dict(
            [
                {
                    "function": {
                        "name": "analyze_market",
                        "arguments": '{"symbols": ["BTC_USDT"], "interval": "4h", "question": "看下 BTC 走势"}',
                    }
                },
                {
                    "function": {
                        "name": "search_research",
                        "arguments": '{"keyword": "比特币 ETF"}',
                    }
                },
            ]
        )

        self.assertEqual(routed["action"], "analyze")
        self.assertEqual(routed["symbols"], ["BTC_USDT"])
        self.assertTrue(routed["with_research"])
        self.assertEqual(routed["research_keyword"], "比特币 ETF")
        self.assertEqual(len(routed.get("plan_steps") or []), 2)

    def test_multi_analyze_tool_calls_merge_symbols(self) -> None:
        routed = _tool_calls_to_routed_dict(
            [
                {
                    "function": {
                        "name": "analyze_market",
                        "arguments": '{"symbols": ["BTC_USDT"], "interval": "4h"}',
                    }
                },
                {
                    "function": {
                        "name": "analyze_market",
                        "arguments": '{"symbols": ["ETH_USDT", "BTC_USDT"], "interval": "4h"}',
                    }
                },
            ]
        )

        self.assertEqual(routed["action"], "analyze")
        self.assertEqual(routed["symbols"], ["BTC_USDT", "ETH_USDT"])
        self.assertEqual(len(routed.get("plan_steps") or []), 2)


if __name__ == "__main__":
    unittest.main()