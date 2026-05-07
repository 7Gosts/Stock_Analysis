from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests
from loguru import logger

from app.memory_store import JsonlMemoryStore, MemoryEvent
from config.runtime_config import get_analysis_config
from tools.deepseek.client import DeepSeekError, decide_message_action
from tools.feishu.client import FeishuError, get_tenant_access_token, send_text_message

# 路由 LLM 输出裸代码时，落地层映射到 market_config 中的 Gate 对（须在白名单内）
_BARE_TO_PAIR: dict[str, str] = {
    "BTC": "BTC_USDT",
    "ETH": "ETH_USDT",
    "SOL": "SOL_USDT",
}
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
) -> dict[str, Any]:
    """仅提供会话默认值与原文 question；标的与周期由路由 LLM 决定，经 _land_* 校验后落地。"""
    raw = (text or "").strip()
    q = raw if raw else "请按固定模板输出当前行情，并结合我的问题意图解释。"
    return {
        "symbol": default_symbol,
        "provider": "gateio",
        "interval": default_interval,
        "question": q,
        "use_rag": True,
        "use_llm_decision": True,
    }


def analyze_multiple_symbols(
    *,
    api_base_url: str,
    symbols: list[str],
    interval: str,
    user_text: str,
) -> str:
    cards: list[str] = []
    for symbol in symbols:
        payload = {
            "symbol": symbol,
            "provider": "gateio",
            "interval": interval,
            "question": user_text,
            "use_rag": True,
            "use_llm_decision": True,
        }
        try:
            task_id = submit_analysis_task(api_base_url=api_base_url, payload=payload)
            result = poll_analysis_result(api_base_url=api_base_url, task_id=task_id)
            cards.append(format_fixed_template_reply(result))
        except Exception as exc:
            cards.append(f"{symbol} {interval} 分析失败：{exc}")
    return "\n\n".join(cards)


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
    return (
        f"{symbol} {interval} 分析结果\n"
        f"- 综合倾向：{tpl['综合倾向']}\n"
        f"- 关键位(Fib)：{tpl['关键位(Fib)']}\n"
        f"- 触发条件：{tpl['触发条件']}\n"
        f"- 失效条件：{tpl['失效条件']}\n"
        f"- 风险点：{risk_text}\n"
        f"- 下次复核时间：{tpl['下次复核时间']}"
    )


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
            symbols: list[str] = []
            interval_multi = default_interval
            for p in payloads:
                if not isinstance(p, dict):
                    continue
                s = str(p.get("symbol") or "").strip().upper()
                iv = str(p.get("interval") or "").strip().lower()
                if s:
                    symbols.append(s)
                if iv:
                    interval_multi = iv
            reply = analyze_multiple_symbols(
                api_base_url=api_base_url,
                symbols=symbols,
                interval=interval_multi,
                user_text=text,
            )
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
                symbol=",".join(symbols),
                interval=interval_multi,
                question=str(text or ""),
                memory_store=memory_store,
            )
            update_conversation_state(
                sender_open_id,
                route={
                    "action": "analyze",
                    "payload": {
                        "symbol": symbols[0] if symbols else "",
                        "interval": interval_multi,
                        "question": text,
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


def _load_allowed_gateio_symbols() -> frozenset[str]:
    """Gate 加密标的白名单（与 market_config.json 中 market=CRYPTO 的 symbol 一致）。"""
    path = _feishu_repo_root() / "config" / "market_config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return frozenset()
    assets = data.get("assets")
    if not isinstance(assets, list):
        return frozenset()
    out: list[str] = []
    for a in assets:
        if not isinstance(a, dict):
            continue
        if str(a.get("market") or "").strip().upper() != "CRYPTO":
            continue
        sym = str(a.get("symbol") or "").strip().upper()
        if sym:
            out.append(sym)
    s = frozenset(out)
    if not s:
        return frozenset({"BTC_USDT", "ETH_USDT", "SOL_USDT"})
    return s


def _canonical_gate_symbol(value: str, allowed: frozenset[str]) -> str | None:
    """将路由输出归一为白名单内的交易对；无法识别则 None。"""
    v = (value or "").strip().upper()
    if not v:
        return None
    if v in allowed:
        return v
    mapped = _BARE_TO_PAIR.get(v)
    if mapped and mapped in allowed:
        return mapped
    return None


def _land_router_symbol_list(value: Any, allowed: frozenset[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for it in value:
        c = _canonical_gate_symbol(str(it or ""), allowed)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def build_router_symbol_clarify(raw: str, allowed: frozenset[str]) -> str:
    sample = "、".join(sorted(allowed)[:12]) if allowed else "（配置中暂无 CRYPTO 标的）"
    tip = (
        f"路由给出的交易对不在当前机器人支持的 Gate 加密列表中（收到：{raw.strip() or '空'}）。\n"
        f"请从下列标的中选择并重试（示例）：{sample}"
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
            "1) 看 BTC_USDT 4h\n"
            "2) ETH 1d 左侧能不能开多？\n"
            "3) SOL_USDT 15m 现在是突破还是假突破？"
        )
    return (
        "我没看懂你的问题。\n"
        "你可以这样问：\n"
        "1) 看 BTC_USDT 4h\n"
        "2) ETH 1d 左侧能不能开多？\n"
        "3) SOL_USDT 15m 现在是突破还是假突破？"
    )


def build_missing_fields_reply(text: str) -> str:
    raw = (text or "").strip()
    if raw:
        return (
            f"我暂时不能直接执行分析（收到：{raw}）。\n"
            "请补充完整参数：交易对 + 周期。\n"
            "例如：看 BTC_USDT 4h，或 ETH_USDT 1d 左侧能不能开多？"
        )
    return "请补充完整参数：交易对 + 周期（例如 BTC_USDT 4h）。"


def build_router_error_reply(text: str) -> str:
    raw = (text or "").strip()
    if raw:
        return (
            f"我现在没能稳定解析你的请求（收到：{raw}）。\n"
            "请明确给出交易对与周期后重试，例如：ETH_USDT 4h。"
        )
    return "我现在没能稳定解析请求，请明确给出交易对与周期后重试。"


def route_user_message(
    text: str,
    *,
    default_symbol: str,
    default_interval: str,
    context: dict[str, Any] | None = None,
    recent_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """意图由路由 LLM（decide_message_action）决定；代码只做白名单、周期合法值与 payload 落地。"""
    raw = (text or "").strip()
    ctx = context if isinstance(context, dict) else {}
    allowed = _load_allowed_gateio_symbols()
    ds = str(default_symbol or "").strip().upper()
    default_canon = _canonical_gate_symbol(ds, allowed)
    if default_canon is None and allowed:
        default_canon = sorted(allowed)[0]
    if default_canon is None:
        default_canon = ds or "BTC_USDT"

    ctx_sym = str(ctx.get("last_symbol") or "").strip()
    base_symbol = _canonical_gate_symbol(ctx_sym, allowed) or default_canon
    base_interval = _normalize_interval(str(ctx.get("last_interval") or default_interval), default_interval)

    if not raw:
        return {"action": "clarify", "clarify_message": build_missing_fields_reply(raw)}

    payload = parse_user_message(
        raw,
        default_symbol=base_symbol,
        default_interval=base_interval,
    )

    try:
        routed = decide_message_action(
            text=raw,
            default_symbol=base_symbol,
            default_interval=base_interval,
            recent_messages=recent_messages,
            allowed_gateio_symbols=sorted(allowed),
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

    routed_symbols = _land_router_symbol_list(routed.get("symbols"), allowed)
    routed_interval = str(routed.get("interval") or "").strip().lower()
    routed_question = str(routed.get("question") or "").strip()

    if len(routed_symbols) > 1:
        payloads: list[dict[str, Any]] = []
        for sym in routed_symbols:
            payloads.append(
                {
                    "symbol": sym,
                    "provider": "gateio",
                    "interval": _normalize_interval(routed_interval, payload["interval"]),
                    "question": routed_question or payload["question"],
                    "use_rag": True,
                    "use_llm_decision": True,
                }
            )
        return {"action": "analyze_multi", "payloads": payloads}

    single = _canonical_gate_symbol(str(routed.get("symbol") or ""), allowed)
    if single is None and len(routed_symbols) == 1:
        single = routed_symbols[0]
    if single is None:
        return {"action": "clarify", "clarify_message": build_router_symbol_clarify(raw, allowed)}

    payload["symbol"] = single
    payload["interval"] = _normalize_interval(routed_interval or str(payload.get("interval") or ""), payload["interval"])
    q = str(routed.get("question") or "").strip()
    if q:
        payload["question"] = q
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
        "我也可以继续看币：例如“看 BTC_USDT 4h”或“ETH 1d 左侧能不能开多？”"
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
        for k in ("action", "symbol", "symbols", "interval", "question", "clarify_message")
        if k in routed
    }
    line = json.dumps(preview, ensure_ascii=False)
    logger.debug("[FeishuBot] route_debug llm_fields={}", _shorten(line, 600))
