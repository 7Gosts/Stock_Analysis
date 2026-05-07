from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any, TypedDict

from app.agent_tools import make_tools
from app.guardrails import ensure_agent_response
from config.runtime_config import get_analysis_config
from analysis.beijing_time import default_review_time_for_interval, now_beijing_str, review_time_has_explicit_clock
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

REQUIRED_TEMPLATE_KEYS = (
    "综合倾向",
    "关键位(Fib)",
    "触发条件",
    "失效条件",
    "风险点",
    "下次复核时间",
)


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    bundle: dict[str, Any]
    result: dict[str, Any]
    tool_trace: list[str]


def run_graph(
    *,
    repo_root: Path,
    symbol: str,
    provider: str = "gateio",
    interval: str = "1d",
    limit: int = 180,
    out_dir: str | None = None,
    question: str | None = None,
    rag_top_k: int = 5,
    analysis_style: str = "auto",
) -> dict[str, Any]:
    app = _build_graph(repo_root=repo_root)
    q = question or "请按固定模板输出当前技术分析。"
    bj_now = now_beijing_str()
    review_hint = default_review_time_for_interval(interval)
    system_prompt = (
        "你是交易分析Agent。你必须先调用工具 fetch_analysis_bundle 获取事实数据，再输出 JSON。"
        "JSON 必须包含字段：综合倾向,关键位(Fib),触发条件,失效条件,风险点,下次复核时间。"
        "「关键位(Fib)」「触发条件」「失效条件」必须与工具返回的 fixed_template 中数值一致，禁止改写为「未提供」或编造未在快照中出现的价位。"
        f"当前北京时间（UTC+8）：{bj_now}；本会话 interval={interval}。"
        f"字段「下次复核时间」必须写含日期与钟点的北京时间（UTC+8），示例：{review_hint}；"
        "禁止按北美/太平洋或无名时区臆测；禁止仅用「下一根收盘后」等无时间点表述。"
    )
    user_prompt = (
        f"symbol={symbol}, provider={provider}, interval={interval}, limit={limit}, "
        f"out_dir={out_dir or ''}, question={q}, rag_top_k={rag_top_k}, analysis_style={analysis_style}. "
        "请先调工具，再给出最终结论。"
    )
    state: AgentState = {
        "messages": [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
        "tool_trace": [],
    }
    out = app.invoke(state)
    result = out.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("LangGraph 未返回有效 result")
    return result


def _build_graph(*, repo_root: Path):
    tools = make_tools(repo_root=repo_root)
    tool_node = ToolNode(tools)
    llm = _build_llm().bind_tools(tools)

    def agent_node(state: AgentState) -> AgentState:
        msg = llm.invoke(state.get("messages", []))
        return {"messages": [msg]}

    def format_node(state: AgentState) -> AgentState:
        bundle = _extract_latest_bundle(state.get("messages", []))
        if not isinstance(bundle, dict):
            raise RuntimeError("未获取到工具分析结果，请检查工具调用。")
        llm_template = _extract_llm_template(state.get("messages", []))
        analysis_result = dict(bundle.get("analysis_result") or {})
        fallback = analysis_result.get("fixed_template") if isinstance(analysis_result.get("fixed_template"), dict) else {}
        fixed_template = _normalize_fixed_template(llm_template=llm_template, fallback=fallback)
        analysis_result["fixed_template"] = fixed_template
        analysis_result["decision_source"] = "llm+rules"
        payload = {
            "analysis_result": analysis_result,
            "risk_flags": bundle.get("risk_flags") or ["normal"],
            "evidence_sources": bundle.get("evidence_sources") or [],
            "meta": dict(bundle.get("meta") or {}),
        }
        payload["meta"]["tool_trace"] = _tool_trace(state.get("messages", []))
        return {"bundle": bundle, "result": payload}

    def guardrail_node(state: AgentState) -> AgentState:
        payload = state.get("result")
        if not isinstance(payload, dict):
            raise RuntimeError("guardrail_node 缺少 result")
        checked = ensure_agent_response(payload, check_paths=False)
        return {"result": checked}

    workflow = StateGraph(AgentState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("format", format_node)
    workflow.add_node("guardrail", guardrail_node)
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: "format"})
    workflow.add_edge("tools", "agent")
    workflow.add_edge("format", "guardrail")
    workflow.add_edge("guardrail", END)
    return workflow.compile()


