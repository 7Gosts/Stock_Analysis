from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.rag_index import RagIndex


class TestRagRetrieval(unittest.TestCase):
    def test_query_hits_overview_and_research(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "output"
            overview_dir = root / "gateio" / "CRYPTO" / "2026-05-07"
            overview_dir.mkdir(parents=True, exist_ok=True)
            overview_payload = {
                "items": [
                    {
                        "symbol": "ETH_USDT",
                        "interval": "4h",
                        "provider": "gateio",
                        "stats": {
                            "trend": "震荡偏多",
                            "last": 2320.68,
                            "price_vs_fib_zone": "0.382~0.5",
                            "market_regime": {"label": "过渡震荡"},
                        },
                    }
                ]
            }
            (overview_dir / "ai_overview.json").write_text(
                json.dumps(overview_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            research_dir = root / "research" / "gateio" / "CRYPTO" / "2026-05-07"
            research_dir.mkdir(parents=True, exist_ok=True)
            (research_dir / "eth_research.md").write_text(
                "以太坊链上活跃度回升，机构观点偏中性。",
                encoding="utf-8",
            )

            idx = RagIndex.from_output_root(root)
            hits = idx.query("ETH 当前趋势和研报观点", top_k=5, min_score=0.0)
            self.assertGreaterEqual(len(hits), 1)
            self.assertTrue(any("ai_overview.json" in str(h.get("source_path")) for h in hits))

    def test_query_hits_memory_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "output"
            root.mkdir(parents=True, exist_ok=True)
            mem = Path(td) / "feishu_memory.jsonl"
            mem.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "open_id": "ou_1",
                                "role": "assistant",
                                "text": "ETH_USDT 4h 关注2330附近支撑",
                                "symbol": "ETH_USDT",
                                "interval": "4h",
                                "question": "看下ETH",
                                "created_ts": 1715078400,
                            },
                            ensure_ascii=False,
                        )
                    ]
                ),
                encoding="utf-8",
            )
            idx = RagIndex.from_output_root(root, memory_paths=[mem])
            hits = idx.query("eth 4h 支撑", top_k=3, min_score=0.0)
            self.assertGreaterEqual(len(hits), 1)
            self.assertTrue(any(str(h.get("source_type")) == "memory" for h in hits))


if __name__ == "__main__":
    unittest.main()
