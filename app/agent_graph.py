"""统一聊天编排 StateGraph：capability → compose → session 更新 → 可选压缩。

所有 task_type 的能力执行在此完成，不再委托 agent_facade。
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from loguru import logger

from app.agent_state import ChatPostRouteState
from app.agent_schemas import AgentRequest, AgentResponse, DEFAULT_FALLBACK_MESSAGE
from app.agent_service import TaskRunner
from app.capabilities.compare_facts import run_compare_facts_bundle
from app.capabilities.quote_facts import run_quote_facts_bundle
from app.capabilities.research_facts import build_research_facts_bundle
from app.executors.facts_bundle import build_evidence_source, merge_facts_bundle
from app.executors.multi_asset_compare import run_multi_asset_compare
from app.formatters.feishu import split_feishu_text
from app.route_chat_handlers import build_chat_handle_result
from app.session_state import SessionState, SessionStateStore
from app.writer import safe_grounded_write


_GRAPH_LOCK = threading.Lock()
_COMPILED_GRAPH: Any = None

_CTX: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "chat_post_route_ctx", default=None
)

_COMPACT_RECENT_THRESHOLD = 24


def unified_chat_agent_enabled() -> bool:
    """默认开启；`AGENT_UNIFIED_GRAPH=0` 关闭并回退旧路径。"""
    v = os.getenv("AGENT_UNIFIED_GRAPH", "1").strip().lower()
    return v not in {"0", "false", "no", "off"}


def _ctx() -> dict[str, Any]:
    c = _CTX.get()
    if not c:
        raise RuntimeError("chat graph runtime context not set")
    return c


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


# ============ Fallback formatters (clean, no rigid "━━"/"【结论】") ============

def _minimal_sim_fallback(facts_bundle: dict[str, Any], *, display_preferences: dict[str, Any]) -> str:
    raw = facts_bundle.get("sim_account_facts") if isinstance(facts_bundle.get("sim_account_facts"), dict) else {}
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    prec = display_preferences.get("precision")
    if not isinstance(prec, int) or prec < 0:
        prec = 4

    def _fmt_num(x: Any) -> str:
        try:
            f = float(x)
        except (TypeError, ValueError):
            return str(x)
        s = f"{f:.{prec}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

    lines: list[str] = ["【模拟账户摘要】"]
    if isinstance(metrics, dict) and metrics:
        for aid, m in metrics.items():
            if not isinstance(m, dict):
                lines.append(f" · {aid}: {m}")
                continue
            bal = m.get("balance")
            eq = m.get("equity")
            av = m.get("available")
            lines.append(
                f" · {aid}: 余额 {_fmt_num(bal)}, 可用 {_fmt_num(av)}, 权益 {_fmt_num(eq)}"
            )
    summary = str(raw.get("summary") or "").strip()
    if summary and not metrics:
        lines.append(summary[:2000])
    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines)


def _fallback_quote(facts: dict[str, Any]) -> str:
    """quote 兜底：简洁价格 + 倾向。"""
    lines: list[str] = ["【价格快照】"]
    for it in (facts.get("items") or []):
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "")
        lp = it.get("last_price")
        tr = str(it.get("trend") or "").strip()
        iv = str(it.get("interval") or "").strip()
        bits = [f"{sym} {iv}".strip()]
        if lp is not None:
            bits.append(f"最新约 {lp}")
        if tr:
            bits.append(f"倾向：{tr}")
        lines.append(" · " + "，".join(x for x in bits if x))
    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines)


def _fallback_compare(facts: dict[str, Any]) -> str:
    """compare 兜底：横向对比行。"""
    lines: list[str] = ["【横向对比】"]
    for row in (facts.get("rows") or []):
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "")
        lp = row.get("last_price")
        tr = str(row.get("trend") or "").strip()
        seg = f" · {sym}："
        if lp is not None:
            seg += f"价约 {lp}；"
        if tr:
            seg += f"综合倾向 {tr}"
        lines.append(seg.rstrip("；"))
    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines)


def _fallback_research(facts: dict[str, Any]) -> str:
    """research 兜底：研报线索。"""
    if not facts.get("ok"):
        return f"研报检索暂不可用：{facts.get('error') or 'unknown'}。仅供技术分析与程序化演示。"
    lines: list[str] = [f"【研报线索】关键词：{facts.get('keyword') or ''}"]
    for it in (facts.get("items") or []):
        if not isinstance(it, dict):
            continue
        t = str(it.get("title") or "").strip()
        org = str(it.get("org_name") or "").strip()
        if t:
            lines.append(f" · {t}" + (f"（{org}）" if org else ""))
    lines.append("以上为检索摘要线索，非官方观点背书。仅供技术分析与程序化演示。")
    return "\n".join(lines)


def _fallback_followup(facts: dict[str, Any]) -> str:
    """followup 兜底：追问回复。"""
    lines: list[str] = ["【追问回复】"]
    ov = facts.get("overview")
    if isinstance(ov, dict):
        items = ov.get("items")
        if isinstance(items, list) and items:
            it = items[0] if isinstance(items[0], dict) else {}
            stats = it.get("stats") or {}
            wy = it.get("wyckoff_123_v1") or {}
            sel = wy.get("selected_setup") or {}
            triggered = sel.get("triggered")
            triggered_text = "待触发" if triggered is False else ("已触发" if triggered is True else "未知")
            lines.append(f" · 标的：{it.get('symbol') or '?'} {it.get('interval') or ''}")
            lines.append(f" · 趋势：{stats.get('trend') or '未知'}")
            lines.append(f" · 触发状态：{triggered_text}")
            if sel.get("entry"):
                lines.append(f" · 入场：{sel.get('entry')}")
            if sel.get("stop"):
                lines.append(f" · 止损：{sel.get('stop')}")
            if sel.get("tp1"):
                lines.append(f" · 止盈1：{sel.get('tp1')}")
            if sel.get("tp2"):
                lines.append(f" · 止盈2：{sel.get('tp2')}")
        else:
            lines.append(" · 未找到结构化分析数据")
    else:
        lines.append(" · 无有效分析产物")
    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines)


def _fallback_analysis(narrative_facts: dict[str, Any]) -> str:
    """analysis 兜底：从 narrative facts 产出简洁分析摘要（不使用━━/【结论】等僵硬格式）。"""
    lines: list[str] = []
    sym = str(narrative_facts.get("symbol") or "UNKNOWN")
    interval = str(narrative_facts.get("interval") or "N/A")
    trend = str(narrative_facts.get("trend") or "").strip()
    last_price = narrative_facts.get("last_price")
    regime = str(narrative_facts.get("regime_label") or "").strip()

    header_bits = [sym, interval]
    if trend:
        header_bits.append(f"倾向{trend}")
    if regime:
        header_bits.append(regime)
    if last_price is not None:
        header_bits.append(f"约{last_price}")
    lines.append(" · " + "，".join(x for x in header_bits if x))

    ft = narrative_facts.get("fixed_template") if isinstance(narrative_facts.get("fixed_template"), dict) else {}
    if ft:
        for key in ("综合倾向", "触发条件", "失效条件"):
            val = str(ft.get(key) or "").strip()
            if val and val != "待补充":
                lines.append(f" · {key}：{val}")

    wy = narrative_facts.get("wyckoff_123_v1") if isinstance(narrative_facts.get("wyckoff_123_v1"), dict) else {}
    sel = wy.get("selected_setup") if isinstance(wy.get("selected_setup"), dict) else None
    if sel:
        triggered = sel.get("triggered")
        triggered_text = "待触发" if triggered is False else ("已触发" if triggered is True else "未知")
        lines.append(f" · 威科夫123 {sel.get('side', '?')}：{triggered_text}")

    ms = narrative_facts.get("ma_snapshot") if isinstance(narrative_facts.get("ma_snapshot"), dict) else {}
    if ms:
        ma_bits = []
        if ms.get("sma20") is not None:
            ma_bits.append(f"SMA20={_fmt_px(ms['sma20'])}")
        if ms.get("sma60") is not None:
            ma_bits.append(f"SMA60={_fmt_px(ms['sma60'])}")
        if ma_bits:
            lines.append(" · " + "，".join(ma_bits))

    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines)


def _fmt_px(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(x) >= 1000:
        return f"{x:,.2f}"
    if abs(x) >= 1:
        return f"{x:.2f}"
    return f"{x:.4f}"


# ============ Capability helpers ============

def _capability_chat(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    base_meta = {
        "route": dict(route),
        "channel": request.channel,
        "task_type": "chat",
        "response_mode": "quick",
    }
    rdict = build_chat_handle_result(route, base_meta=base_meta)
    text = str(rdict.get("final_text") or "").strip()
    chunks = list(rdict.get("reply_chunks") or []) or ([text] if text else [])
    return {"facts_bundle": {}, "skip_compose_llm": True, "reply_text": text, "reply_chunks": chunks}


def _capability_sim(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    from app.capabilities import view_sim_account_state

    scope = str(route.get("scope") or "overview").strip()
    account_id = route.get("account_id") or None
    symbol = route.get("symbol") or None
    cap = view_sim_account_state(scope=scope, account_id=account_id, symbol=symbol)
    cap_d = cap.to_dict()
    tp = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
    symbols = list(tp.get("symbols") or [])
    if not symbols and symbol:
        symbols = [str(symbol).strip().upper()]
    fb = merge_facts_bundle(
        task_type="sim_account",
        response_mode="quick",
        user_question=request.text,
        symbols=symbols,
        sim_account_facts=cap_d,
        evidence_sources=[
            build_evidence_source(
                source_path="postgres:sim_account",
                source_type="journal",
                symbol=symbols[0] if symbols else None,
            )
        ],
        risk_flags=["normal"],
        trace={"executors": ["sim_account_capability"], "scope": scope},
    )
    return fb


def _capability_quote(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    tp = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
    pay = route.get("payload") if isinstance(route.get("payload"), dict) else {}
    sym = str(pay.get("symbol") or (tp.get("symbols") or [""])[0] or "").strip()
    if not sym:
        raise RuntimeError("quote capability missing symbol")
    provider = str(pay.get("provider") or tp.get("provider") or "gateio").strip()
    interval = str(pay.get("interval") or tp.get("interval") or "4h").strip()
    payload = {
        "symbol": sym,
        "provider": provider,
        "interval": interval,
        "question": str(pay.get("question") or tp.get("question") or request.text),
        "use_rag": bool(pay.get("use_rag", True)),
    }
    return run_quote_facts_bundle(
        repo_root=_repo_root(),
        user_question=request.text,
        payloads=[payload],
    )


def _capability_followup(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    followup_ctx = route.get("followup_context") or {}
    symbol = followup_ctx.get("symbol")
    interval = followup_ctx.get("interval")
    output_refs = followup_ctx.get("output_refs") or {}

    if not symbol:
        raise RuntimeError("followup capability missing symbol")

    rag_index = _ctx().get("rag_index")
    facts = rag_index.get_facts_for_followup(
        symbol,
        interval=interval,
        output_ref_path=output_refs.get("ai_overview_path"),
    )
    if not facts.get("found") or not isinstance(facts.get("overview"), dict):
        raise RuntimeError("追问所需的分析产物不存在或无法读取")

    followup_type = followup_ctx.get("followup_type", "general")

    fb = merge_facts_bundle(
        task_type="followup",
        response_mode="followup",
        user_question=request.text,
        symbols=[symbol],
        followup_facts=facts,
        followup_type=followup_type,
        evidence_sources=[{
            "source_path": facts.get("source_path", "rag:index"),
            "source_type": "kline",
            "symbol": symbol,
        }],
        trace={"executors": ["rag_followup"], "followup_type": followup_type},
    )
    return fb


def _capability_research(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    pay = dict(route.get("payload") or {})
    tp = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
    if not pay.get("research_keyword") and tp.get("research_keyword"):
        pay["research_keyword"] = tp.get("research_keyword")
    if not pay.get("symbol") and tp.get("symbols"):
        syms = tp.get("symbols") or []
        if syms:
            pay["symbol"] = syms[0]
    fb, _kw = build_research_facts_bundle(
        rag_index=_ctx()["rag_index"],
        user_question=request.text,
        payload=pay,
    )
    return fb


def _capability_analysis(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    pay = route.get("payload") if isinstance(route.get("payload"), dict) else {}
    tp = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
    sym = str(pay.get("symbol") or (tp.get("symbols") or [""])[0] or "").strip()
    if not sym:
        raise RuntimeError("analysis capability missing symbol")

    repo_root = _repo_root()
    runner = TaskRunner(repo_root=repo_root)
    result = runner.run_analysis(
        symbol=sym,
        provider=str(pay.get("provider") or "gateio"),
        interval=str(pay.get("interval") or "1d"),
        limit=int(pay.get("limit") or 180),
        out_dir=pay.get("out_dir"),
        with_research=bool(pay.get("with_research") or False),
        research_keyword=pay.get("research_keyword"),
        question=str(pay.get("question") or request.text),
        use_rag=bool(pay.get("use_rag", True)),
        rag_top_k=int(pay.get("rag_top_k") or 5),
        use_llm_decision=bool(pay.get("use_llm_decision", True)),
    )

    analysis = result.get("analysis_result") if isinstance(result.get("analysis_result"), dict) else {}
    narrative_facts: dict[str, Any] = {}
    for key in ("symbol", "name", "provider", "interval", "trend", "last_price", "fib_zone", "regime_label"):
        if key in analysis and analysis.get(key) is not None:
            narrative_facts[key] = analysis.get(key)
    if isinstance(analysis.get("fixed_template"), dict):
        narrative_facts["fixed_template"] = analysis.get("fixed_template")
    if isinstance(analysis.get("wyckoff_123_v1"), dict):
        wy = analysis.get("wyckoff_123_v1")
        narrative_facts["wyckoff_123_v1"] = {
            k: wy[k] for k in ("background", "preferred_side", "aligned", "selected_setup", "setups")
            if k in wy
        }
    if isinstance(analysis.get("ma_snapshot"), dict):
        narrative_facts["ma_snapshot"] = analysis.get("ma_snapshot")

    fb = merge_facts_bundle(
        task_type="analysis",
        response_mode="analysis",
        user_question=request.text,
        symbols=[sym],
        market_facts={"analysis_facts": narrative_facts},
        risk_flags=result.get("risk_flags") if isinstance(result.get("risk_flags"), list) else [],
        evidence_sources=result.get("evidence_sources") if isinstance(result.get("evidence_sources"), list) else [],
        trace={"executors": ["local_task_runner"], "task_mode": "local"},
    )

    # Store output_refs for followup
    output_refs: dict[str, str] = {}
    ov_path = str((result.get("meta") or {}).get("ai_overview_path") or "")
    if ov_path:
        output_refs["ai_overview_path"] = ov_path
    report_path = str((result.get("meta") or {}).get("full_report_path") or "")
    if report_path:
        output_refs["full_report_path"] = report_path

    return {**fb, "_output_refs": output_refs, "_narrative_facts": narrative_facts}


def _capability_compare(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    payloads = route.get("payloads") if isinstance(route.get("payloads"), list) else []
    payloads = [p for p in payloads if isinstance(p, dict)]
    if not payloads:
        raise RuntimeError("compare capability missing payloads")
    return run_compare_facts_bundle(
        repo_root=_repo_root(),
        user_question=request.text,
        payloads=payloads,
    )


def _capability_multi_analysis(route: dict[str, Any], request: AgentRequest) -> dict[str, Any]:
    """多标的完整分析：compare + per-symbol TaskRunner。"""
    payloads = route.get("payloads") if isinstance(route.get("payloads"), list) else []
    payloads = [p for p in payloads if isinstance(p, dict)]
    if not payloads:
        raise RuntimeError("multi_analysis capability missing payloads")

    symbols = [str(p.get("symbol") or "") for p in payloads]
    repo_root = _repo_root()
    cf = run_multi_asset_compare(repo_root=repo_root, payloads=payloads)
    fb = merge_facts_bundle(
        task_type="analysis",
        response_mode="analysis",
        user_question=request.text,
        symbols=symbols,
        market_facts={"multi_compare": {"rows": cf.get("rows")}},
        compare_facts=cf,
        evidence_sources=cf.get("evidence_sources") or [],
        risk_flags=cf.get("risk_flags") or [],
        trace={"executors": ["multi_asset_compare"], "note": "digest_writer"},
    )
    return fb


def _capability_display_adjustment(session_state: SessionState) -> dict[str, Any]:
    fb = session_state.last_facts_bundle
    if not isinstance(fb, dict) or not fb:
        raise RuntimeError("display_adjustment missing last_facts_bundle")
    return dict(fb)


# ============ Graph nodes ============

def capability_node(state: ChatPostRouteState) -> dict[str, Any]:
    rt = _ctx()
    request: AgentRequest = rt["request"]
    session_state: SessionState = rt["session_state"]
    route = state.get("route") or {}
    tt = str(route.get("task_type") or "analysis").strip().lower()
    action = str(route.get("action") or "").strip().lower()

    if tt == "display_adjustment":
        fb = _capability_display_adjustment(session_state)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if tt == "chat" or action == "chat":
        return _capability_chat(route, request)

    if tt == "sim_account":
        fb = _capability_sim(route, request)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if tt == "quote" and action == "analyze":
        fb = _capability_quote(route, request)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if tt == "followup" or action == "followup":
        fb = _capability_followup(route, request)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if tt == "research":
        fb = _capability_research(route, request)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if tt == "compare":
        fb = _capability_compare(route, request)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if tt == "quote" and action == "analyze_multi":
        # 多标的 quote 快照
        payloads = route.get("payloads") if isinstance(route.get("payloads"), list) else []
        payloads = [p for p in payloads if isinstance(p, dict)]
        fb = run_quote_facts_bundle(
            repo_root=_repo_root(),
            user_question=request.text,
            payloads=payloads,
        )
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if tt == "compare" and action == "analyze_multi":
        fb = _capability_compare(route, request)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    if action == "analyze_multi":
        fb = _capability_multi_analysis(route, request)
        return {"facts_bundle": fb, "skip_compose_llm": False}

    # 单标的 analysis（默认）
    raw = _capability_analysis(route, request)
    clean = {k: v for k, v in raw.items() if not str(k).startswith("_")}
    out: dict[str, Any] = {"facts_bundle": clean, "skip_compose_llm": False}
    if raw.get("_output_refs"):
        out["_output_refs"] = raw["_output_refs"]
    if raw.get("_narrative_facts"):
        out["_narrative_facts"] = raw["_narrative_facts"]
    return out


def compose_node(state: ChatPostRouteState) -> dict[str, Any]:
    rt = _ctx()
    request: AgentRequest = rt["request"]
    route = state.get("route") or {}
    tt = str(route.get("task_type") or "analysis").strip().lower()
    rm = str(route.get("response_mode") or "analysis").strip().lower()

    if state.get("skip_compose_llm") and state.get("reply_text"):
        return {
            "reply_text": str(state.get("reply_text") or ""),
            "reply_chunks": list(state.get("reply_chunks") or []),
        }

    fb = state.get("facts_bundle") if isinstance(state.get("facts_bundle"), dict) else {}

    # Merge display_preferences
    writer_tt = tt
    writer_rm = rm
    if tt == "display_adjustment":
        if isinstance(fb.get("task_type"), str) and fb.get("task_type"):
            writer_tt = str(fb.get("task_type"))
        if isinstance(fb.get("response_mode"), str) and fb.get("response_mode"):
            writer_rm = str(fb.get("response_mode"))

    prefs: dict[str, Any] = {}
    if isinstance(route.get("display_preferences"), dict):
        prefs = dict(route["display_preferences"])
    if isinstance(state.get("display_preferences"), dict):
        prefs = {**prefs, **state["display_preferences"]}
    if tt == "sim_account":
        simf = fb.get("sim_account_facts") if isinstance(fb.get("sim_account_facts"), dict) else {}
        dd = simf.get("default_display_prefs") if isinstance(simf.get("default_display_prefs"), dict) else {}
        if dd:
            prefs = {**dd, **prefs}

    out = safe_grounded_write(
        facts_bundle=fb,
        user_question=request.text,
        task_type=writer_tt,
        response_mode=writer_rm,
        display_preferences=prefs or None,
    )
    if out and str(out.get("text") or "").strip():
        text = str(out["text"]).strip()
        return {"reply_text": text, "reply_chunks": split_feishu_text(text)}

    # Fallback chain: grounded writer failed → task-specific minimal fallback
    fallback_text = _compose_fallback(fb, state, tt, rm, request.text)
    return {"reply_text": fallback_text, "reply_chunks": split_feishu_text(fallback_text)}


def _compose_fallback(
    fb: dict[str, Any],
    state: ChatPostRouteState,
    tt: str,
    rm: str,
    user_question: str,
) -> str:
    """Task-specific fallback when grounded writer is unavailable."""
    if tt == "sim_account" or tt == "display_adjustment":
        prefs: dict[str, Any] = {}
        route = state.get("route") or {}
        if isinstance(route.get("display_preferences"), dict):
            prefs = dict(route["display_preferences"])
        if isinstance(state.get("display_preferences"), dict):
            prefs = {**prefs, **state["display_preferences"]}
        return _minimal_sim_fallback(fb, display_preferences=prefs)

    if tt == "chat":
        return "我这次没有稳定生成回复。你可以补一句标的/周期，或让我重新分析。"

    # Extract raw facts for fallback
    market_facts = fb.get("market_facts") if isinstance(fb.get("market_facts"), dict) else {}
    research_facts = fb.get("research_facts") if isinstance(fb.get("research_facts"), dict) else {}
    followup_facts = fb.get("followup_facts") if isinstance(fb.get("followup_facts"), dict) else {}
    compare_facts = fb.get("compare_facts") if isinstance(fb.get("compare_facts"), dict) else {}
    narrative_facts = state.get("_narrative_facts") if isinstance(state.get("_narrative_facts"), dict) else {}
    if not narrative_facts:
        analysis_facts = market_facts.get("analysis_facts") if isinstance(market_facts.get("analysis_facts"), dict) else {}
        narrative_facts = analysis_facts

    if tt == "quote":
        raw = market_facts
        items = raw.get("items") if isinstance(raw.get("items"), list) else None
        if items:
            return _fallback_quote({"items": items})
        return DEFAULT_FALLBACK_MESSAGE

    if tt == "compare":
        rows = compare_facts.get("rows") if isinstance(compare_facts.get("rows"), list) else None
        if rows:
            return _fallback_compare({"rows": rows})
        raw_rows = market_facts.get("compare_summary", {}).get("rows") if isinstance(market_facts.get("compare_summary"), dict) else None
        if raw_rows:
            return _fallback_compare({"rows": raw_rows})
        return DEFAULT_FALLBACK_MESSAGE

    if tt == "research":
        return _fallback_research(research_facts)

    if tt == "followup":
        return _fallback_followup(followup_facts)

    # analysis: use narrative_facts
    if narrative_facts:
        return _fallback_analysis(narrative_facts)

    return DEFAULT_FALLBACK_MESSAGE


def update_session_node(state: ChatPostRouteState) -> dict[str, Any]:
    rt = _ctx()
    request: AgentRequest = rt["request"]
    store: SessionStateStore = rt["session_store"]
    route = state.get("route") or {}
    action = str(route.get("action") or route.get("task_type") or "chat").strip().lower()
    tt = str(route.get("task_type") or "chat").strip().lower()
    tp = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
    symbols = list(tp.get("symbols") or [])
    st = store.get(request.session_id)
    fb = state.get("facts_bundle") if isinstance(state.get("facts_bundle"), dict) else {}
    try:
        st.last_facts_bundle = json.loads(json.dumps(fb, ensure_ascii=False)) if fb else {}
    except (TypeError, ValueError):
        st.last_facts_bundle = {}
    prefs: dict[str, Any] = {}
    if isinstance(route.get("display_preferences"), dict):
        prefs = dict(route["display_preferences"])
    if isinstance(state.get("display_preferences"), dict):
        prefs = {**dict(st.last_display_preferences or {}), **state["display_preferences"], **prefs}
    st.last_display_preferences = prefs
    if tt == "sim_account":
        st.last_sim_account_scope = str(route.get("scope") or tp.get("scope") or "overview").strip()

    # For analysis, save output_refs from capability node
    output_refs = dict(tp.get("output_refs") or {})
    if isinstance(state.get("_output_refs"), dict):
        output_refs = {**output_refs, **state["_output_refs"]}

    st.history_version = int(st.history_version or 0) + 1
    store.update_from_route(
        request.session_id,
        action=action,
        task_type=tt,
        symbol=symbols[0] if symbols else None,
        symbols=symbols,
        interval=str(tp.get("interval") or "").strip() or None,
        provider=str(tp.get("provider") or "").strip() or None,
        question=str(tp.get("question") or request.text).strip(),
        output_refs=output_refs,
    )
    store.update(st)
    return {"history_version": st.history_version}


def compact_node(state: ChatPostRouteState) -> dict[str, Any]:
    rt = _ctx()
    request: AgentRequest = rt["request"]
    store: SessionStateStore = rt["session_store"]
    recent = request.context.get("recent_messages")
    n = len(recent) if isinstance(recent, list) else 0
    if n < _COMPACT_RECENT_THRESHOLD:
        return {}
    st = store.get(request.session_id)
    line = f"[auto-compact] recent_messages~{n} 条；history_version={st.history_version}。"
    prev = (st.compacted_summary or "").strip()
    st.compacted_summary = (prev + "\n" + line).strip() if prev else line
    store.update(st)
    return {"compacted_summary": st.compacted_summary}


def _build_graph() -> Any:
    workflow = StateGraph(ChatPostRouteState)
    workflow.add_node("capability", capability_node)
    workflow.add_node("compose", compose_node)
    workflow.add_node("update_session", update_session_node)
    workflow.add_node("compact", compact_node)
    workflow.add_edge(START, "capability")
    workflow.add_edge("capability", "compose")
    workflow.add_edge("compose", "update_session")
    workflow.add_edge("update_session", "compact")
    workflow.add_edge("compact", END)
    return workflow.compile()


def get_chat_post_route_graph() -> Any:
    global _COMPILED_GRAPH
    with _GRAPH_LOCK:
        if _COMPILED_GRAPH is None:
            _COMPILED_GRAPH = _build_graph()
        return _COMPILED_GRAPH


def run_post_route_chat_graph(
    *,
    route: dict[str, Any],
    request: AgentRequest,
    session_state: SessionState,
    session_store: SessionStateStore,
    rag_index: Any,
) -> AgentResponse:
    initial: ChatPostRouteState = {
        "route": dict(route),
        "task_type": str(route.get("task_type") or "analysis"),
        "response_mode": str(route.get("response_mode") or "analysis"),
        "action": str(route.get("action") or ""),
        "facts_bundle": {},
        "display_preferences": dict(route.get("display_preferences") or {}),
        "reply_text": "",
        "reply_chunks": [],
        "skip_compose_llm": False,
    }
    graph = get_chat_post_route_graph()
    token = _CTX.set(
        {
            "request": request,
            "session_state": session_state,
            "session_store": session_store,
            "rag_index": rag_index,
        }
    )
    try:
        out = graph.invoke(initial)
    finally:
        _CTX.reset(token)

    reply_text = str(out.get("reply_text") or "").strip() or DEFAULT_FALLBACK_MESSAGE
    chunks = list(out.get("reply_chunks") or []) or [reply_text]
    tt = str(route.get("task_type") or "analysis")
    rm = str(route.get("response_mode") or "analysis")
    meta: dict[str, Any] = {"route": dict(route), "unified_graph": True}
    fb = out.get("facts_bundle") if isinstance(out.get("facts_bundle"), dict) else {}
    simf = fb.get("sim_account_facts") if isinstance(fb.get("sim_account_facts"), dict) else {}
    if simf:
        meta["domain"] = simf.get("domain")
        meta["intent"] = simf.get("intent")
        if isinstance(simf.get("meta"), dict):
            meta["capability_meta"] = simf["meta"]
        ev = simf.get("evidence_sources")
        if isinstance(ev, list):
            meta["evidence_sources"] = ev
    return AgentResponse(
        task_type=tt,
        response_mode=rm,
        reply_text=reply_text,
        reply_chunks=chunks,
        facts_bundle=fb or None,
        meta=meta,
    )