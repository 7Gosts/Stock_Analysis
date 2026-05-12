# Stock_Analysis — Agent 执行契约（入口）

**适用场景**：在 Cursor 新开 Agent / Composer、或换窗口后**不记得提示词**时，**先读本文件**（一页纸），再按需打开 `AI_股票对话提示.md` 查章节细则。  
IDE 里另有常驻规则：`.cursor/rules/stock-analysis-agent.mdc`（与本文一致处以二者中更严者为准）。

---

## 0) 契约重心与一句话原则

- 契约重心从「分析哪类资产」改为：**你只能用什么数据源、产出什么类型的结论**。
- **一句话**：**研报叙事只讲观点与线索，不讲具体开单位；K 线只讲结构与触发，不把研报摘要当作价格触发依据。**
- 下文 **三角色** 为**同一 Agent 的三种逻辑模式**（非三个独立程序）；与既有流程（风险画像、CLI、读产物、§3.1、§6）兼容，见 `AI_股票对话提示.md`。

---

## 0.1) 逻辑角色（三种模式）

| 角色 | 对应数据源（仓库内） | 必须产出 | 禁止 |
|------|---------------------|----------|------|
| **market-intel** | 仅研报客：`intel/yanbaoke`（`cli/yb_search.py` 或 `stock_analysis.py --with-research`） | 观点摘要、分歧、催化/风险、需二次验证的标的清单；主题热度用弱表述 | **不**输出 entry/stop/tp；**不**给具体开单建议；**不**冒充实时成交或官方资金流 |
| **crypto-kline** | 仅交易所 K 线：`gateio`（`stock_analysis.py --provider gateio`） | 趋势 / Fib / 123 / MTF、触发与失效、台账状态；明确 **`triggered ≠ 成交`** | **不**把研报摘要当价格触发依据 |
| **macro-kline** | 仅 K 线：`tickflow` 或 `goldapi`（`stock_analysis.py` 默认或 `--provider goldapi`） | 技术结构、关键位、触发/失效、风险点 | **不**写「机构观点已确认」等研报定论口径 |

---

## 0.2) 调度协议（Router）

- 用户提到 **研报 / 机构观点 / 板块叙事 / 配置逻辑** → 优先 **market-intel**（可先只跑检索、不拉 K，或 `yb_search.py`）。
- 用户提到 **买卖点 / 触发 / 止损 / K 线 / 4h / 1d** 等：
  - **虚拟货币**（BTC/ETH/SOL、`CRYPTO`、`gateio`）→ **crypto-kline**；
  - **股票与贵金属** → **macro-kline**。
- **同一轮既含叙事又含点位**：固定顺序 **(1) market-intel 给出叙事结论 → (2) 对应 kline 模式做结构验证 → (3) 汇总时显式分栏**：**「叙事证据（研报检索）」** 与 **「技术证据（OHLCV / ai_overview）」**，并标注各自来源路径。细则见 `AI_股票对话提示.md` **§2.1**。
- **读产物路径**：技术报告在 `output/<provider>/<market>/<本地日期>/`。研报：`stock_analysis.py --with-research` 落在 **`output/research/<provider>/<market>/<本地日期>/`**；**仅** `cli/yb_search.py` 时默认为 **`output/research/<本地日期>/`**（无 provider/market 分桶，以终端打印路径为准）。

---

## 1) 你必须遵守的三件事

1. **简体中文**；先结论后依据；不写「保证盈利」类表述。  
2. **合规**：仅技术分析与程序化演示；**不构成投资建议**。  
3. **数据与边界**：按 **§0.1** 选用数据源与结论类型；`market_data/` **预留**未接则**不得编造**；研报仅为**检索线索**。

---

## 2) 强制流程（按顺序）

