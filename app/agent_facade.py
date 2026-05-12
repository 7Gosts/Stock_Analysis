from __future__ import annotations

from pathlib import Path
from typing import Any

from app.analysis_task_client import poll_analysis_result, submit_analysis_task
from app.executors.facts_bundle import merge_facts_bundle
from app.executors.multi_asset_compare import run_multi_asset_compare
from app.executors.quote_snapshot import run_quote_snapshots
from app.executors.research_summary import run_research_summary
from app.formatters.feishu import split_feishu_text
from app.planner import plan_user_message
from app.writer import (
    extract_narrative_facts_from_agent_payload,
    fallback_to_template_reply_enabled,
    grounded_writer_enabled,
    safe_grounded_write,
    write_legacy_narrative_if_enabled,
)


def _repo_root_from_context(context: dict[str, Any]) -> Path:
    raw = context.get("repo_root")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).resolve()
    if isinstance(raw, Path):
        return raw.resolve()
    return Path(__file__).resolve().parents[1]


def _format_quote_fallback(market_facts: dict[str, Any]) -> str:
    lines: list[str] = ["【价格快照】"]
    for it in market_facts.get("items") or []:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "")
        lp = it.get("last_price")
        tr = str(it.get("trend") or "").strip()
        iv = str(it.get("interval") or "").strip()
        rg = str(it.get("regime_label") or "").strip()
        bits = [f"{sym} {iv}".strip()]
        if lp is not None:
            bits.append(f"最新约 {lp}")
        if tr:
            bits.append(f"倾向：{tr}")
        if rg:
            bits.append(f"状态：{rg}")
        lines.append(" · " + "，".join(x for x in bits if x))
    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines).strip()


def _format_compare_fallback(compare_facts: dict[str, Any]) -> str:
    lines: list[str] = ["【横向对比】"]
    for row in compare_facts.get("rows") or []:
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
    return "\n".join(lines).strip()


