"""quote 路径共用：单 / 多标的快照 + merge_facts_bundle。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.executors.facts_bundle import merge_facts_bundle
from app.executors.quote_snapshot import run_quote_snapshots


def run_quote_facts_bundle(
    *,
    repo_root: Path,
    user_question: str,
    payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """执行 ``run_quote_snapshots`` 并合并为 quote 用 facts_bundle。"""
    clean = [p for p in payloads if isinstance(p, dict)]
    if not clean:
        raise ValueError("quote payloads must be non-empty")
    qf = run_quote_snapshots(repo_root=repo_root, payloads=clean)
    symbols: list[str] = []
    for p in clean:
        s = str(p.get("symbol") or "").strip().upper()
        if s and s not in symbols:
            symbols.append(s)
    if not symbols:
        symbols = [""]
    return merge_facts_bundle(
        task_type="quote",
        response_mode="quick",
        user_question=user_question,
        symbols=symbols,
        market_facts=qf,
        evidence_sources=list(qf.get("evidence_sources") or []),
        risk_flags=list(qf.get("risk_flags") or []),
        trace={"executors": ["quote_snapshot"], "n_payloads": len(clean)},
    )
