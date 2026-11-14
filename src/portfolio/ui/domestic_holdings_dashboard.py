"""Presentation and aggregation for the Mac domestic fund holdings dashboard."""
from calendar import monthrange
from bisect import bisect_right
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext

from PySide6.QtCharts import QAreaSeries, QChart, QChartView, QCategoryAxis, QLineSeries, QPieSeries, QValueAxis
from PySide6.QtCore import QMargins, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy, QSpinBox, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QToolTip, QVBoxLayout, QWidget

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
    border: 0; alternate-background-color: #F8FAFC; selection-background-color: #EAF2FF; selection-color:#1E293B; color: #1E293B;
}
QWidget#domesticDashboard QHeaderView::section {
    background: #F1F5FB; color: #475569; border: 0; border-bottom: 1px solid #E2E8F0; padding: 5px;
}
QWidget#domesticDashboard QTabWidget::pane { border:0; background:transparent; }
QWidget#domesticDashboard QTabBar::tab { background:#F1F5FB; color:#64748B; padding:8px 14px; border:0; }
QWidget#domesticDashboard QTabBar::tab:selected { background:#EFF6FF; color:#2563EB; border-bottom:2px solid #2563EB; }
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
            total_pnl = Decimal(row["pnl"]) if row.get("pnl") is not None else None
            # 持有中统一按累计持有收益（含同基金历史部分卖出已实现与现金分红），缺省时退回市值减成本
            pnl = total_pnl if total_pnl is not None else (value - cost if value is not None and cost is not None else None)
            basis = Decimal(row["contributed"]) if total_pnl is not None and row.get("contributed") is not None else cost
            rate = Decimal(row["rate"]) if total_pnl is not None and row.get("rate") is not None else (
                pnl / basis * 100 if pnl is not None and basis is not None and basis > 0 else None
            )
            prepared.append(dict(row, market_value=value, cost=cost, shares=shares,
                                 average=cost / shares if cost is not None and shares and shares > 0 else None,
                                 floating=pnl, floating_rate=rate, _basis=basis))
        market = total(row["market_value"] for row in prepared)
        matched = [row for row in prepared if row["floating"] is not None]
        cost = total(row["cost"] for row in matched)
        basis_total = total(row["_basis"] for row in matched)
        floating = total(row["floating"] for row in matched)
        for row in prepared:
            row["weight"] = row["market_value"] / market * 100 if market and row["market_value"] is not None else None
        return prepared, dict(market=market, cost=cost, floating=floating,
                              rate=floating / basis_total * 100 if basis_total and basis_total > 0 else None,
                              count=len({row["fund_code"] for row in prepared}),
                              top=sum(sorted(values_by_code.values(), reverse=True)[:5]) / market * 100 if market else None,
                              valued=sum(row["market_value"] is not None for row in prepared), matched=len(matched))


def closed_metrics(rows):
    """Aggregate confirmed values without replacing missing amounts with zero."""
    profits = [row for row in rows if row.get("realized") is not None]
    eligible = [row for row in profits if row.get("contributed") is not None and row["contributed"] > 0]
    basis = total(row["contributed"] for row in eligible)
    days = [row["holding_days"] for row in rows if row.get("holding_days") is not None]
    return dict(realized=total(row["realized"] for row in profits),
                buy=total(row.get("buy_amount") for row in rows),
                sell=total(row.get("sell_amount") for row in rows),
                rate=total(row["realized"] for row in eligible) / basis * 100 if basis else None,
                holding_days=Decimal(sum(days)) / Decimal(len(days)) if days else None,
                profit_count=len(profits), rate_count=len(eligible), day_count=len(days))


DOMESTIC_TREND_START = date(2022, 1, 1)


