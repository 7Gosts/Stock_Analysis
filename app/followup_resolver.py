"""追问目标解析器：从用户文本和会话状态定位追问对象。

职责：
1. 判断用户消息是否为追问（"这个"、"它"、"xx"等指代）
2. 从会话状态绑定追问对象到具体标的、周期、产物路径
"""
from __future__ import annotations

import re
from typing import Any

from app.session_state import SessionState, SessionStateStore


_FOLLOWUP_PATTERNS = [
    r"(这个|那个|它|他|她|这只|那只|这|那)\s*(入场|触发|止损|止盈|盈亏比|风险|分析|结构|行情|走势)",
    r"(刚才|上一轮|上次|之前|刚才说的|上次说的)\s*(的|分析|行情|标的|那个|这个)",
    r"(它的|这个的|那个的)\s*(入场|止损|止盈|盈亏比|触发条件)",
    r"(继续|接着|追问|再说|再看)\s*(刚才|之前|上次)",
    r"(补充|展开|详细|深入)\s*(说|讲|解释|分析)",
    r"(还|再|继续)\s*(有|看|问|说)",
]


def _looks_like_followup(text: str) -> bool:
    """判断文本是否为追问模式。"""
    raw = (text or "").strip()
    if not raw:
        return False
    for pat in _FOLLOWUP_PATTERNS:
        if re.search(pat, raw, re.I):
            return True
    # 短文本且无标的关键词，可能是追问
    if len(raw) < 15 and not re.search(r"[A-Z]{2,4}[_\d]", raw, re.I):
        # 不包含明确标的编码，可能是追问
        return True
    return False


def resolve_followup_target(
    text: str,
    session_state: SessionState,
    *,
    prefer_local_facts: bool = True,
) -> dict[str, Any]:
    """解析追问目标。

    Args:
        text: 用户消息文本
        session_state: 当前会话状态
        prefer_local_facts: 优先返回本地产物路径（文档要求）

    Returns:
        resolved: 是否成功解析
        symbol: 绑定标的
        symbols: 多标的列表
        interval: 周期
        provider: 数据源
        output_refs: 本地产物路径（用于 RAG 检索）
        followup_type: 追问类型（entry/stop/tp/status/general）
        reason: 解析失败原因（如有）
    """
    if not _looks_like_followup(text):
        return {"resolved": False, "reason": "非追问模式"}

    if session_state.last_action not in {"analysis", "research", "quote"}:
        return {"resolved": False, "reason": "上一轮非分析任务"}

    target_symbol = session_state.last_symbol
    target_symbols = session_state.last_symbols

    if not target_symbol and not target_symbols:
        return {"resolved": False, "reason": "上一轮无标的"}

    # 提取追问类型
    followup_type = _extract_followup_type(text)

    result: dict[str, Any] = {
        "resolved": True,
        "symbol": target_symbol,
        "symbols": target_symbols,
        "interval": session_state.last_interval,
        "provider": session_state.last_provider,
        "followup_type": followup_type,
        "last_action": session_state.last_action,
        "last_task_type": session_state.last_task_type,
        "last_question": session_state.last_question,
    }

    # 优先返回本地产物路径（用于 RAG）
    if prefer_local_facts and session_state.last_output_refs:
        result["output_refs"] = session_state.last_output_refs

    return result


def _extract_followup_type(text: str) -> str:
    """提取追问类型。"""
    raw = (text or "").strip().lower()
    if re.search(r"(入场|触发|entry|trigger)", raw, re.I):
        return "entry"
    if re.search(r"(止损|stop)", raw, re.I):
        return "stop"
    if re.search(r"(止盈|tp|take\s*profit)", raw, re.I):
        return "tp"
    if re.search(r"(盈亏比|风险收益|risk\s*reward|rr)", raw, re.I):
        return "risk_reward"
    if re.search(r"(状态|持仓|仓位|是否|有没有|当前|现价)", raw, re.I):
        return "status"
    if re.search(r"(为什么|原因|逻辑|理由|怎么|如何)", raw, re.I):
        return "rationale"
    return "general"


def build_followup_clarify_message(
    *,
    text: str,
    session_state: SessionState,
) -> str:
    """生成追问澄清消息（当无法定位时）。"""
    if session_state.last_symbol:
        return (
            f"你是想追问上一轮 {session_state.last_symbol} {session_state.last_interval or ''} 的分析吗？"
            "如果是，请直接说你想了解哪方面（入场、止损、止盈、触发状态等）。"
        )
    return (
        "我没定位到你指的是哪一轮分析。"
        "可以补一句标的名称或周期，或让我重新分析某个标的。"
    )


def default_clarify_message(route_context: dict[str, Any] | None = None) -> str:
    """统一生成可见澄清（文档要求：永不返回空 clarify_message）。"""
    ctx = route_context or {}
    last_sym = ctx.get("last_symbol")
    last_iv = ctx.get("last_interval")
    if last_sym:
        return (
            f"我这次没有稳定拿到可回答的上下文。"
            f"你可以补一句标的/周期（比如「{last_sym} {last_iv or '日线'}」），"
            f"或让我按上一轮 {last_sym} 继续分析。"
        )
    return (
        "我这次没有稳定拿到可回答的上下文。"
        "你可以补一句标的/周期，或让我重新分析。"
    )