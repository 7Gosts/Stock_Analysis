# 常用 SQL 速查（AI / 人工只读）

本文件是“手工排查 / 运营速查”的 SQL 参考，不等于运行时查询能力层。若要把行情、研报、模拟账户做成统一 Agent 能力，设计说明见 `docs/UNIFIED_DATA_CAPABILITY_ARCHITECTURE.md`。

面向「问数据库里账户、纸单、台账各有多少、最新一行是什么」等场景。**只读**示例；写入以应用代码与 `sql/` 下 DML 为准。表含义见 `docs/DATABASE_DESIGN.md`。

连接：使用项目配置里的 `database.postgres.dsn`（`psql`、DBeaver、或 `python -c "from persistence.db import get_sqlalchemy_engine; ..."`）。

---

## 1. 账户资金（`account_ledger`）

`account_ledger` 是**按时间追加的快照**，查余额要按 `snapshot_time` 取每个 `account_id` 最新一行。

```sql
-- 各币种账户最新一行（余额 / 可用 / 占用保证金 / 权益）
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
ORDER BY account_id, snapshot_time DESC;
```

```sql
-- 单个币种（把 :aid 换成 CNY / USD 等）
SELECT balance, available, used_margin, unrealized_pnl, equity, snapshot_time, reason
FROM account_ledger
WHERE account_id = :aid
ORDER BY snapshot_time DESC
LIMIT 5;
```

---

## 2. 模拟持仓（`account_positions`）

```sql
-- 当前未平仓持仓数量
SELECT COUNT(*) AS open_positions
FROM account_positions
WHERE status = 'open';
```

```sql
-- 未平仓明细（含关联 idea）
SELECT id, account_id, symbol, qty, entry_price, entry_notional, unrealized_pnl, linked_idea_id, opened_at
FROM account_positions
WHERE status = 'open'
ORDER BY opened_at DESC
LIMIT 50;
```

---

## 3. 纸交易：委托与成交（`paper_orders` / `paper_fills`）

```sql
-- 委托单总数、成交笔数
SELECT
  (SELECT COUNT(*) FROM paper_orders) AS paper_orders,
  (SELECT COUNT(*) FROM paper_fills) AS paper_fills;
```

```sql
-- 最近 20 笔成交（开仓 fill_seq=1、平仓 fill_seq=2 为约定）
SELECT fill_id, idea_id, symbol, side, fill_qty, fill_price, fill_notional, fill_seq, fill_time
FROM paper_fills
ORDER BY fill_time DESC
LIMIT 20;
```

```sql
-- 某 idea 是否已有开仓 / 平仓成交
SELECT idea_id, fill_seq, fill_qty, fill_price, fill_time
FROM paper_fills
WHERE idea_id = :idea_id
ORDER BY fill_seq;
```

```sql
-- 最近 20 条委托（含状态）
SELECT order_id, idea_id, symbol, side, status, requested_qty, created_at
FROM paper_orders
ORDER BY created_at DESC
LIMIT 20;
```

---

## 4. 台账想法（`journal_ideas`）

```sql
-- 按状态计数
SELECT status, COUNT(*) AS n
FROM journal_ideas
GROUP BY status
ORDER BY n DESC;
```

```sql
-- 最近更新的 30 条 idea
SELECT idea_id, symbol, interval, direction, status, exit_status, updated_at, plan_type
FROM journal_ideas
ORDER BY updated_at DESC NULLS LAST
LIMIT 30;
```

```sql
-- 某标的未结束单（watch / pending / filled）
SELECT idea_id, status, exit_status, entry_price, stop_loss, tp1, created_at
FROM journal_ideas
WHERE symbol = :sym AND status IN ('watch', 'pending', 'filled')
ORDER BY created_at DESC;
```

---

## 5. 台账事件流（`journal_events`）

```sql
-- 某 idea 最近 30 条事件
SELECT id, event_type, old_status, new_status, event_time, payload
FROM journal_events
WHERE idea_id = :idea_id
ORDER BY event_time DESC
LIMIT 30;
```

```sql
-- 全局最近 50 条事件（审计用）
SELECT idea_id, event_type, event_time
FROM journal_events
ORDER BY event_time DESC
LIMIT 50;
```

---

## 6. 运行时从文件加载的 DML（范例）

以下由 `persistence/sql_loader.load_sql_text()` 加载，**不要**在 psql 里直接当「查询手册」执行（含绑定占位符 `:idea_id` 等）：

| 相对路径 | 用途 |
|----------|------|
| `journal/idea_insert.sql` | 插入一条 `journal_ideas` |
| `journal/idea_upsert.sql` | `ON CONFLICT (idea_id) DO UPDATE` 全量对齐 |
| `account/ledger_append_snapshot.sql` | 追加一条 `account_ledger`（充提、调账、开平仓、MTM 共用） |

Python 示例：

```python
from persistence.sql_loader import load_sql, load_sql_text

raw = load_sql("journal/idea_upsert.sql")
stmt = load_sql_text("journal/idea_upsert.sql")
# conn.execute(stmt, params_dict)
```

### 6.1 充钱 / 调账（走应用 API，勿手写 INSERT）

- **`persistence.account_service.deposit_funds`** / **`withdraw_funds`** / **`adjust_funds`**
- CLI（仓库根）：`python scripts/account_cash_move.py deposit --currency USD --amount 1000 --note "入金"` 等

须已有 `account_ledger` 快照（如 `journal_004` 的 `init`）；否则会失败并打日志。

---

## 7. 说明

- 数据为**程序生成的结构快照**，不是交易所官方成交回报。
- 若表为空，先确认已 `alembic upgrade head` 且分析流程已写入 PG（见 `README.md` 与 `DATABASE_DESIGN.md`）。
