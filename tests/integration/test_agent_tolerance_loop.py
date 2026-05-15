"""Agent 容错闭环测试（预留结构）。

本测试文件为即将落地的 repair loop 预留测试结构。
当前阶段：只测试 session state 的容错字段记录能力。
后续阶段：可以扩展为完整的 repair loop 集成测试。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agent_schemas import AgentErrorCode, AgentRequest
from app.planner import AgentRoutingError
from app.query_engine.base import CapabilityResult
from app.session_state import SessionState, SessionStateStore


class TestAgentToleranceLoop(unittest.TestCase):
    """Agent 容错闭环预留测试。"""

    def test_session_state_records_route_attempts(self) -> None:
        """验证 session state 能记录路由尝试次数。

        验证：
        - route_attempts 字段存在
        - reset_route_attempts 能重置计数
        """
        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")
            st = store.get("user_001")
            self.assertEqual(st.route_attempts, 0)

            # 记录一次错误
            store.record_error(
                "user_001",
                error_code="route_missing_symbols",
                error_stage="route",
                error_message="analyze route missing valid symbols",
                recoverable=True,
            )
            st = store.get("user_001")
            self.assertEqual(st.route_attempts, 1)

            # 再记录一次
            store.record_error(
                "user_001",
                error_code="route_invalid_provider",
                error_stage="route",
                error_message="provider not available",
                recoverable=True,
            )
            st = store.get("user_001")
            self.assertEqual(st.route_attempts, 2)

            # 重置
            store.reset_route_attempts("user_001")
            st = store.get("user_001")
            self.assertEqual(st.route_attempts, 0)

    def test_session_state_records_last_error_code(self) -> None:
        """验证 session state 能记录最近一次错误码。

        验证：
        - last_error_code 字段存在
        - record_error 能更新错误码
        """
        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")
            st = store.get("user_002")
            self.assertIsNone(st.last_error_code)

            # 记录错误
            store.record_error(
                "user_002",
                error_code="followup_missing_symbol",
                error_stage="route",
                error_message="followup route missing symbol",
                recoverable=True,
            )
            st = store.get("user_002")
            self.assertEqual(st.last_error_code, "followup_missing_symbol")

            # 记录另一个错误
            store.record_error(
                "user_002",
                error_code="db_unavailable",
                error_stage="infra",
                error_message="postgres connection failed",
                recoverable=True,
            )
            st = store.get("user_002")
            self.assertEqual(st.last_error_code, "db_unavailable")

    def test_session_state_records_repair_history(self) -> None:
        """验证 session state 能记录修正历史。

        验证：
        - repair_history 字段存在且是 list[dict]
        - 每条记录包含 attempt, error_code, timestamp 等
        """
        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")
            st = store.get("user_003")
            self.assertEqual(st.repair_history, [])

            # 记录两次错误
            store.record_error(
                "user_003",
                error_code="route_missing_symbols",
                error_stage="route",
                error_message="analyze route missing symbols",
                recoverable=True,
            )
            store.record_error(
                "user_003",
                error_code="route_invalid_symbol",
                error_stage="route",
                error_message="symbol not in tradeable list",
                recoverable=True,
            )

            st = store.get("user_003")
            self.assertEqual(len(st.repair_history), 2)

            # 验证第一条记录结构
            entry1 = st.repair_history[0]
            self.assertIn("attempt", entry1)
            self.assertIn("error_code", entry1)
            self.assertIn("error_stage", entry1)
            self.assertIn("error_message", entry1)
            self.assertIn("recoverable", entry1)
            self.assertIn("timestamp", entry1)
            self.assertEqual(entry1["error_code"], "route_missing_symbols")
            self.assertEqual(entry1["attempt"], 1)

            # 验证第二条记录
            entry2 = st.repair_history[1]
            self.assertEqual(entry2["error_code"], "route_invalid_symbol")
            self.assertEqual(entry2["attempt"], 2)

    def test_session_state_records_termination_reason(self) -> None:
        """验证 session state 能记录终止原因。

        验证：
        - termination_reason 字段存在
        - record_success 能记录成功
        - record_final_termination 能记录终止
        """
        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")
            st = store.get("user_004")
            self.assertIsNone(st.termination_reason)

            # 记录成功
            store.record_success("user_004", termination_reason="success")
            st = store.get("user_004")
            self.assertEqual(st.termination_reason, "success")
            self.assertIsNone(st.last_error_code)

            # 记录最终终止（最大尝试次数）
            store.record_final_termination(
                "user_004",
                termination_reason="max_attempts_reached",
                final_error_code="route_missing_symbols",
            )
            st = store.get("user_004")
            self.assertEqual(st.termination_reason, "max_attempts_reached")
            self.assertEqual(st.last_error_code, "route_missing_symbols")

    def test_session_state_persist_and_load(self) -> None:
        """验证 session state 能持久化并加载容错字段。

        验证：
        - 新增字段能正确持久化到 JSON
        - 从 JSON 加载后字段值正确
        """
        with tempfile.TemporaryDirectory() as td:
            persist_path = Path(td) / "state.json"
            store1 = SessionStateStore(persist_path=persist_path)

            # 记录错误和终止
            store1.record_error(
                "user_005",
                error_code="execute_analysis_failed",
                error_stage="execute",
                error_message="analysis backend timeout",
                recoverable=True,
            )
            store1.record_final_termination(
                "user_005",
                termination_reason="execute_timeout",
                final_error_code="execute_analysis_failed",
            )

            # 创建新的 store 加载持久化数据
            store2 = SessionStateStore(persist_path=persist_path)
            st = store2.get("user_005")

            self.assertEqual(st.route_attempts, 0)
            self.assertEqual(st.last_error_code, "execute_analysis_failed")
            self.assertEqual(len(st.repair_history), 1)
            self.assertEqual(st.termination_reason, "execute_timeout")

    # ========== 后续扩展：Repair Loop 预留测试结构 ==========

    def test_repair_loop_single_reroute_placeholder(self) -> None:
        """预留：最多一次自动修正测试。

        验证：
        - recoverable=True 的错误能触发 reroute
        - reroute 最多 1 次
        - 达到最大次数后输出 termination_reason
        """
        # TODO: 在 repair loop 实现后补充完整测试
        # 当前阶段：只验证字段结构已就绪
        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")
            # 模拟两次 route 失败
            store.record_error("user_006", error_code="route_missing_symbols", error_stage="route")
            store.record_error("user_006", error_code="route_missing_symbols", error_stage="route")
            st = store.get("user_006")
            self.assertEqual(st.route_attempts, 2)
            # 后续可添加：验证达到 max_attempts 后不再重试

    def test_recoverable_vs_non_recoverable_placeholder(self) -> None:
        """预留：recoverable 错误与非 recoverable 错误的行为差异。

        验证：
        - recoverable=True 的错误允许 reroute
        - recoverable=False 的错误直接终止
        """
        # TODO: 在 repair loop 实现后补充完整测试
        # 当前阶段：验证 repair_history 能记录 recoverable 标记
        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")
            store.record_error(
                "user_007",
                error_code="route_missing_symbols",
                error_stage="route",
                recoverable=True,
            )
            store.record_error(
                "user_007",
                error_code="db_unavailable",
                error_stage="infra",
                recoverable=False,
            )
            st = store.get("user_007")
            self.assertEqual(st.repair_history[0]["recoverable"], True)
            self.assertEqual(st.repair_history[1]["recoverable"], False)

    def test_handle_request_reroutes_once_for_recoverable_route_error(self) -> None:
        """recoverable 路由错误会触发一次 reroute，并在成功后结束。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")
            captured_recent_messages: list[list[dict[str, object]] | None] = []

            def _plan_side_effect(
                text: str,
                *,
                default_symbol: str,
                default_interval: str,
                session_state=None,
                recent_messages=None,
            ):
                copied = [dict(msg) for msg in recent_messages] if isinstance(recent_messages, list) else None
                captured_recent_messages.append(copied)
                if len(captured_recent_messages) == 1:
                    raise AgentRoutingError(
                        "analyze route missing valid symbols",
                        code=AgentErrorCode.route_missing_symbols,
                        recoverable=True,
                        termination_reason="llm_output_invalid",
                    )
                return {
                    "action": "chat",
                    "chat_reply": "请补充标的名称。",
                    "task_type": "chat",
                    "response_mode": "quick",
                    "task_plan": {
                        "task_type": "chat",
                        "response_mode": "quick",
                        "symbols": [],
                        "interval": default_interval,
                        "provider": None,
                        "question": "请补充标的名称。",
                        "with_research": False,
                        "research_keyword": None,
                        "user_text": text,
                        "output_refs": {},
                        "followup_context": {},
                    },
                }

            request = AgentRequest(channel="http", session_id="repair-ok", text="看下走势")

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.get_or_create_rag_index", return_value=MagicMock()),
                patch("app.agent_core.plan_user_message", side_effect=_plan_side_effect),
            ):
                resp = handle_request(request)

            self.assertEqual(resp.task_type, "chat")
            self.assertEqual(resp.reply_text, "请补充标的名称。")
            self.assertEqual(len(captured_recent_messages), 2)
            self.assertIsNone(captured_recent_messages[0])
            self.assertIsInstance(captured_recent_messages[1], list)
            self.assertTrue(any("error_code=route_missing_symbols" in str(msg.get("text", "")) for msg in captured_recent_messages[1] or []))

            st = store.get("repair-ok")
            self.assertEqual(st.route_attempts, 1)
            self.assertEqual(st.termination_reason, "success")
            self.assertIsNone(st.last_error_code)
            self.assertEqual(len(st.repair_history), 1)

    def test_handle_request_does_not_reroute_non_recoverable_route_error(self) -> None:
        """non-recoverable 路由错误应直接终止，不触发 reroute。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")

            request = AgentRequest(channel="http", session_id="repair-stop", text="")

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.get_or_create_rag_index", return_value=MagicMock()),
                patch(
                    "app.agent_core.plan_user_message",
                    side_effect=AgentRoutingError(
                        "empty user message",
                        code=AgentErrorCode.route_empty_message,
                        recoverable=False,
                        termination_reason="user_input_empty",
                    ),
                ) as mocked_plan,
            ):
                resp = handle_request(request)

            self.assertEqual(resp.task_type, "chat")
            self.assertEqual(resp.meta.get("error_code"), "route_empty_message")
            self.assertEqual(mocked_plan.call_count, 1)

            st = store.get("repair-stop")
            self.assertEqual(st.route_attempts, 1)
            self.assertEqual(st.last_error_code, "route_empty_message")
            self.assertEqual(st.termination_reason, "user_input_empty")

    def test_handle_request_max_route_attempts_reached(self) -> None:
        """recoverable 路由错误达到最大尝试次数后终止，termination_reason=max_route_attempts_reached。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")
            call_counts = [0]

            def _plan_side_effect(*args, **kwargs):
                call_counts[0] += 1
                raise AgentRoutingError(
                    "analyze route missing valid symbols",
                    code=AgentErrorCode.route_missing_symbols,
                    recoverable=True,
                    termination_reason="llm_output_invalid",
                )

            request = AgentRequest(channel="http", session_id="max-attempts", text="看下走势")

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.get_or_create_rag_index", return_value=MagicMock()),
                patch("app.agent_core.plan_user_message", side_effect=_plan_side_effect),
            ):
                resp = handle_request(request)

            # 验证只调用了 2 次（max_route_attempts=2）
            self.assertEqual(call_counts[0], 2)
            self.assertEqual(resp.task_type, "chat")
            self.assertEqual(resp.meta.get("error_code"), "route_missing_symbols")
            # 达到最大尝试次数后，termination_reason 应为 max_route_attempts_reached
            self.assertEqual(resp.meta.get("termination_reason"), "max_route_attempts_reached")

            st = store.get("max-attempts")
            self.assertEqual(st.route_attempts, 2)
            self.assertEqual(st.last_error_code, "route_missing_symbols")
            self.assertEqual(st.termination_reason, "max_route_attempts_reached")
            self.assertEqual(len(st.repair_history), 2)

            # 验证回复不包含 traceback
            self.assertNotIn("Traceback", resp.reply_text)
            self.assertNotIn("most recent call last", resp.reply_text)

    def test_handle_request_recoverable_route_error_reroute_success_clears_last_error_code(self) -> None:
        """reroute 成功后，last_error_code 应清空，termination_reason=success，repair_history 保留。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")

            def _plan_side_effect(text: str, **kwargs):
                # 第一次失败，第二次成功
                if _plan_side_effect.call_count == 0:
                    _plan_side_effect.call_count += 1
                    raise AgentRoutingError(
                        "analyze route missing valid symbols",
                        code=AgentErrorCode.route_missing_symbols,
                        recoverable=True,
                        termination_reason="llm_output_invalid",
                    )
                _plan_side_effect.call_count += 1
                return {
                    "action": "chat",
                    "chat_reply": "请补充标的名称。",
                    "task_type": "chat",
                    "response_mode": "quick",
                    "task_plan": {
                        "task_type": "chat",
                        "response_mode": "quick",
                        "symbols": [],
                        "interval": "4h",
                        "provider": None,
                        "question": "请补充标的名称。",
                        "with_research": False,
                        "research_keyword": None,
                        "user_text": text,
                        "output_refs": {},
                        "followup_context": {},
                    },
                }

            _plan_side_effect.call_count = 0

            request = AgentRequest(channel="http", session_id="reroute-success", text="看下走势")

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.get_or_create_rag_index", return_value=MagicMock()),
                patch("app.agent_core.plan_user_message", side_effect=_plan_side_effect),
            ):
                resp = handle_request(request)

            self.assertEqual(resp.task_type, "chat")
            self.assertEqual(resp.reply_text, "请补充标的名称。")

            st = store.get("reroute-success")
            # reroute 成功后状态
            self.assertEqual(st.route_attempts, 1)  # 只记录了一次失败
            self.assertEqual(st.termination_reason, "success")
            self.assertIsNone(st.last_error_code)  # 成功后清空
            # repair_history 保留失败记录
            self.assertEqual(len(st.repair_history), 1)
            self.assertEqual(st.repair_history[0]["error_code"], "route_missing_symbols")

    # ========== 执行层错误测试：不进入 reroute ==========

    def test_execute_analysis_backend_unavailable_no_reroute(self) -> None:
        """分析后端不可用（infra 错误）不触发 reroute，返回友好文案。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")

            route = {
                "action": "analyze",
                "task_type": "analysis",
                "response_mode": "analysis",
                "task_plan": {
                    "symbols": ["BTC_USDT"],
                    "interval": "4h",
                    "provider": None,
                    "question": "分析 BTC",
                    "with_research": False,
                    "research_keyword": None,
                    "user_text": "分析 BTC",
                    "output_refs": {},
                    "followup_context": {},
                },
            }

            request = AgentRequest(channel="feishu", session_id="backend-down", text="分析 BTC")

            # 模拟路由成功，但执行阶段抛出 infra 错误
            def _plan_side_effect(*args, **kwargs):
                return route

            def _facade_side_effect(*args, **kwargs):
                raise RuntimeError("分析后端服务不可用")

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.get_or_create_rag_index", return_value=MagicMock()),
                patch("app.agent_core.plan_user_message", side_effect=_plan_side_effect) as mocked_plan,
                patch("app.agent_facade.handle_user_request", side_effect=_facade_side_effect),
            ):
                resp = handle_request(request)

            # 验证：路由只调用一次，不进入 reroute
            self.assertEqual(mocked_plan.call_count, 1)

            # 验证：返回 chat-style fallback
            self.assertEqual(resp.task_type, "chat")
            self.assertTrue(len(resp.reply_text) > 0)
            self.assertNotIn("Traceback", resp.reply_text)
            self.assertNotIn("RuntimeError", resp.reply_text)

            # 验证：meta 包含结构化错误
            self.assertEqual(resp.meta.get("error_code"), "analysis_backend_unavailable")
            self.assertEqual(resp.meta.get("error_stage"), "infra")
            self.assertEqual(resp.meta.get("termination_reason"), "analysis_backend_unavailable")

            st = store.get("backend-down")
            self.assertEqual(st.route_attempts, 0)  # 执行错误不计入 route_attempts
            self.assertEqual(st.last_error_code, "analysis_backend_unavailable")
            self.assertEqual(st.termination_reason, "analysis_backend_unavailable")

    def test_facade_structured_execute_error_is_normalized_to_error_response(self) -> None:
        """facade 返回结构化 execute error 时，core 应统一包装为 chat-style error response。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")

            route = {
                "action": "analyze",
                "task_type": "analysis",
                "response_mode": "analysis",
                "task_plan": {
                    "symbols": ["BTC_USDT"],
                    "interval": "4h",
                    "provider": None,
                    "question": "分析 BTC",
                    "with_research": False,
                    "research_keyword": None,
                    "user_text": "分析 BTC",
                    "output_refs": {},
                    "followup_context": {},
                },
            }

            request = AgentRequest(channel="feishu", session_id="facade-structured-error", text="分析 BTC")

            facade_result = {
                "task_type": "analysis",
                "response_mode": "analysis",
                "facts_bundle": None,
                "final_text": "分析服务暂时不可用。",
                "reply_chunks": ["分析服务暂时不可用。"],
                "meta": {
                    "route": route,
                    "error_code": "analysis_backend_unavailable",
                    "error_stage": "infra",
                    "recoverable": True,
                    "termination_reason": "analysis_backend_unavailable",
                    "error_message": "分析后端异常：timeout",
                    "error_context": {},
                },
            }

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.get_or_create_rag_index", return_value=MagicMock()),
                patch("app.agent_core.plan_user_message", return_value=route),
                patch("app.agent_facade.handle_user_request", return_value=facade_result),
            ):
                resp = handle_request(request)

            self.assertEqual(resp.task_type, "chat")
            self.assertEqual(resp.meta.get("error_code"), "analysis_backend_unavailable")
            self.assertEqual(resp.meta.get("error_stage"), "infra")
            self.assertEqual(resp.meta.get("termination_reason"), "analysis_backend_unavailable")

            st = store.get("facade-structured-error")
            self.assertEqual(st.route_attempts, 0)
            self.assertEqual(st.last_error_code, "analysis_backend_unavailable")
            self.assertEqual(st.termination_reason, "analysis_backend_unavailable")

    def test_execute_db_unavailable_no_reroute(self) -> None:
        """数据库不可用（infra 错误）不触发 reroute，返回友好文案。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")

            request = AgentRequest(channel="http", session_id="db-down", text="分析 BTC")

            # 模拟路由阶段抛出 infra 错误
            def _plan_side_effect(*args, **kwargs):
                raise AgentRoutingError(
                    "PostgreSQL 数据库不可用",
                    code=AgentErrorCode.db_unavailable,
                    recoverable=True,  # 即使 recoverable=True，infra 错误也不会 reroute
                    termination_reason="db_unavailable",
                )

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.get_or_create_rag_index", return_value=MagicMock()),
                patch("app.agent_core.plan_user_message", side_effect=_plan_side_effect) as mocked_plan,
            ):
                resp = handle_request(request)

            # 验证：infra 错误不进入 reroute
            self.assertEqual(mocked_plan.call_count, 1)

            # 验证：返回友好文案
            self.assertEqual(resp.task_type, "chat")
            self.assertNotIn("Traceback", resp.reply_text)
            self.assertEqual(resp.meta.get("error_code"), "db_unavailable")
            self.assertEqual(resp.meta.get("error_stage"), "infra")
            self.assertEqual(resp.meta.get("termination_reason"), "db_unavailable")

    def test_execute_rag_unavailable_no_reroute(self) -> None:
        """RAG 索引不可用（infra 错误）不触发 reroute，返回友好文案。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")

            request = AgentRequest(channel="http", session_id="rag-down", text="看下 BTC")

            # 模拟 RAG 索引创建失败
            def _rag_side_effect(*args, **kwargs):
                raise RuntimeError("RAG 索引不可用")

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.get_or_create_rag_index", side_effect=_rag_side_effect),
            ):
                resp = handle_request(request)

            # 验证：返回友好文案
            self.assertEqual(resp.task_type, "chat")
            self.assertTrue(len(resp.reply_text) > 0)
            self.assertNotIn("Traceback", resp.reply_text)
            self.assertNotIn("RuntimeError", resp.reply_text)

            # 验证：meta 包含结构化错误
            self.assertEqual(resp.meta.get("error_code"), "rag_unavailable")
            self.assertEqual(resp.meta.get("error_stage"), "infra")
            self.assertEqual(resp.meta.get("termination_reason"), "rag_unavailable")

    def test_sim_account_success_records_success_state(self) -> None:
        """sim_account 成功时应走统一 capability 路径，并写入 success 终止状态。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")
            route = {
                "action": "sim_account",
                "scope": "overview",
                "account_id": "USD",
                "task_type": "sim_account",
                "response_mode": "quick",
                "task_plan": {
                    "symbols": [],
                    "interval": "4h",
                    "provider": None,
                    "question": "看看当前资金额度",
                    "with_research": False,
                    "research_keyword": None,
                    "user_text": "看看当前资金额度",
                    "output_refs": {},
                    "followup_context": {},
                },
            }
            cap_result = CapabilityResult(
                domain="sim_account",
                intent="overview",
                summary="账户余额：USD 可用 1000",
                metrics={"USD": {"available": 1000}},
                evidence_sources=["account_ledger"],
                meta={"sub_queries": ["account.latest_balances"]},
            )
            request = AgentRequest(channel="feishu", session_id="sim-account-ok", text="看看当前资金额度")

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.get_or_create_rag_index", return_value=MagicMock()),
                patch("app.agent_core.plan_user_message", return_value=route),
                patch("app.capabilities.view_sim_account_state", return_value=cap_result),
            ):
                resp = handle_request(request)

            self.assertEqual(resp.task_type, "sim_account")
            self.assertEqual(resp.reply_text, "账户余额：USD 可用 1000")
            self.assertEqual(resp.meta.get("domain"), "sim_account")
            self.assertEqual(resp.meta.get("intent"), "overview")
            self.assertEqual(resp.meta.get("capability_meta"), {"sub_queries": ["account.latest_balances"]})

            st = store.get("sim-account-ok")
            self.assertEqual(st.termination_reason, "success")
            self.assertIsNone(st.last_error_code)

    def test_sim_account_error_records_final_termination(self) -> None:
        """sim_account 失败时应记录 final termination，而不是只记 error。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")
            route = {
                "action": "sim_account",
                "scope": "overview",
                "task_type": "sim_account",
                "response_mode": "quick",
                "task_plan": {
                    "symbols": [],
                    "interval": "4h",
                    "provider": None,
                    "question": "看看账户状态",
                    "with_research": False,
                    "research_keyword": None,
                    "user_text": "看看账户状态",
                    "output_refs": {},
                    "followup_context": {},
                },
            }
            request = AgentRequest(channel="http", session_id="sim-account-fail", text="看看账户状态")

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.get_or_create_rag_index", return_value=MagicMock()),
                patch("app.agent_core.plan_user_message", return_value=route),
                patch("app.capabilities.view_sim_account_state", side_effect=RuntimeError("数据库暂时不可用")),
            ):
                resp = handle_request(request)

            self.assertEqual(resp.task_type, "chat")
            self.assertEqual(resp.meta.get("error_code"), "db_unavailable")
            self.assertEqual(resp.meta.get("termination_reason"), "db_unavailable")

            st = store.get("sim-account-fail")
            self.assertEqual(st.last_error_code, "db_unavailable")
            self.assertEqual(st.termination_reason, "db_unavailable")

    def test_execute_provider_timeout_is_classified(self) -> None:
        """分析类执行超时应细分为 execute_provider_timeout。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")

            route = {
                "action": "analyze",
                "task_type": "analysis",
                "response_mode": "analysis",
                "task_plan": {
                    "symbols": ["BTC_USDT"],
                    "interval": "4h",
                    "provider": None,
                    "question": "分析 BTC",
                    "with_research": False,
                    "research_keyword": None,
                    "user_text": "分析 BTC",
                    "output_refs": {},
                    "followup_context": {},
                },
            }

            request = AgentRequest(channel="feishu", session_id="provider-timeout", text="分析 BTC")

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.get_or_create_rag_index", return_value=MagicMock()),
                patch("app.agent_core.plan_user_message", return_value=route),
                patch("app.agent_facade.handle_user_request", side_effect=TimeoutError("provider timeout")),
            ):
                resp = handle_request(request)

            self.assertEqual(resp.meta.get("error_code"), "execute_provider_timeout")
            self.assertEqual(resp.meta.get("error_stage"), "execute")
            self.assertEqual(resp.meta.get("termination_reason"), "provider_timeout")

            st = store.get("provider-timeout")
            self.assertEqual(st.route_attempts, 0)
            self.assertEqual(st.last_error_code, "execute_provider_timeout")
            self.assertEqual(st.termination_reason, "provider_timeout")

    def test_followup_output_missing_is_classified(self) -> None:
        """追问缺少产物时应细分为 followup_output_missing。"""
        try:
            from app.agent_core import handle_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_core test")

        with tempfile.TemporaryDirectory() as td:
            store = SessionStateStore(persist_path=Path(td) / "state.json")
            route = {
                "action": "followup",
                "task_type": "followup",
                "response_mode": "followup",
                "followup_context": {
                    "resolved": True,
                    "symbol": "BTC_USDT",
                    "symbols": ["BTC_USDT"],
                    "interval": "4h",
                    "provider": None,
                    "output_refs": {"ai_overview_path": "/tmp/missing.json"},
                },
                "task_plan": {
                    "symbols": ["BTC_USDT"],
                    "interval": "4h",
                    "provider": None,
                    "question": "它的触发呢",
                    "with_research": False,
                    "research_keyword": None,
                    "user_text": "它的触发呢",
                    "output_refs": {"ai_overview_path": "/tmp/missing.json"},
                    "followup_context": {},
                },
            }
            rag_index = MagicMock()
            rag_index.get_facts_for_followup.return_value = {
                "symbol": "BTC_USDT",
                "interval": "4h",
                "found": False,
                "overview": None,
                "report": None,
                "research": None,
            }

            request = AgentRequest(channel="http", session_id="followup-missing", text="它的触发呢", context={"rag_index": rag_index})

            with (
                patch("app.agent_core.get_global_session_store", return_value=store),
                patch("app.agent_core.plan_user_message", return_value=route),
            ):
                resp = handle_request(request)

            self.assertEqual(resp.meta.get("error_code"), "followup_output_missing")
            self.assertEqual(resp.meta.get("error_stage"), "execute")
            self.assertEqual(resp.meta.get("termination_reason"), "followup_output_missing")

            st = store.get("followup-missing")
            self.assertEqual(st.route_attempts, 0)
            self.assertEqual(st.last_error_code, "followup_output_missing")
            self.assertEqual(st.termination_reason, "followup_output_missing")

    def test_facade_writer_failure_is_classified(self) -> None:
        """analysis 输出链路在 writer 终止失败时应返回 execute_writer_failed。"""
        from app.agent_facade import handle_user_request

        route = {
            "action": "analyze",
            "task_type": "analysis",
            "response_mode": "analysis",
            "payload": {"symbol": "BTC_USDT", "question": "分析 BTC"},
            "task_plan": {
                "symbols": ["BTC_USDT"],
                "interval": "4h",
                "provider": None,
                "question": "分析 BTC",
                "with_research": False,
                "research_keyword": None,
                "user_text": "分析 BTC",
                "output_refs": {},
                "followup_context": {},
            },
        }

        result_payload = {
            "analysis_result": {
                "symbol": "BTC_USDT",
                "provider": "tickflow",
                "interval": "4h",
                "trend": "up",
                "fixed_template": {"综合倾向": "偏多", "关键位(Fib)": "1", "触发条件": "2", "失效条件": "3", "风险点": "4", "下次复核时间": "5"},
            },
            "meta": {},
        }

        with (
            patch("app.agent_facade._run_analysis_local", return_value=result_payload),
            patch("app.agent_facade.grounded_writer_enabled", return_value=True),
            patch("app.agent_facade.safe_grounded_write", return_value=None),
            patch("app.agent_facade.write_legacy_narrative_if_enabled", side_effect=RuntimeError("writer boom")),
            patch("app.agent_facade.fallback_to_template_reply_enabled", return_value=False),
        ):
            resp = handle_user_request(
                text="分析 BTC",
                channel="http",
                context={
                    "route": route,
                    "user_message_for_chunks": "分析 BTC",
                    "api_base_url": "http://127.0.0.1:8000",
                    "repo_root": "/home/yangtongliu/code/Stock_Analysis",
                    "rag_index": MagicMock(),
                },
            )

        self.assertEqual(resp.get("meta", {}).get("error_code"), "execute_writer_failed")
        self.assertEqual(resp.get("meta", {}).get("error_stage"), "execute")
        self.assertEqual(resp.get("meta", {}).get("termination_reason"), "writer_failed")

    # ========== 错误响应契约测试 ==========

    def test_agent_response_error_has_chat_style_fallback(self) -> None:
        """AgentResponse.error() 必须返回 chat-style fallback 文案，不泄露 traceback。"""
        from app.agent_schemas import AgentResponse, AgentError, AgentErrorCode, AgentErrorStage

        agent_error = AgentError(
            code=AgentErrorCode.execute_analysis_failed,
            stage=AgentErrorStage.execute,
            recoverable=True,
            message="分析后端超时",
            termination_reason="execute_timeout",
        )

        resp = AgentResponse.error(
            error_msg="分析后端超时",
            fallback_text="分析执行失败，请稍后重试。",
            agent_error=agent_error,
        )

        # 验证：task_type=chat，response_mode=quick
        self.assertEqual(resp.task_type, "chat")
        self.assertEqual(resp.response_mode, "quick")

        # 验证：reply_text 是用户友好的 fallback
        self.assertEqual(resp.reply_text, "分析执行失败，请稍后重试。")
        self.assertNotIn("Traceback", resp.reply_text)

        # 验证：meta 包含结构化错误信息
        self.assertEqual(resp.meta.get("error_code"), "execute_analysis_failed")
        self.assertEqual(resp.meta.get("error_stage"), "execute")
        self.assertEqual(resp.meta.get("recoverable"), True)
        self.assertEqual(resp.meta.get("termination_reason"), "execute_timeout")
        self.assertEqual(resp.meta.get("error_message"), "分析后端超时")

    def test_agent_response_error_default_fallback(self) -> None:
        """AgentResponse.error() 未提供 fallback_text 时使用默认兜底文案。"""
        from app.agent_schemas import AgentResponse, AgentError, AgentErrorCode, AgentErrorStage

        agent_error = AgentError(
            code=AgentErrorCode.unknown,
            stage=AgentErrorStage.unknown,
            recoverable=False,
            message="未知错误",
        )

        resp = AgentResponse.error(
            error_msg="未知错误",
            agent_error=agent_error,
        )

        # 验证：使用默认兜底文案
        self.assertTrue(len(resp.reply_text) > 0)
        self.assertIn("没有稳定生成回复", resp.reply_text)

        # 验证：meta 包含错误信息
        self.assertEqual(resp.meta.get("error_code"), "unknown")

    def test_error_response_meta_fields_complete(self) -> None:
        """AgentResponse.error() 的 meta 字段包含完整的结构化错误信息。"""
        from app.agent_schemas import AgentResponse, AgentError, AgentErrorCode, AgentErrorStage

        # 使用 make_agent_error 构建错误
        from app.agent_schemas import make_agent_error

        agent_error = make_agent_error(
            code=AgentErrorCode.route_missing_symbols,
            message="路由未识别有效标的",
            context={"user_text": "看下走势"},
        )

        resp = AgentResponse.error(
            error_msg="路由未识别有效标的",
            fallback_text="请补充标的名称。",
            agent_error=agent_error,
        )

        # 验证：meta 字段完整
        meta = resp.meta
        self.assertIn("error_code", meta)
        self.assertIn("error_stage", meta)
        self.assertIn("recoverable", meta)
        self.assertIn("termination_reason", meta)
        self.assertIn("error_message", meta)
        self.assertIn("error_context", meta)

        # 验证：字段值正确
        self.assertEqual(meta["error_code"], "route_missing_symbols")
        self.assertEqual(meta["error_stage"], "route")
        self.assertEqual(meta["recoverable"], True)
        self.assertEqual(meta["termination_reason"], "路由未识别有效标的")
        self.assertEqual(meta["error_context"]["user_text"], "看下走势")


if __name__ == "__main__":
    unittest.main()