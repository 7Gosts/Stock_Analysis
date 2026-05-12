# SQL 资源目录

- **运行时加载**：`persistence/sql_loader.py` 的 `load_sql()` / `load_sql_text()`，相对路径以本目录为根，例如 `journal/idea_upsert.sql`、`account/ledger_append_snapshot.sql`。
- **AI / 人工只读查询**：常用 `SELECT` 见 `docs/SQL_AI_REFERENCE.md`（与业务表一致，表结构见 `docs/DATABASE_DESIGN.md`）。

DDL 仍以 `alembic/versions/` 为准；本目录放 **DML** 与可选片段，不替代迁移。
