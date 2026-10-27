"""Native workspace composition. No SQL or financial arithmetic belongs here."""
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from portfolio.domain.models import Ledger
from portfolio.ui.charts_page import ChartsPage
from portfolio.ui.components import FilterChips, MetricCard, StatusLabel
from portfolio.ui.dashboard_page import DashboardPage
from portfolio.ui.date_picker import DatePicker
from portfolio.ui.filters import AccountFilter, TickerSearch
from portfolio.ui.icons import icon
from portfolio.ui.live_quotes import LiveQuotesPanel
from portfolio.ui.report_page import ReportPage
from portfolio.ui.sidebar import Sidebar
from portfolio.ui.stock_detail import StockDetail
from portfolio.ui.trade_drawer import TradeDrawer
from portfolio.ui.trade_table import TradeTable


def build_workspace(self):
    central = QWidget()
    self.setCentralWidget(central)
    root = QHBoxLayout(central)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    self.navigation = Sidebar()
    root.addWidget(self.navigation)
    body = QWidget()
    body.setObjectName("workspace")
    layout = QVBoxLayout(body)
    layout.setContentsMargins(24, 20, 24, 18)
    layout.setSpacing(10)
    top = QHBoxLayout()
    back = QPushButton()
    back.setIcon(icon("back", self))
    back.setObjectName("quiet")
    back.setToolTip("后退 · ⌥←")
    back.clicked.connect(lambda: self.move_history(-1))
    forward = QPushButton()
    forward.setIcon(icon("forward", self))
    forward.setObjectName("quiet")
    forward.setToolTip("前进 · ⌥→")
    forward.clicked.connect(lambda: self.move_history(1))
    for button in (back, forward):
        button.setFixedWidth(32)
    self.history_buttons = (back, forward)
    titles = QVBoxLayout()
    titles.setSpacing(5)
    self.heading = QLabel("交易明细")
    self.heading.setObjectName("heading")
    self.description = QLabel("查看每笔交易，追溯原始记录与人员归属。")
    self.description.setObjectName("secondary")
    titles.addWidget(self.heading)
    titles.addWidget(self.description)
    top.addLayout(titles, 1)
    self.asset_reset_button = QPushButton("重置筛选")
    self.asset_reset_button.setToolTip("恢复个人 · 全部渠道 · 全部股票及默认日期")
    self.asset_reset_button.clicked.connect(self.reset_asset_filters)
    self.asset_reset_button.hide()
    top.addWidget(self.asset_reset_button)
    self.refresh_button = QPushButton("刷新")
    self.refresh_button.setIcon(icon("refresh", self))
    self.refresh_button.setToolTip("刷新当前数据源 · ⌘R")
    self.refresh_button.clicked.connect(self.reconnect)
    top.addWidget(self.refresh_button)
    self.export_button = QPushButton("导出")
    self.export_button.setObjectName("primary")
    self.export_button.setIcon(icon("download", self.export_button))
    self.export_button.setToolTip("导出本页原始 16 列；财务报表请使用各报表的导出入口")
    self.export_button.clicked.connect(self.export_current_page)
    top.addWidget(self.export_button)
    layout.addLayout(top)
    status = QHBoxLayout()
    self.source_label = QToolButton()
    self.source_label.setObjectName("sourceBadge")
    self.source_label.setText("未连接 · 选择本地数据库")
    self.source_label.setIcon(icon("database", self))
    self.source_label.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    self.source_label.clicked.connect(lambda: self.navigation.setCurrentRow(1) if self.info else self.choose_database())
    status.addWidget(self.source_label)
    status.addStretch()
    status.addWidget(back)
    status.addWidget(forward)
    self.open_button = QPushButton("切换数据源")
    self.open_button.setObjectName("quiet")
    self.open_button.clicked.connect(self.choose_database)
    status.addWidget(self.open_button)
    self.cancel_button = QPushButton("取消查询")
    self.cancel_button.setObjectName("quiet")
    self.cancel_button.clicked.connect(self.cancel_queries)
    status.addWidget(self.cancel_button)
    layout.addLayout(status)
    self.stat_widget = QWidget()
    stats = QHBoxLayout(self.stat_widget)
    stats.setContentsMargins(0, 0, 0, 0)
    stats.setSpacing(12)
    self.trade_cards = [MetricCard(name, hint) for name, hint in (
        ("匹配交易", "当前筛选 · 全部匹配记录"), ("买入记录", "仅当前账本 · 买入笔数"),
        ("卖出记录", "仅当前账本 · 卖出笔数"), ("待审核", "关联、解析或导入异常"))]
    for card in self.trade_cards:
        stats.addWidget(card, 1)
    layout.addWidget(self.stat_widget)
    self.filters_widget = QFrame()
    self.filters_widget.setObjectName("filterCard")
    filters = QVBoxLayout(self.filters_widget)
    filters.setContentsMargins(12, 12, 12, 12)
    filters.setSpacing(10)
    common = QHBoxLayout()
    common.setSpacing(8)
    self.person = QComboBox()
    self.person.addItem("全部启用人员", None)
    self.accounts = AccountFilter()
    self.accounts.setMinimumWidth(124)
    self.stock = TickerSearch()
    self.stock.addItem("全部股票", None)
    for widget, width, tip in ((self.person, 124, "归属人员"), (self.stock, 148, "输入 Ticker 自动匹配，选择后筛选；清空恢复全部股票。")):
        widget.setFixedWidth(width)
        widget.setToolTip(tip)
    self.as_of = DatePicker(QDate.currentDate())
    self.holding_date = DatePicker(QDate.currentDate())
    self.holding_date.setDateRange(QDate(2025, 1, 1), QDate.currentDate())
    self.holding_date.setDisplayFormat('yyyy-MM-dd')
    self.holding_date.setToolTip('选择估值日期；今天使用实时行情，历史日期查看截至当天的持仓与盈亏。')
    self.holding_date.hide()
    self.start = DatePicker(QDate(2025, 1, 1))
    for control in (self.as_of, self.start):
        control.setCalendarPopup(True)
        control.setDisplayFormat("yyyy-MM-dd")
        control.setDateRange(QDate(2025, 1, 1), QDate.currentDate())
    self.start_enabled = QCheckBox("限制起始日期")
    self.start.setEnabled(False)
    self.date_button = QToolButton()
    self.date_button.setMinimumWidth(250)
    self.date_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    dates = QMenu(self.date_button)
    date_widget = QWidget()
    date_layout = QGridLayout(date_widget)
    date_layout.setContentsMargins(16, 16, 16, 16)
    date_layout.addWidget(self.start_enabled, 0, 0, 1, 2)
    date_layout.addWidget(QLabel("从"), 1, 0)
    date_layout.addWidget(self.start, 1, 1)
    date_layout.addWidget(QLabel("截至"), 2, 0)
    date_layout.addWidget(self.as_of, 2, 1)
    done = QPushButton("完成")
    done.clicked.connect(dates.close)
    date_layout.addWidget(done, 3, 1)
    action = QWidgetAction(dates)
    action.setDefaultWidget(date_widget)
    dates.addAction(action)
    self.date_button.setMenu(dates)
    self.side = QComboBox()
    for text, value in (("全部方向", None), ("买入", "BUY"), ("卖出", "SELL"), ("转入", "TRANSFER_IN"), ("转出", "TRANSFER_OUT")):
        self.side.addItem(text, value)
    self.side.setFixedWidth(90)
    self.search = QLineEdit()
    self.search.setMinimumWidth(120)
    self.search.setPlaceholderText("搜索代码、订单  ⌘F")
    self.search.setClearButtonEnabled(True)
    for control in (self.person, self.accounts, self.holding_date, self.stock, self.date_button, self.side):
        common.addWidget(control)
    common.addWidget(self.search, 1)
    self.advanced_button = QPushButton()
    self.advanced_button.setIcon(icon("filter", self))
    self.advanced_button.setCheckable(True)
    self.advanced_button.setFixedWidth(34)
    self.advanced_button.setToolTip("高级筛选")
    common.addWidget(self.advanced_button)
    reset = QPushButton()
    reset.setIcon(icon("close", self))
    reset.setFixedWidth(34)
    reset.setToolTip("重置全部筛选")
    reset.clicked.connect(self.reset_filters)
    common.addWidget(reset)
    filters.addLayout(common)
    self.advanced_panel = QWidget()
    advanced = QGridLayout(self.advanced_panel)
    advanced.setContentsMargins(0, 8, 0, 0)
    advanced.setHorizontalSpacing(12)
    self.ledger = QComboBox()
    self.ledger.addItem("人员权益账本", Ledger.PERSON.value)
    self.ledger.addItem("券商真实账本", Ledger.BROKER.value)
    self.category = QComboBox()
    self.category.addItem("全部分类", None)
    for category in ("大盘", "杠杆", "正股", "待确认"):
        self.category.addItem(category, category)
    self.currency = QComboBox()
    self.currency.addItem("USD 原币账本", "USD")
    self.currency.addItem("CNY 折算展示", "CNY")
    self.currency.setToolTip("原始交易始终为 USD；CNY 自动补齐参考汇率，非发布日沿用此前最近值（最多7天），不含汇率损益。")
    self.link_status = QComboBox()
    for text, value in (("全部关联状态", None), ("已关联", "LINKED"), ("历史未关联", "LEGACY_UNLINKED"), ("待审核", "REVIEW_REQUIRED"), ("不适用", "NOT_APPLICABLE")):
        self.link_status.addItem(text, value)
    self.source_file = QLineEdit()
    self.source_file.setPlaceholderText("来源文件名")
    self.source_file.setClearButtonEnabled(True)
    self.disabled = QCheckBox("含停用人员")
    self.audit = QCheckBox()  # Legacy API; dedicated checkboxes below make audit flags independent.
    self.audit.hide()
    self.include_reversed = QCheckBox("含撤销记录")
    self.include_reference = QCheckBox("含参考记录")
    for col, (label, control) in enumerate((("账本", self.ledger), ("分类", self.category), ("报表币种", self.currency), ("关联状态", self.link_status))):
        advanced.addWidget(QLabel(label), 0, col)
        advanced.addWidget(control, 1, col)
        advanced.setColumnStretch(col, 1)
    advanced.addWidget(self.source_file, 2, 0)
    advanced.addWidget(self.disabled, 2, 1)
    advanced.addWidget(self.include_reversed, 2, 2)
    advanced.addWidget(self.include_reference, 2, 3)
    self.advanced_panel.hide()
    self.advanced_button.toggled.connect(self.advanced_panel.setVisible)
    filters.addWidget(self.advanced_panel)
    self.chips = FilterChips()
    self.chips.cleared.connect(self.clear_filter)
    self.chips.hide()
    filters.addWidget(self.chips)
    self.filters_widget.setEnabled(False)
    layout.addWidget(self.filters_widget)
    self.live_panel = LiveQuotesPanel(self)
    layout.addWidget(self.live_panel)
    self.banner = StatusLabel("选择数据源后开始查看；所有查询均为只读。")
    layout.addWidget(self.banner)
    self.ownership_alert = StatusLabel()
    self.ownership_alert.hide()
    # [Antigravity] 支持点击告警栏快速直达异常中心
    self.ownership_alert.setCursor(Qt.CursorShape.PointingHandCursor)
    self.ownership_alert.setToolTip("点击跳转到「异常中心」查看问题明细与溯源证据")
    self.ownership_alert.mousePressEvent = lambda event: self.navigation.setCurrentRow(7)
    layout.addWidget(self.ownership_alert)
    self.pages = QStackedWidget()
    trades_page = QWidget()
    trade_layout = QVBoxLayout(trades_page)
    trade_layout.setContentsMargins(0, 0, 0, 0)
    trade_layout.setSpacing(8)
    self.scope_label = QLabel("交易记录 · EXCEL_COMPAT")
    self.scope_label.setObjectName("scope")
    columns = QPushButton("显示列")
    columns.setObjectName("quiet")
    self.table = TradeTable()
    self.table.empty_action.clicked.connect(lambda: self.reset_filters() if self.info and self.current_page is not None else self.reconnect())
    columns.clicked.connect(lambda: self.table.column_menu().exec(columns.mapToGlobal(columns.rect().bottomLeft())))
    self.scope_label.hide()
    columns.setParent(trades_page)
    split = QSplitter(Qt.Orientation.Horizontal)
    self.trade_splitter = split
    surface = QWidget()
    surface.setObjectName("tradeSurface")
    surface_layout = QVBoxLayout(surface)
    surface_layout.setContentsMargins(1, 1, 1, 1)
    surface_layout.addWidget(self.table)
    split.addWidget(surface)
    self.drawer = TradeDrawer()
    self.detail = self.drawer.raw_text
    self.sensitive = self.drawer.sensitive
    self.sensitive.toggled.connect(self.load_provenance)
    self.drawer.closed.connect(self.table.clearSelection)
    split.addWidget(self.drawer)
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 0)
    split.setSizes([900, 390])
    trade_layout.addWidget(split, 1)
    paging = QHBoxLayout()
    self.count_label = QLabel("暂无记录")
    self.count_label.setObjectName("secondary")
    paging.addWidget(self.count_label, 1)
    paging.addWidget(columns)
    self.previous = QPushButton("上一页")
    self.previous.clicked.connect(lambda: self.turn_page(-1))
    paging.addWidget(self.previous)
    self.next = QPushButton("下一页")
    self.next.clicked.connect(lambda: self.turn_page(1))
    paging.addWidget(self.next)
    trade_layout.addLayout(paging)
    self.pages.addWidget(trades_page)
    self.source_details = QPlainTextEdit()
    self.source_details.setReadOnly(True)
    self.source_details.setPlaceholderText("连接数据库后显示文件信息、结构检查和各类数据时间。")
    self.pages.addWidget(self.source_details)
    self.dashboard = DashboardPage()
    self.pages.addWidget(self.dashboard)
    self.asset_page = ReportPage(("当前持仓", "今日盈亏", "实时持仓", "股票汇总", "人员资产", "渠道汇总", "股票归属", "交易回放"), broker_people=True, show_selector=False)
    self.pages.addWidget(self.asset_page)
    self.performance_page = ReportPage(("每日持仓收益", "每月持仓收益", "每月微操收益", "微操配对证据", "月度净投入明细", "完整交易净投入", "清仓收益", "交易回放"))
    self.pages.addWidget(self.performance_page)
    self.charts_page = ChartsPage()
    self.pages.addWidget(self.charts_page)
    self.data_page = ReportPage(("交易回放", "人员划转", "资金事件", "资产调整", "导入批次", "原始记录", "行情状态", "汇率"))
    self.pages.addWidget(self.data_page)
    self.audit_page = ReportPage(("数据异常",))
    self.pages.addWidget(self.audit_page)
    self.reconcile_page = ReportPage(("Excel差异核对",))
    reconcile_button = QPushButton("选择参考 Excel 并核对当前人员…")
    reconcile_button.clicked.connect(self.run_reconciliation)
    self.reconcile_page.layout().insertWidget(0, reconcile_button)
    self.pages.addWidget(self.reconcile_page)
    self.help_page = ReportPage(("指标说明", "取值逻辑", "总览取值逻辑"))
    self.pages.addWidget(self.help_page)
    self.stock_detail = StockDetail()
    self.stock_detail.drill.connect(self.drill_chart)
    self.pages.addWidget(self.stock_detail)
    self.report_pages = (self.asset_page, self.performance_page, self.data_page, self.audit_page, self.help_page, self.dashboard.summaries, self.stock_detail.report)
    for page in (*self.report_pages, self.reconcile_page):
        page.drill.connect(self.drill_record)
        page.export_requested.connect(self.export_financial_report)
        page.provenance_requested.connect(self.report_provenance)
    self.dashboard.drill.connect(self.drill_chart)
    self.charts_page.drill.connect(self.drill_chart)
    self.charts_page.error.connect(self.banner.setText)
    build_system_pages(self)
    from portfolio.ui.earnings_calendar import EarningsCalendarPage
    self.earnings_page = EarningsCalendarPage(self)
    self.pages.addWidget(self.earnings_page)
    from portfolio.ui.distribution_page import HoldingsDistributionPage
    self.distribution_page = HoldingsDistributionPage(self)
    self.pages.addWidget(self.distribution_page)
    self.distribution_page.drill.connect(self.drill_chart)
    self.distribution_page.error.connect(self.banner.setText)
    from portfolio.ui.trend_page import TrendChartsPage
    self.trend_page = TrendChartsPage(self)
    self.pages.addWidget(self.trend_page)
    self.trend_page.drill.connect(self.drill_chart)
    self.trend_page.error.connect(self.banner.setText)
    from portfolio.ui.top_days_page import TopDaysPage
    self.top_days_page = TopDaysPage(self)
    self.pages.addWidget(self.top_days_page)
    self.top_days_page.drill.connect(self.drill_chart)
    self.top_days_page.error.connect(self.banner.setText)
    from portfolio.ui.monthly_realtime_page import MonthlyRealtimePnlPage
    self.monthly_realtime_page = MonthlyRealtimePnlPage(self)
    self.pages.addWidget(self.monthly_realtime_page)
    self.monthly_realtime_page.drill.connect(self.drill_chart)
    self.monthly_realtime_page.error.connect(self.banner.setText)
    # 今日盈亏页内嵌当月/当年走势（复用月度走势详情组件，计算口径一致）
    self.asset_trend = MonthlyRealtimePnlPage(self)
    self.asset_trend.tab_bar.hide()
    self.asset_trend.setMinimumHeight(680)
    self.asset_trend.hide()
    self.asset_trend.drill.connect(self.drill_chart)
    self.asset_trend.error.connect(self.banner.setText)
    chart_layout = self.asset_page.intraday_chart.parentWidget().layout()
    chart_layout.insertWidget(chart_layout.indexOf(self.asset_page.intraday_chart) + 1, self.asset_trend)
    self.asset_page.trend_widget = self.asset_trend
    self.asset_page.trend_mode = 0

    def on_today_trend_changed(index):
        self.asset_page.trend_mode = index
        is_today = self.today_snapshot()
        self.asset_page.intraday_chart.setVisible(is_today and index == 0)
        self.asset_trend.setVisible(is_today and index > 0)
        if index > 0:
            if self.asset_trend.tab_bar.currentIndex() != index - 1:
                self.asset_trend.tab_bar.setCurrentIndex(index - 1)
            self.asset_trend.sync_person_from_main(self.person.currentData())
            if self.analytics:
                self.asset_trend.set_analytics(self.analytics)
    self.live_panel.trend_tabs.currentChanged.connect(on_today_trend_changed)
    self.person.currentIndexChanged.connect(
        lambda *_: self.asset_trend.sync_person_from_main(self.person.currentData()) if self.asset_trend.isVisible() else None)
    from portfolio.ui.ai_assistant_page import AIAssistantPage
    self.ai_assistant_page = AIAssistantPage(self)
    self.pages.addWidget(self.ai_assistant_page)
    from portfolio.ui.ai_stock_analysis_page import AIStockAnalysisPage
    self.stock_analysis_page = AIStockAnalysisPage(self)
    self.pages.addWidget(self.stock_analysis_page)
    from portfolio.ui.monitor_page import MonitoringPage
    self.monitor_page = MonitoringPage(self)
    self.pages.addWidget(self.monitor_page)
    layout.addWidget(self.pages, 1)
    root.addWidget(body, 1)
    self.navigation.routeChanged.connect(self.route_changed)
    self.performance_page.selector.currentTextChanged.connect(self.sync_date_granularity)
    self.asset_page.selector.currentTextChanged.connect(self.sync_date_granularity)
    self.asset_page.selector.currentTextChanged.connect(lambda *_: self.ensure_analytics())
    self.asset_page.selector.currentTextChanged.connect(lambda text: self.asset_reset_button.setVisible(
        self.pages.currentIndex() in (2, 14) or (
            self.pages.currentIndex() == 3 and text in (
                '今日盈亏', '当前持仓', '实时持仓', '股票汇总', '人员资产', '券商资产', '渠道汇总', '股票归属'
            )
        )
    ))
    if self.asset_page.broker_page:
        def on_broker_mode_changed(mode_text):
            if self.pages.currentIndex() != 3:
                return
            if mode_text == '股票归属':
                if self.asset_page.selector.currentText() != '股票归属':
                    self.navigation.select(3, '股票归属')
            else:
                if self.asset_page.selector.currentText() == '股票归属':
                    self.navigation.select(3, '渠道汇总')
        self.asset_page.broker_page.mode.currentTextChanged.connect(on_broker_mode_changed)
    self.table.selectionModel().currentRowChanged.connect(lambda *_: self.load_provenance())
    self.table.clicked.connect(lambda *_: self.load_provenance() if self.drawer.isHidden() else None)
    self.table.frozen.clicked.connect(lambda *_: self.load_provenance() if self.drawer.isHidden() else None)
    for header in (self.table.horizontalHeader(), self.table.frozen.horizontalHeader()):
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(0, Qt.SortOrder.DescendingOrder)
        header.sectionClicked.connect(self.sort)
    for combo in (self.person, self.stock, self.category, self.currency, self.side, self.link_status):
        combo.currentIndexChanged.connect(self.filters_changed)
    self.ledger.currentIndexChanged.connect(self.ledger_changed)
    self.accounts.changed.connect(self.filters_changed)
    self.disabled.toggled.connect(self.disabled_changed)
    for checkbox in (self.audit, self.include_reversed, self.include_reference):
        checkbox.toggled.connect(self.filters_changed)
    def on_start_enabled_toggled(enabled):
        self.start.setEnabled(enabled)
        if not enabled:
            self.start.setDate(QDate(2025, 1, 1))
            self.as_of.setDate(QDate.currentDate())
        elif self.start.date() == self.as_of.date():
            self.start.setDate(QDate(2025, 1, 1))
            self.as_of.setDate(QDate.currentDate())
    self.start_enabled.toggled.connect(on_start_enabled_toggled)
    self.start_enabled.toggled.connect(self.filters_changed)
    self.start.dateChanged.connect(self.filters_changed)
    self.as_of.dateChanged.connect(self.filters_changed)
    self.holding_date.dateChanged.connect(self.filters_changed)
    self.search.textChanged.connect(lambda *_: self.search_timer.start())
    self.source_file.textChanged.connect(lambda *_: self.search_timer.start())
    self.previous.setEnabled(False)
    self.next.setEnabled(False)
    self.export_button.setEnabled(False)
    self.update_filter_chips()


