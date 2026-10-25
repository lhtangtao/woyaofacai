# UI/UX重构 A–C 验收 · 0.3.0 · 2026-10-17

## 完成阶段

按本轮用户《UI/UX全面重构开发指令》先完成 A（盘点与设计）、B（全局框架和交易明细）、C（实际运行与截图优化）。默认浅色，保持原生 PySide6。资产总览大幅重排（D）及其后各业务页的深度重构未包含在本轮。

## 修改文件

- 新增 `src/portfolio/ui/theme.py`、`icons.py`、`sidebar.py`、`components.py`：统一语义颜色、原生矢量图标、分组折叠导航、卡片/状态/可换行条件标签。
- 新增 `workspace.py`、`interactions.py`、`trade_table.py`、`trade_drawer.py`：页面组合、真实访问历史、批量钻取、列选择、轻量标签绘制、侧边详情。
- 修改 `ui/main_window.py`、`finance_table.py`、`report_page.py`、`dashboard_page.py`、`charts_page.py`：接入设计系统、交易与财务加载隔离、快捷键、主题与收益颜色。其他业务页保留既有功能布局，仅统一表格/颜色。
- 修改 `domain/models.py`、`db/repositories/trades.py`：列表专用条件、同快照聚合笔数、账户/人员/费用/关联元信息；原16个交易字段和导出顺序保留。
- 修改 `app.py`、`__init__.py`、`pyproject.toml`、`scripts/build_macos_app.py`：启动验收与0.3.0打包版本。
- 修改 `scripts/benchmark_gui.py`，新增 `scripts/preview_ui.py` 与 `tests/test_trade_interactions.py`；更新原GUI断言以检查新增展示列及财务页面按需加载。
- 文档：本文件、`UI_DESIGN_SYSTEM.md`、`CHANGELOG.md`、README。

## 业务逻辑与数据来源

所有行情、交易及财务结果仍来自选定的SQLite只读快照。人员权益与券商成交始终独立。四张卡统计整个当前查询，而非本页200行；买/卖只按BUY/SELL计数，划转不冒充买卖。待审按关联、解析或批次异常逐交易去重统计，不使用全库问题数替代。

方向、来源文件、关联状态、撤销及参考开关仅作用于原始交易列表，不裁掉持仓成本历史。分页、排序和这些列表条件不触发全量财务重算。财务页面首次进入时计算，相同范围复用已有结果。人员份额没有可靠手续费分摊字段时显示“—”，不把券商整笔费用重复记给每个人。

详情默认基本信息；账务、原始数据分别分页，原始payload默认脱敏。数据库绝对路径移至状态入口的提示和数据源检查页。导航前进/后退恢复实际访问路线、筛选和交易滚动位置，证券钻取保留进入前范围。⌘F定位当前页搜索，⌘R刷新并保留筛选，⌘,打开设置。

只读连接、Decimal/e8、拆股、划转、微操配对及其他财务引擎未改动。真实DB、参考Excel和9个engine源码文件，共11个受保护文件的SHA256、字节数、修改时间均与开工前一致。

## 实际命令与结果

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/python scripts/preview_ui.py --db ../money_2/数据库/money.sqlite3 --output reports/ui-round1
.venv/bin/python scripts/preview_ui.py --db ../money_2/数据库/money.sqlite3 --output reports/ui-round2
.venv/bin/python scripts/preview_ui.py --db ../money_2/数据库/money.sqlite3 --output reports/ui-release
.venv/bin/python -m portfolio.reconcile --db ../money_2/数据库/money.sqlite3 \
  --excel references/个人投资总览.xlsx --as-of 2026-10-15 --person 个人 --report reports/excel_diff_ui.csv
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/benchmark_gui.py \
  --db reports/synthetic_scale.sqlite3 --raw-only --report reports/benchmark_raw_ui.json
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/benchmark_gui.py \
  --db reports/synthetic_scale.sqlite3 --report reports/benchmark_gui_ui.json
