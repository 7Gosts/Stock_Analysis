-- journal_ideas: append-only insert (append_idea / event idea_created)
INSERT INTO journal_ideas (
  idea_id, symbol, asset_name, market, provider, interval,
  plan_type, direction, status, exit_status,
  entry_type, order_kind_cn,
  entry_price, entry_zone_low, entry_zone_high, signal_last, stop_loss,
  tp1, tp2, rr,
  wyckoff_bias, mtf_aligned, structure_flags, tags,
  strategy_reason, lifecycle_v1, meta,
  created_at, updated_at, valid_until, filled_at, closed_at,
  fill_price, closed_price, realized_pnl_pct, unrealized_pnl_pct
) VALUES (
  :idea_id, :symbol, :asset_name, :market, :provider, :interval,
  :plan_type, :direction, :status, :exit_status,
  :entry_type, :order_kind_cn,
  :entry_price, :entry_zone_low, :entry_zone_high, :signal_last, :stop_loss,
  :tp1, :tp2, :rr,
  :wyckoff_bias, :mtf_aligned, CAST(:structure_flags AS jsonb), CAST(:tags AS jsonb),
  :strategy_reason, CAST(:lifecycle_v1 AS jsonb), CAST(:meta AS jsonb),
  CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz),
  CAST(:valid_until AS timestamptz), CAST(:filled_at AS timestamptz), CAST(:closed_at AS timestamptz),
  :fill_price, :closed_price, :realized_pnl_pct, :unrealized_pnl_pct
);
