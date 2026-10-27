# 双数据库重构（2026-10-27）

## 盘点与边界

Money3源库包含真实 broker_trades、person_operations、人员/账户/证券、历史基准、公司行动、调整、原始记录、费用证据、market_prices与fx_rates。原始价格状态包括LEGACY_UNKNOWN，不自动升级为正式确认。
旧live_quotes库：latest_quotes与daily_quote_snapshots是临时/估值；official_daily_closes是正式日价；intraday_candles是已完成K线但旧结构没有逐根来源/确认元数据；intraday_fetches与us_calendar/coverage是下载/日历证据；historical_pnl_*、minute_pnl_snapshots是派生结果；earnings_calendar_cache与channel_cash_snapshots保持原存储。安全账号、Session、监控不迁移。

本次只读实际盘点：2026年1—9月分别有124128、124128、146304、146880、131424、122112、130464、126432、119808根K线；不能把缺盈亏缓存解释成缺行情。288个盈亏点不是价格完整性证据。旧K线需保留来源及待核验状态，不因数量足够声明确认。

## 目标结构及兼容

- `数据库/money.sqlite3`：事实库，显式初始化增加history_prices、history_price_imports；稳定证券ID、UTC、时段、复权、来源、确认状态。旧行情只复制，冲突拒绝覆盖；旧库保留供恢复。
- 同目录`money_derived.sqlite3`：可重建缓存；内容指纹、算法版本、计算对象、按人员/账户/证券的历史回放、五分钟贡献、日报明细、任务及发布代次。金额使用Decimal字符串，JSON使用白名单类型编解码，不反序列化pickle。
- 不改变EXCEL_COMPAT、成交时间解析、缺失值和不同账本的隔离。细粒度回放状态为公共层；收益率与占比仍调用原引擎计算。
- 首次初始化/全量计算必须显式由用户执行。全量写入临时派生库，校验后原子发布；操作系统文件锁协调Mac/Web；失败保留旧版本。事实迁移先SQLite在线备份，并核对旧表内容摘要。
- 内容依赖失效与计算分离；不依赖mtime/WAL。指纹不匹配的结果不能读取；历史输入不变则跨启动复用。未确认行情、缺行情、无缓存、计算失败、未来分别呈现。

## 实际链路

UI/Web → AnalyticsService.calculate → load_snapshot → scalping/performance → replay → holdings/dashboard/charts；日内通过intraday_history.rebuild_history，原先按scope_key写QuoteStore；月/年页面直接SQL读取旧盈亏表且以最后日内点累计，缺日可错误重置基准。统一接入需同时替换底层回放缓存、日内贡献服务、月年曲线与两个入口。

## 开发与验收

六次阶段提交：盘点；存储及迁移；历史预计算；失效与启动；双端接入；交付。
本次用户禁止测试/构建/生产迁移/生产重算。所有运行及财务等价性由用户验收；代码完成不代表已验证。阶段交付和已知限制持续记录在本文末尾。
