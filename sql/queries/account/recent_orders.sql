-- 最近委托单
-- params: symbol (optional), limit
SELECT order_id, idea_id, symbol, side, status, requested_qty, created_at
FROM paper_orders
WHERE (symbol = :symbol OR :symbol = '')
ORDER BY created_at DESC
LIMIT :limit;