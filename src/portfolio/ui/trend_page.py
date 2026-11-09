from dataclasses import replace
import csv
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QMenu, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
)

from portfolio.domain.analytics import ratio
from portfolio.services.charts_service import Chart, Series
from portfolio.services.portfolio_trend import (
    METRICS, TREND_CHART_IDS, build_portfolio_trend, calendar_months, change, prior_month,
)
from portfolio.ui.charts_page import ChartCanvas
from portfolio.ui.distribution_page import DistributionDetailDrawer

PALETTE = ("#2563EB", "#F59E0B", "#10B981", "#7C3AED", "#DB2777", "#0891B2")


def fmt(value, kind="money"):
    if value is None or value == "":
        return "—"
    if kind == "ratio":
        return f"{value:.2%}"
    if kind == "pp":
        return f"{value * 100:+.2f} pp"
    if kind == "count":
        return f"{value:,.0f}"
    return value if isinstance(value, str) else f"{value:,.2f}"


class TrendItem(QTableWidgetItem):
    def __init__(self, value, kind):
        super().__init__(fmt(value, kind))
        self.value = value
        if kind != "text":
            self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other):
        if self.value is None:
            return other.value is not None
        return other.value is not None and self.value < other.value


class TrendChartsPage(QWidget):
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = None
        self.selected_month = ""
        self.hidden_series = set()
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.view_selector = QComboBox()
        for label, value in (("全部时间", 0), ("最近12个月", 12), ("最近24个月", 24)):
            self.view_selector.addItem(label, value)
        self.view_selector.currentIndexChanged.connect(self.update_charts)
        bar.addWidget(QLabel("统计范围"))
        bar.addWidget(self.view_selector)
        self.as_of = QLabel()
        bar.addWidget(self.as_of)
        bar.addStretch()
        for label, action in (("导出 PNG…", self.export), ("导出 CSV…", self.export_csv)):
            button = QPushButton(label)
            button.clicked.connect(action)
            bar.addWidget(button)
        layout.addLayout(bar)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.container = QWidget()
        cards = QVBoxLayout(self.container)
        cards.setSpacing(12)
        kpis = QHBoxLayout()
        self.kpi_labels = []
        for title in ("当前股票市值", "累计净买入", "前五大持仓集中度", "持仓标的数"):
            frame = QFrame()
            frame.setStyleSheet("QFrame { background: white; border: 1px solid #E2E8F0; border-radius: 10px; }")
            column = QVBoxLayout(frame)
            for label, size in ((QLabel(title), 13), (QLabel("—"), 23), (QLabel(), 11)):
                label.setStyleSheet(f"QLabel {{ border: none; color: #1E293B; font-size: {size}px; }}")
                column.addWidget(label)
            self.kpi_labels.append((column.itemAt(1).widget(), column.itemAt(2).widget()))
            kpis.addWidget(frame, 1)
        cards.addLayout(kpis)
        grid = QGridLayout()
        self.canvases = {}
        self.legends = {}
        for index, chart_id in enumerate(TREND_CHART_IDS):
            canvas = ChartCanvas(card_mode=True)
            canvas.detail_mode = True
            canvas.setMinimumHeight(300)
            canvas.selected_detail.connect(self.open_detail)
            self.canvases[chart_id] = canvas
            if chart_id == 11:
                self.structure_detail = QPushButton("查看详情 ›", canvas)
                self.structure_detail.setEnabled(False)
                self.structure_detail.setStyleSheet(
                    "QPushButton { background: #EFF6FF; color: #2563EB; border: 0;"
                    " border-radius: 6px; padding: 5px 9px; }"
                )
                self.structure_detail.clicked.connect(lambda: self.open_detail(11, self.selected_month, "大盘"))
                overlay = QVBoxLayout(canvas)
                overlay.setContentsMargins(16, 15, 16, 0)
                overlay.addWidget(self.structure_detail, alignment=Qt.AlignmentFlag.AlignRight)
                overlay.addStretch()
            cell = QVBoxLayout()
            cell.addWidget(canvas)
            if chart_id in (11, 12):
                legend = QToolButton()
                legend.setText("图例筛选")
                legend.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                legend.setMenu(QMenu(legend))
                self.legends[chart_id] = legend
                cell.addWidget(legend, alignment=Qt.AlignmentFlag.AlignRight)
            grid.addLayout(cell, index // 2, index % 2)
        cards.addLayout(grid)
        cards.addWidget(QLabel("关键指标月度趋势"))
        self.table = self.make_table(["月份", *[label for _, label, _ in METRICS]], [])
        self.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        self.table.setMinimumHeight(260)
        self.table.cellClicked.connect(lambda row, _: self.select_month(self.table.item(row, 0).text()))
        self.table.cellDoubleClicked.connect(lambda row, _: self.open_detail(18, self.table.item(row, 0).text(), ""))
        cards.addWidget(self.table)
        scroll.setWidget(self.container)
        layout.addWidget(scroll, 1)

        self.drawer = DistributionDetailDrawer(self)
        self.drawer.panel.setFixedWidth(560)
        self.drawer.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.escape = QShortcut(QKeySequence("Escape"), self.drawer)
        self.escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape.activated.connect(self.drawer.close_drawer)

    @staticmethod
    def make_table(headers, rows):
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        TrendChartsPage.fill_table(table, rows)
        return table

    @staticmethod
    def fill_table(table, rows):
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, (value, kind) in enumerate(values):
                table.setItem(row, col, TrendItem(value, kind))
        table.resizeColumnsToContents()
        table.setSortingEnabled(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "drawer"):
            self.drawer.setGeometry(self.rect())
            self.drawer.panel.setFixedWidth(min(560, self.width()))

    def clear(self):
        self.data = None
        self.selected_month = ""
        self.drawer.close_drawer()
        self.update_charts()

    def set_analytics(self, analytics):
        self.data = build_portfolio_trend(analytics)
        self.drawer.close_drawer()
        self.update_charts()

    def visible_months(self):
        rows = self.data["months"] if self.data else []
        limit = self.view_selector.currentData()
        if not rows or not limit:
            return rows
        year, month = map(int, rows[-1]["month"].split("-"))
        first_year, first_month = divmod(year * 12 + month - limit, 12)
        return [row for row in rows if row["month"] >= f"{first_year:04d}-{first_month + 1:02d}"]

    def select_month(self, month):
        self.selected_month = month
        for canvas in self.canvases.values():
            canvas.detail_month = month
            canvas.update()

    def update_charts(self):
        rows = self.visible_months()
        dates = {row["month"] for row in rows}
        latest = rows[-1] if rows else {}
        if self.selected_month not in dates:
            self.selected_month = latest.get("month", "")
        self.structure_detail.setEnabled(bool(self.selected_month))
        prior = next((row for row in rows if row["month"] == prior_month(latest["month"])), {}) if latest else {}
        currency = self.data["currency"] if self.data else ""
        self.as_of.setText(f"数据截至 {latest.get('valuation_date') or '—'} · {currency}")
        for (label, sub), key, kind in zip(self.kpi_labels, ("market_value", "net_buy", "concentration", "count"),
                                           ("money", "money", "ratio", "count")):
            label.setText(fmt(latest.get(key), kind))
            delta = change(latest.get(key), prior.get(key))
            sub.setText(f"较上月 {fmt(delta, 'pp' if kind == 'ratio' else kind)}" if delta is not None else currency if kind == "money" else "")
        self.fill_table(self.table, [[(row["month"], "text"), *[(row[key], kind) for key, _, kind in METRICS]] for row in reversed(rows)])
        for canvas in self.canvases.values():
            canvas.chart = None
            canvas.detail_month = self.selected_month
            canvas.hover_index = None
            canvas.update()
        for chart in self.data["charts"] if self.data else ():
            canvas = self.canvases[chart.id]
            canvas.series_colors = {s.name: PALETTE[i % len(PALETTE)] for i, s in enumerate(chart.series)}
            canvas.chart = replace(chart, series=tuple(
                Series(s.name, tuple(point for point in s.points if rows and rows[0]["month"] <= point[0] <= rows[-1]["month"]))
                for s in chart.series if (chart.id, s.name) not in self.hidden_series
            ))
            canvas.update()
            if chart.id in self.legends:
                menu = self.legends[chart.id].menu()
                menu.clear()
                for series in chart.series:
                    action = menu.addAction(series.name)
                    action.setCheckable(True)
                    action.setChecked((chart.id, series.name) not in self.hidden_series)
                    action.triggered.connect(lambda checked, key=(chart.id, series.name): self.toggle_series(key, checked))

    def toggle_series(self, key, visible):
        if visible:
            self.hidden_series.discard(key)
        else:
            self.hidden_series.add(key)
        QTimer.singleShot(0, self.update_charts)

    def open_detail(self, chart_id, month, name):
        rows = self.visible_months()
        row = next((item for item in rows if item["month"] == month), None)
        if row is None:
            return
        self.select_month(month)
        previous = next((item for item in self.data["months"] if item["month"] == prior_month(month)), {})
        drawer = self.drawer
        drawer._clear_layout(drawer.content_layout)
        drawer.kpi_container.hide()
        drawer.title_lbl.setText({18: f"{month} 市值与累计净买入", 13: f"{month} 前五大持仓详情",
                                  14: f"{month} 月末持仓标的"}.get(chart_id, f"{name} 持仓趋势详情"))
        drawer.sub_title_lbl.setText(f"{month} · {row['valuation_date'] or '估值日期缺失'} · {self.data['currency']}")
        if chart_id == 18:
            note = QLabel(self.data["note"])
            note.setWordWrap(True)
            drawer.content_layout.addWidget(note)
            drawer.content_layout.addWidget(self.make_table(["指标", "当月", "较上月"], [
                [(label, "text"), (row[key], kind), (change(row[key], previous.get(key)), "pp" if kind == "ratio" else kind)]
                for key, label, kind in METRICS
            ]))
        elif chart_id in (11, 12):
            self.add_series_detail(rows, row, previous, chart_id, name)
        else:
            self.add_stock_detail(row, row["stocks"][:5] if chart_id == 13 else row["stocks"], ranked=chart_id == 13)
        drawer.scroll.verticalScrollBar().setValue(0)
        drawer.raise_()
        drawer.show()
        drawer.setFocus()

    def add_series_detail(self, rows, row, previous, chart_id, name):
        layout = self.drawer.content_layout
        group = "categories" if chart_id == 11 else "channels"
        share_first = chart_id == 11

        def share(item):
            return ratio(item.get(group, {}).get(name), item.get("market_value"))

        value = row[group].get(name)
        old_value = previous.get(group, {}).get(name)
        subtitle = QLabel(
            f"市值 {fmt(value)}　占比 {fmt(share(row), 'ratio')}\n"
            + (f"上月占比 {fmt(share(previous), 'ratio')}　占比变化 {fmt(change(share(row), share(previous)), 'pp')}" if share_first
               else f"上月市值 {fmt(old_value)}　市值变化 {fmt(change(value, old_value))}")
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        mode = QComboBox()
        mode.addItems(["占比走势", "市值走势"] if share_first else ["市值走势", "占比走势"])
        layout.addWidget(mode)
        canvas = ChartCanvas(card_mode=True)
        layout.addWidget(canvas)

        def plot():
            shares = (mode.currentIndex() == 0) == share_first
            title = "占比走势" if shares else "市值走势"
            values = {item["month"]: share(item) if shares else item[group].get(name) for item in rows}
            points = tuple((month, values.get(month)) for month in calendar_months(list(values)))
            canvas.chart = Chart(chart_id, f"{name} · {title}", "line", (Series(name, points),), "", ratio=shares)
            canvas.update()

        mode.currentIndexChanged.connect(plot)
        plot()
        lookup = {item["month"]: item for item in self.data["months"]}
        layout.addWidget(self.make_table(["月份", "月末市值", "占比", "占比变化"], [
            [(item["month"], "text"), (item[group].get(name), "money"), (share(item), "ratio"),
             (change(share(item), share(lookup.get(prior_month(item["month"]), {}))), "pp")]
            for item in reversed(rows)
        ]))
        part_key = "category" if share_first else "broker"
        stocks = [dict(stock, parts=[part for part in stock["parts"] if part[part_key] == name]) for stock in row["stocks"]]
        self.add_stock_detail(row, [stock for stock in stocks if stock["parts"]], channel_only=not share_first)

    def add_stock_detail(self, month, stocks, *, ranked=False, channel_only=False):
        from portfolio.domain.analytics import strict_sum

        layout = self.drawer.content_layout
        if month["holdings_incomplete"]:
            layout.addWidget(QLabel("部分持仓数量或估值缺失；请核对来源日期。"))
        if ranked and month["concentration"] is None:
            layout.addWidget(QLabel("当前月份缺少可确认的前五大排名。"))
            return
        headers = ["Ticker", "分类", "股数", "月末市值", "占比", "涉及渠道"]
        if channel_only:
            headers.insert(3, "月末价格")
        if ranked:
            headers.insert(0, "排名")
        values = []
        for rank, stock in enumerate(stocks, 1):
            parts = stock["parts"]
            market = strict_sum(part["market_value"] for part in parts)
            quantity = strict_sum(part["quantity"] for part in parts)
            line = [(stock["ticker"], "text"), (stock["category"], "text"), (quantity, "money"),
                    (market, "money"), (ratio(market, month["market_value"]), "ratio"),
                    ("、".join(sorted({p["broker"] for p in parts})), "text")]
            if channel_only:
                prices = {part["price"] for part in parts}
                line.insert(3, (next(iter(prices)) if len(prices) == 1 else None, "money"))
            values.append([(rank, "count"), *line] if ranked else line)
        table = self.make_table(headers, values)
        layout.addWidget(table)
        splits = self.make_table(["渠道", "账户 ID", "股数", "月末价格", "月末市值"], [])
        splits.hide()
        layout.addWidget(splits)
        by_ticker = {stock["ticker"]: stock for stock in stocks}

        def open_stock(index, _):
            ticker = table.item(index, int(ranked)).text()
            self.fill_table(splits, [
                [(part["broker"], "text"), (part["account"], "count"), (part["quantity"], "money"),
                 (part["price"], "money"), (part["market_value"], "money")]
                for part in by_ticker[ticker]["parts"]
            ])
            splits.show()
            self.drawer.scroll.ensureWidgetVisible(splits)

        table.cellClicked.connect(open_stock)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出走势图表", "走势图汇总.png", "PNG (*.png)")
        if path:
            try:
                from PySide6.QtCore import QBuffer, QIODevice
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not self.container.grab().save(buffer, "PNG"):
                    raise ValueError("PNG 编码失败")
                with Path(path).open("xb") as stream:
                    stream.write(bytes(buffer.data()))
            except (OSError, ValueError):
                self.error.emit("导出未完成：目标已存在或不可写。请选择新文件名。")

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出月度趋势", "关键指标月度趋势.csv", "CSV (*.csv)")
        if path:
            try:
                with Path(path).open("x", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow([self.table.horizontalHeaderItem(col).text() for col in range(self.table.columnCount())])
                    for row in range(self.table.rowCount()):
                        writer.writerow([self.table.item(row, col).text() for col in range(self.table.columnCount())])
            except OSError:
                self.error.emit("导出未完成：目标已存在或不可写。请选择新文件名。")
