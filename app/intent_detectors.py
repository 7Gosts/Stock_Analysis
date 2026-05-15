"""基于会话状态 + 用户文本的确定性意图检测（优先于宽泛正则截流）。

用于「展示偏好 / 含糊行情 / 模拟账户追问 / 追问」等可在不调 LLM 时安全兜底的场景。
追问检测逻辑已内联，不再依赖 intent_followup / followup_resolver。
"""
from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.session_state import SessionState


# ── 展示偏好 ──

_DECIMAL_PAT = re.compile(
    r"(?:精确|保留|只显示|只要|显示|改成|改为|用)?\s*(\d{1,2})\s*位\s*小数"
    r"|(\d{1,2})\s*位\s*小数"
    r"|小数\s*(?:点后\s*)?(\d{1,2})\s*位",
    re.I,
)
_BRIEF_PAT = re.compile(r"(简短点|简单说|一句话|精简|再短一点|短一点|概括一下)", re.I)
_REPEAT_PAT = re.compile(r"(再说一遍|重复一下|刚才那个|上一句|上一次回复)", re.I)

# ── 含糊行情 ──

_VAGUE_MARKET_PAT = re.compile(
    r"(最新行情|看下行情|行情怎么样|现在什么价|当前价格|最新价格|现价多少|报价多少|什么价位|走势怎么样)",
    re.I,
)
_EXPLICIT_SYMBOL_PAT = re.compile(
    r"(BTC|ETH|SOL|AU9999|NVDA|AAPL|_[A-Z]{3,}|USDT)",
    re.I,
)
_ANALYSIS_EXPLICIT_PAT = re.compile(
    r"(分析|结构|fib|威科夫|趋势|模板|触发|止损|入场|k线|行情\s*分析|技术\s*面|多周期|共振)",
    re.I,
)

# ── 模拟账户 ──

_SIM_KW = (
    "余额", "资金", "账户", "持仓", "订单", "成交", "仓位", "模拟账户",
    "挂单", "纸单", "入金", "提金", "权益", "可用",
)

# ── 追问（内联自 intent_followup / followup_resolver）──

_FOLLOWUP_PATTERNS = [
    r"(这个|那个|它|他|她|这只|那只|这|那)\s*(入场|触发|止损|止盈|盈亏比|风险|分析|结构|行情|走势)",
    r"(刚才|上一轮|上次|之前|刚才说的|上次说的)\s*(的|分析|行情|标的|那个|这个)",
    r"(它的|这个的|那个的)\s*(入场|止损|止盈|盈亏比|触发条件)",
    r"(继续|接着|追问|再说|再看)\s*(刚才|之前|上次)",
    r"(补充|展开|详细|深入)\s*(说|讲|解释|分析)",
    r"(还|再|继续)\s*(有|看|问|说)",
]

_NEW_REQUEST_PATTERNS = [
    r"(看下|看|查下|查|搜下|搜一下|找下|找一下|帮我|请|麻烦)",
    r"(研报|板块|概念|行业|主题|观点|机构)",
    r"(分析|行情|走势|技术)",
]


def _extract_decimal_places(text: str) -> int | None:
    m = _DECIMAL_PAT.search((text or "").strip())
    if not m:
        return None
    for g in m.groups():
        if g is None:
            continue
        try:
            n = int(g)
        except ValueError:
            continue
        if 0 <= n <= 8:
            return n
    return None


