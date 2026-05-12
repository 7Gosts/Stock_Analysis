from __future__ import annotations

from pathlib import Path
from typing import Any

from app.executors.market_snapshot import run_market_snapshot


def run_quote_snapshots(
    *,
    repo_root: Path,
    payloads: list[dict[str, Any]],
    limit: int = 120,
) -> dict[str, Any]:
    """多标的轻量现价/倾向：仍走 orchestrator，但 facts 仅保留摘要字段。"""
    items: list[dict[str, Any]] = []
    merged_evidence: list[dict[str, Any]] = []
    merged_risk: list[str] = []

    for raw in payloads:
        if not isinstance(raw, dict):
            continue
        sym = str(raw.get("symbol") or "").strip()
        if not sym:
            continue
        provider = str(raw.get("provider") or "gateio").strip()
        interval = str(raw.get("interval") or "4h").strip()
        question = str(raw.get("question") or "").strip() or None
        bundle = run_market_snapshot(
            repo_root=repo_root,
            symbol=sym,
            provider=provider,
            interval=interval,
            limit=limit,
            out_dir=None,
            question=question,
            rag_top_k=3,
            analysis_style="auto",
            with_research=bool(raw.get("with_research")),
            research_keyword=str(raw.get("research_keyword") or "").strip() or None,
        )
        ar = bundle.get("analysis_result") if isinstance(bundle.get("analysis_result"), dict) else {}
        wy = ar.get("wyckoff_123_v1") if isinstance(ar.get("wyckoff_123_v1"), dict) else {}
        items.append(
            {
                "symbol": ar.get("symbol") or sym,
                "name": ar.get("name"),
                "interval": ar.get("interval") or interval,
                "last_price": ar.get("last_price"),
                "trend": ar.get("trend"),
                "regime_label": ar.get("regime_label"),
                "fib_zone": ar.get("fib_zone"),
                "wyckoff_aligned": wy.get("aligned"),
            }
        )
        for ev in bundle.get("evidence_sources") or []:
            if isinstance(ev, dict) and ev.get("source_path"):
                sp = str(ev.get("source_path"))
                if not any(x.get("source_path") == sp for x in merged_evidence):
                    merged_evidence.append(dict(ev))
        for rf in bundle.get("risk_flags") or []:
            s = str(rf).strip()
            if s and s not in merged_risk:
                merged_risk.append(s)
    if not merged_risk:
        merged_risk = ["normal"]
    return {
        "items": items,
        "risk_flags": merged_risk,
        "evidence_sources": merged_evidence[:24],
    }
