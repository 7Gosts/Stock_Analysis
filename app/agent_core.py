"""统一 Agent Core 入口。

这是项目的智能体主入口，所有平台（飞书、CLI、HTTP）都应调用它。

流程：AgentRequest → intent_pipeline/planner → unified graph → AgentResponse
所有 task_type 统一经由 agent_graph 的 capability→compose→session→compact 管道，
不再有平台分支或 facade 委托。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from loguru import logger

from app.agent_schemas import (
    AgentRequest,
    AgentResponse,
    AgentError,
    AgentErrorCode,
    AgentErrorStage,
    TaskType,
    ResponseMode,
)
from app.planner import plan_user_message, log_routed_preview, AgentRoutingError
from app.rag_index import get_or_create_rag_index, RagIndex
from app.session_state import SessionState, SessionStateStore, get_global_session_store
from app.agent_graph import run_post_route_chat_graph, unified_chat_agent_enabled
from app.intent_detectors import apply_intent_pipeline


def _repo_root_default() -> Path:
    return Path(__file__).resolve().parents[1]


def _get_session_state(request: AgentRequest, store: SessionStateStore) -> SessionState:
    ctx_state = request.context.get("session_state")
    if isinstance(ctx_state, SessionState):
        return ctx_state
    return store.get(request.session_id)


def _build_repair_recent_messages(
    recent_messages: list[dict[str, Any]] | None,
    *,
    route_exc: AgentRoutingError,
) -> list[dict[str, Any]]:
    """为单次 reroute 追加结构化修正提示。"""
    repaired = [dict(msg) for msg in (recent_messages or []) if isinstance(msg, dict)]
    repaired.append(
        {
            "role": "assistant",
            "text": (
                "上一轮路由失败。"
                f"error_code={route_exc.code.value}; "
                f"termination_reason={route_exc.termination_reason or route_exc.code.value}; "
                "请根据用户原句、tradable_assets 和默认周期重新选择合法 action。"
                "如果仍无法确定，就返回 action=chat 并自然提示用户补充必要信息。"
            ),
        }
    )
    return repaired


def _classify_execute_exception(*, exc: Exception, task_type: str) -> AgentError:
    """对执行阶段异常做结构化分类。"""
    raw = str(exc)
    lower = raw.lower()
    upper = raw.upper()

    if isinstance(exc, TimeoutError) or "timeout" in lower or "超时" in raw:
        return AgentError(
            code=AgentErrorCode.execute_provider_timeout,
            stage=AgentErrorStage.execute,
            recoverable=True,
            message=raw,
            termination_reason="provider_timeout",
        )

    if "RAG" in upper:
        return AgentError(
            code=AgentErrorCode.rag_unavailable,
            stage=AgentErrorStage.infra,
            recoverable=True,
            message=raw,
            termination_reason="rag_unavailable",
        )

    if task_type == "followup" and "追问所需的分析产物不存在" in raw:
        return AgentError(
            code=AgentErrorCode.followup_output_missing,
            stage=AgentErrorStage.execute,
            recoverable=True,
            message=raw,
            termination_reason="followup_output_missing",
            context={},
        )

    if "postgres" in lower or "数据库" in raw:
        return AgentError(
            code=AgentErrorCode.db_unavailable,
            stage=AgentErrorStage.infra,
            recoverable=True,
            message=raw,
            termination_reason="db_unavailable",
        )

    if "backend" in lower or "后端" in raw:
        return AgentError(
            code=AgentErrorCode.analysis_backend_unavailable,
            stage=AgentErrorStage.infra,
            recoverable=True,
            message=raw,
            termination_reason="analysis_backend_unavailable",
        )

    if task_type == "quote":
        return AgentError(
            code=AgentErrorCode.execute_quote_failed,
            stage=AgentErrorStage.execute,
            recoverable=True,
            message=raw,
            termination_reason="quote_execution_failed",
        )

    if task_type in {"analysis", "compare", "research", "followup"}:
        return AgentError(
            code=AgentErrorCode.execute_analysis_failed,
            stage=AgentErrorStage.execute,
            recoverable=True,
            message=raw,
            termination_reason="analysis_execution_failed",
        )

    return AgentError(
        code=AgentErrorCode.unknown,
        stage=AgentErrorStage.execute,
        recoverable=True,
        message=raw,
        termination_reason="execute_unknown_error",
    )


def handle_request(request: AgentRequest) -> AgentResponse:
    """统一 Agent Core 入口函数。

    Args:
        request: 统一请求对象（来自飞书、CLI、HTTP）

    Returns:
        统一响应对象（包含 reply_text、reply_chunks、facts_bundle、meta）
    """
    start_ts = time.time()

    session_store = get_global_session_store()
    session_state = _get_session_state(request, session_store)
    session_store.reset_route_attempts(request.session_id)

    repo_root = _repo_root_default()
    rag_index = request.context.get("rag_index")

    route: dict[str, Any] = {}
    task_type: TaskType = "analysis"
    route_succeeded = False
    request_succeeded = False
    recent_messages = request.context.get("recent_messages")
    reroute_recent_messages = list(recent_messages) if isinstance(recent_messages, list) else None
    max_route_attempts = 2

    try:
        if rag_index is None:
            rag_index = get_or_create_rag_index(repo_root / "output")

        for attempt in range(1, max_route_attempts + 1):
            try:
                route = None
                if unified_chat_agent_enabled():
                    route = apply_intent_pipeline(request.text, session_state)
                if route is None:
                    route = plan_user_message(
                        request.text,
                        default_symbol=request.default_symbol,
                        default_interval=request.default_interval,
                        session_state=session_state,
                        recent_messages=reroute_recent_messages,
                    )
                route_succeeded = True
                break
            except AgentRoutingError as route_exc:
                logger.warning(
                    "[AgentCore] route_error attempt={}/{} code={} msg={}",
                    attempt,
                    max_route_attempts,
                    route_exc.code.value,
                    route_exc,
                )

                session_store.record_error(
                    request.session_id,
                    error_code=route_exc.code.value,
                    error_stage=route_exc.stage.value,
                    error_message=str(route_exc),
                    recoverable=route_exc.recoverable,
                )

                should_reroute = (
                    route_exc.stage == AgentErrorStage.route and
                    route_exc.recoverable and
                    attempt < max_route_attempts
                )
                if should_reroute:
                    reroute_recent_messages = _build_repair_recent_messages(
                        reroute_recent_messages,
                        route_exc=route_exc,
                    )
                    continue

                termination_reason = route_exc.termination_reason or route_exc.code.value
                if (
                    route_exc.stage == AgentErrorStage.route and
                    route_exc.recoverable and
                    attempt >= max_route_attempts
                ):
                    termination_reason = "max_route_attempts_reached"

                session_store.record_final_termination(
                    request.session_id,
                    termination_reason=termination_reason,
                    final_error_code=route_exc.code.value,
                )

                agent_error = AgentError(
                    code=route_exc.code,
                    stage=route_exc.stage,
                    recoverable=route_exc.recoverable,
                    message=str(route_exc),
                    termination_reason=termination_reason,
                    context=route_exc.context,
                )

                return AgentResponse.error(
                    error_msg=str(route_exc),
                    fallback_text="分析执行失败。请稍后重试或简化问题。",
                    agent_error=agent_error,
                )

        if not route_succeeded:
            raise RuntimeError("route loop exited without success or terminal response")

        log_routed_preview(route)

        task_type = str(route.get("task_type") or "analysis")

        # 统一图：所有 task_type 经由 agent_graph 的 capability→compose→session→compact
        if unified_chat_agent_enabled():
            try:
                resp = run_post_route_chat_graph(
                    route=route,
                    request=request,
                    session_state=session_state,
                    session_store=session_store,
                    rag_index=rag_index,
                )
                request_succeeded = True
                return resp
            except Exception as exc:
                logger.warning("[AgentCore] unified_graph_error exc={}", exc)
                agent_error = _classify_execute_exception(exc=exc, task_type=task_type)
                session_store.record_error(
                    request.session_id,
                    error_code=agent_error.code.value,
                    error_stage=agent_error.stage.value,
                    error_message=str(exc),
                    recoverable=agent_error.recoverable,
                )
                session_store.record_final_termination(
                    request.session_id,
                    termination_reason=agent_error.termination_reason or agent_error.code.value,
                    final_error_code=agent_error.code.value,
                )
                return AgentResponse.error(
                    error_msg=str(exc),
                    fallback_text="分析执行失败。请稍后重试或简化问题。",
                    agent_error=agent_error,
                )

        # AGENT_UNIFIED_GRAPH=0 时，走简化直出路径（无 facade 委托）
        return _fallback_direct_execute(
            route=route,
            request=request,
            task_type=task_type,
            rag_index=rag_index,
            session_store=session_store,
            session_state=session_state,
        )

    except Exception as exc:
        logger.warning("[AgentCore] executor_error exc={}", exc)
        agent_error = _classify_execute_exception(exc=exc, task_type=task_type)

        session_store.record_error(
            request.session_id,
            error_code=agent_error.code.value,
            error_stage=agent_error.stage.value,
            error_message=str(exc),
            recoverable=agent_error.recoverable,
        )

        session_store.record_final_termination(
            request.session_id,
            termination_reason=agent_error.termination_reason or agent_error.code.value,
            final_error_code=agent_error.code.value,
        )
        return AgentResponse.error(
            error_msg=str(exc),
            fallback_text="分析执行失败。请稍后重试或简化问题。",
            agent_error=agent_error,
        )

    finally:
        if route_succeeded and not request_succeeded:
            task_plan = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
            symbols = task_plan.get("symbols") or []
            interval = task_plan.get("interval") or request.default_interval
            provider = task_plan.get("provider")
            question = task_plan.get("question") or request.text

            session_store.update_from_route(
                request.session_id,
                action=str(route.get("action") or task_type),
                task_type=task_type,
                symbol=symbols[0] if symbols else None,
                symbols=symbols,
                interval=interval,
                provider=provider,
                question=question,
            )

        try:
            if route_succeeded and request_succeeded:
                session_store.record_success(
                    request.session_id,
                    termination_reason="success",
                )
        except Exception:
            pass


def _fallback_direct_execute(
    *,
    route: dict[str, Any],
    request: AgentRequest,
    task_type: str,
    rag_index: Any,
    session_store: SessionStateStore,
    session_state: SessionState,
) -> AgentResponse:
    """AGENT_UNIFIED_GRAPH=0 时的简化直出路径。

    不再委托 agent_facade，而是直接调用统一图（保证行为一致）。
    此函数仅在环境变量显式关闭统一图时被调用。
    """
    try:
        resp = run_post_route_chat_graph(
            route=route,
            request=request,
            session_state=session_state,
            session_store=session_store,
            rag_index=rag_index,
        )
        return resp
    except Exception as exc:
        logger.warning("[AgentCore] fallback_direct_execute_error exc={}", exc)
        agent_error = _classify_execute_exception(exc=exc, task_type=task_type)
        session_store.record_error(
            request.session_id,
            error_code=agent_error.code.value,
            error_stage=agent_error.stage.value,
            error_message=str(exc),
            recoverable=agent_error.recoverable,
        )
        session_store.record_final_termination(
            request.session_id,
            termination_reason=agent_error.termination_reason or agent_error.code.value,
            final_error_code=agent_error.code.value,
        )
        return AgentResponse.error(
            error_msg=str(exc),
            fallback_text="分析执行失败。请稍后重试或简化问题。",
            agent_error=agent_error,
        )


def run_agent(request: AgentRequest) -> AgentResponse:
    """run_agent 是 handle_request 的别名。"""
    return handle_request(request)


# ============ 兼容旧接口 ============

def handle_user_request_compat(
    *,
    text: str,
    channel: str = "feishu",
    user_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """兼容旧 agent_facade.handle_user_request 接口。"""
    ctx = context or {}
    request = AgentRequest(
        channel=str(channel),
        session_id=str(user_id or ctx.get("session_id") or "unknown"),
        text=text,
        user_id=user_id,
        default_symbol=str(ctx.get("default_symbol") or "BTC_USDT"),
        default_interval=str(ctx.get("default_interval") or "4h"),
        context=ctx,
        options=ctx.get("options") or {},
    )

    response = handle_request(request)

    return {
        "task_type": response.task_type,
        "response_mode": response.response_mode,
        "final_text": response.reply_text,
        "reply_chunks": response.reply_chunks,
        "facts_bundle": response.facts_bundle,
        "legacy_action": str(response.task_type),
        "meta": response.meta,
    }
