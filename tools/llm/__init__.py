"""LLM client 模块（provider-agnostic）。

当前默认 provider 是 deepseek，但模块命名与异常已 provider-agnostic。
后续可扩展支持其他 provider 的 tool-calling 差异。
"""
from tools.llm.client import (
    LLMClientError,
    DEFAULT_FEISHU_ROUTER_SYSTEM_PROMPT,
    DEFAULT_FEISHU_NARRATIVE_SYSTEM,
    GROUNDED_WRITER_SYSTEM_BY_MODE,
    ROUTER_POLICY,
    decide_feishu_route,
    feishu_route_deepseek_raw_and_routed,
    generate_decision,
    generate_feishu_narrative,
    generate_grounded_answer,
)

__all__ = [
    "LLMClientError",
    "DEFAULT_FEISHU_ROUTER_SYSTEM_PROMPT",
    "DEFAULT_FEISHU_NARRATIVE_SYSTEM",
    "GROUNDED_WRITER_SYSTEM_BY_MODE",
    "ROUTER_POLICY",
    "decide_feishu_route",
    "feishu_route_deepseek_raw_and_routed",
    "generate_decision",
    "generate_feishu_narrative",
    "generate_grounded_answer",
]