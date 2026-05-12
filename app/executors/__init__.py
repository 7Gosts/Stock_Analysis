"""任务执行器：按任务类型拆分，产出事实而非最终渠道文案。"""
from __future__ import annotations

from app.executors.facts_bundle import merge_facts_bundle
from app.executors.market_snapshot import run_market_snapshot
from app.executors.multi_asset_compare import run_multi_asset_compare
from app.executors.quote_snapshot import run_quote_snapshots
from app.executors.research_summary import run_research_summary

__all__ = [
    "merge_facts_bundle",
    "run_market_snapshot",
    "run_multi_asset_compare",
    "run_quote_snapshots",
    "run_research_summary",
]
