# 模拟成交系统升级计划（委托记录 + 成交表）

本文档用于指导将当前基于 `journal_ideas + journal_events` 的简化模拟成交模型，升级为更清晰的“策略台账 / 委托记录 / 成交记录”三层模型。

目标读者：执行改造的代码 Agent / 工程师。

适用范围：当前仓库 `Stock_Analysis` 的台账、模拟成交、状态更新、统计与后续 paper trading 演进。

合规边界：仅技术分析与程序化演示；不构成投资建议；不实现真实自动下单。

---

## 1. 结论先行

当前仓库已经具备“基于 K 线驱动台账状态更新”的第一版模拟成交能力：

1. 能生成候选 idea。
2. 能检查是否进入 `entry_zone`。
3. 能把状态改为 `filled / expired / closed`。
4. 能记录 `mark_to_market_updated / closed_tp / closed_sl` 等事件。

但当前模型把三件事折叠到了一张主表里：

1. 策略想法。
2. 模拟委托。
3. 模拟成交结果。

这对于第一阶段够用，但不适合作为长期数据模型。

**推荐演进方向不是把 `journal_ideas` 直接重命名成成交表，而是：**

1. 保留 `journal_ideas` 作为策略/候选主表。
2. 新增 `paper_orders` 作为模拟委托表。
3. 新增 `paper_fills` 作为模拟成交表。
4. `journal_events` 保留为审计流水。

一句话：

**`journal_ideas` 管“为什么要做”，`paper_orders` 管“准备怎么做”，`paper_fills` 管“实际模拟成交了什么”。**

---

## 2. 为什么不要直接把当前台账等同为成交表

当前的 `journal_ideas` 更像“策略候选 + 当前业务状态快照”，而不是严格意义的订单或成交。

它现在包含的信息有：

1. 分析结论：`direction`、`wyckoff_bias`、`strategy_reason`。
2. 计划参数：`entry_zone`、`stop_loss`、`take_profit_levels`。
3. 当前状态：`status`、`exit_status`、`filled_at_utc`、`closed_at_utc`。

这意味着它回答的是：

1. 这条交易想法是什么。
2. 它当前处于什么状态。

但它没有很好地区分：

1. 是否真的派生过一个模拟委托。
2. 一条 idea 是否可能拆成多次委托。
3. 委托是否部分成交。
4. 成交价、成交量、手续费、滑点是否需要独立记录。

因此，如果直接把当前台账业务特化成“委托记录”，会把未来的扩展空间锁死。

**正确的做法是分层，而不是硬改语义。**

---

## 3. 推荐目标模型

推荐保留当前三张表，再新增两张表：

1. `journal_ideas`
2. `journal_events`
3. `analysis_snapshots`
4. `paper_orders`
5. `paper_fills`

### 3.1 `journal_ideas`

用途：保存策略候选及其当前总体状态。

保留当前定位，不要改成订单表。

建议新增但不强制本轮完成的字段：

1. `execution_status`：`not_ordered / ordered / partially_filled / fully_filled / closed`
2. `primary_order_id`：当前主要委托 ID
3. `last_fill_id`：最近一次成交 ID

但第一阶段完全可以不加这几个字段，避免波及过大。

### 3.2 `paper_orders`

用途：保存每条 idea 派生出来的模拟委托。

建议字段：

```sql
create table paper_orders (
    id bigserial primary key,
    order_id varchar(64) not null unique,
    idea_id varchar(64) not null references journal_ideas(idea_id) on delete cascade,

    symbol varchar(32) not null,
    market varchar(16) not null,
    provider varchar(32) not null,
    interval varchar(16) not null,

    side varchar(16) not null,
    order_type varchar(16) not null,
    tif varchar(16),

    requested_qty numeric(20,8),
    requested_notional numeric(20,8),

    limit_price numeric(20,8),
    trigger_price numeric(20,8),
    stop_price numeric(20,8),

    status varchar(32) not null,
    status_reason varchar(64),

    created_at timestamptz not null,
    updated_at timestamptz not null,
    submitted_at timestamptz,
    cancelled_at timestamptz,
    expired_at timestamptz,

    simulation_rule jsonb,
    meta jsonb
);
```

说明：

1. 一条 idea 至少可对应 0 或 1 条 order；后续允许扩到多条。
2. `status` 建议最少支持：`pending / working / filled / partially_filled / cancelled / expired / rejected`。
3. `simulation_rule` 用于记录这条委托采用的模拟成交规则，例如“限价触碰即成交”“同 bar 双击先止损”。

### 3.3 `paper_fills`

用途：保存每条模拟委托的成交结果。

建议字段：