def closed_trend_points(rows, period, today, *, zeroed=False):
    """Cumulative verified event amounts; never invent missing event dates."""
    events = defaultdict(Decimal)
    covered = 0
    for row in rows:
        if row.get("closed_events") is None:
            continue
        covered += 1
        for day, amount in row["closed_events"]:
            events[date.fromisoformat(day)] += Decimal(amount)
    start = DOMESTIC_TREND_START if today >= DOMESTIC_TREND_START else None
    if period == "year":
        year_start = date(today.year, 1, 1)
        start = max(start, year_start) if start else year_start
    elif period:
        year, month = divmod(today.year * 12 + today.month - 1 - period, 12)
        p_start = date(year, month + 1, min(today.day, monthrange(year, month + 1)[1]))
        start = max(start, p_start) if start else p_start
    cumulative = Decimal(0)
    points = []
    base = None
    seen = False
    for day, amount in sorted(events.items()):
        if day > today:
            break
        seen = True
        cumulative += amount
        if day <= today and (start is None or day >= start):
            if zeroed and base is None:
                base = cumulative
            points.append((day, (cumulative - base) if zeroed else cumulative))
    if not points and seen and start and start <= today:
        points.append((start, Decimal(0) if zeroed else cumulative))
    if points and points[-1][0] < today:
        points.append((today, points[-1][1]))
    return points, covered


def trend_points(values, rows, period, today):
    keys = {(row["account_id"], row["fund_code"]) for row in rows}
    start = DOMESTIC_TREND_START if today >= DOMESTIC_TREND_START else None
    if period == "year":
        year_start = date(today.year, 1, 1)
        start = max(start, year_start) if start else year_start
    elif period:
        year, month = divmod(today.year * 12 + today.month - 1 - period, 12)
        p_start = date(year, month + 1, min(today.day, monthrange(year, month + 1)[1]))
        start = max(start, p_start) if start else p_start
    result = []
    with localcontext() as precision:
        precision.prec = 60
        for day, balances in values.items():
            if not keys or (start and day < start):
                continue
            points = [balances.get(key) for key in keys]
            result.append((day, sum(points, Decimal(0)) if None not in points else None))
    return result


def trend_combo_points(values, rows, period, today):
    """构建国内基金双轴趋势图所需的每日市值、累计盈亏与每日盈亏序列。"""
    mv_points = trend_points(values, rows, period, today)
    keys = {(row["account_id"], row["fund_code"]) for row in rows}
    cum_pnl_map = getattr(values, "cum_pnl", {})
    series = []
    prev_cum = None
    with localcontext() as precision:
        precision.prec = 60
        for day, mv in mv_points:
            pnl_vals = [cum_pnl_map.get(day, {}).get(key) for key in keys]
            cum_pnl = sum(pnl_vals, Decimal(0)) if keys and None not in pnl_vals else None
            daily_pnl = (cum_pnl - prev_cum) if (cum_pnl is not None and prev_cum is not None) else None
            if cum_pnl is not None:
                prev_cum = cum_pnl
            series.append({
                "date": day.isoformat(),
                "market_value": float(mv) if mv is not None else None,
                "cumulative_pnl": float(cum_pnl) if cum_pnl is not None else None,
                "daily_pnl": float(daily_pnl) if daily_pnl is not None else None,
            })
    return series



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


