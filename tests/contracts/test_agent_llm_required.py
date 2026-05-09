from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agent_service import TaskRunner


class TestAgentLlmRequired(unittest.TestCase):
    def test_raises_when_llm_disabled(self) -> None:
        runner = TaskRunner()
        with patch("app.agent_service._llm_enabled", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                runner.run_analysis(symbol="BTC_USDT", provider="gateio", interval="1d")
        self.assertIn("DeepSeek", str(ctx.exception))

    def test_raises_when_use_llm_decision_false(self) -> None:
        runner = TaskRunner()
        with patch("app.agent_service._llm_enabled", return_value=True):
            with self.assertRaises(RuntimeError) as ctx:
                runner.run_analysis(symbol="BTC_USDT", use_llm_decision=False)
        self.assertIn("use_llm_decision", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
