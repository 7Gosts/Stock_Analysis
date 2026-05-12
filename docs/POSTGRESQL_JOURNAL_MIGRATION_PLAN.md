# PostgreSQL 台账改造方案（可直接交给代码 Agent 执行）

本文档用于指导将当前基于 `trade_journal.jsonl` 的台账存储升级为 PostgreSQL 存储。

目标读者：执行重构的代码 Agent / 工程师。

适用范围：当前仓库 `Stock_Analysis` 的台账写入、状态更新、统计查询与后续回放能力。

合规边界：仅技术分析与程序化演示；不构成投资建议；不实现自动下单。

---

## 1. 改造目标

将当前 JSONL 文件式台账升级为可查询、可回放、可审计的 PostgreSQL 结构，同时尽量不影响现有分析逻辑和飞书/HTTP 主链路。

本次改造的具体目标：

1. 保留当前台账字段语义，不改变策略事实含义。
2. 保留 Python 状态机，不把业务逻辑下沉到数据库触发器。
3. 引入 repository 抽象层，避免业务代码直接依赖 PostgreSQL。
4. 支持迁移期 `jsonl + postgres` 双写。
5. 为后续统计、复盘、策略回放、API 查询提供数据库基础。

---

## 2. 当前实现概况

当前台账核心由以下模块组成：

1. `app/orchestrator.py`
2. `app/journal_service.py`
3. `analysis/trade_journal.py`
4. `analysis/ledger_stats.py`

### 2.1 当前数据流

当前链路大致如下：

1. `app/orchestrator.py` 生成候选 `idea`。
2. `app/journal_service.py` 读取 `output/trade_journal.jsonl`。
3. `analysis/trade_journal.py` 用最新 K 线驱动状态机更新。
4. `analysis/journal_policy.py` 负责写入门槛判断。
5. `analysis/ledger_stats.py` 全量扫描 JSONL 生成统计与 Markdown。

### 2.2 当前模式的问题

JSONL 模式在数据量小、单机运行时够用，但存在以下限制：

1. 历史状态变化没有结构化审计轨迹。
2. 统计依赖全量扫文件，规模上来后查询和聚合变慢。
3. API / 飞书后续若要查历史记录、做多维筛选，会越来越依赖 Python 侧手工聚合。
4. 分析快照与台账关联关系弱，难以回放“当时为何产生该候选”。

---

## 3. 本次改造的非目标

以下内容不在本轮改造范围内：

1. 不重写 `analysis/trade_journal.py` 的状态机逻辑。
2. 不重构行情 provider 客户端。
3. 不实现数据库侧存储过程、触发器驱动状态机。
4. 不在本轮直接上向量数据库。
5. 不要求第一阶段删除 JSONL 文件输出。

---

## 4. 目标架构

```mermaid
flowchart TD
  ORCH[orchestrator / journal_service] --> REPO[JournalRepository]
  REPO --> JSONL[Jsonl Repository]
  REPO --> PG[PostgreSQL Repository]

  PG --> IDEAS[journal_ideas]
  PG --> EVENTS[journal_events]
  PG --> SNAP[analysis_snapshots]

  STATS[ledger_stats / API / Bot] --> REPO
```

设计原则：

1. Python 继续负责状态机和业务规则。
2. PostgreSQL 负责结构化持久化、查询、统计基础设施。
3. 存储层通过 repository 抽象，业务代码不直接耦合文件或 SQL。

---

## 5. 存储模型建议

不建议把当前 JSONL 记录直接搬成单表大 JSON。最合适的是三张表：

1. `journal_ideas`
2. `journal_events`
3. `analysis_snapshots`

### 5.1 `journal_ideas`

用途：保存每条台账候选的“当前最新状态快照”。

建议字段：

```sql
create table journal_ideas (
    id bigserial primary key,
    idea_id varchar(64) not null unique,

    symbol varchar(32) not null,
    asset_name varchar(128),
    market varchar(16) not null,
    provider varchar(32) not null,
    interval varchar(16) not null,

    plan_type varchar(16) not null,
    direction varchar(16) not null,
    status varchar(32) not null,
    exit_status varchar(32),

    entry_type varchar(16),
    order_kind_cn varchar(16),

    entry_price numeric(20,8),
    entry_zone_low numeric(20,8),
    entry_zone_high numeric(20,8),
    signal_last numeric(20,8),
    stop_loss numeric(20,8),

    tp1 numeric(20,8),
    tp2 numeric(20,8),
    rr numeric(12,4),

    wyckoff_bias varchar(32),
    mtf_aligned boolean,
    structure_flags jsonb,
    tags jsonb,

    strategy_reason text,
    lifecycle_v1 jsonb,
    meta jsonb,

    created_at timestamptz not null,
    updated_at timestamptz not null,
    valid_until timestamptz,
    filled_at timestamptz,
    closed_at timestamptz,

    fill_price numeric(20,8),
    closed_price numeric(20,8),
    realized_pnl_pct numeric(12,4),
    unrealized_pnl_pct numeric(12,4)
);
```

