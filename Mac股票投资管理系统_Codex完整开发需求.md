# Mac 股票投资管理系统：完整开发需求与 Codex 实施规格书

> **项目代号**：Money Portfolio Desktop（可改名）  
> **文档版本**：1.0（基于上传 Excel 与 SQLite DDL 核验）  
> **目标平台**：macOS，优先 Apple Silicon，兼容性视实际开发机器验证  
> **主要语言**：Python；**GUI**：PySide6 / Qt Widgets  
> **优先级**：P0 数据正确性 > P0 Excel 兼容核对 > P0 可用桌面界面 > P1 深度分析 > P2 自动化高级功能  
> **交付模式**：可运行源代码 + 自动测试 + macOS `.app` 打包方案 + 安装/使用文档  
> **重要事实**：当前只提供了 Excel 工作簿和 SQLite 建表 SQL，**尚未提供真正的 SQLite 数据库文件及原始计算程序源码**。因此不得谎称已完成真实数据库验证，须在获得数据库后运行对应核对与验收。

---

## 0. Codex 必须先阅读的执行指令

你（Codex）是这个软件的实现者。请将本文件作为**需求与验收契约**，不是讨论稿。首次执行时：

1. 阅读本文件全文、`README_交给Codex的步骤.md`、`AGENTS.md`、`references/个人投资总览.xlsx`、`references/sqlite_schema.sql`；优先读取仓库现有源码、测试、README、AGENTS 等（若存在）。
2. **不得重建/覆盖用户原始数据库**；第一期以只读连接为前提；必要的缓存、设置、临时文件存放在应用数据目录或测试隔离目录。
3. 不可从 Excel 的展示结果推断所有底层业务规则都已明确；Excel 中的 `取值逻辑`、`总览取值逻辑` 是明确给出的重要口径来源。遇到它们与数据库约束冲突，列出 `DECISIONS.md` 及差异报告，不能暗改。
4. 先做 schema introspection 和可运行的项目骨架，再逐模块编写**纯计算函数、测试、数据查询、界面**；每一步实际运行测试，不得仅写空壳页面。
5. 所有金额运算使用 `Decimal` 或固定点整数（数据库 `_e8`），禁止 Python `float` 做财务核心计算。
6. **分别处理券商成交账本与人员归属账本**，不能把 `broker_trades` 与 `person_operations` 相加作为交易总额。
7. 实现“Excel 兼容指标”和“严格投资统计指标”的独立计算器；第一期以 Excel 兼容口径为迁移验收重点，严格口径只在有充分数据和明确成本方法时开放。
8. 完成一个模块必须给出：改动文件、设计原因、复用的表、计算口径、测试覆盖、运行命令、未解决问题。
9. 任何代码修改不影响已有数据导入/同步功能；后续写操作须通过显式开关、备份、事务、审计。第一期不得直接执行 `DELETE`、`UPDATE`、`INSERT` 到源库。
10. **先实现完整可打开的 Mac 原生窗口和关键数据页面，再逐步做高级动画/美化**；禁止为了图表好看虚构投资金额。

### 0.1 源文件真实性与局限

| 文件 | 已确认的信息 | 限制 |
|---|---|---|
| `references/个人投资总览.xlsx` | 13 Sheet、27 张 Excel 图表、首页 18 个 KPI；数据/公式/说明可用于黄金样本核对 | Excel 是特定时点快照；其公式、缓存值与数据日期可能不完全一致；不是实时价格来源 |
| `references/sqlite_schema.sql` | 14 个业务表、7 个数据检查视图、相关索引 | 只有建表和视图定义，不能证明实际数据库已存在全部表、全部数据或历史行情足够完整 |
| 尚待提供的 `.db` / `.sqlite` | 实际交易和报价的权威数据库 | 缺失前只能以**合成样本**构建和验证引擎，真实结果不可声称已对齐 |
| 原始投资计算程序（若有） | 可复原拆并股、同日配对、历史基准、微操策略的精确处理 | 未提供前，模糊的特殊规则必须标注“待核验”，不得编造成事实 |

### 0.2 源 Excel 的已知冲突，纳入需求

- 首页显示“统计日：2026-10-15”，但“每日持仓收益”含有 `2026-10-16` 的收益记录。因此必须分别显示**持仓估值日 / 收益数据最新日 / 行情最新报价时间 / 数据同步时间**；禁止合并为一个含糊的“今天”。
- `取值逻辑` 中出现分类“高风险”的说法，但 SQL 的 `instruments.category` 实际允许的枚举为 `大盘/杠杆/正股/待确认`。以**数据库约束**为存储事实；对旧报表分类兼容通过映射层处理，并在设置页显示规则，不得强制写不存在的枚举。
- 月度净投入 Sheet 自身定义为“仅保留月初或月末有持仓的记录”，当月建仓且全部清仓的股票可能不在该表；因此**该报表行的净投入汇总不一定等于同月所有交易买卖差额**，要保留这一旧规则，另外提供“完整交易净买入”指标。
- 原始工作簿可能有异常流水/未确认归属/价格缺失；不可自动静默修复。
- 价格表按 `symbol` 保存，而证券表以 `(market, symbol)` 标识；未来多市场有歧义，必须做冲突检查；第一版不得任意修改用户源表。

---

## 1. 产品目标、用户角色与范围

### 1.1 产品定位

开发一款**本地优先、可离线使用、多人多券商股票账务与业绩分析**的 Mac 桌面应用。用户希望把 Excel 当前所有统计内容迁移到软件，扩大交互能力，保留可追溯的原始交易数据与可核对的原 Excel 算法，兼顾未来券商同步、历史绩效和图表分析。

**本项目不是自动下单软件，也不是把股票价格接进来就结束的简单行情工具。** 最重要的是：股票/现金的人员归属、券商实际交易、历史拆股与资产调整、净投入与收益复核。

### 1.2 三个核心使用场景

1. **打开即看**：看到本人/全部人员、全部/指定券商的股票市值、成本、交易净投入、浮动盈亏、已清仓收益、微操收益、今日/月/年收益与回本缺口。
2. **向下穿透**：点击任意一只股票→查看跨券商/跨人员持仓与交易点→还原每次买卖后的持仓数量及净投入成本→溯源原始文件、sheet 和行号。
3. **复核历史**：选择历史日期/月份→重建历史仓位、收益/净投入变化→与 Excel 原有报表按同一范围和口径逐项核对，发现差异给出证据。

### 1.3 角色与权限

- V1：仅本机单用户操作，不实现网络账号系统；可以查看所有 `persons` 数据并按人员筛选。
- `persons` 是**投资权益归属维度**，不是软件登录用户；不要混淆。
- 未来可扩展共享权限，但禁止在本期提供未经用户同意的云同步。

### 1.4 非目标（明确暂不做）

- 不自动代用户买卖股票；不提供交易建议或自动交易 API。
- 不承诺实时行情、券商 API 始终可用；离线时使用数据库已有价格并显示时间戳。
- 不为无法追踪来源的数字自动编造持仓、货币汇率或资金流水。
- 不将金额精度简化为 2 位小数存入账本；2 位仅为常见显示格式。
- 不把原始 Excel 当作持续写入的数据源；上线后 SQLite 为运行时主数据源，Excel 是迁移参考与导出格式。

---

## 2. 来源工作簿结构及完整迁移清单

经实际读取上传的 xlsx 包，**13 张 Sheet，27 个 chart XML**。以下是需要实施的字段、统计和页面映射。表头必须完整保留，至少支持隐藏/显示列、调整列顺序、排序、搜索、筛选、复制、导出。

| Excel Sheet | 源列数 | 推荐页面 / 内部服务 | 级别 | 迁移要求 |
|---|---:|---|---|---|
| 总览 | 复合布局，A:BD | 首页 `/dashboard` | P0 | 18 KPI、渠道汇总、分类汇总、交易日历、季度矩阵、月度趋势、图表 |
| 当前持仓 | 14 | `/holdings` | P0 | 完整字段、过滤、分组、穿透、合计 |
| 股票汇总 | 18 | `/stocks/summary` | P0 | 持有/已清仓股票统一统计生命周期 |
| 每日持仓收益 | 23 | `/performance/daily` | P0 | 逐交易日/券商/股票收益明细，展开当日交易 |
| 每月微操收益 | 14 | `/performance/scalping` | P1（骨架 P0） | 按月配对统计、打标、配对明细及差异 |
| 每月持仓收益 | 24 | `/performance/monthly` | P0 | 自然月收益，期初/期末仓位及本月买卖 |
| 月度净投入明细 | 10 | `/capital/monthly` | P0 | 旧口径净投入、累计净投入及完整口径对比 |
| 清仓收益 | 13 | `/stocks/closed` | P0 | 已清仓股票绩效与清仓时间 |
| 交易明细 | 16 | `/trades` | P0 | 交易记录、后持仓、后净投入、微操标签、来源 |
| 取值逻辑 | 8 | `/help/metrics` | P0 | 显示字段说明、公式、口径与源字段 |
| 总览取值逻辑 | 5 | `/help/dashboard-metrics` | P0 | 首页 18 KPI + 季度 + 渠道 + 分类 + 月趋势规则 |
| 图表数据 | A:AAD 稀疏区域 | 图表查询服务 | P1 | 逻辑数据集；不复制千列空白区域 |
| 图表趋势数据 | A:AL | 时间序列查询服务 | P1 | 动态查询而非依赖单元格引用 |

**强制原则：** Excel 迁移验收不是“功能看起来差不多”，而是**字段兼容、计算口径兼容、过滤口径兼容、数据来源可解释、差异可导出**。

### 2.1 当前持仓（14 列，按原顺序）

| 顺序 | 原 Excel 字段 | 推荐后端字段 / 规则 |
|---:|---|---|
| 1 | 渠道 | `broker_accounts.broker_code` + 展示名 |
| 2 | 分类 | `instruments.category`，必要时兼容映射 |
| 3 | Ticker | `instruments.symbol`（以 market+symbol 唯一） |
| 4 | 持仓数量 | 逐笔人员或券商账本推演（考虑拆并股、划转） |
| 5 | 当前价($) | `market_prices.LATEST`，缺失/过期明确展示 |
| 6 | 持仓市值($) | `持仓数量 × 当前价` |
| 7 | 持仓成本价($) | Excel：`累计净投入 / 当前持仓数量` |
| 8 | 累计买入金额($) | 当前分组历史买入成交金额之和 |
| 9 | 累计卖出金额($) | 当前分组历史卖出成交金额之和 |
| 10 | 累计净投入($) | `累计买入金额 − 累计卖出金额` |
| 11 | 持仓盈亏金额($) | `持仓市值 − 累计净投入`（Excel 保本口径） |
| 12 | 持仓盈亏比例 | `持仓盈亏 / 累计净投入`；净投入≤0 显示“—” |
| 13 | 回本所需涨幅 | 仅当前价低于保本成本价时 `(保本成本价−当前价)/当前价` |
| 14 | 持仓市值占比 | 当前行市值 / 当前筛选范围的总持仓市值 |

