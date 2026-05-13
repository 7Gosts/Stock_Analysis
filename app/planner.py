"""飞书 Agent 规划层（三层重构版）。

职责收敛为：
1. 识别 task_type
2. 识别 action
3. 生成最小 task plan
4. 永不返回空 clarify_message

关键改变：
- 所有 clarify 分支必须返回非空消息（使用 default_clarify_message）
- 纯研报请求直接路由为 research，不要求标的与周期
- 追问请求先通过 followup_resolver 定位上一轮对象
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from app.feishu_asset_catalog import (
    FeishuAssetCatalog,
    canonical_tradable_symbol,
    canonical_tradable_symbol_list,
    get_catalog_for_repo,
    normalize_provider,
)
from app.followup_resolver import (
    default_clarify_message,
    resolve_followup_target,
    _looks_like_followup,
)
from app.session_state import SessionState
from tools.llm.client import LLMClientError, decide_feishu_route

TaskType = Literal["chat", "clarify", "quote", "compare", "analysis", "research", "followup"]
ResponseMode = Literal["quick", "compare", "analysis", "narrative", "followup"]


def _repo_root() -> Any:
    from pathlib import Path
    return Path(__file__).resolve().parents[1]


def _feishu_asset_catalog() -> FeishuAssetCatalog:
    return get_catalog_for_repo(_repo_root())


def _normalize_interval(value: str, default_interval: str) -> str:
    v = (value or "").strip().lower()
    if v in {"15m", "30m", "1h", "4h", "1d"}:
        return v
    return default_interval


def _to_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


_QUOTE_PAT = re.compile(
    r"(现价|多少钱|什么价|价格多少|报价|最新价|当前价|点位多少|多少\s*钱|price\s*now|"
    r"how\s+much|当前\s*报价)",
    re.I,
)
_ANALYSIS_PAT = re.compile(
    r"(分析|结构|fib|威科夫|趋势|模板|触发|止损|入场|k线|行情\s*分析|技术\s*面|多周期|共振)",
    re.I,
)
_COMPARE_PAT = re.compile(
    r"(谁更强|谁更弱|对比|比较|哪个好|哪个更好|强弱|排序|横向|versus|vs\.?|相对强弱|更适合)",
    re.I,
)
_RESEARCH_PAT = re.compile(
    r"(研报|机构|卖方|首席|观点|怎么看\s*待|配置逻辑|叙事|板块|概念|归属|行业|主题)",
    re.I,
)


def infer_task_type_from_text(
    text: str,
    *,
    legacy_action: str,
    symbol_count: int,
    with_research: bool,
) -> TaskType:
    """语义规则细化任务类型。"""
    raw = (text or "").strip()

    if legacy_action in {"chat", "clarify"}:
        return legacy_action  # type: ignore[return-value]

    if legacy_action == "followup":
        return "followup"

    if legacy_action == "analyze_multi":
        if symbol_count >= 2 and _COMPARE_PAT.search(raw):
            return "compare"
        if _QUOTE_PAT.search(raw) and not _ANALYSIS_PAT.search(raw):
            return "quote"
        return "analysis"

    if legacy_action == "analyze":
        if with_research and _RESEARCH_PAT.search(raw) and not _ANALYSIS_PAT.search(raw):
            return "research"
        if _QUOTE_PAT.search(raw) and not _ANALYSIS_PAT.search(raw):
            return "quote"
        return "analysis"

    return "analysis"


def plan_response_mode(task_type: TaskType) -> ResponseMode:
    if task_type in {"chat", "clarify", "quote"}:
        return "quick"
    if task_type == "compare":
        return "compare"
    if task_type == "research":
        return "narrative"
    if task_type == "followup":
        return "followup"
    return "analysis"


def build_task_plan(
    *,
    task_type: TaskType,
    response_mode: ResponseMode,
    text: str,
    symbols: list[str],
    interval: str,
    provider: str | None,
    with_research: bool,
    research_keyword: str | None,
    question: str,
    output_refs: dict[str, str] | None = None,
    followup_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_type": task_type,
        "response_mode": response_mode,
        "symbols": list(symbols),
        "interval": interval,
        "provider": (provider or "").strip().lower() or None,
        "question": question,
        "with_research": bool(with_research),
        "research_keyword": research_keyword,
        "user_text": (text or "").strip(),
        "output_refs": dict(output_refs or {}),
        "followup_context": dict(followup_context or {}),
    }


def _extract_research_keyword(text: str) -> str | None:
    """从纯研报请求提取关键词。"""
    raw = (text or "").strip()
    if not raw:
        return None
    patterns = (
        r"^(?:请|帮我|麻烦|顺便|看看|看下|看一下|查下|查一下|搜一下|搜下|了解一下|了解)?(?P<kw>.+?)(?:的)?(?:研报|研报线索|机构观点|观点|配置逻辑)$",
        r"^(?:请|帮我|麻烦|顺便|看看|看下|看一下|查下|查一下|搜一下|搜下|了解一下|了解)?(?P<kw>.+?)(?:板块|概念|行业|归属|主题)$",
    )
    for pattern in patterns:
        match = re.search(pattern, raw)
        if match:
            kw = str(match.group("kw") or "").strip().strip("的")
            if kw:
                return kw
    cleaned = re.sub(r"^(?:请|帮我|麻烦|顺便|看看|看下|看一下|查下|查一下|搜一下|搜下|了解一下|了解)+", "", raw)
    cleaned = re.sub(r"(的)?(研报|研报线索|机构观点|观点|配置逻辑|板块|概念|行业|归属|主题)$", "", cleaned)
    cleaned = cleaned.strip(" 的，,。！？!?")
    return cleaned or None


def _looks_like_research_only_request(text: str) -> bool:
    """识别纯研报/板块/归属请求（不要求标的与周期）。"""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_RESEARCH_PAT.search(raw)) and not _ANALYSIS_PAT.search(raw)


def plan_user_message(
    text: str,
    *,
    default_symbol: str,
    default_interval: str,
    session_state: SessionState | None = None,
    recent_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """意图路由（三层重构版）。

    顺序：
    1. 会话状态：先确认追问对象
    2. 本地 RAG：找到结构化事实（由 agent_facade 执行）
    3. 飞书历史：补齐语境（recent_messages）
    4. 显式澄清：若无法回答，返回非空澄清
    """
    raw = (text or "").strip()
    catalog = _feishu_asset_catalog()
    allowed = catalog.allowed_symbols

    # 默认标的
    ds = str(default_symbol or "").strip().upper()
    default_canon = canonical_tradable_symbol(ds, catalog)
    if default_canon is None and allowed:
        default_canon = sorted(allowed)[0]
    if default_canon is None:
        default_canon = "BTC_USDT"

    # 从会话状态获取上一轮上下文
    ctx_sym = None
    ctx_interval = default_interval
    ctx_provider = None
    if session_state:
        ctx_sym = session_state.last_symbol
        ctx_interval = session_state.last_interval or default_interval
        ctx_provider = session_state.last_provider

    base_symbol = canonical_tradable_symbol(str(ctx_sym or ""), catalog) or default_canon
    base_interval = _normalize_interval(str(ctx_interval or default_interval), default_interval)
    lp = str(ctx_provider or "").strip().lower()
    base_provider = lp if lp in {"tickflow", "gateio", "goldapi"} else None

    # 空文本 → 返回非空澄清
    if not raw:
        clarify_msg = default_clarify_message({
            "last_symbol": ctx_sym,
            "last_interval": ctx_interval,
        })
        tt: TaskType = "clarify"
        return {
            "action": "clarify",
            "clarify_message": clarify_msg,
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[],
                interval=base_interval,
                provider=base_provider,
                with_research=False,
                research_keyword=None,
                question=clarify_msg,
            ),
        }

    # 1. 追问检测：优先从会话状态定位
    if session_state and _looks_like_followup(raw):
        followup_result = resolve_followup_target(raw, session_state)
        if followup_result.get("resolved"):
            tt = "followup"
            return {
                "action": "followup",
                "task_type": tt,
                "response_mode": plan_response_mode(tt),
                "followup_context": followup_result,
                "task_plan": build_task_plan(
                    task_type=tt,
                    response_mode=plan_response_mode(tt),
                    text=raw,
                    symbols=followup_result.get("symbols") or [followup_result.get("symbol")] if followup_result.get("symbol") else [],
                    interval=followup_result.get("interval") or base_interval,
                    provider=followup_result.get("provider") or base_provider,
                    with_research=False,
                    research_keyword=None,
                    question=raw,
                    output_refs=followup_result.get("output_refs"),
                    followup_context=followup_result,
                ),
            }
        # 追问但无法定位 → 返回澄清
        clarify_msg = f"我没定位到你指的是哪一轮分析。{ctx_sym or ''} {ctx_interval or ''} 是上一轮标的吗？请确认标的和周期。"
        tt = "clarify"
        return {
            "action": "clarify",
            "clarify_message": clarify_msg,
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[],
                interval=base_interval,
                provider=base_provider,
                with_research=False,
                research_keyword=None,
                question=clarify_msg,
            ),
        }

    # 2. 纯研报请求：不要求标的与周期
    if _looks_like_research_only_request(raw):
        research_keyword = _extract_research_keyword(raw)
        tt: TaskType = "research"
        return {
            "action": "analyze",
            "payload": {
                "symbol": "",
                "provider": base_provider,
                "interval": base_interval,
                "question": raw,
                "use_rag": True,
                "use_llm_decision": True,
                "with_research": True,
                "research_keyword": research_keyword,
            },
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[],
                interval=base_interval,
                provider=base_provider,
                with_research=True,
                research_keyword=research_keyword,
                question=raw,
            ),
        }

    # 3. 调用 LLM 路由（常规分析请求）
    try:
        routed = decide_feishu_route(
            text=raw,
            default_symbol=base_symbol,
            default_interval=base_interval,
            recent_messages=recent_messages,
            tradable_assets=catalog.tradable_assets_for_prompt(),
        )
    except (LLMClientError, Exception) as exc:
        from loguru import logger
        msg = str(exc).replace("\n", " ").strip()
        logger.warning("[Planner] route_llm_error exc_type={} msg={}", type(exc).__name__, msg[:480])
        clarify_msg = default_clarify_message({
            "last_symbol": ctx_sym,
            "last_interval": ctx_interval,
        })
        tt = "clarify"
        return {
            "action": "clarify",
            "clarify_message": clarify_msg,
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[],
                interval=base_interval,
                provider=base_provider,
                with_research=False,
                research_keyword=None,
                question=clarify_msg,
            ),
        }

    action = str(routed.get("action") or "").strip().lower()

    # 4. clarify 分支：必须非空
    if action == "clarify":
        clarify_msg = str(routed.get("clarify_message") or "").strip()
        if not clarify_msg:
            clarify_msg = default_clarify_message({
                "last_symbol": ctx_sym,
                "last_interval": ctx_interval,
            })
        tt = "clarify"
        return {
            "action": "clarify",
            "clarify_message": clarify_msg,
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[],
                interval=base_interval,
                provider=base_provider,
                with_research=False,
                research_keyword=None,
                question=clarify_msg,
            ),
        }

    # 5. chat 分支
    if action == "chat":
        chat_reply = str(routed.get("chat_reply") or "").strip()
        if not chat_reply:
            chat_reply = "收到，有什么我可以帮你分析的吗？"
        tt = "chat"
        return {
            "action": "chat",
            "chat_reply": chat_reply,
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[],
                interval=base_interval,
                provider=base_provider,
                with_research=False,
                research_keyword=None,
                question=chat_reply,
            ),
        }

    # 6. research / concept_board 分支
    if action in {"research", "concept_board"}:
        research_keyword = str(
            routed.get("keyword") or routed.get("research_keyword") or routed.get("symbol") or ""
        ).strip() or None
        routed_symbol = str(routed.get("symbol") or "").strip().upper()
        routed_question = str(routed.get("question") or "").strip()
        tt = "research"
        return {
            "action": "analyze",
            "payload": {
                "symbol": routed_symbol,
                "provider": normalize_provider(routed.get("provider"), symbol_upper=routed_symbol, catalog=catalog),
                "interval": base_interval,
                "question": routed_question or raw,
                "use_rag": True,
                "use_llm_decision": True,
                "with_research": True,
                "research_keyword": research_keyword,
            },
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[routed_symbol] if routed_symbol else [],
                interval=base_interval,
                provider=normalize_provider(routed.get("provider"), symbol_upper=routed_symbol, catalog=catalog),
                with_research=True,
                research_keyword=research_keyword,
                question=routed_question or raw,
            ),
        }

    # 7. analyze 分支（含多标的）
    if action == "analyze":
        routed_symbols = canonical_tradable_symbol_list(routed.get("symbols"), catalog)
        routed_interval = str(routed.get("interval") or "").strip().lower()
        routed_question = str(routed.get("question") or "").strip()
        with_research = _to_bool(routed.get("with_research"), default=False)
        global_kw = str(routed.get("research_keyword") or "").strip() or None

        # 无有效标的 → 返回非空澄清
        if not routed_symbols:
            clarify_msg = default_clarify_message({
                "last_symbol": ctx_sym,
                "last_interval": ctx_interval,
            })
            tt = "clarify"
            return {
                "action": "clarify",
                "clarify_message": clarify_msg,
                "task_type": tt,
                "response_mode": plan_response_mode(tt),
                "task_plan": build_task_plan(
                    task_type=tt,
                    response_mode=plan_response_mode(tt),
                    text=raw,
                    symbols=[],
                    interval=base_interval,
                    provider=base_provider,
                    with_research=False,
                    research_keyword=None,
                    question=clarify_msg,
                ),
            }

        # 多标的
        if len(routed_symbols) > 1:
            payloads: list[dict[str, Any]] = []
            for sym in routed_symbols:
                rk = (global_kw or catalog.research_keyword_for(sym) or None) if with_research else None
                payloads.append({
                    "symbol": sym,
                    "provider": normalize_provider(routed.get("provider"), symbol_upper=sym, catalog=catalog),
                    "interval": _normalize_interval(routed_interval, base_interval),
                    "question": routed_question or raw,
                    "use_rag": True,
                    "use_llm_decision": True,
                    "with_research": with_research,
                    "research_keyword": rk,
                })
            tt = infer_task_type_from_text(
                raw, legacy_action="analyze_multi", symbol_count=len(routed_symbols), with_research=with_research
            )
            return {
                "action": "analyze_multi",
                "payloads": payloads,
                "task_type": tt,
                "response_mode": plan_response_mode(tt),
                "task_plan": build_task_plan(
                    task_type=tt,
                    response_mode=plan_response_mode(tt),
                    text=raw,
                    symbols=list(routed_symbols),
                    interval=_normalize_interval(routed_interval, base_interval),
                    provider=normalize_provider(routed.get("provider"), symbol_upper=routed_symbols[0], catalog=catalog),
                    with_research=with_research,
                    research_keyword=global_kw,
                    question=routed_question or raw,
                ),
            }

        # 单标的
        single = routed_symbols[0]
        pv = normalize_provider(routed.get("provider"), symbol_upper=single, catalog=catalog)
        iv = _normalize_interval(routed_interval, base_interval)
        rk = (global_kw or catalog.research_keyword_for(single) or None) if with_research else None
        tt = infer_task_type_from_text(
            raw, legacy_action="analyze", symbol_count=1, with_research=with_research
        )
        return {
            "action": "analyze",
            "payload": {
                "symbol": single,
                "provider": pv,
                "interval": iv,
                "question": routed_question or raw,
                "use_rag": True,
                "use_llm_decision": True,
                "with_research": with_research,
                "research_keyword": rk,
            },
            "task_type": tt,
            "response_mode": plan_response_mode(tt),
            "task_plan": build_task_plan(
                task_type=tt,
                response_mode=plan_response_mode(tt),
                text=raw,
                symbols=[single],
                interval=iv,
                provider=pv,
                with_research=with_research,
                research_keyword=rk,
                question=routed_question or raw,
            ),
        }

    # 8. 其他未知 action → 返回非空澄清
    clarify_msg = default_clarify_message({
        "last_symbol": ctx_sym,
        "last_interval": ctx_interval,
    })
    tt = "clarify"
    return {
        "action": "clarify",
        "clarify_message": clarify_msg,
        "task_type": tt,
        "response_mode": plan_response_mode(tt),
        "task_plan": build_task_plan(
            task_type=tt,
            response_mode=plan_response_mode(tt),
            text=raw,
            symbols=[],
            interval=base_interval,
            provider=base_provider,
            with_research=False,
            research_keyword=None,
            question=clarify_msg,
        ),
    }


def log_routed_preview(routed: dict[str, Any], *, logger_label: str = "[Planner]") -> None:
    """路由关键字段打日志（调试）。"""
    import os
    from loguru import logger

    if os.getenv("FEISHU_ROUTE_DEBUG", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    if not isinstance(routed, dict):
        return
    keys = (
        "action",
        "task_type",
        "symbol",
        "symbols",
        "interval",
        "question",
        "provider",
        "with_research",
        "research_keyword",
        "clarify_message",
        "followup_context",
        "output_refs",
    )
    preview = {k: routed.get(k) for k in keys if k in routed}
    line = json.dumps(preview, ensure_ascii=False)
    logger.debug("{} route_debug {}", logger_label, line[:600])