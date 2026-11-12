"""Domestic holdings and account-scoped fund detail."""
from decimal import Decimal
import sqlite3

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QPieSeries, QValueAxis, QCategoryAxis
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from portfolio.domestic.holdings_query import REVIEW, holdings

POSITIONS = dict(fund_name="基金名称", fund_code="基金代码", platform="交易平台", fund_type="基金类型",
                 shares="持有份额", nav="最新净值", nav_date="净值日期", market_value="持仓市值（CNY）",
                 pnl="累计盈亏", rate="收益率", weight="持仓占比", status="状态", arrow="详情")
TRADES = dict(confirmation_date="确认日期", platform="交易平台", kind="交易类型", confirmed_shares="确认份额",
              confirmed_amount="确认金额", fee="手续费", confirmed_nav="确认净值", confirmation_status="确认状态",
              parse_error="备注")
NUMERIC = {"pnl", "rate", "shares", "nav", "market_value", "weight", "confirmed_shares", "confirmed_amount", "fee", "confirmed_nav"}


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
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        footer = QHBoxLayout()
        self.note = QLabel()
        self.size = combo([(f"{n} 条/页", n) for n in (10, 20, 50, 100)])
        self.size.setCurrentIndex(1)
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
        self.table.setHorizontalHeaderLabels([label + (" ↓" if self.descending else " ↑") if key == self.sort
                                               else label for key, label in self.columns.items()])
        self.table.setRowCount(len(self.visible_rows))
        for i, row in enumerate(self.visible_rows):
            for j, key in enumerate(self.columns):
                val = row.get(key)
                if key in ("market_value", "confirmed_amount", "fee", "pnl"):
                    val = amount(val)
                elif key in ("weight", "rate") and val is not None:
                    val = f"{Decimal(val):.2f}%"
                item = QTableWidgetItem("›" if key == "arrow" else str(val) if val is not None else "—")
                if key in NUMERIC:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                tooltip = row.get("return_issue") if key in ("pnl", "rate") else row.get("issue")
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
        """)
        layout = QVBoxLayout(self)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        overview = self.scroll_page()
        self.over_cards = QHBoxLayout()
        overview.addLayout(self.over_cards)
        filters = QHBoxLayout()
        self.platform = combo([("全部平台", "")])
        self.kind = combo([("全部基金类型", "")])
        self.state = combo([(x, x) for x in ("持有中", "已清仓", REVIEW)] + [("全部", "")])
        self.search = QLineEdit(placeholderText="搜索基金代码 / 名称")
        for widget in (self.platform, self.kind, self.state, self.search):
            filters.addWidget(widget)
            signal = widget.textChanged if isinstance(widget, QLineEdit) else widget.currentIndexChanged
            signal.connect(lambda *_: self.render_overview(reset=True))
        overview.addLayout(filters)
        box, body = card("基金持仓明细")
        self.positions = FundTable(POSITIONS, "market_value")
        self.positions.table.cellClicked.connect(lambda row, _: self.open_fund(self.positions.visible_rows[row]))
        body.addWidget(self.positions)
        self.total = QLabel()
        body.addWidget(self.total)
        overview.addWidget(box)
        bottom = QHBoxLayout()
        for title, name in (("平台持仓汇总", "platform_box"), ("基金类型汇总", "kind_box"), ("资产分布", "pie_box")):
            box, body = card(title)
            container = QVBoxLayout()
            body.addLayout(container)
            setattr(self, name, container)
            bottom.addWidget(box, 1)
        overview.addLayout(bottom)
        self.dimension = combo([("按基金", "fund_name"), ("按平台", "platform"), ("按基金类型", "fund_type")])
        self.pie_box.addWidget(self.dimension)
        self.pie_content = QVBoxLayout()
        self.pie_box.addLayout(self.pie_content)
        self.dimension.currentIndexChanged.connect(lambda: self.render_overview())
        self.sources = QLabel()
        self.sources.setWordWrap(True)
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
                return holdings(account, fund)
            except (ValueError, ArithmeticError, OSError, sqlite3.Error):
                return None

        def ready(data):
            if data is None:
                self.message.setText("无法读取国内资产，请刷新重试。")
                return
            self.message.setText("")
            if account:
                self.detail = data
                self.render_detail()
            else:
                self.data = data
                self.sources.setText(f"只读数据源 {data['raw_path']} · {data['calc_path']}")
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

    def render_overview(self, reset=False):
        query = self.search.text().strip().casefold()
        rows = [dict(r) for r in self.data.get("rows", []) if
                all(not w.currentData() or r[k] == w.currentData() for w, k in
                    ((self.platform, "platform"), (self.kind, "fund_type")))
                and query in (r["fund_code"] + " " + r["fund_name"]).casefold()]
        scope = rows
        pending = [r for r in scope if bool(r["review_reason"])]
        state = self.state.currentData()
        rows = pending if state == REVIEW else [r for r in scope if not state or r["current_position_status"] == state]
        valued = [Decimal(r["market_value"]) for r in scope if r["market_value"] is not None]
        total = sum(valued, Decimal(0))
        missing = len(pending)
        for row in rows:
            row["weight"] = str(Decimal(row["market_value"]) / total * 100) if total and row["market_value"] is not None else None
        returns = [Decimal(r["pnl"]) for r in scope if r.get("pnl") is not None]
        pnl_label = "累计盈亏（含已清仓）" if len(returns) == len(scope) else "累计盈亏（已核算部分）"
        clear(self.over_cards)
        for title, value in ((("已估值部分（CNY）" if any(r["market_value"] is None for r in scope) else "持仓总市值（CNY）"), amount(total if valued else None)),
                             ("已确认持有基金数量", str(len({r["fund_code"] for r in scope if r["current_position_status"] == "持有中"}))),
                             (pnl_label, amount(sum(returns)) if returns else "待计算"), ("待核对记录", str(missing))):
            box, body = card(title, value)
            if title == "待核对记录":
                action = QPushButton("查看待核对")
                action.clicked.connect(lambda: self.state.setCurrentIndex(self.state.findData(REVIEW)))
                body.addWidget(action)
            self.over_cards.addWidget(box, 1)
        self.positions.rows = rows
        self.positions.set_page(0 if reset else self.positions.page)
        visible_value = self.group_value(rows)
        self.total.setText(f"列表已估值合计 {amount(visible_value)} CNY · {missing} 条待核对")
        for layout, key, widget in ((self.platform_box, "platform", self.platform), (self.kind_box, "fund_type", self.kind)):
            clear(layout)
            for name, group in self.groups(scope, key).items():
                val = self.group_value(group)
                share = f"{val / total:.2%}" if total and val is not None else "—"
                count_valued = sum(r["market_value"] is not None for r in group)
                count_pending = sum(bool(r["review_reason"]) for r in group)
                fund_count = len({r["fund_code"] for r in group if r["current_position_status"] == "持有中"})
                button = QPushButton(f"{name} · 持有 {fund_count} · {amount(val)} · {share}\n"
                                     f"已估值 {count_valued} · 待核对 {count_pending} · 盈亏待计算")
                button.clicked.connect(lambda _, w=widget, n=name: w.setCurrentIndex(w.findData(n)))
                layout.addWidget(button)
        clear(self.pie_content)
        key = self.dimension.currentData()
        series = QPieSeries()
        series.setHoleSize(.55)
        for name, group in self.groups(scope, key).items():
            val = self.group_value(group)
            if val is None or val <= 0:
                continue
            segment = series.append(f"{name} · {amount(val)} · {val / total:.2%}", float(val))
            segment.clicked.connect(lambda n=name, k=key: self.apply_distribution(k, n))
            button = QPushButton(segment.label())
            button.clicked.connect(lambda _, n=name, k=key: self.apply_distribution(k, n))
            self.pie_content.addWidget(button)
        if series.count():
            chart = QChart()
            chart.addSeries(series)
            chart.legend().hide()
            self.add_chart(self.pie_content, chart)
        else:
            self.pie_content.addWidget(QLabel("暂无已估值资产可绘制。"))
        if missing:
            self.pie_content.addWidget(QLabel(f"{missing} 条未估值记录未计入分布。"))

    @staticmethod
    def groups(rows, key):
        groups = {}
        for row in rows:
            groups.setdefault(row[key], []).append(row)
        return groups

    @staticmethod
    def group_value(rows):
        amounts = [Decimal(r["market_value"]) for r in rows if r["market_value"] is not None]
        return sum(amounts, Decimal(0)) if amounts else None

    def apply_distribution(self, key, name):
        if key == "fund_name":
            self.search.setText(name)
        else:
            widget = self.platform if key == "platform" else self.kind
            widget.setCurrentIndex(widget.findData(name))

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
        self.reload()

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
