"""Domestic holdings and account-scoped fund detail."""
from datetime import date
from decimal import Decimal
import sqlite3

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis, QCategoryAxis
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from portfolio.domestic.holdings_history import daily_values
from portfolio.domestic.holdings_query import holdings
from portfolio.ui.domestic_holdings_dashboard import (
    BLUE, GREEN, RED, STYLE, Distribution, HoldingsDelegate, ResponsiveGrid, TrendView,
    holding_metrics, money, panel, percent, trend_points,
)

POSITIONS = dict(fund_name="基金名称", fund_code="基金代码", platform="交易平台", fund_type="基金类型",
                 shares="持有份额", nav="最新净值", nav_date="净值日期", market_value="持仓市值（CNY）",
                 pnl="累计盈亏", rate="收益率", weight="持仓占比", status="状态", arrow="详情")
OPEN_POSITIONS = dict(
    fund_name="基金名称 / 代码", platform="交易平台", fund_type="基金类型", shares="持有份额",
    average="持仓均价", nav="最新单位净值\n（净值日期）", market_value="持仓市值（CNY）",
    cost="持仓成本（CNY）", floating="浮动盈亏（CNY）", floating_rate="收益率",
    weight="持仓占比", arrow="详情",
)
METRICS = (
    ("market", "持仓总市值（CNY）", money), ("cost", "持仓总成本（CNY）", money),
    ("floating", "浮动盈亏（CNY）", money), ("rate", "持仓收益率", percent),
    ("count", "持有基金数量", lambda count: f"{count} 只"), ("top", "前五大基金集中度", percent),
)
TRADES = dict(confirmation_date="确认日期", platform="交易平台", kind="交易类型", confirmed_shares="确认份额",
              confirmed_amount="确认金额", fee="手续费", confirmed_nav="确认净值", confirmation_status="确认状态",
              parse_error="备注")
NUMERIC = {"pnl", "rate", "shares", "nav", "market_value", "weight", "confirmed_shares", "confirmed_amount", "fee", "confirmed_nav", "average", "cost", "floating", "floating_rate"}


