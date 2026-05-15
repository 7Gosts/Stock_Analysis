"""HTTP API 服务层（三层重构 + 统一 Core 版）。

职责收敛为（adapter 层）：
1. 请求校验
2. Request schema 转换
3. 调用统一 agent core
4. 返回统一 response schema

统一接口：
- /agent/run：统一 agent 入口（支持 chat/research/followup/analysis/quote/compare）
- /health：健康检查

文档参考：
- docs/AGENT_CORE_UNIFICATION_PLAN.md §6.3
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.agent_schemas import AgentRequest, AgentResponse
from app.agent_core import handle_request


# ============ 统一接口模型 ============

class AgentRunRequest(BaseModel):
    """统一 Agent 请求模型。

    注：default_symbol / default_interval 现在仅作为 fallback 常量，
    实际路由由 planner 从 session_state + market_config + ROUTER_POLICY 在运行时推导。
    """
    text: str = Field(..., description="用户输入文本")
    session_id: str | None = Field(default=None, description="会话ID，可选")
    user_id: str | None = Field(default=None, description="用户ID，可选")
    default_symbol: str = Field(default="BTC_USDT", description="默认标的（fallback，实际由 planner 推导）")
    default_interval: str = Field(default="4h", description="默认周期（fallback，实际由 planner 推导）")
    channel: str = Field(default="http", description="渠道标识")

    # 会话上下文
    recent_messages: list[dict[str, str]] | None = Field(default=None, description="历史消息（可选）")
    risk_profile: str | None = Field(default=None, description="风险画像")

    # 执行选项
    use_rag: bool = Field(default=True)
    rag_top_k: int = Field(default=5, ge=1, le=20)
    api_base_url: str = Field(default="http://127.0.0.1:8000")


class AgentRunResponse(BaseModel):
    """统一 Agent 响应模型。"""
    task_type: str
    response_mode: str
    reply_text: str
    reply_chunks: list[str] = Field(default_factory=list)
    facts_bundle: dict[str, Any] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_ts: float


# ============ FastAPI 应用 ============

app = FastAPI(title="Stock Analysis Agent API", version="0.2.0")


# ============ 健康检查 ============

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ============ 统一 Agent 入口 ============

@app.post("/agent/run", response_model=AgentRunResponse)
def run_agent(req: AgentRunRequest) -> AgentRunResponse:
    """统一 Agent 入口。

    支持：chat / quote / compare / analysis / research / followup

    流程：
    1. 构造 AgentRequest
    2. 调用 agent_core.handle_request
    3. 返回 AgentRunResponse
    """
    request_id = uuid4().hex
    session_id = req.session_id or request_id

    context: dict[str, Any] = {}
    if req.recent_messages:
        context["recent_messages"] = req.recent_messages
    if req.risk_profile:
        context["risk_profile"] = req.risk_profile

    options: dict[str, Any] = {
        "use_rag": req.use_rag,
        "rag_top_k": req.rag_top_k,
        "api_base_url": req.api_base_url,
    }

    agent_request = AgentRequest(
        channel=str(req.channel or "http"),
        session_id=session_id,
        text=req.text,
        user_id=req.user_id,
        default_symbol=req.default_symbol,
        default_interval=req.default_interval,
        context=context,
        options=options,
    )

    agent_response = handle_request(agent_request)

    return AgentRunResponse(
        task_type=agent_response.task_type,
        response_mode=agent_response.response_mode,
        reply_text=agent_response.reply_text,
        reply_chunks=agent_response.reply_chunks,
        facts_bundle=agent_response.facts_bundle,
        meta=agent_response.meta,
        created_ts=agent_response.created_ts,
    )