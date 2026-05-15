"""统一能力层返回契约定义。

所有 capability（market / research / sim_account）共用同一个 CapabilityResult 结构，
让 CLI、飞书、HTTP 和 Agent tool 调用都消费统一结果形状。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

DomainType = Literal["market", "research", "sim_account"]
IntentType = Literal[
    "overview", "positions", "orders", "fills", "report",
    "snapshot", "active_ideas", "health",
]


@dataclass
class CapabilityResult:
    """能力层统一返回结构。

    不关心调用方是谁。对飞书、CLI、HTTP、Agent tool 来说，它们都只是 capability consumer。
    """

    domain: DomainType
    intent: IntentType
    summary: str
    tables: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    evidence_sources: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    #: 供 compose / grounded writer 使用的展示默认值（如小数位），可选。
    default_display_prefs: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "domain": self.domain,
            "intent": self.intent,
            "summary": self.summary,
            "tables": self.tables,
            "metrics": self.metrics,
            "evidence_sources": self.evidence_sources,
            "meta": self.meta,
        }
        if self.default_display_prefs:
            out["default_display_prefs"] = dict(self.default_display_prefs)
        return out