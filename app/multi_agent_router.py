from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentRoleResult:
    role: str
    output: dict[str, Any]


def serial_merge_role_results(results: list[AgentRoleResult]) -> dict[str, Any]:
    """多Agent扩展预留：先串行汇总，不做并行自治。"""
    merged: dict[str, Any] = {"roles": [], "summary": {}}
    for item in results:
        merged["roles"].append({"role": item.role, "output": item.output})
        fixed_template = ((item.output.get("analysis_result") or {}).get("fixed_template") if isinstance(item.output, dict) else {})
        if isinstance(fixed_template, dict) and not merged["summary"]:
            merged["summary"] = fixed_template
    return merged
