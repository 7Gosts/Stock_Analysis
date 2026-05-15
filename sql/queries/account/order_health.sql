-- Order/Fill/Journal 对账统计
-- params: symbol (optional)
SELECT
  (SELECT COUNT(*) FROM paper_orders WHERE status = 'pending' AND (symbol = :symbol OR :symbol = '')) AS pending_orders,
  (SELECT COUNT(*) FROM paper_orders WHERE status = 'filled' AND (symbol = :symbol OR :symbol = '')) AS filled_orders,
  (SELECT COUNT(*) FROM paper_fills WHERE (symbol = :symbol OR :symbol = '')) AS total_fills,
  (SELECT COUNT(*) FROM journal_ideas WHERE status IN ('watch', 'pending', 'filled') AND (symbol = :symbol OR :symbol = '')) AS active_ideas,
  (SELECT COUNT(*) FROM account_positions WHERE status = 'open' AND (symbol = :symbol OR :symbol = '')) AS open_positions;