"""Domestic holdings and account-scoped fund detail."""
from datetime import date
from decimal import Decimal
import csv
import sqlite3

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis, QCategoryAxis
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from portfolio.domestic.holdings_history import daily_values
from portfolio.domestic.holdings_query import holdings
from portfolio.ui.open_holdings_dashboard import HoldingsKpiCard, OpenMvTrendChart
from portfolio.ui.holdings_page import ModernMetricCard
from portfolio.ui.theme import numeric_font
from portfolio.ui.domestic_holdings_dashboard import (
    BLUE, GREEN, RED, STYLE, Contribution, Distribution, HoldingsDelegate, PlatformList, ResponsiveGrid, TrendView,
    closed_metrics, closed_trend_points, holding_metrics, money, panel, percent, trend_combo_points, trend_points,
)

POSITIONS = dict(fund_name="基金名称", fund_code="基金代码", platform="交易平台", fund_type="基金类型",
                 shares="持有份额", nav="最新净值", nav_date="净值日期", market_value="持仓市值（CNY）",
                 pnl="累计盈亏", rate="收益率", weight="持仓占比", status="状态", arrow="详情")
OPEN_POSITIONS = dict(
    fund_name="基金名称", platform="交易平台", fund_type="基金类型", shares="持有份额",
    average="持仓均价", nav="最新单位净值\n（净值日期）", market_value="持仓市值（CNY）",
    cost="持仓成本（CNY）", floating="持有收益（CNY）", floating_rate="收益率",
    weight="持仓占比", arrow="详情",
)
CLOSED_POSITIONS = dict(fund_name="基金名称", platform="交易平台", fund_type="基金类型",
                        buy_amount="累计买入（CNY）", sell_amount="确认赎回（CNY）",
                        transfer_out_amount="转换转出（CNY）",
                        realized="已实现盈亏（CNY）", realized_rate="收益率", holding_days="持有天数",
                        last_confirmed_close_date="清仓日期", arrow="详情")
METRICS = (
    ("market", "持仓总市值（CNY）", money), ("cost", "持仓总成本（CNY）", money),
    ("floating", "持有收益（CNY）", money), ("rate", "持仓收益率", percent),
    ("count", "持有基金数量", lambda count: f"{count} 只"), ("top", "前五大基金集中度", percent),
)
TRADES = dict(confirmation_date="确认日期", platform="交易平台", kind="交易类型", confirmed_shares="确认份额",
              confirmed_amount="确认金额", fee="手续费", confirmed_nav="确认净值", confirmation_status="确认状态",
              parse_error="备注")
NUMERIC = {"pnl", "rate", "shares", "holding_shares", "nav", "market_value", "weight", "confirmed_shares", "confirmed_amount", "fee", "confirmed_nav", "average", "cost", "floating", "floating_rate", "buy_amount", "sell_amount", "transfer_out_amount", "realized", "realized_rate", "holding_days"}


def amount(value):
    return f"{Decimal(value):,.2f}" if value is not None else "—"


def show_in_overview(row):
    """Hide confirmed sub-yuan holdings without assuming missing values are zero."""
    value = row.get("market_value")
    if row.get("current_position_status") != "持有中" or value is None:
        return True
    return Decimal(str(value)) >= Decimal("1")


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()


def combo(items):
    widget = QComboBox()
    for text, value in items:
        widget.addItem(text, value)
    return widget


def card(title, value=None):
    box = QFrame(objectName="domesticCard")
    layout = QVBoxLayout(box)
    label = QLabel(title)
    label.setWordWrap(True)
    layout.addWidget(label)
    if value is not None:
        label = QLabel(value, objectName="domesticValue")
        label.setWordWrap(True)
        layout.addWidget(label)
    return box, layout


class FundTable(QWidget):
    def __init__(self, columns, default_sort, *, expand_rows=False):
        super().__init__()
        self.columns, self.sort = columns, default_sort
        self.open_positions = columns in (OPEN_POSITIONS, CLOSED_POSITIONS)
        self.expand_rows = expand_rows
        self.descending = True
        self.rows = []
        self.visible_rows = []
        self.page = 0
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(columns))
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().sectionClicked.connect(self.change_sort)
        self.table.horizontalHeader().setMinimumSectionSize(72)
        self.table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        if expand_rows:
            self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setAlternatingRowColors(True)
        if self.open_positions:
            self.table.verticalHeader().setDefaultSectionSize(54)
            self.table.horizontalHeader().setMinimumHeight(39)
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(0, 320)
            self.table.setItemDelegate(HoldingsDelegate(self.table))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        footer = QHBoxLayout()
        self.note = QLabel()
        self.size = combo([(f"{n} 条/页", n) for n in (10, 20, 50, 100)])
        self.size.setCurrentIndex(2 if self.open_positions else 1)
        self.size.currentIndexChanged.connect(lambda: self.set_page(0))
        self.prev, self.next = QPushButton("上一页"), QPushButton("下一页")
        self.prev.clicked.connect(lambda: self.set_page(self.page - 1))
        self.next.clicked.connect(lambda: self.set_page(self.page + 1))
        footer.addWidget(self.note, 1)
        for w in (self.size, self.prev, self.next):
            footer.addWidget(w)
        layout.addLayout(footer)

    def change_sort(self, section):
        key = list(self.columns)[section]
        if key != "arrow":
            self.descending = not self.descending if self.sort == key else True
            self.sort = key
            self.set_page(0)

    def set_page(self, page):
        size = self.size.currentData()
        pages = max(1, (len(self.rows) + size - 1) // size)
        self.page = min(max(0, page), pages - 1)
        known = [row for row in self.rows if row.get(self.sort) is not None]
        missing = [row for row in self.rows if row.get(self.sort) is None]
        known.sort(key=lambda row: Decimal(row[self.sort]) if self.sort in NUMERIC else row[self.sort],
                   reverse=self.descending)
        self.visible_rows = (known + missing)[self.page * size:(self.page + 1) * size]
        if self.open_positions and self.visible_rows:
            metrics = self.table.fontMetrics()
            self.table.setColumnWidth(0, max(320, max(metrics.horizontalAdvance(str(row.get("fund_name") or ""))
                                                       for row in self.visible_rows) + 32))
        nav_date = max((row["nav_date"] for row in self.rows if row.get("nav_date")), default=None) if self.open_positions else None
        headers = []
        for key, label in self.columns.items():
            if self.open_positions and key == "nav":
                label = f"最新单位净值\n（{nav_date or '日期未知'}）"
            headers.append(label + (" ↓" if self.descending else " ↑") if key == self.sort else label)
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.visible_rows))
        if self.expand_rows:
            header_height = max(39, self.table.horizontalHeader().sizeHint().height())
            rows_height = len(self.visible_rows) * self.table.verticalHeader().defaultSectionSize()
            self.table.setFixedHeight(max(100, header_height + rows_height + 18))
        for i, row in enumerate(self.visible_rows):
            for j, key in enumerate(self.columns):
                val = row.get(key)
                if key in ("market_value", "confirmed_amount", "fee", "pnl", "cost", "floating", "buy_amount", "sell_amount", "transfer_out_amount", "realized"):
                    val = amount(val)
                elif key in ("weight", "rate", "floating_rate", "realized_rate") and val is not None:
                    val = f"{Decimal(val):.2f}%"
                if self.open_positions:
                    if key == "nav":
                        if val is not None:
                            val = money(Decimal(val), 4)
                            if row.get("nav_date") != nav_date:
                                val += f"\n{row.get('nav_date') or '日期未知'}"
                        else:
                            val = "—"
                    elif key in ("average", "shares"):
                        val = money(val, 4 if key == "average" else 2)
                item = QTableWidgetItem(("查看 ›" if self.open_positions else "›") if key == "arrow" else str(val) if val is not None else "—")
                if self.open_positions:
                    if key in ("floating", "floating_rate", "realized", "realized_rate") and row.get(key) is not None and row[key]:
                        item.setForeground(QBrush(QColor(GREEN if row[key] > 0 else RED)))
                    elif key == "arrow":
                        item.setForeground(QBrush(QColor(BLUE)))
                if key in NUMERIC:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                tooltip = row.get("cash_summary_scope") if key in ("buy_amount", "sell_amount", "transfer_out_amount") else row.get("return_issue") if key in ("pnl", "rate", "average", "cost", "floating", "floating_rate", "realized", "realized_rate") else row.get("issue")
                if key == "sell_amount" and row.get("sell_amount") is None and row.get("transfer_out_amount") is not None:
                    tooltip = "未发现普通赎回；该基金通过转换转出，金额单独列示。"
                if key in ("realized", "realized_rate") and row.get("historical_returns_as_of"):
                    tooltip = f"历史账本截至 {row['historical_returns_as_of']} 的已确认收益；{row.get('confirmation_note') or ''}"
                if key == "fund_name":
                    tooltip = f"{row.get('fund_name', '')} · {row.get('fund_code', '')}"
                if tooltip:
                    item.setToolTip(tooltip)
                self.table.setItem(i, j, item)
        self.note.setText(f"共 {len(self.rows)} 条 · 第 {self.page + 1} / {pages} 页")
        self.prev.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < pages)


