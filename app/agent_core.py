"""统一 Agent Core 入口（三层重构 + 统一 Core 版）。

这是项目的智能体主入口，所有平台（飞书、CLI、HTTP）都应调用它。

职责：
1. 消费 AgentRequest
2. 识别任务类型（task_type）
3. 会话状态解析
4. 本地 RAG 检索
5. 执行器选择
6. 聚合 facts_bundle
7. 产出 AgentResponse

设计原则：
1. 飞书、CLI、HTTP 都通过它进入系统
2. 不包含任何平台特有的逻辑
3. SessionStateStore 和 RAG 由 core 使用
4. 历史消息只作为弱语境，不覆盖本地结构化事实

文档参考：
- docs/AGENT_CORE_UNIFICATION_PLAN.md
- docs/AGENT_CORE_UNIFICATION_EXECUTION_PROMPT.md
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
    DEFAULT_FALLBACK_MESSAGE,
    TaskType,
    ResponseMode,
)
from app.planner import plan_user_message, log_routed_preview, AgentRoutingError
from app.rag_index import get_or_create_rag_index, RagIndex
from app.session_state import SessionState, SessionStateStore, get_global_session_store


def _repo_root_default() -> Path:
    return Path(__file__).resolve().parents[1]


def _get_session_state(request: AgentRequest, store: SessionStateStore) -> SessionState:
    """从请求中提取或获取会话状态。"""
    ctx_state = request.context.get("session_state")
    if isinstance(ctx_state, SessionState):
        return ctx_state
    return store.get(request.session_id)


def _build_context_from_request(request: AgentRequest) -> dict[str, Any]:
    """从请求构建 agent_facade 需要的 context。"""
    ctx: dict[str, Any] = {
        "default_symbol": request.default_symbol,
        "default_interval": request.default_interval,
        "user_message_for_chunks": request.text,
        "repo_root": request.options.get("repo_root") or _repo_root_default(),
    }

    if request.context.get("recent_messages"):
        ctx["recent_messages"] = request.context["recent_messages"]

    if request.options.get("use_rag"):
        ctx["use_rag"] = request.options["use_rag"]

    if request.options.get("rag_top_k"):
        ctx["rag_top_k"] = request.options["rag_top_k"]

    return ctx


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


def _agent_error_from_meta(meta: dict[str, Any]) -> AgentError | None:
    """从执行器返回的 meta 中恢复结构化错误。"""
    error_code = meta.get("error_code")
    error_stage = meta.get("error_stage")
    if not error_code or not error_stage:
        return None

    try:
        code = AgentErrorCode(str(error_code))
        stage = AgentErrorStage(str(error_stage))
    except ValueError:
        return None

    return AgentError(
        code=code,
        stage=stage,
        recoverable=bool(meta.get("recoverable", False)),
        message=str(meta.get("error_message") or meta.get("error") or "execute_error"),
        termination_reason=str(meta.get("termination_reason") or "") or None,
        context=dict(meta.get("error_context") or {}),
    )


def _classify_execute_exception(*, exc: Exception, task_type: str) -> AgentError:
    """对执行阶段异常做最小可用的结构化分类。"""
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


def _execute_capability_task(
    *,
    task_type: TaskType,
    route: dict[str, Any],
    request: AgentRequest,
    session_store: SessionStateStore,
) -> tuple[AgentResponse, bool] | None:
    """执行统一 capability 任务；当前用于收口 sim_account。"""
    if task_type != "sim_account":
        return None

    from app.capabilities import view_sim_account_state

    scope = str(route.get("scope") or "overview").strip()
    account_id = route.get("account_id") or None
    symbol = route.get("symbol") or None

    try:
        cap_result = view_sim_account_state(
            scope=scope,
            account_id=account_id,
            symbol=symbol,
        )
    except Exception as cap_exc:
        logger.warning("[AgentCore] capability_error task_type={} exc={}", task_type, cap_exc)
        agent_error = _classify_execute_exception(exc=cap_exc, task_type=task_type)
        session_store.record_error(
            request.session_id,
            error_code=agent_error.code.value,
            error_stage=agent_error.stage.value,
            error_message=str(cap_exc),
            recoverable=agent_error.recoverable,
        )
        session_store.record_final_termination(
            request.session_id,
            termination_reason=agent_error.termination_reason or agent_error.code.value,
            final_error_code=agent_error.code.value,
        )
        return (
            AgentResponse.error(
                error_msg=str(cap_exc),
                fallback_text="模拟账户数据暂时不可用，请稍后重试。",
                agent_error=agent_error,
            ),
            False,
        )

    meta: dict[str, Any] = {
        "route": dict(route),
        "domain": cap_result.domain,
        "intent": cap_result.intent,
        "evidence_sources": cap_result.evidence_sources,
    }
    if cap_result.meta:
        meta["capability_meta"] = dict(cap_result.meta)

    reply_text = str(cap_result.summary or "").strip()
    reply_chunks = [reply_text] if reply_text else []
    return (
        AgentResponse(
            task_type=task_type,
            response_mode=str(route.get("response_mode") or "quick"),
            reply_text=reply_text,
            reply_chunks=reply_chunks,
            facts_bundle=cap_result.to_dict(),
            meta=meta,
        ),
        True,
    )


def handle_request(request: AgentRequest) -> AgentResponse:
    """统一 Agent Core 入口函数。

    Args:
        request: 统一请求对象（来自飞书、CLI、HTTP）

    Returns:
        统一响应对象（包含 reply_text、reply_chunks、facts_bundle、meta）

    流程：
    1. 获取会话状态（SessionStateStore）
    2. 获取 RAG 索引
    3. 调用 planner 进行意图路由
    4. 调用执行器
    5. 聚合 facts_bundle
    6. 更新会话状态
    7. 返回 AgentResponse
    """
    start_ts = time.time()

    # 1. 获取会话状态存储，重置本轮路由尝试次数
    session_store = get_global_session_store()
    session_state = _get_session_state(request, session_store)
    session_store.reset_route_attempts(request.session_id)

    # 2. 获取 RAG 索引
    repo_root = _repo_root_default()
    rag_index = request.context.get("rag_index")

    route: dict[str, Any] = {}
    task_type: TaskType = "analysis"
    response_mode: ResponseMode = "analysis"
    action = ""
    route_succeeded = False
    request_succeeded = False
    recent_messages = request.context.get("recent_messages")
    reroute_recent_messages = list(recent_messages) if isinstance(recent_messages, list) else None
    max_route_attempts = 2

    # 3. 调用 planner 进行意图路由
    # 4. 调用 agent_facade 执行
    facade_ctx = _build_context_from_request(request)
    facade_ctx["session_state"] = session_state

    try:
        if rag_index is None:
            rag_index = get_or_create_rag_index(repo_root / "output")
        facade_ctx["rag_index"] = rag_index

        for attempt in range(1, max_route_attempts + 1):
            try:
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
        response_mode = str(route.get("response_mode") or "analysis")
        action = str(route.get("action") or "").strip().lower()
        facade_ctx["route"] = route

        capability_result = _execute_capability_task(
            task_type=task_type,
            route=route,
            request=request,
            session_store=session_store,
        )
        if capability_result is not None:
            response, request_succeeded = capability_result
            return response

        # 飞书适配：调用现有 agent_facade（其他 task_type）
        if request.channel == "feishu":
            from app.agent_facade import handle_user_request as _handle_user_request_impl
            result = _handle_user_request_impl(
                text=request.text,
                channel=request.channel,
                user_id=request.user_id,
                context=facade_ctx,
            )
            result_meta = dict(result.get("meta") or {})
            agent_error = _agent_error_from_meta(result_meta)
            if agent_error is not None:
                session_store.record_error(
                    request.session_id,
                    error_code=agent_error.code.value,
                    error_stage=agent_error.stage.value,
                    error_message=agent_error.message,
                    recoverable=agent_error.recoverable,
                )
                session_store.record_final_termination(
                    request.session_id,
                    termination_reason=agent_error.termination_reason or agent_error.code.value,
                    final_error_code=agent_error.code.value,
                )
                return AgentResponse.error(
                    error_msg=agent_error.message,
                    fallback_text=str(result.get("final_text") or DEFAULT_FALLBACK_MESSAGE),
                    meta=result_meta,
                    agent_error=agent_error,
                )
            request_succeeded = True
            return AgentResponse(
                task_type=str(result.get("task_type") or task_type),
                response_mode=str(result.get("response_mode") or response_mode),
                reply_text=str(result.get("final_text") or DEFAULT_FALLBACK_MESSAGE),
                reply_chunks=list(result.get("reply_chunks") or []),
                facts_bundle=result.get("facts_bundle"),
                meta=dict(result.get("meta") or {}),
            )

        # HTTP/CLI 适配：简化路径
        else:
            # chat 直接返回
            if task_type == "chat":
                msg = str(route.get("chat_reply") or "").strip()
                if not msg:
                    raise AgentRoutingError(
                        "chat route missing reply text",
                        code=AgentErrorCode.route_missing_chat_reply,
                        recoverable=True,
                        termination_reason="llm_output_invalid",
                    )
                request_succeeded = True
                return AgentResponse(
                    task_type=task_type,
                    response_mode="quick",
                    reply_text=msg,
                    reply_chunks=[msg],
                    facts_bundle=None,
                    meta={"route": dict(route)},
                )

            # followup：从 RAG 获取
            if task_type == "followup":
                followup_ctx = route.get("followup_context") or {}
                symbol = followup_ctx.get("symbol")
                interval = followup_ctx.get("interval")
                output_refs = followup_ctx.get("output_refs") or {}

                if not symbol:
                    raise AgentRoutingError(
                        "followup route missing symbol",
                        code=AgentErrorCode.followup_missing_symbol,
                        recoverable=True,
                        termination_reason="followup_context_invalid",
                    )

                facts = rag_index.get_facts_for_followup(
                    symbol, interval=interval,
                    output_ref_path=output_refs.get("ai_overview_path"),
                )

                if not facts.get("found") or not isinstance(facts.get("overview"), dict):
                    agent_error = AgentError(
                        code=AgentErrorCode.followup_output_missing,
                        stage=AgentErrorStage.execute,
                        recoverable=True,
                        message="追问所需的分析产物不存在或无法读取",
                        termination_reason="followup_output_missing",
                        context={
                            "symbol": symbol,
                            "interval": interval,
                            "output_ref_path": output_refs.get("ai_overview_path"),
                        },
                    )
                    session_store.record_error(
                        request.session_id,
                        error_code=agent_error.code.value,
                        error_stage=agent_error.stage.value,
                        error_message=agent_error.message,
                        recoverable=agent_error.recoverable,
                    )
                    session_store.record_final_termination(
                        request.session_id,
                        termination_reason=agent_error.termination_reason or agent_error.code.value,
                        final_error_code=agent_error.code.value,
                    )
                    return AgentResponse.error(
                        error_msg=agent_error.message,
                        fallback_text="我这次没拿到上一轮分析产物。你可以让我重新分析一次。",
                        agent_error=agent_error,
                    )

                reply = _build_followup_reply(facts, request.text)

                from app.executors.facts_bundle import merge_facts_bundle, build_evidence_source
                fb = merge_facts_bundle(
                    task_type="followup",
                    response_mode="followup",
                    user_question=request.text,
                    symbols=[symbol],
                    followup_facts=facts,
                    evidence_sources=[build_evidence_source(
                        source_path=facts.get("source_path", "rag:index"),
                        source_type="kline",
                        symbol=symbol,
                    )],
                    trace={"executors": ["rag_followup"]},
                )

                request_succeeded = True
                return AgentResponse(
                    task_type="followup",
                    response_mode="followup",
                    reply_text=reply,
                    reply_chunks=[reply],
                    facts_bundle=fb,
                    meta={"route": dict(route), "symbol": symbol},
                )

            # research：从 RAG 或实时检索
            if task_type == "research":
                task_plan = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
                kw = str(task_plan.get("research_keyword") or request.text).strip()

                hits = rag_index.query(kw, top_k=5, source_type_filter="research")
                if hits:
                    research_facts = {"ok": True, "keyword": kw, "items": []}
                    for hit in hits:
                        research_facts["items"].append({
                            "title": hit.get("snippet", "")[:50],
                            "source_path": hit.get("source_path"),
                            "score": hit.get("score"),
                        })
                else:
                    from app.executors.research_summary import run_research_summary
                    research_facts = run_research_summary(keyword=kw, n=5)

                reply = _build_research_reply(research_facts)

                from app.executors.facts_bundle import merge_facts_bundle, build_evidence_source
                fb = merge_facts_bundle(
                    task_type="research",
                    response_mode="narrative",
                    user_question=request.text,
                    symbols=[],
                    research_facts=research_facts,
                    evidence_sources=[build_evidence_source(
                        source_path="yanbaoke:search",
                        source_type="research",
                    )],
                    trace={"executors": ["research_summary"], "keyword": kw},
                )

                request_succeeded = True
                return AgentResponse(
                    task_type="research",
                    response_mode="narrative",
                    reply_text=reply,
                    reply_chunks=[reply],
                    facts_bundle=fb,
                    meta={"route": dict(route), "research_keyword": kw},
                )

            # analysis / quote / compare：调用 agent_facade
            from app.agent_facade import handle_user_request as _handle_user_request_impl
            result = _handle_user_request_impl(
                text=request.text,
                channel=request.channel,
                user_id=request.user_id,
                context=facade_ctx,
            )
            result_meta = dict(result.get("meta") or {})
            agent_error = _agent_error_from_meta(result_meta)
            if agent_error is not None:
                session_store.record_error(
                    request.session_id,
                    error_code=agent_error.code.value,
                    error_stage=agent_error.stage.value,
                    error_message=agent_error.message,
                    recoverable=agent_error.recoverable,
                )
                session_store.record_final_termination(
                    request.session_id,
                    termination_reason=agent_error.termination_reason or agent_error.code.value,
                    final_error_code=agent_error.code.value,
                )
                return AgentResponse.error(
                    error_msg=agent_error.message,
                    fallback_text=str(result.get("final_text") or DEFAULT_FALLBACK_MESSAGE),
                    meta=result_meta,
                    agent_error=agent_error,
                )
            request_succeeded = True
            return AgentResponse(
                task_type=str(result.get("task_type") or task_type),
                response_mode=str(result.get("response_mode") or response_mode),
                reply_text=str(result.get("final_text") or DEFAULT_FALLBACK_MESSAGE),
                reply_chunks=list(result.get("reply_chunks") or []),
                facts_bundle=result.get("facts_bundle"),
                meta=dict(result.get("meta") or {}),
            )
    except Exception as exc:
        # 其他执行阶段错误（未分类）
        logger.warning("[AgentCore] executor_error exc={}", exc)
        agent_error = _classify_execute_exception(exc=exc, task_type=task_type)

        # 记录错误到 session state
        session_store.record_error(
            request.session_id,
            error_code=agent_error.code.value,
            error_stage=agent_error.stage.value,
            error_message=str(exc),
            recoverable=agent_error.recoverable,
        )

        # 记录最终终止状态
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
        # 5. 更新会话状态（记录本轮 symbols）
        if route_succeeded:
            task_plan = route.get("task_plan") if isinstance(route.get("task_plan"), dict) else {}
            symbols = task_plan.get("symbols") or []
            interval = task_plan.get("interval") or request.default_interval
            provider = task_plan.get("provider")
            question = task_plan.get("question") or request.text

            session_store.update_from_route(
                request.session_id,
                action=action,
                task_type=task_type,
                symbol=symbols[0] if symbols else None,
                symbols=symbols,
                interval=interval,
                provider=provider,
                question=question,
            )

        # 6. 记录成功状态（如果没有异常）
        # 注意：如果有异常，错误记录已经在 except block 中完成
        try:
            if route_succeeded and request_succeeded:
                session_store.record_success(
                    request.session_id,
                    termination_reason="success",
                )
        except Exception:
            # 忽略状态记录失败，不影响主流程
            pass


def run_agent(request: AgentRequest) -> AgentResponse:
    """run_agent 是 handle_request 的别名。"""
    return handle_request(request)


def _build_followup_reply(facts: dict[str, Any], question: str) -> str:
    """构建追问回复（简化版）。"""
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
                lines.append(f" · 入场参考：{sel.get('entry')}")
            if sel.get("stop"):
                lines.append(f" · 止损参考：{sel.get('stop')}")
            if sel.get("tp1"):
                lines.append(f" · 止盈1：{sel.get('tp1')}")
        else:
            lines.append(" · 未找到结构化分析数据")
    else:
        lines.append(" · 无有效分析产物")
    lines.append("仅供技术分析与程序化演示，不构成投资建议。")
    return "\n".join(lines)


def _build_research_reply(facts: dict[str, Any]) -> str:
    """构建研报回复（简化版）。"""
    if not facts.get("ok"):
        return f"研报检索暂不可用：{facts.get('error') or 'unknown'}。仅供技术分析与程序化演示。"

    lines: list[str] = [f"【研报线索】关键词：{facts.get('keyword') or ''}"]
    for it in facts.get("items") or []:
        if not isinstance(it, dict):
            continue
        t = str(it.get("title") or "").strip()
        if t:
            lines.append(f" · {t}")
    lines.append("以上为检索摘要线索，非官方观点背书。仅供技术分析与程序化演示。")
    return "\n".join(lines)


# ============ 兼容旧接口 ============

def handle_user_request_compat(
    *,
    text: str,
    channel: str = "feishu",
    user_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """兼容旧 agent_facade.handle_user_request 接口。

    内部转换为 AgentRequest -> handle_request -> AgentResponse -> dict
    """
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