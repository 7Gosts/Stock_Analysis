from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests
from loguru import logger

from app.feishu_asset_catalog import (
    FeishuAssetCatalog,
    canonical_tradable_symbol,
    canonical_tradable_symbol_list,
    get_catalog_for_repo,
    normalize_provider,
)
from app.memory_store import JsonlMemoryStore, MemoryEvent
from config.runtime_config import get_analysis_config
from tools.deepseek.client import DeepSeekError, decide_message_action
from tools.feishu.client import FeishuError, get_tenant_access_token, send_text_message
_SEEN_MESSAGE_IDS: dict[str, float] = {}
_MESSAGE_DEDUP_TTL_SEC = 10 * 60
_SEEN_LOCK = threading.Lock()
_CONV_STATE: dict[str, dict[str, Any]] = {}
_CONV_LOCK = threading.Lock()
_CONV_TTL_SEC = 30 * 60
_DEFAULT_MEMORY_ROUNDS = 4
_BOT_START_TS_MS = int(time.time() * 1000)
_STARTUP_GRACE_MS = 5000


def parse_user_message(
    text: str,
    *,
    default_symbol: str,
    default_interval: str,
    provider: str | None = None,
    with_research: bool = False,
    research_keyword: str | None = None,
) -> dict[str, Any]:
    """仅提供会话默认值与原文 question；标的与周期由路由 LLM 决定，经 _land_* 校验后落地。"""
    raw = (text or "").strip()
    q = raw if raw else "请按固定模板输出当前行情，并结合我的问题意图解释。"
    cat = get_catalog_for_repo(_feishu_repo_root())
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


def analyze_multiple_symbols(*, api_base_url: str, payloads: list[dict[str, Any]]) -> str:
    cards: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        sym = str(payload.get("symbol") or "")
        try:
            task_id = submit_analysis_task(api_base_url=api_base_url, payload=payload)
            result = poll_analysis_result(api_base_url=api_base_url, task_id=task_id)
            cards.append(format_fixed_template_reply(result))
        except Exception as exc:
            cards.append(f"{sym} 分析失败：{exc}")
    return "\n\n".join(cards)


def _fmt_leg_price(leg: Any) -> str:
    """威科夫腿价位：支持 {price: x} 或直接数值。"""
    if leg is None:
        return "—"
    if isinstance(leg, dict):
        pv = leg.get("price")
        if pv is None:
            return "—"
        try:
            v = float(pv)
        except (TypeError, ValueError):
            return str(pv)
    else:
        try:
            v = float(leg)
        except (TypeError, ValueError):
            return str(leg)
    if abs(v) >= 1000:
        return f"{v:.2f}"
    return f"{v:.4f}"


def format_wyckoff_123_reply_lines(wyckoff: Any) -> list[str]:
    """从 stats.wyckoff_123_v1 快照生成飞书可读的两行：背景 + 123 形态要点。"""
    if not isinstance(wyckoff, dict) or not wyckoff:
        return []
    bg = wyckoff.get("background") if isinstance(wyckoff.get("background"), dict) else {}
    bias = str(bg.get("bias") or "neutral")
    bias_cn = {
        "long_only": "偏多（优先评估多头 123）",
        "short_only": "偏空（优先评估空头 123）",
        "neutral": "中性（未强制多空 123）",
    }.get(bias, bias)
    effort = str(bg.get("effort_result") or "—")
    state = str(bg.get("state") or "—")
    lines: list[str] = [
        f"- 威科夫背景：{bias_cn}；effort_result={effort}；state={state}",
    ]
    sel = wyckoff.get("selected_setup") if isinstance(wyckoff.get("selected_setup"), dict) else None
    pref = str(wyckoff.get("preferred_side") or "")
    aligned = wyckoff.get("aligned")
    if sel:
        side = str(sel.get("side") or "?")
        p1, p2, p3 = _fmt_leg_price(sel.get("p1")), _fmt_leg_price(sel.get("p2")), _fmt_leg_price(sel.get("p3"))
        lines.append(
            f"- 威科夫123（{side}）：P1={p1}，P2={p2}，P3={p3}；"
            f"entry={sel.get('entry')}，stop={sel.get('stop')}，tp1={sel.get('tp1')}，tp2={sel.get('tp2')}，triggered={sel.get('triggered')}"
        )
    else:
        lines.append(
            f"- 威科夫123：当前未选出与背景一致的程式单（preferred_side={pref or '无'}，aligned={aligned}）"
        )
    return lines


