from __future__ import annotations

import unittest

from app.feishu_bot_service import format_fixed_template_reply


class TestFeishuReplyFormatter(unittest.TestCase):
    def test_format_fixed_template(self) -> None:
        result = {
            "analysis_result": {
                "symbol": "BTC_USDT",
                "interval": "4h",
                "fixed_template": {
                    "综合倾向": "偏多",
                    "关键位(Fib)": "0.618~0.786",
                    "触发条件": "突破 0.786",
                    "失效条件": "跌破 0.5",
                    "风险点": ["假突破", "高波动"],
                    "下次复核时间": "下一根4hK线收盘后",
                },
            }
        }
        text = format_fixed_template_reply(result)
        self.assertIn("━━ BTC_USDT 4h ━━", text)
        self.assertIn("【结论】", text)
        self.assertIn("综合倾向：偏多", text)
        self.assertIn("风险点：假突破；高波动", text)
        self.assertNotIn("你的问题", text)

    def test_format_shows_decision_source_and_meta_note(self) -> None:
        result = {
            "analysis_result": {
                "symbol": "X",
                "interval": "1d",
                "decision_source": "rules",
                "fixed_template": {
                    "综合倾向": "多",
                    "关键位(Fib)": "x",
                    "触发条件": "a",
                    "失效条件": "b",
                    "风险点": ["r"],
                    "下次复核时间": "t",
                },
            },
            "meta": {"llm_warning": "rate limited"},
        }
        text = format_fixed_template_reply(result)
        self.assertIn("【决策】", text)
        self.assertIn("来源：rules", text)
        self.assertIn("【执行旁注】", text)

    def test_format_includes_wyckoff_123_when_present(self) -> None:
        result = {
            "analysis_result": {
                "symbol": "ETH_USDT",
                "interval": "4h",
                "fixed_template": {
                    "综合倾向": "震荡",
                    "关键位(Fib)": "0.5",
                    "触发条件": "x",
                    "失效条件": "y",
                    "风险点": ["r"],
                    "下次复核时间": "下一根4h",
                },
                "wyckoff_123_v1": {
                    "background": {"bias": "neutral", "effort_result": "balanced", "state": "a|b"},
                    "preferred_side": None,
                    "aligned": False,
                    "selected_setup": None,
                },
            }
        }
        text = format_fixed_template_reply(result)
        self.assertIn("【威科夫 123】", text)
        self.assertIn("威科夫背景", text)
        self.assertIn("威科夫123", text)

    def test_format_includes_ma_snapshot_line(self) -> None:
        result = {
            "analysis_result": {
                "symbol": "AU9999",
                "interval": "1d",
                "fixed_template": {
                    "综合倾向": "多",
                    "关键位(Fib)": "x",
                    "触发条件": "a",
                    "失效条件": "b",
                    "风险点": ["r"],
                    "下次复核时间": "t",
                },
                "ma_snapshot": {
                    "sma20": 1032.7,
                    "ma_short_period": 13,
                    "sma_short": 1023.4,
                    "p_ma_short_pct": 1.55,
                },
            }
        }
        text = format_fixed_template_reply(result)
        self.assertIn("【均线系统】", text)
        self.assertIn("SMA20=", text)
        self.assertIn("SMA13=", text)

    def test_format_includes_journal_new_entries(self) -> None:
        result = {
            "analysis_result": {
                "symbol": "BTC_USDT",
                "interval": "4h",
                "fixed_template": {
                    "综合倾向": "偏多",
                    "关键位(Fib)": "x",
                    "触发条件": "x",
                    "失效条件": "y",
                    "风险点": ["r"],
                    "下次复核时间": "t",
                },
            },
            "meta": {
                "journal": {
                    "created": 1,
                    "updated": 0,
                    "path": "/tmp/j.jsonl",
                    "new_entries": [
                        {
                            "idea_id": "abc",
                            "symbol": "BTC_USDT",
                            "interval": "4h",
                            "plan_type": "tactical",
                            "direction": "long",
                            "status": "watch",
                            "entry_price": 100.0,
                            "entry_zone": [99.0, 101.0],
                            "stop_loss": 98.0,
                            "take_profit_levels": [102.0, 103.0],
                            "rr": 1.5,
                            "order_kind_cn": "挂单",
                        }
                    ],
                }
            },
        }
        text = format_fixed_template_reply(result)
        self.assertIn("【台账】", text)
        self.assertIn("本轮新增候选", text)
        self.assertIn("idea_id=abc", text)


if __name__ == "__main__":
    unittest.main()
