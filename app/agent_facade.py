"""Agent Facade：执行器选择与 facts_bundle 聚合（三层重构版）。

职责收敛为：
1. 根据 task_type 选择执行器
2. 聚合 facts_bundle
3. 调 grounded writer

关键改变：
- 新增 followup_analysis 分支：不重新跑行情，只读取本地最新有效产物并回答追问
- research 分支支持直接消费本地 research RAG，不强依赖实时检索
- chat 分支先看本地语料是否可回答
- facts_bundle 必须包含 source 信息（按文档要求）
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from app.analysis_task_client import poll_analysis_result, submit_analysis_task
from app.executors.facts_bundle import merge_facts_bundle
from app.executors.multi_asset_compare import run_multi_asset_compare
from app.executors.quote_snapshot import run_quote_snapshots
from app.executors.research_summary import run_research_summary
from app.formatters.feishu import split_feishu_text
from app.rag_index import (
    get_or_create_rag_index,
    RagIndex,
)
from app.session_state import SessionState
from app.writer import (
    fallback_to_template_reply_enabled,
    grounded_writer_enabled,
    safe_grounded_write,
    write_legacy_narrative_if_enabled,
)


TaskType = Literal["chat", "clarify", "quote", "compare", "analysis", "research", "followup"]
ResponseMode = Literal["quick", "compare", "analysis", "narrative", "followup"]


# ============ 本地格式化函数（原 feishu_bot_service 迁移） ============

def _analyze_multiple_symbols_local(*, api_base_url: str, payloads: list[dict[str, Any]]) -> str:
    """多标的分析本地实现。"""
    cards: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        sym = str(payload.get("symbol") or "")
        try:
            task_id = submit_analysis_task(api_base_url=api_base_url, payload=payload)
            result = poll_analysis_result(api_base_url=api_base_url, task_id=task_id)
            chunks = _feishu_reply_chunks_local(result, user_question=str(payload.get("question") or ""))
            cards.append("\n\n".join(chunks))
        except Exception as exc:
            cards.append(f"{sym} 分析失败：{exc}")
    return "\n\n".join(cards)


def _feishu_reply_chunks_local(result_payload: dict[str, Any], *, user_question: str | None = None) -> list[str]:
    """飞书回复分段本地实现。"""
    flat = _format_fixed_template_reply_local(result_payload, user_question=user_question)
    return split_feishu_text(flat)


def _format_fixed_template_reply_local(result_payload: dict[str, Any], *, user_question: str | None = None) -> str:
    """固定模板格式化本地实现。"""
    analysis = result_payload.get("analysis_result") if isinstance(result_payload.get("analysis_result"), dict) else {}
    tpl = analysis.get("fixed_template") if isinstance(analysis.get("fixed_template"), dict) else {}

    required = ["综合倾向", "关键位(Fib)", "触发条件", "失效条件", "风险点", "下次复核时间"]
    for k in required:
        tpl.setdefault(k, "待补充")

    risk_points = tpl.get("风险点")
    if isinstance(risk_points, list):
        risk_text = "；".join(str(x) for x in risk_points if str(x).strip()) or "无"
    else:
        risk_text = str(risk_points)

    symbol = str(analysis.get("symbol") or "UNKNOWN")
    interval = str(analysis.get("interval") or "N/A")

    parts: list[str] = [
        f"━━ {symbol} {interval} ━━",
        "",
        "【结论】",
        f"  · 综合倾向：{tpl['综合倾向']}",
        "",
    ]

    msnap = analysis.get("ma_snapshot")
    if isinstance(msnap, dict) and msnap:
        parts.extend(_ma_system_block_lines(msnap))
        parts.append("")

    parts.extend([
        "【关键位与触发】",
        f"  · Fib / 区间：{tpl['关键位(Fib)']}",
        f"  · 触发条件：{tpl['触发条件']}",
        f"  · 失效条件：{tpl['失效条件']}",
        "",
        "【风险与复核】",
        f"  · 风险点：{risk_text}",
        f"  · 下次复核：{tpl['下次复核时间']}",
        "",
    ])

    wy_lines = _format_wyckoff_123_reply_lines(analysis.get("wyckoff_123_v1"))
    if wy_lines:
        parts.append("【威科夫 123】")
        for w in wy_lines:
            if w.startswith("- "):
                parts.append("  · " + w[2:])
            else:
                parts.append("  " + w.strip())
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _format_wyckoff_123_reply_lines(wyckoff: Any) -> list[str]:
    """威科夫 123 格式化。"""
    if not isinstance(wyckoff, dict) or not wyckoff:
        return []
    bg = wyckoff.get("background") if isinstance(wyckoff.get("background"), dict) else {}
    bias = str(bg.get("bias") or "neutral")
    bias_cn = {"long_only": "偏多", "short_only": "偏空", "neutral": "中性"}.get(bias, bias)

    lines: list[str] = [f"- 威科夫背景：{bias_cn}；state={bg.get('state') or '—'}"]

    sel = wyckoff.get("selected_setup") if isinstance(wyckoff.get("selected_setup"), dict) else None
    if sel:
        side = str(sel.get("side") or "?")
        triggered = sel.get("triggered")
        triggered_text = "待触发" if triggered is False else ("已触发" if triggered is True else "未知")
        lines.append(
            f"- 威科夫123（{side}）：entry={sel.get('entry')}，stop={sel.get('stop')}, "
            f"tp1={sel.get('tp1')}，tp2={sel.get('tp2')}，{triggered_text}"
        )
    else:
        lines.append("- 威科夫123：当前未选出程式单")

    return lines


def _ma_system_block_lines(ms: dict[str, Any]) -> list[str]:
    """均线系统格式化。"""
    if not isinstance(ms, dict) or not ms:
        return []
    lines: list[str] = ["【均线系统】"]

    bits: list[str] = []
    if "sma20" in ms and ms["sma20"] is not None:
        bits.append(f"SMA20={_fmt_ma_px(ms['sma20'])}")
    if "sma60" in ms and ms["sma60"] is not None:
        bits.append(f"SMA60={_fmt_ma_px(ms['sma60'])}")
    if bits:
        lines.append("  · " + "，".join(bits))

    return lines


def _fmt_ma_px(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(x) >= 1000:
        return f"{x:,.2f}"
    if abs(x) >= 1:
        return f"{x:.2f}"
    return f"{x:.4f}"


def _repo_root_from_context(context: dict[str, Any]) -> Path:
    raw = context.get("repo_root")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).resolve()
    if isinstance(raw, Path):
        return raw.resolve()
    return Path(__file__).resolve().parents[1]


def _format_fallback_reply(
    *,
    task_type: str,
    facts: dict[str, Any],
    user_question: str,
) -> str:
    """兜底格式化（当 grounded writer 不可用或失败时）。"""
    lines: list[str] = []

    if task_type == "quote":
        lines.append("【价格快照】")
        for it in facts.get("items") or []:
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

    elif task_type == "compare":
        lines.append("【横向对比】")
        for row in facts.get("rows") or []:
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

    elif task_type == "research":
        if not facts.get("ok"):
            return f"研报检索暂不可用：{facts.get('error') or 'unknown'}。仅供技术分析与程序化演示。"
        lines.append(f"【研报线索】关键词：{facts.get('keyword') or ''}")
        for it in facts.get("items") or []:
            if not isinstance(it, dict):
                continue
            t = str(it.get("title") or "").strip()
            org = str(it.get("org_name") or "").strip()
            if t:
                lines.append(f" · {t}" + (f"（{org}）" if org else ""))
        lines.append("以上为检索摘要线索，非官方观点背书。仅供技术分析与程序化演示。")

    elif task_type == "followup":
        lines.append("【追问回复】")
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

    else:
        lines.append("分析完成。")
        lines.append("仅供技术分析与程序化演示，不构成投资建议。")

    return "\n".join(lines).strip()


def _try_grounded_chunks(
    *,
    facts_bundle: dict[str, Any],
    user_question: str,
    task_type: str,
    response_mode: str,
) -> list[str] | None:
    if not grounded_writer_enabled():
        return None
    out = safe_grounded_write(
        facts_bundle=facts_bundle,
        user_question=user_question,
        task_type=task_type,
        response_mode=response_mode,
    )
    if not out or not str(out.get("text") or "").strip():
        return None
    return split_feishu_text(str(out["text"]))


def handle_user_request(
    *,
    text: str,
    channel: str = "feishu",
    user_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一 Agent 入口（三层重构版）。

    context 需含：
    - default_symbol, default_interval
    - session_state (SessionState 对象)
    - conversation_context (可选)
    - recent_messages (可选)
    - api_base_url (分析类)
    - rag_index (可选，默认从 output 构建)
    """
    ctx = context if isinstance(context, dict) else {}
    default_symbol = str(ctx.get("default_symbol") or "BTC_USDT").strip().upper()
    default_interval = str(ctx.get("default_interval") or "4h").strip().lower()
    api_base_url = str(ctx.get("api_base_url") or "http://127.0.0.1:8000").strip()
    session_state = ctx.get("session_state")
    recent = ctx.get("recent_messages") if isinstance(ctx.get("recent_messages"), list) else None
    user_q = str(ctx.get("user_message_for_chunks") or text or "").strip()

    # 从 planner 获取路由（已在 agent_core 中调用）
    route = ctx.get("route") if isinstance(ctx.get("route"), dict) else {}
    task_type = str(route.get("task_type") or "analysis")
    response_mode = str(route.get("response_mode") or "analysis")
    action = str(route.get("action") or "").strip().lower()

    base_meta: dict[str, Any] = {
        "route": dict(route),
        "channel": channel,
        "task_type": task_type,
        "response_mode": response_mode,
    }

    repo_root = _repo_root_from_context(ctx)
    rag_index = ctx.get("rag_index") or get_or_create_rag_index(repo_root / "output")

    # 1. clarify 分支（已保证非空）
    if task_type == "clarify" or action == "clarify":
        msg = str(route.get("clarify_message") or "").strip()
        if not msg:
            msg = "我这次没有稳定拿到可回答的上下文。你可以补一句标的/周期，或让我重新分析。"
        return {
            "task_type": "clarify",
            "response_mode": "quick",
            "facts_bundle": None,
            "final_text": msg,
            "reply_chunks": [msg],
            "legacy_action": "clarify",
            "meta": base_meta,
        }

    # 2. chat 分支
    if task_type == "chat" or action == "chat":
        msg = str(route.get("chat_reply") or "").strip()
        if not msg:
            msg = "收到，有什么我可以帮你分析的吗？"
        return {
            "task_type": "chat",
            "response_mode": "quick",
            "facts_bundle": None,
            "final_text": msg,
            "reply_chunks": [msg],
            "legacy_action": "chat",
            "meta": base_meta,
        }

    # 3. followup 分支（新增：不重新跑行情，只读取本地产物）
    if task_type == "followup" or action == "followup":
        followup_ctx = route.get("followup_context") or {}
        symbol = followup_ctx.get("symbol")
        interval = followup_ctx.get("interval")
        output_refs = followup_ctx.get("output_refs") or {}

        if not symbol:
            msg = "无法确认你要追问的行情标的，您可以重新输入股票代码或查询对应板块。"
            return {
                "task_type": "clarify",
                "response_mode": "quick",
                "facts_bundle": None,
                "final_text": msg,
                "reply_chunks": [msg],
                "legacy_action": "clarify",
                "meta": base_meta,
            }

        # 从 RAG 或 output_refs 获取事实
        facts = rag_index.get_facts_for_followup(
            symbol,
            interval=interval,
            output_ref_path=output_refs.get("ai_overview_path"),
        )

        # 获取 followup_type 以生成针对性回答
        followup_type = followup_ctx.get("followup_type", "general")

        fb = merge_facts_bundle(
            task_type="followup",
            response_mode="followup",
            user_question=user_q,
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

        chunks = _try_grounded_chunks(
            facts_bundle=fb,
            user_question=user_q,
            task_type="followup",
            response_mode="followup",
        )
        if not chunks:
            fallback_text = _format_fallback_reply(
                task_type="followup",
                facts=facts,
                user_question=user_q,
            )
            chunks = split_feishu_text(fallback_text)

        return {
            "task_type": "followup",
            "response_mode": "followup",
            "facts_bundle": fb,
            "final_text": "\n\n".join(chunks),
            "reply_chunks": chunks,
            "legacy_action": "followup",
            "meta": {**base_meta, "followup_type": followup_type, "symbol": symbol},
        }

    # 4. analyze_multi 分支（多标的）
    if action == "analyze_multi":
        payloads = route.get("payloads") if isinstance(route.get("payloads"), list) else []
        payloads = [p for p in payloads if isinstance(p, dict)]
        if not payloads:
            msg = "未解析到有效标的。"
            return {
                "task_type": task_type,
                "response_mode": response_mode,
                "facts_bundle": None,
                "final_text": msg,
                "reply_chunks": [msg],
                "legacy_action": "analyze_multi",
                "meta": base_meta,
            }

        symbols = [str(p.get("symbol") or "") for p in payloads]

        # quote 快照
        if task_type == "quote":
            qf = run_quote_snapshots(repo_root=repo_root, payloads=payloads)
            fb = merge_facts_bundle(
                task_type="quote",
                response_mode="quick",
                user_question=user_q,
                symbols=symbols,
                market_facts=qf,
                evidence_sources=qf.get("evidence_sources") or [],
                risk_flags=qf.get("risk_flags") or [],
                trace={"executors": ["quote_snapshot"]},
            )
            chunks = _try_grounded_chunks(
                facts_bundle=fb, user_question=user_q, task_type="quote", response_mode="quick"
            )
            if not chunks:
                fallback_text = _format_fallback_reply(
                    task_type="quote", facts=qf, user_question=user_q
                )
                chunks = split_feishu_text(fallback_text)
            return {
                "task_type": "quote",
                "response_mode": "quick",
                "facts_bundle": fb,
                "final_text": "\n\n".join(chunks),
                "reply_chunks": chunks,
                "legacy_action": "analyze_multi",
                "meta": base_meta,
            }

        # compare 对比
        if task_type == "compare":
            cf = run_multi_asset_compare(repo_root=repo_root, payloads=payloads)
            fb = merge_facts_bundle(
                task_type="compare",
                response_mode="compare",
                user_question=user_q,
                symbols=symbols,
                market_facts={"compare_summary": {"rows": cf.get("rows")}},
                compare_facts=cf,
                evidence_sources=cf.get("evidence_sources") or [],
                risk_flags=cf.get("risk_flags") or [],
                trace={"executors": ["multi_asset_compare"]},
            )
            chunks = _try_grounded_chunks(
                facts_bundle=fb, user_question=user_q, task_type="compare", response_mode="compare"
            )
            if not chunks:
                fallback_text = _format_fallback_reply(
                    task_type="compare", facts=cf, user_question=user_q
                )
                chunks = split_feishu_text(fallback_text)
            return {
                "task_type": "compare",
                "response_mode": "compare",
                "facts_bundle": fb,
                "final_text": "\n\n".join(chunks),
                "reply_chunks": chunks,
                "legacy_action": "analyze_multi",
                "meta": base_meta,
            }

        # 多标的完整分析
        cf = run_multi_asset_compare(repo_root=repo_root, payloads=payloads)
        fb = merge_facts_bundle(
            task_type="analysis",
            response_mode="analysis",
            user_question=user_q,
            symbols=symbols,
            market_facts={"multi_compare": {"rows": cf.get("rows")}},
            compare_facts=cf,
            evidence_sources=cf.get("evidence_sources") or [],
            risk_flags=cf.get("risk_flags") or [],
            trace={"executors": ["multi_asset_compare"], "note": "digest_writer"},
        )
        chunks = _try_grounded_chunks(
            facts_bundle=fb, user_question=user_q, task_type="analysis", response_mode="analysis"
        )
        if not chunks:
            flat = _analyze_multiple_symbols_local(api_base_url=api_base_url, payloads=payloads)
            chunks = split_feishu_text(flat)
        return {
            "task_type": "analysis",
            "response_mode": "analysis",
            "facts_bundle": fb,
            "final_text": "\n\n".join(chunks),
            "reply_chunks": chunks,
            "legacy_action": "analyze_multi",
            "meta": base_meta,
        }

    # 5. 单标的 analyze 分支
    if action != "analyze":
        msg = "未知请求类型。"
        return {
            "task_type": task_type,
            "response_mode": response_mode,
            "facts_bundle": None,
            "final_text": msg,
            "reply_chunks": [msg],
            "legacy_action": action or "unknown",
            "meta": base_meta,
        }

    payload = route.get("payload") if isinstance(route.get("payload"), dict) else {}
    sym = str(payload.get("symbol") or "").strip()

    # 5.1 research 分支（直接消费 RAG 或本地研报）
    if task_type == "research":
        kw = str(payload.get("research_keyword") or sym or user_q or "").strip()

        # 先从本地 RAG 搜索
        hits = rag_index.query(kw, top_k=5, source_type_filter="research")
        if hits:
            rs_facts = {"ok": True, "keyword": kw, "items": []}
            for hit in hits:
                rs_facts["items"].append({
                    "title": hit.get("snippet", "").split("title=")[-1].split(" org=")[0] if "title=" in hit.get("snippet", "") else hit.get("snippet", "")[:50],
                    "source_path": hit.get("source_path"),
                    "score": hit.get("score"),
                })
        else:
            # RAG 未命中则实时检索
            rs = run_research_summary(keyword=kw, n=5)
            rs_facts = rs

        fb = merge_facts_bundle(
            task_type="research",
            response_mode="narrative",
            user_question=user_q,
            symbols=[sym] if sym else [],
            research_facts=rs_facts,
            evidence_sources=[{"source_path": "yanbaoke:search", "source_type": "research"}],
            risk_flags=["normal"] if rs_facts.get("ok") else ["research:degraded"],
            trace={"executors": ["research_summary"], "keyword": kw},
        )

        chunks = _try_grounded_chunks(
            facts_bundle=fb, user_question=user_q, task_type="research", response_mode="narrative"
        )
        if not chunks:
            fallback_text = _format_fallback_reply(
                task_type="research", facts=rs_facts, user_question=user_q
            )
            chunks = split_feishu_text(fallback_text)

        return {
            "task_type": "research",
            "response_mode": "narrative",
            "facts_bundle": fb,
            "final_text": "\n\n".join(chunks),
            "reply_chunks": chunks,
            "legacy_action": "analyze",
            "meta": {**base_meta, "research_keyword": kw},
        }

    # 5.2 quote 分支
    if task_type == "quote":
        qf = run_quote_snapshots(repo_root=repo_root, payloads=[payload])
        fb = merge_facts_bundle(
            task_type="quote",
            response_mode="quick",
            user_question=user_q,
            symbols=[sym],
            market_facts=qf,
            evidence_sources=qf.get("evidence_sources") or [],
            risk_flags=qf.get("risk_flags") or [],
            trace={"executors": ["quote_snapshot"]},
        )
        chunks = _try_grounded_chunks(
            facts_bundle=fb, user_question=user_q, task_type="quote", response_mode="quick"
        )
        if not chunks:
            fallback_text = _format_fallback_reply(
                task_type="quote", facts=qf, user_question=user_q
            )
            chunks = split_feishu_text(fallback_text)
        return {
            "task_type": "quote",
            "response_mode": "quick",
            "facts_bundle": fb,
            "final_text": "\n\n".join(chunks),
            "reply_chunks": chunks,
            "legacy_action": "analyze",
            "meta": base_meta,
        }

    # 5.3 单标的完整分析（HTTP 异步）
    try:
        task_id = submit_analysis_task(api_base_url=api_base_url, payload=payload)
        result = poll_analysis_result(api_base_url=api_base_url, task_id=task_id)
    except Exception as exc:
        err = f"分析失败：{exc}"
        return {
            "task_type": "analysis",
            "response_mode": "analysis",
            "facts_bundle": None,
            "final_text": err,
            "reply_chunks": split_feishu_text(err),
            "legacy_action": "analyze",
            "meta": {**base_meta, "error": str(exc)},
        }

    # 提取 narrative facts
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
        user_question=user_q,
        symbols=[sym],
        market_facts={"analysis_facts": narrative_facts},
        risk_flags=result.get("risk_flags") if isinstance(result.get("risk_flags"), list) else [],
        evidence_sources=result.get("evidence_sources") if isinstance(result.get("evidence_sources"), list) else [],
        trace={"executors": ["http_langgraph"], "task_id": task_id},
    )

    # 尝试多种输出方式
    chunks: list[str] | None = None
    if grounded_writer_enabled():
        chunks = _try_grounded_chunks(
            facts_bundle=fb, user_question=user_q, task_type="analysis", response_mode="analysis"
        )
    if not chunks and grounded_writer_enabled():
        try:
            body = write_legacy_narrative_if_enabled(facts=narrative_facts, user_question=user_q)
            chunks = split_feishu_text(body)
        except Exception:
            chunks = None
    if not chunks and fallback_to_template_reply_enabled():
        flat = _format_fixed_template_reply_local(result, user_question=user_q)
        chunks = split_feishu_text(flat)
    if not chunks:
        chunks = split_feishu_text("分析完成，但未能生成展示文本。")

    # 记录 output_refs 用于后续追问
    output_refs: dict[str, str] = {}
    ov_path = str(result.get("meta", {}).get("ai_overview_path") or "")
    if ov_path:
        output_refs["ai_overview_path"] = ov_path
    report_path = str(result.get("meta", {}).get("full_report_path") or "")
    if report_path:
        output_refs["full_report_path"] = report_path

    return {
        "task_type": "analysis",
        "response_mode": "analysis",
        "facts_bundle": fb,
        "final_text": "\n\n".join(chunks),
        "reply_chunks": chunks,
        "legacy_action": "analyze",
        "meta": {**base_meta, "task_id": task_id, "output_refs": output_refs},
    }