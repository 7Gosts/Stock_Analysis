from __future__ import annotations

from typing import Any


def format_api_agent_response(body: dict[str, Any]) -> dict[str, Any]:
    """HTTP API 轻量包装：保持结构化字段为主。"""
    return dict(body) if isinstance(body, dict) else {"raw": body}
