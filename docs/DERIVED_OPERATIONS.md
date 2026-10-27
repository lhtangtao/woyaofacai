# 双数据库初始化与人工验收

代码已修改，未执行测试，待用户验收。以下命令已在代码中实现，但本轮没有执行初始化、迁移、网络补取或生产全量生成。请在项目目录运行，使用本项目`.venv`。

## 两个业务数据库

- `数据库/money.sqlite3`：原始交易、归属、历史基准、调整、公司行动、汇率、原始行情；显式`import-history`新增`history_prices`、`history_price_imports`、`history_fact_revisions`。不改写原始交易表。
- `数据库/money_derived.sqlite3`：`objects`（按人员/账户/证券/月的回放、持仓、微操和收益单元）、`intraday_contributions`、`report_details`、`partitions`、`partition_inputs`、`source_manifest`、`invalidations`、`jobs`及`metadata`。
- 原有应用数据目录的`live_quotes.sqlite3`继续保存临时报价、下载状态、交易所日历、财报与渠道现金等独立状态。账号、密码、Session及监控库不迁移。
- 迁移只复制原始历史价格，不复制旧`historical_pnl_points`金额，也不重贴Money2缓存键。旧库保留。显式初始化事实行情后，新K线与正式日收盘价由行情保存入口写入事实库，旧缓存不再保存第二份新增权威价格；历史旧副本作为恢复证据保留。
- 旧K线没有逐根确认元数据，保留`LEGACY_COMPLETED`；新完成K线为`PROVIDER_COMPLETED`，正式日收盘为`CONFIRMED`。这些状态不因288个点自动升级。价格冲突拒绝静默覆盖。

## 首次操作

```sh
cd /Users/tangtao/money3
# 只读：表盘点及完整性
.venv/bin/python -m portfolio.derived inspect
# 只读：全部历史权益所需的证券/交易日，逐时段和UTC缺口
.venv/bin/python -m portfolio.derived coverage --start 2026-01-01 --end 2026-10-27
# 明确写入授权入口：先在线备份源库，再事务追加旧历史行情
.venv/bin/python -m portfolio.derived import-history
# 若日历覆盖证据不足，显式联网获取真实全年日历
.venv/bin/python -m portfolio.derived fetch-calendar --start 2026-01-01 --end 2026-12-31
# 如覆盖报告确有缺价，显式仅补缺失证券/日期；不重新下载完整日期
.venv/bin/python -m portfolio.derived fetch-missing-prices --start 2026-01-01 --end 2026-10-27
# 仅创建空派生库
.venv/bin/python -m portfolio.derived init
# 首次全部历史人员（包括巴小、停用人员）和独立券商账本
.venv/bin/python -m portfolio.derived rebuild --mode all
# 只读查看生成、完整性状态和任务摘要
.venv/bin/python -m portfolio.derived status
```

默认起点2026-01-01，截止美东当前日期；夜盘按原交易日时段处理。更早的成交和成本基准始终加载，不被展示起点截断。检查其他历史期间可显式传`--start`与`--end`。自选源库/旧行情库参数在子命令前：`python -m portfolio.derived --db <Money3源库> --legacy-quotes <旧行情库> inspect`；不自动探测兄弟项目。

## 日常启动与维护

```sh
./启动Money3.command
./启动Money3.command --no-sync --rebuild-derived=changed
./启动Money3.command --no-sync --rebuild-derived=all
# 单独维护，不启动界面
.venv/bin/python -m portfolio.derived check
.venv/bin/python -m portfolio.derived rebuild --mode changed
# 单独启动任一客户端也支持相同重建参数
.venv/bin/python -m portfolio --rebuild-derived=changed
.venv/bin/python -m portfolio.web.app --rebuild-derived=changed
```

一键启动先完成显式同步与指定重建，再启动Web和Mac。`--no-sync`确实跳过同步，`--api/--excel/--only-mobile/--no-mobile/--no-public`保留。普通启动不隐式首次全量生成；用户首次成功发布后，变化或已失效结果可以后台增量生成。图表缺失/失效分区留空，不能当作有效旧金额展示。启动器原有“Web源码比产物新则构建”机制保留，用户启动时可能调用npm；本轮未运行构建。

