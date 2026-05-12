from __future__ import annotations

from pathlib import Path
from typing import Any

from config import runtime_config

from app.journal_repository import JournalRepository
from app.journal_repository_jsonl import JsonlJournalRepository


def get_journal_repository(journal_path: Path) -> JournalRepository:
    """按配置返回台账仓库；postgres / dualwrite 需有效 DSN 与 schema。"""
    backend = runtime_config.get_database_backend()
    if backend == "postgres":
        from app.journal_repository_pg import PostgresJournalRepository

        return PostgresJournalRepository(journal_path)
    if backend == "dualwrite":
        from app.journal_repository_dualwrite import DualWriteJournalRepository

        return DualWriteJournalRepository(journal_path)
    return JsonlJournalRepository(journal_path)


def load_journal_entries_for_stats(journal_path: Path) -> list[dict[str, Any]]:
    """统计读入口：postgres 读库；jsonl / dualwrite 读 JSONL 主路径。"""
    backend = runtime_config.get_database_backend()
    if backend == "postgres":
        from app.journal_repository_pg import PostgresJournalRepository

        return PostgresJournalRepository(journal_path).list_entries()
    return JsonlJournalRepository(journal_path).list_entries()
