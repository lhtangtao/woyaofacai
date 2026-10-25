# M0 / M1 阶段验收 · 2026-10-17

## 执行环境

- macOS 27.0.1，Apple Silicon arm64。
- 独立 money3/.venv，Python 3.12.14，PySide6 6.12.0，pytest 9.1.1、pytest-qt 4.5.0、Ruff 0.17.1。
- GUI 自动测试采用 Qt offscreen；原生窗口使用 macOS Cocoa 显示会话另行验收。

## 完成阶段与实际命令

| 阶段 / 命令 | 真实结果 |
|---|---|
| `python scripts/audit_sources.py` | 14 表、7 视图、13 Sheet、27 chart、18 KPI；生成 SOURCE_INVENTORY、DB_MAPPING、METRIC_DICTIONARY |
| `pytest -q tests/test_m0.py` | 初次独立阶段验收 17 passed；完整资料、表头、chart series、快照算术与 e8 边界 |
| `pytest -q tests/test_data_layer.py tests/test_gui.py` | 初次 M1 验收 25 passed；随后新增刷新保留筛选与原始 JSON 高精度测试，在最终全量验收中通过 |
| `pytest -q` | 最终 44 passed，无跳过项；仅代表当前 M0/M1 实现，不代表 M2–M7 全部验收项 |
| `ruff check .` | All checks passed |
| `python scripts/inspect_database.py --db ../money_2/数据库/money.sqlite3 --integrity` | 真实库结构兼容、14 表、7 视图、integrity_check=ok、foreign_key_check=0 |
| `python -m portfolio.cli.health --db ../money_2/数据库/money.sqlite3` | 同一只读检查入口，可在无 GUI 环境运行 |
| `python -m portfolio --demo --no-settings --smoke-ms 3500 --screenshot <临时PNG>` | 原生 Cocoa 窗口成功打开、读合成库、抓取自身窗口、自动关闭，退出码 0；人工查看截图，主要控件/表格/状态均可读 |
| `python -m portfolio --db ../money_2/数据库/money.sqlite3 --no-settings --smoke-ms 3000` | 原生真实库只读窗口成功启动/关闭，退出码 0，未截取真实资产内容 |

补充命令记录：安装依赖与 editable 包成功，实际版本见 requirements-lock.txt。首次沙箱内原生启动因无显示会话失败；获得显示会话访问后原生冒烟成功，未用该失败冒充通过。

## 数据与计算证据

- 真实人员账本只读查询：1,467 条，第一页 200 行，16 列；首条可追溯到来源记录。
- 真实券商账本只读查询：1,230 条，第一页 200 行，16 列；首条可追溯到来源记录。
- 以上是本次连接时的有效查询行数，不是持仓数、持股数或收益核对结果；源库后续同步可改变行数。
- 元数据核验：当前源库历史基准均为 REFERENCE；目前有 REVIEW_REQUIRED 的 OWNER_TRANSFER，后续财务引擎不得静默假设转移成本已确认。
- 参考 Excel 的全部图表引用、定义名称、公式和缓存值已抽取为私有本地快照；13 Sheet / 27 chart 与需求一致。
- TQQQ 黄金样本只验证原快照行的数量×价格、买入−卖出、市值−净投入算术；不声称从真实库重放得出相同收益。

## 源文件保护

- 正常只读查询、原生应用启动及测试后，真实数据库的 SHA256、字节数、mtime 均与本轮指纹基线一致。
- Downloads 中原始参考 Excel 的 SHA256、字节数、mtime 均未改变；money3/references 副本与提供原件逐字节相同。
- 写保护测试逐项拒绝 UPDATE / DELETE / INSERT / DROP / CREATE / ATTACH / 改写 user_version / 关闭 query_only / 改 journal_mode，并核对合成源库哈希不变。
- 查询参数绑定，搜索中的引号/通配符不改变查询范围；长订单号以文本保留；NULL 金融字段保持缺失。
- 所有建库都仅发生在内存或排他创建的合成目标文件；没有对真实库执行建表或迁移。
- 私有原件、生成快照、临时报告与运行环境被版本控制忽略；未执行提交或上传。

## 尚未完成与已知边界

1. M2 财务回放未实现：后持仓、后净投入、后均价无结果，界面明确缺失。
2. M3–M5 的 18 KPI、所有收益报表、微操、27 图表未实现；微操列不伪造标签。
3. M6 完整 Excel/数据库同日同范围对账未完成；不能将“结构兼容、查询可用”表述为财务完全一致。
4. 仅 USD 原币、本页 CSV 导出；完整筛选导出、换汇、跨重启筛选偏好尚未开放。
5. 大规模性能目标（10 万交易 / 200 万行情）、完整查询取消负载、M7 .app 打包未验收。
6. UI 的分页行数和元数据待审数不构成财务指标；多粒度成交的经济重复核验还未运行。
7. 结构检查验证表/视图和必要字段类型/非空/主键；尚未证明所有额外 CHECK/业务枚举的结构等价性。

## 下一阶段（M2）

1. 提取旧程序的拆股顺序、净成本、人员划转、重复粒度和重开仓规则，建立 Decimal 金样本。
2. 实现不依赖 Qt 的事件规范化与账本分别回放，覆盖同日顺序/参考/撤销/不完整归属。
3. 完成 14 列当前持仓与 18 列股票汇总，缺行情保留不完整状态。
4. 以匹配日期、人员、券商范围执行真实库与 Excel 抽样差异报告，再开始首页 KPI。
