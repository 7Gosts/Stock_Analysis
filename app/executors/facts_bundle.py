from __future__ import annotations

from typing import Any


def merge_facts_bundle(
    *,
    task_type: str,
    response_mode: str,
    user_question: str,
    symbols: list[str],
    market_facts: dict[str, Any] | None = None,
    compare_facts: dict[str, Any] | None = None,
    research_facts: dict[str, Any] | None = None,
    memory_facts: dict[str, Any] | None = None,
    risk_flags: list[str] | None = None,
    evidence_sources: list[dict[str, Any]] | None = None,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一事实包：供 writer / guardrail 消费；trace 不直接展示给终端用户。"""
    return {
        "task_type": task_type,
        "response_mode": response_mode,
        "symbols": list(symbols),
        "user_question": (user_question or "").strip(),
        "market_facts": market_facts if isinstance(market_facts, dict) else {},
        "compare_facts": compare_facts if isinstance(compare_facts, dict) else {},
        "research_facts": research_facts if isinstance(research_facts, dict) else {},
        "memory_facts": memory_facts if isinstance(memory_facts, dict) else {},
        "risk_flags": list(risk_flags or []),
        "evidence_sources": list(evidence_sources or []),
        "trace": trace if isinstance(trace, dict) else {},
    }