.venv/bin/python scripts/build_macos_app.py
```

- 开工基线76项通过；本轮93项通过，原有财务测试完整保留；Ruff通过。
- 新回归覆盖查询全范围统计、两本账隔离、重复待审去重、划转计数、独立审计开关、LIKE字面匹配、未知费用、抽屉脱敏、重置、历史恢复、钻取目标、异步错误隔离及不重复计算。
- 对账PASS 203707 / DIFF 4522 / UNAVAILABLE 9443。**整份对账CSV与0.2.0版本逐字节相同**，不是只比较计数。财务与旧Excel等价验收依然未通过。
- 性能（合成10万交易/200万行情，offscreen）：原始交易首屏1.68秒、最大心跳间隔0.021秒；同时进入完整财务页面时，原始首屏2.62秒、后台完成11.56秒、最大心跳间隔0.237秒。保留实际结果，不将这轮称为整体性能提升；财务缓存/SQL可另行量测优化。
- 原生截图平台为Cocoa。第一轮发现按钮图标异常、导航分组重影、顶部占用过多、长账户名折行。修复透明图标位图、分组绘制与表格单行显示，并移走重复技术说明。
- 后续轮次复核1280×800、1440×900、1920×1080的真实窗口，以及抽屉、高级条件、空态、错误态、深色、折叠导航。首轮1920宽窗口受屏幕可用区限制，高度只有942；后续显式设置验收最小尺寸后，实际达到1920×1080。
- 1280×800的默认交易内容视口从306px增到401px；1440×900为497px；1920×1080为685px。表格行40px、表头42px。高级条件展开时允许内容区缩小，横向列通过滚动和列选择访问。
- 截图、几何尺寸、对账和源保护证据分别位于忽略版本控制的 `reports/ui-*/`、`reports/excel_diff_ui.*`、`reports/ui_refactor_integrity.json`，不随安装包分发。

## 尚待解决

旧Excel对账差异仍待按既有决策核验。本轮未重写财务口径，也未实现数据库写回、API同步或标准会计模式；数据同步入口明确标记未开放。深度业务页布局（资产总览、持仓、收益、图表）依文档后续阶段实施。安装包为本机arm64构建，仍未做Developer ID签名、公证和跨机分发验收。

## 下一阶段 D：资产总览

1. 保留18个原指标的名称、公式、日期、完整性与钻取映射；首屏展示8个核心指标，其余10个可展开，自定义排序保存为本机显示偏好。
2. 上方4×2核心卡：总资产、当日收益率、全时期总盈亏、浮动盈亏、本月盈亏、本年盈亏、全时期收益率、持仓胜率；缺数据继续显示—并解释原因。
3. 中部资产/收益趋势占2/3，渠道/分类分布占1/3；底部持仓摘要和收益贡献排名；日历与季度矩阵独立折叠区域，避免整页图表堆叠。
4. 图表与表格共用当前人员、账户、币种和日期，复用本轮导航与钻取恢复机制。仍不更改财务计算或两本账边界。
5. 先测试18指标无遗漏/无重复、切换与钻取，再在三种尺寸做实际截图；最终仍对源文件指纹及完整Excel差异CSV做回归。

## 安装包回归

最终 `dist/Money3.app` 版本为0.3.0；构建后进行本地ad-hoc签名并通过 `codesign --verify --deep --strict`。签名不等同于Developer ID或公证。

从 `/private/tmp` 执行包内可执行文件，清除PYTHONPATH、PYTHONHOME、VIRTUAL_ENV、QT_QPA_PLATFORM，PATH只保留系统目录：原生启动交易页与原有资产总览页成功，原有14列持仓CSV导出成功。再次验证签名后启动成功。安装包内未包含真实DB、Excel、CSV或密钥文件。

独立包验收使用 `--demo --no-settings --smoke-ms 3500 --smoke-page 0/2`；财务页额外 `--smoke-export` 到新文件。证据保存在 `reports/bundle_ui_validation.json` 和 `reports/bundle-*-ui.png`。

## 后续小调整：交易页去除重复账户展示

完成内容：按用户确认的“一渠道一个券商账户”，去掉交易列表的券商账户列及列选择入口、右侧基本信息中的重复账户字段；筛选改为“全部渠道”，下拉仅显示渠道，单选时显示渠道名称。渠道列适当加宽。

修改文件：`ui/trade_table.py`、`ui/trade_drawer.py`、`ui/filters.py`，以及本验收记录、README、CHANGELOG。仅调整显示，查询仍按原account_id过滤，原16列导出和财务计算保持不变，未修改数据库。

验证命令：`ruff check src tests scripts`、`python -m pytest -q`、`python scripts/build_macos_app.py`，并对更新的独立包执行原生交易页启动/截图。沿用已有回归验证，未为纯展示修改新增测试。

待解决：本次无新增财务问题；此前Excel差异仍保留。下一阶段仍为资产总览D。

本次实际结果：93项测试通过，Ruff通过；安装包重建、本地签名及原生启动均成功，截图 `reports/channel-only-ui.png` 已确认表格仅保留渠道、筛选显示“全部渠道”。

## 后续交互：Ticker 输入匹配

完成内容：证券筛选改为可输入框，按Ticker前缀匹配，忽略大小写；候选显示Ticker和市场，最多显示8行并可滚动。支持鼠标和键盘选择，选定后按原instrument_id过滤；清空恢复全部股票。无匹配或未确认的输入不会新建证券、冒充已选条件或丢失原筛选。候选只来自当前源数据库，未额外调用行情API。

修改文件：`ui/filters.py`、`workspace.py`、`theme.py`；新增自动补全的真实输入/候选/鼠标选择/键盘确认/清空/未知代码回归到既有 `tests/test_trade_interactions.py`。数据库及财务公式均未修改。

执行验证：`ruff check src tests scripts`通过，`python -m pytest -q`全部95项通过；原生Cocoa运行检查输入aa的候选列表与点选筛选，截图保存到 `reports/ticker-*-ui.png`；随后重建 `.app` 并验证独立启动。

待解决及下一步：本次无新增财务问题，既有Excel差异和后续资产总览D计划不变。

Ticker更新安装包：重建、本地签名校验及脱离开发环境的Cocoa启动成功；截图 reports/ticker-bundle-ui.png。

## 后续交互：持仓报表自适应列宽

完成内容：取消每列155px的固定宽度，新增ReportTable按Qt实际单元格尺寸和窗口可用宽度分配列宽，减少留白、长表头换行；报表缩放、分页、排序或显示列变化后重新适配。保留所有14个持仓字段及完整导出，财务数值和SQL未修改。较长精确数量可通过原值提示、明细和复制/导出查看。

修改文件：新增 `ui/report_table.py`；修改 `ui/report_page.py` 接入表格、表头提示及布局刷新。README与CHANGELOG同步记录。

执行命令及结果：`ruff check src tests scripts`通过，`python -m pytest -q`全部95项通过。Cocoa实际加载真实库、全部人员29条持仓，在1280×800、1440×900、1920×1080分别检查14列和滚动范围：1440/1920宽度下横向滚动最大值均为0；1280窄窗口、全量较长数据仍需要129px横向范围，避免过度压缩金额。截图和几何记录位于 `reports/holdings-fit-*.png`、`reports/holdings-fit-geometry.json`。

同步重建安装包并进行独立启动验证。未新增财务问题；此前Excel差异与下一阶段D计划不变。

自适应列宽安装包验收：脱离虚拟环境原生打开持仓页面成功，截图及14列CSV导出均已验证。

## 自动待核对检查范围：从2026-07-01起

完成内容：按用户要求，将Money3自动质量检查的展示/计数范围设为2026-07-01（含）至所选截止日期；首页错误/待核对、异常中心和可计算指标的待核对标记使用同一最终检查结果。界面标明起始日。

修改文件：新增 `domain/audit_period.py`，修改 `db/repositories/analytics.py`、`engine/audit.py`、`services/analytics_service.py`、`ui/main_window.py`；新增 `tests/test_audit_period.py`，在两项历史案例测试中显式指定不限制审核日期以保留既有检查规则回归。README和CHANGELOG同步说明。

日期规则：有业务日期的告警按业务日期过滤，不能用导入时间把旧记录算作新记录；没有直接日期时追溯交易、行情、历史基准或原始字段中的可靠日期。仍无法确定日期的来源以INFO说明，保留原始数据但不猜测归期。当前报告日的持仓/账本一致性、现金期初等必要前提仍按当前状态核验。因此“只检查7月起”不意味着当前问题全部归零。

数据和计算边界：不删除、不更新源数据库。成本回放仍使用7月以前的买入、拆股、划转和基准；缺失值不会因为历史告警不计数而自动填零。原始交易页的“待审核”仍属于源记录状态，与本次自动检查的“待核对”口径不同。

验证：Ruff通过，101项测试通过，新增覆盖6月30日/7月1日边界、未来日期、月份告警、来源日期追溯、无日期原始行、跨期成本保留、警告与数值分离、源库不变。真实库截至2026-10-16全部人员：错误38→12、WARN待核对970→1；所有18指标的数值与日期及27份非异常报表的逐行值摘要完全相同，源库SHA256、大小和修改时间保持不变。证据 `reports/audit_july_before.json`、`reports/audit_july_validation.json`。

未解决：期间内及当前状态的真实问题继续保留；此前Excel差异没有在本次被修改或消除。下一步仍为后续资产总览D，或按异常中心逐项核验当前问题。

7月起检查安装包验收：重建及本地签名校验通过，脱离开发环境只读打开真实库异常中心成功；原生截图 reports/audit-july-bundle.png。

## 后续功能：每笔操作后的持仓数量及关联说明

完成内容：交易页默认在成交数量旁显示“后持仓数量”，右侧基本信息增加“操作后持仓（股）”；本页CSV原16字段中的后持仓数量同步填入回放结果。关联状态的表头、单元格和账务详情提供解释；状态仅表示人员操作与券商成交的对应关系，不等于全部数据核验通过。

修改文件：新增 `services/post_trade_positions.py`、`domain/trade_labels.py`；修改 `services/trades_service.py`、`ui/trade_table.py`、`ui/trade_drawer.py`；新增 `tests/test_post_trade_positions.py`，更新原导出/查询测试中“后持仓尚为空”的旧断言；README与CHANGELOG记录。

计算口径与来源：在原始交易同一个只读数据库快照中，复用既有replay，对本页涉及的人员/券商账户/证券读取完整此前历史，不让搜索、方向、起始日、排序或分页截断期初。人员份额和券商真实成交分别回放，包含拆股、划转与OPENING基准；未来成交证据不影响较早时点。历史未关联记录仍参与有效人员账本。撤销、参考、开账以前无可重建状态或成交粒度冲突显示“—”并注明原因；负数数量和归属待审提供额外提示。数值为该笔操作当时的持仓，非用当前持仓倒填。

验证：Ruff通过，110项测试通过。新增覆盖人员/真实账本分离、跨日及分页历史、方向/搜索条件、拆股时点、人员划转、OPENING、撤销/参考、历史未关联、成交冲突、未来证据及源库不变。真实“个人/INTW”34条操作的后持仓与既有全量回放逐条一致，最后卖出74股后为0股；源库SHA256不变。原生1920×1080窗口打开详情后，表格横向滚动范围仍为0，截图 `reports/post-trade-quantity-ui.png`。计算证据 `reports/post-trade-quantity-validation.json`。

本次不改变其他收益/成本公式和7月起质量检查范围；仍有历史数据问题需要在异常中心核验。下一阶段D计划不变。

安装包验收：重建应用及本地签名校验通过；脱离虚拟环境启动打包应用成功，交易页显示后持仓数量，示例买入6股后为6、卖出4股后为2。截图 `reports/post-quantity-bundle.png`。
# 后续功能：交易后的净投入成本价

打包验收：应用重建、本地签名验证及脱离虚拟环境的原生启动均成功，交易页新成本列显示正常，截图 `reports/post-cost-bundle.png`。

完成阶段：在每笔交易后显示成本价，采用用户明确指定的净投入口径，复用现有历史回放。买入10股×100、卖出5股×30后，净投入850、余股5、成本170/股。清仓的每股成本显示—；负成本不清零，详情保留净投入。沿用来源账本的计算金额，不额外猜测或重复分配未知手续费。成本字段的CSV精度保持Decimal原值。

修改文件：`services/post_trade_positions.py`填充原字段10、11及说明；`ui/trade_table.py`显示操作后成本并提供公式提示；`ui/trade_drawer.py`增加成本/净投入；`tests/test_post_trade_positions.py`、`tests/test_data_layer.py`、`tests/test_gui.py`及README/CHANGELOG更新。

执行命令：`ruff check src tests scripts`、`QT_QPA_PLATFORM=offscreen python -m pytest -q`；只读真实库比对脚本；原生macOS窗口截图检查；`python scripts/build_macos_app.py`重建应用。

验证：114项自动测试通过，Ruff通过。新增用户170/股案例、按人员与券商独立核算、过滤仍保留先前买入、负成本、清仓、缺失划转金额；原有拆股与OPENING案例补验操作后成本，冲突记录不猜值。真实个人/INTW的34条数量、净投入、成本与既有全量回放逐条一致，源库SHA256不变。1920×1080原生窗口含详情时横向滚动范围0，截图 `reports/post-trade-cost-ui.png`；计算证据 `reports/post-trade-cost-validation.json`。

尚待解决：来源缺失或归属待审核仍需要核验；此前Excel差异维持原记录，本次不改变收益计算口径。下一步按用户后续需求或阶段D计划推进。


## 交易页移除关联状态展示

完成：按用户要求移除交易表格的关联状态列及列选择入口，同时移除右侧账务详情中的关联状态字段。保留底层关联信息与数据核验逻辑，不改数据库或财务计算。

修改文件：`ui/trade_table.py`、`ui/trade_drawer.py`及README/CHANGELOG/本验收文档。

验证命令：`ruff check src tests scripts`、`QT_QPA_PLATFORM=offscreen python -m pytest -q`、`python scripts/build_macos_app.py`。不新增仅验证隐藏字段的实现镜像测试，执行既有回归与打包应用启动检查。

尚待解决：既有数据核验问题与Excel对账差异保持原记录。下一步按后续用户需求推进。

移除关联状态验收结果：Ruff通过，114项测试全部通过；同步更新`tests/test_gui.py`原有列数断言（20→19）。应用重建、签名校验、独立原生启动通过，交易页截图 `reports/trades-no-link-status.png`，成本与后持仓列仍正常显示。

## 金额和股数统一两位小数

完成：当前持仓及其他报表的数量不再展示冗长精度；交易页成交数量/后持仓、交易详情中的金额/成本/股数同步显示两位。百分比维持两位百分数，ID/日期/笔数不受影响。只调整展示，Decimal计算、精确原值核查与CSV导出保留完整精度。

修改：`ui/report_page.py`、`ui/trade_table.py`、`ui/finance_table.py`、`ui/trade_drawer.py`和README/CHANGELOG。

验证：Ruff通过、既有114项测试全部通过；直接模型检查89.999999999显示90.00、15.69显示15.69、0.123456比例显示12.35%，原始Decimal值不变。命令 `ruff check src tests scripts`、`QT_QPA_PLATFORM=offscreen python -m pytest -q`；执行macOS应用重建及原生启动检查。

遗留：原数据问题及既有Excel差异保持原记录；下一步按用户需求继续。

两位小数打包验收：本地签名校验及独立原生启动通过；持仓页截图 `reports/two-decimal-holdings.png`。

## 月份下拉列表截断修复

完成：月份选择器最小宽160、列表最小宽180，禁用文字省略，采用Qt列表弹出并限制可见12项、其余滚动选择，避免原生菜单的窄宽度裁切年月。

修改：`ui/dashboard_page.py`、README、CHANGELOG及本记录。仅界面尺寸和弹出方式调整，数据库与计算不变。

验证命令：`ruff check src tests scripts`、`QT_QPA_PLATFORM=offscreen python -m pytest -q`、`python scripts/build_macos_app.py`。Ruff通过、114项测试通过。原生窗口打开24个月份检查：输入框宽160，列表可用宽170，年月文字完整且有滚动条，截图 `reports/month-popup-fit.png`。

遗留：无本次新增计算问题，原核验事项保持原记录；下一步按用户后续需求推进。

月份修复安装包验收：应用重建、本地签名校验及脱离虚拟环境的原生启动均通过。

## 资产总览补齐全年月度盈亏

完成阶段：在每日收益日历下增加全年1—12月盈亏（两行六列）和年度合计，随日历所选年份切换，默认最新收益年份。采用相同人员/渠道/证券筛选结果；正负收益颜色区分、两位小数，年度标签明确年份和币种。

修改文件：`engine/dashboard.py`新增纯函数monthly_gains复用每日收益values[19]、`ui/dashboard_page.py`添加月度矩阵；新增`tests/test_monthly_overview.py`，更新README/CHANGELOG。

计算口径：月度为该月所有每日持仓收益行严格求和，年度为该年已有收益行严格求和，与本年盈亏一致。无收益行月份显示—，零收益明确0.00；任一行缺值，该月与年度合计均不可确认，不展示部分金额冒充完整值。未来月份没有数据时不补零，保持当前已有历史/估值完整性限制。

执行命令与验证：`ruff check src tests scripts`通过，`QT_QPA_PLATFORM=offscreen python -m pytest -q`共117项通过。新增覆盖同日多人员求和、跨年排除、无数据/真零区分、缺失值传播、人员筛选下年度合计与本年KPI一致。使用只读真实库原生打开资产总览验证12个单元格和既有本年盈亏一致，截图 `reports/annual-months-ui.png`。执行`python scripts/build_macos_app.py`重建并检查本地应用。

尚待解决：原始收益日缺失仍按现有核验处理，本次不补造数据，不改变原数据库或既有财务公式。下一步按用户后续需求推进。

全年月度盈亏安装包验收：重建、本地签名校验、独立原生启动通过；真实库月度矩阵无需横向滚动。

## 移除重复账户资产视图

完成：每个渠道只有一个账户，券商资产入口改为既有渠道汇总；资产页选择器、证券详情选择器移除账户资产，减少重复账户标识。底层账户账本/数据库和原财务报表结果保留，不改变计算。

修改：`ui/sidebar.py`、`ui/workspace.py`、`ui/stock_detail.py`及README/CHANGELOG/本验收记录。

验证命令：`ruff check src tests scripts`、`QT_QPA_PLATFORM=offscreen python -m pytest -q`、直接Qt路由检查、`python scripts/build_macos_app.py`。检查券商路由展示渠道汇总、两个选择器均无账户资产。

遗留：原财务核验问题保持；后续按用户需求继续。

账户视图精简验收结果：Ruff通过、117项测试通过；Qt实际路由与两个选择器检查通过，安装包重建/签名校验/原生独立启动通过。

## 默认数据库与启动自动连接

完成：将用户指定数据库保存到本机QSettings的default_database；应用无显式数据源参数时自动只读连接。成功选择其他真实数据源后记住新路径；演示库不保存为默认；--no-settings不读写配置。路径不写死进源码，找不到默认库时仍走原有明确错误提示，不创建空数据库。

修改：`app.py`、`ui/main_window.py`及README/CHANGELOG。本机偏好设置保存用户授权路径，不修改源库。

验证命令：只读校验真实库结构及QSettings保存/读取检查、`ruff check src tests scripts`、`QT_QPA_PLATFORM=offscreen python -m pytest -q`、`python scripts/build_macos_app.py`。

遗留：其他电脑需选择一次本机数据源；下一步按用户需求继续。

默认库验收结果：Ruff与117项测试通过，本地签名校验通过；打包应用不传--db启动自动连接真实money.sqlite3并显示交易，截图 `reports/default-database-startup.png`。
# 用户授权的负数持仓容差

安装包验收：应用重建、本地签名校验及独立原生启动通过。

完成：用户明确要求忽略0.5以内误差，回放余额在[-0.5,0)股范围内归零，交易、开账和拆股状态统一处理。超过0.5股的缺口保留异常；正常正数零股、原始交易数量和现金金额保持原值。数据库只读，不改写源记录。

修改：`engine/replay.py`、`tests/test_replay.py`以及文档。

验证：Ruff通过，pytest共121项通过。新增微量负数、0.49、0.5及0.50000001边界和正数零股/现金不变检查。真实个人账本重算：ORCX尾差不再阻断收益，AAOX的真实负仓仍保留；源库SHA256不变。剩余缺失收益行按月为4月2、6月10、7月2、8月13、10月4条。执行macOS应用重建及独立启动验证。

未解决：剩余真实负仓及缺失数据仍可能使月/年指标未完整；不宣称Excel完全对账。下一步按用户需求核查剩余来源问题。

# AAOX单笔超卖按零处理

最终验收：Ruff通过、122项测试通过，应用重建和本地签名校验通过；打包应用连接默认真实数据库原生启动通过。

完成：按用户明确要求，单独处理个人/长桥/AAOX的2026-10-05 14:37:00卖出99股后-9股为0，后续持仓从0延续，10月7日卖出后正好为0。原成交数量、现金和源库不变。例外以INFO保留来源，其他真实负仓继续核验。

修改文件：新增`engine/quantity_adjustments.py`，`engine/replay.py`接入，`tests/test_replay.py`覆盖例外及其他人员/券商/证券/时间不触发，更新文档。

执行验证：Ruff、pytest回归；真实个人截至2026-12-02只读重算，确认两笔卖出后持仓0、10月缺失日收益行全部恢复，源库SHA256不变。执行`python scripts/build_macos_app.py`重建。

遗留：其他月份缺失数据仍令本年盈亏未完整；本次不伪造全年值。数量修正延续后，后续AAOX持仓比原回放多9股，估值及每股净投入成本相应重算。下一步按用户要求核验其他数据。

# 2026-10-18：AAOX恢复正常回放

最终结果：Ruff和122项测试通过；最新真实库AAOX无负仓异常，无临时覆盖记录，源库SHA256不变。应用重建、签名验证和默认库独立启动均通过。

完成：用户确认数据已修正并对齐，删除AAOX单笔归零例外，恢复按数据库有效交易正常回放。保留0.5股负数尾差规则，不增加自动补股。

修改文件：删除`engine/quantity_adjustments.py`，移除`engine/replay.py`调用，更新`tests/test_replay.py`为源数据修正前后回放测试；更新文档。此前记录中的AAOX归零说明为已撤销历史决定。

验证命令：`ruff check src tests scripts`、`QT_QPA_PLATFORM=offscreen python -m pytest -q`、只读真实库检查无USER_QUANTITY_OVERRIDE且源库SHA256不变，重建应用并验证独立启动。

尚待解决：其他原有数据问题保持既有检查规则，下一步按用户需求继续。

# 期间收益缺值提示与本年原因核查

验收结果：Ruff和124项测试通过；真实库界面确认AAOX去年无收益记录、个人全年缺失提示包含MUU/WDCX等原因。应用重建、签名校验和连接默认库独立原生启动通过。

完成：KPI无历史收益记录时明确“该期间无收益记录”，取消该卡片无意义待核对后缀；有记录但收益行缺失则保留“数据未完整”，悬停提供渠道/股票/受影响日期范围/记录数。分母条件不满足单独提示暂不适用。数值计算不变。

修改：`engine/dashboard.py`新增metric_gap解释函数、`ui/dashboard_page.py`接入提示、`tests/test_monthly_overview.py`增加跨年无记录与部分缺失区分测试，更新文档。

真实个人截至2026-12-02：2026年缺失14条日收益，分别BBAE MUU 4月20—21日2条、长桥SNXX 6月4—5日2条、长桥GGLL 6月11—15日3条、BBAE SNXX 6月11—26日5条、长桥WDCX 7月7—8日2条。触发原因均为真实负仓及后续期初市值不可确认。正常10月不代表全年完整，7月起自动检查范围不截断全年计算历史。

验证命令：Ruff、pytest、只读真实数据Qt卡片检查，macOS打包和独立启动验证。其他数据问题不在本次被擅自更改；下一步按用户需求核对原始流水。


## 指标卡片精简及悬停修复

最终验收：鼠标位于卡片内时原生悬停弹窗实际可见，截图确认完整列出缺失原因；安装包重建、签名校验及默认库独立启动通过。

完成：移除18张指标卡日期后重复的待核对字样；保留数据未完整等数值可用性提示。新增MetricCard封装350ms悬停触发和ToolTip事件显式展示，启用AlwaysShowToolTips，说明以440px宽富文本自动换行，停留最长30秒，移开隐藏。数据核验与财务计算不变。

修改文件：新增`ui/metric_card.py`，修改`ui/dashboard_page.py`及README/CHANGELOG。

验证：Ruff通过、124项既有回归测试通过，原生macOS真实库触发ToolTip事件检查弹窗包含MUU/WDCX等本年缺失原因且所有卡片无待核对文字，截图`reports/metric-gap-hover.png`。执行应用重建、签名及独立启动检查。

遗留：原始数据异常继续在异常中心和相关悬停说明中保留；下一步按用户需求处理。
# Money2负仓估值口径迁移

安装包验收：重建、签名校验及默认真实库独立原生启动通过，截图`reports/money2-debt-valuation-bundle.png`。

完成：按用户明确要求兼容Money2主流程`src/build_investment_overviews.py`的compute_daily_holdings_and_profit：负仓估值0，交易金额完整计入，后续买入先抵欠股。仅改变Money3每日/月度业绩计算，实际回放数量不归零、不更改源数据库、保留0.5股尾差规则及真实负仓核验。

修改文件：`engine/performance.py`调整负仓市值/收益计算、跳过无交易的零估值欠股日；每日表期初/期末数量展示非负，元信息保存原始带符号数量和明确估值口径。`tests/test_performance.py`增加超卖后回补、跨月欠股抵扣、缺金额仍不可计算测试。README/CHANGELOG/DECISIONS同步更新。

命令与结果：Ruff通过，`QT_QPA_PLATFORM=offscreen python -m pytest -q`127项通过。只读真实库个人截至2026-12-02：2026年缺失每日收益14→0，本年盈亏-10972.64450000000020920美元、本年收益率-0.1311507455…，本月盈亏-15541.7267…；源库SHA256不变。证据`reports/money2-debt-valuation-validation.json`。执行应用重建、签名与原生独立启动。

遗留：仍保留源数据实际负仓告警；缺失价格/金额和成交冲突仍不猜值。与Money2采用同一负仓估值原则，不宣称所有历史Excel数据完全对账。下一步按用户需求核查其他差异。

# 全年月度盈亏年度选择

安装包验收：重建、本地签名校验与默认库独立原生启动通过。真实个人2025年度合计为+9249.28美元，与既有汇总一致。

完成：每月盈亏矩阵旁新增独立年份选择器，从当前筛选收益数据提取可用年份，初始选择最新年度；可切换2025/2026，刷新保留仍可用的年份。该选择不改变每日历月份，筛选后已无所选年份时回到最新可用年份。仍用既有月收益汇总公式，不修改数据库。

修改：`ui/dashboard_page.py`及README/CHANGELOG/本记录。

验证：Ruff通过、127项既有测试通过；只读真实个人库在macOS原生窗口检查2025/2026可选、切到2025后年度合计与monthly_gains一致、每日历月份不变、刷新保留2025。截图`reports/annual-year-picker.png`。执行macOS打包、签名和独立启动验证。

遗留：无新增计算问题，历史对账问题不在本次修改。后续按用户需求继续。

# 并排日历与每季度一行

安装包结果：重建、本地签名校验、默认真实库独立原生启动通过。

完成：按用户要求，每日收益日历在左、全年月度收益在右，同一行展示；全年改为四行三列，每行一个季度，季度标签显示一至四季度。保留年度选择、年度合计及每日历月份选择。两个表格等高336px并顶端对齐，每日历列宽随容器伸缩。

修改：`ui/dashboard_page.py`和README/CHANGELOG；只改布局和月份单元格索引，不改变收益计算、数据库或导出数据。

执行与验证：Ruff通过，pytest127项通过；真实库macOS原生窗口确认12个月的四行三列位置正确、两个表格顶端y坐标一致、年份切换及年度合计仍正确。截图`reports/quarterly-calendar-layout.png`。执行macOS重建、签名及独立启动检查。

遗留：其他既有核对事项保持，下一步按用户需求继续优化。

# 季度矩阵右侧增加季度合计

安装包验收：重建、本地签名校验及默认库独立原生启动通过。

完成：全年月度盈亏矩阵由四行三列变为四行四列，最右为季度合计。按所选年度/季度的每日收益严格求和，不把缺失行补零；尚未结束季度显示截止收益日期。金额两位、红绿区分，年份切换沿用原筛选，数据库不变。

修改文件：`engine/dashboard.py`新增quarterly_gains，`ui/dashboard_page.py`接入，`tests/test_monthly_overview.py`覆盖季度边界/跨年排除/缺失传播/未满季度汇总，更新README/CHANGELOG。

执行验证：Ruff通过、pytest128项通过。真实库原生窗口四季度单元格均存在且12个月布局保留，截图`reports/quarterly-totals-layout.png`；2026年Q1 -9400.93、Q2 +35114.87、Q3 -21144.86、Q4截至10-16 -15541.73美元（仅展示层四舍五入）。执行macOS应用打包及独立启动验证。

遗留：既有数据质量/对账事项保留；下一步按用户需求继续。

# 全部日期与日历选择修复

最终验收：128项测试通过，本地签名和独立原生启动通过；默认数据源实际显示2025-01-01至2026-10-18，截图`reports/default-date-range-bundle.png`。原生日历最大日期确认为今天。

完成：按用户要求，未限制起始日的默认范围显示2025-01-01至当前日期，清空筛选也恢复该范围；保存的未来截止日不再恢复为默认，日期控件上限为今天。仍用完整历史回放成本，实际历史估值日期独立显示。自选历史区间仍可用。

新增DatePicker让日期文字和图标区域点击均触发Qt原生日历按钮；日历图标可见，弹出前同步起止范围。修复全局QTableView单元格padding导致两位数日期被裁切，日历最小320×290、单元格padding0。

修改：`ui/date_picker.py`、`ui/workspace.py`、`ui/interactions.py`、`ui/main_window.py`及README/CHANGELOG。

验证命令：Ruff、pytest128项通过；原生macOS日期筛选菜单内点击截止日期文字，确认calendarWidget可见、范围2025-01-01至系统今天，截图`reports/date-calendar-popup.png`确认日期数字和星期完整显示。重建应用并进行签名及独立启动验证。

遗留：历史估值仍以数据库实际行情日为准；本次不改源库或财务公式。下一步按用户需求继续。

# 图表文字与数字间距优化

安装包验收：应用重建、本地签名校验及图表页独立原生启动通过。

完成：图表字体使用系统GeneralFont、像素字号、AbsoluteSpacing=0、WordSpacing=0、Stretch=100及字距微调kerning；对齐参数明确，去掉图例通过多个空格分隔数字的写法。圆环图名称/金额/占比分列，金额和百分比右对齐，百分比两位小数；标题17px、图例13px、注释11px。

修改：`ui/charts_page.py`及README/CHANGELOG。仅呈现变化，不改金融数据、数据库、筛选和图表下钻。

验证：Ruff通过、128项测试通过（包含27张图逐张渲染及PNG导出检查）。macOS原生渲染拉科持仓分布，截图`reports/chart-compact-numbers.png`，确认1,023.15和28.95%等数字正常字距。执行打包/签名及独立启动验证。

遗留：无新增财务问题，下一步按用户要求继续优化。

# 长桥实时行情与10秒刷新

真实源保护追加验收：真实库计算后应用休市估值覆盖层，源库SHA256前后相同；历史metrics/reports完整repr保持一致，所有休市行明确显示参考价，新鲜报价计数0。最终安装包实际确认长桥休市状态。

完成阶段：实时行情订阅、市场时段和停市策略、隔离估值、GUI设置/状态及SDK打包接入完成。

修改文件：新增`domain/live_quotes.py`（报价及US时段）、`services/live_quotes.py`（凭证安全读取/SDK线程/日历/订阅/缓存/重试）、`engine/live_valuation.py`（Decimal覆盖层）、`ui/live_quotes.py`（10秒GUI刷新和配置）；修改`ui/workspace.py`、`ui/main_window.py`、`ui/interactions.py`、`ui/report_page.py`接入生命周期、路由及实时列；`pyproject.toml`增加longbridge==5.2.1，`scripts/build_macos_app.py`包含SDK；新增`tests/test_live_quotes.py`、无密钥`longbridge_quotes.example.json`，更新.gitignore及文档。

数据来源与逻辑：仅QuoteContext，无TradeContext和任何下单能力。长桥交易日历只读获取，盘前04:00—09:30、盘中09:30—16:00、盘后16:00—20:00、夜盘20:00—次日04:00按纽约时间判定；半日市使用13:00收盘/17:00盘后结束，下一日必须为实际交易日才启用夜盘。SDK enable_overnight开启。休市取消订阅和报价请求，只保留本地状态判断。新订阅或切换时段拉一次快照，后续价格通过SDK推送更新，UI每10秒读取缓存。

“实时持仓”保留原14字段并追加时段/报价时间/状态，默认隐藏部分累计/比例字段以避免过宽，右键可恢复，导出仍含全部17字段。实时资产/浮盈摘要单独显示；正式日历和年度计算不重跑。无新报价使用标记的数据库参考价；不将上个交易时段的价格冒充本时段实时价，旧报价标记明确。历史截止日、演示和非USD模式不连接实时估值。

执行命令：`pip install longbridge==5.2.1`、`ruff check src tests scripts`、`QT_QPA_PLATFORM=offscreen python -m pytest -q`、真实SDK只读连接与trading_days验证、原生Qt界面检查、`python scripts/build_macos_app.py`和打包应用15秒独立运行。

验证结果：Ruff通过、148项测试通过（新增20项用例覆盖交易时段边界、周末/假日/半日市、夏令时、周日晚夜盘、时段过期及旧报价拒绝、无重复轮询、休市退订、凭证不执行Python、错误不泄密、推送乱序、Decimal估值和历史对象/源库不变、GUI定时器不调用历史计算）。现有本机Money2凭证读取成功、长桥连接和US交易日历成功，返回2026-10-19等未来实际交易日；当前确认CLOSED，未发送quote或subscribe。原生界面和最终安装包均显示“美股休市·已暂停行情订阅和查询”、参考价及10秒更新状态。证据`reports/live-quotes-closed-ui.png`、`reports/live-holdings-closed-ui.png`、`reports/live-quotes-bundle.png`。

安装包结果：官方SDK已包含在Money3.app中，本地签名验证通过；脱离虚拟环境连接默认数据库及现有长桥配置，15秒运行正常并读取到交易日历。源数据库未修改。

尚待解决/实际限制：当前休市，不能声称已实测真实开市推送或每只证券的夜盘权限；各交易时段及推送处理已用可控测试覆盖，首次开市后需观察实际行情时间/权限。仅美股实时估值；不支持夜盘的股票保持参考价。历史核对事项不因行情接入自动消除。下一步按真实开市返回数据核验长桥各时段覆盖。


# 累计本金与总资产变化组合图

完成阶段：将图18由四条折线改为Money2/参考图对应的堆叠柱＋总资产蓝线；本金基础浅蓝、资产高于本金绿色、资产低于本金红色。顶部图例、完整紧凑月份标签（窄窗口自适应间隔）、金额轴刻度按堆叠总高确定；保留月度点击和精确值悬浮。

修改文件：src/portfolio/services/charts_service.py、src/portfolio/ui/charts_page.py、tests/test_capital_chart.py及README/CHANGELOG/本验收文档。

业务逻辑与数据来源：沿用只读数据库的月末资产和累计净投入；本金基柱min(本金,资产)，绿色max(资产-本金,0)，红色max(本金-资产,0)，总资产独立折线。Decimal计算及缺失值不补零保持不变；真正缺失资产点折线断开。

执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；Cocoa真实库个人图18渲染截图reports/capital-assets-stacked.png。

测试结果：150项通过；新增测试验证基柱与盈利/亏损段首尾相接、同轴堆叠、像素颜色和缺失点不绘制；现有27张图导出回归通过。原生截图确认历史月份、盈亏颜色及蓝线正确。

尚待解决：原有Excel全面对账差异未因呈现变化而消除；不修改源数据库。下一步按用户反馈调整。

安装包验证：Money3.app重建及本地签名验证通过，脱离虚拟环境独立启动图表页并保存截图成功。最终回归仍为150项通过。

# 排行榜控件显示范围

完成：仅图15/16/17显示排行榜分页，其余24张图和无数据/清空状态隐藏。修改ui/charts_page.py及README/CHANGELOG/本验收文档；仅界面显示条件，源库与财务计算不变。验证：Ruff通过、150项测试通过，另用Qt遍历27张图逐张断言控件显示条件及clear隐藏行为。执行ruff check src tests scripts、QT_QPA_PLATFORM=offscreen python -m pytest -q，并重建Money3.app。无新增财务风险，既有对账事项不变；下一步按反馈继续。

# 月度报表年月筛选

完成：每月持仓收益、每月微操收益、月度净投入明细使用年份和12个月份面板，范围只显示年月；每日页恢复原日历。

修改：ui/date_picker.py、ui/interactions.py、ui/workspace.py、ui/main_window.py、tests/test_month_picker.py及README/CHANGELOG/本文档。

逻辑：起始月首、历史月末、当前月截止今天；页面切换不改写原日期，源库与财务公式不改。

验证命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；Cocoa嵌套菜单检查；python scripts/build_macos_app.py。结果152项通过，覆盖日/月切换、整月边界、2月末、当月截止和未来月份禁选。原生截图reports/month-picker-native.png确认年月面板正常展示。初版重复打开菜单时Qt销毁策略导致崩溃，已改为复用面板，重复打开和完整回归通过。应用已重建并通过本地签名验证。

遗留：旧Excel对账事项不变，无新增源库更改。下一步按用户反馈继续。

# 报表表头下拉排序

完成：点击每列标题下拉选择正序/倒序，标题箭头标出方向；冻结首列同样支持。右键列显隐维持原行为。

修改文件：ui/report_page.py、ui/report_table.py、tests/test_report_sort_menu.py及README/CHANGELOG/本验收文档。

逻辑：对筛选后的全部记录按原始Decimal/整数或文本排序，缺失值最后，再分页；每张报表保存列名和方向，重新加载及实时覆盖层更新后重用。财务数值与源库不改。

验证命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；Cocoa原生菜单验证；python scripts/build_macos_app.py。结果154项通过；新增测试通过实际表头点击/菜单动作验证升降序、精确小数、缺失值、超过200条分页与刷新保留。程序调用sort_column旧默认方向兼容原测试。

原生证据：reports/report-sort-menu-native.png、reports/report-sort-header-native.png。激活测试窗口后菜单可见且倒序选中正确；应用已重建并通过本地签名验证。

遗留：排序选择目前在本次运行内保留，不新增跨重启设置；原Excel对账事项不变。下一步按用户反馈继续。

# 图表月份联动

完成：原月份控件仅联动图21，现图1/2/3按选择月份重绘月末证券/渠道/分类分布；图4/5/6按所选基准月的前一月显示；图21仍按选择月份显示每日收益。其余图不显示月份控件。

修改文件：services/charts_service.py、ui/charts_page.py、tests/test_chart_month_selection.py及README/CHANGELOG/本文档。

数据来源：既有每月持仓收益报表的月末市值列，按月份筛选后用Decimal聚合；最新模式继续读取当前持仓。历史缺失月份不回退当前数据，不补零；最新月标明截至实际收益日。选月不更改源数据库或金融公式。

验证命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；Cocoa个人账本6月快照验证；python scripts/build_macos_app.py。结果156项通过；新增用例覆盖跨月金额变化、证券/渠道/分类三种维度、上月关系、缺失月份、当前月日期标注、控件联动、刷新保留和最新恢复。原生真实库切到6月后series与最新不同且标题为6月，截图reports/chart-month-june-native.png。

遗留：历史原始核对事项不变，无源库更改。下一步按用户反馈继续。

# 图表零刻度线对比度

完成：纵向柱形/折线、横向排行、本金资产组合图统一深色2px零轴与加粗0标签，比例图为0%。普通网格保持浅色，靠近零轴的普通刻度省略以避免重叠；零轴后绘避免被柱体覆盖。

修改文件：ui/charts_page.py及README/CHANGELOG/本文档。仅呈现变化，财务计算、源库及坐标值不变。

验证命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；Cocoa个人账本每月微操图截图；python scripts/build_macos_app.py。156项测试通过（含27张图渲染导出），原生截图reports/chart-zero-baseline-native.png确认正负收益分界明显且0标签可见。

遗留：原始核对事项不变，无新增财务问题。下一步按用户反馈继续。

# 2026-10-19 开市启动无数据/卡住修复

完成阶段：修复真实开市行情下的SDK初始化死锁。修改文件services/live_quotes.py、tests/test_live_quotes.py及README/CHANGELOG/本文档。

故障证据：真实只读库38.4MB，拉科完整账本计算0.33秒；源码版和禁用实时行情的安装包显示结果正常，实际启动脚本开启实时行情后无法响应。原生sample显示GUI线程等待GIL，快照线程在SecurityQuote.last_done的Rust LazyLock初始化Decimal时导入Python，推送线程在PushQuote.last_done等待同一初始化锁并持有GIL，形成死锁。采样保存在/private/tmp/money3-hang-20261019.txt。

实现：初始快照转为先于subscribe；_SDK_VALUES_LOCK将快照和推送的SDK getter串行化，覆盖重建feed的旧回调，Python锁等待释放GIL；quote网络请求在锁外。保留10秒缓存刷新、行情分时段、休市暂停、精确Decimal、历史与实时隔离。源库不写入。

验证命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；python scripts/build_macos_app.py。169项通过；新增回归验证订阅前先读取初始Decimal、两个feed并发访问SDK字段时最大并发为1，金额精度不变。独立安装包和实际启动脚本的开市冷启动验证另附。

尚待解决：历史核对事项保持原状。下一步按用户反馈继续。

安装包验证追加：已重建并通过本地签名检查；用用户实际的启动Money3.command和保存设置连续三次冷启动（资产总览/实时持仓/资产总览），均约7秒正常显示结果、生成截图并退出。真实账本总览18项指标和拉科8条持仓已恢复，证据reports/startup-fixed-live-20261019-1/2/3.png。

真实开市追加验证：新版按实际启动脚本保持实时行情运行18秒，跨过10秒刷新周期，GUI正常关闭、无死锁；状态显示长桥夜盘已订阅7只，账本8条持仓及摘要正常。证据reports/startup-fixed-night-live-20261019.png。截图新鲜报价仍为0/8，当前展示标记的收盘参考价，不能据此声称已验证每只股票的夜盘最新价格覆盖；本次验证确认快照Decimal转换、订阅完成和GUI定时器持续响应。

# 2026-10-19 夜盘报价与实时总览

完成阶段：修复夜盘报价被拒、总览当日收益率仍显示周五收盘指标的问题。修改services/live_quotes.py、engine/live_valuation.py、ui/live_quotes.py、ui/dashboard_page.py、tests/test_live_quotes.py、新增tests/test_live_metrics.py及README/CHANGELOG/本文档。

证据：只读公开证券报价时SDK返回无时区本地时间，例如当前UTC与overnight_quote.timestamp相差本地8小时；原逻辑replace(tzinfo=UTC)导致被未来时间校验拒绝。改用astimezone(UTC)，既有aware时间保持正确，naive时间按系统本地转换。无凭证输出或源库写入。

业务口径：实时持仓按原始数量与有效时段价格估值；总览实时层使用实时市值减上一交易日收盘市值、减当日买入、加当日卖出。若已有当日收益行，使用其期初市值与现金流，覆盖当日全清仓证券；无当日流水的持仓需要最近收盘基准。缺失报价/基准保留缺失。收益率沿用EXCEL_COMPAT分母市值减当日盈亏，夜盘交易日为session.end的纽约日期。期间指标按完整历史KPI加实时增量；已含当日时替换当天，避免重复或被报表起始范围截断。各数值使用Decimal精度50，历史Analytics/日历/月报不修改。卡片明示交易日、时段和覆盖，关闭实时恢复历史卡片。

执行：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；真实公开证券SDK时间检查；Cocoa当前账本18秒夜盘验证；python scripts/build_macos_app.py。测试175项通过，覆盖本地时间转换、夜盘跨周末归属、日盈亏/收益率、今日买卖与清仓现金流、缺失基准、年度范围不截断和当日不重复计入、历史对象不变。

原生证据reports/night-dashboard-live-20261019.png：有效新鲜报价8/8，总览当日收益率及今日盈亏显示10月19日夜盘，页面正常刷新。昨天盈亏调整为最近上一交易日收盘值和对应收盘日期。

遗留：历史日历/月度报表仍是数据库的收盘统计，实时报价不会改写源库；仅USD美股实时估值，个别无夜盘报价证券会明确显示缺失/参考。历史核对事项保持原状。下一步按用户反馈继续。

最终安装包验收：本地签名通过，使用原启动Money3.command运行18秒跨过刷新周期，界面显示长桥夜盘订阅7只、新鲜报价8/8持仓，当日收益率日期10月19日且数值跟随报价变化；上一交易日卡片已正确标为10月16日收盘。正常退出无死锁。证据reports/night-dashboard-bundle-20261019.png；最终回归175项通过。

# 2026-10-19 成交单价列位置

完成：交易明细默认成交单价列（成交均价）移至成交数量前。修改ui/trade_table.py及README/CHANGELOG/本文档。仅表头视觉顺序变化，数据来源、列索引和金额计算不变。验证执行Ruff、Qt默认可见列顺序断言、pytest回归和macOS打包；断言单价紧邻数量之前，其他可见列保持原顺序。无新增财务问题；下一步按用户反馈继续。

列顺序调整验证结果：Ruff通过，175项测试通过，Qt断言确认价格列紧邻数量之前。

# 2026-10-19 股票与人员资产状态筛选

完成阶段：股票汇总增加当前状态筛选；人员资产将券商账户显示替换为渠道，并追加当前状态列及同类筛选。

修改文件：ui/report_page.py、services/analytics_service.py、新增tests/test_stock_status_filter.py和tests/test_person_asset_status.py，及README/CHANGELOG/本文档。

来源与规则：股票汇总使用现有当前状态原值。人员资产保持每人/每账户/每证券的回放分组，用账户broker_code显示短渠道名，账户ID在元信息保留用于穿透。数量原始值=0为已清仓，>0为持有中，<0为负仓异常；粒度冲突显示成交待核对及未知数量，未知状态不当作清仓。净投入不因清仓归零。仅筛选显示，不改数据源或财务计算；完整行先筛选和排序再分页/导出，其他报表不套用状态条件。刷新保留筛选。

执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；Cocoa实际个人账本两页验证；python scripts/build_macos_app.py。结果178项通过。新增用例覆盖状态与文本组合、数值排序、刷新保持、其他报表隔离、500行先筛后分页/导出、渠道与账户ID保留、零/正/负/小数与冲突识别、原始positions不变。

原生验证：股票持有中条件下所有行当前状态匹配；人员资产已清仓条件下所有原始数量=0，表头为渠道，不含券商账户。证据reports/stock-status-holding-native.png及reports/person-channel-closed-native.png。

尚待解决：筛选不跨应用重启保存，旧财务核对事项保持原状，未修改源库。下一步按用户反馈继续。

# 2026-10-19 今日盈亏独立页面

完成阶段：在当前持仓上方新增今日盈亏，三张卡片显示盈亏金额、EXCEL_COMPAT当日收益率、按Ticker去重的上涨/下跌/平盘/缺失数；明细包含渠道、Ticker、数量、昨收、最新价、涨跌幅、盈亏、时段及报价时间，默认盈利倒序，可切换亏损最多/涨幅最大/跌幅最大。金额和数量显示两位小数。

修改文件：新增engine/today_holdings.py、tests/test_today_holdings.py；修改ui/sidebar.py、workspace.py、interactions.py、report_page.py、live_quotes.py，以及README、CHANGELOG和本文档。

业务逻辑与来源：复用只读Analytics、每日持仓收益中的期初市值与当日买入/卖出现金流，行情状态最近收盘价及长桥有效时段缓存报价。盈亏=当前市值－期初市值－当日买入金额＋当日卖出金额；无当日收益行时按持仓数量乘最新价与最近收盘价之差。比例沿用盈亏/(当前市值－盈亏)。未知报价/基准保留缺失，严格汇总不隐式填零。只展示原始数量>0及待确认数量，零及负仓排除；当日已全清仓证券不属于当前持仓，故可能与总览含清仓交易的今日盈亏不同。夜盘按纽约下一交易日，关闭实时恢复历史收盘数据，不修改历史报表或源库。

执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；Cocoa真实账本夜盘页面18秒验证；python scripts/build_macos_app.py。回归181项通过，覆盖当日部分卖出现金流、收益率精度50、缺失报价、历史对象不变、清仓排除、四种排序及刷新保持、其他报表隔离与侧边栏顺序。

原生证据：reports/today-holdings-live-native.png，26条持仓均有有效当日盈亏，三卡、表格及排序正常。历史核对差异仍保留，未据本功能宣称全部历史对账通过。尚待解决：仅USD美股支持实时估值，其他口径使用收盘数据；无有效报价不补假值。下一阶段：按实际使用反馈调整。

打包验收：Money3.app构建及codesign --verify --deep --strict通过；通过启动Money3.command运行12秒，账本及实时行情加载正常并正常退出。启动证据reports/today-holdings-bundle-startup.png；今日盈亏页面另以原生源码界面验证，见上述证据。

# 2026-10-19 渠道盈亏图表、默认首页与资产日期

完成阶段：今日盈亏增加正负渠道柱状图与明显零轴、各渠道当前持仓资产及全部持仓资产汇总；软件默认进入本页。资产管理（总览/今日盈亏/当前持仓/股票汇总/人员资产/券商资产）隐藏日期选项并固定2025-01-01至系统当天，其他页面自选日期独立保留。

修改文件：engine/today_holdings.py、新增ui/channel_overview.py、ui/report_page.py、ui/main_window.py、ui/interactions.py及tests/test_today_holdings.py；既有交易交互测试显式导航交易页，财务页测试更新默认报表断言；README/CHANGELOG/本文档。

业务逻辑与数据来源：渠道盈亏沿用今日持仓现金流公式，按渠道Decimal精度50严格汇总；资产使用live_holdings市值列，包含参考估值并在界面明确不含账户现金。图表和汇总先筛选再聚合，不受分页影响；缺失盈亏或资产保持缺失，不填零。跨时段及非交易时段沿用既有行情策略。源库只读。

执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；Cocoa实际账本18秒刷新截图；python scripts/build_macos_app.py；启动脚本打包验收。原生证据reports/today-holdings-live-native.png。新增测试覆盖渠道严格汇总/参考资产/缺失盈亏、默认首页、图表筛选隔离及固定日期与其他页面日期保留。

尚待解决：本页资产为当前持仓市值，不包含无可信来源的账户现金；历史对账事项保持原状。下一步按用户反馈继续。

最终结果：Ruff通过，184项测试通过（本阶段新增3项）；Money3.app打包及本地签名校验通过。修正原交易测试为显式进入交易页，并在缓存测试中明确使用与资产页相同的统计期间，验证同一财务范围下列表排序不重新计算。原生检查确认日期选项隐藏、渠道汇总刷新及卡片无裁切。打包启动截图reports/today-channel-bundle.png。

# 2026-10-19 渠道图高度与整页滚动

完成阶段：渠道图加高至340px，正负范围自适应零轴与柱高；今日盈亏表格按筛选全部行展开，隐藏分页及内部纵向滚动条，使用外层页面滚动。修改ui/channel_overview.py、ui/report_page.py、tests/test_today_holdings.py及README/CHANGELOG/本文档。

业务逻辑与来源：仅布局和分页展现变化，复用原始Decimal渠道合计，不修改财务算法或源库。其他报表恢复200行分页、内部滚动和自适应高度。执行Ruff、QT_QPA_PLATFORM=offscreen pytest、原生实际账本18秒刷新截图、macOS构建及启动验证。原生截图reports/today-holdings-live-native.png确认柱高明显增加，26条持仓全部展示。新增215行测试确认全部行渲染、外层滚动有效、刷新保留位置及切换其他报表恢复分页。尚待解决：窄窗口若列宽不足仍保留必要的横向滚动；下一步按用户反馈调整。

最终验证：185项测试通过，Ruff通过，Money3.app重建与codesign严格校验通过；实际启动脚本截图reports/today-expanded-bundle.png。金额与数量计算保持原值，仅绘图坐标转换为浮点。

# 2026-10-19 渠道资产横向柱图点击筛选

完成阶段：右侧资产横向柱图及选中变灰/再次取消/直接切换渠道，下面表格及顶部指标联动，渠道图仍保留全部候选渠道和资产总计。修改ui/channel_overview.py、ui/report_page.py、tests/test_today_holdings.py及README/CHANGELOG/本文档。

业务逻辑与数据来源：沿用channel_summary的只读持仓市值和Decimal汇总；先应用上方全局条件与文本搜索，再画全部候选渠道，选中渠道仅进一步筛选表格/指标/导出，避免其他柱消失而无法切换。有效选中项跨10秒刷新保持，搜索或全局筛选不再包含该渠道时自动取消。原财务计算和源库不变。

执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；Cocoa真实账本18秒截图；python scripts/build_macos_app.py及启动脚本验收。186项测试通过，新增真实Qt鼠标点击验证表格联动、全渠道柱保留、选中像素灰色/取消像素蓝色、刷新保持、直接切换及搜索导致选中失效。原生证据reports/today-holdings-live-native.png。

尚待解决：资产继续为持仓市值、不含现金；保留已有参考估值和缺失规则。下一步按用户反馈继续。

原生点击验收：真实26条持仓点击BBAE后仅剩3条，再次点击恢复26条；灰色选中柱截图reports/today-channel-selected-native.png。打包成功、本地严格签名验证通过，实际启动脚本运行12秒并正常退出，截图reports/today-asset-bars-bundle.png。

# 2026-10-19 盈亏柱点击联动

完成阶段：补充左侧盈亏图点击功能，与资产横向柱共享渠道筛选及灰色选中态。修改ui/channel_overview.py、tests/test_today_holdings.py及README/CHANGELOG/本文档。业务逻辑与数据来源不变，仅将盈亏柱及其渠道标签区域加入命中检测，复用既有筛选信号；正负盈亏正常色与选择取消恢复逻辑保持。执行Ruff、offscreen pytest、macOS打包和启动验证。新增Qt鼠标测试覆盖正/负盈亏柱、灰色像素、对应明细、刷新保留、跨图取消后恢复正负颜色及同柱再次点击取消。原历史对账问题与源库保持原样，下一步按用户反馈继续。

最终结果：Ruff通过、187项测试通过；真实账本点击盈亏柱后26条持仓筛为BBAE的3条，两侧BBAE柱同步变灰，再点盈亏柱恢复26条。证据reports/today-pnl-selected-native.png。Money3.app构建及严格签名校验通过，启动脚本验收截图reports/today-pnl-click-bundle.png。

# 2026-10-19 今日盈亏成本价列

完成阶段：在今日盈亏表持仓数量之后、昨收之前显示成本价。修改engine/today_holdings.py、ui/report_page.py、tests/test_today_holdings.py、tests/test_analytics_ui.py及README/CHANGELOG/本文档。

业务逻辑与数据来源：直接读取live_holdings原有持仓成本列（累计净投入除当前持仓数量），不重算或按行情变化修改成本；无有效行情时仍可展示历史成本，缺成本保持缺失。数据模型追加成本列以保持既有盈亏/涨跌幅索引，默认视觉顺序放在数量后；切换其他报表时恢复相关表头位置。原始成本精度与导出值保留、界面两位小数，源库只读。

执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen pytest；原生真实账本查看与渠道点击回归；python scripts/build_macos_app.py及启动脚本。既有测试补充成本值来源、无行情成本仍保留、成本表头位置与两位显示断言。尚待解决：保持既有历史对账事项；下一步按用户反馈继续。

最终验证：Ruff及187项测试通过；实际个人账本显示成本价紧跟数量，BBAE筛选持仓成本为ORCX 25.66、AAOX 49.93、LITE 941.00；原生截图reports/today-pnl-selected-native.png。Money3.app重建、严格签名校验通过，启动脚本验收截图reports/today-cost-bundle.png。

# 2026-10-19 单日历史回看与排行榜排序

完成阶段：今日盈亏增加独立日历日期控件、累计盈亏卡片及累计盈亏列；图15/16/17增加三种排序与动态分页。

修改文件：ui/workspace.py、interactions.py、main_window.py、live_quotes.py、report_page.py、channel_overview.py、charts_page.py；engine/today_holdings.py、services/charts_service.py；新增tests/test_snapshot_and_rank.py及更新相关表头/测试数据，README/CHANGELOG/本文档。

业务逻辑与来源：今日盈亏独立holding_date默认系统今天，scope为2025-01-01至所选日；只读Analytics回放限制交易与行情截止日，其他资产页面仍固定今天，交易页原日期保持独立，跨范围回交易页会刷新旧范围列表。历史日期使用最近有效收盘收益日并在提示中同时注明所选日与有效日；没有记录不当零，非交易日或缺少该日记录不伪装为当天收盘。历史不连接实时报价，回今天恢复既有10秒估值。累计卡片=截至所选日期全部已清仓收益＋当前持仓浮动盈亏，包含已全清仓股票；明细累计盈亏=该账户证券累计清仓收益＋当前持仓浮盈，成本与现有口径不变，使用Decimal精度50。卡片受上方条件/渠道柱选中控制，明细文本搜索不影响全部交易累计口径，已在悬停说明。

排行规则：以盈亏Decimal原值从大到小/从小到大/绝对幅度从大到小，等值按代码确定顺序，缺失始终最后；先全量排序再取24条，页码按总条数生成，排序变更回第一页。图17正负分系列合并为每Ticker一条总贡献，不改原Analytics对象；横向图预留名称与负值金额间距。

执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；原生真实只读账本历史/今天/排行多阶段验证；python scripts/build_macos_app.py及实际启动脚本。新增4项覆盖日期截止与无未来成交/估值、非交易日回退、其他页面日期隔离、切回今天、累计含完全清仓/缺失、全量排序后分页与多系列去重、排序重置页码和其他图隐藏控件。

原生证据：reports/snapshot-20261010-native.png显示所选2026-10-10、有效收盘2026-10-09；reports/snapshot-return-today-native.png确认返回当天；reports/rank-loss-order-native.png确认实际有数据的亏损最多排序。未修改真实源库。尚待解决：累计卡片仍采用既有净投入与清仓收益口径，并不构成历史全量对账已通过；数据缺失时仍保留缺失。下一步按用户反馈继续。

最终验收结果：Ruff通过，191项测试通过；Money3.app重建与codesign --verify --deep --strict通过；实际启动Money3.command运行12秒正常退出。打包截图reports/snapshot-date-bundle.png确认日期控件与累计盈亏卡片已生效。真实全启用人员账本的历史回看、切回今天及有数据排行榜排序已完成原生验证；未修改源数据库。用户要求先完成上述功能，Git提交暂未执行。

# 2026-10-19 券商人员盈亏视图

完成阶段：券商资产提供当日盈亏/累计盈亏双视图，渠道/独立截止日、卡片、人员横向柱图、汇总表、人员股票明细、点击筛选/再次恢复、三种排序及人员汇总导出。

修改文件：新增engine/people_pnl.py、ui/broker_people.py、tests/test_broker_people.py；修改services/analytics_service.py、ui/main_window.py、interactions.py、workspace.py、report_page.py、live_quotes.py，以及README/CHANGELOG/本文档。

来源与计算：calculate_people逐人调用只读AnalyticsService，不将多人按份额粗分。券商账本另算用于核对，不与人员账本重复加总；计算前后源库/WAL/汇率缓存身份发生变化则拒绝混合结果、提示刷新。每人日盈亏=实时日末市值－期初收盘市值－当日买入现金流＋当日卖出现金流；历史使用对应日收益行，完全卖出股票仍纳入当日贡献与明细。统一使用人员/券商全部账本最近有效收盘日，避免某个已清仓人员的旧交易日被误当今天；实时无当日收益行仅接受公共上一收盘日的基准。缺价格/数量/现金流保持缺失。累计盈亏=各人已清仓收益＋持仓浮动盈亏；同一股票某人清仓、另一人仍持有时，已清仓/浮盈分类按人员状态汇总，分类不同不单独判为未分配。

未分配／归属差异=券商当日盈亏、累计盈亏、资产及净投入减人员合计；显示对应差异明细，可能涵盖停用人员、未归属或录入差异，不把它强塞给某人，不把未知算成0。日收益率沿用EXCEL_COMPAT盈亏/(市值－盈亏)，不适用分母保留缺失；所有金额核心计算Decimal精度50，仅绘图坐标转float。资产不含现金。人员视图隐藏原个人筛选并固定人员权益账本，离开后恢复原控件值。历史不连实时，当天复用同一QuoteFeed缓存，订阅补充券商未归属持仓以估值差异，不逐人请求行情。

界面：顶部渠道总盈亏使用独立券商账本合计，未分配行使人员可核对；累计分解卡片按人员分类合计。隐藏通用人员组合估值摘要，避免混淆两套账本口径。柱图和两张表全部展开、整页滚动，不挤压卡片和行；点击状态在10秒估值刷新中保留。导出保留原精度、日期/渠道/口径及说明。

执行：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；Cocoa真实账本历史当日/累计/点击验证；长桥单渠道今天14秒以上行情验证；macOS打包及实际启动脚本验收。新增8项测试覆盖全清仓当日现金流、按人员清仓分类、未分配残差、不混加账本、缺失不作零、公共收益日、实时价格及基准、UI点击/排序/模式/个人筛选隔离、只读服务以及源库变化拒绝混合。

原生证据：reports/broker-people-daily-native.png、broker-people-lifetime-native.png、broker-people-selected-native.png；真实账本10行人员汇总（含差异）、193条股票贡献；reports/broker-people-live-channel-native.png验证仅长桥渠道，盘前时段随缓存估值并保留全部人员。尚待解决：源账本既有财务核对事项保持，不据本功能宣称全量对账通过；有差异独立展示。下一步按用户反馈继续，Git提交仍按用户要求暂缓。

最终测试：Ruff通过，全量199项测试通过；最新股票贡献排序修改后人员视图8项专项再次通过。另验证从带单人筛选的今日盈亏返回券商视图时，正确恢复全部人员Analytics与实时报价范围；导出保留当前汇总排序。打包烟雾工具增加隐藏smoke-report参数，便于实际启动脚本直接验收券商视图，不改变普通启动默认今日盈亏。

最后回归与安装包：最新全量199项测试通过，Ruff通过；美股时段同时注明所选日期和纽约交易日，周末休市标签有回归断言。Money3.app构建与严格签名校验通过，实际启动脚本以渠道汇总目标打开券商人员页，15秒烟雾验收截图reports/broker-people-bundle.png。所有源数据库仅以只读方式访问，Git未提交。

# 2026-10-19 人员零值柱隐藏

完成阶段：人员盈亏柱图隐藏显示为零的条目。修改文件：ui/broker_people.py及README/CHANGELOG/本文档。业务逻辑与来源：仅在PeopleBars接收图表行时按金额两位小数的实际显示值过滤，兼容正负零和舍入零，当日与累计模式共用；未知值保留，源账本、汇总表、明细和合计不改。执行过的命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest tests/test_broker_people.py -q。测试结果：Ruff通过，人员盈亏专项8项通过。尚待解决：无新增财务差异处理。下一阶段计划：根据用户反馈调整，Git仍暂缓。

补充验收：直接断言精确零、负零、±0.004均隐藏，±0.01及缺失保留，原始行不改；原生窗口使用真实账本验证当日/累计与点击明细通过，截图reports/broker-people-daily-native.png中零值人员不再占柱图行。Money3.app已重新构建，codesign --verify --deep --strict通过。

# 2026-10-19 股票归属视图

完成阶段：券商资产增加股票归属查询。修改文件：engine/stock_ownership.py、ui/broker_people.py、ui/report_page.py、ui/interactions.py、tests/test_broker_people.py及三份文档。业务逻辑与数据来源：复用所选范围的PeopleBundle逐人员权益账本股票汇总数量，人员内多账户合并；独立券商账本为核对基准，差额单列，未知传播，占比以券商股数为分母，非正分母无占比。仅展示未清仓股票及非零人员/差额，保留8位小数股；不请求新行情，不写源库。

用户验收：券商资产→上方选择长桥→视图下拉选择股票归属→股票选择TSLL；查看人员股数、比例及顶部核对卡片。股数从大到小排列；可改日期回看，导出含股票/日期/渠道及精确值；再切当日/累计盈亏恢复原视图。全部渠道选择时同股票跨渠道合并。

执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q。结果：Ruff通过，全量202项通过。新增3项覆盖全清仓人员排除、券商股数独立核对、重复账户合并、极小小数股、负差额、缺失传播及模式切换。尚待解决：既有账本差异单列保留，不自动修正归属。下一阶段按用户反馈，Git仍暂缓。

真实账本原生验收：长桥→股票归属→TSLL，显示6名持有人，人员股数加归属差额等于券商总股数；切回当日盈亏明细正常。截图reports/stock-ownership-tsll-native.png已检查，股数/比例和小数股正常展示。

交付：Money3.app重新构建成功，严格签名校验通过。最终小调整后人员专项11项再次通过，Ruff通过；此前全量202项通过。

# 2026-10-19 股票归属改为环形饼图

完成阶段：股票归属展示环形饼图及人员图例。修改文件：ui/ownership_pie.py、ui/broker_people.py及README/CHANGELOG/本文档。业务逻辑与来源：沿用stock_ownership报告的Decimal股数及占比，不改源账本或计算；仅绘图角度转换整型。仅所有股数非负、总股数正且合计一致时绘饼；否则显示原因与全部图例明细。未分配行灰色，其他人员分色；图中央显示股票和券商总股数。

执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest tests/test_broker_people.py -q。结果：Ruff通过，11项人员专项通过。用户验收：券商资产→股票归属→长桥TSLL，观察环形图与姓名/股数/占比图例，明细和切回盈亏功能保留。尚待解决：账本差额按原规则列示。下一步按用户反馈，Git仍暂缓。

最终验收：真实账本长桥TSLL的6人环形图、股数和占比已在原生窗口截图核验（reports/stock-ownership-tsll-native.png）；空数据、未知、负值、小数股绘制分支均实测通过。Money3.app重新打包及严格签名校验通过。

# 2026-10-19 今日盈亏当前市值

完成阶段：今日盈亏持仓表最新价后增加当前市值。修改文件：engine/today_holdings.py、ui/report_page.py、tests/test_today_holdings.py、tests/test_analytics_ui.py及三份文档。业务逻辑与来源：直接复用live_holdings市值v[5]，与渠道资产图的current_asset完全相同；实时价可用时为股数乘报价，缺实时价沿用标明的参考估值，未知保持缺失。历史沿用选定截止日回放结果，不增加行情请求。

执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；原生窗口只读真实账本验收。用户验收：今日盈亏→最新价与今日涨跌幅之间查看当前市值，可用表头排序、导出原值；切换日期与渠道后跟随变化。测试补充实时及参考市值取值断言、默认列位置、显示值；更新列数预期为12列。尚待解决：原账本差异保持。下一步按用户反馈，Git仍暂缓。

最终结果：全量202项通过，Ruff通过；原生只读真实账本30条持仓逐行验证市值等于渠道资产图current_asset，默认列位置为最新价后；截图reports/today-market-value-native.png。
Money3.app重新打包与严格签名校验通过。

# 2026-10-19 持仓财报日历

完成阶段：新增独立持仓财报日历，左侧资产管理入口，月份浏览/当前月、人员渠道股票筛选、按天点击明细、公司名称/代码/发布时间/北京时间及未返回日程列表；ETF关联设置仅本机保存。
修改文件：新增services/earnings_service.py、ui/earnings_calendar.py、tests/test_earnings_calendar.py；修改ui/sidebar.py、workspace.py、interactions.py、main_window.py及README/CHANGELOG/DECISIONS/本文档。
业务逻辑与来源：当前筛选当日持仓使用AnalyticsService只读回放；长桥CalendarContext只读数据按公司匹配和去重，不改SQLite，不新增行情订阅。用户已配置的凭证仅解析读取，异常显示通用错误不泄露SDK密钥。独立工作线程和取消标记，24小时内存缓存/12个月上限，按日分页返回重复提示不完整，不补造事件和精确时刻。TSLL关联来源与时间边界见DECISIONS。
执行过的命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；原生真实持仓财报接口验收。
测试结果与对账差异：Ruff通过，全量208项通过。新增6项覆盖日期带EST标记、日期时间戳占位不冒充时分、夏令时换算、按日分页、缓存/强制刷新/取消、重复页提示、持仓/ETF关联/清仓排除、日历日期点击/公司名/持仓关联和未返回列表、当前持仓异步加载及过期月份结果隔离。真实只读持仓24个标的匹配本月5项财报，原生截图reports/earnings-calendar-native.png已检查，包含Tesla及盘前/盘后标记；API对部分日期分页重复，界面已明确显示不完整提示。
尚待解决：长桥数据覆盖与精确发布时间取决于接口，未匹配不能推断未公布；未指定ETF关联可由用户本机设置，不自动猜测。未声称财报与公司IR公告逐条核验。下一阶段计划：按用户反馈继续，Git提交仍暂缓。
用户验收：重启Money3→持仓财报日历→选择渠道/人员，查看本月公司日程；点日期筛选，再点恢复；改月份查历史/未来日程；“关联公司”设置单股ETF的对应公司，刷新财报可重取。

最终交付：Money3.app重新构建及codesign --verify --deep --strict通过。实际启动脚本以--smoke-page 14运行35秒成功退出，截图reports/earnings-calendar-bundle.png已核验；原生真实接口已加载公司日历与时段，页面完成后隐藏旧交易加载提示。正常启动仍默认今日盈亏，用户设置与源库未被验收脚本修改。

# 2026-10-19 单股杠杆ETF财报自动关联

完成阶段：财报页面按ETF对应正股统一处理，覆盖当前12只单股ETF，普通股照常；指数基金单独说明，未知杠杆标待关联。修改文件：新增services/earnings_links.py；修改services/earnings_service.py、ui/earnings_calendar.py、tests/test_earnings_calendar.py及四份文档。业务逻辑与来源：关联来源逐条记录在earnings_links.py，只有发行人/监管资料核实的标的内置；日历去重正股事件并标注ETF持仓，不改持股与财务口径。旧设置一次性迁移，手动覆盖及空值保留。

执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；真实账本只读关联核验及原生日历验收。新增测试覆盖12项映射、指数排除、未知杠杆待确认、旧默认清理/自定义保留、正股与ETF共享一份财报及日历关联代码。真实当前持仓核验12只单股ETF、4只指数ETF，全部杠杆分类已覆盖。尚待解决：长桥分页不完整限制仍提示；未来未知产品需要核对映射，默认不猜。下一阶段按用户反馈，Git仍暂缓。

验证结果：全量210项通过，Ruff通过。当前真实账本12只单股ETF/4只指数ETF关联已核对；原生接口现在匹配本月8项财报（日历展示正股公司并标注ETF关联持仓），截图reports/earnings-calendar-native.png已检查。数据源分页重复限制继续明示，未宣称全量财报完整。
Money3.app重新构建及严格签名校验通过。重启正常应用时执行一次旧默认关联迁移；原生验收使用no-settings方式，未改用户设置。

# 2026-10-19 收益日历实时今日与月汇总

完成阶段：资产总览日历同步实时当日盈亏，计入本周、月、季度、年度合计。修改文件：engine/dashboard.py、engine/live_valuation.py、ui/dashboard_page.py、ui/main_window.py、tests/test_live_metrics.py及三份文档。业务逻辑与数据来源：复用live_metrics今日盈亏（含当日全清仓现金流和可信期初基准），仅创建日历视图聚合行，按纽约交易日替换已有该日全部显示行，或增加新日；金额Decimal原值。原始Analytics每证券日报/月报/SQLite不变。无实时/历史恢复原始收盘展示，实时未知保留缺失而非零；保留月份/年份选择。今日点击穿透今日盈亏，其他日期仍走历史日报。

执行：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q。全量213项通过（周合计标记补充前），新增3项覆盖当日多证券收盘行替换/不重复累加、缺报价月汇总缺失、停用恢复、跨年月/交易日、新日历金额/月矩阵/季度标记与点击路径。尚待解决：源账本历史缺值继续保留，非本功能自动修复；实时为估值非正式收盘。下一阶段按用户反馈，Git仍暂缓。

真实行情验收：拉科人员、当日8条持仓行情覆盖完整时，10/19日历值已出现，月矩阵金额与实时本月盈亏KPI完全一致；源Analytics每日收益未插入实时行。截图reports/live-profit-calendar-native.png。为避免日历金额被第三行挤成省略号，实时标记移到日期同一行，保持两行完整显示；周合计同样标含实时估值。补充后Ruff和14项实时/月汇总专项通过。
最终两行排版截图已核验，今日金额及实时标记完整；Money3.app重新构建与严格签名校验通过。

# 2026-10-19 人员切换复用持久化行情

完成阶段：修复切换人员先闪回周五数据，报价每10秒持久化并支持重开优先读缓存。修改文件：新增services/quote_store.py、tests/test_quote_store.py；修改domain/live_quotes.py、services/live_quotes.py、engine/live_valuation.py、ui/live_quotes.py、ui/main_window.py、ui/dashboard_page.py、tests/test_live_quotes.py及四份文档。
业务逻辑与来源：源库LATEST和收盘均截至10/16，独立行情库保存当前Longbridge报价和交易日历；UTF/UTC时间、精确Decimal文本、按代码时段单条更新、较旧tick不覆盖新tick，14天与2000条上限。切换人员保留共享行情，首次全组合预热；绑定直接使用缓存，冷启动显示等待当前时段。新增quote_source并保留报价时间/旧报价标记；只按可信交易日历判定时段，过期日/时段不冒充实时。源SQLite只读。
执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q。结果：Ruff通过，全量217项通过（最后增加DB行数上限前）。新增4项覆盖精确值持久化重开、旧tick拒绝、跨日时段不可用、人员切换保留/关闭保存、全人员预热只做一次、绑定优先缓存与冷启动不显示周五今日值；原行情测试适配注入Store参数。
尚待解决：无当前时段报价时等待，不编造价格；报价缓存写失败会提示，盘中估值不是正式收盘，实际交易依然需Money2原流程更新源库。下一阶段按用户反馈，Git暂缓。

原生真实验证：全部持仓预热后保存96条代码/时段报价记录；拉科切换个人时bind直接使用当日报价，无10/16今日数值帧。关闭后以同一临时QuoteStore重开，30条持仓来自持久化缓存，报价时间与来源保留，源SQLite只读。截图reports/quote-person-switch-native.png及quote-restart-cache-native.png已核验。正常安装包使用应用数据目录live_quotes.sqlite3；原生验收注入临时库，不改用户偏好。最后Ruff及27项行情/缓存专项通过，Money3.app构建及严格签名校验通过。

# 2026-10-19 财报日历突出今天

完成阶段：用浅蓝背景、蓝色边框及加粗蓝色“今天”标记突出当前日期。修改文件：ui/earnings_calendar.py、ui/main_window.py及三份文档。业务逻辑与来源：仅视图样式，日期按Asia/Shanghai当前日判断，复用主题tokens，主题切换重绘；财报数据/查询与源账本保持原样。执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest tests/test_earnings_calendar.py -q；原生截图验收及应用打包。尚待解决：无新增数据需求。下一阶段按用户反馈，Git暂缓。

验证结果：财报日历相关 8 项测试通过，Ruff 检查通过；原生窗口截图确认今天格子蓝色边框、浅蓝底色和蓝色加粗文字，切换其他月份无误标。已重新打包 Money3.app 并通过 codesign 校验。本次为界面样式修改，未改变财报或资产计算。

### 跨天收益归档验收（2026-10-20）
完成阶段：跨交易日历史估值恢复及今日基准修复。
修改文件：quote_store、analytics_service、performance/holdings/live_valuation/today_holdings/close_baseline、dashboard、live_quotes 控制器及领域读模型。
业务逻辑与来源：原始账本只读，本机独立行情缓存新增逐日快照；历史估值与正式收盘明确区分，正式价优先，实际流水重放，Decimal计算。
执行命令：ruff check src tests scripts；QT_QPA_PLATFORM=offscreen python -m pytest -q；原生只读核验脚本 /private/tmp/check_money3_history.py（行情缓存使用临时副本）。
真实核验：10/19恢复30条日收益，30条金额已知；日历显示“历史估值”，月度汇总包含历史估值，原生截图 reports/historical-estimate-native.png。这是已有本机快照恢复的估值，不等同正式收盘对账。
尚待内容：缺少任何报价的历史交易日无法凭空恢复，正式收盘数据补齐后自动优先替换。
下一阶段：完成Mac打包和签名校验。

最终回归：224 项测试通过（7项新增归档/基准/跨天与日历保留测试），Ruff src/tests/scripts 全部通过。真实源库以只读连接读取；行情恢复验证使用独立临时缓存副本。原生界面确认10/19历史估值、10/20独立实时盈亏及月度汇总。
打包验证：scripts/build_macos_app.py 成功；codesign --verify --deep --strict 通过；启动Money3.command --no-settings --smoke-ms 7000 --smoke-page 2 加载只读账本后正常退出（退出码0）。该打包冒烟隔离用户设置，不启用行情缓存；历史恢复由此前原生缓存副本核验覆盖。

### 正式收盘补取验收（2026-10-20）
完成阶段：长桥日K正式收盘缓存与估值替换。修改：services/live_quotes.py、quote_store.py、tests/test_official_closes.py。
来源：长桥 Day/NoAdjust/Intraday 日K，只写独立行情缓存，源 money.sqlite3 只读。
执行：Ruff src/tests/scripts；全量pytest；实际长桥API同步脚本 /private/tmp/sync_money3_official_closes.py；原生只读核验 /private/tmp/check_money3_official.py。
结果：226项测试通过，Ruff通过。实际补取10/19全部24只证券的正式日K，无失败；真实账本30条10/19收益均已知且全部无历史估值标记，日历和月度汇总不再标为历史估值。截图 reports/official-close-native.png。新增测试覆盖未收盘拒绝、错误退避、休市不反复查询、持久化去重、常规时段不复权参数、源库优先和正式数据替换估值。
尚待：接口无权限/无日K时保持估值或缺失，不能把缓存快照冒充正式价。下一步：Mac打包及签名启动验证。
Mac发布验证：重新打包成功，codesign --verify --deep --strict 通过；启动Money3.command --no-settings --smoke-ms 7000 --smoke-page 2 只读账本启动验证退出码0。正式收盘替换的原生界面已核验，昨日盈亏显示“10/19 收盘”，日历10/19不再含历史估值标记。

股票状态默认筛选（2026-10-20）：股票汇总/人员资产的状态选择初始为“持有中”；可手动选择全部状态、已清仓或异常状态，行情刷新与切换报告保留本次选择，其他报表不受该状态过滤影响。仅修改列表默认值，不改变原始记录或资产计算。
验证：现有股票状态/人员资产筛选测试3项通过，包含首次进入仅展示持有中、全部状态可恢复、搜索排序刷新保持筛选、已清仓筛选和导出；Ruff通过。
