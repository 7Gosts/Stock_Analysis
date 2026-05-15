"""行情能力层：查看行情与技术结构。

通过 market_snapshot executor 产出 CapabilityResult，
与 research / sim_account 同级。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.query_engine.base import CapabilityResult
from app.executors.market_snapshot import run_market_snapshot


def view_market_snapshot(
    *,
    repo_root: Path,
    symbol: str,
    provider: str = "gateio",
    interval: str = "1d",
    limit: int = 180,
    out_dir: str | None = None,
    question: str | None = None,
    rag_top_k: int = 5,
    analysis_style: str = "auto",
    with_research: bool = False,
    research_keyword: str | None = None,
) -> CapabilityResult:
    """查看行情快照，返回 CapabilityResult。

    内部调用 run_market_snapshot，然后包装为统一的 CapabilityResult。
    """
    raw = run_market_snapshot(
        repo_root=repo_root,
        symbol=symbol,
        provider=provider,
        interval=interval,
        limit=limit,
        out_dir=out_dir,
        question=question,
        rag_top_k=rag_top_k,
        analysis_style=analysis_style,
        with_research=with_research,
        research_keyword=research_keyword,
    )

    analysis = raw.get("analysis_result") or {}
    risk_flags = raw.get("risk_flags") or []
    evidence_sources_raw = raw.get("evidence_sources") or []
    meta_raw = raw.get("meta") or {}

    # 构建 summary
    trend = analysis.get("trend") or "未知"
    last_price = analysis.get("last_price")
    fib_zone = analysis.get("fib_zone") or "未知"
    symbol_display = analysis.get("symbol") or symbol
    interval_display = analysis.get("interval") or interval

    summary = (
        f"{symbol_display}（{interval_display}）：趋势={trend}，"
        f"最新价={last_price}，Fib 区={fib_zone}，"
        f"风险标记={risk_flags}"
    )

    evidence_paths = [str(e.get("source_path", "")) for e in evidence_sources_raw if e.get("source_path")]

    return CapabilityResult(
        domain="market",
        intent="snapshot",
        summary=summary,
        tables=[analysis],
        metrics={
            "trend": trend,
            "last_price": last_price,
            "fib_zone": fib_zone,
            "risk_flags": risk_flags,
        },
        evidence_sources=evidence_paths,
        meta={
            "session_dir": meta_raw.get("session_dir"),
            "symbols_processed": meta_raw.get("symbols_processed"),
            "journal": meta_raw.get("journal"),
            "risk_flags": risk_flags,
        },
    )