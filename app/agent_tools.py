from __future__ import annotations

from pathlib import Path
from typing import Any

from app.executors.market_snapshot import run_market_snapshot
from langchain_core.tools import tool


def _build_analysis_bundle(
    *,
    repo_root: Path,
    symbol: str,
    provider: str,
    interval: str,
    limit: int,
    out_dir: str | None,
    question: str | None,
    rag_top_k: int,
    analysis_style: str,
) -> dict[str, Any]:
    return run_market_snapshot(
        repo_root=repo_root,
        symbol=symbol,
        provider=provider,
        interval=interval,
        limit=limit,
        out_dir=out_dir,
        question=question,
        rag_top_k=rag_top_k,
        analysis_style=analysis_style,
        with_research=False,
        research_keyword=None,
    )


def make_tools(*, repo_root: Path) -> list[Any]:
    @tool
    def fetch_analysis_bundle(
        symbol: str,
        provider: str = "gateio",
        interval: str = "1d",
        limit: int = 180,
        out_dir: str | None = None,
        question: str | None = None,
        rag_top_k: int = 5,
        analysis_style: str = "auto",
    ) -> dict[str, Any]:
        """拉取行情并生成结构化分析快照（含固定模板、风险标记与证据源）。"""
        return _build_analysis_bundle(
            repo_root=repo_root,
            symbol=symbol,
            provider=provider,
            interval=interval,
            limit=limit,
            out_dir=out_dir,
            question=question,
            rag_top_k=rag_top_k,
            analysis_style=analysis_style,
        )

    return [fetch_analysis_bundle]