```sql
create table paper_fills (
    id bigserial primary key,
    fill_id varchar(64) not null unique,
    order_id varchar(64) not null references paper_orders(order_id) on delete cascade,
    idea_id varchar(64) not null references journal_ideas(idea_id) on delete cascade,

    symbol varchar(32) not null,
    side varchar(16) not null,

    fill_qty numeric(20,8),
    fill_price numeric(20,8) not null,
    fill_notional numeric(20,8),
    fee numeric(20,8),
    fee_currency varchar(16),
    slippage_bps numeric(12,4),

    fill_time timestamptz not null,
    fill_seq int,
    fill_source varchar(32) not null,
    meta jsonb
);
```

说明：

1. 第一阶段哪怕每条 order 只允许一条 fill，这张表也值得先建。
2. 后续如果要支持部分成交、多次成交，这张表直接接得住。
3. `fill_source` 可取值：`paper_bar_touch / paper_bar_close / paper_next_open / imported_exchange`。

---

## 4. 最小落地原则

这次改造的重点是“升级数据模型”，不是“重写整个状态机”。

所以第一阶段应遵守四条原则：

1. 保留 `analysis/trade_journal.py` 当前状态机主体。
2. 保留 `journal_ideas` 作为主业务对象。
3. 在状态机判断 `filled` 时同步生成 `paper_orders + paper_fills`。
4. 先做一单一委托一成交，不上部分成交和复杂撮合。

这样改动最小，也最容易验收。

---

## 5. 推荐执行顺序

### Phase A：建模，不改业务入口

目标：先把表和 repository 接口准备好。

改动内容：

1. 新增 Alembic migration：创建 `paper_orders`、`paper_fills`。
2. 新增 repository 抽象或 service：
   1. `app/paper_trade_repository.py`
   2. `app/paper_trade_repository_pg.py`
3. 暂不改 JSONL 兼容层，paper trading 新能力先只落 PostgreSQL。

验收标准：

1. 数据库成功建两张新表。
2. 不影响现有 `journal_ideas / journal_events` 路径。

### Phase B：在 filled 路径上生成 order/fill

目标：当 idea 首次被判定为 `filled` 时，自动补一条模拟委托和一条模拟成交。

改动点建议：

1. `analysis/trade_journal.py`
2. `app/journal_service.py`

推荐做法：

1. 不在 `update_idea_with_rows()` 里直接写库。
2. 仍由 `update_idea_with_rows()` 只返回状态变化结果。
3. 在 `app/journal_service.py` 检测到 `watch/pending -> filled` 时：
   1. 先写 `journal_events.filled`
   2. 再调用 `paper_trade_service.create_order_and_fill_from_idea(...)`

理由：

1. `analysis/trade_journal.py` 应维持纯规则函数属性。
2. 写库、副作用、生成 ID 更适合放 service 层。

验收标准：

1. 每条首次变成 `filled` 的 idea，都能对应生成 1 条 `paper_orders`。
2. 同时生成 1 条 `paper_fills`。
3. 重跑同一轮不会重复生成第二条。

### Phase C：平仓时补出场成交

目标：当 `filled -> closed(tp/sl)` 时，新增一条出场成交。

实现建议：

1. 入场 fill 和出场 fill 都写进 `paper_fills`。
2. 用 `fill_seq=1` 表示入场，`fill_seq=2` 表示出场。
3. `paper_orders` 第一阶段仍可只保留 1 条 order，把“开仓委托”作为主 order；出场 fill 可用 `meta` 标记 `close_reason=tp/sl`。

更完整但复杂的方案是：

1. 开仓单单独一条 order。
2. 止盈/止损单分别派生 exit order。

但这会让第一阶段范围变大，不建议马上上。

### Phase D：统计层接入 fill 视角

目标：让统计不再只依赖 idea 状态，也能读取 fill 数据。

改动点：

1. `analysis/ledger_stats.py`

第一阶段只做两件事：

1. 保持原统计逻辑不坏。
2. 新增一小块统计：
   1. `paper_order_count`
   2. `paper_fill_count`
   3. `filled_idea_without_fill_count`

这个第三项非常关键，用来监控数据一致性。

---

## 6. 业务语义约定

为了防止其他 AI 在执行时把语义搞混，必须统一以下定义。

### 6.1 idea

`journal_ideas` 里的记录，表示一次策略候选/交易想法。

### 6.2 order

`paper_orders` 里的记录，表示这条 idea 派生出来的一次模拟委托。

### 6.3 fill

`paper_fills` 里的记录，表示这次模拟委托的一次成交结果。

### 6.4 当前状态映射

第一阶段建议按以下方式映射：

1. `idea.status in {watch, pending}`：还没有成交。
2. `idea.status == filled`：至少已有 1 条入场 fill。
3. `idea.status == closed and exit_status == tp/sl`：应已有入场 fill + 出场 fill。

重要：

