"""paper_orders / paper_fills 模拟委托与成交

Revision ID: journal_002
Revises: journal_001
Create Date: 2026-05-12

"""

from __future__ import annotations

from alembic import op

revision = "journal_002"
down_revision = "journal_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_orders (
            id bigserial PRIMARY KEY,
            order_id varchar(64) NOT NULL UNIQUE,
            idea_id varchar(64) NOT NULL REFERENCES journal_ideas(idea_id) ON DELETE CASCADE,
            symbol varchar(32) NOT NULL,
            market varchar(16) NOT NULL,
            provider varchar(32) NOT NULL,
            interval varchar(16) NOT NULL,
            side varchar(16) NOT NULL,
            order_type varchar(16) NOT NULL,
            tif varchar(16),
            requested_qty numeric(20,8),
            requested_notional numeric(20,8),
            limit_price numeric(20,8),
            trigger_price numeric(20,8),
            stop_price numeric(20,8),
            status varchar(32) NOT NULL,
            status_reason varchar(64),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            submitted_at timestamptz,
            cancelled_at timestamptz,
            expired_at timestamptz,
            simulation_rule jsonb,
            meta jsonb
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_fills (
            id bigserial PRIMARY KEY,
            fill_id varchar(64) NOT NULL UNIQUE,
            order_id varchar(64) NOT NULL REFERENCES paper_orders(order_id) ON DELETE CASCADE,
            idea_id varchar(64) NOT NULL REFERENCES journal_ideas(idea_id) ON DELETE CASCADE,
            symbol varchar(32) NOT NULL,
            side varchar(16) NOT NULL,
            fill_qty numeric(20,8),
            fill_price numeric(20,8) NOT NULL,
            fill_notional numeric(20,8),
            fee numeric(20,8),
            fee_currency varchar(16),
            slippage_bps numeric(12,4),
            fill_time timestamptz NOT NULL,
            fill_seq int,
            fill_source varchar(32) NOT NULL,
            meta jsonb
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_paper_orders_idea_id ON paper_orders (idea_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_paper_orders_status_created ON paper_orders (status, created_at);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_paper_fills_order_fill_time ON paper_fills (order_id, fill_time);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_paper_fills_idea_fill_time ON paper_fills (idea_id, fill_time);")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_fills_idea_fill_seq ON paper_fills (idea_id, fill_seq);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_paper_fills_idea_fill_seq")
    op.execute("DROP INDEX IF EXISTS idx_paper_fills_idea_fill_time")
    op.execute("DROP INDEX IF EXISTS idx_paper_fills_order_fill_time")
    op.execute("DROP INDEX IF EXISTS idx_paper_orders_status_created")
    op.execute("DROP INDEX IF EXISTS idx_paper_orders_idea_id")
    op.execute("DROP TABLE IF EXISTS paper_fills CASCADE")
    op.execute("DROP TABLE IF EXISTS paper_orders CASCADE")