注：股票出现 **负数累计净投入** 而仍有正持仓时，保本成本价可以是负数，但不得擅自清零；首页/详情解释其含义。数量为 0 不显示为当前持仓；数量小于 0 是数据质量异常而非负仓策略的默认合法结果。

### 2.2 股票汇总（18 列）

`渠道、分类、Ticker、当前状态、当前持仓数量、当前价/清仓价($)、当前市值($)、持仓成本价($)、累计买入金额($)、累计卖出金额($)、累计净投入($)、持仓盈亏金额($)、持仓盈亏比例、回本所需涨幅、持仓市值占比、累计清仓收益($)、累计微操收益($)、总收益贡献($)`。

- `当前状态`：持仓数量 > 0 为“持有中”；完全清仓为“已清仓”；负持仓进入异常列表，不自动视为“已清仓”。
- 旧 Excel `总收益贡献`：持有中取该持仓净投入法盈亏，已清仓取完全清仓收益；若同一股票经历多轮开/平仓，应遵循原账本事件周期/历史累计处理，不应单纯按最后一段覆盖历史。
- 同一股票跨券商先保留多行，汇总页面另可按 Ticker 跨账户折叠。

### 2.3 每日持仓收益（23 列）

`日期、渠道、分类、Ticker、日末状态、期初数量、当日买入数量、当日卖出数量、日末持仓数量、成本均价($)、日末收盘价($)、日末市值($)、日末市值占比、持仓盈亏比例、回本所需涨幅、期初市值($)、当日买入金额($)、当日卖出金额($)、当日操作笔数、当日收益($)、累计买入金额($)、累计卖出金额($)、累计净投入($)`。

- 历史日依据 `DAILY_CLOSE`；最新交易日可切换 `LATEST` 预估视图，但必须显示“估算/未收盘”和时间。
- 用数据库已有交易日构成时间轴，**不要生成周末、假日的虚假 0 收益记录**。
- 从某证券首次有效交易起，只在 `期初数量 > 0`、`期末数量 > 0` 或当日存在有效交易时产生明细；按原口径生成清仓当日记录。
- 同一个 `business_date` 的买卖顺序按明确的 `book_time, sequence_no, id`，具体排序规则需以原程序确认，不能依赖 SQL 默认排序。

### 2.4 每月微操收益（14 列）

`月份、渠道、分类、Ticker、微操次数、微操数量、微操均买价($)、微操均卖价($)、每股价差($)、买入金额($)、卖出金额($)、微操收益($)、首笔微操时间、最近微操时间`。

- “微操”指短周期买卖配对统计，Excel 取值逻辑明确提到**先买后卖和先卖后买、FIFO 严格配对**。
- 必须支持部分成交、跨日/跨月（归属规则待原脚本核验）、小数股、连续卖出再买入、同时间多笔、先卖后买配对。
- 交易页面显示 `微操N / 持仓N / 减仓 / 异常卖出`，算法应输出配对明细与事件来源；计数、数量、收益能逐笔回溯。
- Excel 一些微操字段说明与实际缓存金额可能存在费用/配对差异；**没有真实原算法源代码前不得宣称已 100% 复刻**。先做可核对独立引擎及差异报告。

### 2.5 每月持仓收益（24 列）

`月份、渠道、分类、Ticker、月末状态、期初数量、本月买入数量、本月卖出数量、月末持仓数量、成本均价($)、月末收盘价($)、月末市值($)、月末市值占比、持仓盈亏比例、回本所需涨幅、期初市值($)、本月买入金额($)、本月卖出金额($)、本月微操收益($)、本月操作笔数、本月收益($)、累计买入金额($)、累计卖出金额($)、累计净投入($)`。

- 本月收益应优先汇总**每日收益**，并与 `月末市值−月初市值−本月买入+本月卖出` 交叉核对，使用一致的估值时点。
- 拆并股不属于现金买卖；归属变动不代表真实外部资金净流入；展示方法见“划转与账本”。

### 2.6 月度净投入明细（10 列）

`月份、渠道、分类、Ticker、月初持仓数量、月末持仓数量、本月买入金额($)、本月卖出金额($)、本月净投入($)、月末累计净投入($)`。

Excel 明确要求**只保留月初或月末数量 > 0** 的记录；当月开仓又全部卖出的股票不进此表。这个“旧报表行过滤”与“完整交易现金流分析”是不同维度，须单独实现两个接口。

### 2.7 清仓收益（13 列）

`渠道、分类、Ticker、历史总买入数量、历史总卖出数量、历史总买入金额($)、历史总卖出金额($)、清仓收益($)、操作笔数、买入均价($)、卖出均价($)、首次买入时间、最终清仓时间`。

- 旧口径：`完全清仓收益=历史总卖出金额−历史总买入金额`（仅该分组达到最终平仓）；不能把部分卖出的收益按同一公式误认为最终清仓收益。
- 均价按拆并股调整后的同一股本单位对比；同时可展示原始成交均价以便溯源。
- 新增清仓后表现页（P2）：平均清仓价/最后清仓价/当前价对照，但价格不足时显示缺失。

### 2.8 交易明细（16 列）

`交易时间、渠道、分类、Ticker、方向、成交数量、成交均价($)、成交金额($)、微操标记、后持仓数量、后累计净投入($)、后累计净投入均价($)、交易类型、划转对象、订单ID、数据来源`。

- 显示原来源记录、交易时间/账务日/原市场日三种时间；保留源顺序。
- 追加展开字段：人员、券商账户、原始记录 ID、执行 ID、手续费、币种、关联状态、是否逆转、记录粒度。
- 根据 `source_record_id` 跳转 `raw_records -> import_batches`，显示导入批次、来源文件、来源 sheet / row、原始 payload，允许复制但不能篡改原始记录。

---

## 3. Excel 首页和18个指标的精确计算契约

**默认 `metric_mode=EXCEL_COMPAT`**，默认范围“全部已启用人员 + 全部有效券商账户”，若历史 Excel 实际只有“个人”记录，以 Excel 的既有统计范围核对，不能拿不同范围的数字比较。

| 编号 | 指标（Excel 原名） | 兼容口径 / 验收规则 |
|---:|---|---|
| 1 | 当前总资产 | `Σ当前持仓市值`；**不含现金、融资** |
| 2 | 当日收益率 | `今日盈亏 / (当前总资产−今日盈亏)`；分母≤0 为“—” |
| 3 | 全时期总盈亏 | `已清仓收益+当前持仓盈亏`，沿用原费用定义 |
| 4 | 已清仓收益 | `Σ完整清仓记录的清仓收益` |
| 5 | 浮动盈亏 | `Σ当前持仓的(市值−累计净投入)`；是 Excel 命名，严格会计中的“未实现损益”可能不同 |
| 6 | 回本缺口 | `Σ每条当前持仓 max(−持仓盈亏,0)+Σ每条已清仓记录 max(−清仓收益,0)`；盈利不得抵扣 |
| 7 | 昨日盈亏 | 最新收益日之前最近有数据的**交易日**收益总和 |
| 8 | 今日盈亏 | “每日持仓收益”最大收益日期的收益总和，而不是硬用系统今日日期 |
| 9 | 上月盈亏 | 最新收益日期所在自然月的前一月每日收益之和 |
| 10 | 本月盈亏 | 最新收益日期所在自然月每日收益之和 |
| 11 | 去年盈亏 | 上一自然年每日收益之和 |
| 12 | 本年盈亏 | 最新收益日期所在自然年每日收益之和 |
| 13 | 本月微操收益 | 指定自然月微操配对收益合计，**不再加到月收益** |
| 14 | 本年微操收益 | 最新日期所在自然年微操收益合计 |
| 15 | 本月收益率 | `本月盈亏 / (当前总资产−本月盈亏)`，非严格资金加权收益率 |
| 16 | 本年收益率 | `本年盈亏 / (当前总资产−本年盈亏)`，非严格投资收益率 |
| 17 | 全时期收益率 | `全时期总盈亏 / [当前持仓净投入合计+清仓记录净投入合计]`；分母≤0 显示“—” |
| 18 | 当前持仓胜率 | `当前持仓盈利记录数 / 当前持仓记录总数`；盈亏=0 不算盈利 |

### 3.1 KPI 展示与交互

- 按 Excel 首页保持 3 行 × 6 项，共 18 项；桌面窄窗口自动改为 3 或 2 列排列。
- 金额显示 `$12,345.67`（UI 可设币种和位数），负数以 `−$123.45` 和亏损色呈现，正数可显示 `+$123.45`；CSV/Excel 导出保留完整原数值和币种。
- 每项卡片显示：指标名称、数值、对应截至日期、鼠标悬浮计算口径；点击弹出“组成明细”和筛选穿透。
- 因旧统计收益率受入金/出金影响，悬浮提示“Excel 兼容口径，非标准投资回报率”，不要误导。
- 出现行情缺失/汇率缺失/待审记录时 KPI 显示 `数据未完整` / `部分估值` 标记，并可查看影响项目；不得以 0 偷偷替代缺失。

### 3.2 首页渠道汇总表

字段按旧 Excel：`渠道、当前资产、当前本金、当前股票数、资产占比、已清仓收益、浮动盈亏、总盈亏、盈利股票数、亏损股票数、回本缺口、当前现金`。核心注意：

- `当前本金` 的**渠道汇总**旧逻辑包含当前持仓与已清仓交易的历史累计净成本，并不等于外部入金。
- 股票数统计记录数（同 ticker 不同券商算不同记录），不是股票股数。
- 现金在旧口径中仅辅助展示，不并入当前总资产。
- 汇总每个渠道，不将券商账户与人员归属混在一起。

### 3.3 首页分类汇总表

字段与渠道汇总表基本一致（去掉现金），以 `大盘 / 杠杆 / 正股` 分类。不同之处：

- **分类表的“当前本金” = 当前分类市值 − 当前分类浮动盈亏**，只含当前持仓；**不同于渠道表当前本金定义**。即使名称相同也必须独立计算，不能公用一个错误的汇总函数。
- 原 Excel 未标记为大盘/杠杆的映射为“正股”；数据库若为 `待确认`，应在独立“未确认分类”列表中提示并由适配器根据已确认的旧分类映射进行兼容，不默认篡改 DB 原值。
- `盈利股票数、亏损股票数` 包括持有中的盈亏记录与已清仓盈亏记录，不是证券唯一代码数。

### 3.4 首页交易日历

