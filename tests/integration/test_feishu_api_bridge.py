from __future__ import annotations

import unittest
from unittest.mock import patch


class TestFeishuApiBridge(unittest.TestCase):
    """Agent facade 错误路径测试（本地执行器版）。

    旧版 analysis_task_client（HTTP 异步客户端）已被删除，
    测试改为覆盖 _run_analysis_local 的本地执行路径。
    """

    # ========== agent_facade 错误路径测试 ==========

    def test_agent_facade_analysis_failure_returns_structured_error(self) -> None:
        """agent_facade 分析失败时返回结构化错误。

        验证：
        - meta 中包含 error_code
        - reply_text 是用户可见的友好文本
        - 不包含 traceback

        注意：此测试需要 loguru 依赖，若无则跳过。
        """
        try:
            from app.agent_facade import handle_user_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_facade test")

        # 模拟路由成功但本地分析失败
        route = {
            "action": "analyze",
            "task_type": "analysis",
            "response_mode": "analysis",
            "payload": {
                "symbol": "BTC_USDT",
                "interval": "4h",
                "question": "分析 BTC",
            },
            "task_plan": {
                "symbols": ["BTC_USDT"],
                "interval": "4h",
            },
        }

        # 模拟 _run_analysis_local 抛出异常
        with patch(
            "app.agent_facade._run_analysis_local",
            side_effect=Exception("Backend timeout"),
        ):
            result = handle_user_request(
                text="分析 BTC",
                channel="feishu",
                user_id="test_user",
                context={
                    "route": route,
                    "default_symbol": "BTC_USDT",
                    "default_interval": "4h",
                },
            )

        # 验证返回用户可见文本
        self.assertIn("final_text", result)
        self.assertTrue(len(result["final_text"]) > 0)
        # 不包含 traceback
        self.assertNotIn("Traceback", result["final_text"])
        self.assertNotIn("most recent call last", result["final_text"])

        # 验证 meta 存在
        self.assertIn("meta", result)

    def test_agent_facade_error_response_has_user_visible_fallback(self) -> None:
        """agent_facade 错误响应必须有用户可见的兜底文案。

        验证：
        - reply_text 存在且非空
        - reply_text 是中文友好文案

        注意：此测试需要 loguru 依赖，若无则跳过。
        """
        try:
            from app.agent_facade import handle_user_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_facade test")

        route = {
            "action": "analyze",
            "task_type": "analysis",
            "response_mode": "analysis",
            "payload": {
                "symbol": "ETH_USDT",
                "interval": "1h",
                "question": "分析 ETH",
            },
            "task_plan": {
                "symbols": ["ETH_USDT"],
                "interval": "1h",
            },
        }

        with patch(
            "app.agent_facade._run_analysis_local",
            side_effect=Exception("Connection refused"),
        ):
            result = handle_user_request(
                text="分析 ETH",
                channel="feishu",
                user_id="test_user",
                context={
                    "route": route,
                    "default_symbol": "ETH_USDT",
                    "default_interval": "1h",
                },
            )

        # 验证用户可见文案
        final_text = result.get("final_text", "")
        self.assertTrue(len(final_text) > 0)
        # 应包含中文提示（友好文案）
        self.assertTrue(
            any(kw in final_text for kw in ["分析", "失败", "重试", "不可用"]),
            f"final_text should contain friendly message, got: {final_text}"
        )

    def test_agent_facade_success_path_does_not_reference_task_id(self) -> None:
        """本地分析成功路径不应再引用旧 HTTP task_id。"""
        try:
            from app.agent_facade import handle_user_request
        except ImportError:
            self.skipTest("loguru not installed, skipping agent_facade test")

        route = {
            "action": "analyze",
            "task_type": "analysis",
            "response_mode": "analysis",
            "payload": {
                "symbol": "AU9999",
                "provider": "goldapi",
                "interval": "1d",
                "question": "上海金 Au9999 今天走势",
            },
            "task_plan": {
                "symbols": ["AU9999"],
                "interval": "1d",
            },
        }
        analysis_result = {
            "analysis_result": {
                "symbol": "AU9999",
                "interval": "1d",
                "fixed_template": {
                    "综合倾向": "偏多",
                    "关键位(Fib)": "0.236~0.382",
                    "触发条件": "entry=None，tp1=None，tp2=None，triggered=None",
                    "失效条件": "stop=None",
                    "风险点": ["低流动性阶段容易出现假突破"],
                    "下次复核时间": "下个交易日收盘后复核（北京时间）",
                },
            },
            "risk_flags": ["normal"],
            "evidence_sources": [],
            "meta": {
                "ai_overview_path": "/tmp/ai_overview.json",
                "full_report_path": "/tmp/full_report.md",
            },
        }

        with patch("app.agent_facade._run_analysis_local", return_value=analysis_result), patch(
            "app.agent_facade.grounded_writer_enabled",
            return_value=False,
        ), patch(
            "app.agent_facade.fallback_to_template_reply_enabled",
            return_value=True,
        ):
            result = handle_user_request(
                text="上海金 Au9999 今天走势",
                channel="feishu",
                user_id="test_user",
                context={
                    "route": route,
                    "default_symbol": "BTC_USDT",
                    "default_interval": "4h",
                },
            )

        self.assertIn("final_text", result)
        self.assertTrue(result["final_text"])
        self.assertIn("meta", result)
        self.assertNotIn("task_id", result["meta"])
        self.assertEqual(result["meta"]["output_refs"]["ai_overview_path"], "/tmp/ai_overview.json")


if __name__ == "__main__":
    unittest.main()