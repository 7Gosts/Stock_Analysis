from __future__ import annotations

from pathlib import Path
from typing import Any

from persistence.journal_repository import JournalRepository


def get_journal_repository(_journal_path: Path) -> JournalRepository:
    """返回 PostgreSQL 台账仓库（需 `database.postgres.dsn`）。"""
    from persistence.journal_repository_pg import PostgresJournalRepository

    return PostgresJournalRepository()


def load_journal_entries_for_stats(journal_path: Path) -> list[dict[str, Any]]:
    """统计读入口：从 `journal_ideas` 读取。"""
    from persistence.journal_repository_pg import PostgresJournalRepository

    return PostgresJournalRepository().list_entries()
