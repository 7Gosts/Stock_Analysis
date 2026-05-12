"""契约：sql_loader 路径解析与 journal SQL 文件存在性。"""
from __future__ import annotations

import pytest

from persistence.sql_loader import load_sql, load_sql_text


def test_load_sql_rejects_traversal() -> None:
    with pytest.raises(ValueError):
        load_sql("../secrets.env")
    with pytest.raises(ValueError):
        load_sql("foo/../../../etc/passwd")


def test_load_sql_journal_files() -> None:
    ins = load_sql("journal/idea_insert.sql")
    assert "INSERT INTO journal_ideas" in ins
    assert ":idea_id" in ins
    ups = load_sql_text("journal/idea_upsert.sql")
    assert "ON CONFLICT (idea_id)" in str(ups)
