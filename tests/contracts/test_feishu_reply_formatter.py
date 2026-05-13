"""飞书回复格式化测试（三层重构版）。

测试 agent_facade.py 中的本地格式化函数。
"""
from __future__ import annotations

import unittest

from app.agent_facade import (
    _format_fixed_template_reply_local,
    _format_wyckoff_123_reply_lines,
    _ma_system_block_lines,
    _fmt_ma_px,
)


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
        text = _format_fixed_template_reply_local(result)
        self.assertIn("━━ BTC_USDT 4h ━━", text)
        self.assertIn("【结论】", text)
        self.assertIn("综合倾向：偏多", text)
        self.assertIn("风险点：假突破；高波动", text)

    def test_format_shows_wyckoff_123_when_present(self) -> None:
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
        text = _format_fixed_template_reply_local(result)
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
        text = _format_fixed_template_reply_local(result)
        self.assertIn("【均线系统】", text)
        self.assertIn("SMA20=", text)

    def test_fmt_ma_px_large_value(self) -> None:
        self.assertEqual(_fmt_ma_px(10000), "10,000.00")
        self.assertEqual(_fmt_ma_px(1000), "1,000.00")

    def test_fmt_ma_px_medium_value(self) -> None:
        self.assertEqual(_fmt_ma_px(100.5), "100.50")
        self.assertEqual(_fmt_ma_px(1.5), "1.50")

    def test_fmt_ma_px_small_value(self) -> None:
        self.assertEqual(_fmt_ma_px(0.1234), "0.1234")

    def test_fmt_ma_px_invalid(self) -> None:
        self.assertEqual(_fmt_ma_px(None), "—")
        self.assertEqual(_fmt_ma_px("invalid"), "—")

    def test_wyckoff_123_reply_lines_with_setup(self) -> None:
        wy = {
            "background": {"bias": "long_only", "state": "accumulation"},
            "selected_setup": {
                "side": "long",
                "entry": 100.0,
                "stop": 95.0,
                "tp1": 110.0,
                "tp2": 120.0,
                "triggered": False,
            },
        }
        lines = _format_wyckoff_123_reply_lines(wy)
        self.assertTrue(lines)
        self.assertIn("偏多", lines[0])
        self.assertIn("待触发", lines[1])

    def test_wyckoff_123_reply_lines_no_setup(self) -> None:
        wy = {
            "background": {"bias": "neutral"},
            "selected_setup": None,
        }
        lines = _format_wyckoff_123_reply_lines(wy)
        self.assertTrue(lines)
        self.assertIn("中性", lines[0])
        self.assertIn("未选出", lines[1])

    def test_ma_system_block_lines_empty(self) -> None:
        lines = _ma_system_block_lines({})
        self.assertEqual(lines, [])

    def test_ma_system_block_lines_with_data(self) -> None:
        ms = {"sma20": 100.0, "sma60": 95.0}
        lines = _ma_system_block_lines(ms)
        self.assertTrue(lines)
        self.assertIn("【均线系统】", lines[0])


if __name__ == "__main__":
    unittest.main()