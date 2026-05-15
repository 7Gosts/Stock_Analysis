"""统一聊天 Agent 状态（LangGraph StateGraph）。

与 `langgraph_flow.py` 内单次分析 AgentState 区分：本模块用于多轮对话编排。
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ChatPostRouteState(TypedDict, total=False):
    """路由完成后的编排状态：capability → compose → session → compact。"""

    messages: Annotated[list[BaseMessage], add_messages]
    route: dict[str, Any]
    task_type: str
    response_mode: str
    action: str
    facts_bundle: dict[str, Any]
    display_preferences: dict[str, Any]
    reply_text: str
    reply_chunks: list[str]
    skip_compose_llm: bool
    history_version: int
    compacted_summary: str | None
    # Private keys for compose fallback (not persisted)
    _output_refs: dict[str, Any]
    _narrative_facts: dict[str, Any]