**未来要逐步让 `filled` 的判断依赖 `paper_fills` 是否存在，而不是完全依赖 idea 表自身字段。**

第一阶段先双轨存在，避免改动过大。

---

## 7. 文件级执行清单

以下清单按优先级排序，其他 AI 应按顺序执行。

### 7.1 数据库层

新增文件：

1. `alembic/versions/journal_002_paper_orders_fills.py`

内容：

1. 创建 `paper_orders`
2. 创建 `paper_fills`
3. 建必要索引：
   1. `paper_orders(idea_id)`
   2. `paper_orders(status, created_at)`
   3. `paper_fills(order_id, fill_time)`
   4. `paper_fills(idea_id, fill_time)`

### 7.2 repository/service 层

新增文件：

1. `app/paper_trade_service.py`

建议函数：

1. `create_entry_order_and_fill(...)`
2. `create_exit_fill(...)`
3. `has_entry_fill_for_idea(idea_id)`
4. `has_exit_fill_for_idea(idea_id)`

说明：

1. 这层先只支持 PostgreSQL。
2. 若当前 backend 不是 `postgres/dualwrite`，可以 no-op 或仅 log warning。

### 7.3 业务层

修改文件：

1. `app/journal_service.py`

改造要求：

1. 在识别到 `filled` 事件时调用 `create_entry_order_and_fill(...)`
2. 在识别到 `closed_tp / closed_sl` 时调用 `create_exit_fill(...)`
3. 保证幂等：重复跑同一轮不会重复插入 order/fill

幂等建议：

1. 以 `idea_id + fill_seq` 做逻辑唯一判断
2. 或在 service 层先查是否已有相同角色的 fill

### 7.4 统计与监控层

修改文件：

1. `analysis/ledger_stats.py`

新增输出：

1. idea 数
2. order 数
3. fill 数
4. `filled` 但无 entry fill 的条数
5. `closed` 但无 exit fill 的条数

---

## 8. 第一阶段不要做的事

为了防止其他 AI 把范围做炸，这些事情明确不做：

1. 不把 `journal_ideas` 直接替换成 `paper_orders`
2. 不删除 `status / filled_at_utc / closed_at_utc`
3. 不引入真实交易所 API
4. 不做盘口级撮合模拟
5. 不做部分成交
6. 不做多 order 拆单
7. 不改飞书主链路

原因：这些都属于第二阶段及以后。

---

## 9. 验收标准

执行完成后至少满足以下验收项：

1. 跑一次 `ETH_USDT 4h` 分析，若新 idea 首次成交：
   1. `journal_ideas` 有记录
   2. `journal_events` 有 `filled`
   3. `paper_orders` 新增 1 条
   4. `paper_fills` 新增 1 条 entry fill
2. 若后续平仓：
   1. `journal_events` 有 `closed_tp` 或 `closed_sl`
   2. `paper_fills` 新增 1 条 exit fill
3. 重复跑同一轮分析：
   1. 不会重复插入相同 entry fill
   2. 不会重复插入相同 exit fill
4. `dualwrite` 模式下：
   1. JSONL 主路径不坏
   2. PostgreSQL 路径同步成功

---

## 10. 建议交给代码 Agent 的执行指令

可以直接把下面这段喂给其他 AI：

```text
请基于 docs/PAPER_TRADING_ORDER_FILL_PLAN.md 执行第一阶段改造：

1. 新增 Alembic migration，创建 paper_orders 和 paper_fills 两张表。
2. 新增 app/paper_trade_service.py，只支持 PostgreSQL 路径即可。
3. 修改 app/journal_service.py：
   - 当 idea 首次从 watch/pending 变为 filled 时，生成一条 entry order 和一条 entry fill。
   - 当 idea 从 filled 变为 closed_tp / closed_sl 时，生成一条 exit fill。
4. 保持 analysis/trade_journal.py 只负责状态判断，不直接写库。
5. 保证幂等，避免重复插入 order/fill。
6. 为新增逻辑补最小单测和集成验证。

注意：
- 不要删除现有 journal_ideas / journal_events 逻辑。
- 不要把 journal_ideas 直接重命名成 order 表。
- 不要实现真实交易所下单。
- 只做 PostgreSQL 路径，JSONL 保持兼容即可。
```

---

## 11. 最终建议

如果你的业务目标是“当前 paper trading 能跑通、能复盘、能向真实交易系统演进”，那么最稳的路线是：

1. 保留当前 `journal_ideas` 作为策略主表。
2. 新增 `paper_orders`。
3. 新增 `paper_fills`。
4. 逐步把成交语义从 `idea.status == filled`，迁移为“存在 entry fill”。

这条路线的优点是：

1. 不会破坏当前系统能跑的部分。
2. 能快速得到更清晰的执行层数据。
3. 后续若要接真实交易 API，也能自然扩展，而不用推倒重来。