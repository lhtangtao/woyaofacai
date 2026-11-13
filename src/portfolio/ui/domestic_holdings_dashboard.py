"""Presentation and aggregation for the Mac domestic fund holdings dashboard."""
from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext

from PySide6.QtCharts import QAreaSeries, QChart, QChartView, QCategoryAxis, QLineSeries, QPieSeries, QValueAxis
from PySide6.QtCore import QMargins, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QToolTip, QVBoxLayout, QWidget

BLUE = "#2563EB"
GREEN = "#15803D"
RED = "#DC2626"
COLORS = (BLUE, "#0D9488", "#8B5CF6", "#D97706", "#64748B", "#0EA5E9")
PLATFORM_COLORS = {"支付宝": BLUE, "天天基金": "#D97706"}
STYLE = """
QWidget#domesticDashboard { background: #F5F7FB; }
QFrame#domesticPanel { background: white; border: 1px solid #E2E8F0; border-radius: 11px; }
QWidget#domesticDashboard QLabel { background: transparent; border: none; color: #1E293B; }
QWidget#domesticDashboard QLabel#domesticCaption { color: #64748B; font-size: 12px; }
QWidget#domesticDashboard QLabel#domesticMetric { color: #1E293B; font-size: 20px; font-weight: 700; }
QWidget#domesticDashboard QLabel#domesticMetric[sign="positive"] { color: #15803D; }
QWidget#domesticDashboard QLabel#domesticMetric[sign="negative"] { color: #DC2626; }
QWidget#domesticDashboard QLabel#domesticSection { font-size: 14px; font-weight: 700; }
QWidget#domesticDashboard QPushButton, QPushButton#domesticMode {
    padding: 5px 9px; border: 1px solid #E2E8F0; border-radius: 7px; background: #F8FAFC; color: #334155;
}
QWidget#domesticDashboard QPushButton:hover, QPushButton#domesticMode:hover { background: #EFF6FF; }
QWidget#domesticDashboard QPushButton:checked, QPushButton#domesticMode:checked {
    background: #2563EB; color: white; border-color: #2563EB;
}
QWidget#domesticDashboard QComboBox, QWidget#domesticDashboard QLineEdit {
    min-height: 22px; padding: 5px 8px; border: 1px solid #E2E8F0; border-radius: 7px; background: white;
}
QWidget#domesticDashboard QTableView {
    border: 0; alternate-background-color: #F8FAFC; selection-background-color: #EAF2FF; color: #1E293B;
}
QWidget#domesticDashboard QHeaderView::section {
    background: #F1F5FB; color: #475569; border: 0; border-bottom: 1px solid #E2E8F0; padding: 5px;
}
"""


def money(value, digits=2):
    return "—" if value is None else f"{value:,.{digits}f}"


def percent(value):
    return "—" if value is None else f"{value:.2f}%"


def total(values):
    known = [value for value in values if value is not None]
    return sum(known, Decimal(0)) if known else None


def holding_metrics(rows):
    with localcontext() as precision:
        precision.prec = 60
        values_by_code = defaultdict(Decimal)
        prepared = []
        for row in rows:
            value, cost, shares = (Decimal(row[key]) if row.get(key) is not None else None
                                   for key in ("market_value", "cost", "shares"))
            if value is not None:
                values_by_code[row["fund_code"]] += value
            pnl = value - cost if value is not None and cost is not None else None
            prepared.append(dict(row, market_value=value, cost=cost, shares=shares,
                                 average=cost / shares if cost is not None and shares and shares > 0 else None,
                                 floating=pnl, floating_rate=pnl / cost * 100 if pnl is not None and cost > 0 else None))
        market = total(row["market_value"] for row in prepared)
        matched = [row for row in prepared if row["floating"] is not None]
        cost = total(row["cost"] for row in matched)
        floating = total(row["floating"] for row in matched)
        for row in prepared:
            row["weight"] = row["market_value"] / market * 100 if market and row["market_value"] is not None else None
        return prepared, dict(market=market, cost=cost, floating=floating,
                              rate=floating / cost * 100 if cost and cost > 0 else None,
                              count=len({row["fund_code"] for row in prepared}),
                              top=sum(sorted(values_by_code.values(), reverse=True)[:5]) / market * 100 if market else None,
                              valued=sum(row["market_value"] is not None for row in prepared), matched=len(matched))


def trend_points(values, rows, period, today):
    keys = {(row["account_id"], row["fund_code"]) for row in rows}
    start = None
    if period == "year":
        start = date(today.year, 1, 1)
    elif period:
        year, month = divmod(today.year * 12 + today.month - 1 - period, 12)
        start = date(year, month + 1, min(today.day, monthrange(year, month + 1)[1]))
    result = []
    with localcontext() as precision:
        precision.prec = 60
        for day, balances in values.items():
            if not keys or (start and day < start):
                continue
            points = [balances.get(key) for key in keys]
            result.append((day, sum(points, Decimal(0)) if None not in points else None))
    return result



