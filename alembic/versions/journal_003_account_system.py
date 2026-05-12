"""
account_ledger / account_positions / account_events

Revision ID: journal_003
Revises: journal_002
Create Date: 2026-05-13

"""
from __future__ import annotations

from alembic import op

revision = "journal_003"
down_revision = "journal_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS account_ledger (
            id bigserial PRIMARY KEY,
            account_id varchar(16) NOT NULL,
            balance numeric(20,8) NOT NULL,
            available numeric(20,8) NOT NULL,
            used_margin numeric(20,8) NOT NULL DEFAULT 0,
            unrealized_pnl numeric(20,8) DEFAULT 0,
            equity numeric(20,8) DEFAULT 0,
            snapshot_time timestamptz NOT NULL,
            reason varchar(64) NOT NULL,
            meta jsonb
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS account_positions (
            id bigserial PRIMARY KEY,
            account_id varchar(16) NOT NULL,
            symbol varchar(64) NOT NULL,
            market varchar(16) NOT NULL,
            direction varchar(16) NOT NULL,
            status varchar(16) NOT NULL,
            qty numeric(20,8) NOT NULL,
            entry_price numeric(20,8),
            entry_notional numeric(20,8),
            exit_price numeric(20,8),
            exit_notional numeric(20,8),
            unrealized_pnl numeric(20,8),
            realized_pnl numeric(20,8),
            realized_pnl_pct numeric(12,6),
            opened_at timestamptz,
            closed_at timestamptz,
            linked_order_id varchar(64),
            linked_idea_id varchar(64),
            meta jsonb
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS account_events (
            id bigserial PRIMARY KEY,
            account_id varchar(16) NOT NULL,
            event_type varchar(64) NOT NULL,
            transaction_time timestamptz NOT NULL,
            details jsonb,
            linked_idea_id varchar(64)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_account_ledger_account_time ON account_ledger (account_id, snapshot_time DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_account_positions_account_status ON account_positions (account_id, status);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_account_events_account_time ON account_events (account_id, transaction_time DESC);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_account_events_account_time")
    op.execute("DROP INDEX IF EXISTS idx_account_positions_account_status")
    op.execute("DROP INDEX IF EXISTS idx_account_ledger_account_time")
    op.execute("DROP TABLE IF EXISTS account_events CASCADE")
    op.execute("DROP TABLE IF EXISTS account_positions CASCADE")
    op.execute("DROP TABLE IF EXISTS account_ledger CASCADE")