- 保留 Excel 的交易日历与周一到周五、每周合计的行为；可切换月份/年份，默认最新收益日的月份。
- 格内展示“日期 + 当日损益（USD）”，绿色盈利/红色亏损/灰色无数据；周末可以留空，不造收益。
- 单击显示当日所有证券收益贡献、买卖操作、价格来源；右侧可查看本周合计。
- 可扩展成 12 个月热力图，但不能取代原月历及周汇总。

### 3.5 季度与各月收益分布矩阵

- 显示 Q1~Q4 下各自然月收益率、季度收益率和年化收益率。
- Excel `总览取值逻辑` 指定：交易日收益率 `r_d=当日收益合计/期初市值合计`（仅期初市值>0），季度收益率 `Π(1+r_d)-1`；完整季度年化 `(1+季度收益率)^4 - 1`；**未完成的季度年化显示“—”**。
- 要明确季度样本日的分母范围、是否排除缺价格的个股；出现缺历史行情应降级提示，不能生成虚假年化。
- 年化结果仅是复利折算，不意味着实际持有超过一年；鼠标提示解释。

### 3.6 月度趋势表

保留：`月份、每月收益、累计收益、各券商月收益、净投入、累计本金、各券商股票总资产、总资产、大盘占比、杠杆占比、其他占比`。实现：按月份升序计算累计再倒序展示；“其他”在 Excel **具体代表正股**，不是未知分类兜底。

---

## 4. 27 张图表的迁移计划（每张有归宿）

经 workbook 结构核对，**原文件有 27 张图表**，必须逐个对应到软件，不允许只说“做趋势图”而遗漏具体类型。每个图表至少支持：日期范围、人员、券商、分类、Ticker 过滤；鼠标提示（日期/数值/来源）；单击联动表格；导出 PNG；显示数据不完整/数据截止日。

| 编号 | 原 Excel 图表标题 | 软件分组 | 推荐展示形式 | 优先级 |
|---:|---|---|---|---|
| 01 | 当前股票市值占比 | 资产结构 | 环形图 + 前 N 列表 | P0 |
| 02 | 各渠道当前资产占比 | 资产结构 | 环形图 | P0 |
| 03 | 当前持仓分类占比 | 资产结构 | 环形图 | P0 |
| 04 | 上月末股票市值占比 | 资产结构 | 上月末环形图 | P1 |
| 05 | 各渠道上月末资产占比 | 资产结构 | 上月末渠道结构 | P1 |
| 06 | 上月末持仓分类占比 | 资产结构 | 上月末分类结构 | P1 |
| 07 | 每月收益走势 | 收益趋势 | 柱状图 | P0 |
| 08 | 月度收益率走势（%） | 收益趋势 | 折线图 | P0 |
| 09 | 每月微操收益 | 收益趋势 | 柱状图 | P1 |
| 10 | 每月净投入走势 | 资金趋势 | 柱状图 | P0 |
| 11 | 持仓结构占比走势（%） | 资产结构 | 大盘/杠杆/正股堆叠面积或折线 | P1 |
| 12 | 各渠道月末股票市值 | 资金趋势 | 多序列堆叠面积 / 柱状 | P1 |
| 13 | 前五大持仓集中度（%） | 资产结构 | 折线图 | P1 |
| 14 | 月末持仓标的数 | 资产结构 | 柱状图 | P1 |
| 15 | 已清仓标的盈亏分布 | 盈亏排行榜 | 正负水平条形图 | P1 |
| 16 | 持仓标的浮动盈亏分布 | 盈亏排行榜 | 正负水平条形图 | P0 |
| 17 | 全部股票按Ticker汇总盈亏 | 盈亏排行榜 | 正负水平条形图 | P1 |
| 18 | 累计本金与总资产变化 | 资金趋势 | 多序列面积/折线图 | P0 |
| 19 | 月度净投入与月度收益 | 资金趋势 | 双柱图 | P1 |
| 20 | 本月每日收益与累计收益 | 收益趋势 | 柱状 + 折线 | P0 |
| 21 | 所选月份每日收益与累计收益 | 收益趋势 | 月份选择器 + 柱线组合 | P1 |
| 22 | 最爽五天 | 盈亏排行榜 | 最高日收益 Top5 | P1 |
| 23 | 最惨五天 | 盈亏排行榜 | 最差日收益 Bottom5 | P1 |
| 24 | 全时期盈亏（USD） | 收益贡献 | 渠道/分类收益对比（依原图维度） | P1 |
| 25 | 本年盈亏（USD） | 收益贡献 | 同上，期间=本年 | P1 |
| 26 | 本月盈亏（USD） | 收益贡献 | 同上，期间=本月 | P1 |
| 27 | 当日盈亏（USD） | 收益贡献 | 同上，期间=最新收益日 | P1 |

图表 24-27 的原 Excel 区域包含渠道及分类多组数据系列；实施时优先按原 Excel chart series 的引用范围还原精确维度，再确定是否分两个切换视图。不要仅根据标题猜测。

---

## 5. 桌面应用整体设计与交互

### 5.1 信息架构与导航

```text
Money Portfolio Desktop
├── 首页
│   ├── 18 KPI
│   ├── 渠道汇总 / 分类汇总
│   ├── 交易日历 / 季度矩阵 / 月度趋势
│   └── 资产 / 收益精选图表
├── 资产管理
│   ├── 当前持仓（14 列）
│   ├── 股票汇总（18 列）
│   ├── 人员资产（新增）
│   └── 券商账户（新增）
├── 收益分析
│   ├── 每日持仓收益（23 列）
│   ├── 每月持仓收益（24 列）
│   ├── 每月微操收益（14 列）
│   ├── 清仓收益（13 列）
│   └── 月度净投入（10 列）
├── 可视化
│   ├── 收益趋势
│   ├── 资产结构
│   ├── 净投入趋势
│   ├── 盈亏排行榜
│   └── 股票价格 / 买卖点
├── 数据中心
│   ├── 交易明细（16 列）
│   ├── 资金事件
│   ├── 内部划转
│   ├── 导入批次 / 原始记录
│   ├── 数据异常 / 对账
│   └── 行情 / 汇率状态
└── 系统
    ├── 指标字典 / 公式说明
    ├── 数据路径 / 备份与只读模式
    ├── Excel 对账报告
    └── 偏好设置 / 日志 / 关于
```

### 5.2 主窗口规格

- Mac 常规窗口，默认 1440×900；最小支持约 1100×700；所有主要页面可滚动/自适应，不因窗口缩小丢失控件。
- 左导航栏固定/可折叠；主内容区上方具有全局工具栏；右侧详情抽屉可选。
- 系统浅色/深色模式联动，支持用户手动切换；使用 macOS 字体优先系统字体，不强行携带受限字库。
- 顶部常驻筛选：**人员、券商账号（支持多选）、股票、交易分类、基准日期、统计口径、币种**。选择后全局状态通知相关页面刷新，不能出现切换人员后图表/表格不同步。
- 顶部还有：数据文件名（避免连错库）、源库只读状态、最后同步时间、待审记录数量、全局搜索。
- 上一页/下一页导航、面包屑、右键复制单元格/行、快捷键 `⌘F` 搜索、`⌘R` 刷新、`⌘,` 设置（Mac 键盘标准）；退出时保存非敏感窗口布局与筛选偏好。
- 使用中文界面；金额/收益率/日期的格式本地化；图例、按钮与状态信息均中文；英文代码如 `Ticker`、`FILL` 可保留行业术语并附解释。

### 5.3 组件与交互标准

1. **通用财务表格**：冻结首列、列排序、全文检索、范围筛选、多列筛选、分页/虚拟滚动、列宽、合计行、点击行显示详情、复制所选单元格/行、导出当页/当前筛选/全部记录。
2. **图表联动**：悬停显示原始值+格式值；选中股票图例筛选表格；点击日历日/走势图点按对应 `date` 过滤交易；点击排行榜进入股票详情。
3. **状态语义**：盈利=绿色、亏损=红色（允许在设置中改配色）；同时用 `+`/`−` 和标签表达，不单靠颜色区分。`缺行情`、`待审核`、`不完整` 和 `同步失败` 用清晰文字提示。
4. **空态**：“该筛选组合暂无数据 / 历史数据不完整 / 未发现有效行情 / 原始库未连接”；给出可执行下一步，不能填假示例数字。
5. **加载性能**：耗时查询在工作线程/异步任务执行；Qt GUI 主线程只更新 UI；有取消/超时/错误提示，不能导致界面无响应。
6. **金额行为**：鼠标悬停显示准确币种、未四舍五入的金额、估值日期、价格类型。负净成本、0 成本、权益划转要有说明。

### 5.4 首页布局建议

```text
┌─────────────────────── Money Portfolio ──────────────────────────┐
│ 数据库 money_2.db [只读] | 人员 [全部] | 券商 [全部] | 日 期 [最新] │
├───────────────┬──────────────────────────────────────────────────┤
│ 首页          │ 18 KPI：3行×6列（窗口小自适应）                 │
│ 资产管理      ├──────────────────────────────────────────────────┤
│ 交易流水      │ 渠道汇总表                 分类汇总表            │
│ 收益分析      ├──────────────────────────────────────────────────┤
│ 图表分析      │ 收益交易日历               季度收益矩阵          │
│ 数据中心      ├──────────────────────────────────────────────────┤
│ 指标说明      │ 每月收益 / 净投入 / 资产趋势                    │
│ 设置          ├──────────────────────────────────────────────────┤
│               │ 持仓 TOP5       盈亏榜       异常提醒           │
└───────────────┴──────────────────────────────────────────────────┘
```

### 5.5 股票详情页面

- 页头：Ticker、证券名、市场、分类、币种、价格与报价时间、今日涨跌（有数据才显示）。
- 筛选：人员、券商、起止日期、显示全部/买入/卖出/人员划转、行情类型。
- 价格图：历史收盘价曲线（以后可扩展 K 线）；买入 B 绿点、卖出 S 橙点、划转独立形状；悬停展示交易时间、数量、价格、金额、账户、人员、原始来源。
- 汇总：当前数量、市值、累计买入卖出、净投入、保本成本价、当前盈亏、该股票已清仓收益、微操配对收益；提示部分收益可能为不同维度，禁止直接相加。
- 下方标签：交易流水、逐笔成本轨迹、日收益、月收益、人员分布、券商分布、原始记录。

### 5.6 人员和券商三维钻取

必须支持 `全部 → 人员 → 券商 → Ticker → 交易`，也支持 `全部 → 券商 → 人员 → Ticker → 交易` 两种钻取路径。

- 同一股票同一个券商可能由多个人持有权益；**同券商真实股数与人员分配股数进行对账**。
- 账户现金可能只有账户归属（`cash_events.person_id IS NULL`），在人员视角不得自行按比例分配。
- `persons.enabled=0` 不代表历史权益自动清零：默认在筛选中隐藏停用人员，但“全部历史”模式仍可查看，且不得因停用从对账总和剔除历史记录。

