-- account_ledger：追加一条资金快照（init / 交易 / 充提 / 调账 等共用）
INSERT INTO account_ledger (account_id, balance, available, used_margin, unrealized_pnl, equity, snapshot_time, reason, meta)
VALUES (:aid, :bal, :avail, :used, :u, :equity, CAST(:t AS timestamptz), :reason, CAST(:meta AS jsonb));