def build_progress_page(self):
    import json
    from importlib.resources import files

    from portfolio.ui.theme import tokens

    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)

    heading = QLabel("开发进度与优化记录")
    heading.setObjectName("sectionTitle")
    layout.addWidget(heading)

    sub = QLabel("按时间倒序记录每次优化的内容与完成情况；时间均为北京时间。")
    sub.setStyleSheet("color: #64748B; font-size: 13px; margin-bottom: 4px;")
    layout.addWidget(sub)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)

    container = QWidget()
    c_layout = QVBoxLayout(container)
    c_layout.setContentsMargins(0, 0, 16, 0)
    c_layout.setSpacing(14)

    try:
        data = json.loads(files("portfolio").joinpath("resources/progress_log.json").read_text(encoding="utf-8"))
    except Exception:
        data = []

    theme = tokens(self)
    for entry in sorted(data, key=lambda item: item.get("time", ""), reverse=True):
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.panel};
                border: 1px solid {theme.border};
                border-radius: 8px;
            }}
        """)
        c_v = QVBoxLayout(card)
        c_v.setContentsMargins(18, 14, 18, 14)
        c_v.setSpacing(8)

        # Header with time and title
        time_str = entry.get("time", "")
        title_str = entry.get("title", "")
        tag_str = entry.get("tag", "优化")

        h_widget = QWidget()
        h_layout = QHBoxLayout(h_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(8)

        time_lbl = QLabel(time_str)
        time_lbl.setStyleSheet(f"color: {theme.brand}; font-weight: bold; font-size: 13px;")
        h_layout.addWidget(time_lbl)

        title_lbl = QLabel(title_str)
        title_lbl.setStyleSheet(f"color: {theme.heading}; font-weight: bold; font-size: 14px;")
        h_layout.addWidget(title_lbl, 1)

        author_str = entry.get("author", "")
        if author_str:
            author_lbl = QLabel(author_str)
            author_lbl.setStyleSheet(f"background-color: {theme.hover}; color: {theme.violet}; border: 1px solid {theme.border}; font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 500;")
            h_layout.addWidget(author_lbl)

        tag_lbl = QLabel(tag_str)
        tag_lbl.setStyleSheet(f"background-color: {theme.selected}; color: {theme.brand}; font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 500;")
        h_layout.addWidget(tag_lbl)

        c_v.addWidget(h_widget)

        # Bullet points
        for d in entry.get("details", []):
            item_lbl = QLabel(f"•  {d}")
            item_lbl.setWordWrap(True)
            item_lbl.setStyleSheet(f"color: {theme.text}; font-size: 13px; line-height: 1.5; margin-left: 4px;")
            c_v.addWidget(item_lbl)

        c_layout.addWidget(card)

    c_layout.addStretch()
    scroll.setWidget(container)
    layout.addWidget(scroll, 1)
    return page


def build_system_pages(self):
    sync_page = QWidget()
    sync_content = QVBoxLayout(sync_page)
    sync_heading = QLabel("数据同步尚未开放")
    sync_heading.setObjectName("sectionTitle")
    sync_content.addWidget(sync_heading)
    sync_label = QLabel("展示页面只读本地数据库；可通过Money3启动脚本的 --excel 或 --api 参数同步本目录交易流水后刷新。")
    sync_label.setWordWrap(True)
    sync_content.addWidget(sync_label)
    sync_content.addStretch()
    self.pages.addWidget(sync_page)

    self.pages.addWidget(build_progress_page(self))
    page = QWidget()
    content = QVBoxLayout(page)
    heading = QLabel("外观与工作区")
    heading.setObjectName("sectionTitle")
    content.addWidget(heading)
    content.addWidget(QLabel("主题"))
    self.theme_selector = QComboBox()
    for title, value in (("浅色（默认）", "light"), ("深色", "dark"), ("跟随系统", "system")):
        self.theme_selector.addItem(title, value)
    self.theme_selector.currentIndexChanged.connect(lambda: self._set_theme(self.theme_selector.currentData()))
    content.addWidget(self.theme_selector)
    self.green_profit = QCheckBox("绿涨红跌（关闭后使用红涨绿跌）")
    self.green_profit.setChecked(True)
    self.green_profit.toggled.connect(lambda checked: self.theme_manager.apply(self.theme, green_profit=checked))
    content.addWidget(self.green_profit)
    source = QPushButton("查看数据源、结构和完整路径")
    source.clicked.connect(lambda: self.navigation.setCurrentRow(1))
    content.addWidget(source)
    content.addWidget(QLabel("计算口径：EXCEL_COMPAT · 财务缺失值保留为 — · 源库只读"))
    content.addStretch()
    self.pages.addWidget(page)
