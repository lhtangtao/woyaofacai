# Mac / Web 功能一致性静态审查（2026-10-27）

审查结论：当前不能认为双端已完整一致。共享 Python 引擎不等于所有 Web 页面已接入相同的数据、筛选和交互。

范围：本地 main（8e8b5ed）及当前未提交工作区，主要菜单、React 页面、Flask v1 接口、原生界面和共用服务/引擎调用链。未逐行穷尽所有源码；以下是可由源码定位的差异，不是运行或浏览器验收结果。按用户要求未执行测试、构建或请求真实接口。

## 需要优先修复

1. **P1 股票归属接口的总额没有按人员权限收口。** `src/portfolio/web/api_v1.py:856` 调用 `calculate_people`；`src/portfolio/services/analytics_service.py:40` 把人员范围改成全部。接口只过滤 `report_data`，但 `choices`、`broker_total`、`assigned_total`、`difference` 仍从全账本结果返回（api_v1.py:897）。普通用户即使只能看自己的行，也可能读到其他范围的标的或总量。需先限制计算/输出范围，并对券商全量汇总单独授权。

2. **P1 资产总览未统一实时估值与历史缓存。** `api_v1.py:251` 直接使用 `calculate()` 的 metrics；原生 `ui/live_quotes.py:375` 使用 `live_metrics()`。Web 通用服务在 `web/app.py:354` 未传 QuoteStore/FxStore，原生 main_window.py:244 注入两者。今日盈亏专用服务已用共享 QuoteStore，但其他通用页面没有跟上。不能保证同一人员的资产总览、当前持仓、历史报表与 Mac 同快照一致。

3. **P1 券商资产接口与页面字段错配，且股票/现金关系错误。** `EntitiesView.tsx:45` 不读取 API 返回的 `channels`；页面读取 broker/stock_val/total_val，接口返回 channel/stock_value/total_asset（api_v1.py:813）。即便补上列表，名称与总额仍需映射。引擎 dashboard.py:230 的渠道 values[1] 已是股票市值，api_v1.py:817 又减现金，不能得到正确股票市值；总资产也不能直接把该值当含现金合计。

4. **P1 股票归属页面也未对接真实 DTO。** `EntitiesView.tsx:45` 不读取 `/ownership` 返回的 `report`；还缺少原生的股票选择、饼图、券商/归属差额汇总。接口虽提供 choices，client.ownership() 和页面没有选择参数交互。空白表格不代表没有归属数据。

5. **P1 Excel 对账页面会给出误导性的通过结论。** `SystemViews.tsx:219` 读取 diffs，而后端 `api_v1.py:1787` 返回 discrepancies；页面230及256行固定写“核心财务事实比对一致”“100%对齐一致”。后端对 reconcile 返回的每列解释也与 reconcile.py:66 的真实差异报告列不符，而且把 person_name 写死为个人。需以 PASS/DIFF/UNAVAILABLE 等真实结果驱动展示，禁止固定通过文案。

6. **P1 新增清仓当前价遗漏旧 Excel 列映射兼容。** 这是本轮 Codex 修改带来的待修问题：`engine/holdings.py:9` 在卖出均价后插入当前价，但 `reconcile.py:93` 仍按 report.columns 的位置读取原 Excel。旧13列清仓表的首次买入/最终清仓时间会与新14列表错位，产生伪差异。应保持旧对账投影或按旧字段明确映射，新增报价不纳入历史黄金对账。不能通过改原 Excel 或调容差解决。

7. **P1 Top 日/月与收益排行的 DTO 不匹配。** `api_v1.py:1042/1077` 返回 [日期,金额] 数组；`ChartsView.tsx` 的 Top 分支按对象读 date/profit/value，会显示空日期或把金额回退为0。排行接口返回 points（api_v1.py:1107），页面却读 items/ranking。Top 数量参数前端 top_n、后端 count 也不一致。

