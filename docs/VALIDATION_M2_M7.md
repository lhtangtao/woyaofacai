# M2–M7 实施与验收记录

日期：2026-10-17。版本：0.2.0。环境：Apple Silicon、macOS 27、Python 3.12.14、PySide6 6.12.0。

## 总体结论

后续各阶段的可运行实现已接入，包括纯 Decimal 回放、18 KPI、完整字段报表、27 图表、异常、Excel 对账与 macOS 安装包。
**功能可运行不等于财务等价验收通过。M6 真实旧表对账仍有大量差异，本版作为用户检查的候选版本，不声称全部原 Excel 数字已复刻。**
本轮不修改真实源库、参考 Excel 或 Money2 的已有代码。源库及参考 Excel 的 SHA256、大小、mtime 均保持不变，私有证据为 reports/source_integrity_m2_m7.json。

## 完成阶段、修改文件及业务来源

### M2：数量和净成本

- 新增 domain/analytics.py、db/repositories/analytics.py、engine/replay.py、engine/holdings.py。
- 来源为 broker_trades 或 person_operations 的独立有效账本。过滤 ACTIVE/ACCOUNT_LEDGER；人员默认隐藏停用，但全历史数量对账包含停用人员。
- e8 在边界精确解码；核心不导入 Qt、Pandas 或 float。排序为账务日、时间、公司行动优先、sequence、稳定 ID。
- 累计买卖和净投入在清仓/重开仓后仍保留生命周期；拆股调整数量及累计买卖股数，不改金额。
- OPENING 以截止日进入，不再累计此前交易；REFERENCE 不入账。未知划转成本和成交粒度不确认为完整财务值。
- 输出当前持仓14列、股票汇总18列、清仓13列，原始候选记录保持可追溯。
- 验证：test_replay.py 及只读数据层首轮合计29项通过；后续开账、粒度等用例继续覆盖。

### M3：指标和首页

- 新增 engine/dashboard.py、engine/cash.py、engine/fx.py、ui/dashboard_page.py。
- 18 KPI 使用需求中的兼容公式，渠道本金含清仓净投入，分类本金仅含当前持仓。
- 今日/昨日依据数据库最大收益日和前一有数据日；日历可点击，季度按日开盘资产复利，未完成季度不年化。
- 现金与股票总资产分开。没有可靠期初/净现金额或费用可能重复时，现金余额保持缺失。
- CNY 是 USD 结果的显示折算，来源汇率须日期匹配。月收益逐日折算后合计；历史汇率缺口会传到月KPI，不被期末汇率掩盖。收益率仍是 USD 兼容口径，不是 CNY 标准会计回报。
- 验证：收益样例、非自然日昨日、季度边界、现金未知/重复费用、FX缺口及金额合计；并纳入全量回归。

### M4：完整报表

- 新增 engine/performance.py、services/analytics_service.py、ui/report_page.py；扩展 services/export_service.py。
- 日收益23列、月收益24列、净投入10列、清仓13列、交易回放16列均有可执行契约测试。
- 价格时间轴来自源库，不造周末假零；真实交易在缺价日仍保留并提示。缺收盘或期初价值时不借当前价计算历史收益。
- 月收益由日收益汇总，并核对月初/月末公式。月内开平仓为零不进旧净投入表，但在完整交易净投入中保留。
- 起始日不裁掉成本历史；报表过滤在回放之后执行。
- 整份当前筛选/本页导出 CSV、JSON、XLSX；使用标准库生成 OOXML，Decimal按文本保存，参数页说明精度和筛选，不写公式或宏，不覆盖现有文件。
- 验证：T006/T007 合成例为6股、净投入520、市值660、盈亏140；月收益140，微操80不会叠加为220。缺价、跨日、拆股、月内回零和导出精度均测试。

### M5：微操及图表

- 新增 engine/scalping.py、services/charts_service.py、ui/charts_page.py、ui/stock_detail.py。
- 月内双向配对；兼容旧程序的最近买入优先/FIFO卖出顺序；纯引擎另外支持FIFO测试。
- 固定订单费按每张订单1美元乘实际配对比例分摊，不按每股1美元。转移权益单列，不作为券商成交或微操买卖。
- 逐笔证据包含原始买卖、来源、分摊、配对策略；有成交粒度冲突时不确认数量/收益，保留候选证据。
- 原Excel全部27个图表都有独立数据集，图24–27保持渠道/分类两组维度，图21可换月份。
- 支持悬停、联动、正负柱形、图表PNG；证券详情显示原币价格及B/S/划转标记。
- 验证：全部27图实际绘制并导出PNG；部分配对、先卖后买、拆股、订单费与策略差异测试。
- 未通过项：旧源码的残额分摊缺陷未复制，本版按原始金额比例计算；微操结果存在明显差异。原源码还会按配对逐项舍入、忽略小股数，本版不把这些行为当精确核心规则。详见DECISIONS A19/A20/A24。

### M6：异常与对账

- 新增 engine/audit.py、portfolio/reconcile.py；扩展报告导出和GUI对账页。
- 实现DQ001–018。全历史人员数量与独立券商数量核对；检查超分配、单边划转、多粒度、解析/资金/调整待审、缺价/汇率、日期差异、复权、开账重叠等。
- 只诊断不自动改库。界面可按ERROR、规则号、证券搜索，来源明细默认遮盖敏感字段。
- CLI/GUI支持18KPI、明细、渠道/分类、交易后状态及27图表系列核对。
- Excel公式空缓存不当成零：18KPI由参考明细独立推导，汇总和动态系列缓存缺失保持UNAVAILABLE。
- 实际执行命令及本地私有报告：

