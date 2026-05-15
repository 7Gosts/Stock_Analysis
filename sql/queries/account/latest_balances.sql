-- 各币种账户最新余额快照
-- params: account_id (optional, filter by specific account)
SELECT DISTINCT ON (account_id)
  account_id,
  balance,
  available,
  used_margin,
  unrealized_pnl,
  equity,
  snapshot_time,
  reason
FROM account_ledger
WHERE account_id = :account_id OR :account_id = ''
ORDER BY account_id, snapshot_time DESC;