---

## 6. SQLite 数据字典与数据源职责

### 6.1 十四张表，按功能分类

| 表名 | 主要职责 | 主要字段/使用约束 |
|---|---|---|
| `import_batches` | 源数据批次、导入来源/状态/版本 | `batch_key, source_kind, source_scope, status, record_count, imported_at_utc` |
| `raw_records` | 不可丢失的原始输入/来源行/解析状态 | `payload_json, source_locator, source_sheet, source_row, parse_status` |
| `persons` | 人员身份/是否启用 | `id, name, enabled` |
| `broker_accounts` | 券商账户，含账务时区和切日规则 | `broker_code, account_key, base_currency, book_timezone, day_cutoff` |
| `instruments` | 证券标准名、币种、类型与分类 | `market, symbol, asset_type, category` |
| `instrument_aliases` | 不同提供商的代码别名 | `(provider, market, source_symbol) -> instrument_id` |
| `broker_trades` | 真实券商成交/订单（物理账户） | `account_id, instrument_id, trade_key, record_granularity, side, quantity_e8, calculation_amount_e8, record_state, accounting_role` |
| `person_operations` | 人员权益操作/交易映射/划转 | `person_id, operation_kind, side, broker_trade_id, counterparty_person_id, transfer_group_key, sequence_no, link_status` |
| `cash_events` | 出入金/分红/税费/利息/费用 | `event_type, amount_e8, accounting_role, person_id(nullable)` |
| `corporate_actions` | 拆股与反向拆股 | `effective_date, action_type, ratio_numerator, ratio_denominator, broker_scope` |
| `historical_baselines` | 历史开账基准/参考/历史归档 | `baseline_date, baseline_role, baseline_kind, scope_type` 及各 `_e8` |
| `asset_adjustments` | 估值/现金/对账调整 | `adjustment_mode, target_metric, amount_e8, effective_from/to, status` |
| `market_prices` | 最新、日收盘、前收盘、月收盘 | `symbol, price_type, trade_date, period_key, quoted_at_utc, price_status` |
| `fx_rates` | 汇率，支持历史换算 | `rate_date, base_currency, quote_currency, rate_e8, provider` |

### 6.2 原有 7 个检查视图

`检查_人员操作`、`检查_券商成交`、`检查_证券行情`、`检查_资金流水`、`检查_历史基准`、`检查_资产调整`、`检查_原始记录`。

- 它们可用于**审计与追溯页面**，但并不能替代完整的账务计算层。
- 程序启动时执行 `PRAGMA table_info` / `sqlite_master`，检查表/视图是否存在、必要列是否存在；不符合时进入“兼容性检查失败”的只读提示页而非崩溃。
- 所有查询使用参数绑定，表名/字段名白名单映射，绝不拼接用户输入 SQL 字符串。

### 6.3 两本账必须独立

**真实账户账本（Broker Ledger）**

- 从 `broker_trades` 中筛选 `record_state='ACTIVE' AND accounting_role='ACCOUNT_LEDGER'`。
- `record_granularity` 有 `FILL / ORDER_TOTAL / SOURCE_RECORD`，同一业务真实成交可能因多个粒度混存而重复；**仅凭这些字段无法自动判定全部重复关系**。必须实现去重/冲突策略及来源检查，优先处理有稳定执行 ID 的原始实际成交。不得仅按日期/股数/价格去重。
- 交易金额以明确 `calculation_amount_e8` 和 `amount_basis` 维持 Excel 口径；现金口径可采用 `net_amount_e8`，但绝不把 `gross_amount_e8` 与净额重复扣费。

**人员权益账本（Person Ledger）**

- 以 `person_operations` 推演人员对某一 `(account_id, instrument_id)` 的数量和权益归属。
- `MARKET_TRADE` 对应券商成交时可关联 `broker_trade_id`；对 `LEGACY_UNLINKED` 或 `REVIEW_REQUIRED` 分别打状态。
- `OWNER_TRANSFER` 是人员之间调拨数量/成本，不产生券商真实成交；`EXTERNAL_TRANSFER`、`BOOK_SETTLEMENT` 为不同业务类型，必须依据证据处理，不能一概算买卖。
- 人员操作的 `amount_e8` 未必等同券商真实现金出入；不要将二者合并累加。
- 同一账户中 `SUM(person_quantity)` 理论上应对账 `broker_quantity`，有偏差时列差异，不能默默补到某个人头上。

### 6.4 活动状态、时间、汇率和标的

- `broker_trades.record_state='REVERSED'` 不参与有效账本；`cash_events` 和 `corporate_actions` 同理；历史修正是否重放需保留审计链。
- 排序：事件日期与时间使用 `business_date, book_time, sequence_no, stable id` 的已定义口径；存储的 `executed_at_utc` 与归属账务日不同不能混用。`broker_accounts.book_timezone` 和 `day_cutoff` 定义既有账务日规则，**已有 `business_date` 优先为权威**。
- `market_prices.price_type='LATEST'` 与 `DAILY_CLOSE` 不是同一时点；有缓存的 `price_status='LEGACY_UNKNOWN'` 时显示来源未知标志。历史重建不允许用当前价替代历史日价。
- `instruments(market,symbol)` 是唯一证券身份；`instrument_aliases` 专用于券商/行情别名映射，不能强制通过字符串前缀判断证券市场。
- `fx_rates` 是 1 单位 `base_currency` 换 `quote_currency` 的乘数，日期精确；跨币种汇总需要明示换算日、汇率来源/缺失策略，禁止无声默认 USD=其他币种。

### 6.5 数据质量状态

需完整支持：`PENDING / SUCCESS / FAILED` 导入批次；`PENDING / ACCEPTED / REVIEW_REQUIRED / IGNORED` 原始解析；`LINKED / LEGACY_UNLINKED / REVIEW_REQUIRED` 人员关联；`ACTIVE / REVERSED` 交易/现金/公司行动；`BALANCE_ANCHOR / DELTA` 资产调整；`OPENING / REFERENCE / ARCHIVED_PERIOD` 历史基准。具体值以 `sqlite_schema.sql` 为准，不允许另造不可写入的 enum。

---

## 7. 财务计算引擎：必须独立于 UI 的业务规则

### 7.1 统一基本类型

```python
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum

E8 = Decimal(100_000_000)

def from_e8(raw: int | None) -> Decimal | None:
    if raw is None:
        return None
    return Decimal(raw) / E8

def to_e8(value: Decimal) -> int:
    # 仅在写入派生缓存时调用；原库 V1 不写
    return int((value * E8).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

class MetricMode(str, Enum):
    EXCEL_COMPAT = 'EXCEL_COMPAT'
    ACCOUNTING = 'ACCOUNTING'  # 标准收益，数据就绪后开启

@dataclass(frozen=True)
class QueryScope:
    as_of: date
    person_ids: tuple[int, ...] | None
    account_ids: tuple[int, ...] | None
    instrument_ids: tuple[int, ...] | None
    metric_mode: MetricMode = MetricMode.EXCEL_COMPAT
    reporting_currency: str = 'USD'
```

**严禁**：`float(price_e8) / 1e8` 再累加；不要以 UI 显示值反算现金/持仓；不要把 `None` 价格转为 `Decimal('0')`。

### 7.2 数量/成本/现金轨迹

按每个“账户 + 证券 +（如有）人员”，逐条对账本事件回放：

```text
initial_quantity = 由有效 OPENING 基准确定的期初数量（如有，否则0）
initial_net_investment = 有证据的开账净投入（如有，否则0）
BUY:    quantity += qty;  net_investment += calculation_amount
SELL:   quantity -= qty;  net_investment -= calculation_amount
SPLIT:  quantity *= numerator / denominator; cash unchanged
OWNER_TRANSFER_OUT: owner_quantity -= qty;  owner_cost -= carried_cost
OWNER_TRANSFER_IN:  owner_quantity += qty;  owner_cost += carried_cost
```

此为**逻辑示例**而非直接复制粘贴的生产代码：

- 必须区分 `ACCOUNT_LEDGER` 实际成交与 `REFERENCE`，避免重复计数。
- 转移权益是否继承原始净投入还是采用明确转让金额，需根据 `person_operations.amount_basis / amount_e8`、原脚本及划转配对记录核验；缺依据进入审核队列。
- `BOOK_SETTLEMENT` 是否视作资金流而非股票成交，必须按来源业务类型处理。
- 当发生逆转、同日划转与拆股，应制定总顺序，确保重放确定性；测试覆盖。
- 开账基准应与之后流水去重：使用截止日 `T0` 的 `OPENING` 作为初始资产，只应用 `T>T0` 的增量，不可再累计基准之前全部交易。

### 7.3 Excel 兼容的净投入成本

对期末数量 `Q>0`：

```text
cum_buy_amount  = Σ买入 calculation_amount
cum_sell_amount = Σ卖出 calculation_amount
net_investment  = cum_buy_amount − cum_sell_amount
breakeven_cost  = net_investment / Q
market_value    = Q × selected_price
excel_position_pnl = market_value − net_investment
excel_position_return = excel_position_pnl / net_investment, iff net_investment>0
recover_upside  = (breakeven_cost−selected_price)/selected_price
                  only if breakeven_cost>selected_price>0
```

注意：

- 这是一种**资金净流入视角的保本计算**，已卖出部分的盈利会反映在剩余持仓净成本中。不能将其直接称为会计标准“未实现盈亏”。
- 当仓位为 0 时，`net_investment` 的负值可对应完全清仓收益；历史清仓记录应再根据历史分段或基准判断，避免重复纳入同一利润。
- 标准 FIFO 已实现/未实现成本应另开引擎，切换口径时清楚显示二者差别。

### 7.4 日收益与月收益

旧 Excel 说明表确认以下算法：

```text
opening_quantity = 前一有效价格日结转的期末数量
opening_value    = opening_quantity × previous_day_close
ending_value     = ending_quantity × close_or_latest_price
buy_amount_day   = Σ当天买入成交金额
sell_amount_day  = Σ当天卖出成交金额
pnl_day          = ending_value − opening_value − buy_amount_day + sell_amount_day
pnl_month        = Σ当月 pnl_day
```

实例（合成测试用，不代表用户真实数据）：

- 昨日 10 股 × $100 = $1,000；今日买入 2 股 × $105 = $210；今日收盘 $110；日末 12 股市值 $1,320。
- `当日收益 = 1320−1000−210+0 = $110`。
- 如果今日卖出 3 股、卖得 $345，收盘剩 9 股 ×$110=$990，则 `当日收益=990−1000−210+345=$125`（此示例中同时买入 2 股且卖出 3 股，期末 9 股，股数保持一致）。