```bash
.venv/bin/python -m portfolio.reconcile \
  --db ../money_2/数据库/money.sqlite3 \
  --excel references/个人投资总览.xlsx \
  --as-of 2026-10-15 --person 个人 \
  --report reports/excel_diff_release.csv
```

该次结果：203,707项PASS、4,522项DIFF、9,443项UNAVAILABLE。按单元格/字段计数，不是“通过率”，无意义空值不会当PASS。
差异主要分布在交易明细2312、日收益1287、月收益336、股票汇总224、微操162、持仓143等；数字为该次数据快照，后续入库会改变。
源报告：reports/excel_diff_release.csv/.json；摘要：reports/reconciliation_summary.json。

已核实的具体边界：

- 原Excel总览统计日为10/15，日报包含10/16；旧当前价格不是10/15数据库收盘价。例TQQQ参考价70.14而数据库当日收盘73.82，不能拿未来/旧快照价格强行对齐。
- TQQQ的230股、累计净投入9809.23可在对应范围核对；报价不同带来市值及盈亏差异。
- 原表有四位数量与极小残量截断，本版保留精确拆股结果，清仓分类和成本会不同。
- 嘉信GGLL 2026-10微操136.14在真实重算中通过；其他微操配对/分摊存在实质差异，不用一个通过样例替代全表验收。
- 部分总览/图表无保存缓存，无法直接比较；报告明确记录而非伪造一致。
- 大量“交易明细”差异包含标签、来源表现和字段表示，不能把全部DIFF解释为经济损失；同样也不能假定全部只是格式问题。需逐源行分类复核。

### M7：macOS构建与回归

- 新增 desktop_entry.py、scripts/build_macos_app.py/.sh、logging_config.py；更新 app.py、workers.py、启动脚本、版本和锁文件。
- 使用官方 pyside6-deploy/Nuitka4.1.1成功构建，无需改用PyInstaller。资源显式打入schema.sql及metric_help.json，不捆绑真实DB/Excel/Token。
- 输出 dist/Money3.app，arm64。内含Python动态库和Qt；已在工作目录为/private/tmp、移除PYTHONPATH/PYTHONHOME/VIRTUAL_ENV、PATH仅系统目录时运行。
- 原生窗口测试包括合成库首页/图表、真实库持仓、14列CSV导出、关闭及再次启动。真实库测试通过--db调用与选库相同的只读加载路径，未声称自动点击过原生文件选择对话框。
- 本地偏好按源库路径散列隔离；自动测试覆盖关窗重启恢复人员筛选。原生冒烟用--no-settings，避免改动用户现有偏好。
- 当前包未做Developer ID签名/公证，未在另一台Mac或Intel机器验收；不能声称跨机分发无需任何操作。

## 实际测试与性能

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python scripts/benchmark.py --output reports/synthetic_scale.sqlite3 --report reports/benchmark_scale.json
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/benchmark_gui.py --db reports/synthetic_scale.sqlite3 --report reports/benchmark_gui.json
.venv/bin/python scripts/build_macos_app.py
```

最终回归：76项通过，Ruff通过。引擎、数据层、导出和GUI都执行，未测覆盖率百分比，不声称达到90%覆盖率。
合成性能库：100,000券商交易、100,000人员映射、2,000,000行情。建库仅在排他创建的新合成文件中，拒绝覆盖现有路径。
一次实测：源检查0.090秒、200行分页0.103秒、纯后台财务计算8.837秒；GUI首次分页0.421秒、后台完成9.389秒、20ms心跳共420次、最大间隔0.225秒。
性能GUI使用offscreen，原生窗口启动另测；不混称这两种证据，也不保证所有硬件都达到相同耗时。

## 修改范围及尚待解决

新增/主要修改文件：

- domain/analytics.py、domain/models.py、db/repositories/analytics.py、trades.py。
- engine/replay.py、holdings.py、performance.py、dashboard.py、scalping.py、audit.py、cash.py、fx.py。
- services/analytics_service.py、charts_service.py、export_service.py。
- ui/main_window.py、report_page.py、dashboard_page.py、charts_page.py、stock_detail.py、workers.py。
- app.py、logging_config.py、reconcile.py、demo.py、resources/metric_help.json、desktop_entry.py。
- tests/test_replay.py、test_performance.py、test_audit_export.py、test_cash.py、test_analytics_ui.py；更新test_data_layer的币种支持契约。
- scripts/benchmark.py、benchmark_gui.py、build_macos_app.py/.sh、启动Money3.command、pyproject.toml、requirements-lock.txt、.gitignore。
- README、CHANGELOG、IMPLEMENTATION_PLAN、DECISIONS、RECONCILIATION、本验收记录与RELEASE_NOTES。

剩余工作不是继续补造数字：需要对齐旧Excel行情快照/账务范围，决定旧微操缺陷与精确分摊之间的兼容展示方式，并核实小数股残量/划转来源。
数据状态未修复前，相关KPI降级或缺失是有意保护。M6财务等价验收仍未完成，用户应先检查差异报告再作为替代旧表的正式财务账本。
后续标准会计、写回、完整多币种资产及签名分发仍保持关闭，不自动扩展授权范围。