字段设计原则：

1. 高频查询字段使用结构化列。
2. 易演化字段使用 `jsonb`。
3. 仍保留 `idea_id` 作为主业务唯一键。

### 5.2 `journal_events`

用途：保存每条候选的生命周期事件，便于审计、回放、回测和问题排查。

建议字段：

```sql
create table journal_events (
    id bigserial primary key,
    idea_id varchar(64) not null references journal_ideas(idea_id) on delete cascade,
    event_type varchar(32) not null,
    old_status varchar(32),
    new_status varchar(32),
    event_time timestamptz not null,
    payload jsonb not null default '{}'::jsonb
);
```

建议事件类型最少包括：

1. `idea_created`
2. `status_changed`
3. `filled`
4. `expired`
5. `closed_tp`
6. `closed_sl`
7. `mark_to_market_updated`
8. `journal_gate_rejected`

### 5.3 `analysis_snapshots`

用途：保存台账生成时的分析快照，支持未来回放“为什么产生这条候选”。

建议字段：

```sql
create table analysis_snapshots (
    id bigserial primary key,
    idea_id varchar(64),
    symbol varchar(32) not null,
    provider varchar(32) not null,
    interval varchar(16) not null,
    snapshot_time timestamptz not null,
    trend varchar(32),
    last_price numeric(20,8),
    fib_zone varchar(64),
    risk_flags jsonb,
    fixed_template jsonb,
    raw_stats jsonb,
    source_session_dir text
);
```

说明：

1. `idea_id` 可为空，因为不是每次分析都会生成台账候选。
2. 若未来要做“某条台账回放当时分析背景”，这张表很关键。

---

## 6. 为什么不是单表 JSONB

不建议只建一张 `journal_entries` 大表，把整条 JSONL 原样塞进 `jsonb`。原因如下：

1. 当前状态查询会变复杂。
2. 活跃候选筛选、按状态统计、按 symbol/interval 查询会频繁解包 JSON。
3. 后续事件审计会缺失结构化语义。
4. 分析快照与台账本身是两类不同数据。

推荐方案是：

1. 结构化列承载核心字段。
2. `jsonb` 承载易演化扩展字段。
3. 状态演进用事件表记录。

---

## 7. 连接池与数据库接入建议

当前仓库不是高并发数据库系统，因此不要把连接池配置得过重。

### 7.1 建议技术栈

新增依赖建议：

```txt
SQLAlchemy>=2.0
psycopg[binary]>=3.2
alembic>=1.13
```

推荐原因：

1. SQLAlchemy 2.0 稳定且主流。
2. `psycopg` 是当前 PostgreSQL Python 驱动主流选择。
3. `alembic` 适合管理 schema 迁移。

### 7.2 建议新增模块

1. `app/db.py`
2. `app/journal_repository.py`
3. `app/journal_repository_jsonl.py`
4. `app/journal_repository_pg.py`

### 7.3 连接池建议

建议在 `app/db.py` 中创建 engine：

```python
from sqlalchemy import create_engine

engine = create_engine(
    dsn,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True,
)
```

说明：

1. `pool_size=5` 对当前 CLI、FastAPI、飞书机器人负载足够。
2. `pool_pre_ping=True` 避免连接失效导致的长时间报错。
3. 不建议第一阶段使用异步 ORM，当前仓库并不需要。

---

## 8. 配置设计建议

建议在 `config/analysis_defaults.yaml` 中新增：

```yaml
database:
  backend: "jsonl"   # jsonl | postgres | dualwrite
  postgres:
    dsn: "postgresql+psycopg://stock_user:stock_pass@127.0.0.1:5432/stock_analysis"
    pool_size: 5
    max_overflow: 10
    pool_pre_ping: true
    echo: false
```

说明：

1. `jsonl` 用于兼容当前模式。
2. `dualwrite` 用于平滑迁移。
3. `postgres` 用于迁移完成后单独使用数据库。

建议在 `config/runtime_config.py` 中新增读取函数：

1. `get_database_config()`
2. `get_postgres_dsn()`
3. `get_database_backend()`

---

## 9. Repository 抽象设计

必须先引入 repository，避免业务代码直接散落 SQL。

建议接口：