def _format_research_fallback(research_facts: dict[str, Any]) -> str:
    if not research_facts.get("ok"):
        return (
            f"研报检索暂不可用：{research_facts.get('error') or 'unknown'}。"
            "仅供技术分析与程序化演示，不构成投资建议。"
        )
    lines = [f"【研报线索】关键词：{research_facts.get('keyword') or ''}"]
    for it in research_facts.get("items") or []:
        if not isinstance(it, dict):
            continue
        t = str(it.get("title") or "").strip()
        org = str(it.get("org_name") or "").strip()
        if t:
            lines.append(f" · {t}" + (f"（{org}）" if org else ""))
    lines.append("以上为检索摘要线索，非官方观点背书。仅供技术分析与程序化演示，不构成投资建议。")
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
    """
    统一 Agent 入口：规划 → 执行 → 撰稿（可选）→ 飞书分段。
    context 需含：default_symbol, default_interval, conversation_context?, recent_messages?, api_base_url（分析类）.
    """
    _ = user_id
    ctx = context if isinstance(context, dict) else {}
    default_symbol = str(ctx.get("default_symbol") or "BTC_USDT").strip().upper()
    default_interval = str(ctx.get("default_interval") or "4h").strip().lower()
    api_base_url = str(ctx.get("api_base_url") or "http://127.0.0.1:8000").strip()
    conv = ctx.get("conversation_context") if isinstance(ctx.get("conversation_context"), dict) else None
    recent = ctx.get("recent_messages") if isinstance(ctx.get("recent_messages"), list) else None
    user_q = str(ctx.get("user_message_for_chunks") or text or "").strip()

    route = plan_user_message(
        text,
        default_symbol=default_symbol,
        default_interval=default_interval,
        context=conv,
        recent_messages=recent,
    )
    task_type = str(route.get("task_type") or "analysis")
    response_mode = str(route.get("response_mode") or "analysis")
    action = str(route.get("action") or "").strip().lower()

    base_meta: dict[str, Any] = {"route": dict(route), "channel": channel}

    if action == "clarify":
        msg = str(route.get("clarify_message") or "").strip()
        return {
            "task_type": "clarify",
            "response_mode": "quick",
            "facts_bundle": None,
            "final_text": msg,
            "reply_chunks": [msg] if msg else [],
            "legacy_action": "clarify",
            "meta": base_meta,
        }
    if action == "chat":
        msg = str(route.get("chat_reply") or "").strip()
        return {
            "task_type": "chat",
            "response_mode": "quick",
            "facts_bundle": None,
            "final_text": msg,
            "reply_chunks": [msg] if msg else [],
            "legacy_action": "chat",
            "meta": base_meta,
        }

    repo_root = _repo_root_from_context(ctx)

    if action == "analyze_multi":
        payloads = route.get("payloads") if isinstance(route.get("payloads"), list) else []
        payloads = [p for p in payloads if isinstance(p, dict)]
        if not payloads:
            return {
                "task_type": task_type,
                "response_mode": response_mode,
                "facts_bundle": None,
                "final_text": "",
                "reply_chunks": ["未解析到有效标的。"],
                "legacy_action": "analyze_multi",
                "meta": base_meta,
            }

        if task_type == "quote":
            qf = run_quote_snapshots(repo_root=repo_root, payloads=payloads)
            fb = merge_facts_bundle(
                task_type=task_type,
                response_mode=response_mode,
                user_question=user_q,
                symbols=[str(p.get("symbol") or "") for p in payloads],
                market_facts=qf,
                evidence_sources=qf.get("evidence_sources") or [],
                risk_flags=qf.get("risk_flags") or [],
                trace={"executors": ["quote_snapshot"]},
            )
            chunks = _try_grounded_chunks(
                facts_bundle=fb, user_question=user_q, task_type=task_type, response_mode=response_mode
            )
            if not chunks:
                body = _format_quote_fallback(qf)
                chunks = split_feishu_text(body)
            return {
                "task_type": task_type,
                "response_mode": response_mode,
                "facts_bundle": fb,
                "final_text": "\n\n".join(chunks),
                "reply_chunks": chunks,
                "legacy_action": "analyze_multi",
                "meta": base_meta,
            }

        if task_type == "compare":
            cf = run_multi_asset_compare(repo_root=repo_root, payloads=payloads)
            fb = merge_facts_bundle(
                task_type=task_type,
                response_mode=response_mode,
                user_question=user_q,
                symbols=[str(p.get("symbol") or "") for p in payloads],
                market_facts={"compare_summary": {"rows": cf.get("rows")}},
                compare_facts=cf,
                evidence_sources=cf.get("evidence_sources") or [],
                risk_flags=cf.get("risk_flags") or [],
                trace={"executors": ["multi_asset_compare"]},
            )
            chunks = _try_grounded_chunks(
                facts_bundle=fb, user_question=user_q, task_type=task_type, response_mode=response_mode
            )
            if not chunks:
                body = _format_compare_fallback(cf)
                chunks = split_feishu_text(body)
            return {
                "task_type": task_type,
                "response_mode": response_mode,
                "facts_bundle": fb,
                "final_text": "\n\n".join(chunks),
                "reply_chunks": chunks,
                "legacy_action": "analyze_multi",
                "meta": base_meta,
            }

        # 多标的完整分析：用对比执行器拉事实 + grounded 压缩，避免逐段硬拼模板
        cf = run_multi_asset_compare(repo_root=repo_root, payloads=payloads)
        fb = merge_facts_bundle(
            task_type="analysis",
            response_mode="analysis",
            user_question=user_q,
            symbols=[str(p.get("symbol") or "") for p in payloads],
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
            from app.feishu_bot_service import analyze_multiple_symbols

            flat = analyze_multiple_symbols(api_base_url=api_base_url, payloads=payloads)
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

    if action != "analyze":
        return {
            "task_type": task_type,
            "response_mode": response_mode,
            "facts_bundle": None,
            "final_text": "",
            "reply_chunks": [],
            "legacy_action": action or "unknown",
            "meta": base_meta,
        }

    payload = route.get("payload") if isinstance(route.get("payload"), dict) else {}
    sym = str(payload.get("symbol") or "").strip()

    if task_type == "research":
        kw = str(payload.get("research_keyword") or sym or user_q or "").strip()
        rs = run_research_summary(keyword=kw, n=5)
        fb = merge_facts_bundle(
            task_type="research",
            response_mode="narrative",
            user_question=user_q,
            symbols=[sym] if sym else [],
            research_facts=rs,
            evidence_sources=[{"source_path": "yanbaoke:search", "source_type": "research"}],
            risk_flags=["normal"] if rs.get("ok") else ["research:degraded"],
            trace={"executors": ["research_summary"]},
        )
        chunks = _try_grounded_chunks(
            facts_bundle=fb, user_question=user_q, task_type="research", response_mode="narrative"
        )
        if not chunks:
            chunks = split_feishu_text(_format_research_fallback(rs))
        return {
            "task_type": "research",
            "response_mode": "narrative",
            "facts_bundle": fb,
            "final_text": "\n\n".join(chunks),
            "reply_chunks": chunks,
            "legacy_action": "analyze",
            "meta": base_meta,
        }

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
            chunks = split_feishu_text(_format_quote_fallback(qf))
        return {
            "task_type": "quote",
            "response_mode": "quick",
            "facts_bundle": fb,
            "final_text": "\n\n".join(chunks),
            "reply_chunks": chunks,
            "legacy_action": "analyze",
            "meta": base_meta,
        }

    # 单标的完整分析：HTTP 异步 + grounded 或旧叙事/模板
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

    narrative_facts = extract_narrative_facts_from_agent_payload(result)
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
        from app.feishu_bot_service import format_fixed_template_reply

        flat = format_fixed_template_reply(result, user_question=user_q)
        chunks = split_feishu_text(flat)
    if not chunks:
        chunks = split_feishu_text("分析完成，但未能生成展示文本。")

    return {
        "task_type": "analysis",
        "response_mode": "analysis",
        "facts_bundle": fb,
        "final_text": "\n\n".join(chunks),
        "reply_chunks": chunks,
        "legacy_action": "analyze",
        "meta": {**base_meta, "task_id": task_id},
    }
