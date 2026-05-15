from __future__ import annotations

import unittest
from unittest.mock import patch

from app.evaluation import forbidden_internal_field_leak_rate, task_match_rate
from app.guardrails import validate_facts_bundle
from app.planner import infer_task_type_from_text, plan_user_message, AgentRoutingError
from app.agent_schemas import AgentErrorCode, AgentErrorStage


class TestAgentPlannerTaskTypes(unittest.TestCase):
    """Planner 路由契约测试。"""

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

    def test_plan_user_message_single_symbol_analyze_uses_symbols_list(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["AU9999"],
                "interval": "1d",
                "question": "上海金 Au9999 今天走势",
            },
        ):
            route = plan_user_message(
                "上海金 Au9999 今天走势",
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        self.assertEqual(route["task_plan"]["symbols"], ["AU9999"])
        self.assertEqual(route["payload"]["symbol"], "AU9999")

    def test_gold_symbol_without_explicit_interval_falls_back_to_daily(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["AU9999"],
                "interval": "4h",
                "question": "上海金 Au9999 今天走势分析",
            },
        ):
            route = plan_user_message(
                "上海金 Au9999 今天走势",
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        self.assertEqual(route["payload"]["provider"], "goldapi")
        self.assertEqual(route["payload"]["interval"], "1d")
        self.assertEqual(route["task_plan"]["interval"], "1d")

    def test_gold_symbol_keeps_explicit_intraday_interval(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["AU9999"],
                "interval": "4h",
                "question": "上海金 Au9999 4h 走势分析",
            },
        ):
            route = plan_user_message(
                "上海金 Au9999 4h 走势",
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        self.assertEqual(route["payload"]["interval"], "4h")

    def test_plan_user_message_multi_symbol_analyze_keeps_symbols_list(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["AU9999", "BTC_USDT"],
                "interval": "4h",
                "question": "看下上海金和 BTC 的走势",
            },
        ):
            route = plan_user_message(
                "看下上海金和 BTC 的走势",
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        self.assertEqual(route["task_plan"]["symbols"], ["AU9999", "BTC_USDT"])
        self.assertEqual(route["payloads"][0]["symbol"], "AU9999")
        self.assertEqual(route["payloads"][1]["symbol"], "BTC_USDT")

    # ========== 新增 Route Contract 测试 ==========

    def test_single_symbol_route_contract_uses_symbols_list(self) -> None:
        """单标的 analyze 路由最终使用 symbols=["AU9999"]。

        验证：
        - task_plan.symbols 存在且长度为 1
        - payload.symbol 兼容单标的场景
        """
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["AU9999"],
                "interval": "1d",
                "question": "上海金 Au9999 今天走势",
            },
        ):
            route = plan_user_message(
                "上海金 Au9999 今天走势",
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        # 验证 symbols 契约
        self.assertIn("symbols", route["task_plan"])
        self.assertEqual(route["task_plan"]["symbols"], ["AU9999"])
        self.assertEqual(len(route["task_plan"]["symbols"]), 1)

        # 验证 payload 兼容单标的
        self.assertEqual(route["payload"]["symbol"], "AU9999")

    def test_multi_symbol_route_contract_uses_symbols_list(self) -> None:
        """多标的 analyze 路由最终使用 symbols=[...]。

        验证：
        - task_plan.symbols 存在且长度 > 1
        - payloads 数量与 symbols 数量一致
        """
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["AU9999", "BTC_USDT", "ETH_USDT"],
                "interval": "4h",
                "question": "看下上海金、BTC 和 ETH 的走势",
            },
        ):
            route = plan_user_message(
                "看下上海金、BTC 和 ETH 的走势",
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        # 验证 symbols 契约
        self.assertIn("symbols", route["task_plan"])
        self.assertEqual(route["task_plan"]["symbols"], ["AU9999", "BTC_USDT", "ETH_USDT"])
        self.assertEqual(len(route["task_plan"]["symbols"]), 3)

        # 验证 payloads 数量与 symbols 一致
        self.assertEqual(len(route["payloads"]), 3)
        self.assertEqual(route["payloads"][0]["symbol"], "AU9999")
        self.assertEqual(route["payloads"][1]["symbol"], "BTC_USDT")
        self.assertEqual(route["payloads"][2]["symbol"], "ETH_USDT")

    def test_quote_task_type_contract(self) -> None:
        """quote 作为 task_type 的契约验证。

        验证：
        - quote 是 task_type，不是 action
        - 多标的时 action 为 analyze_multi
        - symbols 存在
        """
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["BTC_USDT", "ETH_USDT"],
                "question": "BTC 和 ETH 现价多少",
            },
        ):
            route = plan_user_message(
                "BTC 和 ETH 现价多少",
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        # quote 是 task_type，不是 action
        # 多标的时 planner 将 action 设为 analyze_multi
        self.assertEqual(route["action"], "analyze_multi")
        # task_type 由 infer_task_type_from_text 推导为 quote 或 analysis
        self.assertIn(route["task_type"], ["quote", "analysis"])
        self.assertIn("symbols", route["task_plan"])
        self.assertEqual(len(route["task_plan"]["symbols"]), 2)

    def test_compare_task_type_contract(self) -> None:
        """compare 作为 task_type 的契约验证。

        验证：
        - compare 是 task_type，不是 action
        - 多标的时 action 为 analyze_multi
        - symbols 存在且长度 >= 2
        """
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["BTC_USDT", "ETH_USDT"],
                "interval": "4h",
                "question": "BTC 和 ETH 谁更强",
            },
        ):
            route = plan_user_message(
                "BTC 和 ETH 谁更强",
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        # compare 是 task_type，不是 action
        # 多标的时 planner 将 action 设为 analyze_multi
        self.assertEqual(route["action"], "analyze_multi")
        # task_type 由 infer_task_type_from_text 推导为 compare 或 analysis
        self.assertIn(route["task_type"], ["compare", "analysis"])
        self.assertIn("symbols", route["task_plan"])
        self.assertGreaterEqual(len(route["task_plan"]["symbols"]), 2)

    def test_research_task_type_contract(self) -> None:
        """research 作为 task_type 的契约验证。

        验证：
        - research 是 task_type，action 应为 analyze
        - symbols 可为空列表
        - research_keyword 存在
        """
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "research",  # LLM 返回 research action
                "symbols": [],
                "keyword": "半导体",
                "question": "机构怎么看半导体",
            },
        ):
            route = plan_user_message(
                "机构怎么看半导体",
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        # planner 将 action=research 转换为 action=analyze + task_type=research
        self.assertEqual(route["action"], "analyze")
        self.assertEqual(route["task_type"], "research")
        self.assertIn("symbols", route["task_plan"])
        self.assertEqual(route["task_plan"]["symbols"], [])
        # research 关键字可能在 task_plan.keyword 或 task_plan.research_keyword
        self.assertTrue("keyword" in route["task_plan"] or "research_keyword" in route["task_plan"])

    def test_chat_route_contract(self) -> None:
        """chat 路由契约验证。

        验证：
        - action=chat
        - chat_reply 存在
        """
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "chat",
                "chat_reply": "你好，有什么可以帮助你的？",
            },
        ):
            route = plan_user_message(
                "你好",
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        self.assertEqual(route["action"], "chat")
        self.assertIn("chat_reply", route)
        self.assertNotEqual(route["chat_reply"], "")

    def test_planner_preserves_router_plan_steps_for_analysis_with_research(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["BTC_USDT"],
                "interval": "4h",
                "question": "看下 BTC 4h 结构，并补充研报",
                "with_research": True,
                "research_keyword": "比特币 ETF",
                "plan_steps": [
                    {"action": "analyze", "symbols": ["BTC_USDT"], "interval": "4h"},
                    {"action": "research", "keyword": "比特币 ETF"},
                ],
            },
        ):
            route = plan_user_message(
                "看下 BTC 4h 结构，并补充研报",
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        self.assertEqual(route["action"], "analyze")
        self.assertTrue(route["payload"]["with_research"])
        self.assertEqual(route["payload"]["research_keyword"], "比特币 ETF")
        self.assertEqual(len(route.get("plan_steps") or []), 2)

    # ========== 路由错误结构化测试 ==========

    def test_analyze_route_missing_symbols_raises_structured_error(self) -> None:
        """analyze route 缺少有效标的应抛出 AgentRoutingError。

        验证：
        - 错误码为 route_missing_symbols
        - 错误阶段为 route
        - recoverable=True
        """
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": [],  # 空列表
                "interval": "4h",
                "question": "看下走势",
            },
        ):
            with self.assertRaises(AgentRoutingError) as ctx:
                plan_user_message(
                    "看下走势",
                    default_symbol="BTC_USDT",
                    default_interval="4h",
                )

        exc = ctx.exception
        self.assertEqual(exc.code, AgentErrorCode.route_missing_symbols)
        self.assertEqual(exc.stage, AgentErrorStage.route)
        self.assertTrue(exc.recoverable)
        self.assertIn("termination_reason", exc.__dict__)

    def test_empty_user_message_raises_structured_error(self) -> None:
        """空用户输入应抛出 AgentRoutingError。

        验证：
        - 错误码为 route_empty_message
        - recoverable=False
        """
        with self.assertRaises(AgentRoutingError) as ctx:
            plan_user_message(
                "",  # 空输入
                default_symbol="BTC_USDT",
                default_interval="4h",
            )

        exc = ctx.exception
        self.assertEqual(exc.code, AgentErrorCode.route_empty_message)
        self.assertEqual(exc.stage, AgentErrorStage.route)
        self.assertFalse(exc.recoverable)

    def test_unknown_route_action_raises_structured_error(self) -> None:
        """未知 action 应抛出 AgentRoutingError。

        验证：
        - 错误码为 route_unknown_action
        - recoverable=False
        """
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "invalid_action",
                "interval": "4h",
                "question": "测试",
            },
        ):
            with self.assertRaises(AgentRoutingError) as ctx:
                plan_user_message(
                    "测试",
                    default_symbol="BTC_USDT",
                    default_interval="4h",
                )

        exc = ctx.exception
        self.assertEqual(exc.code, AgentErrorCode.route_unknown_action)
        self.assertFalse(exc.recoverable)

    def test_chat_route_missing_reply_raises_structured_error(self) -> None:
        """chat route 缺少 chat_reply 应抛出 AgentRoutingError。"""
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "chat",
                # 缺少 chat_reply
            },
        ):
            with self.assertRaises(AgentRoutingError) as ctx:
                plan_user_message(
                    "随便聊聊",
                    default_symbol="BTC_USDT",
                    default_interval="4h",
                )

        exc = ctx.exception
        self.assertEqual(exc.code, AgentErrorCode.route_missing_chat_reply)


if __name__ == "__main__":
    unittest.main()
