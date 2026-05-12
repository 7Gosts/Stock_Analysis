"""从 config YAML 的 accounts 写入 account_ledger 首条 init 快照（幂等）。

Revision ID: journal_004
Revises: journal_003
Create Date: 2026-05-07

说明：
- 仅当某 account_id 在 account_ledger 中尚无任何行时插入 reason='init' 行。
- 金额来自运行迁移时 `config.runtime_config` 解析到的 YAML（与运行时一致）。
- 修改 YAML 后不会自动覆盖已有账本行；需手工调整库或删 init 后再迁（不推荐生产库随意删）。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "journal_004"
down_revision = "journal_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from config.runtime_config import get_account_initial_balance, get_accounts_config  # noqa: E402

    conn = op.get_bind()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    for key in get_accounts_config():
        cur = str(key).strip().upper()
        if not cur:
            continue
        bal = float(get_account_initial_balance(cur))
        if bal <= 0:
            continue
        exists = conn.execute(
            text("SELECT 1 FROM account_ledger WHERE account_id = :aid LIMIT 1"),
            {"aid": cur},
        ).scalar()
        if exists:
            continue
        conn.execute(
            text(
                "INSERT INTO account_ledger (account_id, balance, available, used_margin, unrealized_pnl, equity, snapshot_time, reason, meta) "
                "VALUES (:aid, :bal, :avail, 0, 0, :equity, CAST(:t AS timestamptz), 'init', '{}'::jsonb)"
            ),
            {"aid": cur, "bal": bal, "avail": bal, "equity": bal, "t": now},
        )


def downgrade() -> None:
    op.execute("DELETE FROM account_ledger WHERE reason = 'init'")