**日收益算法在入金、分红、佣金、账户内部划转、拆并股、停牌、缺价的情形下可能需要额外调整或提示。** 不得盲目套公式产生貌似精确的结果。每个结果附 `completeness/status`。

### 7.5 价格日期优先级（严格区分）

```text
“当前持仓”选价：
  有效 LATEST，时间戳满足用户配置的有效期 -> LATEST
  否则可回落到最新 DAILY_CLOSE -> 显示“非实时/上次收盘”
  否则 -> 缺行情，不计算含该证券的完整市值总额

“历史某日”选价：
  目标日期 DAILY_CLOSE；若为月末可明确使用 MONTH_CLOSE 对账
  缺失 -> 标记缺口，不使用未来收盘/当前价填充

“今日收益”选价：
  按来源的收益最新日与价格时点决定；在盘前/盘后必须标记估算
```

历史价格复权：`corporate_actions` 给出拆并股事件时，应确保交易数量、基准持仓、价格数据采用**同一股本口径**；如果历史价格源已经是复权价格，不能二次复权。需要 `price_adjustment_policy` 配置与对账测试。

### 7.6 拆并股规则

- 拆股 2:1：100 股 → 200 股；总账面投资额不变，单位保本成本从 $50 → $25；不产生买卖现金流/收益。
- 反向拆股 1:10：100 股 → 10 股，投资额不变；如有零碎股现金结算必须通过可溯源现金/交易事件处理。
- 根据 `broker_scope` 限制适用券商，不可同一股票在所有券商无条件套用。
- 基准快照内的历史量可能已经调整，计算时必须考虑基准生效时间；建立拆并股黄金样本。

### 7.7 人员划转规则

- `OWNER_TRANSFER` 成对事件应按 `transfer_group_key` 成组验证：股票、账户、数量、方向和人头一致。
- 转出人与转入人的合计股数必须守恒，账户真实总股数不变；内部转移不进真实券商交易量，也不作为外部入金。
- 转移成本的继承/价值口径必须明确；若是历史 Excel 独特算法，以原规则和基准核对后实施；未知时显示“待人工核定成本”，不得硬塞均价。
- 不同人员之间的收益归属以转移生效时点为界，防止一人转出后仍累计后续价格盈亏。

### 7.8 已清仓统计、标准收益和微操

- 完全清仓时 Excel 兼容总收益可计算 `cum_sell−cum_buy`；若同证券发生重新建仓，依原始归属周期/历史累计定义决定“清仓收益”累计与重置策略。
- **标准口径**的 realized/unrealized P&L 必须通过成本批次 FIFO（可后续支持移动加权）分别计算；实现单元测试后才能展示。
- 微操收益是交易盈亏的**分析切片**，不得再与 `日收益/月收益/全时期盈亏` 直接相加。
- 微操匹配算法若无法从材料唯一确定：先开发可配置、可测试的 FIFO 配对引擎 + 配对审计表；把 Excel 黄金样本的差异作为 blocker 报告，不能编造等价性。

### 7.9 资金事件与汇率

`cash_events` 分别支持 `DEPOSIT, WITHDRAWAL, DIVIDEND, INTEREST, FEE, TAX, TRANSFER, OTHER`，其 `accounting_role` 为 `CAPITAL / INCOME / EXPENSE / INFORMATIONAL / REVIEW_REQUIRED`：

- 真实现金余额计算应基于可靠期初余额 + 交易净现金流 + 资金事件 + 适用调整，且避免同一费用在成交净额与现金事件中重复。
- 来源只有现金事件但没有可信期初余额时，显示“现金流净变动（非现金余额）”，不称为“当前现金”。
- 组合完整资产（股票+现金−负债）与旧 Excel 股票总市值是两种明确模式。
- 支持 USD 与 CNY 等报告币种；报告原始币种必须可溯源；同一金额展示换算时附 FX 日期/提供商与缺失提示。

### 7.10 基准与资产调整

- `historical_baselines` 的 `OPENING` 为开账起点；`REFERENCE` 只作参考/对账，不能直接作为新增交易；`ARCHIVED_PERIOD` 作归档期间统计并避免与重算范围重复。
- `asset_adjustments` 的 `BALANCE_ANCHOR` 是指定指标的余额锚点，`DELTA` 为增量；须按 `target_metric` 的语义映射，未知 target 不强行套用。
- 期初/期末资产不可跨不同 `scope_type`（PERSON_ACCOUNT / PERSON / ACCOUNT / TOTAL）简单相加，因为存在层级覆盖。
- 任何校正进入 `adjustment_audit` 派生记录，展示理由、起止日期、源记录和校正前后差额。

---

## 8. 数据对账和异常中心（P0）

### 8.1 内置异常规则

| ID | 严重度 | 检查 |
|---|---|---|
| DQ001 | ERROR | 任何有效事件回放后持仓为负且未有明确支持的做空机制 |
| DQ002 | ERROR | 人员归属持仓数量合计与券商真实仓位不一致 |
| DQ003 | ERROR | 同一 `broker_trade_id` 关联了超出实际数量的人员操作 |
| DQ004 | ERROR | `OWNER_TRANSFER` 转出入缺配对/数量不守恒/标的账户不一致 |
| DQ005 | ERROR | 同一业务成交在 FILL/ORDER_TOTAL/SOURCE_RECORD 混用导致疑似重复 |
| DQ006 | WARN | 人员操作 `link_status='REVIEW_REQUIRED'` / `LEGACY_UNLINKED` |
| DQ007 | WARN | 原始记录 `parse_status='REVIEW_REQUIRED'` 或 `PENDING` |
| DQ008 | WARN | 资金事件 `accounting_role='REVIEW_REQUIRED'` |
| DQ009 | WARN | 估值/资产调整 `status='REVIEW_REQUIRED'` |
| DQ010 | WARN | 股票缺少所需日期行情 / 行情长时间未更新 / 价格口径 UNKNOWN |
| DQ011 | WARN | 没有足够 FX 无法跨币种合并 |
| DQ012 | WARN | 最新持仓日期和收益最大日期不一致 |
| DQ013 | WARN | 拆并股事件与数量重放不一致 / 已复权疑似二次复权 |
| DQ014 | WARN | 历史开账基准与交易区间重叠导致重复 |
| DQ015 | WARN | `instrument_aliases` 代码映射冲突 / 行情 symbol 多市场歧义 |
| DQ016 | WARN | 无现金期初基准却尝试显示现金余额 |
| DQ017 | WARN | 买入/卖出金额与记录中的总额、手续费、净额不一致 |
| DQ018 | INFO | Excel 兼容指标与标准口径指标差异（解释性，而非自动错误） |

### 8.2 “差异核对表”字段

`规则ID、日期、人员、账户、证券、Excel值、数据库计算值、差异、容差、状态、口径、完整性、原始记录、备注、建议动作`。

- 可筛选 P0 阻断项；一键打开该交易的 `raw_records.payload_json` 和 Excel sheet/单元格（若已收集）。
- 允许导出 CSV / Excel / JSON，便于 Codex 排错；用户可以复制“差异摘要”发给 Codex。
- V1 **不自动修正**原库，只提供诊断；修复策略留待 V2 的事务式编辑模块。

### 8.3 Excel 黄金快照对账

对上传工作簿中可见数值进行归档（只作为核对输入，不作为长期权威数据）；Codex 必须开发：

```text
scripts/extract_excel_reference.py --input references/个人投资总览.xlsx --output tests/fixtures/excel_snapshot.json
python -m portfolio.reconcile --db <真实db> --excel references/个人投资总览.xlsx \
    --as-of <匹配Excel统计日> --report reports/excel_diff.csv
```

此为**目标命令接口**，实际要在代码中落地，并支持中文文件路径。黄金对账范围至少：

1. 18 首页 KPI（注意统计日/收益日不一致问题）；
2. 当前持仓表的 14 列、每个 `(broker, ticker)`；
3. 股票汇总表的 18 列；
4. 指定样本的每日收益、月收益、微操收益、清仓收益；
5. 渠道、分类两张汇总表；
6. 交易表的后持仓/后净投入/微操标签；
7. 27 个图表的数据系列长度/基本合计值。

每项差异都应告诉用户**是日期、数据范围、公式、缺行情、缺现金、拆股、粒度重复还是尚未确认口径**，不要只报“不一致”。

---

## 9. 技术选型与项目目录约束

### 9.1 推荐技术栈

| 层 | 推荐 | 说明 |
|---|---|---|
| 运行时 | Python 3.11+（按项目依赖兼容性锁定实际版本） | Mac arm64 优先；不依赖 Docker 运行桌面 GUI |
| GUI | PySide6 (Qt Widgets) | 原生 macOS 桌面窗口、表格、筛选、菜单与快捷键 |
| 图表 | pyqtgraph 优先，适当使用 QtCharts / Matplotlib（按可维护性评估） | 大量日线和交易点数据渲染；不得让绘图代码承担业务计算 |
| 数据库 | Python 标准库 `sqlite3` + Repository 模式 | 只读 URI 连接；必要时连接池或线程独立连接 |
| 财务数值 | `decimal.Decimal` + `_e8` 整数 | 保留高精度，显式处理空值 |
| 测试 | pytest + pytest-qt（GUI 测试） | 参数化单元测试、内存 SQLite fixture、端到端测试 |
| 代码规范 | Ruff、类型注解、可选 mypy | 以代码质量检查可执行为准 |
| 导入/导出 | CSV 标准库；Excel 读取可选 `openpyxl`（仅针对 Codex 实际项目，不作为本会话中的电子表格制作工具） | 对外导出 Excel 时不覆盖用户来源工作簿 |
| 配置 | `pyproject.toml`、配置文件 + macOS 应用数据目录 | 不在源代码写账户密码、令牌、绝对用户路径 |
| 打包 | `pyside6-deploy`，实际打包失败再评估备选 | 打包 macOS `.app`，发布时校验资源路径及依赖 |

官方参考：

- Qt for Python: https://doc.qt.io/qtforpython-6/
- QAbstractTableModel: https://doc.qt.io/qtforpython-6/PySide6/QtCore/QAbstractTableModel.html
- QSortFilterProxyModel: https://doc.qt.io/qtforpython-6/PySide6/QtCore/QSortFilterProxyModel.html
- pyside6-deploy: https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html
- SQLite Python module: https://docs.python.org/3/library/sqlite3.html
- SQLite URI filename: https://www.sqlite.org/uri.html
- pytest: https://docs.pytest.org/en/stable/

### 9.2 建议代码目录