def amount(value):
    return f"{Decimal(value):,.2f}" if value is not None else "—"


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
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
    def __init__(self, columns, default_sort):
        super().__init__()
        self.columns, self.sort = columns, default_sort
        self.open_positions = columns is OPEN_POSITIONS
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
        self.table.setAlternatingRowColors(True)
        if self.open_positions:
            self.table.verticalHeader().setDefaultSectionSize(44)
            self.table.horizontalHeader().setMinimumHeight(39)
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(0, 226)
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
        nav_date = max((row["nav_date"] for row in self.rows if row.get("nav_date")), default=None) if self.open_positions else None
        headers = []
        for key, label in self.columns.items():
            if self.open_positions and key == "nav":
                label = f"最新单位净值\n（{nav_date or '日期未知'}）"
            headers.append(label + (" ↓" if self.descending else " ↑") if key == self.sort else label)
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.visible_rows))
        for i, row in enumerate(self.visible_rows):
            for j, key in enumerate(self.columns):
                val = row.get(key)
                if key in ("market_value", "confirmed_amount", "fee", "pnl", "cost", "floating"):
                    val = amount(val)
                elif key in ("weight", "rate", "floating_rate") and val is not None:
                    val = f"{Decimal(val):.2f}%"
                if self.open_positions:
                    if key == "fund_name":
                        val = f"{val}\n{row['fund_code']}"
                    elif key == "nav":
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
                    if key in ("floating", "floating_rate") and row.get(key) is not None and row[key]:
                        item.setForeground(QBrush(QColor(GREEN if row[key] > 0 else RED)))
                    elif key == "arrow":
                        item.setForeground(QBrush(QColor(BLUE)))
                if key in NUMERIC:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                tooltip = row.get("return_issue") if key in ("pnl", "rate", "average", "cost", "floating", "floating_rate") else row.get("issue")
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
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        overview = self.scroll_page()
        overview.setSpacing(9)
        overview.parentWidget().setObjectName("domesticDashboard")
        mode_bar = QHBoxLayout()
        self.modes = QButtonGroup(self)
        for i, name in enumerate(("持有中", "已清仓")):
            button = QPushButton(name, objectName="domesticMode", checkable=True)
            self.modes.addButton(button, i)
            mode_bar.addWidget(button)
        self.modes.button(0).setChecked(True)
        self.modes.idClicked.connect(lambda _: self.render_overview(reset=True))
        mode_bar.addStretch()
        overview.addLayout(mode_bar)

        self.metric_area = QWidget()
        self.metric_labels = {}
        cards = []
        for key, title, _ in METRICS:
            frame, body = panel()
            caption = QLabel(title, objectName="domesticCaption", wordWrap=True)
            value = QLabel("—", objectName="domesticMetric")
            note = QLabel(objectName="domesticCaption", wordWrap=True)
            for widget in (caption, value, note):
                body.addWidget(widget)
            cards.append(frame)
            self.metric_labels[key] = value, note
        self.metric_grid = ResponsiveGrid(cards, 183, 6)
        self.metric_grid.setContentsMargins(0, 0, 0, 0)
        self.metric_area.setLayout(self.metric_grid)
        overview.addWidget(self.metric_area)

        filter_box, filter_body = panel()
        self.platform = combo([("全部平台", "")])
        self.kind = combo([("全部基金类型", "")])
        self.search = QLineEdit(placeholderText="搜索基金名称 / 基金代码")
        reset = QPushButton("重置筛选")
        reset.clicked.connect(self.reset_filters)
        self.filter_grid = ResponsiveGrid([self.platform, self.kind, self.search, reset], 140, 4)
        filter_body.addLayout(self.filter_grid)
        for widget in (self.platform, self.kind, self.search):
            signal = widget.textChanged if widget is self.search else widget.currentIndexChanged
            signal.connect(lambda *_: self.render_overview(reset=True))
        overview.addWidget(filter_box)

        self.graph_area = QWidget()
        self.by_platform = Distribution("按平台分布", lambda name: self.apply_distribution(self.platform, name))
        self.by_type = Distribution("按基金类型分布", lambda name: self.apply_distribution(self.kind, name))
        trend, trend_body = panel()
        trend_header = QHBoxLayout()
        trend_header.addWidget(QLabel("持仓市值趋势", objectName="domesticSection"), 1)
        self.history_periods = (1, 3, 6, "year", 0)
        self.history_ranges = QButtonGroup(self)
        for i, label in enumerate(("近1月", "近3月", "近6月", "今年", "全部")):
            button = QPushButton(label, checkable=True)
            self.history_ranges.addButton(button, i)
            trend_header.addWidget(button)
        self.history_ranges.button(3).setChecked(True)
        self.history_ranges.idClicked.connect(lambda _: self.render_history())
        trend_body.addLayout(trend_header)
        self.history_plot = TrendView()
        trend_body.addWidget(self.history_plot, 1)
        self.graph_grid = ResponsiveGrid([self.by_platform, self.by_type, trend], 345, 3)
        self.graph_grid.setContentsMargins(0, 0, 0, 0)
        self.graph_area.setLayout(self.graph_grid)
        overview.addWidget(self.graph_area)
        self.history_scope = []

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
        self.open_positions = FundTable(OPEN_POSITIONS, "market_value")
        self.open_positions.table.setMinimumHeight(280)
        self.open_positions.table.cellClicked.connect(
            lambda row, _: self.open_fund(self.open_positions.visible_rows[row]))
        body.addWidget(self.open_positions, 1)
        self.open_total = QLabel(wordWrap=True)
        body.addWidget(self.open_total)
        overview.addWidget(self.open_box, 1)

        self.closed_box, body = panel("已清仓基金明细")
        self.positions = FundTable(POSITIONS, "market_value")
        self.positions.table.cellClicked.connect(lambda row, _: self.open_fund(self.positions.visible_rows[row]))
        body.addWidget(self.positions, 1)
        overview.addWidget(self.closed_box, 1)
        self.closed_box.hide()
        self.sources = QLabel(objectName="domesticCaption", wordWrap=True)
        overview.addWidget(self.sources)

        detail = self.scroll_page()
        bar = QHBoxLayout()
        back = QPushButton("← 返回持仓管理")
        back.clicked.connect(self.back)
        self.breadcrumb = QLabel()
        self.breadcrumb.setWordWrap(True)
        bar.addWidget(self.breadcrumb, 1)
        bar.addWidget(back)
        detail.addLayout(bar)
        self.detail_cards = QHBoxLayout()
        detail.addLayout(self.detail_cards)
        middle = QHBoxLayout()
        box, body = card("交易明细")
        filter_row = QHBoxLayout()
        self.tx_kind = combo([("全部交易类型", "")])
        self.start, self.end = (QLineEdit(placeholderText=label) for label in ("确认起日 YYYY-MM-DD", "确认止日 YYYY-MM-DD"))
        for widget in (self.start, self.end, self.tx_kind):
            filter_row.addWidget(widget)
            signal = widget.textChanged if isinstance(widget, QLineEdit) else widget.currentIndexChanged
            signal.connect(self.render_trades)
        body.addLayout(filter_row)
        self.trade_source_note = QLabel("原始交易记录未提供确认净值。")
        self.trade_source_note.setWordWrap(True)
        self.trade_source_note.hide()
        body.addWidget(self.trade_source_note)
        self.trades = FundTable(TRADES, "confirmation_date")
        body.addWidget(self.trades)
        middle.addWidget(box, 3)
        box, body = card("持仓概览")
        self.fund_overview = QLabel()
        self.fund_overview.setWordWrap(True)
        body.addWidget(self.fund_overview)
        body.addStretch()
        middle.addWidget(box, 1)
        detail.addLayout(middle)
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
        detail.addWidget(box)
        self.render_trend()

    def scroll_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        self.stack.addWidget(scroll)
        return layout

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
            available = max(0, event.size().width() - 50)
            for grid in (self.metric_grid, self.filter_grid, self.graph_grid):
                grid.reflow(available)

    def reset_filters(self):
        for control in (self.platform, self.kind, self.search):
            control.blockSignals(True)
        self.platform.setCurrentIndex(0)
        self.kind.setCurrentIndex(0)
        self.search.clear()
        for control in (self.platform, self.kind, self.search):
            control.blockSignals(False)
        self.profit_filters.button(0).setChecked(True)
        self.render_overview(reset=True)

    def apply_distribution(self, control, name):
        control.setCurrentIndex(0 if control.currentData() == name else control.findData(name))

    def render_overview(self, reset=False):
        all_rows = self.data.get("rows", [])
        opened = self.modes.checkedId() == 0
        for i, status in enumerate(("持有中", "已清仓")):
            count = sum(row["current_position_status"] == status for row in all_rows)
            self.modes.button(i).setText(f"{status}（{count}）")
        for widget in (self.metric_area, self.graph_area, self.open_box):
            widget.setVisible(opened)
        self.closed_box.setVisible(not opened)
        query = self.search.text().strip().casefold()
        rows = [row for row in all_rows
                if row["current_position_status"] == ("持有中" if opened else "已清仓")
                and all(not control.currentData() or row[key] == control.currentData()
                        for control, key in ((self.platform, "platform"), (self.kind, "fund_type")))
                and query in (row["fund_code"] + " " + row["fund_name"]).casefold()]
        if not opened:
            prepared, _ = holding_metrics(rows)
            self.positions.rows = prepared
            self.positions.set_page(0 if reset else self.positions.page)
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
        self.history_scope = [row for row in all_rows
                              if row["fund_code"] not in money_funds
                              and all(not control.currentData() or row[key] == control.currentData()
                                     for control, key in ((self.platform, "platform"), (self.kind, "fund_type")))
                              and query in (row["fund_code"] + " " + row["fund_name"]).casefold()]
        self.by_platform.render(prepared, "platform", summary["market"])
        self.by_type.render(prepared, "fund_type", summary["market"])
        self.render_history()
        for key, _, formatter in METRICS:
            value, note = self.metric_labels[key]
            metric = summary[key]
            value.setText(formatter(metric))
            sign = ("positive" if metric > 0 else "negative") if key in ("floating", "rate") and metric else ""
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
            f"浮动盈亏 {money(totals['floating'])}　收益率 {percent(totals['rate'])}"
            + ("　· 部分数据未核算" if totals["matched"] < len(shown) else "")
        )

    def render_history(self):
        cutoff = self.history_scope[0]["valuation_date"] if self.history_scope else None
        points = trend_points(self.data.get("daily_values", {}), self.history_scope,
                              self.history_periods[self.history_ranges.checkedId()], date.fromisoformat(cutoff)) if cutoff else []
        history = self.data.get("daily_values", {})
        keys = {(row["account_id"], row["fund_code"]) for row in self.history_scope}
        details = {}
        reasons = {}
        for day, value in points:
            if value is None:
                for key in keys:
                    if history.get(day, {}).get(key) is None:
                        reason = getattr(history, "missing", {}).get((day, key), "历史数据缺失")
                        reasons.setdefault(reason, set()).add(key[1])
            else:
                dates = [history.nav_dates.get((day, key)) for key in keys
                         if history.get(day, {}).get(key) not in (None, 0)]
                dates = sorted({day for day in dates if day})
                details[day] = "、".join(dates) if dates else "当日无持仓"
        self.history_plot.render(points, details)
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
        self.render_detail()
        self.stack.setCurrentIndex(1)
        self.stack.currentWidget().verticalScrollBar().setValue(0)
        self.reload()

    def back(self):
        self.selected = None
        self.stack.setCurrentIndex(0)
        self.message.clear()

    def render_detail(self):
        clear(self.detail_cards)
        self.choices(self.tx_kind, "全部交易类型", self.detail.get("transactions", []), "kind")
        self.render_trades()
        self.render_trend()
        rows = self.detail.get("rows", [])
        self.fund_overview.clear()
        if not rows:
            self.breadcrumb.setText("国内资产 / 持仓管理 / 持仓明细")
            return
        row = rows[0]
        self.breadcrumb.setText(f"国内资产 / 持仓管理 / {row['fund_name']}")
        for label, value in (("当前持有份额", row["shares"] or "—"),
                             (f"最新单位净值 · {row['nav_date'] or '净值缺失'}", row["nav"] or "—"),
                             ("当前市值（CNY）", amount(row["market_value"])), ("累计盈亏", amount(row.get("pnl"))),
                             ("收益率", f"{Decimal(row['rate']):.2f}%" if row.get("rate") is not None else "—")):
            self.detail_cards.addWidget(card(label, value)[0], 1)
        self.fund_overview.setText(
            f"平台  {row['platform']}\n账户  {row['account_name']}\n基金代码  {row['fund_code']}\n"
            f"交易资料覆盖截至  {row['coverage_through'] or '—'}\n{row['status']}\n"
            f"净值状态  {row['nav_status']}\n"
            f"最近确认清仓日  {row['last_confirmed_close_date'] or '—'}\n\n"
            f"当前份额  {row['shares'] or '—'}\n当前市值  {amount(row['market_value'])}\n"
            f"累计买入份额  {row.get('buy_shares', '—')}\n累计卖出份额  {row.get('sell_shares', '—')}\n"
            f"剩余持仓成本  {amount(row.get('cost'))}\n已实现盈亏  {amount(row.get('realized_pnl'))}\n"
            + (f"最近确认已实现盈亏  {amount(row['last_confirmed_realized_pnl'])}\n" if row.get("last_confirmed_realized_pnl") is not None else "")
            + (f"{row['confirmation_note']}\n" if row.get("confirmation_note") else "")
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