def format_journal_notice_lines(result_payload: dict[str, Any]) -> list[str]:
    """本轮若写入新台账候选（过门控），生成飞书追加行。"""
    meta = result_payload.get("meta") if isinstance(result_payload.get("meta"), dict) else {}
    raw = meta.get("journal")
    j = raw if isinstance(raw, dict) else {}
    entries = j.get("new_entries")
    if not isinstance(entries, list) or not entries:
        return []
    lines: list[str] = [
        "- 台账：本轮新增候选（结构快照，非交易所成交）",
    ]
    for e in entries:
        if not isinstance(e, dict):
            continue
        sym = str(e.get("symbol") or "?")
        iv = str(e.get("interval") or "?")
        pt = str(e.get("plan_type") or "tactical")
        dire = str(e.get("direction") or "?")
        st = str(e.get("status") or "?")
        ep = e.get("entry_price")
        ez = e.get("entry_zone")
        sl = e.get("stop_loss")
        tps = e.get("take_profit_levels")
        rr = e.get("rr")
        ok = str(e.get("order_kind_cn") or "")
        iid = str(e.get("idea_id") or "")
        tp_txt = "—"
        if isinstance(tps, list) and tps:
            t2 = tps[1] if len(tps) > 1 else "—"
            tp_txt = f"{tps[0]}/{t2}"
        zone_txt = ""
        if isinstance(ez, list) and len(ez) >= 2:
            zone_txt = f" 区[{ez[0]},{ez[1]}]"
        lines.append(
            f"  · {sym} {iv} {pt} {dire} | {st} | entry={ep}{zone_txt} | stop={sl} | "
            f"tp={tp_txt} | rr={rr} | {ok} | idea_id={iid}"
        )
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


def _format_ma_snapshot_feishu_line(ms: dict[str, Any]) -> str:
    """单行兼容（测试/旧调用）；飞书主路径用 `_ma_system_block_lines`。"""
    parts: list[str] = []
    if "sma20" in ms:
        parts.append(f"SMA20={_fmt_ma_px(ms['sma20'])}")
    if "sma60" in ms:
        parts.append(f"SMA60={_fmt_ma_px(ms['sma60'])}")
    for period_key, val_key in (
        ("ma_short_period", "sma_short"),
        ("ma_mid_period", "sma_mid"),
        ("ma_long_period", "sma_long"),
    ):
        if period_key in ms and val_key in ms:
            try:
                n = int(ms[period_key])
            except (TypeError, ValueError):
                continue
            parts.append(f"SMA{n}={_fmt_ma_px(ms[val_key])}")
    pct_bits: list[str] = []
    for label, key in (("短", "p_ma_short_pct"), ("中", "p_ma_mid_pct"), ("长", "p_ma_long_pct")):
        if key in ms and ms[key] is not None:
            try:
                pct_bits.append(f"{label}{float(ms[key]):+.2f}%")
            except (TypeError, ValueError):
                pass
    if pct_bits:
        parts.append("现价距均线 " + "，".join(pct_bits))
    return "；".join(parts) if parts else "—"


def _ma_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _ma_reading_sentence(ms: dict[str, Any]) -> str:
    """基于 ma_snapshot 数值的规则读数（非模型生成）。"""
    sv = _ma_float(ms.get("sma_short"))
    mv = _ma_float(ms.get("sma_mid"))
    lv = _ma_float(ms.get("sma_long"))
    ps = _ma_float(ms.get("p_ma_short_pct"))
    pm = _ma_float(ms.get("p_ma_mid_pct"))
    pl = _ma_float(ms.get("p_ma_long_pct"))
    chunks: list[str] = []
    rels: list[str] = []
    if ps is not None:
        rels.append("短期均线上方" if ps >= 0 else "短期均线下方")
    if pm is not None:
        rels.append("中期均线上方" if pm >= 0 else "中期均线下方")
    if pl is not None:
        rels.append("长期均线上方" if pl >= 0 else "长期均线下方")
    if rels:
        chunks.append("；".join(rels) + "。")
    if sv is not None and mv is not None and lv is not None:
        if sv > mv > lv:
            chunks.append("三档均线呈短>中>长，偏多头扩散结构。")
        elif sv < mv < lv:
            chunks.append("三档均线呈短<中<长，偏空头扩散结构。")
        else:
            chunks.append("三档均线非单调多/空排列，常见于震荡或趋势切换。")
    return "".join(chunks)


def _ma_system_block_lines(ms: dict[str, Any]) -> list[str]:
    """飞书：均线系统分区（多行，便于扫读）。"""
    if not isinstance(ms, dict) or not ms:
        return []
    lines: list[str] = ["【均线系统】"]
    bits20: list[str] = []
    if "sma20" in ms and ms["sma20"] is not None:
        bits20.append(f"SMA20={_fmt_ma_px(ms['sma20'])}")
    if "sma60" in ms and ms["sma60"] is not None:
        bits20.append(f"SMA60={_fmt_ma_px(ms['sma60'])}")
    if bits20:
        lines.append("  · 宽基（阶段过滤）：" + "，".join(bits20))
    triple: list[tuple[str, str, str]] = []
    for period_key, val_key, pct_key in (
        ("ma_short_period", "sma_short", "p_ma_short_pct"),
        ("ma_mid_period", "sma_mid", "p_ma_mid_pct"),
        ("ma_long_period", "sma_long", "p_ma_long_pct"),
    ):
        if period_key not in ms or val_key not in ms:
            continue
        try:
            n = int(ms[period_key])
        except (TypeError, ValueError):
            continue
        px = _fmt_ma_px(ms[val_key])
        pctf = _ma_float(ms.get(pct_key))
        if pctf is not None:
            triple.append((str(n), px, f"{pctf:+.2f}%"))
        else:
            triple.append((str(n), px, ""))
    if triple:
        lines.append("  · 操作档（短/中/长周期）：")
        for n, px, pct in triple:
            if pct:
                lines.append(f"      SMA{n}={px}，现价相对该均线 {pct}")
            else:
                lines.append(f"      SMA{n}={px}")
    reading = _ma_reading_sentence(ms)
    if reading:
        lines.append("  · 读数：" + reading)
    return lines


