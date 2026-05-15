"""多标的 compare：``run_multi_asset_compare`` + merge_facts_bundle。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.executors.facts_bundle import merge_facts_bundle
from app.executors.multi_asset_compare import run_multi_asset_compare


def run_compare_facts_bundle(
    *,
    repo_root: Path,
    user_question: str,
    payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    clean = [p for p in payloads if isinstance(p, dict)]
    if not clean:
        raise ValueError("compare payloads must be non-empty")
    cf = run_multi_asset_compare(repo_root=repo_root, payloads=clean)
    symbols = [str(p.get("symbol") or "").strip().upper() for p in clean if p.get("symbol")]
    return merge_facts_bundle(
        task_type="compare",
        response_mode="compare",
        user_question=user_question,
        symbols=symbols,
        market_facts={"compare_summary": {"rows": cf.get("rows")}},
        compare_facts=cf,
        evidence_sources=list(cf.get("evidence_sources") or []),
        risk_flags=list(cf.get("risk_flags") or []),
        trace={"executors": ["multi_asset_compare"]},
    )