```text
money-portfolio-desktop/
├── AGENTS.md                             # 代码代理硬规则
├── README.md                             # Mac 安装、运行、连接库
├── pyproject.toml                        # 主项目依赖、测试与打包配置
├── .gitignore
├── docs/
│   ├── PRODUCT_REQUIREMENTS.md           # 本需求文档
│   ├── METRIC_DICTIONARY.md              # 所有指标的数学与展示定义
│   ├── DB_MAPPING.md                     # 数据字典/来源/聚合口径
│   ├── RECONCILIATION.md                 # Excel 基准核对方法
│   ├── DECISIONS.md                      # 已确认与待确认业务决策
│   ├── RELEASE_NOTES.md
│   └── CHANGELOG.md
├── assets/
│   ├── icons/
│   ├── styles/
│   └── translations/
├── src/portfolio/
│   ├── __init__.py
│   ├── __main__.py                         # python -m portfolio
│   ├── app.py                              # QApplication 初始化
│   ├── config.py                           # 配置读取与校验
│   ├── logging_config.py
│   ├── domain/
│   │   ├── enums.py
│   │   ├── entities.py                      # Account/Person/Instrument/Trade
│   │   ├── value_objects.py                # Amount, Price, Quantity, Currency
│   │   ├── errors.py
│   │   └── scopes.py                        # QueryScope, MetricMode
│   ├── db/
│   │   ├── connection.py                    # SQLite 只读连接
│   │   ├── schema_validator.py
│   │   ├── repositories/
│   │   │   ├── accounts.py
│   │   │   ├── persons.py
│   │   │   ├── instruments.py
│   │   │   ├── broker_trades.py
│   │   │   ├── person_operations.py
│   │   │   ├── prices.py
│   │   │   ├── baselines.py
│   │   │   ├── cash.py
│   │   │   └── provenance.py
│   │   └── queries/
│   ├── engine/
│   │   ├── events.py                       # 归一化事件流
│   │   ├── replay.py                       # 时间顺序回放
│   │   ├── position_engine.py              # 持仓与净投入
│   │   ├── stock_actions.py                # 拆并股
│   │   ├── owner_transfer.py               # 人员划转
│   │   ├── excel_compat_metrics.py         # Excel 兼容指标
│   │   ├── daily_returns.py
│   │   ├── monthly_returns.py
│   │   ├── scalping_fifo.py                # 微操匹配
│   │   ├── accounting_metrics.py           # 严格口径，渐进开放
│   │   ├── fx.py
│   │   └── completeness.py                 # 数据完整度
│   ├── services/
│   │   ├── dashboard_service.py
│   │   ├── holdings_service.py
│   │   ├── stocks_service.py
│   │   ├── performance_service.py
│   │   ├── trades_service.py
│   │   ├── charts_service.py
│   │   ├── audit_service.py
│   │   ├── reconciliation_service.py
│   │   └── export_service.py
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── navigation.py
│   │   ├── state.py                          # 全局过滤条件
│   │   ├── theme.py
│   │   ├── widgets/
│   │   │   ├── metric_card.py
│   │   │   ├── finance_table.py
│   │   │   ├── price_chart.py
│   │   │   ├── date_filter.py
│   │   │   ├── detail_drawer.py
│   │   │   └── status_banner.py
│   │   ├── models/                          # QAbstractTableModel
│   │   └── pages/
│   │       ├── dashboard.py
│   │       ├── holdings.py
│   │       ├── stock_summary.py
│   │       ├── stock_detail.py
│   │       ├── persons.py
│   │       ├── brokers.py
│   │       ├── daily_performance.py
│   │       ├── monthly_performance.py
│   │       ├── scalping.py
│   │       ├── net_investment.py
│   │       ├── closed_stocks.py
│   │       ├── trades.py
│   │       ├── data_quality.py
│   │       ├── reconciliation.py
│   │       ├── metric_help.py
│   │       └── settings.py
│   └── cli/
│       ├── health.py
│       └── reconcile.py
├── tests/
│   ├── fixtures/
│   ├── unit/
│   ├── integration/
│   ├── gui/
│   └── golden/
├── scripts/
│   ├── inspect_database.py
│   ├── extract_excel_reference.py
│   ├── build_demo_db.py                   # 全部合成数据，供演示和 CI
│   └── build_macos_app.sh
└── reports/                                # Git ignore，含用户敏感数据
```

结构不要求逐个空文件提前创建；必须**随着实现实际落地**。若现有项目已有可复用组织结构，应以最小破坏迁移，但满足职责分层。

### 9.3 层间调用原则

```text
UI (PySide6)
  -> Services / read models
     -> Metrics/Replay Engine (pure Python)
        -> Repositories (sqlite3, read-only)
           -> SQLite database
```

- `ui/` 中不写大段 SQL、不可直接改变账务状态。
- `engine/` 不能 import PySide6；可用内存合成事件直接测试。
- `repositories/` 只查库、解释数据，不在这里画图或混算各种口径。
- `services/` 负责过滤、调用、对账、状态和缓存。
- 金额输出统一 `MetricValue(value, currency, valuation_date, status, warnings, provenance)`，不要让 UI 猜测数值是否完整。

### 9.4 只读 SQLite 连接示例

```python
import sqlite3
from pathlib import Path

def open_readonly_db(db_path: str) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve(strict=True)
    # 标准 file:// URI；处理带中文、空格路径，不插值用户 SQL
    uri = path.as_uri() + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only = ON')
    conn.execute('PRAGMA foreign_keys = ON')
    return conn
```

- 只有指定的用户数据库文件路径可打开；源库不存在时弹出选择，不自动创建空数据库。
- 连接探测时检查 `PRAGMA integrity_check`（开销较大，应仅在用户触发或首次扫描时运行），`PRAGMA foreign_key_check` 仅作诊断。
- macOS `.app` 包内目录视为只读；数据库、设置、导出、缓存放用户可写目录。不修改 Python 安全机制来绕过 macOS 权限提示。
- SQLite 连接一般**每个工作线程独立创建、独立关闭**，不要跨线程复用默认 `check_same_thread=True` 的对象。

### 9.5 交易查询示例：原始行为证据

```sql
SELECT
    t.id, t.trade_key, a.broker_code, a.account_key,
    i.market, i.symbol, t.business_date, t.book_time,
    t.side, t.quantity_e8, t.price_e8, t.calculation_amount_e8,
    t.amount_basis, t.record_granularity,
    t.accounting_role, t.record_state,
    t.source_record_id
FROM broker_trades AS t
JOIN broker_accounts AS a ON a.id = t.account_id
JOIN instruments AS i ON i.id = t.instrument_id
WHERE t.record_state = 'ACTIVE'
  AND t.accounting_role = 'ACCOUNT_LEDGER'
  AND t.business_date <= ?
ORDER BY t.business_date, t.book_time, t.id;
```

此 SQL **只能读取候选有效账务记录**，不是已完成的交易去重/人员归属算法；`ORDER_TOTAL` 与 `FILL` 混合时仍须做业务粒度检验。

---

## 10. 查询服务和页面接口契约

在没有服务器的单机程序里，“API”指服务层 Python 方法/返回模型，**不需要额外启动 HTTP 后端**。应提供类型注解、错误模型和测试。

### 10.1 核心查询接口

```python
class DashboardService:
    def get_overview(self, scope: QueryScope) -> "DashboardDTO": ...
    def get_broker_summary(self, scope: QueryScope) -> list["BrokerSummaryDTO"]: ...
    def get_category_summary(self, scope: QueryScope) -> list["CategorySummaryDTO"]: ...
    def get_profit_calendar(self, scope: QueryScope, year: int, month: int) -> "CalendarDTO": ...
    def get_quarter_matrix(self, scope: QueryScope, year: int) -> "QuarterMatrixDTO": ...

class HoldingsService:
    def get_positions(self, scope: QueryScope, sort: str, page: int, size: int) -> "PagedHoldings": ...
    def get_holdings_by_person_account(self, scope: QueryScope) -> list["PersonHoldingDTO"]: ...
    def get_stock_lifecycle(self, instrument_id: int, scope: QueryScope) -> "StockLifecycleDTO": ...

class PerformanceService:
    def get_daily_profit(self, scope: QueryScope) -> list["DailyProfitDTO"]: ...
    def get_monthly_profit(self, scope: QueryScope) -> list["MonthlyProfitDTO"]: ...
    def get_scalping_summary(self, scope: QueryScope) -> list["ScalpingDTO"]: ...
    def get_closed_stocks(self, scope: QueryScope) -> list["ClosedStockDTO"]: ...
    def get_net_investment(self, scope: QueryScope, excel_filter: bool) -> list["InvestmentDTO"]: ...

class TradesService:
    def get_trades(self, scope: QueryScope, filters: "TradeFilters") -> "PagedTrades": ...
    def get_trade_provenance(self, source_record_id: int) -> "ProvenanceDTO": ...

class AuditService:
    def get_data_quality(self, scope: QueryScope) -> list["AuditIssueDTO"]: ...
    def reconcile_broker_and_person_holdings(self, scope: QueryScope) -> list["DiscrepancyDTO"]: ...
```

接口 DTO 需包含 `completeness_status`, `as_of`, `valuation_source`, `currency`，涉及来源时包含 `source_record_ids`，金额统一 Decimal（序列化 JSON 时为十进制字符串）。

### 10.2 数据缓存与一致性

- 缓存键至少含 `source_db_identity + source_db_mtime + scope + metric_mode + calculation_version + price_as_of`；源库替换或内容变更后必须失效。
- 不得缓存带旧人员筛选的结果在新人员视图中使用。
- 大量历史数据可以按“账户/证券/日”增量缓存，但需能清缓存重算，且不修改源数据库。
- 同一画面应使用一致的**查询快照/时间基准**；不要左侧 KPI 取新价、右侧表格取旧价而未说明。

### 10.3 导出要求

- `导出 CSV`：UTF-8 BOM 可配置以兼容 Excel，日期 ISO 字符串，金融数值保留精度，列名中文。
- `导出 XLSX`：按显示列顺序和筛选范围，包含“参数说明/口径/数据截止日/数据源”附页；Excel 原表不覆盖。
- `导出 PNG`：支持每张图表独立导出，日期、过滤条件、数据截止日加在图注。
- `导出对账结果`：CSV + JSON，至少包含差异原因分类和来源 ID，便于后续调试。

---

## 11. 自动化测试和验收标准

### 11.1 测试分类

1. **纯函数单元测试**：Decimal 精度、净投入、盈亏、回本缺口、日/月收益、划转、拆股、微操配对。
2. **数据库集成测试**：基于 `sqlite_schema.sql` 在测试临时文件建库，插入合成数据；覆盖完整主外键、记录粒度/角色、只读连接和数据追溯。
3. **黄金 Excel 测试**：从源工作簿提取字段样本，对齐完全相同的日期、账户和口径；遇到真实库缺失时标记 `SKIPPED: real database not supplied`，不可假装通过。
4. **界面测试**：窗口启动、导航、筛选联动、表格列/排序、无数据页面、日期切换、图表交互、导出提示。
5. **打包冒烟测试**：在目标 Mac 打开 `.app`，完成选库、查看首页、打开详情、导出 CSV、退出重启。