def format_fixed_template_reply(result_payload: dict[str, Any], *, user_question: str | None = None) -> str:
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
    parts.extend(
        [
            "【关键位与触发】",
            f"  · Fib / 区间：{tpl['关键位(Fib)']}",
            f"  · 触发条件：{tpl['触发条件']}",
            f"  · 失效条件：{tpl['失效条件']}",
            "",
            "【风险与复核】",
            f"  · 风险点：{risk_text}",
            f"  · 下次复核：{tpl['下次复核时间']}",
            "",
        ]
    )
    ds = str(analysis.get("decision_source") or "").strip()
    if ds:
        parts.extend(["【决策】", f"  · 来源：{ds}", ""])
    meta = result_payload.get("meta") if isinstance(result_payload.get("meta"), dict) else {}
    note = meta.get("llm_warning") or meta.get("langgraph_warning")
    if isinstance(note, str) and note.strip():
        parts.extend(["【执行旁注】", f"  · {note.strip()[:240]}", ""])
    wy_lines = format_wyckoff_123_reply_lines(analysis.get("wyckoff_123_v1"))
    if wy_lines:
        parts.append("【威科夫 123】")
        for w in wy_lines:
            if w.startswith("- "):
                parts.append("  · " + w[2:])
            else:
                parts.append("  " + w.strip())
        parts.append("")
    jlines = format_journal_notice_lines(result_payload)
    if jlines:
        parts.append("【台账】")
        for i, jl in enumerate(jlines):
            if i == 0 and jl.startswith("- 台账："):
                parts.append("  · " + jl.replace("- 台账：", "", 1).strip())
                continue
            parts.append(jl if jl.startswith("  ") else "  " + jl.lstrip())
    return "\n".join(parts).rstrip() + "\n"


def submit_analysis_task(*, api_base_url: str, payload: dict[str, Any], timeout_sec: float = 20.0) -> str:
    url = f"{api_base_url.rstrip('/')}/agent/analyze"
    resp = requests.post(url, json=payload, timeout=timeout_sec)
    resp.raise_for_status()
    obj = resp.json()
    task_id = str(obj.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError(f"提交分析任务失败: {obj}")
    return task_id


def poll_analysis_result(
    *,
    api_base_url: str,
    task_id: str,
    timeout_sec: float = 120.0,
    poll_interval_sec: float = 2.0,
) -> dict[str, Any]:
    url = f"{api_base_url.rstrip('/')}/agent/tasks/{task_id}"
    start = time.time()
    while True:
        resp = requests.get(url, timeout=20.0)
        resp.raise_for_status()
        obj = resp.json()
        status = str(obj.get("status") or "")
        if status == "completed":
            result = obj.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"任务完成但 result 非对象: {obj}")
            return result
        if status == "failed":
            raise RuntimeError(f"分析任务失败: {obj.get('error')}")
        if time.time() - start > timeout_sec:
            raise TimeoutError(f"轮询分析任务超时: {task_id}")
        time.sleep(max(0.5, poll_interval_sec))


def extract_event_text(data: Any) -> str:
    content = (
        getattr(getattr(getattr(data, "event", None), "message", None), "content", "")
        or ""
    )
    if not isinstance(content, str):
        return ""
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return content
    if isinstance(obj, dict) and isinstance(obj.get("text"), str):
        return obj["text"]
    deep = _extract_text_from_obj(obj)
    if deep:
        return deep
    return content


def extract_sender_open_id(data: Any) -> str:
    sender_id = getattr(getattr(getattr(data, "event", None), "sender", None), "sender_id", None)
    return str(getattr(sender_id, "open_id", "") or "").strip()


def extract_message_id(data: Any) -> str:
    message = getattr(getattr(data, "event", None), "message", None)
    return str(getattr(message, "message_id", "") or "").strip()


