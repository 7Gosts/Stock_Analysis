"""feishu_adapter.get_recent_messages 截断行为。"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

from app.feishu_adapter import get_recent_messages


class TestFeishuRecentMessages(unittest.TestCase):
    def test_truncates_when_over_keep_pairs(self) -> None:
        store = MagicMock()
        rows = [{"role": "user", "text": str(i)} for i in range(40)]
        store.load_recent.return_value = rows
        with unittest.mock.patch.dict(os.environ, {"AGENT_RECENT_MESSAGE_KEEP_PAIRS": "4"}):
            out = get_recent_messages("u1", rounds=50, memory_store=store)
        self.assertLessEqual(len(out), 8)

    def test_no_truncation_when_short(self) -> None:
        store = MagicMock()
        store.load_recent.return_value = [{"role": "user", "text": "a"}]
        out = get_recent_messages("u1", rounds=10, memory_store=store)
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