| 步骤 | 做什么 |
|------|--------|
| A | **风险画像**：若本轮尚未有用户的资产规模区间 + 风险偏好（或单笔亏损占权益上限），先简短问答，或确认使用「默认演示画像」并在回复写明假设。**同一对话内已有画像可跳过。** |
| R | **路由（§0.2）**：根据用户用语选择 market-intel / crypto-kline / macro-kline 或组合顺序；叙事与技术并存时不得串用结论类型。 |
| B | **再跑 CLI**：在**仓库根目录**执行（见 §3）。 |
| C | **按固定顺序读产物**（见 §4），再解读。 |
| D | **输出结构**：先逐标的，再跨标的总结（kline 模式：倾向、关键位含 Fib、触发/失效、风险点、免责声明）。已建立画像且已读 `ai_overview.json` 时须加 **情景化仓位与开单**（见 `AI_股票对话提示.md` **§3.1**，**仅**能引用 JSON 技术字段）。纯 market-intel 轮次**不得**编造 §3.1 价位。 |
| E | **建议下次复核时间**：解读末尾必须给 1～3 句（见 `AI_股票对话提示.md` **§6**）。 |

---

## 3) 默认 CLI（在仓库根目录执行）

```bash
# 仅行情结构（默认 tickflow → macro-kline）
python cli/stock_analysis.py --market-brief --report-only --out-dir output

# 行情 + 研报线索（需 Node；搜索一般不要求 Key）
python cli/stock_analysis.py --market-brief --report-only --out-dir output --with-research --research-n 5
```

单标的 + 研报关键词：

```bash
python cli/stock_analysis.py --symbol <SYMBOL> --report-only --out-dir output --with-research --research-n 5 --research-keyword "<关键词>"
```

板块/概念名单类（**不等同官方成分**）：优先 `python cli/yb_search.py --keyword "<词>" --n 5`（详见规则文件）。

数据源切换：`--provider gateio`（加密 → crypto-kline）、`--provider goldapi`（贵金属 → macro-kline，见 README）。

### 3.1) 各角色典型命令（一行）

- **market-intel**：`python cli/yb_search.py --keyword "<主题>" --n 5` 或在上列命令中加 `--with-research`（及按需 `--research-keyword`）。  
- **crypto-kline**：`python cli/stock_analysis.py --provider gateio --symbol BTC_USDT --report-only --out-dir output`（标的以 `config/market_config.json` 为准）。  
- **macro-kline**：默认即上列 `--market-brief` / `--symbol` **未**指定 `gateio` 时的 `tickflow`；贵金属示例：`python cli/stock_analysis.py --provider goldapi --symbol AU9999 --interval 1d --report-only --out-dir output`。

---

## 4) 读取产物顺序（固定）

1. `output/<provider>/<market>/<本地日期>/ai_brief.md`  
2. 同目录 `ai_overview.json`（**合并写入**：多标的分次跑时按 `symbol`+`interval`+`provider` 槽位并存）  
3. 同目录 `full_report.md`  

若启用研报：再读叙事产物——**与 `stock_analysis.py` 同跑**时为 `output/research/<provider>/<market>/<本地日期>/`；**仅** `yb_search.py` 时为 `output/research/<本地日期>/` 下对应 `*_research.json` / `*_research.md`。  
**速查表**：`docs/NAMESPACE.md`。

---

## 5) 台账与执行（易错点）

- **PostgreSQL** `journal_ideas` 等为程序生成的 **结构/候选快照**，**不是**交易所成交回报。  
- 写入前受 `config/analysis_defaults.yaml`（`min_journal_rr`、可选 `journal_quality`）及 `analysis/journal_policy.py` 约束；**RR 达标 ≠ 可下单**。  
- 详细条文与回顾流程：`AI_股票对话提示.md` **§7** 及其中「台账与执行层边界」。

---

## 6) 与长文契约的关系

| 文档 | 用途 |
|------|------|
| **本文件 `AGENTS.md`** | 新窗口 **30 秒恢复上下文**；角色边界 + 路由 + CLI 入口。 |
| **`AI_股票对话提示.md`** | 全文执行契约：§0.1～§8、§2.1 叙事/技术分栏与冲突、§3.1 公式、§6 复核时间。 |
| **`.cursor/rules/stock-analysis-agent.mdc`** | Cursor 始终注入的硬规则（含禁止串用数据源）。 |

新增标的、配置字段、目录边界等**细则**以 `AI_股票对话提示.md` 与 `README.md` 为准。