### 11.2 至少 30 个验收测试用例

| ID | 场景 | 预期结果 |
|---:|---|---|
| T001 | 启动且未配置数据库 | 进入选择文件引导，不生成空库 |
| T002 | 选择结构正确的 SQLite | 成功打开并显示明确的只读状态 |
| T003 | 选择缺关键表的数据库 | 输出结构差异，不白屏/崩溃 |
| T004 | `123456789` e8 | 精确解析为 Decimal `1.23456789` |
| T005 | 大额/小数股累计 | 无浮点累积误差，导出保留精度 |
| T006 | 买入 10 股 $100，卖 4 股 $120 | 期末 6 股、旧净投入 $520（不含费用） |
| T007 | 6 股、价 $110、净投入 $520 | 市值 $660、Excel 持仓盈亏 $140 |
| T008 | 累计净投入≤0 | 收益率显示“—”，不除零 |
| T009 | 亏损两笔 −100、−50，盈利一笔 +200 | 回本缺口 $150 而非 0 |
| T010 | 来源一笔交易 FILL 与 ORDER_TOTAL 并存 | 检出重复风险，不加倍持仓 |
| T011 | `REFERENCE` 券商成交 | 不进真实账户持仓 |
| T012 | `REVERSED` 交易 | 不进当前有效持仓，保留审计 |
| T013 | 两人合计数量=券商数量 | 人员/券商对账通过 |
| T014 | 人员合计数量不等于券商数量 | 明确报差额，不能隐式分配 |
| T015 | 内部划转 A→B 30 股 | A−30、B+30、券商总量不变 |
| T016 | 划转只有出记录无入记录 | DQ004 异常 |
| T017 | 拆股 2:1 | 股数 ×2，成本总额不变、每股成本 ÷2 |
| T018 | 反向拆股 1:10 | 股数 ÷10，金额不变（无零股结算时） |
| T019 | 已经由基准反映拆股的历史持仓 | 不重复拆股 |
| T020 | 缺历史收盘价 | 返回不完整，不使用 LATEST 冒充收盘 |
| T021 | 本地系统日期为周六，最新收益日周五 | “今日盈亏”按最新收益日显示并标注日期 |
| T022 | 最新收益日前有交易日 | “昨日盈亏”取上一个有数据交易日 |
| T023 | 跨自然月最后一日/第一日 | 月收益按正确月份切分 |
| T024 | 月初 0→日内买卖归 0 | “月度净投入明细”旧筛选不含该行，完整交易分析包含 |
| T025 | 同时两个币种却无 FX | 不直接相加，提示 FX 缺失 |
| T026 | 现金期初未知 | 显示现金变动而非假现金余额 |
| T027 | 先买后卖/先卖后买部分配对 | 微操数量和配对交易可追溯 |
| T028 | 微操收益 $100、月收益 $200 | 首页月收益仍为 $200，不变 $300 |
| T029 | 一份交易同时含本地账务时间/UTC | 账务日不因系统时区变化而漂移 |
| T030 | 长桥和嘉信持有同一个 Ticker | 券商视图分行，跨券商汇总合行，无重复 |
| T031 | 库中已停用人员有历史数据 | 历史可查，默认筛选行为明示 |
| T032 | 数据量 10 万条交易 | 窗口可响应，表格可滚动/分页 |
| T033 | 同一天多条交易时间相同 | 使用稳定序号，重放结果确定 |
| T034 | Excel 统计日早于数据表最大日期 | 页面同时标示两个截止日，给出对账时间选择 |
| T035 | 异常记录有 source_record_id | 能穿透原始 JSON、批次、文件/行号 |
| T036 | 数据文件是只读文件权限 | 首页仍可读，程序绝不尝试修改 |
| T037 | 图表过滤人员后切换月份 | 图表和表格口径完全一致 |
| T038 | 导出当前持仓 CSV | 列名 14 个、顺序与原 Excel 保持一致 |
| T039 | 关闭 GUI 重启 | 可恢复上次非敏感筛选设置 |
| T040 | 打包 `.app` 在 Mac 启动 | 能选库、查看关键页面并导出，无路径报错 |

### 11.3 来自 Excel 的真实黄金例（用于字段准确性检查）

**以下仅是 Excel 快照中的历史值，不是实时证券价格，也不能代表今天的真实资产。**

| Sheet/行 | 分组 | 检查项 | Excel 快照值 / 可推导事实 |
|---|---|---|---|
| 当前持仓第 2 行 | LONGBRIDGE / TQQQ | 持仓、报价、市值 | 230 股 × $70.14 = $16,132.20 |
| 当前持仓第 2 行 | LONGBRIDGE / TQQQ | 净投入 | $34,418.39 − $24,609.16 = $9,809.23 |
| 当前持仓第 2 行 | LONGBRIDGE / TQQQ | Excel 盈亏 | $16,132.20 − $9,809.23 = $6,322.97 |
| 当前持仓第 7 行 | FIRSTRADE / MUU | 持仓净投入、盈亏 | 净投入 $9,206.07；市值 $3,820.50；浮亏 $5,385.57 |
| 每月微操收益第 2 行 | 2026-10 / SCHWAB / GGLL | 买卖金额与微操收益 | $2,984.80 买、$3,120.94 卖，报表收益 $136.14 |

测试要区别**内部算术可验证**与**源数据库能否重算该值**。在真实数据库未提供前，只能对这些样本做算术与 Excel 抽取测试；不可伪称数据库全链路已通过。

### 11.4 基本代码规范/质量门槛

运行命令至少包括：

```bash
python -m portfolio
pytest -q
ruff check .
python scripts/inspect_database.py --db /path/to/money_2.db
python -m portfolio.cli.health --db /path/to/money_2.db
```

- 每个里程碑结束：测试通过 + 能启动 GUI + README 更新 + CHANGELOG 更新 + 异常清单。
- 核心引擎覆盖所有正/负/零/缺失/跨日/复权样本，建议目标覆盖率≥90%；对回放、划转和微操算法审查优先级高于单纯覆盖率百分比。
- 财务数值应按两种容差分别测试：数量精度默认 `1e-8` 股，金额**核心值按 e8 精确**；UI 两位小数与 Excel 报表兼容差额默认 `$0.01`（仅展示/黄金快照允许，不表示可把所有内部数四舍五入）。
- 不同来源费用若导致可解释差异，显示具体差额与费用口径，不得靠加大容差“通过”。

---

## 12. 性能、稳定性与安全

### 12.1 性能目标

以下为验收目标，需在真实 Mac 硬件上量测、记录环境：

- SQLite 库 10 万笔交易 + 200 万条日价格的性能合成数据下，主窗口必须持续可操作；按需分页/视图复用，禁止把所有行塞进 QWidget 单元格。
- 首页冷启动后首个可用状态目标 ≤3 秒（大库可先显示骨架状态，再异步计算，需给进度；不承诺所有报表一次算完）。
- 普通 1 万行表格筛选/排序交互目标接近即时；超大报告采用服务层聚合、结果分页。
- 切换筛选避免重新加载全部历史价格；根据条件缓存汇总/增量。
- 大量数据/失效数据/断开数据库不会导致界面崩溃，应有错误提示和可重试入口。

### 12.2 数据安全

- **源库默认只读，V1 不允许任何数据编辑写回**。
- 从文件菜单选择真实库时先备份（用户允许时），首次只读无需主动覆盖/重命名数据库；后续写功能强制先做可恢复备份。
- 禁止在日志/异常报告/截图中输出账户密钥、个人敏感字段、原始账户 token。
- `raw_records.payload_json` 可含隐私信息；详情 UI 提供敏感字段遮盖显示开关，导出前二次确认。
- 不上传数据库到任何云服务；外部行情/API 同步需明确选择同意、遵循平台条款并妥善保管访问令牌（后续可用 macOS Keychain）。
- 不保存个人姓名/账户余额于默认崩溃上传、第三方遥测。V1 默认无遥测。

### 12.3 数据来源完整性

- 源库的路径、文件大小、修改时间、结构检查结果在“设置 → 数据源”中可见。
- 查询每个价格附 `provider/status/date/time`；如果 `market_prices` 不直接有 provider，提供通过 `source_record_id` 回溯，字段提取失败标记未知。
- 采用“允许部分展示但明确标记不完整”策略，完整组合总资产不能在缺价时装作完全准确。
- 导入记录 `batch_key`、`content_sha256` 用于判断批次一致性；不能仅用文件名或时间去重。

---

## 13. 研发任务拆分和里程碑（必须逐阶段可运行）

### M0 — 仓库盘点 + 数据与口径审计（优先做）

**任务**：扫描源码/SQL/Excel；生成数据库字典与 13 Sheet/27 图表清单；校验真实库是否已提供；创建 `DECISIONS.md`，列出无法确定的配对/划转/复权细节。

**交付**：`docs/DB_MAPPING.md`、`docs/METRIC_DICTIONARY.md`、Excel 抽取脚本、`scripts/inspect_database.py`、源码/数据库安全检查报告。

**验收**：14 表/7 视图清单准确；18 KPI 定义无漏项；13 Sheet/27 chart 清单准确；未提供真实库时明确说明，不能报“完整通过”。

### M1 — 最小桌面应用与数据层（P0）

**任务**：创建 PySide6 主窗口、导航、主题、全局筛选；只读 SQLite 连接、schema 校验、演示库生成脚本；可查询人员/券商/股票/交易明细。

**交付**：可以在 Mac 用 `python -m portfolio` 打开、选择数据库、浏览 16 列交易记录和来源穿透。

**验收**：窗口可用；源数据库零写入；错误场景能处理；可运行集成测试。

### M2 — 持仓回放与基础财务引擎（P0）

**任务**：`Decimal/e8`、事件回放、数量/净投入/市值/收益、过滤维度、有效成交与参考记录隔离、基础拆并股与人员划转守恒检查；完成当前持仓 14 列、股票汇总 18 列。

**交付**：持仓/股票汇总页面、人员/券商钻取、异常记录入口；核心单测。

**验收**：合成样本正确；真实库存在时与 Excel 抽样对账；无数据不造数。

### M3 — Excel 首页完整迁移（P0）

**任务**：18 KPI、渠道/分类汇总、交易日历、季度矩阵、月度趋势；基于统一状态筛选；首页核心图表 01/02/03/07/08/10/16/18/20。

**交付**：实际可用的首页、指标说明弹窗、统计日与收益日并列展示。

**验收**：18 KPI 全部具备计算和解释；与实际数据库匹配的 Excel 样本可生成差异报告；支持无价告警。

### M4 — Excel 核心报表迁移（P0）

