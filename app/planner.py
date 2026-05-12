"""飞书 / Agent 规划层：意图路由后的任务类型、回答模式与 task_plan 统一结构。"""
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
from tools.deepseek.client import DeepSeekError, decide_feishu_route

TaskType = Literal["chat", "clarify", "quote", "compare", "analysis", "research"]
ResponseMode = Literal["quick", "compare", "analysis", "narrative"]

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
    r"(研报|机构|卖方|首席|观点|怎么看\s*待|配置逻辑|叙事)",
    re.I,
)


def infer_task_type_from_text(
    text: str,
    *,
    legacy_action: str,
    symbol_count: int,
    with_research: bool,
) -> TaskType:
    """在 LLM 已落地为 analyze / analyze_multi 后，用语义规则细化任务类型（不改变合法标的校验）。"""
    raw = (text or "").strip()
    low = raw.lower()

    if legacy_action in {"chat", "clarify"}:
        return legacy_action  # type: ignore[return-value]

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
    }


def parse_user_message(
    text: str,
    *,
    default_symbol: str,
    default_interval: str,
    provider: str | None = None,
    with_research: bool = False,
    research_keyword: str | None = None,
) -> dict[str, Any]:
    """仅提供会话默认值与原文 question；标的与周期由路由 LLM 决定，经落地函数校验。"""
    raw = (text or "").strip()
    q = raw if raw else "请按固定模板输出当前行情，并结合我的问题意图解释。"
    cat = _feishu_asset_catalog()
    sym_u = str(default_symbol or "").strip().upper()
    pv = provider if provider else normalize_provider(None, symbol_upper=sym_u, catalog=cat)
    rk = str(research_keyword).strip() if isinstance(research_keyword, str) and str(research_keyword).strip() else None
    return {
        "symbol": default_symbol,
        "provider": pv,
        "interval": default_interval,
        "question": q,
        "use_rag": True,
        "use_llm_decision": True,
        "with_research": bool(with_research),
        "research_keyword": rk,
    }


