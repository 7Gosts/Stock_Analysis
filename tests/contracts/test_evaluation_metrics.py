from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.evaluation import evaluate_payload, load_eval_cases


class TestEvaluationMetrics(unittest.TestCase):
    def test_evaluate_payload_ok(self) -> None:
        payload = {
            "analysis_result": {
                "fixed_template": {
                    "综合倾向": "偏多",
                    "关键位(Fib)": "0.382~0.5",
                    "触发条件": "entry=1",
                    "失效条件": "stop=0.9",
                    "风险点": ["波动风险"],
                    "下次复核时间": "下一根4hK线收盘后复核",
                }
            },
            "risk_flags": ["normal"],
            "evidence_sources": [{"source_path": "/tmp/a.json", "source_type": "kline"}],
        }
        out = evaluate_payload(payload)
        self.assertTrue(out["valid"])
        self.assertTrue(out["structure_ok"])
        self.assertTrue(out["factual_ok"])
        self.assertFalse(out["hallucination_hit"])

    def test_load_eval_cases(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cases.json"
            p.write_text(
                '[{"symbol":"BTC_USDT","provider":"gateio","interval":"4h","question":"看走势","use_llm_decision":false}]',
                encoding="utf-8",
            )
            cases = load_eval_cases(p)
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].symbol, "BTC_USDT")


if __name__ == "__main__":
    unittest.main()
