"""飞书 Bot 服务层（三层重构 + 统一 Core 版）。

职责收敛为（adapter 层）：
1. 消息接收（飞书 WebSocket）
2. 消息去重
3. session/open_id 映射
4. 回复发送 / 分段
5. 调用统一 agent core

禁止承担（core 层职责）：
1. 主路由逻辑
2. 追问解析主逻辑
3. 本地 RAG 决策
4. 事实优先级选择
5. 平台无关 fallback 生成

文档参考：
- docs/AGENT_CORE_UNIFICATION_PLAN.md §4.1
- docs/AGENT_CORE_UNIFICATION_EXECUTION_PROMPT.md §3
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from app.agent_schemas import AgentRequest, AgentResponse, DEFAULT_CLARIFY_MESSAGE
from app.agent_core import handle_request
from app.memory_store import JsonlMemoryStore, MemoryEvent
from app.session_state import get_global_session_store
from config.runtime_config import get_analysis_config
from tools.feishu.client import FeishuError, get_tenant_access_token, send_text_message


# 消息去重
_SEEN_MESSAGE_IDS: dict[str, float] = {}
_SEEN_LOCK = threading.Lock()
_MESSAGE_DEDUP_TTL_SEC = 10 * 60

# Bot 启动时间戳
_BOT_START_TS_MS = int(time.time() * 1000)
_STARTUP_GRACE_MS = 5000

# 飞书历史轮数
_DEFAULT_MEMORY_ROUNDS = 4
_MAX_FEISHU_MESSAGE_CHARS = 4000


def load_feishu_settings() -> dict[str, str]:
    """加载飞书配置。

    注：default_symbol / default_interval 已迁移到 runtime route context，
    由 planner 从 session_state + market_config + 常量推导，不再从 YAML 读取。
    """
    cfg = get_analysis_config()
    node = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    memory_node = node.get("memory") if isinstance(node.get("memory"), dict) else {}

    app_id = str(node.get("app_id") or "").strip()
    app_secret = str(node.get("app_secret") or "").strip()

    # 默认标的和周期由 planner 在运行时从 session_state + market_config 推导
    # 这里使用常量作为 fallback（仅用于 adapter 初始化，实际路由由 planner 决定）
    default_symbol = "BTC_USDT"
    default_interval = "4h"

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


def build_memory_store(settings: dict[str, str]) -> JsonlMemoryStore | None:
    """构建飞书历史存储（JSONL）。"""
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


def build_event_handler(
    *,
    app_id: str,
    app_secret: str,
    default_symbol: str,
    default_interval: str,
    llm_memory_rounds: int = _DEFAULT_MEMORY_ROUNDS,
    memory_store: JsonlMemoryStore | None = None,
) -> Any:
    """构建飞书事件处理器（adapter 版）。

    只做：
    1. 消息接收
    2. 构造 AgentRequest
    3. 调用统一 agent_core
    4. 发送 AgentResponse
    5. 写入飞书历史
    """
    lark = _import_lark()

    def _process_message(*, sender_open_id: str, text: str) -> None:
        """处理单条消息（adapter 模式）。"""
        _log_event("recv", open_id=sender_open_id, text=text)

        # 1. 获取飞书历史（用于指代消解，不作为事实源）
        recent_messages = get_recent_messages(
            sender_open_id, rounds=llm_memory_rounds, memory_store=memory_store
        )

        # 2. 获取会话状态（由 agent_core 管理）
        session_store = get_global_session_store()
        session_state = session_store.get(sender_open_id)

        # 3. 构造统一请求
        request = AgentRequest.from_feishu(
            open_id=sender_open_id,
            text=text,
            default_symbol=default_symbol,
            default_interval=default_interval,
            session_state=session_state,
            recent_messages=recent_messages,
        )

        # 4. 调用统一 agent core
        response = handle_request(request)

        # 5. 发送回复（必须非空）
        send_reply_or_fallback(
            app_id=app_id,
            app_secret=app_secret,
            sender_open_id=sender_open_id,
            reply_chunks=response.reply_chunks,
            fallback_text=response.reply_text or DEFAULT_CLARIFY_MESSAGE,
        )

        # 6. 写入飞书历史（第三层）
        action = response.meta.get("route", {}).get("action") or response.task_type
        symbols = response.meta.get("route", {}).get("task_plan", {}).get("symbols") or []
        sym_join = symbols[0] if symbols else None

        if response.reply_text:
            append_conversation_message(
                sender_open_id,
                role="assistant",
                text=response.reply_text,
                action=str(action),
                symbol=sym_join,
                interval=response.meta.get("route", {}).get("task_plan", {}).get("interval"),
                question=text,
                memory_store=memory_store,
            )

        append_conversation_message(
            sender_open_id,
            role="user",
            text=text,
            memory_store=memory_store,
        )

        _log_event("reply", open_id=sender_open_id, action=str(action), text=response.reply_text[:200])

    def _on_message(data: Any) -> None:
        """飞书消息事件回调。"""
        sender_type = extract_sender_type(data)
        if sender_type != "user":
            return
        if extract_message_type(data) != "text":
            return
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


def send_reply_or_fallback(
    *,
    app_id: str,
    app_secret: str,
    sender_open_id: str,
    reply_chunks: list[str],
    fallback_text: str,
) -> None:
    """发送回复，若 reply_chunks 为空则发送 fallback_text。

    文档要求：空结果也必须显式回复。
    """
    chunks = [str(c).strip() for c in reply_chunks if str(c).strip()]
    if not chunks:
        chunks = [fallback_text]

    try:
        token = get_tenant_access_token(app_id=app_id, app_secret=app_secret)
        for ch in chunks:
            if not ch:
                continue
            send_text_message(
                tenant_access_token=token,
                receive_id=sender_open_id,
                text=ch,
                receive_id_type="open_id",
            )
    except FeishuError as exc:
        logger.warning("[FeishuAdapter] send_reply_error err={}", exc)


def run_feishu_bot(*, api_base_url: str = "http://127.0.0.1:8000", log_level: Any = None) -> None:
    """启动飞书 Bot（adapter 版）。"""
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
        app_id=app_id,
        app_secret=app_secret,
        default_symbol=settings["default_symbol"],
        default_interval=settings["default_interval"],
        llm_memory_rounds=_to_int(settings.get("llm_memory_rounds"), default=_DEFAULT_MEMORY_ROUNDS, minimum=0, maximum=12),
        memory_store=memory_store,
    )

    cli = lark.ws.Client(app_id, app_secret, event_handler=event_handler, log_level=log_level)
    cli.start()


# ============ 辅助函数 ============

def extract_event_text(data: Any) -> str:
    content = getattr(getattr(getattr(data, "event", None), "message", None), "content", "") or ""
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
    if ts < 10_000_000_000:
        ts = ts * 1000
    return ts


def is_stale_message(data: Any) -> bool:
    cts = extract_message_create_time_ms(data)
    if cts is None:
        return False
    return cts < (_BOT_START_TS_MS - _STARTUP_GRACE_MS)


def get_recent_messages(
    sender_open_id: str,
    *,
    rounds: int,
    memory_store: JsonlMemoryStore | None = None,
) -> list[dict[str, str]]:
    """获取飞书历史（只用于指代消解）。"""
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
    return []


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
    """写入飞书历史。"""
    key = str(sender_open_id or "").strip()
    if not key:
        return
    r = str(role or "").strip().lower()
    t = str(text or "").strip()
    if r not in {"user", "assistant"} or not t:
        return
    if memory_store:
        memory_store.append_event(
            MemoryEvent(
                open_id=key,
                role=r,
                text=t,
                action=action,
                symbol=symbol,
                interval=interval,
                question=question,
                created_ts=time.time(),
            )
        )


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


def _log_event(stage: str, **kwargs: Any) -> None:
    safe_items: list[str] = []
    for k, v in kwargs.items():
        s = str(v).replace("\n", " ").strip()
        if k == "text":
            s = _shorten(s, 140)
        safe_items.append(f"{k}={s}")
    msg = " ".join(safe_items)
    logger.info("[FeishuAdapter] {} {}", stage, msg.strip())


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 3)] + "..."