"""agent_graph 与 compact 行为单测（不调用真实 LLM）。"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import app.agent_graph as ag
from app.agent_graph import compact_node, unified_chat_agent_enabled
from app.agent_schemas import AgentRequest
from app.session_state import SessionState, SessionStateStore


class TestCompactNode(unittest.TestCase):
    def test_compact_skips_below_threshold(self) -> None:
        store = SessionStateStore(persist_path=None)
        st = SessionState(open_id="u1")
        store.update(st)
        req = AgentRequest(channel="feishu", session_id="u1", text="hi", context={"recent_messages": [{"role": "user", "text": "a"}]})
        token = ag._CTX.set({"request": req, "session_store": store, "session_state": st, "rag_index": None})
        try:
            out = compact_node({})
        finally:
            ag._CTX.reset(token)
        self.assertEqual(out, {})

    def test_compact_appends_when_many_recent(self) -> None:
        store = SessionStateStore(persist_path=None)
        st = SessionState(open_id="u1", history_version=3)
        store.update(st)
        many = [{"role": "user", "text": str(i)} for i in range(30)]
        req = AgentRequest(channel="feishu", session_id="u1", text="x", context={"recent_messages": many})
        token = ag._CTX.set({"request": req, "session_store": store, "session_state": store.get("u1"), "rag_index": None})
        try:
            compact_node({})
        finally:
            ag._CTX.reset(token)
        st2 = store.get("u1")
        self.assertIsNotNone(st2.compacted_summary)
        self.assertIn("auto-compact", st2.compacted_summary or "")


class TestUnifiedFlag(unittest.TestCase):
    def test_unified_respects_env(self) -> None:
        with patch.dict(os.environ, {"AGENT_UNIFIED_GRAPH": "1"}):
            self.assertTrue(unified_chat_agent_enabled())
        with patch.dict(os.environ, {"AGENT_UNIFIED_GRAPH": "0"}):
            self.assertFalse(unified_chat_agent_enabled())


if __name__ == "__main__":
    unittest.main()