def should_process_message(message_id: str, *, now_ts: float | None = None) -> bool:
    if not message_id:
        return True
    now = time.time() if now_ts is None else float(now_ts)
    with _SEEN_LOCK:
        expired = [mid for mid, ts in _SEEN_MESSAGE_IDS.items() if (now - ts) > _MESSAGE_DEDUP_TTL_SEC]
        for mid in expired:
            _SEEN_MESSAGE_IDS.pop(mid, None)
        if message_id in _SEEN_MESSAGE_IDS:
            return False
        _SEEN_MESSAGE_IDS[message_id] = now
        return True


def extract_message_type(data: Any) -> str:
    message = getattr(getattr(data, "event", None), "message", None)
    return str(getattr(message, "message_type", "") or "").strip().lower()


def extract_sender_type(data: Any) -> str:
    sender = getattr(getattr(data, "event", None), "sender", None)
    return str(getattr(sender, "sender_type", "") or "").strip().lower()


def extract_message_create_time_ms(data: Any) -> int | None:
    message = getattr(getattr(data, "event", None), "message", None)
    raw = str(getattr(message, "create_time", "") or "").strip()
    if not raw:
        return None
    try:
        ts = int(raw)
    except ValueError:
        return None
    # 部分平台可能给秒级时间戳，统一转为毫秒
    if ts < 10_000_000_000:
        ts = ts * 1000
    return ts


def is_stale_message(data: Any) -> bool:
    cts = extract_message_create_time_ms(data)
    if cts is None:
        return False
    return cts < (_BOT_START_TS_MS - _STARTUP_GRACE_MS)


def load_feishu_settings() -> dict[str, str]:
    cfg = get_analysis_config()
    node = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    memory_node = node.get("memory") if isinstance(node.get("memory"), dict) else {}
    app_id = str(node.get("app_id") or "").strip()
    app_secret = str(node.get("app_secret") or "").strip()
    default_symbol = str(node.get("default_symbol") or "BTC_USDT").strip().upper()
    default_interval = str(node.get("default_interval") or "4h").strip().lower()
    llm_memory_rounds = _to_int(node.get("llm_memory_rounds"), default=_DEFAULT_MEMORY_ROUNDS, minimum=0, maximum=12)
    memory_enabled = _to_bool(memory_node.get("enabled"), default=True)
    memory_backend = str(memory_node.get("backend") or "jsonl").strip().lower() or "jsonl"
    memory_file = str(memory_node.get("memory_file") or "output/feishu_memory.jsonl").strip()
    memory_history_days = _to_int(memory_node.get("history_days"), default=30, minimum=1, maximum=365)
    memory_max_messages = _to_int(memory_node.get("max_messages_per_user"), default=2000, minimum=100, maximum=20000)
    memory_long_term_top_k = _to_int(memory_node.get("long_term_top_k"), default=3, minimum=1, maximum=10)
    return {
        "app_id": app_id,
        "app_secret": app_secret,
        "default_symbol": default_symbol,
        "default_interval": default_interval,
        "llm_memory_rounds": str(llm_memory_rounds),
        "memory_enabled": "1" if memory_enabled else "0",
        "memory_backend": memory_backend,
        "memory_file": memory_file,
        "memory_history_days": str(memory_history_days),
        "memory_max_messages_per_user": str(memory_max_messages),
        "memory_long_term_top_k": str(memory_long_term_top_k),
    }


