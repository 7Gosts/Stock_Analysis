-- 当前未平仓持仓明细
-- params: account_id (optional), symbol (optional), limit
SELECT id, account_id, symbol, qty, entry_price, entry_notional,
       unrealized_pnl, linked_idea_id, opened_at
FROM account_positions
WHERE status = 'open'
  AND (account_id = :account_id OR :account_id = '')
  AND (symbol = :symbol OR :symbol = '')
ORDER BY opened_at DESC
LIMIT :limit;