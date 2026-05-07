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
        self.assertIn("BTC_USDT 4h 分析结果", text)
        self.assertIn("综合倾向：偏多", text)
        self.assertIn("风险点：假突破；高波动", text)
        self.assertNotIn("你的问题", text)


if __name__ == "__main__":
    unittest.main()
