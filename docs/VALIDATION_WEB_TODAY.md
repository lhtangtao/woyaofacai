# Web「今日盈亏」专项修复验收

基线：GitHub / 本地 `c036bae`；2026-10-26，北京时间。只补 Web，原生 PySide6 界面与财务引擎没有修改。

完成阶段：Web 今日盈亏功能补齐与专项验收。

修改文件：`frontend/src/views/ReportsView.tsx`、`TodayPnlView.tsx`、`today-pnl.css`、`frontend/src/api/client.ts`、`src/portfolio/web/api_v1.py`、`services/web_today.py`、`db/repositories/analytics.py`（只新增证券目录查询）、专项测试及文档、构建产物。

## 业务逻辑与数据来源

- API `GET /api/v1/holdings/today` 一次返回筛选范围内的卡片、渠道汇总、曲线、明细及来源日期。旧今日盈亏报表入口与 CSV 导出复用该适配器，消除另一条收盘专用路径。
- 实时估值、今日收益和卡片分别复用 `live_holdings`、`today_holdings`、`today_summary`、`lifetime_summary/lifetime_total`。不增加 Web 专属盈亏公式。表格的展示占比按原生 ReportPage 在本地筛选后使用的分母及精度重算。
- 行情缓存使用原桌面的应用数据目录 `MoneyPortfolio/Money Portfolio/live_quotes.sqlite3`，不根据 Flask 的应用名建立另一份库。复用 `QuoteFeed` 推送、缓存和交易日历，浏览器每10秒读取一次快照。历史补取在单个后台工作线程中执行，有限队列、按范围/来源版本去重、60秒退避，已有缓存优先呈现。
- 五分钟曲线调用原有 `rebuild_history`，读取按 `scope_key` 和 `source_version` 校验的真实历史点；没有历史点时可展示原生的分钟观察记录，并明确标注类型。完整时间轴、分界、极值、最新有效点、缺失断线与告警均保留。未伪造任何生产曲线。
- API 金融值以 Decimal 文本传输，只有浏览器显示和 ECharts 坐标转换为 Number。源业务库通过 `readonly_connection` 读取；连接不跨线程使用。缓存写入仅涉及独立行情缓存。
- 人员权限先在服务端形成 QueryScope，再进行曲线读取及补取；未绑定人员的普通账号采用不可命中的范围，避免空 tuple 被引擎解释为全人员。匿名局域网访问只有读资产能力。
- 日期、渠道、Ticker、组合搜索、表头排序、预设排序及导出使用相同条件。切换条件中止旧请求并隐藏旧范围的数据，避免人员切换时闪现上一人员资产。
- 保留原桌面口径：卡片的当日盈亏只含当前持仓，曲线包含当日清仓；累计盈亏按人员/日期/渠道统计全部历史，本表 Ticker 搜索不裁切累计卡片。页面说明了这一区别。

## 执行过的命令与实际结果

| 检查 | 结果 |
|---|---|
| `git ls-remote origin HEAD` | 与本地 `c036bae` 一致；未合并或重置工作区 |
| `.venv/bin/python -m pytest tests/test_web_today.py -q` | 19 通过 |
| 专项 + today_holdings / daily_quote_history / quote_store / live_quotes / live_metrics 回归 | 68 通过、4 失败 |
| 从 `git archive HEAD` 导出的修改前代码复测这4项 | 同样4项失败，确认是基线已有差异 |
| `.venv/bin/python -m pytest tests/test_web_today.py tests/test_daily_quote_history.py tests/test_live_quotes.py -q` | 49 通过，1项已有依赖弃用警告 |
| `.venv/bin/python -m pytest tests/test_prd_v2_security.py -q` | 32 通过 |
| `npm --prefix frontend run build` | TypeScript 与 Vite 构建通过；保留原有大 bundle 提示 |
| Ruff 检查新增服务、证券查询和专项测试 | 通过 |
| `node frontend/tests/today-pnl.browser.cjs`（配置本机 Playwright 模块） | Chrome 实测11类检查通过，页面脚本错误0；逐格校验财务表格；10秒后实际观察到新请求 |
| 本机长桥实时接入探测 | 实际订阅21只证券，24/24条持仓取得新鲜盘中报价；凭证未输出，写入验收缓存副本 |

