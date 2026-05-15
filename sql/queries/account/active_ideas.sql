-- 活动交易想法 (watch/pending/filled)
-- params: symbol (optional), limit
SELECT idea_id, symbol, interval, direction, status, exit_status,
       entry_price, stop_loss, tp1, created_at, updated_at, plan_type
FROM journal_ideas
WHERE status IN ('watch', 'pending', 'filled')
  AND (symbol = :symbol OR :symbol = '')
ORDER BY updated_at DESC NULLS LAST
LIMIT :limit;