def _extract_latest_bundle(messages: list[BaseMessage]) -> dict[str, Any] | None:
    for m in reversed(messages):
        if isinstance(m, ToolMessage):
            content = m.content
            if isinstance(content, dict):
                return content
            if isinstance(content, str):
                try:
                    obj = json.loads(content)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    return obj
    return None


def _extract_llm_template(messages: list[BaseMessage]) -> dict[str, Any]:
    for m in reversed(messages):
        if not isinstance(m, AIMessage):
            continue
        c = m.content
        if isinstance(c, str) and c.strip():
            try:
                obj = json.loads(c)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
        if isinstance(c, list):
            for part in c:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    try:
                        obj = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict):
                        return obj
    return {}


def _normalize_fixed_template(*, llm_template: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    fb = dict(fallback or {})
    out = dict(fb)
    for k in REQUIRED_TEMPLATE_KEYS:
        if k in llm_template and llm_template[k] not in (None, ""):
            out[k] = llm_template[k]
    if llm_template.get("bias"):
        out["综合倾向"] = llm_template["bias"]
    if llm_template.get("trigger"):
        out["触发条件"] = llm_template["trigger"]
    if llm_template.get("invalidation"):
        out["失效条件"] = llm_template["invalidation"]
    if llm_template.get("review_time"):
        out["下次复核时间"] = llm_template["review_time"]
    if llm_template.get("risk_points"):
        out["风险点"] = llm_template["risk_points"]
    if not isinstance(out.get("风险点"), list):
        out["风险点"] = [str(out.get("风险点") or "常规波动风险")]
    for k in REQUIRED_TEMPLATE_KEYS:
        out.setdefault(k, "待补充")
    # 工具快照优先：关键位/触发/失效以 overview 规则模板为准，避免 LLM 写成「未提供」覆盖数值
    for key in ("关键位(Fib)", "触发条件", "失效条件"):
        if _overview_template_field_usable(fb.get(key)):
            out[key] = fb[key]
    fb_rt = str(fb.get("下次复核时间") or "")
    out_rt = str(out.get("下次复核时间") or "")
    if review_time_has_explicit_clock(fb_rt) and not review_time_has_explicit_clock(out_rt):
        out["下次复核时间"] = fb["下次复核时间"]
    return out


def _overview_template_field_usable(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        t = v.strip()
        if not t or t in ("待补充", "未知"):
            return False
        return True
    return bool(v)


def _tool_trace(messages: list[BaseMessage]) -> list[str]:
    out: list[str] = []
    for m in messages:
        if isinstance(m, ToolMessage):
            nm = str(m.name or "unknown_tool")
            out.append(nm)
    return out


def _build_llm() -> ChatOpenAI:
    model = _deepseek_model()
    api_key = _deepseek_api_key()
    base_url = _deepseek_base_url()
    return ChatOpenAI(
        model=model,
        temperature=0.2,
        api_key=api_key,
        base_url=base_url,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _deepseek_api_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    cfg = get_analysis_config()
    node = cfg.get("deepseek") if isinstance(cfg.get("deepseek"), dict) else {}
    key = str(node.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("缺少 DeepSeek API Key（环境变量或 config/analysis_defaults.yaml）")
    return key


def _deepseek_base_url() -> str:
    v = os.getenv("DEEPSEEK_BASE_URL", "").strip()
    if v:
        return v
    cfg = get_analysis_config()
    node = cfg.get("deepseek") if isinstance(cfg.get("deepseek"), dict) else {}
    return str(node.get("base_url") or "https://api.deepseek.com").strip()


def _deepseek_model() -> str:
    v = os.getenv("DEEPSEEK_MODEL", "").strip()
    if v:
        return v
    cfg = get_analysis_config()
    node = cfg.get("deepseek") if isinstance(cfg.get("deepseek"), dict) else {}
    return str(node.get("model") or "deepseek-v4-flash").strip()