def build_event_handler(
    *,
    api_base_url: str,
    app_id: str,
    app_secret: str,
    default_symbol: str,
    default_interval: str,
    llm_memory_rounds: int = _DEFAULT_MEMORY_ROUNDS,
    memory_store: JsonlMemoryStore | None = None,
    long_term_top_k: int = 3,
) -> Any:
    lark = _import_lark()

    def _process_message(*, sender_open_id: str, text: str) -> None:
        _log_event("recv", open_id=sender_open_id, text=text)
        ctx = get_conversation_state(sender_open_id, memory_store=memory_store)
        recent_messages = get_recent_messages(sender_open_id, rounds=llm_memory_rounds, memory_store=memory_store)
        long_term_notes = get_long_term_memory(
            sender_open_id,
            query=text,
            top_k=long_term_top_k,
            memory_store=memory_store,
        )
        for note in long_term_notes:
            recent_messages.append({"role": "assistant", "text": f"[长期记忆] {note}"})
        append_conversation_message(sender_open_id, role="user", text=text, memory_store=memory_store)

        route = route_user_message(
            text,
            default_symbol=default_symbol,
            default_interval=default_interval,
            context=ctx,
            recent_messages=recent_messages,
        )
        _log_event("route", open_id=sender_open_id, action=str(route.get("action") or "unknown"))
        update_conversation_state(sender_open_id, route=route, raw_text=text)
        if route.get("action") == "clarify":
            reply = str(route.get("clarify_message") or build_ambiguous_reply(text))
            try:
                token = get_tenant_access_token(app_id=app_id, app_secret=app_secret)
                send_text_message(
                    tenant_access_token=token,
                    receive_id=sender_open_id,
                    text=reply,
                    receive_id_type="open_id",
                )
            except FeishuError:
                pass
            _log_event("reply", open_id=sender_open_id, action="clarify", text=reply)
            append_conversation_message(
                sender_open_id,
                role="assistant",
                text=reply,
                action="clarify",
                memory_store=memory_store,
            )
            return
        if route.get("action") == "chat":
            reply = build_chat_reply(route.get("chat_reply"))
            try:
                token = get_tenant_access_token(app_id=app_id, app_secret=app_secret)
                send_text_message(
                    tenant_access_token=token,
                    receive_id=sender_open_id,
                    text=reply,
                    receive_id_type="open_id",
                )
            except FeishuError:
                pass
            _log_event("reply", open_id=sender_open_id, action="chat", text=reply)
            append_conversation_message(
                sender_open_id,
                role="assistant",
                text=reply,
                action="chat",
                memory_store=memory_store,
            )
            return
        if route.get("action") == "analyze_multi":
            payloads = route.get("payloads")
            if not isinstance(payloads, list):
                payloads = []
            reply = analyze_multiple_symbols(api_base_url=api_base_url, payloads=payloads)
            sym_join = ",".join(
                str(p.get("symbol") or "").strip().upper() for p in payloads if isinstance(p, dict) and str(p.get("symbol") or "").strip()
            )
            interval_multi = default_interval
            first: dict[str, Any] = {}
            for p in payloads:
                if not isinstance(p, dict):
                    continue
                if not first:
                    first = p
                iv = str(p.get("interval") or "").strip().lower()
                if iv:
                    interval_multi = iv
            try:
                token = get_tenant_access_token(app_id=app_id, app_secret=app_secret)
                send_text_message(
                    tenant_access_token=token,
                    receive_id=sender_open_id,
                    text=reply,
                    receive_id_type="open_id",
                )
            except FeishuError:
                pass
            _log_event("reply", open_id=sender_open_id, action="analyze_multi", text=reply)
            append_conversation_message(
                sender_open_id,
                role="assistant",
                text=reply,
                action="analyze",
                symbol=sym_join,
                interval=interval_multi,
                question=str(text or ""),
                provider=str(first.get("provider") or "") or None,
                memory_store=memory_store,
            )
            update_conversation_state(
                sender_open_id,
                route={
                    "action": "analyze",
                    "payload": {
                        "symbol": str(first.get("symbol") or ""),
                        "interval": interval_multi,
                        "question": text,
                        "provider": str(first.get("provider") or ""),
                    },
                },
                raw_text=text,
            )
            return

        payload = route.get("payload")
        if not isinstance(payload, dict):
            payload = parse_user_message(
                text,
                default_symbol=default_symbol,
                default_interval=default_interval,
            )
        try:
            task_id = submit_analysis_task(api_base_url=api_base_url, payload=payload)
            _log_event("analyze_submit", open_id=sender_open_id, task_id=task_id, symbol=str(payload.get("symbol") or ""), interval=str(payload.get("interval") or ""))
            result = poll_analysis_result(api_base_url=api_base_url, task_id=task_id)
            reply = format_fixed_template_reply(result, user_question=text)
        except Exception as exc:
            reply = f"分析失败：{exc}"
        try:
            token = get_tenant_access_token(app_id=app_id, app_secret=app_secret)
            send_text_message(
                tenant_access_token=token,
                receive_id=sender_open_id,
                text=reply,
                receive_id_type="open_id",
            )
        except FeishuError:
            # 回消息失败时不再抛出，避免影响 ws 主循环
            pass
        _log_event("reply", open_id=sender_open_id, action="analyze", text=reply)
        append_conversation_message(
            sender_open_id,
            role="assistant",
            text=reply,
            action="analyze",
            symbol=str(payload.get("symbol") or ""),
            interval=str(payload.get("interval") or ""),
            question=str(payload.get("question") or ""),
            provider=str(payload.get("provider") or "") or None,
            memory_store=memory_store,
        )

    def _on_message(data: Any) -> None:
        # 只处理用户发送的 text 消息，避免非文本事件/自身消息导致循环触发
        sender_type = extract_sender_type(data)
        if sender_type != "user":
            return
        if extract_message_type(data) != "text":
            return
        # 忽略机器人重启前的积压消息，避免“翻历史”批量回复
        if is_stale_message(data):
            return
        sender_open_id = extract_sender_open_id(data)
        if not sender_open_id:
            return
        message_id = extract_message_id(data)
        if not should_process_message(message_id):
            return
        text = extract_event_text(data)
        th = threading.Thread(
            target=_process_message,
            kwargs={"sender_open_id": sender_open_id, "text": text},
            daemon=True,
        )
        th.start()

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_on_message)
        .build()
    )