class LegendButton(QPushButton):
    def __init__(self, lines):
        super().__init__()
        self.lines = lines
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self._update_text()

    def _update_text(self):
        width = max(1, self.width() - 12)
        self.setText("\n".join(self.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, width)
                               for line in self.lines))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_text()


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
        self.plot = QChartView(self.chart)
        self.plot.setStyleSheet("background:transparent; border:0;")
        self.plot.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.plot.setFixedSize(136, 136)
        self.center = QLabel(self.plot)
        self.center.setGeometry(18, 41, 100, 54)
        self.center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.center.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.plot)
        self.legend = QVBoxLayout()
        self.legend.setSpacing(2)
        row.addLayout(self.legend, 1)
        self.compact = False
        self.last_render = None

    def set_compact(self, compact):
        if self.compact == compact:
            return
        self.compact = compact
        self.plot.setFixedSize(108, 108) if compact else self.plot.setFixedSize(136, 136)
        self.center.setGeometry(8, 28, 92, 52) if compact else self.center.setGeometry(18, 41, 100, 54)
        if self.last_render is not None:
            rows, key, market, value_key, absolute = self.last_render
            self.render(rows, key, market, value_key=value_key, absolute=absolute)

    def render(self, rows, key, market, value_key="market_value", absolute=False):
        self.last_render = (rows, key, market, value_key, absolute)
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
        self.legend.addStretch()
        if absolute and market is None:
            self.legend.addWidget(QLabel("暂无已核算收益\n无法计算分布"))
            self.legend.addStretch()
            self.chart.addSeries(series)
            return
        grouped = []
        for name, group in groups.items():
            value = total(abs(row[value_key]) if absolute and row.get(value_key) is not None else row.get(value_key) for row in group)
            grouped.append((name, group, value))
        grouped.sort(key=lambda item: (item[2] is None, -(item[2] or Decimal(0)), item[0]))
        for i, (name, group, value) in enumerate(grouped):
            color = PLATFORM_COLORS.get(name, COLORS[i % len(COLORS)])
            portion = percent(value / market * 100) if market and value is not None else "—"
            count = len({row["fund_code"] for row in group if not row.get("is_historical_baseline")})
            if value is not None and value > 0:
                segment = series.append(name, float(value))
                segment.setColor(QColor(color))
                segment.setBorderColor(QColor("white"))
                segment.clicked.connect(lambda name=name: self.selected(name))
            lines = (f"● {name}", f"{portion}  {money(value)}  {count} 只") if self.compact else (f"● {name}  {portion}  {money(value)}  {count}只",)
            button = LegendButton(lines)
            button.setToolTip(f"{name}　{portion}　{money(value)}　{count} 只")
            button.setStyleSheet(f"text-align:left; border:0; background:transparent; color:{color}; padding:2px 0px; font-size:11px;")
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
        self.table.horizontalHeader().setMinimumSectionSize(56)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("QTableWidget {font-size:11px;} QHeaderView::section {font-size:11px; padding:4px 6px;}")
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
        from portfolio.ui.open_holdings_dashboard import RatioProgressBarCell

        value_key = "realized" if closed else "market_value"
        groups = defaultdict(list)
        for row in rows:
            groups[row["platform"]].append(row)
        self.names = sorted(groups)
        self.table.setColumnCount(5 if closed else 6)
        self.table.setHorizontalHeaderLabels(["交易平台", "确认买入", "确认赎回", "历史收益", "基金数"] if closed else
                                             ["交易平台", "市值占比", "持仓成本", "持仓市值", "持有收益", "基金数"])
        header = self.table.horizontalHeader()
        if closed:
            for col in range(5):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch if col == 0 else QHeaderView.ResizeMode.ResizeToContents)
        else:
            for col in range(6):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed if col == 1 else QHeaderView.ResizeMode.ResizeToContents)
            self.table.setColumnWidth(1, 130)
        self.table.setRowCount(len(self.names) + 1)
        market = None if closed else total(row.get("market_value") for row in rows)
        for index, name in enumerate(self.names + ["合计"]):
            group = rows if name == "合计" else groups[name]
            values = [row.get(value_key) for row in group]
            value = total(values)
            incomplete = sum(v is None for v in values)
            pnl_value = None
            ratio_fraction = None
            ratio_text = "—"
            if closed:
                fund_count = len({r['fund_code'] for r in group if not r.get('is_historical_baseline')})
                pnl_value = value
                columns = (name, money(total(r.get("buy_amount") for r in group)),
                           money(total(r.get("sell_amount") for r in group)), money(value),
                           str(fund_count))
            else:
                matched = [r for r in group if r.get("floating") is not None]
                incomplete = sum(r.get("floating") is None for r in group)
                pnl_value = total(r.get('floating') for r in matched)
                if market and value is not None and market > 0:
                    ratio_fraction = float(value / market)
                    ratio_text = f"{ratio_fraction * 100:.1f}%"
                columns = (name, ratio_text,
                           money(total(r.get('cost') for r in matched)), money(value),
                           money(pnl_value), str(len({r['fund_code'] for r in group})))
            pnl_col = 3 if closed else 4
            money_cols = {1, 2, 3} if closed else {2, 3, 4}
            for column, text in enumerate(columns):
                if not closed and column == 1:
                    self.table.setItem(index, column, QTableWidgetItem(""))
                    cell = RatioProgressBarCell(
                        ratio_fraction or 0.0,
                        ratio_text,
                        is_total=(name == "合计"),
                    )
                    cell.layout().setContentsMargins(4, 2, 4, 2)
                    cell.pbar.setMinimumWidth(14)
                    self.table.setCellWidget(index, column, cell)
                    continue
                self.table.removeCellWidget(index, column)
                item = QTableWidgetItem(text)
                if column in money_cols:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == pnl_col and pnl_value is not None:
                    if pnl_value > 0:
                        item.setForeground(QColor(GREEN))
                    elif pnl_value < 0:
                        item.setForeground(QColor(RED))
                if incomplete:
                    item.setToolTip(f"{incomplete} 条缺少可确认的历史收益；金额基于已导入交易，转换单独统计")
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
        heading = QHBoxLayout()
        heading.addWidget(self.title)
        self.top_n = QSpinBox()
        self.top_n.setRange(1, 50)
        self.top_n.setValue(5)
        self.top_n.setFixedWidth(64)
        self.top_n.setToolTip("分别显示盈利和亏损排名前 N 条基金记录")
        self.top_n.valueChanged.connect(self._top_changed)
        heading.addWidget(self.top_n)
        heading.addStretch()
        self.body.addLayout(heading)
        self.rows = []
        self.closed = False
        labels = QHBoxLayout()
        self.loss_label, self.gain_label = QLabel(), QLabel()
        self.loss_label.setStyleSheet("color:#DC2626; background:#FEF2F2; border-radius:4px; padding:4px;")
        self.gain_label.setStyleSheet("color:#16A34A; background:#F0FDF4; border-radius:4px; padding:4px;")
        labels.addWidget(self.loss_label); labels.addStretch(); labels.addWidget(self.gain_label)
        self.body.addLayout(labels)
        self.chart = ContributionPlot(selected)
        self.body.addWidget(self.chart)

    def _top_changed(self, _value):
        self.render(self.rows, self.closed)

    def render(self, rows, closed=False):
        self.rows, self.closed = list(rows), closed
        n = self.top_n.value()
        self.title.setText(("已清仓基金历史收益" if closed else "持有中基金收益") + "贡献 Top")
        key = "realized" if closed else "floating"
        losses = sorted((r for r in rows if r.get(key) is not None and r[key] < 0), key=lambda r: r[key])
        gains = sorted((r for r in rows if r.get(key) is not None and r[key] > 0), key=lambda r: r[key], reverse=True)
        label = "可核对合计" if closed else "全部"
        self.loss_label.setText(f"亏损贡献（Top {n}） · {label} {money(total(r[key] for r in losses))} CNY")
        self.gain_label.setText(f"盈利贡献（Top {n}） · {label} {money(total(r[key] for r in gains))} CNY")
        self.chart.set_data(losses[:n], gains[:n], key)


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
        side = max(160.0, min(330.0, width * .30))
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
                value = money(row[self.key])
                value_width = painter.fontMetrics().horizontalAdvance(value) + 8
                name_width = max(0, int(label.width() - value_width))
                name = f"{index + 1}  {row.get('fund_name') or row['fund_code']}"
                name = painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, name_width)
                painter.drawText(QRectF(label.x(), label.y(), name_width, label.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
                painter.drawText(QRectF(label.x() + name_width, label.y(), value_width, label.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, value)

    def mousePressEvent(self, event):
        for rect, row in self.hits:
            if rect.contains(event.position()):
                self.selected(row)
                return
        super().mousePressEvent(event)


class HoldingsDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.column() != 1 and not (index.column() == 0 and "\n" in str(index.data() or "")):
            super().paint(painter, option, index)
            return
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        # Qt converts newlines to U+2028 in style options; use the model's
        # original value so the name and code remain two separate lines.
        text, styled.text = str(index.data() or ""), ""
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
            bold_metrics = painter.fontMetrics()
            font.setBold(False)
            font.setPointSizeF(max(9, font.pointSizeF() - 1))
            painter.setFont(font)
            normal_metrics = painter.fontMetrics()
            block_height = bold_metrics.height() + normal_metrics.height()
            top = rect.y() + max(0, (rect.height() - block_height) // 2)
            bold_font = painter.font()
            bold_font.setBold(True)
            painter.setFont(bold_font)
            painter.drawText(rect.x(), top + bold_metrics.ascent(),
                             bold_metrics.elidedText(name, Qt.TextElideMode.ElideRight, rect.width()))
            painter.setFont(font)
            painter.setPen(QColor("#64748B"))
            painter.drawText(rect.x(), top + bold_metrics.height() + normal_metrics.ascent(), code)
        else:
            width = min(rect.width(), painter.fontMetrics().horizontalAdvance(text) + 14)
            badge = QRect(rect.center().x() - width // 2, rect.center().y() - 10, width, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFF2DE" if text == "天天基金" else "#EAF2FF"))
            painter.drawRoundedRect(badge, 5, 5)
            painter.setPen(QColor(PLATFORM_COLORS.get(text, BLUE)))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()



def _fmt_signed_cny(value):
    if value is None:
        return "—"
    v = float(value)
    return f"-¥{abs(v):,.2f}" if v < 0 else f"¥{v:,.2f}"


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
        self._hover_idx = -1
        self._primary_series = None
        self._real_values = {}
        self._cum_mode = None

    def render(self, points, nav_dates=None, *, calendar_spacing=False, step_series=False,
               value_label="持仓总市值", real_values=None, cum_mode=None):
        chart = self.chart()
        chart.removeAllSeries()
        self.area_boundaries.clear()
        self._hover_idx = -1
        self._primary_series = None
        for axis in chart.axes():
            axis.setVisible(False)
            chart.removeAxis(axis)
            axis.deleteLater()
        chart.setBackgroundVisible(False)
        chart.setMargins(QMargins())
        chart.legend().hide()
        self.days = ([ (day - points[0][0]).days for day, _ in points ] if calendar_spacing and points
                     else list(range(len(points))))
        self.values = points
        self.x_axis = None
        self.nav_dates = nav_dates or {}
        self.value_label = value_label
        self.step_series = step_series
        self._real_values = real_values or {}
        self._cum_mode = cum_mode
        values = [float(value) for _, value in points if value is not None]
        if not values:
            chart.update()
            return
        low, high = min(values), max(values)
        pad = max(1, (high - low) * .10, high * .015)
        base = low - pad if low < 0 else max(0, low - pad)
        x = QCategoryAxis()
        x.setLabelsPosition(QCategoryAxis.AxisLabelsPosition.AxisLabelsPositionOnValue)
        padding_x = max(.2, self.days[-1] * .005) if calendar_spacing else .2
        x.setStartValue(-padding_x - 1)
        step = max(1, (len(points) + 4) // 5)
        multi_year = points[0][0].year != points[-1][0].year
        labels = set()
        tick_indexes = set()
        if calendar_spacing:
            span = self.days[-1]
            candidates = sorted({min(range(len(self.days)), key=lambda i: abs(self.days[i] - span * k / 5)) for k in range(6)})
            for index in candidates:
                if not tick_indexes or self.days[index] - self.days[max(tick_indexes)] >= span / 7:
                    tick_indexes.add(index)
        for index, (day, _) in enumerate(points):
            if (index in tick_indexes if calendar_spacing else index % step == 0 or index == len(points) - 1):
                label = day.strftime("%Y-%m" if multi_year else "%m-%d")
                if label not in labels:
                    x.append(label, self.days[index])
                    labels.add(label)
        x.setRange(-padding_x, max(1, self.days[-1]) + padding_x)
        y = QValueAxis()
        y.setRange(base, high + pad)
        y.setLabelFormat("%.0f")
        y.setLabelsColor(QColor("#64748B"))
        y.setGridLineColor(QColor("#F1F5F9"))
        x.setLabelsColor(QColor("#64748B"))
        x.setGridLineVisible(False)
        chart.addAxis(x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(y, Qt.AlignmentFlag.AlignLeft)
        self.x_axis = x
        run = []

        def draw_run():
            if not run:
                return
            line = QLineSeries()
            previous = None
            for day, value in run:
                if step_series and previous is not None:
                    line.append(day, float(previous))
                line.append(day, float(value))
                previous = value
            if len(run) == 1:
                series = line
                line.setColor(QColor(BLUE))
                line.setPointsVisible(True)
                self._primary_series = line
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
                previous = None
                for day, value in run:
                    if step_series and previous is not None:
                        outline.append(day, float(previous))
                    outline.append(day, float(value))
                    previous = value
                outline.setPen(QPen(QColor(BLUE), 1.6))
                chart.addSeries(outline)
                outline.attachAxis(x)
                outline.attachAxis(y)
                self._primary_series = outline

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
        new_idx = -1
        if self.x_axis and self.days and plot.contains(pos):
            start = self.x_axis.min()
            end = self.x_axis.max()
            target = start + (pos.x() - plot.left()) / plot.width() * (end - start)
            idx = max(0, bisect_right(self.days, target) - 1) if self.step_series else min(
                range(len(self.days)), key=lambda i: abs(self.days[i] - target)
            )
            if self.values[idx][1] is not None:
                new_idx = idx
        if new_idx != self._hover_idx:
            self._hover_idx = new_idx
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.viewport().update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not (0 <= self._hover_idx < len(self.values)) or self._primary_series is None:
            return
        day, value = self.values[self._hover_idx]
        if value is None:
            return
        chart = self.chart()
        plot = chart.plotArea()
        if not plot.isValid():
            return
        pt_vp = self.mapFromScene(chart.mapToScene(
            chart.mapToPosition(QPointF(self.days[self._hover_idx], float(value)), self._primary_series)
        ))
        top_vp = self.mapFromScene(chart.mapToScene(QPointF(plot.left(), plot.top())))
        bot_vp = self.mapFromScene(chart.mapToScene(QPointF(plot.right(), plot.bottom())))
        hx = float(pt_vp.x())
        hy = float(pt_vp.y())
        top_y = float(top_vp.y())
        bot_y = float(bot_vp.y())

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 垂直辅助虚线 + 高亮圆点
        painter.setPen(QPen(QColor("#94A3B8"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(hx, top_y), QPointF(hx, bot_y))
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.setBrush(QColor("#1D4ED8"))
        painter.drawEllipse(QPointF(hx, hy), 4.5, 4.5)

        # 白色圆角卡片悬浮框
        tip_lines: list[tuple[str, str]] = [
            ("", str(day)),
            ("#2563EB", f"{self.value_label}：{_fmt_signed_cny(value)}"),
        ]
        if self._cum_mode == "zeroed":
            if day in self._real_values:
                tip_lines.append(("#64748B", f"真实累计盈亏：{_fmt_signed_cny(self._real_values[day])}"))
            tip_lines.append(("", "模式：区间归零"))
        elif self._cum_mode == "real":
            tip_lines.append(("", "模式：真实累计"))
        dates = self.nav_dates.get(day)
        if dates:
            tip_lines.append(("", f"净值日期：{dates}"))

        tip_font = QFont(".AppleSystemUIFont", 10)
        tip_bold = QFont(".AppleSystemUIFont", 10, QFont.Weight.Bold)
        painter.setFont(tip_bold)
        tfm = painter.fontMetrics()
        tip_w = max(tfm.horizontalAdvance(t) for _, t in tip_lines) + 26.0
        tip_h = 10.0 + len(tip_lines) * 17.0
        vw = float(self.viewport().width())
        tx = hx + 10.0
        if tx + tip_w > vw - 4.0:
            tx = hx - tip_w - 10.0
        tx = max(4.0, min(vw - tip_w - 4.0, tx))
        ty = max(4.0, min(bot_y - tip_h - 4.0, top_y + 4.0))

        painter.setPen(QPen(QColor("#CBD5E1"), 1))
        painter.setBrush(QColor(255, 255, 255, 245))
        painter.drawRoundedRect(QRectF(tx, ty, tip_w, tip_h), 7.0, 7.0)
        for li, (dot_c, txt) in enumerate(tip_lines):
            ly = ty + 5.0 + li * 17.0
            painter.setFont(tip_bold if li == 0 else tip_font)
            tx_pos = tx + 8.0
            if dot_c:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(dot_c))
                painter.drawEllipse(QPointF(tx + 12.0, ly + 8.0), 3.0, 3.0)
                tx_pos = tx + 19.0
            painter.setPen(QColor("#0F172A"))
            painter.drawText(
                QRectF(tx_pos, ly, tip_w - (tx_pos - tx) - 4.0, 16.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                txt,
            )
