# 分阶段实施计划

## 2026-10-17 后续阶段执行状态

用户授权继续后续所有阶段；当前版本 0.2.0。

| 阶段 | 已落地 | 验收状态 |
|---|---|---|
| M2 | Decimal 回放、拆股、开账截止、划转、14/18 列持仓/股票 | 引擎测试通过；真实旧表精度/日期差异保留 |
| M3 | 18 KPI、两种本金口径、日历、季度、趋势 | 公式测试通过；不完整数据保持缺失 |
| M4 | 日23/月24/净投入10/清仓13/交易16、整份/本页导出 | 契约及边界测试通过 |
| M5 | 双向月内配对、证据、27图表、证券买卖点 | 图表绘制通过；旧残额分摊算法差异未等价验收 |
| M6 | DQ001–018、Excel逐字段差异及CLI/GUI | 工具可用；真实财务一致性验收未通过 |
| M7 | 官方 pyside6-deploy Apple Silicon .app | 本机原生启动/导出/关闭/重启检查；未做签名、公证或其他机器测试 |

详细文件、命令、性能、数据完整性及遗留项见 VALIDATION_M2_M7.md。
下方保留原计划，不把“功能代码已落地”称为“财务等价验收通过”。

## 已确认范围

开发根目录为 money3；当前由本目录 portfolio.sync 负责券商/文件同步和交易流水入库。
Money3展示层只读本地库；在本目录交易流水补充【谁】后，显式执行本地 --excel 同步，再刷新。
这与需求“Excel 不作为软件运行时账本”一致，不改变此前授权的数据维护流程。

采用 Python 3.11+、PySide6 Qt Widgets、sqlite3。分层为 domain → db/repositories → services → ui；财务计算在独立 engine，不导入 Qt。
保留 EXCEL_COMPAT 与 ACCOUNTING 的边界，后者在证据和测试齐全前禁用。所有金额和股数在边界以整数 e8 解码为 Decimal。

## 里程碑

| 阶段 | 交付 | 实际验收边界 |
|---|---|---|
| M0（本轮） | 完整源码/SQL/Excel 盘点、字典、27 图表系列、18 KPI、决策清单、提取脚本 | 结构和快照算术核验，不冒充真实账本收益对账 |
| M1（本轮） | 中文主窗口、选库、只读结构检查、人员/券商分账查询、多账户和证券筛选、16 列交易表、分页排序、原始来源、CSV 本页导出 | 完成 T001–T004、T011–T012、T029、T031、T033、T035–T036 的相应查询/安全部分及 GUI 测试 |
| M2 | Decimal 事件重放、拆并股、权益划转守恒、14/18 列持仓和股票表 | 针对旧程序建立参数化金样本；解决 A02/A03/A04/A05/A12 的影响后展示数字 |
| M3 | 18 KPI、渠道/分类、收益日历/季度矩阵、核心图表 | 18 KPI 解释和同范围对账，明确估值日与收益日 |
| M4 | 日 23/月 24/净投入 10/清仓 13/交易 16 列完整计算与导出 | 跨月、清仓、月初月末过滤及缺价边界 |
| M5 | 微操 14 列、逐笔 FIFO 证据、全部 27 图表 | 与原微操代码/Excel 样本对账，不能把微操收益再叠加月收益 |
| M6 | DQ001–018、完整 Excel 差异报告 | 不自动修正源库；所有严重差异可追溯 |
| M7 | macOS .app、启动/选库/导出/重启回归 | Apple Silicon 实机打包验证，不以 Python 冒烟替代安装包验收 |

## 本轮具体文件职责

- AGENTS.md / docs/PRODUCT_REQUIREMENTS.md / references：保留提供的规则与原件，私有 Excel 不提交 Git。
- docs/SOURCE_INVENTORY.md / DB_MAPPING.md / METRIC_DICTIONARY.md：由脚本从实际资料生成。
- docs/DECISIONS.md / RECONCILIATION.md：冲突、未确认业务规则与验收状态。
- src/portfolio/db：只读 URI、query_only、写权限拦截、14 表/7 视图及必要列检查。
- src/portfolio/domain：Decimal/e8、查询范围和两个独立账本的类型。
- src/portfolio/services：分页查询、来源追溯、来源日期及只读本页导出。
- src/portfolio/ui：异步工作线程、全局筛选、表格、详情与错误/空态；连接不跨线程复用。
- scripts：只读 Excel 抽取、结构检查、合成库创建。创建合成库采用排他创建，拒绝覆盖任何已有文件。
- tests：隔离合成库、只读约束、精度、分页/筛选、权限与 GUI 测试。

## 财务正确性边界

M1 的交易表保留 16 个原始表头，其中微操标记与 3 个回放字段暂无引擎结果，显示缺失而非 0。
字段提示明确为 M2/M5 待计算，不借用旧 Excel 的缓存作为运行时结果。
未解决的成交粒度冲突只展示原始行和状态，不据此生成持仓/收益合计。
本轮不新增任何行情请求、券商授权、同步、源库迁移、源库写入、财务口径变化。

## 官方实现参考

- SQLite URI / mode=ro：https://www.sqlite.org/uri.html
- Qt QAbstractTableModel：https://doc.qt.io/qtforpython-6/PySide6/QtCore/QAbstractTableModel.html
- Qt 工作池：https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThreadPool.html

## 后续性能验收

M1 已采用数据库分页、虚拟化表格、线程独立连接和取消/超时，不一次加载全部交易和行情。
10 万交易 / 200 万行情的目标量测、完整图表联动和 .app 安装验收留给后续阶段，未以小库测试替代。