def run_feishu_bot(*, api_base_url: str = "http://127.0.0.1:8000", log_level: Any = None) -> None:
    lark = _import_lark()
    if log_level is None:
        log_level = lark.LogLevel.INFO
    settings = load_feishu_settings()
    app_id = settings["app_id"]
    app_secret = settings["app_secret"]
    if not app_id or not app_secret:
        raise RuntimeError("缺少飞书凭证：请在 config/analysis_defaults.yaml 中配置 feishu.app_id/app_secret")
    memory_store = build_memory_store(settings)
    event_handler = build_event_handler(
        api_base_url=api_base_url,
        app_id=app_id,
        app_secret=app_secret,
        default_symbol=settings["default_symbol"],
        default_interval=settings["default_interval"],
        llm_memory_rounds=_to_int(settings.get("llm_memory_rounds"), default=_DEFAULT_MEMORY_ROUNDS, minimum=0, maximum=12),
        memory_store=memory_store,
        long_term_top_k=_to_int(settings.get("memory_long_term_top_k"), default=3, minimum=1, maximum=10),
    )
    cli = lark.ws.Client(app_id, app_secret, event_handler=event_handler, log_level=log_level)
    cli.start()


def build_memory_store(settings: dict[str, str]) -> JsonlMemoryStore | None:
    if str(settings.get("memory_enabled") or "1") != "1":
        return None
    backend = str(settings.get("memory_backend") or "jsonl").strip().lower()
    if backend != "jsonl":
        return None
    raw_path = str(settings.get("memory_file") or "output/feishu_memory.jsonl").strip()
    p = Path(raw_path).expanduser()
    if not p.is_absolute():
        p = (Path(__file__).resolve().parents[1] / p).resolve()
    store = JsonlMemoryStore(
        path=p,
        max_messages_per_user=_to_int(settings.get("memory_max_messages_per_user"), default=2000, minimum=100, maximum=20000),
        history_days=_to_int(settings.get("memory_history_days"), default=30, minimum=1, maximum=365),
    )
    store.compact()
    return store


def _import_lark():
    try:
        import lark_oapi as lark  # type: ignore
    except Exception as exc:
        raise RuntimeError("未安装 lark-oapi，请先执行 `pip install -r requirements.txt`。") from exc
    return lark


def _extract_text_from_obj(obj: Any) -> str:
    texts: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            tag = str(node.get("tag") or "").lower()
            if tag == "at":
                return
            txt = node.get("text")
            if isinstance(txt, str) and txt.strip():
                texts.append(txt.strip())
            for v in node.values():
                _walk(v)
            return
        if isinstance(node, list):
            for it in node:
                _walk(it)

    _walk(obj)
    return " ".join(texts).strip()


def _feishu_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _feishu_asset_catalog() -> FeishuAssetCatalog:
    return get_catalog_for_repo(_feishu_repo_root())


def build_router_symbol_clarify(raw: str, catalog: FeishuAssetCatalog) -> str:
    sample = "、".join(sorted(catalog.allowed_symbols)[:12]) if catalog.allowed_symbols else "（配置中暂无标的）"
    tip = (
        f"路由给出的代码不在当前机器人支持的标的列表中（收到：{raw.strip() or '空'}）。\n"
        f"请从 market_config 已配置的标的中选择并重试（示例）：{sample}"
    )
    return tip


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


def _to_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        v = int(value)
    except Exception:
        return default
    if v < minimum:
        return minimum
    if v > maximum:
        return maximum
    return v


def build_ambiguous_reply(text: str) -> str:
    raw = (text or "").strip()
    if raw:
        return (
            f"我没看懂你的问题（收到：{raw}）。\n"
            "你可以这样问：\n"
            "1) 看 NVDA 1d 或 BTC_USDT 4h\n"
            "2) AU9999 1d 黄金节奏\n"
            "3) 贵州茅台 1d 带研报\n"
            "4) ETH 1d 左侧能不能开多？"
        )
    return (
        "我没看懂你的问题。\n"
        "你可以这样问：\n"
        "1) 看 NVDA 1d 或 BTC_USDT 4h\n"
        "2) AU9999 1d 黄金节奏\n"
        "3) 贵州茅台 1d 带研报\n"
        "4) ETH 1d 左侧能不能开多？"
    )


def build_missing_fields_reply(text: str) -> str:
    raw = (text or "").strip()
    if raw:
        return (
            f"我暂时不能直接执行分析（收到：{raw}）。\n"
            "请补充：标的代码（须在 market_config 中）+ 周期（15m/30m/1h/4h/1d）。\n"
            "例如：看 AAPL 1d；AU9999 4h；BTC_USDT 1d 带研报。"
        )
    return "请补充标的与周期（例如 NVDA 1d、AU9999 1d、BTC_USDT 4h）。"


def build_router_error_reply(text: str) -> str:
    raw = (text or "").strip()
    if raw:
        return (
            f"我现在没能稳定解析你的请求（收到：{raw}）。\n"
            "请明确给出标的与周期后重试，例如：600519.SH 1d 或 ETH_USDT 4h。"
        )
    return "我现在没能稳定解析请求，请明确给出标的与周期后重试。"


