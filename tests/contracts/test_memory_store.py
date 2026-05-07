from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from app.memory_store import JsonlMemoryStore, MemoryEvent


class TestJsonlMemoryStore(unittest.TestCase):
    def test_append_and_load_recent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mem.jsonl"
            store = JsonlMemoryStore(path=p)
            store.append_event(MemoryEvent(open_id="u1", role="user", text="看下BTC 4h"))
            store.append_event(MemoryEvent(open_id="u1", role="assistant", text="好的"))
            rows = store.load_recent(open_id="u1", limit=2)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["role"], "user")
            self.assertEqual(rows[1]["role"], "assistant")

    def test_load_last_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mem.jsonl"
            store = JsonlMemoryStore(path=p)
            store.append_event(
                MemoryEvent(
                    open_id="u1",
                    role="assistant",
                    text="分析结果",
                    action="analyze",
                    symbol="ETH_USDT",
                    interval="1h",
                    question="看下ETH",
                )
            )
            profile = store.load_last_profile(open_id="u1")
            self.assertEqual(profile.get("symbol"), "ETH_USDT")
            self.assertEqual(profile.get("interval"), "1h")

    def test_search_long_term(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mem.jsonl"
            store = JsonlMemoryStore(path=p)
            store.append_event(MemoryEvent(open_id="u1", role="user", text="看下SOL_USDT 15m突破"))
            store.append_event(MemoryEvent(open_id="u1", role="assistant", text="SOL突破关注回踩", symbol="SOL_USDT", interval="15m"))
            hits = store.search_long_term(open_id="u1", query="sol 15m", top_k=2)
            self.assertGreaterEqual(len(hits), 1)
            self.assertIn("SOL", str(hits[0]["text"]).upper())

    def test_compact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mem.jsonl"
            store = JsonlMemoryStore(path=p, max_messages_per_user=100, history_days=1)
            old_ts = time.time() - 3 * 86400
            store.append_event(MemoryEvent(open_id="u1", role="user", text="old", created_ts=old_ts))
            store.append_event(MemoryEvent(open_id="u1", role="user", text="new"))
            store.compact()
            rows = store.load_recent(open_id="u1", limit=10)
            texts = [str(x.get("text")) for x in rows]
            self.assertNotIn("old", texts)
            self.assertIn("new", texts)


if __name__ == "__main__":
    unittest.main()
