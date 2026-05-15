"""测试结构化错误模型。"""
from __future__ import annotations

import unittest

from app.agent_schemas import (
    AgentErrorCode,
    AgentErrorStage,
    AgentError,
    AgentResponse,
)
from app.planner import AgentRoutingError


class TestAgentErrorModel(unittest.TestCase):
    """测试 AgentError 和相关结构化错误类。"""

    def test_agent_error_code_enum_values(self) -> None:
        """验证错误码枚举值正确。"""
        self.assertEqual(AgentErrorCode.route_missing_symbols.value, "route_missing_symbols")
        self.assertEqual(AgentErrorCode.route_invalid_symbol.value, "route_invalid_symbol")
        self.assertEqual(AgentErrorCode.route_missing_chat_reply.value, "route_missing_chat_reply")
        self.assertEqual(AgentErrorCode.followup_missing_symbol.value, "followup_missing_symbol")
        self.assertEqual(AgentErrorCode.db_unavailable.value, "db_unavailable")
        self.assertEqual(AgentErrorCode.analysis_backend_unavailable.value, "analysis_backend_unavailable")
        self.assertEqual(AgentErrorCode.unknown.value, "unknown")

    def test_agent_error_stage_enum_values(self) -> None:
        """验证错误阶段枚举值正确。"""
        self.assertEqual(AgentErrorStage.route.value, "route")
        self.assertEqual(AgentErrorStage.execute.value, "execute")
        self.assertEqual(AgentErrorStage.infra.value, "infra")
        self.assertEqual(AgentErrorStage.unknown.value, "unknown")

    def test_agent_error_to_meta_dict(self) -> None:
        """验证 AgentError.to_meta_dict() 返回正确结构。"""
        err = AgentError(
            code=AgentErrorCode.route_missing_symbols,
            stage=AgentErrorStage.route,
            recoverable=True,
            message="analyze route missing valid symbols",
            termination_reason="llm_output_invalid",
            context={"action": "analyze"},
        )
        meta = err.to_meta_dict()
        self.assertEqual(meta["error_code"], "route_missing_symbols")
        self.assertEqual(meta["error_stage"], "route")
        self.assertEqual(meta["recoverable"], True)
        self.assertEqual(meta["termination_reason"], "llm_output_invalid")
        self.assertEqual(meta["error_message"], "analyze route missing valid symbols")
        self.assertEqual(meta["error_context"], {"action": "analyze"})

    def test_agent_response_error_with_agent_error(self) -> None:
        """验证 AgentResponse.error() 可以接收 AgentError。"""
        agent_err = AgentError(
            code=AgentErrorCode.route_missing_chat_reply,
            stage=AgentErrorStage.route,
            recoverable=True,
            message="chat route missing chat_reply",
            termination_reason="llm_output_invalid",
        )
        resp = AgentResponse.error(
            error_msg="chat route missing chat_reply",
            fallback_text="我这次没有稳定生成回复。你可以补一句标的/周期，或让我重新分析。",
            agent_error=agent_err,
        )
        self.assertEqual(resp.task_type, "chat")
        self.assertEqual(resp.response_mode, "quick")
        self.assertIn("error_code", resp.meta)
        self.assertEqual(resp.meta["error_code"], "route_missing_chat_reply")
        self.assertEqual(resp.meta["error_stage"], "route")
        self.assertEqual(resp.meta["recoverable"], True)

    def test_agent_response_error_without_agent_error(self) -> None:
        """验证 AgentResponse.error() 不传 agent_error 时向后兼容。"""
        resp = AgentResponse.error(
            error_msg="some error",
            fallback_text="fallback text",
        )
        self.assertEqual(resp.task_type, "chat")
        self.assertEqual(resp.response_mode, "quick")
        self.assertIn("error", resp.meta)
        self.assertEqual(resp.meta["error"], "some error")

    def test_agent_routing_error_creation(self) -> None:
        """验证 AgentRoutingError 异常类正确创建。"""
        exc = AgentRoutingError(
            "analyze route missing valid symbols",
            code=AgentErrorCode.route_missing_symbols,
            recoverable=True,
            termination_reason="llm_output_invalid",
            context={"action": "analyze"},
        )
        self.assertEqual(str(exc), "analyze route missing valid symbols")
        self.assertEqual(exc.code, AgentErrorCode.route_missing_symbols)
        self.assertEqual(exc.stage, AgentErrorStage.route)
        self.assertEqual(exc.recoverable, True)
        self.assertEqual(exc.termination_reason, "llm_output_invalid")
        self.assertEqual(exc.context, {"action": "analyze"})

    def test_agent_routing_error_to_agent_error(self) -> None:
        """验证 AgentRoutingError.to_agent_error() 正确转换。"""
        exc = AgentRoutingError(
            "route_empty_message",
            code=AgentErrorCode.route_empty_message,
            recoverable=False,
            termination_reason="user_input_empty",
        )
        agent_err = exc.to_agent_error()
        self.assertIsInstance(agent_err, AgentError)
        self.assertEqual(agent_err.code, AgentErrorCode.route_empty_message)
        self.assertEqual(agent_err.stage, AgentErrorStage.route)
        self.assertEqual(agent_err.recoverable, False)
        self.assertEqual(agent_err.message, "route_empty_message")

    def test_agent_error_frozen(self) -> None:
        """验证 AgentError 是不可变的 (frozen=True)。"""
        err = AgentError(
            code=AgentErrorCode.unknown,
            stage=AgentErrorStage.unknown,
            recoverable=False,
            message="test",
        )
        with self.assertRaises(AttributeError):
            err.message = "modified"


if __name__ == "__main__":
    unittest.main()