def route_user_message(
    text: str,
    *,
    default_symbol: str,
    default_interval: str,
    context: dict[str, Any] | None = None,
    recent_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """意图由路由 LLM（decide_message_action）决定；代码只做标的表、周期合法值与 payload 落地。"""
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
        return {"action": "clarify", "clarify_message": build_missing_fields_reply(raw)}

    payload = parse_user_message(
        raw,
        default_symbol=base_symbol,
        default_interval=base_interval,
        provider=base_provider,
    )

    try:
        routed = decide_message_action(
            text=raw,
            default_symbol=base_symbol,
            default_interval=base_interval,
            recent_messages=recent_messages,
            tradable_assets=catalog.tradable_assets_for_prompt(),
        )
    except Exception as exc:
        _log_router_llm_failure(exc)
        return {"action": "clarify", "clarify_message": build_router_error_reply(raw)}

    _log_routed_llm_preview(routed)

    action = str(routed.get("action") or "").strip().lower()
    if action == "clarify":
        clarify_msg = str(routed.get("clarify_message") or "").strip()
        return {
            "action": "clarify",
            "clarify_message": clarify_msg or build_ambiguous_reply(raw),
        }
    if action == "chat":
        chat_reply = str(routed.get("chat_reply") or "").strip()
        if chat_reply:
            return {"action": "chat", "chat_reply": chat_reply}
        return {"action": "chat"}

    if action not in {"analyze"}:
        return {"action": "clarify", "clarify_message": build_ambiguous_reply(raw)}

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
        return {"action": "analyze_multi", "payloads": payloads}

    single = canonical_tradable_symbol(str(routed.get("symbol") or ""), catalog)
    if single is None and len(routed_symbols) == 1:
        single = routed_symbols[0]
    if single is None:
        return {"action": "clarify", "clarify_message": build_router_symbol_clarify(raw, catalog)}

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
    return {"action": "analyze", "payload": payload}


def get_conversation_state(sender_open_id: str, *, memory_store: JsonlMemoryStore | None = None) -> dict[str, Any]:
    key = str(sender_open_id or "").strip()
    if not key:
        return {}
    now = time.time()
    with _CONV_LOCK:
        expired = [uid for uid, st in _CONV_STATE.items() if (now - float(st.get("updated_ts") or 0.0)) > _CONV_TTL_SEC]
        for uid in expired:
            _CONV_STATE.pop(uid, None)
        cur = _CONV_STATE.get(key) or {}
        out = dict(cur)
    if memory_store:
        profile = memory_store.load_last_profile(open_id=key)
        if profile.get("symbol") and not out.get("last_symbol"):
            out["last_symbol"] = profile["symbol"]
        if profile.get("interval") and not out.get("last_interval"):
            out["last_interval"] = profile["interval"]
        if profile.get("question") and not out.get("last_question"):
            out["last_question"] = profile["question"]
        if profile.get("provider") and not out.get("last_provider"):
            out["last_provider"] = profile["provider"]
    return out


def get_recent_messages(
    sender_open_id: str,
    *,
    rounds: int,
    memory_store: JsonlMemoryStore | None = None,
) -> list[dict[str, str]]:
    key = str(sender_open_id or "").strip()
    if not key or rounds <= 0:
        return []
    if memory_store:
        rows = memory_store.load_recent(open_id=key, limit=2 * rounds)
        out: list[dict[str, str]] = []
        for it in rows:
            role = str(it.get("role") or "").strip().lower()
            text = str(it.get("text") or "").strip()
            if role in {"user", "assistant"} and text:
                out.append({"role": role, "text": text})
        return out
    with _CONV_LOCK:
        st = _CONV_STATE.get(key) or {}
        rows = st.get("recent_messages")
        if not isinstance(rows, list):
            return []
        sliced = rows[-(2 * rounds) :]
        out: list[dict[str, str]] = []
        for it in sliced:
            if not isinstance(it, dict):
                continue
            role = str(it.get("role") or "").strip().lower()
            text = str(it.get("text") or "").strip()
            if role in {"user", "assistant"} and text:
                out.append({"role": role, "text": text})
        return out


def get_long_term_memory(
    sender_open_id: str,
    *,
    query: str,
    top_k: int,
    memory_store: JsonlMemoryStore | None = None,
) -> list[str]:
    if not memory_store:
        return []
    rows = memory_store.search_long_term(
        open_id=str(sender_open_id or "").strip(),
        query=query,
        top_k=max(1, int(top_k)),
    )
    out: list[str] = []
    for it in rows:
        text = str(it.get("text") or "").strip()
        symbol = str(it.get("symbol") or "").strip().upper()
        interval = str(it.get("interval") or "").strip().lower()
        if symbol and interval:
            out.append(f"{symbol} {interval}: {text}")
        elif symbol:
            out.append(f"{symbol}: {text}")
        else:
            out.append(text)
    return [x for x in out if x]


def append_conversation_message(
    sender_open_id: str,
    *,
    role: str,
    text: str,
    action: str | None = None,
    symbol: str | None = None,
    interval: str | None = None,
    question: str | None = None,
    provider: str | None = None,
    memory_store: JsonlMemoryStore | None = None,
) -> None:
    key = str(sender_open_id or "").strip()
    if not key:
        return
    r = str(role or "").strip().lower()
    t = str(text or "").strip()
    if r not in {"user", "assistant"} or not t:
        return
    now = time.time()
    with _CONV_LOCK:
        st = dict(_CONV_STATE.get(key) or {})
        rows = st.get("recent_messages")
        if not isinstance(rows, list):
            rows = []
        rows.append({"role": r, "text": t, "ts": now})
        st["recent_messages"] = rows[-24:]
        st["updated_ts"] = now
        _CONV_STATE[key] = st
    if memory_store:
        memory_store.append_event(
            MemoryEvent(
                open_id=key,
                role=r,
                text=t,
                action=action,
                symbol=(symbol or None),
                interval=(interval or None),
                question=(question or None),
                provider=(provider or None),
                created_ts=now,
            )
        )


def update_conversation_state(sender_open_id: str, *, route: dict[str, Any], raw_text: str) -> None:
    key = str(sender_open_id or "").strip()
    if not key:
        return
    now = time.time()
    action = str(route.get("action") or "").strip().lower()
    with _CONV_LOCK:
        st = dict(_CONV_STATE.get(key) or {})
        st["updated_ts"] = now
        st["last_user_text"] = str(raw_text or "").strip()
        st["last_action"] = action
        if action == "analyze":
            payload = route.get("payload") if isinstance(route.get("payload"), dict) else {}
            st["last_symbol"] = str(payload.get("symbol") or st.get("last_symbol") or "").strip().upper()
            st["last_interval"] = str(payload.get("interval") or st.get("last_interval") or "").strip().lower()
            st["last_question"] = str(payload.get("question") or st.get("last_question") or "").strip()
            pv = str(payload.get("provider") or "").strip().lower()
            if pv in {"tickflow", "gateio", "goldapi"}:
                st["last_provider"] = pv
            st["pending_clarify"] = False
        elif action == "analyze_multi":
            payloads = route.get("payloads") if isinstance(route.get("payloads"), list) else []
            first = payloads[0] if payloads and isinstance(payloads[0], dict) else {}
            st["last_symbol"] = str(first.get("symbol") or st.get("last_symbol") or "").strip().upper()
            st["last_interval"] = str(first.get("interval") or st.get("last_interval") or "").strip().lower()
            st["last_question"] = str(first.get("question") or st.get("last_question") or "").strip()
            pv = str(first.get("provider") or "").strip().lower()
            if pv in {"tickflow", "gateio", "goldapi"}:
                st["last_provider"] = pv
            st["pending_clarify"] = False
        elif action == "clarify":
            st["pending_clarify"] = True
        else:
            st["pending_clarify"] = False
        _CONV_STATE[key] = st


def build_chat_reply(chat_reply: Any) -> str:
    cleaned = str(chat_reply or "").strip()
    if cleaned:
        return cleaned
    return (
        "可以闲聊呀 🙂\n"
        "我也可以看股票/黄金/加密：例如“NVDA 1d”“AU9999 1d”“BTC_USDT 4h”，或“贵州茅台 1d 带研报”。"
    )


def _log_event(stage: str, **kwargs: Any) -> None:
    safe_items: list[str] = []
    for k, v in kwargs.items():
        s = str(v).replace("\n", " ").strip()
        if k == "text":
            s = _shorten(s, 140)
        safe_items.append(f"{k}={s}")
    msg = " ".join(safe_items)
    logger.info("[FeishuBot] {} {}", stage, msg.strip())


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _route_debug_enabled() -> bool:
    return os.getenv("FEISHU_ROUTE_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def _log_router_llm_failure(exc: BaseException) -> None:
    """DeepSeek 路由失败：默认一行；FEISHU_ROUTE_DEBUG=1 时附带 traceback。"""
    msg = str(exc).replace("\n", " ").strip()
    logger.warning(
        "[FeishuBot] route_llm_error exc_type={} msg={}",
        type(exc).__name__,
        _shorten(msg, 480),
    )
    if _route_debug_enabled():
        logger.opt(exception=exc).debug("[FeishuBot] route_llm traceback")


def _log_routed_llm_preview(routed: dict[str, Any]) -> None:
    if not _route_debug_enabled():
        return
    if not isinstance(routed, dict):
        return
    preview = {
        k: routed.get(k)
        for k in (
            "action",
            "symbol",
            "symbols",
            "interval",
            "question",
            "provider",
            "with_research",
            "research_keyword",
            "clarify_message",
        )
        if k in routed
    }
    line = json.dumps(preview, ensure_ascii=False)
    logger.debug("[FeishuBot] route_debug llm_fields={}", _shorten(line, 600))