def plan_user_message(
    text: str,
    *,
    default_symbol: str,
    default_interval: str,
    context: dict[str, Any] | None = None,
    recent_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """意图由路由 LLM 决定；本函数补充 task_type / response_mode / task_plan。"""
    raw = (text or "").strip()
    ctx = context if isinstance(context, dict) else {}
    catalog = _feishu_asset_catalog()
    allowed = catalog.allowed_symbols

    ds = str(default_symbol or "").strip().upper()
    default_canon = canonical_tradable_symbol(ds, catalog)
    if default_canon is None and allowed:
        default_canon = sorted(allowed)[0]
    if default_canon is None:
        default_canon = "BTC_USDT"

    ctx_sym = str(ctx.get("last_symbol") or "").strip()
    base_symbol = canonical_tradable_symbol(ctx_sym, catalog) or default_canon
    lp = str(ctx.get("last_provider") or "").strip().lower()
    base_provider = lp if lp in {"tickflow", "gateio", "goldapi"} else None
    base_interval = _normalize_interval(str(ctx.get("last_interval") or default_interval), default_interval)

    if not raw:
        out: dict[str, Any] = {"action": "clarify", "clarify_message": ""}
        tt: TaskType = "clarify"
        out["task_type"] = tt
        out["response_mode"] = plan_response_mode(tt)
        out["task_plan"] = build_task_plan(
            task_type=tt,
            response_mode=out["response_mode"],
            text=raw,
            symbols=[],
            interval=base_interval,
            provider=base_provider,
            with_research=False,
            research_keyword=None,
            question="",
        )
        return out

    payload = parse_user_message(
        raw,
        default_symbol=base_symbol,
        default_interval=base_interval,
        provider=base_provider,
    )

    try:
        routed = decide_feishu_route(
            text=raw,
            default_symbol=base_symbol,
            default_interval=base_interval,
            recent_messages=recent_messages,
            tradable_assets=catalog.tradable_assets_for_prompt(),
        )
    except (DeepSeekError, Exception) as exc:
        from loguru import logger

        msg = str(exc).replace("\n", " ").strip()
        logger.warning("[Planner] route_llm_error exc_type={} msg={}", type(exc).__name__, msg[:480])
        out = {"action": "clarify", "clarify_message": ""}
        tt = "clarify"
        out["task_type"] = tt
        out["response_mode"] = plan_response_mode(tt)
        out["task_plan"] = build_task_plan(
            task_type=tt,
            response_mode=out["response_mode"],
            text=raw,
            symbols=[],
            interval=base_interval,
            provider=base_provider,
            with_research=False,
            research_keyword=None,
            question="",
        )
        return out

    action = str(routed.get("action") or "").strip().lower()
    if action == "clarify":
        clarify_msg = str(routed.get("clarify_message") or "").strip()
        out = {"action": "clarify", "clarify_message": clarify_msg}
        tt = "clarify"
        out["task_type"] = tt
        out["response_mode"] = plan_response_mode(tt)
        out["task_plan"] = build_task_plan(
            task_type=tt,
            response_mode=out["response_mode"],
            text=raw,
            symbols=[],
            interval=base_interval,
            provider=base_provider,
            with_research=False,
            research_keyword=None,
            question=clarify_msg,
        )
        return out
    if action == "chat":
        chat_reply = str(routed.get("chat_reply") or "").strip()
        if chat_reply:
            out = {"action": "chat", "chat_reply": chat_reply}
        else:
            out = {"action": "chat"}
        tt = "chat"
        out["task_type"] = tt
        out["response_mode"] = plan_response_mode(tt)
        out["task_plan"] = build_task_plan(
            task_type=tt,
            response_mode=out["response_mode"],
            text=raw,
            symbols=[],
            interval=base_interval,
            provider=base_provider,
            with_research=False,
            research_keyword=None,
            question=chat_reply,
        )
        return out

    if action not in {"analyze"}:
        out = {"action": "clarify", "clarify_message": ""}
        tt = "clarify"
        out["task_type"] = tt
        out["response_mode"] = plan_response_mode(tt)
        out["task_plan"] = build_task_plan(
            task_type=tt,
            response_mode=out["response_mode"],
            text=raw,
            symbols=[],
            interval=base_interval,
            provider=base_provider,
            with_research=False,
            research_keyword=None,
            question="",
        )
        return out

    routed_symbols = canonical_tradable_symbol_list(routed.get("symbols"), catalog)
    routed_interval = str(routed.get("interval") or "").strip().lower()
    routed_question = str(routed.get("question") or "").strip()
    with_research = _to_bool(routed.get("with_research"), default=False)
    global_kw = str(routed.get("research_keyword") or "").strip() or None

    if len(routed_symbols) > 1:
        payloads: list[dict[str, Any]] = []
        for sym in routed_symbols:
            rk = (global_kw or catalog.research_keyword_for(sym) or None) if with_research else None
            payloads.append(
                {
                    "symbol": sym,
                    "provider": normalize_provider(routed.get("provider"), symbol_upper=sym, catalog=catalog),
                    "interval": _normalize_interval(routed_interval, payload["interval"]),
                    "question": routed_question or payload["question"],
                    "use_rag": True,
                    "use_llm_decision": True,
                    "with_research": with_research,
                    "research_keyword": rk,
                }
            )
        out = {"action": "analyze_multi", "payloads": payloads}
        tt = infer_task_type_from_text(
            raw, legacy_action="analyze_multi", symbol_count=len(routed_symbols), with_research=with_research
        )
        out["task_type"] = tt
        out["response_mode"] = plan_response_mode(tt)
        sym0 = str(routed_symbols[0] or "")
        pv0 = normalize_provider(routed.get("provider"), symbol_upper=sym0, catalog=catalog)
        out["task_plan"] = build_task_plan(
            task_type=tt,
            response_mode=out["response_mode"],
            text=raw,
            symbols=list(routed_symbols),
            interval=_normalize_interval(routed_interval, payload["interval"]),
            provider=pv0,
            with_research=with_research,
            research_keyword=global_kw,
            question=routed_question or payload["question"],
        )
        return out

    single = canonical_tradable_symbol(str(routed.get("symbol") or ""), catalog)
    if single is None and len(routed_symbols) == 1:
        single = routed_symbols[0]
    if single is None:
        out = {"action": "clarify", "clarify_message": ""}
        tt = "clarify"
        out["task_type"] = tt
        out["response_mode"] = plan_response_mode(tt)
        out["task_plan"] = build_task_plan(
            task_type=tt,
            response_mode=out["response_mode"],
            text=raw,
            symbols=[],
            interval=base_interval,
            provider=base_provider,
            with_research=False,
            research_keyword=None,
            question="",
        )
        return out

    payload["symbol"] = single
    payload["interval"] = _normalize_interval(routed_interval or str(payload.get("interval") or ""), payload["interval"])
    q = str(routed.get("question") or "").strip()
    if q:
        payload["question"] = q
    payload["provider"] = normalize_provider(routed.get("provider"), symbol_upper=single, catalog=catalog)
    payload["with_research"] = with_research
    if with_research:
        payload["research_keyword"] = global_kw or catalog.research_keyword_for(single) or None
    else:
        payload["research_keyword"] = None

    out = {"action": "analyze", "payload": payload}
    tt = infer_task_type_from_text(
        raw, legacy_action="analyze", symbol_count=1, with_research=with_research
    )
    out["task_type"] = tt
    out["response_mode"] = plan_response_mode(tt)
    out["task_plan"] = build_task_plan(
        task_type=tt,
        response_mode=out["response_mode"],
        text=raw,
        symbols=[single],
        interval=str(payload.get("interval") or ""),
        provider=str(payload.get("provider") or "") or None,
        with_research=with_research,
        research_keyword=str(payload.get("research_keyword") or "").strip() or None,
        question=str(payload.get("question") or ""),
    )
    return out


def route_intent(
    text: str,
    *,
    default_symbol: str,
    default_interval: str,
    recent_messages: list[dict[str, str]] | None = None,
    tradable_assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """仅调用 DeepSeek 路由，不做标的表落地（供调试或 HTTP 层使用）。"""
    return decide_feishu_route(
        text=text,
        default_symbol=default_symbol,
        default_interval=default_interval,
        recent_messages=recent_messages,
        tradable_assets=tradable_assets,
    )


def log_routed_preview(routed: dict[str, Any], *, logger_label: str = "[Planner]") -> None:
    """可选：将路由关键字段打日志（与飞书 route_debug 对齐）。"""
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
    )
    preview = {k: routed.get(k) for k in keys if k in routed}
    line = json.dumps(preview, ensure_ascii=False)
    logger.debug("{} route_debug {}", logger_label, line[:600])