class DomesticHoldingsPage(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.data = {}
        self.detail = {}
        self.selected = None
        self.setStyleSheet("""
            QFrame#domesticCard { background: white; border: 1px solid #e2e8f0; border-radius: 10px; }
            QLabel#domesticValue { font-size: 21px; font-weight: 700; color: #1e293b; }
        """ + STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self.splitter.setStyleSheet("QSplitter::handle:horizontal {border-left:1px solid #E2E8F0; margin:4px 1px;} QSplitter::handle:horizontal:hover {background:#93C5FD;}")
        self._drawer_width = 700
        self.splitter.splitterMoved.connect(self._drawer_resized)
        layout.addWidget(self.splitter, 1)
        self.stack = QStackedWidget()
        self.splitter.addWidget(self.stack)
        overview = self.scroll_page()
        self.overview_viewport = self.stack.currentWidget().viewport()
        self.overview_viewport.installEventFilter(self)
        overview.setSpacing(9)
        overview.parentWidget().setObjectName("domesticDashboard")
        mode_bar = QHBoxLayout()
        self.modes = QButtonGroup(self)
        for i, name in enumerate(("持有中", "已清仓")):
            button = QPushButton(name, objectName="domesticMode", checkable=True)
            button.setFixedHeight(34)
            self.modes.addButton(button, i)
            mode_bar.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        self.modes.button(0).setChecked(True)
        self.modes.idClicked.connect(lambda _: self.render_overview(reset=True))
        mode_bar.addStretch()
        overview.addLayout(mode_bar)

        self.metric_area = QWidget()
        self.metric_labels = {}
        self.metric_captions = {}
        self.metric_cards = {}
        cards = []
        for (key, title, _), icon in zip(METRICS, ("¥", "∑", "±", "%", "#", "◔")):
            frame = HoldingsKpiCard(title, icon)
            caption, value = frame.title_lbl, frame.val_lbl
            note = QLabel(objectName="domesticCaption", wordWrap=True)
            frame.layout().itemAt(1).layout().addWidget(note)
            note.hide()
            cards.append(frame)
            self.metric_labels[key] = value, note
            self.metric_captions[key] = caption
            self.metric_cards[key] = frame
        self.metric_grid = ResponsiveGrid(cards, 183, 6)
        self.metric_grid.setContentsMargins(0, 0, 0, 0)
        self.metric_area.setLayout(self.metric_grid)
        overview.addWidget(self.metric_area)

        filter_box = QWidget()
        filter_body = QVBoxLayout(filter_box)
        filter_body.setContentsMargins(0, 0, 0, 0)
        filter_body.setSpacing(4)
        self.platform = combo([("全部平台", "")])
        self.kind = combo([("全部基金类型", "")])
        self.search = QLineEdit(placeholderText="搜索基金名称 / 基金代码")
        self.close_start = QLineEdit(placeholderText="YYYY-MM-DD")
        self.close_end = QLineEdit(placeholderText="YYYY-MM-DD")
        self.close_start.setToolTip("清仓开始日期")
        self.close_end.setToolTip("清仓结束日期")
        reset = QPushButton("重置筛选")
        reset.clicked.connect(self.reset_filters)
        for widget in (self.platform, self.kind):
            widget.setMaximumWidth(190)
        reset.setMaximumWidth(110)
        for widget in (self.platform, self.kind, self.search, reset):
            widget.setFixedHeight(34)
        self.close_dates = QWidget()
        dates_layout = QHBoxLayout(self.close_dates)
        dates_layout.setContentsMargins(0, 0, 0, 0)
        dates_layout.setSpacing(4)
        dates_layout.addWidget(QLabel("清仓日期"))
        for widget in (self.close_start, self.close_end):
            widget.setFixedHeight(34)
            widget.setMinimumWidth(110)
            widget.setMaximumWidth(145)
        dates_layout.addWidget(self.close_start)
        dates_layout.addWidget(self.close_end)
        search_group = QWidget()
        search_layout = QHBoxLayout(search_group)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(6)
        search_layout.addWidget(self.close_dates)
        search_layout.addWidget(self.search, 1)
        self.filter_grid = ResponsiveGrid([self.platform, self.kind, search_group, reset], 140, 4, weights=(1, 1, 3, 1))
        filter_body.addLayout(self.filter_grid)
        self.close_dates.hide()
        for widget in (self.platform, self.kind, self.search, self.close_start, self.close_end):
            signal = widget.textChanged if isinstance(widget, QLineEdit) else widget.currentIndexChanged
            signal.connect(lambda *_: self.render_overview(reset=True))
        mode_bar.addWidget(filter_box, 1)

        self.graph_area = QWidget()
        self.by_platform = PlatformList(lambda name: self.apply_distribution(self.platform, name))
        self.by_type = Distribution("按基金类型分布", lambda name: self.apply_distribution(self.kind, name))
        trend, trend_body = panel()
        trend_header = QHBoxLayout()
        self.history_title = QLabel("持仓市值趋势", objectName="domesticSection")
        trend_header.addWidget(self.history_title, 1)
        self.history_periods = (1, 3, 6, "year", 0)
        self.history_ranges = QButtonGroup(self)
        for i, label in enumerate(("近1月", "近3月", "近6月", "今年", "全部")):
            button = QPushButton(label, checkable=True)
            self.history_ranges.addButton(button, i)
            trend_header.addWidget(button)
        self.history_ranges.button(3).setChecked(True)
        self.history_ranges.idClicked.connect(lambda _: self.render_history())
        trend_body.addLayout(trend_header)
        self.trend_controls_widget = QWidget()
        ctrl_row = QHBoxLayout(self.trend_controls_widget)
        ctrl_row.setContentsMargins(0, 0, 0, 0)
        ctrl_row.setSpacing(6)
        ctrl_row.addWidget(QLabel("收益指标:", objectName="domesticCaption"))
        self.pnl_metric_group = QButtonGroup(self)
        for i, label in enumerate(("累计盈亏", "每日盈亏")):
            btn = QPushButton(label, checkable=True)
            self.pnl_metric_group.addButton(btn, i)
            ctrl_row.addWidget(btn)
        self.pnl_metric_group.button(0).setChecked(True)
        self.pnl_metric_label = ctrl_row.itemAt(0).widget()
        ctrl_row.addSpacing(8)
        self.cum_mode_label = QLabel("累计模式:", objectName="domesticCaption")
        ctrl_row.addWidget(self.cum_mode_label)
        self.cum_mode_group = QButtonGroup(self)
        for i, label in enumerate(("真实累计", "区间归零")):
            btn = QPushButton(label, checkable=True)
            self.cum_mode_group.addButton(btn, i)
            ctrl_row.addWidget(btn)
        self.cum_mode_group.button(0).setChecked(True)
        ctrl_row.addStretch()
        trend_body.addWidget(self.trend_controls_widget)

        self.open_trend_chart = OpenMvTrendChart(currency_symbol="¥")
        trend_body.addWidget(self.open_trend_chart, 1)

        self.trend_legend_widget = QWidget()
        legend_row = QHBoxLayout(self.trend_legend_widget)
        legend_row.setContentsMargins(0, 0, 0, 0)
        legend_row.setSpacing(14)
        legend_row.addStretch()
        self.legend_mv_btn = QPushButton("● 持仓市值 (左轴)", checkable=True)
        self.legend_mv_btn.setChecked(True)
        self.legend_pnl_btn = QPushButton("● 累计盈亏 (右轴)", checkable=True)
        self.legend_pnl_btn.setChecked(True)
        for btn, color in ((self.legend_mv_btn, "#2563EB"), (self.legend_pnl_btn, "#F59E0B")):
            btn.setStyleSheet(f"QPushButton {{ border:0; background:transparent; color:{color}; font-size:11px; padding:2px 4px; }}"
                              f"QPushButton:!checked {{ color:#94A3B8; }}")
            legend_row.addWidget(btn)
        legend_row.addStretch()
        trend_body.addWidget(self.trend_legend_widget)

        self.pnl_metric_group.idClicked.connect(self._on_trend_mode_changed)
        self.cum_mode_group.idClicked.connect(self._on_trend_mode_changed)
        self.legend_mv_btn.clicked.connect(self._on_trend_legend_toggled)
        self.legend_pnl_btn.clicked.connect(self._on_trend_legend_toggled)

        self.history_plot = TrendView()
        trend_body.addWidget(self.history_plot, 1)
        self.history_note = QLabel("", objectName="domesticCaption", wordWrap=True)
        trend_body.addWidget(self.history_note)
        for widget in (self.by_platform, self.by_type, trend):
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            widget.setMinimumWidth(0)
        self.graph_grid = ResponsiveGrid([self.by_platform, self.by_type, trend], 345, 3, weights=(4, 3, 4))
        self.graph_grid.setContentsMargins(0, 0, 0, 0)
        self.graph_area.setLayout(self.graph_grid)
        overview.addWidget(self.graph_area)
        self.history_scope = []

        self.contribution = Contribution(self.open_fund)
        overview.addWidget(self.contribution)

        self.open_box, body = panel()
        table_header = QHBoxLayout()
        self.open_title = QLabel("基金持仓明细", objectName="domesticSection")
        table_header.addWidget(self.open_title, 1)
        self.profit_filters = QButtonGroup(self)
        for i, name in enumerate(("全部", "盈利", "亏损")):
            button = QPushButton(name, checkable=True)
            self.profit_filters.addButton(button, i)
            table_header.addWidget(button)
        self.profit_filters.button(0).setChecked(True)
        self.profit_filters.idClicked.connect(lambda _: self.render_overview(reset=True))
        body.addLayout(table_header)
        self.open_positions = FundTable(OPEN_POSITIONS, "market_value", expand_rows=True)
        self.open_positions.table.cellClicked.connect(
            lambda row, _: self.open_fund(self.open_positions.visible_rows[row]))
        body.addWidget(self.open_positions, 1)
        self.open_total = QLabel(wordWrap=True)
        body.addWidget(self.open_total)
        overview.addWidget(self.open_box, 1)

        self.closed_box, body = panel()
        closed_header = QHBoxLayout()
        self.closed_title = QLabel("已清仓基金明细", objectName="domesticSection")
        closed_header.addWidget(self.closed_title, 1)
        self.closed_filters = QButtonGroup(self)
        for i, name in enumerate(("全部", "盈利", "亏损")):
            button = QPushButton(name, checkable=True)
            self.closed_filters.addButton(button, i)
            closed_header.addWidget(button)
        self.closed_filters.button(0).setChecked(True)
        self.closed_filters.idClicked.connect(lambda _: self.render_overview(reset=True))
        body.addLayout(closed_header)
        self.positions = FundTable(CLOSED_POSITIONS, "realized", expand_rows=True)
        self.positions.table.cellClicked.connect(lambda row, _: self.open_fund(self.positions.visible_rows[row]))
        body.addWidget(self.positions, 1)
        self.closed_total = QLabel(wordWrap=True)
        body.addWidget(self.closed_total)
        overview.addWidget(self.closed_box, 1)
        self.closed_box.hide()
        self.sources = QLabel(objectName="domesticCaption", wordWrap=True)
        overview.addWidget(self.sources)

        detail = self.scroll_page(drawer=True)
        detail.parentWidget().setObjectName("domesticDashboard")
        bar = QHBoxLayout()
        back = QPushButton("✕")
        back.setToolTip("关闭基金详情")
        back.setFixedSize(32, 32)
        back.clicked.connect(self.back)
        self.breadcrumb = QLabel()
        self.breadcrumb.setWordWrap(True)
        bar.addWidget(self.breadcrumb, 1)
        bar.addWidget(back)
        detail.addLayout(bar)
        self.detail_identity = QLabel("基金详情", objectName="domesticSection")
        detail.addWidget(self.detail_identity)
        detail_metrics = []
        self.detail_metrics = {}
        for key, title in (("shares", "当前持有"), ("cost", "持仓成本"), ("nav", "最新净值"),
                           ("market", "持仓市值"), ("floating", "浮动盈亏"), ("pnl", "累计盈亏")):
            metric = ModernMetricCard(title, compact=True)
            metric.setFixedHeight(72)
            metric.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            metric.layout().setContentsMargins(6, 7, 6, 7)
            metric.val_lbl.setFont(numeric_font(12, bold=True))
            self.detail_metrics[key] = metric
            detail_metrics.append(metric)
        self.detail_metric_grid = ResponsiveGrid(detail_metrics, 100, 6)
        detail.addLayout(self.detail_metric_grid)
        self.detail_tabs = QTabWidget()
        detail.addWidget(self.detail_tabs)
        trade_tab = QWidget()
        trade_layout = QVBoxLayout(trade_tab)
        trade_layout.setContentsMargins(0, 8, 0, 0)
        self.detail_tabs.addTab(trade_tab, "交易记录")
        middle = QVBoxLayout()
        box, body = card("交易明细")
        filter_row = QHBoxLayout()
        self.tx_kind = combo([("全部交易类型", "")])
        self.start, self.end = (QLineEdit(placeholderText=label) for label in ("确认起日 YYYY-MM-DD", "确认止日 YYYY-MM-DD"))
        for widget in (self.start, self.end, self.tx_kind):
            filter_row.addWidget(widget)
            signal = widget.textChanged if isinstance(widget, QLineEdit) else widget.currentIndexChanged
            signal.connect(self.render_trades)
        body.addLayout(filter_row)
        trade_actions = QHBoxLayout()
        trade_actions.addStretch()
        export_trades = QPushButton("导出 CSV")
        export_trades.clicked.connect(self.export_trades)
        trade_actions.addWidget(export_trades)
        body.addLayout(trade_actions)
        self.trade_source_note = QLabel("原始交易记录未提供确认净值。")
        self.trade_source_note.setWordWrap(True)
        self.trade_source_note.hide()
        body.addWidget(self.trade_source_note)
        self.trades = FundTable(TRADES, "confirmation_date")
        body.addWidget(self.trades)
        middle.addWidget(box, 1)
        box, body = card("持仓概览")
        self.fund_overview = QLabel()
        self.fund_overview.setWordWrap(True)
        body.addWidget(self.fund_overview)
        body.addStretch()
        middle.addWidget(box)
        trade_layout.addLayout(middle)
        holdings_tab = QWidget()
        holdings_layout = QVBoxLayout(holdings_tab)
        holdings_layout.setContentsMargins(0, 8, 0, 0)
        self.detail_tabs.addTab(holdings_tab, "持仓分析")
        box, body = card("份额 / 市值变化")
        range_row = QHBoxLayout()
        self.trend_range = combo([("近6个月", 6), ("近1年", 12), ("全部", 0), ("自定义", -1)])
        self.trend_range.setCurrentIndex(1)
        self.trend_start, self.trend_end = (QLineEdit(placeholderText=label) for label in ("起月 YYYY-MM", "止月 YYYY-MM"))
        for widget in (self.trend_range, self.trend_start, self.trend_end):
            range_row.addWidget(widget)
            signal = widget.textChanged if isinstance(widget, QLineEdit) else widget.currentIndexChanged
            signal.connect(self.render_trend)
        body.addLayout(range_row)
        self.trend_body = QVBoxLayout()
        body.addLayout(self.trend_body)
        holdings_layout.addWidget(box)
        pnl_tab = QWidget()
        pnl_layout = QVBoxLayout(pnl_tab)
        pnl_layout.setContentsMargins(0, 8, 0, 0)
        pnl_cards = QHBoxLayout()
        self.pnl_metrics = {}
        for key, title in (("floating", "浮动盈亏（CNY）"), ("realized", "已实现盈亏（CNY）"), ("pnl", "累计盈亏（CNY）")):
            metric = ModernMetricCard(title, compact=True)
            metric.setFixedHeight(72)
            self.pnl_metrics[key] = metric
            pnl_cards.addWidget(metric, 1)
        pnl_layout.addLayout(pnl_cards)
        self.pnl_note = QLabel(wordWrap=True)
        pnl_layout.addWidget(self.pnl_note)
        pnl_layout.addStretch()
        self.detail_tabs.addTab(pnl_tab, "收益分析")
        monthly_tab = QWidget()
        monthly_layout = QVBoxLayout(monthly_tab)
        monthly_layout.setContentsMargins(0, 8, 0, 0)
        monthly_layout.addWidget(QLabel("按月查看历史持有份额和市值；缺少数据时显示未核算。"))
        self.monthly = FundTable(dict(month="月份", holding_shares="月末份额", market_value="月末市值（CNY）"), "month")
        monthly_layout.addWidget(self.monthly)
        self.detail_tabs.addTab(monthly_tab, "月度表现")
        self.render_trend()

    def scroll_page(self, *, drawer=False):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12) if drawer else layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(content)
        if drawer:
            self.drawer = scroll
            self.drawer.setMinimumWidth(360)
            self.drawer.setMaximumWidth(900)
            self.drawer.setStyleSheet("QScrollArea {background:white; border:1px solid #E2E8F0; border-radius:10px;}")
            self.splitter.addWidget(self.drawer)
            self.drawer.hide()
        else:
            self.stack.addWidget(scroll)
        return layout

    def _drawer_resized(self, *_):
        sizes = self.splitter.sizes()
        if sizes and hasattr(self, "graph_grid"):
            self._reflow_overview(max(0, sizes[0] - 30))
        if len(sizes) == 2 and sizes[1] > 0:
            self._drawer_width = sizes[1]
            if hasattr(self, "detail_metric_grid"):
                self.detail_metric_grid.reflow(max(600, sizes[1] - 36))

    def _reflow_overview(self, width):
        self.metric_grid.reflow(width)
        self.filter_grid.reflow(width)
        drawer_open = hasattr(self, "drawer") and not self.drawer.isHidden()
        self.by_type.set_compact(drawer_open)
        for button in self.history_ranges.buttons():
            button.setMinimumWidth(0)
            button.setMaximumWidth(38 if drawer_open else 16777215)
        self.graph_grid.reflow(max(width, 3 * self.graph_grid.width_per_card) if drawer_open else width)

    def eventFilter(self, watched, event):
        if (watched is getattr(self, "overview_viewport", None) and event.type() == QEvent.Type.Resize
                and hasattr(self, "graph_grid")):
            width = max(0, event.size().width() - 8)
            self._reflow_overview(width)
        return super().eventFilter(watched, event)

    def reload(self):
        account, fund = self.selected or ("", "")
        self.message.setText("正在读取国内资产…")

        def read(_cancel):
            try:
                data = holdings(account, fund)
                if not account:
                    active = [row for row in data["rows"] if row["current_position_status"] == "持有中"]
                    try:
                        data["daily_values"] = daily_values(data["rows"])
                    except (ValueError, ArithmeticError, OSError, sqlite3.Error):
                        data["history_error"] = True
                return data
            except (ValueError, ArithmeticError, OSError, sqlite3.Error):
                return None

        def ready(data):
            if (account, fund) != (self.selected or ("", "")):
                return
            if data is None:
                self.message.setText("无法读取国内资产，请刷新重试。")
                return
            self.message.setText("")
            if account:
                self.detail = data
                self.render_detail()
            else:
                self.data = data
                as_of = next((row["valuation_date"] for row in data["rows"]), "—")
                self.sources.setText(f"估值日期 {as_of} · 只读数据源 {data['raw_path']} · {data['calc_path']}")
                self.choices(self.platform, "全部平台", data["rows"], "platform")
                self.choices(self.kind, "全部基金类型", data["rows"], "fund_type")
                self.render_overview()

        self.host._submit("domestic_holdings", read, ready)

    @staticmethod
    def choices(widget, label, rows, key):
        current = widget.currentData()
        widget.blockSignals(True)
        widget.clear()
        widget.addItem(label, "")
        for val in sorted({r[key] for r in rows if r.get(key)}):
            widget.addItem(val, val)
        widget.setCurrentIndex(max(0, widget.findData(current)))
        widget.blockSignals(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "graph_grid"):
            sizes = self.splitter.sizes()
            available = max(0, (sizes[0] if not self.drawer.isHidden() and sizes else event.size().width()) - 50)
            self._reflow_overview(available)
            self.detail_metric_grid.reflow(max(600, self.drawer.width() - 36) if not self.drawer.isHidden() else available)

    def reset_filters(self):
        for control in (self.platform, self.kind, self.search, self.close_start, self.close_end):
            control.blockSignals(True)
        self.platform.setCurrentIndex(0)
        self.kind.setCurrentIndex(0)
        self.search.clear()
        self.close_start.clear()
        self.close_end.clear()
        for control in (self.platform, self.kind, self.search, self.close_start, self.close_end):
            control.blockSignals(False)
        self.profit_filters.button(0).setChecked(True)
        self.closed_filters.button(0).setChecked(True)
        self.render_overview(reset=True)

    def apply_distribution(self, control, name):
        control.setCurrentIndex(0 if control.currentData() == name else control.findData(name))

    def render_overview(self, reset=False):
        history_rows = self.data.get("rows", [])
        all_rows = [row for row in history_rows if show_in_overview(row)]
        opened = self.modes.checkedId() == 0
        self.close_dates.setVisible(not opened)
        for i, status in enumerate(("持有中", "已清仓")):
            count = sum(row["current_position_status"] == status for row in all_rows)
            self.modes.button(i).setText(f"{status}（{count}）")
        self.open_box.setVisible(opened)
        self.closed_box.setVisible(not opened)
        query = self.search.text().strip().casefold()
        rows = [row for row in all_rows
                if row["current_position_status"] == ("持有中" if opened else "已清仓")
                and all(not control.currentData() or row[key] == control.currentData()
                        for control, key in ((self.platform, "platform"), (self.kind, "fund_type")))
                and query in (row["fund_code"] + " " + row["fund_name"]).casefold()]
        if not opened and (self.close_start.text().strip() or self.close_end.text().strip()):
            start, end = self.close_start.text().strip(), self.close_end.text().strip()
            rows = [row for row in rows if row.get("last_confirmed_close_date")
                    and (not start or row["last_confirmed_close_date"] >= start)
                    and (not end or row["last_confirmed_close_date"] <= end)]
        if not opened:
            prepared = []
            for row in rows:
                realized = row.get("last_confirmed_total_pnl")
                rate = row.get("last_confirmed_rate")
                contributed = row.get("last_confirmed_contributed")
                if realized is None and not row.get("return_issue"):
                    realized, rate, contributed = row.get("pnl"), row.get("rate"), row.get("contributed")
                prepared.append(dict(row, realized=Decimal(realized) if realized is not None else None,
                                     realized_rate=Decimal(rate) if rate is not None else None,
                                     contributed=Decimal(contributed) if contributed is not None else None))
            profit = [r for r in prepared if r["realized"] is not None and r["realized"] > 0]
            loss = [r for r in prepared if r["realized"] is not None and r["realized"] < 0]
            for i, (name, group) in enumerate(zip(("全部", "盈利", "亏损"), (prepared, profit, loss))):
                self.closed_filters.button(i).setText(f"{name}（{len(group)}）")
            shown = (prepared, profit, loss)[self.closed_filters.checkedId()]
            self.closed_title.setText(f"已清仓基金明细（{len(shown)} 条）")
            self.positions.rows = shown
            self.positions.set_page(0 if reset else self.positions.page)
            self.by_platform.render(prepared, closed=True)
            absolute = sum((abs(r["realized"]) for r in prepared if r["realized"] is not None), Decimal(0))
            self.by_type.render(prepared, "fund_type", absolute or None, value_key="realized", absolute=True)
            self.contribution.render(prepared, closed=True)
            self.history_title.setText("累计已实现盈亏趋势")
            self.closed_scope = prepared
            self.render_history()
            closed_titles = ("历史已实现盈亏（CNY）", "已清仓基金数量", "已导入买入（CNY）", "已导入赎回（CNY）", "基金成本加权收益率", "已确认平均持有时长")
            stats = closed_metrics(prepared)
            self.closed_total.setText(f"合计　{len(prepared)} 条　买入 {money(stats['buy'])}　赎回 {money(stats['sell'])}　历史收益 {money(stats['realized'])} CNY")
            closed_fund_count = len({r["fund_code"] for r in prepared if not r.get("is_historical_baseline")})
            numbers = (stats['realized'], closed_fund_count, stats['buy'], stats['sell'], stats['rate'], stats['holding_days'])
            for (key, _, _), title, number, icon in zip(METRICS, closed_titles, numbers, ("¥", "#", "买", "卖", "%", "◷")):
                self.metric_captions[key].setText(title)
                self.metric_cards[key].icon_badge.setText(icon)
                value, note = self.metric_labels[key]
                value.setText("—" if number is None else f"{number} 只" if key == "cost" else f"{number:.0f} 天" if key == "top" else percent(number) if key == "count" else money(number))
                note.setText(f"历史可核对 {stats['profit_count']}/{len(prepared)} 条" if key == "market" else
                             f"已确认 {stats['day_count']}/{len(prepared)} 条" if key == "top" else "")
                note.setVisible((key == "market" and stats['profit_count'] < len(prepared)) or
                                (key == "top" and stats['day_count'] < len(prepared)))
                self.metric_cards[key].setToolTip(
                    "按可核对基金的成本加权，基金间转换转入会计入该基金成本，不能当作账户资金收益率。" if key == "count" else
                    "确认流水汇总；转换进出和分红单独统计，可能不含缺失历史。" if key in ("floating", "rate") else
                    "仅统计历史账本可核对的记录。")
                value.setProperty("sign", "positive" if key == "market" and number is not None and number > 0 else "negative" if key == "market" and number is not None and number < 0 else "")
                self.metric_cards[key].set_value(value.text(), GREEN if key == "market" and number is not None and number > 0 else RED if key == "market" and number is not None and number < 0 else "#0F172A")
                value.style().unpolish(value); value.style().polish(value)
            return

        prepared, summary = holding_metrics(rows)
        profits = [row for row in prepared if row["floating"] is not None and row["floating"] > 0]
        losses = [row for row in prepared if row["floating"] is not None and row["floating"] < 0]
        for i, (name, group) in enumerate(zip(("全部", "盈利", "亏损"), (prepared, profits, losses))):
            self.profit_filters.button(i).setText(f"{name}（{len(group)}）")
        # The profit/loss switch applies to the table; portfolio cards and distributions
        # continue to describe the selected platform, fund type and search scope.
        shown = (prepared, profits, losses)[self.profit_filters.checkedId()]
        self.open_title.setText(f"基金持仓明细（{len(shown)} 条）")
        self.open_positions.rows = shown
        self.open_positions.set_page(0 if reset else self.open_positions.page)
        money_funds = getattr(self.data.get("daily_values", {}), "money_funds", set())
        self.history_scope = [row for row in history_rows
                              if row["fund_code"] not in money_funds
                              and not row.get("is_historical_baseline")
                              and all(not control.currentData() or row[key] == control.currentData()
                                     for control, key in ((self.platform, "platform"), (self.kind, "fund_type")))
                              and query in (row["fund_code"] + " " + row["fund_name"]).casefold()]
        self.by_platform.render(prepared)
        self.by_type.render(prepared, "fund_type", summary["market"])
        self.contribution.render(prepared)
        self.history_title.setText("持仓市值趋势")
        self.render_history()
        for (key, title, formatter), icon in zip(METRICS, ("¥", "∑", "±", "%", "#", "◔")):
            self.metric_captions[key].setText(title)
            self.metric_cards[key].icon_badge.setText(icon)
            value, note = self.metric_labels[key]
            metric = summary[key]
            value.setText(formatter(metric))
            sign = ("positive" if metric > 0 else "negative") if key in ("floating", "rate") and metric else ""
            self.metric_cards[key].set_value(value.text(), GREEN if sign == "positive" else RED if sign == "negative" else "#0F172A",
                badge_bg="#DCFCE7" if sign == "positive" else "#FEE2E2" if sign == "negative" else "#EFF6FF",
                badge_fg=GREEN if sign == "positive" else RED if sign == "negative" else BLUE)
            value.setProperty("sign", sign)
            value.style().unpolish(value)
            value.style().polish(value)
            covered = summary["valued"] if key in ("market", "top") else summary["matched"]
            partial = key != "count" and covered < len(rows)
            note.setText(f"部分数据未核算 · {covered}/{len(rows)} 条" if partial else "")
            note.setVisible(partial)
        _, totals = holding_metrics(shown)
        self.open_total.setText(
            f"合计　持仓市值 {money(totals['market'])}　持仓成本 {money(totals['cost'])}　"
            f"持有收益 {money(totals['floating'])}　收益率 {percent(totals['rate'])}"
            + ("　· 部分数据未核算" if totals["matched"] < len(shown) else "")
        )

    def _on_trend_mode_changed(self, _id=None):
        is_closed = self.modes.checkedId() == 1
        is_daily = (not is_closed) and (self.pnl_metric_group.checkedId() == 1)
        cum_mode = "zeroed" if self.cum_mode_group.checkedId() == 1 else "real"
        self.cum_mode_label.setEnabled(not is_daily)
        for btn in self.cum_mode_group.buttons():
            btn.setEnabled(not is_daily)
        if is_closed:
            self.render_history()
            return
        self.legend_pnl_btn.setText("● 每日盈亏 (右轴)" if is_daily else "● 累计盈亏 (右轴)")
        self.open_trend_chart.set_mode(pnl_metric="daily" if is_daily else "cum", cum_mode=cum_mode)

    def _on_trend_legend_toggled(self):
        self.open_trend_chart.set_visibility(
            show_mv=self.legend_mv_btn.isChecked(),
            show_pnl=self.legend_pnl_btn.isChecked(),
        )

    def render_history(self):
        if self.modes.checkedId() == 1:
            self.trend_controls_widget.show()
            self.pnl_metric_label.hide()
            for btn in self.pnl_metric_group.buttons():
                btn.hide()
            self.cum_mode_label.setEnabled(True)
            for btn in self.cum_mode_group.buttons():
                btn.setEnabled(True)
            self.open_trend_chart.hide()
            self.trend_legend_widget.hide()
            rows = getattr(self, "closed_scope", [])
            cutoff = date.fromisoformat(rows[0]["valuation_date"]) if rows else date.today()
            zeroed = self.cum_mode_group.checkedId() == 1
            period = self.history_periods[self.history_ranges.checkedId()]
            points, covered = closed_trend_points(rows, period, cutoff, zeroed=zeroed)
            real_points = closed_trend_points(rows, period, cutoff, zeroed=False)[0] if zeroed else points
            self.history_plot.render(
                points,
                calendar_spacing=True,
                step_series=True,
                value_label="区间累计(归零)" if zeroed else "真实累计盈亏",
                real_values=dict(real_points),
                cum_mode="zeroed" if zeroed else "real",
            )
            self.history_plot.setVisible(bool(points))
            self.history_note.setText((f"按确认日累计，仅包含有逐笔收益依据的 {covered}/{len(rows)} 条记录。" if points else
                                       f"当前范围没有可展示的收益确认事件；有逐笔依据 {covered}/{len(rows)} 条。"))
            self.history_note.show()
            return
        self.history_note.hide()
        self.history_plot.hide()
        self.trend_controls_widget.show()
        self.pnl_metric_label.show()
        for btn in self.pnl_metric_group.buttons():
            btn.show()
        is_daily = self.pnl_metric_group.checkedId() == 1
        self.cum_mode_label.setEnabled(not is_daily)
        for btn in self.cum_mode_group.buttons():
            btn.setEnabled(not is_daily)
        self.open_trend_chart.show()
        self.trend_legend_widget.show()
        cutoff = self.history_scope[0]["valuation_date"] if self.history_scope else None
        history = self.data.get("daily_values", {})
        combo_points = trend_combo_points(
            history, self.history_scope,
            self.history_periods[self.history_ranges.checkedId()], date.fromisoformat(cutoff),
        ) if cutoff else []
        self.open_trend_chart.set_data(combo_points)
        if getattr(history, "calendar_incomplete", False):
            self.message.setText("部分日期的交易日历尚未覆盖，按工作日显示，请核对节假日。")
        elif self.message.text() == "部分日期的交易日历尚未覆盖，按工作日显示，请核对节假日。":
            self.message.clear()

    def open_fund(self, row):
        self.selected = row["account_id"], row["fund_code"]
        self.detail = {}
        self.start.clear()
        self.end.clear()
        self.trades.sort, self.trades.descending = "confirmation_date", True
        self.trend_range.setCurrentIndex(1)
        self.trend_start.clear()
        self.trend_end.clear()
        self.detail_tabs.setCurrentIndex(0)
        self.render_detail()
        if self.drawer.isHidden():
            self.drawer.show()
            width = max(360, min(900, self._drawer_width, int(self.width() * .6)))
            self.splitter.setSizes([max(200, self.width() - width), width])
        self._drawer_resized()
        self.detail_metric_grid.reflow(max(600, self.drawer.width() - 36))
        self.drawer.verticalScrollBar().setValue(0)
        self.reload()

    def back(self):
        sizes = self.splitter.sizes()
        if len(sizes) == 2 and sizes[1] > 0:
            self._drawer_width = sizes[1]
        self.selected = None
        self.drawer.hide()
        self._drawer_resized()
        self.message.clear()

    def render_detail(self):
        self.choices(self.tx_kind, "全部交易类型", self.detail.get("transactions", []), "kind")
        self.render_trades()
        self.render_trend()
        self.monthly.rows = self.detail.get("history", [])
        self.monthly.set_page(0)
        rows = self.detail.get("rows", [])
        self.fund_overview.clear()
        if not rows:
            self.breadcrumb.setText("国内资产 / 持仓管理 / 持仓明细")
            self.detail_identity.setText("正在加载基金详情…")
            for metric in (*self.detail_metrics.values(), *self.pnl_metrics.values()):
                metric.set_content("—")
            self.pnl_note.clear()
            return
        row = rows[0]
        self.breadcrumb.setText(f"国内资产 / 持仓管理 / {row['fund_name']}")
        self.detail_identity.setText(f"{row['fund_code']}  {row['fund_name']}  ·  {row['platform']}")
        value = Decimal(row["market_value"]) if row.get("market_value") is not None else None
        cost = Decimal(row["cost"]) if row.get("cost") is not None else None
        floating = value - cost if value is not None and cost is not None else None
        realized_raw = row.get("last_confirmed_realized_pnl") if row.get("last_confirmed_realized_pnl") is not None else row.get("realized_pnl") if not row.get("return_issue") else None
        pnl_raw = row.get("last_confirmed_total_pnl") if row.get("last_confirmed_total_pnl") is not None else row.get("pnl") if not row.get("return_issue") else None
        realized = Decimal(realized_raw) if realized_raw is not None else None
        pnl = Decimal(pnl_raw) if pnl_raw is not None else None
        data = {"shares": amount(row.get("shares")), "cost": amount(cost),
                "nav": row.get("nav") or "—", "market": amount(value),
                "floating": amount(floating), "pnl": amount(pnl)}
        for key, metric in self.detail_metrics.items():
            numeric = floating if key == "floating" else pnl if key == "pnl" else None
            color = GREEN if numeric is not None and numeric > 0 else RED if numeric is not None and numeric < 0 else None
            metric.set_content(data[key], row.get("nav_date") or "净值缺失" if key == "nav" else "", color)
            metric.val_lbl.setStyleSheet(f"color:{color or '#0F172A'};")
        for key, number in (("floating", floating), ("realized", realized), ("pnl", pnl)):
            color = GREEN if number is not None and number > 0 else RED if number is not None and number < 0 else None
            self.pnl_metrics[key].set_content(amount(number) if number is not None else "—", "", color)
            self.pnl_metrics[key].val_lbl.setStyleSheet(f"color:{color or '#0F172A'};")
        note = row.get("confirmation_note") or row.get("return_issue") or "累计盈亏包含已实现收益及现金分红；浮动盈亏为市值减剩余持仓成本。"
        historical = f"历史收益截至 {row['historical_returns_as_of']}；" if row.get("historical_returns_as_of") and not row.get("confirmation_note") else ""
        self.pnl_note.setText(historical + note)
        self.fund_overview.setText(
            f"平台  {row['platform']}\n账户  {row['account_name']}\n基金代码  {row['fund_code']}\n"
            f"交易资料覆盖截至  {row['coverage_through'] or '—'}\n{row['status']}\n"
            f"净值状态  {row['nav_status']}\n"
            f"最早可确认持有日  {row.get('first_confirmed_holding_date') or '—'}\n"
            f"首次确认买入或转入日  {row.get('first_trade_date') or '—'}\n"
            f"最近确认清仓日  {row['last_confirmed_close_date'] or '—'}\n\n"
            f"当前份额  {row['shares'] or '—'}\n当前市值  {amount(row['market_value'])}\n"
            f"累计买入份额  {row.get('buy_shares', '—')}\n累计卖出份额  {row.get('sell_shares', '—')}\n"
            f"确认买入金额  {amount(row.get('buy_amount'))}\n确认赎回金额  {amount(row.get('sell_amount'))}\n"
            f"转换转入金额  {amount(row.get('transfer_in_amount'))}\n转换转出金额  {amount(row.get('transfer_out_amount'))}\n"
            f"历史现金分红  {amount(row.get('dividend_amount'))}\n交易手续费  {amount(row.get('fee_amount'))}\n"
            f"剩余持仓成本  {amount(row.get('cost'))}\n已实现盈亏  {amount(row.get('realized_pnl'))}\n"
            + (f"最近确认已实现盈亏  {amount(row['last_confirmed_realized_pnl'])}\n" if row.get("last_confirmed_realized_pnl") is not None else "")
            + (f"{row['confirmation_note']}\n" if row.get("confirmation_note") else "")
            + (f"流水说明  {row['cash_summary_scope']}\n" if row.get("cash_summary_scope") else "")
            + (f"流水异常  {row['cash_summary_issue']}\n" if row.get("cash_summary_issue") else "")
            + f"收益说明  {row.get('return_issue') or '累计盈亏 ÷ 累计买入金额（非年化）'}\n"
            f"累计分红  {amount(row.get('cash_dividend'))}\n"
            f"数据说明  {row.get('issue') or '—'}\n"
            f"最近可核对份额  {row.get('last_confirmed_shares', '—')}  {row.get('last_confirmed_date', '')}\n"
            f"累计净值  {row.get('accumulated_nav') or '—'}  ·  来源  {row.get('nav_source') or '—'}")

    def render_trades(self, *_):
        start, end = self.start.text().strip(), self.end.text().strip()
        kind = self.tx_kind.currentData()
        platform = next((r["platform"] for r in self.detail.get("rows", [])), "—")
        records = self.detail.get("transactions", [])
        has_nav = any(r.get("confirmed_nav") is not None for r in records)
        show_status = any(r.get("confirmation_status") not in (None, "", "成功") for r in records)
        self.trade_source_note.setVisible(bool(records) and not has_nav)
        for field, visible in (("confirmed_nav", has_nav), ("confirmation_status", show_status)):
            self.trades.table.setColumnHidden(list(TRADES).index(field), not visible)
            if not visible and self.trades.sort == field:
                self.trades.sort, self.trades.descending = "confirmation_date", True
        self.trades.rows = [dict(r, platform=platform) for r in records
                            if (not kind or r["kind"] == kind) and ((not start and not end) or
                                r["confirmation_date"] and (not start or r["confirmation_date"] >= start)
                                and (not end or r["confirmation_date"] <= end))]
        self.trades.set_page(0)

    def export_trades(self):
        if not self.trades.rows:
            return
        code = self.selected[1] if self.selected else "基金"
        path, _ = QFileDialog.getSaveFileName(self, "导出交易记录", f"{code}_交易明细.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            fields = [key for index, key in enumerate(TRADES) if not self.trades.table.isColumnHidden(index)]
            writer.writerow([TRADES[key] for key in fields])
            for row in self.trades.rows:
                writer.writerow([row.get(key) if row.get(key) is not None else "" for key in fields])

    @staticmethod
    def add_chart(layout, chart):
        view = QChartView(chart)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        view.setMinimumHeight(220)
        layout.addWidget(view)

    def render_trend(self, *_):
        clear(self.trend_body)
        period = self.trend_range.currentData()
        for field in (self.trend_start, self.trend_end):
            field.setVisible(period == -1)
        rows = self.detail.get("history", [])
        if period == -1:
            start, end = self.trend_start.text(), self.trend_end.text()
            rows = [r for r in rows if (not start or r["month"] >= start) and (not end or r["month"] <= end)]
        elif period:
            rows = rows[-period:]
        chart = QChart()
        axis = QCategoryAxis()
        axis.setLabelsPosition(QCategoryAxis.AxisLabelsPosition.AxisLabelsPositionOnValue)
        axis.setStartValue(-1)
        for i, row in enumerate(rows):
            if i % max(1, len(rows) // 6) == 0 or i == len(rows) - 1:
                axis.append(row["month"], i)
        axis.setRange(-.2, max(1, len(rows) - 1) + .2)
        chart.addAxis(axis, Qt.AlignmentFlag.AlignBottom)
        for key, label, side in (("holding_shares", "份额", Qt.AlignmentFlag.AlignLeft),
                                 ("market_value", "市值（CNY）", Qt.AlignmentFlag.AlignRight)):
            values = [float(r[key]) for r in rows if r[key] is not None]
            if not values:
                continue
            y_axis = QValueAxis()
            y_axis.setTitleText(label)
            y_axis.setRange(min(0, min(values)), max(1, max(values)))
            chart.addAxis(y_axis, side)
            line = None
            for i, row in enumerate(rows):
                if row[key] is None:
                    line = None
                    continue
                if line is None:
                    line = QLineSeries(name=label)
                    line.setPointsVisible(True)
                    chart.addSeries(line)
                    line.attachAxis(axis)
                    line.attachAxis(y_axis)
                line.append(i, float(row[key]))
        chart.legend().hide()
        if chart.series():
            self.add_chart(self.trend_body, chart)
            if not any(r["market_value"] is not None for r in rows):
                self.trend_body.addWidget(QLabel("缺少历史净值，市值曲线暂无数据。"))
        else:
            self.trend_body.addWidget(QLabel("当前范围没有可用的历史持仓快照。"))
