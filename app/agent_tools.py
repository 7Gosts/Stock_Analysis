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

    @tool
    def view_sim_account_state(
        scope: str = "overview",
        account_id: str | None = None,
        symbol: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """查看模拟账户状态。

        scope 可选值：
        - overview: 余额 + 持仓 + 活动想法 + 对账统计
        - positions: 当前未平仓持仓
        - active_ideas: watch/pending/filled 的活动交易想法
        - orders: 最近委托
        - fills: 最近成交
        - health: order/fill 对账统计
        """
        from app.capabilities.sim_account_capability import view_sim_account_state as _view

        result = _view(
            scope=scope,
            account_id=account_id,
            symbol=symbol,
            limit=limit,
        )
        return result.to_dict()

    return [fetch_analysis_bundle, view_sim_account_state]