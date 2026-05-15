"""事实包聚合（三层重构版）。

职责：
1. 统一聚合 task_type 对应的事实源
2. 所有 facts_bundle 必须包含 evidence_sources（按文档要求）
3. followup 分支新增 followup_facts 和 followup_type 参数
"""
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
    followup_facts: dict[str, Any] | None = None,
    followup_type: str | None = None,
    sim_account_facts: dict[str, Any] | None = None,
    risk_flags: list[str] | None = None,
    evidence_sources: list[dict[str, Any]] | None = None,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一事实包：供 writer / guardrail 消费。

    Args:
        task_type: 任务类型（chat/quote/compare/analysis/research/followup）
        response_mode: 回复模式
        user_question: 用户原始问题
        symbols: 标的列表
        market_facts: 行情事实（quote/analysis）
        compare_facts: 对比事实（compare）
        research_facts: 研报事实（research）
        memory_facts: 长期记忆（弱补充）
        followup_facts: 追问事实（followup，从 RAG 获取）
        followup_type: 追问类型（entry/stop/tp/status/rationale/general）
        sim_account_facts: 模拟账户 capability 原始字典（domain/intent/summary/tables/metrics）
        risk_flags: 风险标记
        evidence_sources: 证据来源（必须包含 source_path、source_type、symbol）
        trace: 执行轨迹（不直接展示给终端用户）

    Returns:
        统一事实包，包含：
        - task_type
        - response_mode
        - symbols
        - user_question
        - market_facts / compare_facts / research_facts / followup_facts
        - risk_flags
        - evidence_sources（必须有）
        - trace
    """
    bundle: dict[str, Any] = {
        "task_type": task_type,
        "response_mode": response_mode,
        "symbols": list(symbols),
        "user_question": (user_question or "").strip(),
        "risk_flags": list(risk_flags or []),
        "evidence_sources": list(evidence_sources or []),
        "trace": trace if isinstance(trace, dict) else {},
    }

    # 按任务类型填充事实源
    if task_type in {"quote", "analysis"} and market_facts:
        bundle["market_facts"] = market_facts if isinstance(market_facts, dict) else {}

    if task_type == "compare" and compare_facts:
        bundle["compare_facts"] = compare_facts if isinstance(compare_facts, dict) else {}

    if task_type == "research" and research_facts:
        bundle["research_facts"] = research_facts if isinstance(research_facts, dict) else {}

    if task_type == "followup" and followup_facts:
        bundle["followup_facts"] = followup_facts if isinstance(followup_facts, dict) else {}
        bundle["followup_type"] = str(followup_type or "general")

    if task_type == "sim_account" and sim_account_facts:
        bundle["sim_account_facts"] = sim_account_facts if isinstance(sim_account_facts, dict) else {}

    # 长期记忆（弱补充，不作为主事实源）
    if memory_facts:
        bundle["memory_facts"] = memory_facts if isinstance(memory_facts, dict) else {}

    # 确保 evidence_sources 非空（文档要求）
    if not bundle["evidence_sources"]:
        bundle["evidence_sources"] = [{"source_path": "none", "source_type": "unknown"}]

    return bundle


def build_evidence_source(
    *,
    source_path: str,
    source_type: str,
    symbol: str | None = None,
    interval: str | None = None,
    score: float | None = None,
    snippet: str | None = None,
) -> dict[str, Any]:
    """构建单个证据来源（按文档 8.2 契约）。"""
    ev: dict[str, Any] = {
        "source_path": str(source_path),
        "source_type": str(source_type),
    }
    if symbol:
        ev["symbol"] = str(symbol).upper()
    if interval:
        ev["interval"] = str(interval).lower()
    if score is not None:
        ev["score"] = float(score)
    if snippet:
        ev["snippet"] = str(snippet)[:240]
    return ev