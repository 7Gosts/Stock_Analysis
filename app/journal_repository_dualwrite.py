from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from loguru import logger

from app.journal_repository_jsonl import JsonlJournalRepository
from app.journal_repository_pg import PostgresJournalRepository
from config import runtime_config


def _pg_error_fields(exc: BaseException) -> dict[str, Any]:
    return {
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
    }


class DualWriteJournalRepository:
    """迁移期双写：JSONL 为主路径；先 JSONL 成功再写 PG；PG 失败结构化日志，默认不回滚 JSONL。"""

    def __init__(self, journal_path: Path) -> None:
        self._path = journal_path.resolve()
        self._jsonl = JsonlJournalRepository(self._path)
        self._pg = PostgresJournalRepository(self._path)
        self._rollback_jsonl = runtime_config.get_dualwrite_rollback_jsonl_on_pg_failure()

    def list_entries(self) -> list[dict[str, Any]]:
        return self._jsonl.list_entries()

    def save_entries(self, entries: list[dict[str, Any]]) -> None:
        prev = self._jsonl.list_entries()
        self._jsonl.save_entries(entries)
        try:
            self._pg.save_entries(entries)
        except Exception as exc:
            logger.error(
                "[JournalDualWrite] pg_save_entries_failed backend=dualwrite op=save_entries {}",
                _pg_error_fields(exc),
            )
            if self._rollback_jsonl:
                try:
                    self._jsonl.save_entries(prev)
                    logger.warning("[JournalDualWrite] rolled_back_jsonl_after_pg_failure op=save_entries")
                except Exception as exc2:
                    logger.critical(
                        "[JournalDualWrite] rollback_jsonl_failed op=save_entries {}",
                        _pg_error_fields(exc2),
                    )

    def append_idea(self, idea: dict[str, Any]) -> None:
        prev = self._jsonl.list_entries()
        self._jsonl.append_idea(idea)
        try:
            self._pg.append_idea(idea)
        except Exception as exc:
            iid = str(idea.get("idea_id") or "")
            logger.error(
                "[JournalDualWrite] pg_append_idea_failed backend=dualwrite idea_id={} {}",
                iid,
                _pg_error_fields(exc),
            )
            if self._rollback_jsonl:
                try:
                    self._jsonl.save_entries(prev)
                    logger.warning(
                        "[JournalDualWrite] rolled_back_jsonl_after_pg_failure idea_id={} op=append_idea",
                        iid,
                    )
                except Exception as exc2:
                    logger.critical(
                        "[JournalDualWrite] rollback_jsonl_failed idea_id={} op=append_idea {}",
                        iid,
                        _pg_error_fields(exc2),
                    )

    def update_idea(self, idea_id: str, patch: dict[str, Any]) -> None:
        prev = self._jsonl.list_entries()
        self._jsonl.update_idea(idea_id, patch)
        try:
            self._pg.update_idea(idea_id, patch)
        except Exception as exc:
            logger.error(
                "[JournalDualWrite] pg_update_idea_failed backend=dualwrite idea_id={} {}",
                idea_id,
                _pg_error_fields(exc),
            )
            if self._rollback_jsonl:
                try:
                    self._jsonl.save_entries(prev)
                    logger.warning(
                        "[JournalDualWrite] rolled_back_jsonl_after_pg_failure idea_id={} op=update_idea",
                        idea_id,
                    )
                except Exception as exc2:
                    logger.critical(
                        "[JournalDualWrite] rollback_jsonl_failed idea_id={} op=update_idea {}",
                        idea_id,
                        _pg_error_fields(exc2),
                    )

    def append_event(self, idea_id: str, event_type: str, payload: dict[str, Any]) -> None:
        prev = self._jsonl.list_entries()
        self._jsonl.append_event(idea_id, event_type, payload)
        try:
            self._pg.append_event(idea_id, event_type, payload)
        except Exception as exc:
            logger.error(
                "[JournalDualWrite] pg_append_event_failed backend=dualwrite idea_id={} event_type={} {}",
                idea_id,
                event_type,
                _pg_error_fields(exc),
            )
            if self._rollback_jsonl:
                try:
                    self._jsonl.save_entries(prev)
                    logger.warning(
                        "[JournalDualWrite] rolled_back_jsonl_after_pg_failure idea_id={} event_type={} op=append_event",
                        idea_id,
                        event_type,
                    )
                except Exception as exc2:
                    logger.critical(
                        "[JournalDualWrite] rollback_jsonl_failed idea_id={} op=append_event {}",
                        idea_id,
                        _pg_error_fields(exc2),
                    )

    def has_active_idea(
        self,
        *,
        symbol: str,
        interval: str,
        direction: str,
        plan_type: str,
    ) -> bool:
        return self._jsonl.has_active_idea(
            symbol=symbol,
            interval=interval,
            direction=direction,
            plan_type=plan_type,
        )
