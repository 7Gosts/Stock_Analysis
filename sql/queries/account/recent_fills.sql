-- 最近成交记录
-- params: symbol (optional), limit
SELECT fill_id, idea_id, symbol, side, fill_qty, fill_price,
       fill_notional, fill_seq, fill_time
FROM paper_fills
WHERE (symbol = :symbol OR :symbol = '')
ORDER BY fill_time DESC
LIMIT :limit;