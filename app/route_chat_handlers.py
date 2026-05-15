"""chat 路由：供统一图 capability 使用。"""
from __future__ import annotations

from typing import Any

from app.agent_schemas import AgentError, AgentErrorCode, AgentErrorStage


def build_chat_handle_result(route: dict[str, Any], *, base_meta: dict[str, Any]) -> dict[str, Any]:
    """与 ``handle_user_request`` 的 chat 分支返回形状一致。"""
    msg = str(route.get("chat_reply") or "").strip()
    fallback = "我这次没有稳定生成回复。你可以补一句标的/周期，或让我重新分析。"
    if not msg:
        agent_err = AgentError(
            code=AgentErrorCode.route_missing_chat_reply,
            stage=AgentErrorStage.route,
            recoverable=True,
            message="chat route missing chat_reply",
            termination_reason="llm_output_invalid",
        )
        return {
            "task_type": "chat",
            "response_mode": "quick",
            "facts_bundle": None,
            "final_text": fallback,
            "reply_chunks": [fallback],
            "legacy_action": "chat",
            "meta": {**base_meta, **agent_err.to_meta_dict()},
        }
    return {
        "task_type": "chat",
        "response_mode": "quick",
        "facts_bundle": None,
        "final_text": msg,
        "reply_chunks": [msg],
        "legacy_action": "chat",
        "meta": base_meta,
    }