浏览器覆盖：四张卡片、真实历史曲线、渠道选择/恢复、实际点击渠道柱体、Ticker、排序及CSV、无结果、拦截请求模拟断网、当前日期、10秒轮询、900像素宽度下图表实际缩放。截图人工检查了布局、极值标记、时段边界、渠道资产和持仓表格。

## 原生截图及相同快照比较

使用原生 `ReportPage` / `IntradayPnlChart` 原始组件生成 Qt 离屏截图，Web 使用真正的 Chrome 页面截图；未改造原生组件。不是用户提供的旧截图，也不是对完整 MainWindow 菜单和操作系统窗口进行逐像素自动比较。

验收期间检测到真实账本更新，旧曲线按版本校验正确失效。因此最终比较使用真实账本的只读备份及行情缓存的范围副本，并用真实已缓存K线重新生成临时曲线；两端使用同一份冻结快照，未改写源交易或扩大容差。

| 日期与渠道 | 持仓明细 | 完整时间轴 | 有效五分钟点 | 对比 |
|---|---:|---:|---:|---|
| 2026-10-23 全部渠道 | 24行 | 289点 | 288点 | 明细与原生逐值相等，曲线逐点相等 |
| 2026-10-23 长桥 | 15行 | 289点 | 288点 | 明细与原生逐值相等，曲线逐点相等 |
| 2026-10-26 全部渠道 | 24行 | 289点 | 167点 | 明细与原生逐值相等，曲线逐点相等 |
| 2026-10-26 长桥 | 15行 | 289点 | 167点 | 明细与原生逐值相等，曲线逐点相等 |

当日尚未取得的行情和未来时间保持空值。四卡片和极值另由专项测试核对原生函数/组件结果，未只验证表格“能够显示”。

本机验收证据位于忽略版本控制的 `reports/web_today_acceptance/`：`parity.json`、`browser-result.json`、`live-probe.json`、`snapshot-*.json`、`native-*.png`、`web-*.png`。其中包含私人资产，不提交 Git。`run_server.py` 保存了本次隔离服务、冻结副本及截图的具体步骤。测试账户独立初始化；真实认证库前后哈希一致。真实交易库、流水和行情库在验收期间存在更新，不能声称它们全程哈希不变；本次适配器使用只读业务库连接，最终验收转在副本上进行。

## 尚待解决与下一阶段计划

保留以下4个已有回归失败，未为了通过测试改变桌面端：

- `test_today_page_preset_sort_summary_and_sidebar_order`：旧夹具行13列，现有桌面表14列，动态占比赋值越界。
- `test_asset_dates_are_fixed_and_other_pages_keep_custom_dates`：旧测试对固定起始日期的预期与当前桌面实现不同。
- `test_binding_new_owner_uses_cached_quotes_first_and_cold_start_waits`：旧 SimpleNamespace 夹具没有现有代码所需的 trading_days。
- `test_dashboard_calendar_and_month_grid_follow_live_cards_and_restore`：现有季度单元格未包含旧测试预期的“实时估值”文字。

没有对全仓历史账本与原 Excel 进行重新财务验收。公网/Tailscale真实网络、真实物理断网、半日市的端到端实机验收仍待补充；休市回溯、缺行情及旧报价已做隔离自动测试。本次浏览器断网检查是请求拦截，不冒充物理断网实验。

下一阶段：独立处理已有桌面测试差异，并扩展真实网络与半日市验收。运行中的 Web 服务需要重新启动来载入新的 Python API；生产前端构建产物已经更新。