def panel(title=None):
    frame = QFrame(objectName="domesticPanel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(7)
    if title:
        layout.addWidget(QLabel(title, objectName="domesticSection"))
    return frame, layout


class ResponsiveGrid(QGridLayout):
    def __init__(self, widgets, width_per_card, columns, weights=None):
        super().__init__()
        self.widgets = widgets
        self.width_per_card = width_per_card
        self.max_columns = columns
        self.weights = weights
        self.columns = 0
        self.reflow(columns * width_per_card)

    def reflow(self, width):
        columns = min(self.max_columns, max(1, width // self.width_per_card))
        if columns == self.columns:
            return
        for index in range(self.max_columns):
            self.setColumnStretch(index, (self.weights[index] if self.weights and columns == self.max_columns else 1) if index < columns else 0)
        self.columns = columns
        for i, widget in enumerate(self.widgets):
            self.removeWidget(widget)
            self.addWidget(widget, i // columns, i % columns)


class Distribution(QWidget):
    def __init__(self, title, selected):
        super().__init__()
        self.selected = selected
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        frame, body = panel(title)
        layout.addWidget(frame)
        row = QHBoxLayout()
        body.addLayout(row, 1)
        self.chart = QChart()
        self.chart.legend().hide()
        self.chart.setBackgroundVisible(False)
        self.chart.setMargins(QMargins())
        plot = QChartView(self.chart)
        plot.setStyleSheet("background:transparent; border:0;")
        plot.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot.setFixedSize(132, 132)
        self.center = QLabel(plot)
        self.center.setGeometry(27, 42, 78, 48)
        self.center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.center.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(plot)
        self.legend = QVBoxLayout()
        self.legend.setSpacing(3)
        row.addLayout(self.legend, 1)

    def render(self, rows, key, market, value_key="market_value", absolute=False):
        self.chart.removeAllSeries()
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        groups = defaultdict(list)
        for row in rows:
            groups[row[key]].append(row)
        series = QPieSeries()
        series.setHoleSize(.69)
        series.setPieSize(.98)
        self.center.setText(f"<b>{money(market)}</b><br>{'盈亏绝对值' if absolute else '持仓市值'}")
        if absolute and market is None:
            self.legend.addWidget(QLabel("暂无已核算收益\n无法计算分布"))
            self.legend.addStretch()
            self.chart.addSeries(series)
            return
        for i, (name, group) in enumerate(sorted(groups.items())):
            color = PLATFORM_COLORS.get(name, COLORS[i % len(COLORS)])
            value = total(abs(row[value_key]) if absolute and row.get(value_key) is not None else row.get(value_key) for row in group)
            portion = percent(value / market * 100) if market and value is not None else "—"
            count = len({row["fund_code"] for row in group})
            if value is not None and value > 0:
                segment = series.append(name, float(value))
                segment.setColor(QColor(color))
                segment.setBorderColor(QColor("white"))
                segment.clicked.connect(lambda name=name: self.selected(name))
            button = QPushButton(f"● {name}  {portion}  {money(value)}  {count} 只")
            button.setStyleSheet(f"text-align:left; border:0; background:transparent; color:{color}; padding:4px;")
            button.clicked.connect(lambda _, name=name: self.selected(name))
            self.legend.addWidget(button)
        self.chart.addSeries(series)
        if not groups:
            self.legend.addWidget(QLabel("暂无持仓"))
        self.legend.addStretch()


class PlatformList(QWidget):
    """Account distribution as a clickable list rather than another donut."""
    def __init__(self, selected):
        super().__init__()
        self.selected = selected
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame, self.body = panel("按交易平台分布")
        outer.addWidget(frame)
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(68)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("QTableWidget {font-size:11px;} QHeaderView::section {font-size:11px; padding:4px;}")
        self.table.setMinimumHeight(170)
        self.table.cellClicked.connect(self._clicked)
        self.body.addWidget(self.table, 1)
        self.names = []

    def _clicked(self, row, _column):
        if row < len(self.names):
            self.selected(self.names[row])
        else:
            self.selected("")

    def render(self, rows, closed=False):
        value_key = "realized" if closed else "market_value"
        groups = defaultdict(list)
        for row in rows:
            groups[row["platform"]].append(row)
        self.names = sorted(groups)
        self.table.setColumnCount(4 if closed else 6)
        self.table.setHorizontalHeaderLabels(["交易平台", "已核算盈亏", "未核算", "基金数"] if closed else
                                             ["交易平台", "市值占比", "持仓成本", "持仓市值", "浮动盈亏", "基金数"])
        self.table.setRowCount(len(self.names) + 1)
        market = total(row.get("market_value") for row in rows)
        for index, name in enumerate(self.names + ["合计"]):
            group = rows if name == "合计" else groups[name]
            values = [row.get(value_key) for row in group]
            value = total(values)
            incomplete = sum(v is None for v in values)
            if closed:
                columns = (name, money(value), str(incomplete), str(len({r['fund_code'] for r in group})))
            else:
                matched = [r for r in group if r.get("floating") is not None]
                incomplete = sum(r.get("floating") is None for r in group)
                columns = (name, percent(value / market * 100) if market and value is not None else "—",
                           money(total(r.get('cost') for r in matched)), money(value),
                           money(total(r.get('floating') for r in matched)), str(len({r['fund_code'] for r in group})))
            for column, text in enumerate(columns):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if incomplete:
                    item.setToolTip(f"{incomplete} 条收益尚未核算")
                if name == "合计":
                    font = item.font(); font.setBold(True); item.setFont(font)
                self.table.setItem(index, column, item)


class Contribution(QWidget):
    def __init__(self, selected):
        super().__init__()
        self.selected = selected
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame, self.body = panel()
        outer.addWidget(frame)
        self.title = QLabel(objectName="domesticSection")
        self.body.addWidget(self.title)
        labels = QHBoxLayout()
        self.loss_label, self.gain_label = QLabel(), QLabel()
        self.loss_label.setStyleSheet("color:#DC2626; background:#FEF2F2; border-radius:4px; padding:4px;")
        self.gain_label.setStyleSheet("color:#16A34A; background:#F0FDF4; border-radius:4px; padding:4px;")
        labels.addWidget(self.loss_label); labels.addStretch(); labels.addWidget(self.gain_label)
        self.body.addLayout(labels)
        self.chart = ContributionPlot(selected)
        self.body.addWidget(self.chart)

    def render(self, rows, closed=False):
        self.title.setText(("已清仓基金已实现盈亏" if closed else "持有中基金浮动盈亏") + "贡献 Top 5")
        key = "realized" if closed else "floating"
        losses = sorted((r for r in rows if r.get(key) is not None and r[key] < 0), key=lambda r: r[key])
        gains = sorted((r for r in rows if r.get(key) is not None and r[key] > 0), key=lambda r: r[key], reverse=True)
        self.loss_label.setText(f"亏损贡献（Top 5） · 全部 {money(total(r[key] for r in losses))} CNY")
        self.gain_label.setText(f"盈利贡献（Top 5） · 全部 {money(total(r[key] for r in gains))} CNY")
        self.chart.set_data(losses[:5], gains[:5], key)


class ContributionPlot(QWidget):
    """Domestic fund contribution bars, scaled around a shared zero."""
    def __init__(self, selected):
        super().__init__()
        self.selected, self.losses, self.gains, self.key, self.hits = selected, [], [], "floating", []
        self.setMouseTracking(True)
        self.setMinimumHeight(150)

    def set_data(self, losses, gains, key):
        self.losses, self.gains, self.key = losses, gains, key
        self.setFixedHeight(max(100, max(len(losses), len(gains), 1) * 26 + 18))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.hits = []
        if not self.losses and not self.gains:
            painter.setPen(QColor("#94A3B8"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无已核算的盈亏数据")
            return
        width = float(self.width())
        side = max(130.0, min(200.0, width * .22))
        center = width / 2
        half = max(10.0, center - side - 12)
        maximum = max(abs(r[self.key]) for r in self.losses + self.gains)
        painter.setPen(QPen(QColor("#CBD5E1"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(int(center), 5, int(center), self.height() - 5)
        for sign, rows in ((-1, self.losses), (1, self.gains)):
            for index, row in enumerate(rows):
                y = 8 + index * 26
                length = float(abs(row[self.key]) / maximum) * half
                bar = QRectF(center - length if sign < 0 else center, y + 5, max(2, length), 15)
                label = QRectF(4 if sign < 0 else width - side, y, side - 4, 25)
                self.hits.extend(((bar, row), (label, row)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#EF4444" if sign < 0 else "#22C55E"))
                painter.drawRoundedRect(bar, 3, 3)
                painter.setPen(QColor(RED if sign < 0 else GREEN))
                text = f"{index + 1}  {row['fund_code']}   {money(row[self.key])}"
                painter.drawText(label, Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if sign < 0 else Qt.AlignmentFlag.AlignRight), text)

    def mousePressEvent(self, event):
        for rect, row in self.hits:
            if rect.contains(event.position()):
                self.selected(row)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        for rect, row in self.hits:
            if rect.contains(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(), f"{row['fund_name']} · {row['platform']}\n{money(row[self.key])} CNY", self)
                return
        QToolTip.hideText()


class HoldingsDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.column() not in (0, 1):
            super().paint(painter, option, index)
            return
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text, styled.text = styled.text, ""
        styled.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        painter.save()
        rect = option.rect.adjusted(8, 2, -8, -2)
        painter.setClipRect(rect)
        if index.column() == 0:
            name, _, code = text.partition("\n")
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#1E293B"))
            first = QRect(rect.x(), rect.y(), rect.width(), rect.height() // 2)
            second = QRect(rect.x(), first.bottom() + 1, rect.width(), rect.height() - first.height())
            painter.drawText(first, Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, rect.width()))
            font.setBold(False)
            font.setPointSizeF(max(9, font.pointSizeF() - 1))
            painter.setFont(font)
            painter.setPen(QColor("#64748B"))
            painter.drawText(second, Qt.AlignmentFlag.AlignVCenter, code)
        else:
            width = min(rect.width(), painter.fontMetrics().horizontalAdvance(text) + 14)
            badge = QRect(rect.center().x() - width // 2, rect.center().y() - 10, width, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFF2DE" if text == "天天基金" else "#EAF2FF"))
            painter.drawRoundedRect(badge, 5, 5)
            painter.setPen(QColor(PLATFORM_COLORS.get(text, BLUE)))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()



class TrendView(QChartView):
    def __init__(self):
        super().__init__(QChart())
        self.setStyleSheet("background:transparent; border:0;")
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMouseTracking(True)
        self.setMinimumHeight(162)
        self.days = []
        self.values = []
        self.x_axis = None
        self.area_boundaries = []

    def render(self, points, nav_dates=None):
        chart = self.chart()
        chart.removeAllSeries()
        self.area_boundaries.clear()
        for axis in chart.axes():
            axis.setVisible(False)
            chart.removeAxis(axis)
            axis.deleteLater()
        chart.setBackgroundVisible(False)
        chart.setMargins(QMargins())
        chart.legend().hide()
        self.days = list(range(len(points)))
        self.values = points
        self.x_axis = None
        self.nav_dates = nav_dates or {}
        values = [float(value) for _, value in points if value is not None]
        if not values:
            chart.update()
            return
        low, high = min(values), max(values)
        pad = max(1, (high - low) * .10, high * .015)
        base = max(0, low - pad)
        x = QCategoryAxis()
        x.setLabelsPosition(QCategoryAxis.AxisLabelsPosition.AxisLabelsPositionOnValue)
        x.setStartValue(-1)
        step = max(1, (len(points) + 4) // 5)
        for index, (day, _) in enumerate(points):
            if index % step == 0 or index == len(points) - 1:
                x.append(day.strftime("%m-%d" if len(points) < 300 else "%y-%m"), index)
        x.setRange(-0.2, max(1, len(points) - 1) + 0.2)
        y = QValueAxis()
        y.setRange(base, high + pad)
        y.setLabelFormat("%.0f")
        chart.addAxis(x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(y, Qt.AlignmentFlag.AlignLeft)
        self.x_axis = x
        run = []

        def draw_run():
            if not run:
                return
            line = QLineSeries()
            for day, value in run:
                line.append(day, float(value))
            if len(run) == 1:
                series = line
                line.setColor(QColor(BLUE))
                line.setPointsVisible(True)
            else:
                bottom = QLineSeries()
                bottom.append(run[0][0], base)
                bottom.append(run[-1][0], base)
                series = QAreaSeries(line, bottom)
                self.area_boundaries.append((line, bottom))
                # QAreaSeries outlines the entire polygon, including vertical
                # edges at missing-data gaps; draw only the actual upper line.
                series.setPen(QPen(Qt.PenStyle.NoPen))
                series.setBrush(QColor(37, 99, 235, 28))
            chart.addSeries(series)
            series.attachAxis(x)
            series.attachAxis(y)
            if len(run) > 1:
                outline = QLineSeries()
                for day, value in run:
                    outline.append(day, float(value))
                outline.setPen(QPen(QColor(BLUE), 1.6))
                chart.addSeries(outline)
                outline.attachAxis(x)
                outline.attachAxis(y)

        for day, (_, value) in zip(self.days, points):
            if value is None:
                draw_run()
                run = []
            else:
                run.append((day, value))
        draw_run()

    def mouseMoveEvent(self, event):
        pos = self.chart().mapFromScene(self.mapToScene(event.position().toPoint()))
        plot = self.chart().plotArea()
        if self.x_axis and plot.contains(pos):
            start = self.x_axis.min()
            end = self.x_axis.max()
            target = start + (pos.x() - plot.left()) / plot.width() * (end - start)
            index = max(0, min(len(self.values) - 1, round(target)))
            day, value = self.values[index]
            if value is not None:
                dates = self.nav_dates.get(day, "—")
                QToolTip.showText(event.globalPosition().toPoint(),
                                  f"{day} · 持仓总市值 {money(value)} CNY\n净值日期 {dates}", self)
            else:
                QToolTip.hideText()
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)
