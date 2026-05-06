from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.orchestrator import run as run_orchestrator


def _rows(n: int = 120) -> list[dict[str, float | str]]:
    now = datetime.now(timezone.utc)
    out: list[dict[str, float | str]] = []
    p = 20.0
    for i in range(n):
        t = now - timedelta(days=n - i)
        c = p + i * 0.08
        out.append(
            {
                "time": t.isoformat(),
                "open": c - 0.03,
                "high": c + 0.08,
                "low": c - 0.10,
                "close": c,
                "volume": 1200 + i,
            }
        )
    return out


class TestCliGoldenOutputs(unittest.TestCase):
    def test_cli_golden_core_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = root / "market_config.json"
            cfg.write_text(
                json.dumps(
                    {
                        "default_symbols": ["TEST_USDT"],
                        "assets": [
                            {
                                "symbol": "TEST_USDT",
                                "name": "TestCoin",
                                "market": "CRYPTO",
                                "data_symbol": "TEST_USDT",
                                "tags": ["测试币"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            args = Namespace(
                provider="gateio",
                config=str(cfg),
                market_brief=True,
                symbol=None,
                interval="1d",
                limit=120,
                out_dir=str(root / "output"),
                report_only=True,
                with_research=False,
                research_n=3,
                research_type="title",
                research_keyword=None,
                mtf_interval="auto",
                no_mtf=False,
                analysis_style="auto",
            )
            with patch("app.orchestrator.fetch_ohlcv", return_value=_rows(120)):
                code = run_orchestrator(args)
            self.assertEqual(code, 0)

            day = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
            overview = root / "output" / "gateio" / "CRYPTO" / day / "ai_overview.json"
            self.assertTrue(overview.is_file())
            obj = json.loads(overview.read_text(encoding="utf-8"))
            got = {
                "provider": obj.get("provider"),
                "interval": obj.get("interval"),
                "items_len": len(obj.get("items") or []),
                "first_symbol": (obj.get("items") or [{}])[0].get("symbol"),
                "first_interval": (obj.get("items") or [{}])[0].get("interval"),
            }
            expected = {
                "provider": "gateio",
                "interval": "1d",
                "items_len": 1,
                "first_symbol": "TEST_USDT",
                "first_interval": "1d",
            }
            self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main()

