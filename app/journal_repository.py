"""台账持久化抽象：业务层只依赖本协议，不直接绑 JSONL 或 PostgreSQL。"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class JournalRepository(Protocol):
    """首版即含单条语义；JSONL 实现可内部全量读写落盘。"""

    def list_entries(self) -> list[dict[str, Any]]:
        """返回当前台账条目列表（可变列表，调用方就地修改后可用 save_entries 落盘）。"""
        ...

    def save_entries(self, entries: list[dict[str, Any]]) -> None:
        """全量写回（兼容批量与迁移脚本）。"""
        ...

    def append_idea(self, idea: dict[str, Any]) -> None:
        """追加一条候选（含 idea_id）；实现侧应写入 idea_created 类语义。"""
        ...

    def update_idea(self, idea_id: str, patch: dict[str, Any]) -> None:
        """按 idea_id 浅合并 patch 后持久化。"""
        ...

    def append_event(self, idea_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """追加生命周期事件（JSONL 可写入 idea 内 _journal_events）。"""
        ...

    def has_active_idea(
        self,
        *,
        symbol: str,
        interval: str,
        direction: str,
        plan_type: str,
    ) -> bool:
        ...