## 计算与查询

- 人员从全部历史账本、基准、现金及调整中发现，不依赖当前UI，不写死ID；包含停用人员和巴小。无历史权益的空人员不批量生成零记录。
- 回放按人员/账户/证券与月份保存，并保存日末持仓供五分钟开账复用。微操与每日/月度收益按月复用，新增今天的行情不要求重算已确认且依赖不变的旧月。
- 每日、每月、微操、净投入、历史持仓、累计收益、历史资产、清仓及股票汇总、排行榜与27类原图表，经原`AnalyticsService`使用持久化计算单元；当前界面的比率、占比、组合及仪表盘继续按原公式聚合。`report_details`是预生成财务明细及核对资料，普通报表并非直接读取一整张筛选组合快照。
- 同一持仓存在多份OPENING基准时，保留原引擎按截止日取最新基准的整单元计算并持久化；其输入变化可能需要回算整个单元，未强行套用月度增量。
- 多人员组合保留原引擎对负仓、开账及分组语义，从持久化人员回放状态重新聚合；不能承诺所有多人员页面只执行一条SQL。当前实时持仓/资产/今日盈亏仍由原实时引擎计算。
- 月度30分钟、年度2小时，均来自同一五分钟贡献。已有日收益参与跨日基准；缺日价导致收益未知时，后续累计保持未知。没有价格时不编造日内点，不将首个有缓存的10月标为年初。日结与日内若有差异不再人为平滑，需核对原始价格/成交时间。
- Mac/Web使用同一源路径、行情缓存、Python引擎、版本和派生查询。Web在读取前应用现有人员授权；全库状态接口仅具备管理用户能力的账号可访问。

## 失效、并发与恢复

源表行内容摘要与SQLite提交版本用于变更检测，不以文件mtime/WAL大小替代。原始记录变更关联其实际业务日期；今日临时价与无财务影响的原始审核记录不误伤全年。事件、归属、费用、基准、拆股、价格修正和算法源码版本纳入依赖；无法精确归属时扩大失效范围。失效和重算独立。

全量与增量维护使用跨进程构建锁，写`.building-*`临时派生库。所有历史人员、每日/月度精确核对、对象校验及SQLite完整性通过后原子替换。旧代保存在`.previous-*`；失败不发布新代，失败任务与临时库保留供诊断。实时分区按事务替换。原始账本在计算期间变化会取消发布。

事实迁移先生成`money.sqlite3.before-history-*.bak`，再在同一写事务导入，核对所有原始表摘要与外键；异常回滚。工具不会自动用备份覆盖真实源库。若要恢复事实库，应先关闭同步及客户端，保留当前库与WAL并人工核对备份，避免丢失迁移后的交易。

## 人工验收与已知限制

1. 先检查覆盖报告、事实备份与导入数量，再首次全量生成；本轮未执行这些操作。
2. 不先点击巴小，直接检查其历史分区，再在双端切换巴小/个人/停用人员授权范围，确认无全年回放或重复下载。
3. 对比单人/多人、渠道、Ticker在每日/月度/微操/净投入/清仓/资产/排行榜上的金额及缺失值；尤其核对多次开账、负仓、划转、拆股、纯日期成交和跨月滚动。
4. 查看2026年1—9月年度曲线、正确年初累计、月/年同日采样、最高最低、缺五分钟但有日收益的跨日衔接；未来时段保持空白。
5. 手工验证原始成交、归属、历史价格更正及算法变更后的失效；同时启动Mac/Web并尝试重复重建；中断生成，确认旧代仍可用、失效金额不冒充有效金额。
6. 验证普通账号不能读取其他人员、匿名局域网继续只读；验证断网、缺价、延迟行情与临时月末状态。

尚未验证：所有运行时路径、SDK补取、首次迁移/全量生成耗时与磁盘量、两端金额等价性、打包及并发故障恢复。旧行情来源/确认元数据不足不能靠本轮代码补成“已确认”。报价历史中没有真实K线的时间段仍会缺失。多次OPENING基准与原引擎按截止日取最新基准的语义需要重点人工核对，不能把静态解析成功当作财务对账通过。