def _merge_prefs_from_text(text: str, base: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    p = _extract_decimal_places(text)
    if p is not None:
        out["precision"] = p
    if _BRIEF_PAT.search(text):
        out["compact"] = True
    if _REPEAT_PAT.search(text):
        out["repeat"] = True
    return out


def detect_display_preference(
    text: str,
    session_state: SessionState,
) -> dict[str, Any] | None:
    """若存在上一轮 facts 且本轮为展示类偏好，返回 display_adjustment 路由片段。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if not session_state.last_facts_bundle:
        return None
    if not (
        _extract_decimal_places(raw) is not None
        or _BRIEF_PAT.search(raw)
        or _REPEAT_PAT.search(raw)
    ):
        return None
    prefs = _merge_prefs_from_text(raw, dict(session_state.last_display_preferences or {}))
    return {
        "task_type": "display_adjustment",
        "action": "display_adjustment",
        "response_mode": "quick",
        "scope": None,
        "display_preferences": prefs,
    }


def detect_ambiguous_market_intent(
    text: str,
    session_state: SessionState,
) -> dict[str, Any] | None:
    """无明确标的的行情/报价请求：承接上一轮标的走 quote。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if _EXPLICIT_SYMBOL_PAT.search(raw):
        return None
    if _ANALYSIS_EXPLICIT_PAT.search(raw):
        return None
    if not _VAGUE_MARKET_PAT.search(raw):
        return None
    syms = [str(s).strip().upper() for s in (session_state.last_symbols or []) if s]
    if not syms and session_state.last_symbol:
        syms = [str(session_state.last_symbol).strip().upper()]
    if not syms:
        return None
    return {
        "task_type": "quote",
        "action": "analyze",
        "response_mode": "quick",
        "payload": {
            "symbol": syms[0],
            "symbols": syms,
            "provider": session_state.last_provider,
            "interval": session_state.last_interval or "4h",
            "question": raw,
            "use_rag": True,
            "use_llm_decision": True,
        },
        "task_plan": {
            "task_type": "quote",
            "response_mode": "quick",
            "symbols": syms,
            "interval": session_state.last_interval or "4h",
            "provider": session_state.last_provider,
            "question": raw,
            "with_research": False,
            "research_keyword": None,
            "user_text": raw,
            "output_refs": dict(session_state.last_output_refs or {}),
            "followup_context": {},
        },
    }


def _scope_from_text(text: str) -> str | None:
    raw = (text or "").strip()
    if re.search(r"成交|fill", raw, re.I):
        return "fills"
    if re.search(r"委托|订单(?!.*成交)", raw, re.I) or "挂单" in raw:
        return "orders"
    if "持仓" in raw or re.search(r"position", raw, re.I):
        return "positions"
    if re.search(r"余额|资金|权益|overview", raw, re.I):
        return "overview"
    if re.search(r"健康|对账", raw, re.I):
        return "health"
    return None


def detect_sim_account_intent(
    text: str,
    session_state: SessionState,
) -> dict[str, Any] | None:
    """模拟账户意图：展示偏好已在外层排除。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if session_state.last_task_type == "sim_account":
        scope = _scope_from_text(raw)
        if scope:
            return {
                "task_type": "sim_account",
                "action": "sim_account",
                "response_mode": "quick",
                "scope": scope,
                "task_plan": {
                    "task_type": "sim_account",
                    "response_mode": "quick",
                    "symbols": list(session_state.last_symbols or []),
                    "interval": session_state.last_interval or "4h",
                    "provider": session_state.last_provider,
                    "question": raw,
                    "with_research": False,
                    "research_keyword": None,
                    "user_text": raw,
                    "output_refs": {},
                    "followup_context": {},
                },
            }
    if any(kw in raw for kw in _SIM_KW):
        return {
            "task_type": "sim_account",
            "action": "sim_account",
            "response_mode": "quick",
            "scope": "overview",
            "task_plan": {
                "task_type": "sim_account",
                "response_mode": "quick",
                "symbols": [],
                "interval": session_state.last_interval or "4h",
                "provider": session_state.last_provider,
                "question": raw,
                "with_research": False,
                "research_keyword": None,
                "user_text": raw,
                "output_refs": {},
                "followup_context": {},
            },
        }
    return None


def looks_like_followup(text: str) -> bool:
    """判断文本是否为追问模式。"""
    raw = (text or "").strip()
    if not raw:
        return False
    for pat in _FOLLOWUP_PATTERNS:
        if re.search(pat, raw, re.I):
            return True
    for pat in _NEW_REQUEST_PATTERNS:
        if re.search(pat, raw, re.I):
            return False
    return False


def extract_followup_type(text: str) -> str:
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


def resolve_followup_target(
    text: str,
    session_state: SessionState,
    *,
    prefer_local_facts: bool = True,
) -> dict[str, Any]:
    """解析追问目标。"""
    if not looks_like_followup(text):
        return {"resolved": False, "reason": "非追问模式"}

    if session_state.last_action not in {
        "analysis", "research", "quote", "compare", "followup",
        "analyze", "analyze_multi", "sim_account",
    }:
        return {"resolved": False, "reason": "上一轮非分析任务"}

    target_symbol = session_state.last_symbol
    target_symbols = session_state.last_symbols

    if not target_symbol and not target_symbols:
        return {"resolved": False, "reason": "上一轮无标的"}

    followup_type = extract_followup_type(text)

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

    if prefer_local_facts and session_state.last_output_refs:
        result["output_refs"] = session_state.last_output_refs

    return result


def detect_followup_route(
    text: str,
    session_state: SessionState,
) -> dict[str, Any] | None:
    """追问检测，产出 planner 兼容的 route dict。"""
    if not looks_like_followup(text):
        return None
    fr = resolve_followup_target(text, session_state)
    if not fr.get("resolved"):
        return None
    symbols = fr.get("symbols") or ([fr["symbol"]] if fr.get("symbol") else [])
    return {
        "action": "followup",
        "task_type": "followup",
        "response_mode": "followup",
        "followup_context": fr,
        "task_plan": {
            "task_type": "followup",
            "response_mode": "followup",
            "symbols": [str(s).upper() for s in symbols if s],
            "interval": fr.get("interval"),
            "provider": fr.get("provider"),
            "question": text,
            "with_research": False,
            "research_keyword": None,
            "user_text": text,
            "output_refs": fr.get("output_refs") or {},
            "followup_context": fr,
        },
    }


def apply_intent_pipeline(
    text: str,
    session_state: SessionState,
) -> dict[str, Any] | None:
    """按优先级返回完整 route dict；未命中则返回 None 交由 LLM planner。"""
    d = detect_display_preference(text, session_state)
    if d:
        return _finalize_display_route(d, text, session_state)
    d = detect_ambiguous_market_intent(text, session_state)
    if d:
        return d
    d = detect_sim_account_intent(text, session_state)
    if d:
        return _finalize_sim_route(d, text)
    d = detect_followup_route(text, session_state)
    if d:
        return d
    return None


def _finalize_display_route(
    fragment: dict[str, Any],
    text: str,
    session_state: SessionState,
) -> dict[str, Any]:
    prefs = dict(fragment.get("display_preferences") or {})
    return {
        "action": "display_adjustment",
        "task_type": "display_adjustment",
        "response_mode": "quick",
        "task_plan": {
            "task_type": "display_adjustment",
            "response_mode": "quick",
            "symbols": list(session_state.last_symbols or []),
            "interval": session_state.last_interval,
            "provider": session_state.last_provider,
            "question": text,
            "with_research": False,
            "research_keyword": None,
            "user_text": text,
            "output_refs": dict(session_state.last_output_refs or {}),
            "followup_context": {},
        },
        "display_preferences": prefs,
    }


def _finalize_sim_route(fragment: dict[str, Any], text: str) -> dict[str, Any]:
    scope = str(fragment.get("scope") or "overview").strip()
    tp = dict(fragment.get("task_plan") or {})
    tp.setdefault("question", text)
    return {
        "action": "sim_account",
        "task_type": "sim_account",
        "response_mode": "quick",
        "scope": scope,
        "task_plan": tp,
    }


def recent_messages_for_router(
    messages: list[BaseMessage],
    *,
    limit_pairs: int = 10,
) -> list[dict[str, str]]:
    """将 LangChain messages 转为 decide_feishu_route 所需的 recent_messages。"""
    out: list[dict[str, str]] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            c = str(m.content or "").strip()
            if c:
                out.append({"role": "user", "text": c})
        elif isinstance(m, AIMessage):
            c = str(m.content or "").strip()
            if c:
                out.append({"role": "assistant", "text": c})
    max_items = max(2, int(limit_pairs) * 2)
    return out[-max_items:]