```python
class JournalRepository(Protocol):
    def list_entries(self) -> list[dict[str, Any]]: ...
    def save_entries(self, entries: list[dict[str, Any]]) -> None: ...
    def append_idea(self, idea: dict[str, Any]) -> None: ...
    def update_idea(self, idea_id: str, patch: dict[str, Any]) -> None: ...
    def has_active_idea(
        self,
        *,
        symbol: str,
        interval: str,
        direction: str,
        plan_type: str,
    ) -> bool: ...
    def append_event(self, *, idea_id: str, event_type: str, payload: dict[str, Any]) -> None: ...
```

说明：

1. 第一阶段可以保守保留 `list_entries` / `save_entries` 这类粗粒度接口，便于最小侵入迁移。
2. 第二阶段再细化为更贴近数据库的查询和 patch 接口。

---

## 10. 与当前代码的衔接方式

### 10.1 `app/journal_service.py`

这是首个应该改造的业务入口。

当前职责：

1. 读取 JSONL
2. 更新旧条目
3. 追加新候选
4. 写回 JSONL
5. 导出统计

改造目标：

1. 从直接文件读写改为依赖 `JournalRepository`。
2. 状态机逻辑仍保留在 Python。
3. 写入新候选或状态变化时，追加 `journal_events`。

### 10.2 `analysis/trade_journal.py`

当前逻辑建议保持不动，继续负责：

1. `watch/pending -> filled/expired`
2. `filled -> closed(tp/sl) / float_*`

改造方式：

1. 状态机输出 patch。
2. repository 层负责持久化 patch。
3. 每次状态变化同步写一条 event。

### 10.3 `analysis/ledger_stats.py`

当前是全量读 JSONL 再聚合。

迁移建议：

1. 第一阶段不改业务输出格式。
2. 第二阶段新增基于 PostgreSQL 的聚合查询函数。
3. 第三阶段再切换默认统计来源。

不要第一阶段就把整份文件重写成 SQL 版本，风险太高。

---

## 11. 双写迁移方案

推荐采用四阶段迁移，避免一次性切换。

### 阶段 1：抽象层接入

目标：repository 抽象先落地，默认仍用 JSONL。

任务：

1. 新增 `JournalRepository` 抽象。
2. 新增 `JsonlJournalRepository`。
3. 改造 `journal_service.py` 使用 repository。

验收：

1. 行为与当前 JSONL 版本一致。
2. 所有现有台账测试继续通过。

### 阶段 2：PostgreSQL 双写

目标：保留 JSONL 输出，同时新增 PostgreSQL 写入。

任务：

1. 新增 `PostgresJournalRepository`。
2. 新增 `DualWriteJournalRepository`。
3. 所有新增 idea、状态变化、快照都同时写入数据库。

验收：

1. JSONL 和 PostgreSQL 结果一致。
2. 双写失败时日志可观测，不静默吞错。

### 阶段 3：统计读 PostgreSQL

目标：查询和统计先切换到数据库，写入仍双写。

任务：

1. 新增 SQL 聚合函数或视图。
2. `ledger_stats.py` 支持从 PostgreSQL 读取。

验收：

1. 近 7/30 天统计与 JSONL 版本一致。
2. API / Bot 可以开始查询数据库视图。

### 阶段 4：主存储切 PostgreSQL

目标：数据库成为主读写源，JSONL 降级为导出或备份。

任务：

1. 配置切到 `backend=postgres`。
2. 保留 JSONL 导出开关，用于离线备份。

验收：

1. 主链路不再依赖全量扫描 JSONL。
2. 台账读写、统计、历史查询全部可从 PostgreSQL 完成。

---

## 12. 索引建议

建议最少建立以下索引：

```sql
create unique index uq_journal_ideas_idea_id on journal_ideas (idea_id);
create index idx_journal_ideas_symbol_interval on journal_ideas (symbol, interval);
create index idx_journal_ideas_status on journal_ideas (status);
create index idx_journal_ideas_created_at on journal_ideas (created_at desc);
create index idx_journal_ideas_market_status on journal_ideas (market, status);
create index idx_journal_events_idea_id_event_time on journal_events (idea_id, event_time desc);
create index idx_journal_ideas_structure_flags_gin on journal_ideas using gin (structure_flags);
create index idx_journal_ideas_meta_gin on journal_ideas using gin (meta);
```

这些索引覆盖当前最典型查询：

1. 活跃候选查询
2. 按 symbol / interval 查询
3. 按 status 查询
4. 按时间窗口做统计
5. 查看某个 `idea_id` 的生命周期事件

---

## 13. 统计层升级建议

当前 `analysis/ledger_stats.py` 中的统计逻辑非常适合未来迁到 SQL 视图或物化视图。

