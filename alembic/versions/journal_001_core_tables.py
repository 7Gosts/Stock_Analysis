"""journal_ideas / journal_events / analysis_snapshots 初始表与索引

Revision ID: journal_001
Revises:
Create Date: 2026-05-12

"""

from __future__ import annotations

from alembic import op

revision = "journal_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS journal_ideas (
            id bigserial PRIMARY KEY,
            idea_id varchar(64) NOT NULL UNIQUE,
            symbol varchar(32) NOT NULL,
            asset_name varchar(128),
            market varchar(16) NOT NULL,
            provider varchar(32) NOT NULL,
            interval varchar(16) NOT NULL,
            plan_type varchar(16) NOT NULL,
            direction varchar(16) NOT NULL,
            status varchar(32) NOT NULL,
            exit_status varchar(32),
            entry_type varchar(16),
            order_kind_cn varchar(16),
            entry_price numeric(20,8),
            entry_zone_low numeric(20,8),
            entry_zone_high numeric(20,8),
            signal_last numeric(20,8),
            stop_loss numeric(20,8),
            tp1 numeric(20,8),
            tp2 numeric(20,8),
            rr numeric(12,4),
            wyckoff_bias varchar(32),
            mtf_aligned boolean,
            structure_flags jsonb,
            tags jsonb,
            strategy_reason text,
            lifecycle_v1 jsonb,
            meta jsonb,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            valid_until timestamptz,
            filled_at timestamptz,
            closed_at timestamptz,
            fill_price numeric(20,8),
            closed_price numeric(20,8),
            realized_pnl_pct numeric(12,4),
            unrealized_pnl_pct numeric(12,4)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS journal_events (
            id bigserial PRIMARY KEY,
            idea_id varchar(64) NOT NULL REFERENCES journal_ideas(idea_id) ON DELETE CASCADE,
            event_type varchar(32) NOT NULL,
            old_status varchar(32),
            new_status varchar(32),
            event_time timestamptz NOT NULL,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_snapshots (
            id bigserial PRIMARY KEY,
            idea_id varchar(64),
            symbol varchar(32) NOT NULL,
            provider varchar(32) NOT NULL,
            interval varchar(16) NOT NULL,
            snapshot_time timestamptz NOT NULL,
            trend varchar(32),
            last_price numeric(20,8),
            fib_zone varchar(64),
            risk_flags jsonb,
            fixed_template jsonb,
            raw_stats jsonb,
            source_session_dir text
        );
        """
    )
    for stmt in (
        "CREATE INDEX IF NOT EXISTS idx_journal_ideas_symbol_interval ON journal_ideas (symbol, interval);",
        "CREATE INDEX IF NOT EXISTS idx_journal_ideas_status ON journal_ideas (status);",
        "CREATE INDEX IF NOT EXISTS idx_journal_ideas_created_at ON journal_ideas (created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_journal_ideas_market_status ON journal_ideas (market, status);",
        "CREATE INDEX IF NOT EXISTS idx_journal_events_idea_id_event_time ON journal_events (idea_id, event_time DESC);",
        "CREATE INDEX IF NOT EXISTS idx_journal_ideas_structure_flags_gin ON journal_ideas USING gin (structure_flags);",
        "CREATE INDEX IF NOT EXISTS idx_journal_ideas_meta_gin ON journal_ideas USING gin (meta);",
    ):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS journal_events CASCADE")
    op.execute("DROP TABLE IF EXISTS analysis_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS journal_ideas CASCADE")
