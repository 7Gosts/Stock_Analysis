"""统一 Agent Core 入口：Request/Response Schema 定义。

所有入口（飞书、CLI、HTTP）最终都转换为统一请求/响应对象。

设计原则：
1. channel 区分平台
2. session_id 统一会话标识
3. context 包含会话状态和历史消息
4. options 包含执行参数

文档参考：docs/AGENT_CORE_UNIFICATION_PLAN.md §3
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal
import time


ChannelType = Literal["feishu", "cli", "http", "test"]
TaskType = Literal["chat", "quote", "compare", "analysis", "research", "followup", "sim_account"]
ResponseMode = Literal["quick", "compare", "analysis", "narrative", "followup", "sim_account"]


class AgentErrorCode(str, Enum):
    """Agent 错误码枚举。

    分三类：
    - route_*: 路由阶段错误
    - execute_*: 执行阶段错误
    - infra_*: 基础设施错误
    """
    # Route 阶段错误
    route_missing_symbols = "route_missing_symbols"
    route_invalid_symbol = "route_invalid_symbol"
    route_missing_chat_reply = "route_missing_chat_reply"
    route_empty_message = "route_empty_message"
    route_unknown_action = "route_unknown_action"

    # Followup 阶段错误
    followup_missing_symbol = "followup_missing_symbol"
    followup_output_missing = "followup_output_missing"

    # Execute 阶段错误
    execute_analysis_failed = "execute_analysis_failed"
    execute_quote_failed = "execute_quote_failed"
    execute_provider_timeout = "execute_provider_timeout"
    execute_writer_failed = "execute_writer_failed"

    # Infra 错误
    db_unavailable = "db_unavailable"
    analysis_backend_unavailable = "analysis_backend_unavailable"
    rag_unavailable = "rag_unavailable"

    # 其他
    unknown = "unknown"


class AgentErrorStage(str, Enum):
    """错误发生阶段。"""
    route = "route"
    execute = "execute"
    infra = "infra"
    unknown = "unknown"


@dataclass(frozen=True)
class AgentError:
    """结构化错误信息。

    用于填充 AgentResponse.meta，供后续 repair loop 使用。
    """
    code: AgentErrorCode
    stage: AgentErrorStage
    recoverable: bool
    message: str
    termination_reason: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_meta_dict(self) -> dict[str, Any]:
        """转换为 meta 字典格式。"""
        return {
            "error_code": self.code.value,
            "error_stage": self.stage.value,
            "recoverable": self.recoverable,
            "termination_reason": self.termination_reason,
            "error_message": self.message,
            "error_context": self.context,
        }


@dataclass
class AgentRequest:
    """统一 Agent 请求对象。

    所有平台入口最终都转换为该对象，然后调用 agent_core 处理。

    注：default_symbol / default_interval 现在仅作为 fallback 常量，
    实际路由由 planner 从 session_state + market_config + ROUTER_POLICY 在运行时推导。
    """
    channel: ChannelType
    session_id: str  # 平台侧会话ID（飞书 open_id、CLI session_id、HTTP request_id）
    text: str  # 用户原始输入
    user_id: str | None = None  # 平台用户ID，可空
    default_symbol: str = "BTC_USDT"  # fallback 常量（实际由 planner 运行时推导）
    default_interval: str = "4h"  # fallback 常量（实际由 planner 运行时推导）

    # 会话上下文
    context: dict[str, Any] = field(default_factory=dict)
    # context 可包含：
    # - recent_messages: list[dict[str, str]] 飞书历史消息
    # - session_state: SessionState 会话状态对象
    # - risk_profile: str | None 风险画像
    # - output_refs: dict[str, str] 上轮产物路径

    # 执行选项
    options: dict[str, Any] = field(default_factory=dict)
    # options 可包含：
    # - use_rag: bool
    # - rag_top_k: int
    # - use_llm_decision: bool
    # - api_base_url: str (HTTP 分析时需要)
    # - provider: str
    # - repo_root: str | Path

    # 元数据
    created_ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "session_id": self.session_id,
            "text": self.text,
            "user_id": self.user_id,
            "default_symbol": self.default_symbol,
            "default_interval": self.default_interval,
            "context": self.context,
            "options": self.options,
            "created_ts": self.created_ts,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentRequest":
        return cls(
            channel=str(d.get("channel") or "http"),
            session_id=str(d.get("session_id") or ""),
            text=str(d.get("text") or ""),
            user_id=d.get("user_id"),
            default_symbol=str(d.get("default_symbol") or "BTC_USDT"),
            default_interval=str(d.get("default_interval") or "4h"),
            context=dict(d.get("context") or {}),
            options=dict(d.get("options") or {}),
            created_ts=float(d.get("created_ts") or time.time()),
        )

    @classmethod
    def from_feishu(
        cls,
        *,
        open_id: str,
        text: str,
        default_symbol: str,
        default_interval: str,
        session_state: Any | None = None,
        recent_messages: list[dict[str, str]] | None = None,
        rag_index: Any | None = None,
    ) -> "AgentRequest":
        """从飞书事件构造请求。"""
        ctx: dict[str, Any] = {}
        if session_state is not None:
            ctx["session_state"] = session_state
        if recent_messages:
            ctx["recent_messages"] = recent_messages
        if rag_index is not None:
            ctx["rag_index"] = rag_index

        return cls(
            channel="feishu",
            session_id=open_id,
            text=text,
            user_id=open_id,
            default_symbol=default_symbol,
            default_interval=default_interval,
            context=ctx,
        )

    @classmethod
    def from_http(
        cls,
        *,
        request_id: str,
        text: str,
        default_symbol: str = "BTC_USDT",
        default_interval: str = "4h",
        user_id: str | None = None,
        api_base_url: str = "http://127.0.0.1:8000",
        use_rag: bool = True,
        rag_top_k: int = 5,
    ) -> "AgentRequest":
        """从 HTTP 请求构造请求。"""
        return cls(
            channel="http",
            session_id=request_id,
            text=text,
            user_id=user_id,
            default_symbol=default_symbol,
            default_interval=default_interval,
            context={},
            options={
                "api_base_url": api_base_url,
                "use_rag": use_rag,
                "rag_top_k": rag_top_k,
            },
        )

    @classmethod
    def from_cli(
        cls,
        *,
        text: str,
        default_symbol: str = "BTC_USDT",
        default_interval: str = "4h",
        session_id: str | None = None,
    ) -> "AgentRequest":
        """从 CLI 输入构造请求。"""
        import uuid
        sid = session_id or uuid.uuid4().hex[:8]

        return cls(
            channel="cli",
            session_id=sid,
            text=text,
            default_symbol=default_symbol,
            default_interval=default_interval,
            context={},
        )


@dataclass
class AgentResponse:
    """统一 Agent 响应对象。

    所有平台入口都接收该对象，然后做各自平台的展示/发送处理。
    """
    task_type: TaskType
    response_mode: ResponseMode
    reply_text: str  # 给用户的最终回复（完整文本）
    reply_chunks: list[str] = field(default_factory=list)  # 分段回复（飞书分段发送）

    # 事实包（供 guardrail / writer 消费）
    facts_bundle: dict[str, Any] | None = None

    # 元数据
    meta: dict[str, Any] = field(default_factory=dict)
    # meta 可包含：
    # - route: dict 路由结果
    # - output_refs: dict[str, str] 本轮产物路径（供下一轮追问使用）
    # - evidence_sources: list 证据来源
    # - warnings: list[str] 警告信息
    # - error: str | None 错误信息

    created_ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "response_mode": self.response_mode,
            "reply_text": self.reply_text,
            "reply_chunks": self.reply_chunks,
            "facts_bundle": self.facts_bundle,
            "meta": self.meta,
            "created_ts": self.created_ts,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentResponse":
        return cls(
            task_type=str(d.get("task_type") or "chat"),
            response_mode=str(d.get("response_mode") or "quick"),
            reply_text=str(d.get("reply_text") or ""),
            reply_chunks=list(d.get("reply_chunks") or []),
            facts_bundle=d.get("facts_bundle"),
            meta=dict(d.get("meta") or {}),
            created_ts=float(d.get("created_ts") or time.time()),
        )

    @classmethod
    def chat(
        cls,
        *,
        message: str,
        meta: dict[str, Any] | None = None,
    ) -> "AgentResponse":
        """构造闲聊响应。"""
        return cls(
            task_type="chat",
            response_mode="quick",
            reply_text=message,
            reply_chunks=[message] if message else [],
            facts_bundle=None,
            meta=meta or {},
        )

    @classmethod
    def error(
        cls,
        *,
        error_msg: str,
        fallback_text: str | None = None,
        meta: dict[str, Any] | None = None,
        agent_error: AgentError | None = None,
    ) -> "AgentResponse":
        """构造错误响应（必须有 fallback_text）。

        Returns:
            AgentResponse 包含结构化错误信息在 meta 中
        """
        reply = fallback_text or "我这次没有稳定生成回复。你可以补一句标的/周期，或让我重新分析。"
        m = meta or {}
        m["error"] = error_msg
        if agent_error is not None:
            m.update(agent_error.to_meta_dict())
        return cls(
            task_type="chat",
            response_mode="quick",
            reply_text=reply,
            reply_chunks=[reply],
            facts_bundle=None,
            meta=m,
        )

# 统一 chat-style fallback 文案（空结果也必须显式回复）
DEFAULT_CHAT_FALLBACK_MESSAGE = (
    "我这次没有稳定拿到可回答的上下文。"
    "你可以补一句标的/周期，或让我重新分析。"
)

DEFAULT_FALLBACK_MESSAGE = (
    "分析完成，但未能生成展示文本。"
    "仅供技术分析与程序化演示，不构成投资建议。"
)


# ============ 错误码默认属性映射 ============

ERROR_CODE_DEFAULTS: dict[AgentErrorCode, dict[str, Any]] = {
    AgentErrorCode.route_missing_symbols: {
        "stage": AgentErrorStage.route,
        "recoverable": True,
        "termination_reason": "路由未识别有效标的",
    },
    AgentErrorCode.route_invalid_symbol: {
        "stage": AgentErrorStage.route,
        "recoverable": True,
        "termination_reason": "路由识别的标的不在可交易列表中",
    },
    AgentErrorCode.route_missing_chat_reply: {
        "stage": AgentErrorStage.route,
        "recoverable": False,
        "termination_reason": "chat 路由缺少回复文本",
    },
    AgentErrorCode.route_empty_message: {
        "stage": AgentErrorStage.route,
        "recoverable": False,
        "termination_reason": "用户输入为空",
    },
    AgentErrorCode.route_unknown_action: {
        "stage": AgentErrorStage.route,
        "recoverable": False,
        "termination_reason": "路由返回未知 action",
    },
    AgentErrorCode.followup_missing_symbol: {
        "stage": AgentErrorStage.route,
        "recoverable": True,
        "termination_reason": "追问路由缺少标的",
    },
    AgentErrorCode.followup_output_missing: {
        "stage": AgentErrorStage.execute,
        "recoverable": True,
        "termination_reason": "追问所需的输出产物不存在",
    },
    AgentErrorCode.execute_analysis_failed: {
        "stage": AgentErrorStage.execute,
        "recoverable": True,
        "termination_reason": "分析执行失败",
    },
    AgentErrorCode.execute_quote_failed: {
        "stage": AgentErrorStage.execute,
        "recoverable": True,
        "termination_reason": "价格快照执行失败",
    },
    AgentErrorCode.execute_provider_timeout: {
        "stage": AgentErrorStage.execute,
        "recoverable": True,
        "termination_reason": "分析数据获取超时",
    },
    AgentErrorCode.execute_writer_failed: {
        "stage": AgentErrorStage.execute,
        "recoverable": True,
        "termination_reason": "分析结果生成失败",
    },
    AgentErrorCode.db_unavailable: {
        "stage": AgentErrorStage.infra,
        "recoverable": True,
        "termination_reason": "PostgreSQL 数据库不可用",
    },
    AgentErrorCode.analysis_backend_unavailable: {
        "stage": AgentErrorStage.infra,
        "recoverable": True,
        "termination_reason": "分析后端服务不可用",
    },
    AgentErrorCode.rag_unavailable: {
        "stage": AgentErrorStage.infra,
        "recoverable": True,
        "termination_reason": "RAG 索引不可用",
    },
    AgentErrorCode.unknown: {
        "stage": AgentErrorStage.unknown,
        "recoverable": False,
        "termination_reason": "未知错误",
    },
}


def make_agent_error(
    code: AgentErrorCode,
    message: str = "",
    context: dict[str, Any] | None = None,
) -> AgentError:
    """根据错误码快速构建结构化错误。

    自动从 ERROR_CODE_DEFAULTS 中填充默认属性。
    """
    defaults = ERROR_CODE_DEFAULTS.get(code, {})
    return AgentError(
        code=code,
        stage=defaults.get("stage", AgentErrorStage.unknown),
        recoverable=defaults.get("recoverable", False),
        message=message,
        termination_reason=defaults.get("termination_reason", ""),
        context=context or {},
    )