建议后续增加：

1. `journal_active_ideas_v`
2. `journal_symbol_stats_v`
3. `journal_market_stats_v`
4. `journal_period_stats_v`

第一阶段不要求全部视图落地，但应在 repository 设计中预留接口。

---

## 14. 数据迁移建议

建议新增一次性迁移脚本，例如：

1. `scripts/migrate_journal_jsonl_to_pg.py`

迁移脚本职责：

1. 读取现有 `output/trade_journal.jsonl`
2. 解析每条 idea
3. 写入 `journal_ideas`
4. 为每条记录至少补一条 `idea_created` 事件
5. 可选写入 `analysis_snapshots`

迁移原则：

1. 迁移不应修改原 JSONL 文件。
2. 迁移支持重复执行时幂等，基于 `idea_id` 去重。
3. 迁移日志应输出成功数、跳过数、失败数。

---

## 15. 测试补充建议

代码 Agent 在改造数据库时，必须补下列测试。

### 15.1 Repository 测试

新增建议：

1. `test_jsonl_repository_roundtrip`
2. `test_pg_repository_insert_and_update`
3. `test_dualwrite_repository_consistency`

### 15.2 状态机持久化测试

新增建议：

1. `test_status_transition_creates_event`
2. `test_filled_to_tp_persists_closed_state`
3. `test_expired_pending_persists_event`

### 15.3 统计测试

新增建议：

1. PostgreSQL 版 period stats 与 JSONL 版结果对比测试
2. 活跃候选查询测试
3. symbol 维度聚合测试

---

## 16. 性能与磁盘空间建议

对于当前仓库体量，PostgreSQL 不会成为明显性能瓶颈。瓶颈仍主要在：

1. 行情请求
2. 技术分析计算
3. LLM 调用

数据库侧建议：

1. 初期单机 PostgreSQL 即可。
2. 连接池使用小池配置。
3. 不要在第一阶段就把复杂业务逻辑下沉到 SQL。

磁盘空间量级建议：

1. PostgreSQL 程序与依赖通常在数百 MB 级别。
2. 初始化空数据库集群通常额外占用数百 MB 以内。
3. 对当前项目，预留 `1GB 到 2GB` 足够宽松。

---

## 17. 建议的代码执行顺序

代码 Agent 应按以下顺序实施，每步都保持仓库可运行。

### Step 1

新增数据库配置读取函数与依赖，仍保持默认 `backend=jsonl`。

### Step 2

新增 `JournalRepository`、`JsonlJournalRepository`，改造 `journal_service.py` 依赖抽象层。

### Step 3

新增 `PostgresJournalRepository`、数据库模型和 Alembic 迁移。

### Step 4

新增 `DualWriteJournalRepository`，接通双写。

### Step 5

新增 JSONL -> PostgreSQL 迁移脚本。

### Step 6

扩展 `ledger_stats.py` 支持 PostgreSQL 聚合读取。

### Step 7

增加事件查询、活跃候选查询、按 symbol/period 聚合查询接口。

---

## 18. 风险与实现约束

代码 Agent 在改造过程中必须遵守：

1. 不要删除现有 JSONL fallback。
2. 不要直接在业务代码里散布 SQL 语句。
3. 不要把状态机改写成数据库触发器逻辑。
4. 不要把所有字段都僵硬结构化，适当保留 `jsonb` 扩展能力。
5. 不要第一阶段就强推所有统计切 PostgreSQL。

---

## 19. 交付拆单建议

建议拆成以下代码工单：

1. 工单 A：数据库配置、连接池、依赖接入
2. 工单 B：Repository 抽象 + JSONL 实现迁移
3. 工单 C：PostgreSQL 表模型 + Alembic 初始迁移
4. 工单 D：Dual write + 迁移脚本
5. 工单 E：统计查询切 PostgreSQL + 测试补齐

资源有限时推荐优先级：B > C > D > E > A 的扩展优化。

说明：A 是基础接线，但真正降低业务耦合的关键是 B。

---

## 20. 预期结果

完成本方案后，理想状态应为：

1. 台账主数据既可保留文件导出，也可在 PostgreSQL 中结构化存储。
2. 任意一条 `idea_id` 都可追溯当前状态与历史事件。
3. 近 7/30 天统计可直接从数据库读取，不再依赖全量扫描 JSONL。
4. 后续飞书、HTTP、评测、回放能力都可建立在同一份数据库事实之上。

---

*本文档用途：供代码 Agent 直接执行 PostgreSQL 改造。若实现过程中出现边界不清，优先遵循“Python 负责状态机，PostgreSQL 负责持久化与查询，repository 负责隔离存储实现”的原则。*