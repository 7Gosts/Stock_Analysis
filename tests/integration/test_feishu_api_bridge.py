from __future__ import annotations

import unittest
from unittest.mock import patch

from app.analysis_task_client import poll_analysis_result, submit_analysis_task


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class TestFeishuApiBridge(unittest.TestCase):
    def test_submit_and_poll(self) -> None:
        with (
            patch("app.analysis_task_client.requests.post", return_value=_Resp({"task_id": "t1", "status": "queued"})),
            patch(
                "app.analysis_task_client.requests.get",
                side_effect=[
                    _Resp({"status": "running"}),
                    _Resp(
                        {
                            "status": "completed",
                            "result": {
                                "analysis_result": {
                                    "symbol": "BTC_USDT",
                                    "interval": "4h",
                                    "fixed_template": {
                                        "综合倾向": "偏多",
                                        "关键位(Fib)": "0.618~0.786",
                                        "触发条件": "突破 0.786",
                                        "失效条件": "跌破 0.5",
                                        "风险点": ["假突破"],
                                        "下次复核时间": "下一根4hK线收盘后",
                                    },
                                },
                                "risk_flags": ["normal"],
                                "evidence_sources": [{"source_path": "/tmp/mock", "source_type": "kline"}],
                            },
                        }
                    ),
                ],
            ),
        ):
            tid = submit_analysis_task(
                api_base_url="http://127.0.0.1:8000",
                payload={"symbol": "BTC_USDT", "interval": "4h"},
            )
            self.assertEqual(tid, "t1")
            result = poll_analysis_result(api_base_url="http://127.0.0.1:8000", task_id=tid, timeout_sec=5, poll_interval_sec=0.01)
            self.assertEqual(result["analysis_result"]["symbol"], "BTC_USDT")


if __name__ == "__main__":
    unittest.main()
