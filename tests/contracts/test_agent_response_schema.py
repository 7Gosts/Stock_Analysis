from __future__ import annotations

import unittest

from app.guardrails import validate_agent_response
from app.agent_schemas import (
    AgentResponse,
    AgentError,
    AgentErrorCode,
    AgentErrorStage,
)


class TestAgentResponseSchema(unittest.TestCase):
    """AgentResponse Schema 测试。"""
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

    # ========== 新增结构化错误预留测试 ==========

    def test_structured_error_schema_accepts_error_code(self) -> None:
        """为即将落地的错误模型预留测试结构。

        验证：
        - error_code 字段可以存在于 meta 中
        - error_code 是字符串类型
        """
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
            "meta": {
                "error_code": "route_missing_symbols",
            },
        }
        # 验证 meta.error_code 可以被接受
        self.assertIn("error_code", payload.get("meta", {}))
        self.assertIsInstance(payload["meta"]["error_code"], str)

    def test_structured_error_schema_accepts_error_stage(self) -> None:
        """验证 error_stage 字段可以存在于 meta 中。

        验证：
        - error_stage 字段可以存在于 meta 中
        - error_stage 是字符串类型 (route / execute / infra / unknown)
        """
        payload = {
            "analysis_result": {
                "symbol": "BTC_USDT",
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
            "meta": {
                "error_code": "followup_missing_symbol",
                "error_stage": "route",
            },
        }
        self.assertIn("error_stage", payload.get("meta", {}))
        self.assertIn(payload["meta"]["error_stage"], ["route", "execute", "infra", "unknown"])

    def test_structured_error_schema_accepts_recoverable(self) -> None:
        """验证 recoverable 字段可以存在于 meta 中。

        验证：
        - recoverable 字段可以存在于 meta 中
        - recoverable 是布尔类型
        """
        payload = {
            "analysis_result": {
                "symbol": "BTC_USDT",
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
            "meta": {
                "error_code": "db_unavailable",
                "error_stage": "infra",
                "recoverable": True,
            },
        }
        self.assertIn("recoverable", payload.get("meta", {}))
        self.assertIsInstance(payload["meta"]["recoverable"], bool)

    def test_structured_error_schema_accepts_termination_reason(self) -> None:
        """验证 termination_reason 字段可以存在于 meta 中。

        验证：
        - termination_reason 字段可以存在于 meta 中
        - termination_reason 是字符串类型
        """
        payload = {
            "analysis_result": {
                "symbol": "BTC_USDT",
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
            "meta": {
                "error_code": "route_missing_symbols",
                "error_stage": "route",
                "recoverable": True,
                "termination_reason": "llm_output_invalid",
            },
        }
        self.assertIn("termination_reason", payload.get("meta", {}))
        self.assertIsInstance(payload["meta"]["termination_reason"], str)

    def test_success_response_accepts_termination_reason_success(self) -> None:
        """验证成功响应可以包含 termination_reason='success'。

        验证：
        - 成功时 termination_reason 可以是 "success"
        """
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
            "meta": {
                "termination_reason": "success",
            },
        }
        self.assertIn("termination_reason", payload.get("meta", {}))
        self.assertEqual(payload["meta"]["termination_reason"], "success")

    # ========== AgentResponse.error() 实际行为测试 ==========

    def test_agent_response_error_includes_structured_error_meta(self) -> None:
        """AgentResponse.error() 包含结构化错误元信息。

        验证：
        - meta 中包含 error 字段（向后兼容）
        - 当传入 agent_error 时，meta 包含 error_code
        """
        agent_err = AgentError(
            code=AgentErrorCode.route_missing_symbols,
            stage=AgentErrorStage.route,
            recoverable=True,
            message="analyze route missing valid symbols",
            termination_reason="llm_output_invalid",
        )
        resp = AgentResponse.error(
            error_msg="analyze route missing valid symbols",
            fallback_text="无法识别有效标的，请补充标的名称。",
            agent_error=agent_err,
        )

        # 验证基本响应结构
        self.assertEqual(resp.task_type, "chat")
        self.assertEqual(resp.response_mode, "quick")
        self.assertIn("error", resp.meta)  # 向后兼容

        # 验证结构化错误元信息
        self.assertIn("error_code", resp.meta)
        self.assertEqual(resp.meta["error_code"], "route_missing_symbols")
        self.assertIn("error_stage", resp.meta)
        self.assertEqual(resp.meta["error_stage"], "route")

    def test_agent_response_error_with_recoverable_flag(self) -> None:
        """AgentResponse.error() 包含 recoverable 标志。

        验证：
        - recoverable 字段存在
        - recoverable 是布尔值
        """
        agent_err = AgentError(
            code=AgentErrorCode.db_unavailable,
            stage=AgentErrorStage.infra,
            recoverable=True,
            message="数据库暂时不可用",
            termination_reason="infra_transient_failure",
        )
        resp = AgentResponse.error(
            error_msg="数据库暂时不可用",
            fallback_text="服务暂时不可用，请稍后重试。",
            agent_error=agent_err,
        )

        self.assertIn("recoverable", resp.meta)
        self.assertTrue(resp.meta["recoverable"])

    def test_agent_response_error_with_non_recoverable_flag(self) -> None:
        """AgentResponse.error() 包含 non-recoverable 标志。

        验证：
        - recoverable=False 的场景
        """
        agent_err = AgentError(
            code=AgentErrorCode.route_unknown_action,
            stage=AgentErrorStage.route,
            recoverable=False,
            message="未知路由 action",
            termination_reason="invalid_route_output",
        )
        resp = AgentResponse.error(
            error_msg="未知路由 action",
            fallback_text="请求类型无法识别。",
            agent_error=agent_err,
        )

        self.assertIn("recoverable", resp.meta)
        self.assertFalse(resp.meta["recoverable"])

    def test_agent_response_error_backward_compatible_without_agent_error(self) -> None:
        """AgentResponse.error() 不传 agent_error 时向后兼容。

        验证：
        - 仍然可以正常创建
        - meta 包含 error 字段
        """
        resp = AgentResponse.error(
            error_msg="some generic error",
            fallback_text="发生错误，请稍后重试。",
        )

        self.assertEqual(resp.task_type, "chat")
        self.assertIn("error", resp.meta)
        self.assertEqual(resp.meta["error"], "some generic error")

    def test_agent_response_error_includes_termination_reason(self) -> None:
        """AgentResponse.error() 包含 termination_reason。

        验证：
        - termination_reason 字段存在
        - termination_reason 是字符串
        """
        agent_err = AgentError(
            code=AgentErrorCode.analysis_backend_unavailable,
            stage=AgentErrorStage.infra,
            recoverable=True,
            message="分析后端不可用",
            termination_reason="backend_timeout",
        )
        resp = AgentResponse.error(
            error_msg="分析后端不可用",
            fallback_text="分析服务暂时不可用。",
            agent_error=agent_err,
        )

        self.assertIn("termination_reason", resp.meta)
        self.assertIsInstance(resp.meta["termination_reason"], str)

    def test_agent_response_error_has_user_visible_text(self) -> None:
        """AgentResponse.error() 必须包含用户可见文本。

        验证：
        - reply_text 存在且非空
        - reply_text 不包含技术性 traceback
        """
        agent_err = AgentError(
            code=AgentErrorCode.execute_analysis_failed,
            stage=AgentErrorStage.execute,
            recoverable=True,
            message="Traceback (most recent call last): ...",
        )
        resp = AgentResponse.error(
            error_msg="Traceback...",
            fallback_text="分析执行失败，请稍后重试。",
            agent_error=agent_err,
        )

        # reply_text 是用户可见的兜底文案
        self.assertTrue(len(resp.reply_text) > 0)
        # reply_text 不应包含 traceback
        self.assertNotIn("Traceback", resp.reply_text)
        self.assertNotIn("most recent call last", resp.reply_text)


if __name__ == "__main__":
    unittest.main()
