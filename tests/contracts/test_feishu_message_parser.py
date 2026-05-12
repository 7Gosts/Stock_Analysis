from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.memory_store import JsonlMemoryStore, MemoryEvent
from tools.deepseek.client import DeepSeekError, decide_feishu_route

from app.feishu_bot_service import (
    _BOT_START_TS_MS,
    _CONV_STATE,
    _SEEN_MESSAGE_IDS,
    append_conversation_message,
    extract_message_create_time_ms,
    get_conversation_state,
    get_recent_messages,
    is_stale_message,
    parse_user_message,
    route_user_message,
    should_process_message,
    update_conversation_state,
)


class TestFeishuMessageParser(unittest.TestCase):
    def setUp(self) -> None:
        _SEEN_MESSAGE_IDS.clear()
        _CONV_STATE.clear()

    def test_parse_defaults_only_symbol_interval_from_llm(self) -> None:
        payload = parse_user_message(
            "帮我看一下 ETH_USDT 1d",
            default_symbol="BTC_USDT",
            default_interval="4h",
        )
        self.assertEqual(payload["symbol"], "BTC_USDT")
        self.assertEqual(payload["interval"], "4h")
        self.assertIn("ETH_USDT", payload["question"])

    def test_fallback_to_default(self) -> None:
        payload = parse_user_message(
            "你好",
            default_symbol="SOL_USDT",
            default_interval="4h",
        )
        self.assertEqual(payload["symbol"], "SOL_USDT")
        self.assertEqual(payload["interval"], "4h")
        self.assertEqual(payload["provider"], "gateio")

    def test_should_process_message_dedup(self) -> None:
        self.assertTrue(should_process_message("om_1", now_ts=1000))
        self.assertFalse(should_process_message("om_1", now_ts=1001))

    def test_parse_natural_text_keeps_defaults(self) -> None:
        payload = parse_user_message(
            "看下 SOL_USDT 15m",
            default_symbol="BTC_USDT",
            default_interval="4h",
        )
        self.assertEqual(payload["symbol"], "BTC_USDT")
        self.assertEqual(payload["interval"], "4h")
        self.assertIn("SOL_USDT", payload["question"])

    def test_parse_bare_eth_sol_keeps_defaults(self) -> None:
        payload = parse_user_message(
            r"看下 eth \sol 的短线",
            default_symbol="BTC_USDT",
            default_interval="4h",
        )
        self.assertEqual(payload["symbol"], "BTC_USDT")
        self.assertEqual(payload["interval"], "4h")
        self.assertIn("eth", payload["question"].lower())

    def test_route_omitted_symbol_clarifies_not_default_btc(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={"action": "analyze", "interval": "4h", "question": ""},
        ):
            routed = route_user_message(
                r"看下 eth \sol 的短线",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "clarify")
        self.assertEqual(routed.get("clarify_message"), "")

    def test_route_invalid_symbol_clarifies(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={"action": "analyze", "symbol": "FOO_USDT", "interval": "4h", "question": ""},
        ):
            routed = route_user_message(
                "看下 FOO",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "clarify")
        self.assertEqual(routed.get("clarify_message"), "")

    def test_route_user_message_clarify_by_router(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={"action": "clarify", "clarify_message": "请补充标的和周期"},
        ):
            routed = route_user_message(
                "看看",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "clarify")
        self.assertIn("请补充", routed["clarify_message"])

    def test_route_user_message_analyze_by_router(self) -> None:
        with (
            patch(
                "app.planner.decide_feishu_route",
                return_value={
                    "action": "analyze",
                    "symbol": "ETH_USDT",
                    "interval": "1d",
                    "question": "关注回踩后是否延续",
                },
            ),
        ):
            routed = route_user_message(
                "看下eth日线",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "analyze")
        payload = routed["payload"]
        self.assertEqual(payload["symbol"], "ETH_USDT")
        self.assertEqual(payload["interval"], "1d")
        self.assertEqual(payload["provider"], "gateio")

    def test_route_bare_eth_lands_to_eth_usdt(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={"action": "analyze", "symbol": "ETH", "interval": "4h", "question": "短线"},
        ):
            routed = route_user_message(
                "看下eth短线",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "analyze")
        self.assertEqual(routed["payload"]["symbol"], "ETH_USDT")

    def test_route_unknown_action_clarifies(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={"action": "noop", "symbol": "BTC_USDT"},
        ):
            routed = route_user_message(
                "随便",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "clarify")

    def test_route_user_message_chat_by_router(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={"action": "chat", "chat_reply": "你好呀，我在呢"},
        ):
            routed = route_user_message(
                "你好呀",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "chat")
        self.assertIn("你好呀", routed["chat_reply"])

    def test_route_user_message_multi_symbols_by_router(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["BTC_USDT", "ETH_USDT", "SOL_USDT"],
                "interval": "1h",
                "question": "看下短线行情",
            },
        ):
            routed = route_user_message(
                "看下比特币、ETH、SOL短线",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "analyze_multi")
        payloads = routed["payloads"]
        self.assertEqual(len(payloads), 3)
        self.assertEqual(payloads[0]["interval"], "1h")
        for p in payloads:
            self.assertEqual(p["provider"], "gateio")

    def test_route_nvda_tickflow_with_research(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbol": "NVDA",
                "interval": "1d",
                "question": "带研报",
                "provider": "tickflow",
                "with_research": True,
                "research_keyword": "英伟达",
            },
        ):
            routed = route_user_message(
                "NVDA 1d 带研报",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "analyze")
        pl = routed["payload"]
        self.assertEqual(pl["symbol"], "NVDA")
        self.assertEqual(pl["provider"], "tickflow")
        self.assertTrue(pl["with_research"])
        self.assertEqual(pl["research_keyword"], "英伟达")

    def test_route_multi_mixed_providers(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["NVDA", "BTC_USDT", "AU9999"],
                "interval": "4h",
                "question": "对比节奏",
                "with_research": False,
            },
        ):
            routed = route_user_message(
                "NVDA、BTC、黄金都看 4h",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "analyze_multi")
        payloads = routed["payloads"]
        self.assertEqual(len(payloads), 3)
        by_sym = {p["symbol"]: p["provider"] for p in payloads}
        self.assertEqual(by_sym["NVDA"], "tickflow")
        self.assertEqual(by_sym["BTC_USDT"], "gateio")
        self.assertEqual(by_sym["AU9999"], "goldapi")

    def test_extract_message_create_time_ms(self) -> None:
        class _Msg:
            create_time = "1715078400000"

        class _Evt:
            message = _Msg()

        class _Data:
            event = _Evt()

        ts = extract_message_create_time_ms(_Data())
        self.assertEqual(ts, 1715078400000)

    def test_is_stale_message(self) -> None:
        stale_ms = max(1, _BOT_START_TS_MS - 60_000)

        class _Msg:
            create_time = str(stale_ms)

        class _Evt:
            message = _Msg()

        class _Data:
            event = _Evt()

        self.assertTrue(is_stale_message(_Data()))

    def test_followup_uses_context(self) -> None:
        ctx = {"last_symbol": "ETH_USDT", "last_interval": "1h", "last_question": "看下ETH走势"}
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbol": "ETH_USDT",
                "interval": "1h",
                "question": "看下ETH走势；补充：继续",
            },
        ):
            routed = route_user_message(
                "继续",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=ctx,
            )
        self.assertEqual(routed["action"], "analyze")
        payload = routed["payload"]
        self.assertEqual(payload["symbol"], "ETH_USDT")
        self.assertEqual(payload["interval"], "1h")
        self.assertIn("补充", payload["question"])

    def test_update_and_get_conversation_state(self) -> None:
        route = {
            "action": "analyze",
            "payload": {
                "symbol": "SOL_USDT",
                "interval": "15m",
                "question": "看下SOL",
                "provider": "gateio",
            },
        }
        update_conversation_state("ou_xxx", route=route, raw_text="看下SOL 15m")
        st = get_conversation_state("ou_xxx")
        self.assertEqual(st.get("last_symbol"), "SOL_USDT")
        self.assertEqual(st.get("last_interval"), "15m")
        self.assertEqual(st.get("last_provider"), "gateio")

    def test_recent_messages_window(self) -> None:
        append_conversation_message("ou_mem", role="user", text="看下BTC 4h")
        append_conversation_message("ou_mem", role="assistant", text="好的，先看4h")
        append_conversation_message("ou_mem", role="user", text="再看1h")
        rows = get_recent_messages("ou_mem", rounds=1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["role"], "assistant")
        self.assertEqual(rows[1]["role"], "user")

    def test_router_receives_recent_messages(self) -> None:
        recent = [{"role": "user", "text": "看下BTC 4h"}]
        with patch(
            "app.planner.decide_feishu_route",
            return_value={"action": "chat", "chat_reply": "收到"},
        ) as mocked:
            route_user_message(
                "继续",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context={"last_symbol": "BTC_USDT", "last_interval": "4h"},
                recent_messages=recent,
            )
        called_kwargs = mocked.call_args.kwargs
        self.assertIn("recent_messages", called_kwargs)
        self.assertEqual(called_kwargs["recent_messages"], recent)
        self.assertIn("tradable_assets", called_kwargs)
        self.assertGreater(len(called_kwargs.get("tradable_assets") or []), 0)

    def test_context_can_fallback_to_persistent_memory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = JsonlMemoryStore(path=Path(td) / "mem.jsonl")
            store.append_event(
                MemoryEvent(
                    open_id="ou_ctx",
                    role="assistant",
                    text="历史分析",
                    symbol="BTC_USDT",
                    interval="4h",
                    question="看下BTC",
                )
            )
            st = get_conversation_state("ou_ctx", memory_store=store)
            self.assertEqual(st.get("last_symbol"), "BTC_USDT")
            self.assertEqual(st.get("last_interval"), "4h")

    def test_strict_mode_router_error_goes_clarify(self) -> None:
        with patch("app.planner.decide_feishu_route", side_effect=RuntimeError("x")):
            routed = route_user_message(
                "看下走势",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "clarify")
        self.assertEqual(routed.get("clarify_message"), "")

    def test_analyze_missing_symbol_clarifies(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={"action": "analyze", "symbol": "", "interval": "4h", "question": ""},
        ):
            routed = route_user_message(
                "看下走势",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context=None,
            )
        self.assertEqual(routed["action"], "clarify")
        self.assertEqual(routed.get("clarify_message"), "")

    def test_empty_text_goes_clarify(self) -> None:
        routed = route_user_message(
            "",
            default_symbol="BTC_USDT",
            default_interval="4h",
            context=None,
        )
        self.assertEqual(routed["action"], "clarify")
        self.assertEqual(routed.get("clarify_message"), "")

    def test_natural_language_all_request_goes_multi(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["BTC_USDT", "ETH_USDT", "SOL_USDT"],
                "interval": "4h",
                "question": "都看短线行情",
            },
        ):
            routed = route_user_message(
                "都看，4h",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context={},
            )
        self.assertEqual(routed["action"], "analyze_multi")
        payloads = routed["payloads"]
        self.assertEqual(len(payloads), 3)
        self.assertEqual(payloads[0]["interval"], "4h")

    def test_natural_language_crypto_generic_goes_multi(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbols": ["BTC_USDT", "ETH_USDT", "SOL_USDT"],
                "interval": "4h",
                "question": "看下虚拟货币的行情",
            },
        ):
            routed = route_user_message(
                "看下虚拟货币的行情",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context={},
            )
        self.assertEqual(routed["action"], "analyze_multi")

    def test_first_choice_after_clarify(self) -> None:
        with patch(
            "app.planner.decide_feishu_route",
            return_value={
                "action": "analyze",
                "symbol": "BTC_USDT",
                "interval": "4h",
                "question": "都看，4h",
            },
        ):
            routed = route_user_message(
                "第一个",
                default_symbol="BTC_USDT",
                default_interval="4h",
                context={"pending_clarify": True, "last_interval": "4h", "last_user_text": "都看，4h"},
            )
        self.assertEqual(routed["action"], "analyze")
        self.assertEqual(routed["payload"]["symbol"], "BTC_USDT")

    def test_decide_feishu_route_parses_analyze_tool(self) -> None:
        fake_resp = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "analyze_market",
                                    "arguments": '{"symbol":"ETH_USDT","interval":"1d","question":"日线"}',
                                },
                            }
                        ]
                    }
                }
            ]
        }
        with patch("tools.deepseek.client._post_json", return_value=fake_resp):
            r = decide_feishu_route(
                text="看下eth日线",
                default_symbol="BTC_USDT",
                default_interval="4h",
                tradable_assets=[],
            )
        self.assertEqual(r["action"], "analyze")
        self.assertEqual(r["symbol"], "ETH_USDT")
        self.assertEqual(r["interval"], "1d")

    def test_decide_feishu_route_hello_user_content_only_no_tool_calls(self) -> None:
        """模拟：飞书用户发「你好」，DeepSeek 未调 tools，只在 message.content 里回寒暄（真实 API 常见形态）。"""
        # chat/completions 典型片段：无 tool_calls，仅有 assistant 正文（可能被写成闲聊而未触发 reply_chat）
        fake_deepseek_json = {
            "id": "cmpl-feishu-sim",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": (
                            "你好！我是行情分析助手，可以说下想看的标的和周期，"
                            "例如 NVDA 1d、BTC_USDT 4h、AU9999 1d。"
                        ),
                        # 注意：无 tool_calls 或 tool_calls 为空时走正文兜底 → route 解析为 action=chat
                    },
                }
            ],
        }
        with patch("tools.deepseek.client._post_json", return_value=fake_deepseek_json):
            r = decide_feishu_route(
                text="你好",
                default_symbol="BTC_USDT",
                default_interval="4h",
                tradable_assets=[],
            )
        # 本地跑单测时可直接看到解析结果（unittest 默认成功用例不打印断言对象）
        print(
            "\n[模拟飞书发「你好」] DeepSeek 无 tool_calls，仅用 message.content → route_user_message 等价结构：\n"
            + json.dumps(r, ensure_ascii=False, indent=2)
            + "\n",
            flush=True,
        )
        self.assertEqual(r["action"], "chat")
        self.assertEqual(
            r["chat_reply"],
            "你好！我是行情分析助手，可以说下想看的标的和周期，例如 NVDA 1d、BTC_USDT 4h、AU9999 1d。",
        )

    def test_decide_feishu_route_returns_chat_from_raw_content_when_no_tools(self) -> None:
        fake_resp = {"choices": [{"message": {"content": "  Hi，有什么可以帮你？ "}}]}
        with patch("tools.deepseek.client._post_json", return_value=fake_resp):
            r = decide_feishu_route(
                text="你好啊",
                default_symbol="BTC_USDT",
                default_interval="4h",
                tradable_assets=[],
            )
        self.assertEqual(r["action"], "chat")
        self.assertEqual(r["chat_reply"], "Hi，有什么可以帮你？")

    def test_decide_feishu_route_raises_when_no_tool_calls_and_no_content(self) -> None:
        fake_resp = {"choices": [{"message": {}}]}
        with patch("tools.deepseek.client._post_json", return_value=fake_resp):
            with self.assertRaises(DeepSeekError):
                decide_feishu_route(
                    text="hi",
                    default_symbol="BTC_USDT",
                    default_interval="4h",
                )


if __name__ == "__main__":
    unittest.main()
