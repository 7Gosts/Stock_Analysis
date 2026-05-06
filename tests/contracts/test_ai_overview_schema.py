from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.orchestrator import run as run_orchestrator


def _fake_rows(n: int = 90) -> list[dict[str, float | str]]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, float | str]] = []
    base = 10.0
    for i in range(n):
        t = now - timedelta(days=n - i)
        c = base + i * 0.05
        rows.append(
            {
                "time": t.isoformat(),
                "open": c - 0.02,
                "high": c + 0.05,
                "low": c - 0.06,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
    return rows


class TestAiOverviewSchema(unittest.TestCase):
    def test_ai_overview_contains_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cfg = td_path / "market_config.json"
            cfg.write_text(
                json.dumps(
                    {
                        "default_symbols": ["TEST.SZ"],
                        "assets": [
                            {
                                "symbol": "TEST.SZ",
                                "name": "测试标的",
                                "market": "CN",
                                "data_symbol": "TEST.SZ",
                                "tags": ["测试"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            out_dir = td_path / "output"
            args = Namespace(
                provider="tickflow",
                config=str(cfg),
                market_brief=False,
                symbol="TEST.SZ",
                interval="1d",
                limit=90,
                out_dir=str(out_dir),
                report_only=True,
                with_research=False,
                research_n=3,
                research_type="title",
                research_keyword=None,
                mtf_interval="auto",
                no_mtf=False,
                analysis_style="auto",
            )
            with patch("app.orchestrator.fetch_ohlcv", return_value=_fake_rows(90)):
                code = run_orchestrator(args)
            self.assertEqual(code, 0)

            day = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
            p = out_dir / "tickflow" / "CN" / day / "ai_overview.json"
            self.assertTrue(p.is_file())
            obj = json.loads(p.read_text(encoding="utf-8"))
            self.assertIn("generated_at_utc", obj)
            self.assertIn("items", obj)
            self.assertIsInstance(obj["items"], list)
            self.assertGreaterEqual(len(obj["items"]), 1)
            it = obj["items"][0]
            for k in ("symbol", "provider", "interval", "stats"):
                self.assertIn(k, it)
            stats = it["stats"]
            for k in ("last", "trend", "fib_levels", "wyckoff_123_v1", "structure_filters_v1", "time_stop_v1"):
                self.assertIn(k, stats)


if __name__ == "__main__":
    unittest.main()