**任务**：每日收益 23 列、每月收益 24 列、净投入 10 列、清仓收益 13 列、原交易 16 列；可排序/筛选/导出。

**交付**：Excel 核心报表完整替换能力；点击行钻取交易/来源。

**验收**：列名顺序和定义对齐；月初月末、同日多笔、净投入过滤等边界场景通过。

### M5 — 微操配对与全部 27 图表（P1）

**任务**：微操 FIFO、打标、配对可追溯；原 27 图表全部迁移，图表数据联动、PNG 导出。

**交付**：14 列微操表、微操配对细节页、全部图表页面。

**验收**：真实微操黄金样本与 Excel 差异可解释；27 张均非空壳；图表标题和系列正确。

### M6 — 数据质量/对账中心（P0，部分应随 M1-M4 同步开发）

**任务**：DQ001-DQ018 检查、原始记录溯源、Excel 对账、银行/券商与人员仓位核对。

**交付**：可导出差异报表；所有严重错误可穿透到交易/来源；真实库尚未提供时标注缺失。

**验收**：至少 40 项测试用例；有重复/缺价/划转差异时能定位。

### M7 — Mac 安装包、文档与发布（P0）

**任务**：`.app` 打包、Mac arm64 冒烟、首次选库向导、设置/日志清理、使用手册、版本说明。

**交付**：`.app`、完整源码、版本锁定依赖、README、测试报告、示例数据、可复现打包步骤。

**验收**：非开发方式运行时不依赖系统 Python 项目路径；可以选库、看首页、查交易、导出；源库无修改。

### M8 — 后续能力（P2，不阻挡 V1 发布）

券商/API 增量同步、行情自动更新、价格与最大回撤提醒、TWR/XIRR、成本法切换、清仓后表现、自动月报、可写账本与撤销审计、跨设备同步（需用户另行决定）。

---

## 14. 研发验收检查清单（DoD）

**任何模块完成的定义：**

- [ ] 对应源 Excel 表头、说明表/逻辑和 SQLite 来源已核查
- [ ] 计算函数和界面完全分离
- [ ] 财务金额和股数使用 Decimal/e8，`NULL` 不当 0
- [ ] 只读连接、参数化 SQL、正确事件粒度
- [ ] 人员与真实券商账本的范围明确，不双算
- [ ] 有空数据、重复数据、缺价、异常状态的处理
- [ ] 当前页面所有控件可用，未实现的 P2 功能不装作可用
- [ ] 每个关键数字可查看指标公式/时间/来源
- [ ] 支持全局筛选、排序、搜索、导出（适用表格时）
- [ ] 对应单元测试、集成测试、必要 GUI 测试已执行
- [ ] 与 Excel 样本对齐或已生成可解释差异报告
- [ ] 改动文件列表、运行命令、实际测试结果写入 CHANGELOG
- [ ] 原数据库未被改动；测试临时文件已清理

---

## 15. Codex 的工作方式与每次回复格式

### 15.1 工作机制

- 按 `M0 → M1 → M2 → ...` 顺序**连续实现能够完成的任务**，每完成阶段运行实际检查。
- 面对确定的需求不反复询问；发现源库缺失也不阻断构建骨架和合成数据测试，只把真实核对标注为待完成。
- 发现重大含糊点（微操跨月归属、成本划转金额、已复权价格、历史基准重叠）不要擅自决定不可逆规则；给出候选方案、实际资料证据、默认安全策略及被阻挡的验收项。
- 如现有项目已经有规则或测试，优先复用并做回归，不能按本文建议任意重构掉已工作的功能。
- 先尽量做 M0-M4 可用版本；后续 M5+ 由剩余任务清单继续推进，任何阶段都保持应用可运行。

### 15.2 每次提交/阶段回复格式

```text
【完成阶段】M?
【做了什么】具体页面/公式/接口/测试
【修改文件】逐文件列出
【关键实现】账本来源、统计维度、口径、时间规则
【实际运行】命令 + 运行结果，不可编造
【已验证】用例编号/Excel 对账差异
【未完成/风险】真实库缺失、规则缺证据、性能风险
【下一阶段】明确可执行的 3~6 项任务
```

---

## 16. 直接复制给 Codex 的启动提示词

> 请读取当前目录的 `docs/PRODUCT_REQUIREMENTS.md` 和根目录 `AGENTS.md`，严格按照《Mac 股票投资管理系统完整开发需求》实施。我的项目数据来自 `references/sqlite_schema.sql`（数据库表结构）和 `references/个人投资总览.xlsx`（旧系统报表、数据及公式）。**先检查仓库和现有代码，再执行 M0 与 M1**：建立只读 SQLite 数据访问层、schema 检查、Qt/PySide6 可运行桌面应用、全局筛选和交易明细页面，并提供基于原 schema 的临时测试数据库及测试。**不要修改原始数据库、不要硬编码真实投资金额、不要采用浮点财务计算**。随后继续按 M2、M3、M4 构建持仓、18 个首页指标、每日/月度/净投入/清仓核心报表，逐步完成原 Excel 13 Sheet 和 27 张图表的迁移。每个阶段都运行测试并报告改动文件、运行结果和当前不能证明的对账项。缺少真实 `.db` 文件时先完成可测合成样本和界面，不要假称已与真实数据库一致。

---

## 17. 交付验收与用户最终体验

最终用户在 Mac 上应能：

1. 双击 `.app` 打开中文投资软件。
2. 首次点击“选择数据库”，选择本地真实 SQLite 文件，看到只读状态和结构检查结果。
3. 打开首页立即看到 18 个指标、资产/收益状态，并知道数据到底截至何时。
4. 选择某个人、某个券商或者某只股票，所有页面同步过滤。
5. 查看与旧 Excel 对齐的 14/18/23/24/14/10/13/16 列核心表格。
6. 打开日收益、月收益、月净投入、交易日历、季度矩阵和收益走势图。
7. 在股票走势图上识别所有买入/卖出点，穿透到成交价、股数、人员、券商及 Excel/API 原始来源。
8. 查看已清仓与当前持仓收益、Excel 净成本/标准成本的差异、人员划转和拆股影响。
9. 点击数据质量中心，准确识别负持仓、重复订单、缺价格、待审核和归属不一致的问题。
10. 导出当前筛选报表、图表及 Excel 对账差异。
11. 即使脱网，也可查看本地已同步的历史交易/行情，价格来源和时间清楚。
12. 更新项目后，原始账本不会被覆盖，能够持续运行回归测试。

**最终优先级结论：财务计算准确 + 原 Excel 兼容优先，漂亮的界面和新增分析随后增强。**

---

## 附录 A：关键业务争议清单（必须写入 DECISIONS.md）

| 编号 | 需要澄清的事实 | 暂行安全策略 |
|---|---|---|
| A01 | 微操 FIFO 是同日/同月/跨月配对，卖买部分先后如何标号？ | 用来源与黄金对账求证；未核验不宣称一致 |
| A02 | 人员 `OWNER_TRANSFER` 是否完全继承净投入成本？ | 只确认数量守恒，成本标记待审 |
| A03 | `broker_trades` FILL/ORDER_TOTAL/SOURCE_RECORD 如何在不同适配器去重？ | 以稳定执行/订单关系分层去重，歧义进入 DQ005 |
| A04 | 历史 `market_prices` 是否复权过？ | 不二次复权；等待来源证据 |
| A05 | `historical_baselines` OPENING 基准与交易历史如何切换？ | 只回放基准之后交易并对账 |
| A06 | `asset_adjustments.target_metric` 实际有哪些值和含义？ | 未知 target 不进入财务指标 |
| A07 | 费用是否已含在 calculation_amount 与净收入？ | Excel 兼容和严格现金两口径分别处理；检查重复 |
| A08 | Excel 首页统计日 10/15 与日报至 10/16 的差异 | 分开报告估值日和收益日，阻止错误“同日”对账 |
| A09 | “当前资产”和“累计本金”是否只含股票，不含现金？ | Excel 模式仅股票；完整资产另建模式 |
| A10 | 汇率应用在成交日还是报告日？ | 原币账本不变；报告换算按明确配置并标注 |
| A11 | 股票类别“高风险”“待确认”冲突如何映射？ | 数据库保持约束值，旧分类别名单独管理 |
| A12 | 已清仓后重新开仓，旧“累计清仓收益”是多次累计还是只算最后周期？ | 依据旧程序/金样本核定，不自行删除历史收益 |
| A13 | 同一时间交易的 `sequence_no` 和 broker trade 顺序关系 | 依据人员账本排序，最终稳定 id 兜底；异源顺序有疑点报告 |
| A14 | 现金事件同时存在券商净成交手续费记录时如何避免重复？ | 通过明确事件关联与对账判重 |
| A15 | 报表日期是交易所时区还是账务 `business_date`？ | 既存账务归属以 `business_date` 为准，单独显示市场日 |

## 附录 B：数据计算和 UI 显示的严谨区分

| 术语 | Excel 旧口径 | 更严格的投资管理含义 |
|---|---|---|
| 当前总资产 | 当前股票市值合计 | 股票+现金及其他资产−负债（须数据齐全） |
| 当前本金/净投入 | 股票累计买入减卖出 | 外部资金实际投入是不同概念 |
| 浮动盈亏 | 市值−股票累计净投入 | 未实现盈亏通常基于剩余税务/会计成本批次 |
| 已清仓收益 | 完全清仓标的历史卖出−买入 | FIFO/加权成本法可在部分卖出时确认已实现盈亏 |
| 微操收益 | 做 T 匹配形成的交易分析指标 | 已包含在相应交易回报内，不可额外相加 |
| 当日收益率 | 今日收益/反推昨日市值 | 严格时间加权须正确处理外部现金流时间 |
| 当前现金 | Excel 辅助调整值可能为空 | 需要真实期初余额+完整收支流水才可称现金余额 |
| 历史持仓市值 | 每日持仓 × 对应日收盘价 | 历史复权和价格缺口必须明确 |

## 附录 C：官方参考文档

1. Qt for Python 总览：https://doc.qt.io/qtforpython-6/
2. Qt Models / Views：https://doc.qt.io/qtforpython-6/overviews/qtwidgets-modelview.html
3. PySide6 官方部署工具：https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html
4. Python sqlite3：https://docs.python.org/3/library/sqlite3.html
5. SQLite URI / 只读访问：https://www.sqlite.org/uri.html
6. Python Decimal：https://docs.python.org/3/library/decimal.html
7. pytest：https://docs.pytest.org/en/stable/

> **资料优先级**：①真实数据库字段约束与有效源记录；②随项目提供的原有计算程序/测试（若存在）；③Excel 两张取值逻辑说明；④Excel 当前展示/缓存结果；⑤本规格书的推导与改进建议。发生冲突必须提供差异及证据并经人工确认，不得无声改变原数据口径。

**文档完。**