8. **P1 指标说明和开发进度尚未对接。** `SystemViews.tsx:183` 对 metrics 使用 .map，但 `api_v1.py:1714` 返回原 JSON 字典；指标页存在渲染异常路径。开发进度页面148行读取 logs，后端1729行返回 progress_logs，因此页面可能为空。日志写入成功不代表 Web 已展示。

9. **P1 资产总览有字段语义错误和缺失变0。** `api_v1.py:299` 把回本缺口映射为 net_invested；`DashboardView.tsx:60` 同样读取回本缺口作为净投入。这两项业务意义不同。另外多处 `|| 0` / `or Decimal(0)` 会把缺失资产或现金表现为确定的0，违反原生未知值语义。需保留 null 和可信现金基准。

## 功能与交互缺口

10. **P2 通用筛选范围未保持一致。** 原生 `main_window.py:450` 支持人员、券商/人员账本、渠道账户、Ticker、分类、起止日期、停用/审计及币种。Web 多数页面只传 person_id，实体页虽然依赖 personId 重载，却没有把参数传给 people/brokers/ownership；Top/排行接口自身也未读取前端传的 person_id。顶部切换不等于后端已按同范围计算。

11. **P2 新人员盈亏页只对齐了部分功能。** 双端已有金额/收益率标签、表头排序及共享 people_reports，但 `BrokerPeoplePnl.tsx:100` 只提交日期，没有原生渠道/Ticker筛选。导出指向 `/export/report/人员盈亏`（370行），而该报告只在 people_reports 动态生成，通用导出未接入此流程，因此不能保证可导出，更不能保证与当前筛选、排序一致。

12. **P2 月度高频走势与历史持仓分布未复刻。** 原生 `monthly_realtime_page.py:222` 后有月/年、累计/日内、人员、渠道等专属控件及高频曲线；Web ChartsView 的 monthly 分支仅按标题筛选通用 charts。原生 distribution_page.py:38 有历史月份选择，Web 分布页仅筛 pie。菜单名字相同，数据与操作能力仍不同。

13. **P2 数据中心与数据同步存在无对应内容的菜单入口。** Sidebar 提供 reports:list 和 system:sync，App 对 list 仍按普通报表名加载；没有名为 list 的报表。system:sync 仅定义标题，没有对应内容组件。只读用户不开放写同步可以是正确权限设计，但应有明确说明页，不能空白。

14. **P2 证券详情未接入原生证券详情。** Sidebar 将证券详情指向 reports:分类汇总；原生 stock_detail.py 提供选中证券的价格、买卖/划转标记及交易回放。分类汇总表不等价于证券详情，通用 Web 报表也没有对应双击下钻。

15. **P2 AI 股票页的清空行为未按模式区分。** `AiViews.tsx:79` 在股票分析模式也调用 api.aiClear()，而 client.ts:254 访问的是 `/ai/clear`（投资组合会话），不是当前股票历史。页面会清空显示，但股票历史未必被清除，还可能清理错误的组合会话。原生支持当前标的独立会话与中止，Web 尚未完整对应。

## 已共享的部分与结论边界

- 今日盈亏专用链路已复用 today_holdings、today_summary、channel_summary、QuoteStore/QuoteFeed、intraday_history；两端汇总行、人员盈亏及清仓当前价也已有公共函数。
- 上述共享不能替代页面到接口的逐项接线审查，也不能证明运行时每个页面相同。
- Web 账号权限、匿名局域网只读和远程登录本来就与本地桌面运行环境不同；这类必要权限差异不应为了“界面一致”被取消。
- 建议顺序：权限范围与错误通过文案 → 接口契约/未知值 → 同一缓存与实时快照 → 筛选/导出/专属图表和下钻。
- 本次仅审查；清单所列问题尚未修复。随后用户指定的 Mac 总览日历内部滚动条已单独改为完整展开，未作运行验收。
