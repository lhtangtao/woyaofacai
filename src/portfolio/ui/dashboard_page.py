import calendar
from collections import defaultdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListView,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.analytics import strict_sum
from portfolio.engine.dashboard import (
    annual_rate,
    compound_rates,
    daily_rates,
    live_daily_rows,
    metric_gap,
    monthly_gains,
    monthly_rates,
    quarterly_gains,
    quarterly_rates,
)
from portfolio.ui.metric_card import MetricCard
from portfolio.ui.report_page import ReportPage
from portfolio.ui.theme import tokens


class DashboardPage(QWidget):
    drill = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analytics = None
        self.daily_rows, self.live_day = (), None
        self.cards, self.widgets = [], []
        outer = QVBoxLayout(self)
        self.dates = QLabel("等待只读计算结果")
        outer.addWidget(self.dates)
        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        for index in range(18):
            card = MetricCard("—")
            card.setMinimumHeight(94)
            card.setObjectName("metricCard")
            card.clicked.connect(lambda checked=False, n=index: self.drill.emit("metric", str(n)))
            self.cards.append(card)
        body.addLayout(self.grid)
        self._arrange()
        calendars = QHBoxLayout()
        calendars.setSpacing(16)
        daily_panel, yearly_panel = QWidget(), QWidget()
        daily_layout, yearly_layout = QVBoxLayout(daily_panel), QVBoxLayout(yearly_panel)
        for layout in (daily_layout, yearly_layout):
            layout.setContentsMargins(0, 0, 0, 0)
        calendars.addWidget(daily_panel, 3)
        calendars.addWidget(yearly_panel, 2)
        body.addLayout(calendars)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("收益交易日历"))
        self.month = QComboBox()
        self.month.setMinimumWidth(160)
        self.month.setMinimumContentsLength(9)
        self.month.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        month_view = QListView()
        month_view.setMinimumWidth(180)
        month_view.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.month.setView(month_view)
        self.month.setMaxVisibleItems(12)
        self.month.setStyleSheet('QComboBox { combobox-popup: 0; }')
        self.month.currentTextChanged.connect(self.calendar)
        controls.addWidget(self.month)
        controls.addStretch()
        daily_layout.addLayout(controls)
        self.calendar_table = QTableWidget(0, 6)
        self.calendar_table.setHorizontalHeaderLabels(["周一", "周二", "周三", "周四", "周五", "本周合计"])
        self.calendar_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.calendar_table.verticalHeader().hide()
        self.calendar_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.calendar_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.calendar_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.calendar_table.setFixedHeight(336)
        self.calendar_table.setStyleSheet('QTableView::item { padding:0 4px; } QHeaderView::section { padding:0 4px; }')
        self.calendar_table.cellClicked.connect(self.calendar_clicked)
        self.calendar_table.viewport().installEventFilter(self)
        daily_layout.addWidget(self.calendar_table)
        daily_note = QLabel('点击日期查看收益；实时估值计入日、周、月汇总，收盘记录更新后自动替换。')
        daily_note.setObjectName('secondary')
        daily_note.setWordWrap(True)
        daily_layout.addWidget(daily_note)
        annual_heading = QHBoxLayout()
        self.annual_title = QLabel('本年每月盈亏')
        annual_heading.addWidget(self.annual_title)
        self.year = QComboBox()
        self.year.setMinimumWidth(120)
        self.year.setMinimumContentsLength(6)
        self.year.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        year_view = QListView()
        year_view.setMinimumWidth(140)
        self.year.setView(year_view)
        self.year.setStyleSheet('QComboBox { combobox-popup: 0; }')
        self.year.currentTextChanged.connect(self.annual_year_changed)
        annual_heading.addWidget(self.year)
        annual_heading.addStretch()
        self.annual_total = QLabel('年度合计：—')
        yearly_layout.addLayout(annual_heading)
        self.monthly_table = QTableWidget(4, 4)
        self.monthly_table.setStyleSheet('QTableView::item { padding:0 4px; }')
        self.monthly_table.horizontalHeader().hide()
        self.monthly_table.setVerticalHeaderLabels(['一季度', '二季度', '三季度', '四季度'])
        self.monthly_table.verticalHeader().setFixedWidth(60)
        self.monthly_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.monthly_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.monthly_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.monthly_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.monthly_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.monthly_table.setFixedHeight(336)
        self.monthly_table.viewport().installEventFilter(self)
        yearly_layout.addWidget(self.monthly_table)
        yearly_layout.addWidget(self.annual_total)
        annual_note = QLabel('按已有每日盈亏及当日实时估值汇总；无数据不当作零。')
        annual_note.setObjectName('secondary')
        annual_note.setWordWrap(True)
        yearly_layout.addWidget(annual_note)
        daily_layout.addStretch()
        self.summaries = ReportPage(("渠道汇总", "分类汇总", "季度矩阵", "月度趋势"))
        self.summaries.setMinimumHeight(480)
        body.addWidget(self.summaries)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _arrange(self):
        columns = 6 if self.width() >= 1160 else 3 if self.width() >= 720 else 2
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event):
        self._arrange()
        super().resizeEvent(event)

    def clear(self):
        self.analytics = None
        self.daily_rows, self.live_day = (), None
        self.day_rates = {}
        self.metrics = None
        for card in self.cards:
            card.setText("计算中…")
        self.summaries.clear()
        self.calendar_table.setRowCount(0)
        self.monthly_table.clearContents()
        self.year.setEnabled(False)
        self.annual_title.setText('本年每月盈亏')
        self.annual_total.setText('年度合计：—')

    def set_analytics(self, analytics):
        self.analytics = analytics
        self.dates.setText(f"历史估值日：{', '.join(analytics.valuation_days) or '无有效估值'}　历史收益最新日：{analytics.latest_profit_day or '无'}　口径：EXCEL_COMPAT　币种：{analytics.scope.reporting_currency}")
        self.set_metrics(analytics.metrics)
        self.summaries.set_reports(analytics.reports)
    def set_metrics(self, metrics):
        if not self.analytics:
            return
        analytics = self.analytics
        from portfolio.services.overview_metrics import overview_metrics
        metrics=overview_metrics(analytics,metrics)
        for card, metric in zip(self.cards, metrics):
            gap, _ = metric_gap(metric.name, analytics.reports['每日持仓收益'].rows, analytics.scope.as_of) if metric.value is None else ('', '')
            if metric.value is None and metric.formula.startswith('长桥'):
                gap = '等待报价或收盘基准'
            always_neutral = metric.name in ("当前总资产", "当前持仓总成本")
            if always_neutral:
                value = f"— {gap}" if metric.value is None else f"{metric.value:.2%}" if metric.ratio else f"{metric.value:,.2f}"
            else:
                value = f"— {gap}" if metric.value is None else f"{metric.value:+.2%}" if metric.ratio else f"{metric.value:+,.2f}"
            card.setText(f"{metric.name}\n{value}\n{metric.day}")
            t = tokens(self)
            if always_neutral:
                color = t.heading
            else:
                is_performance = metric.ratio or any(word in metric.name for word in ("盈亏", "收益", "微操"))
                color = (t.profit if metric.value > 0 else t.loss if metric.value < 0 else t.muted) if is_performance and metric.value is not None else t.heading
            card.setStyleSheet(f"QPushButton {{text-align:left; padding:12px; font-size:14px; border-radius:12px; border:1px solid {t.border}; background:{t.panel}; color:{color};}}")

        self.update_daily_views(metrics)

    def waiting_live(self):
        if self.analytics:
            from portfolio.services.overview_metrics import overview_metrics
            keep = {'已清仓收益', '上月盈亏', '去年盈亏', '本月微操收益', '本年微操收益','当前持仓总成本'}
            for card, metric in zip(self.cards, overview_metrics(self.analytics)):
                if metric.name not in keep:
                    card.setText(metric.name+'\n— 正在读取本时段行情')

    def update_daily_views(self, metrics):
        self.metrics = metrics
        self.daily_rows, self.live_day = live_daily_rows(self.analytics.reports['每日持仓收益'].rows, metrics)
        self.day_rates = daily_rates(self.daily_rows, metrics, self.live_day)
        self.estimate_days = {r.values[0] for r in self.daily_rows if r.metadata.get('historical_estimate')}
        months = sorted({r.values[0][:7] for r in self.daily_rows}, reverse=True)
        selected_month, selected_year = self.month.currentText(), self.year.currentText()
        self.month.blockSignals(True)
        self.month.clear()
        self.month.addItems(months)
        if selected_month in months:
            self.month.setCurrentText(selected_month)
        self.month.blockSignals(False)
        years = sorted({month[:4] for month in months}, reverse=True)
        self.year.blockSignals(True)
        self.year.clear()
        self.year.addItems(years)
        if selected_year in years:
            self.year.setCurrentText(selected_year)
        self.year.blockSignals(False)
        self.year.setEnabled(bool(years))
        self.calendar()
        self.annual_year_changed(self.year.currentText())

    def calendar(self, *_):
        if not self.analytics or not self.month.currentText():
            return
        y, m = map(int, self.month.currentText().split("-"))
        days = defaultdict(list)
        for row in self.daily_rows:
            days[row.values[0]].append(row.values[19])
        weeks = calendar.Calendar().monthdatescalendar(y, m)
        self.calendar_table.setRowCount(len(weeks))
        row_height = max(64, self.calendar_table.fontMetrics().lineSpacing() * 3 + 12, 332 // len(weeks))
        for week_index, week in enumerate(weeks):
            totals = []
            week_days = []
            for column, day in enumerate(week[:5]):
                key = day.isoformat()
                gain = strict_sum(days[key]) if key in days and day.month == m else None
                rate = self.day_rates.get(key) if key in days and day.month == m else None
                rate_str = f"\n{rate:+.2%}" if rate is not None else ""
                if day.month == m:
                    text = f"{day.month}/{day.day}\n{gain:+,.2f}{rate_str}" if gain is not None else f"{day.month}/{day.day}\n— 无数据"
                else:
                    text = ""
                item = QTableWidgetItem(text)
                if key in getattr(self, 'estimate_days', set()) and day.month == m:
                    item.setText(f'{day.month}/{day.day} · 历史估值\n' + (f'{gain:+,.2f}{rate_str}' if gain is not None else '— 数据未完整'))
                if key == self.live_day and day.month == m:
                    item.setText(f'{day.month}/{day.day} · 实时估值\n' + (f'{gain:+,.2f}{rate_str}' if gain is not None else '— 等待报价/基准'))
                item.setData(Qt.ItemDataRole.UserRole, key if day.month == m else "")
                if gain is not None:
                    from PySide6.QtGui import QColor
                    item.setForeground(QColor(tokens(self).profit if gain > 0 else tokens(self).loss if gain < 0 else tokens(self).muted))
                self.calendar_table.setItem(week_index, column, item)
                if day.month == m and key in days:
                    totals.append(gain)
                    week_days.append(key)
            total = strict_sum(totals) if totals else None
            week_rate = compound_rates(week_days, self.day_rates) if week_days else None
            rate_str = f"\n{week_rate:+.2%}" if week_rate is not None else ""
            text = f"{total:+,.2f}{rate_str}" if total is not None else "—"
            week_item = QTableWidgetItem(text)
            if total is not None:
                from PySide6.QtGui import QColor
                week_item.setForeground(QColor(tokens(self).profit if total > 0 else tokens(self).loss if total < 0 else tokens(self).muted))
            self.calendar_table.setItem(week_index, 5, week_item)
            self.calendar_table.setRowHeight(week_index, row_height)
        # Include the header and frame as well as every week. Hiding the inner
        # scrollbar alone would otherwise leave the first/last dates clipped.
        height = (self.calendar_table.horizontalHeader().height()
                  + sum(self.calendar_table.rowHeight(i) for i in range(len(weeks)))
                  + self.calendar_table.frameWidth() * 2)
        self.calendar_table.setFixedHeight(height)
        self.calendar_table.scrollToTop()
        self.monthly_table.setFixedHeight(max(336, height))

    def annual_year_changed(self, text):
        if self.analytics and text:
            self.annual_months(int(text))

    def annual_months(self, year):
        from PySide6.QtGui import QColor

        daily = self.daily_rows
        gains, total = monthly_gains(daily, year)
        m_rates = monthly_rates(daily, year, getattr(self, 'day_rates', {}), getattr(self, 'metrics', None), self.live_day)
        q_rates = quarterly_rates(daily, year, getattr(self, 'day_rates', {}))
        a_rate = annual_rate(daily, year, getattr(self, 'day_rates', {}), getattr(self, 'metrics', None), self.live_day)
        self.annual_title.setText(f'{year} 年每月盈亏（{self.analytics.scope.reporting_currency}）')
        annual_text = '年度合计：'
        if total is not None:
            annual_text += f'{total:+,.2f}'
            if a_rate is not None:
                annual_text += f'（{a_rate:+.2%}）'
        else:
            annual_text += '— 数据未完整'
        self.annual_total.setText(annual_text)
        for index, gain in enumerate(gains):
            rate = m_rates[index]
            rate_str = f'\n{rate:+.2%}' if rate is not None else ''
            label = f'{index + 1}月\n' + (f'{gain:+,.2f}{rate_str}' if gain is not None else '— 无数据/未完整')
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            t = tokens(self)
            item.setForeground(QColor(t.profit if gain is not None and gain > 0 else t.loss if gain is not None and gain < 0 else t.muted))
            self.monthly_table.setItem(index // 3, index % 3, item)
        for quarter, gain in enumerate(quarterly_gains(daily, year)):
            rate = q_rates[quarter]
            rate_str = f'\n{rate:+.2%}' if rate is not None else ''
            value = f'{gain:+,.2f}{rate_str}' if gain is not None else '— 无数据/未完整'
            item = QTableWidgetItem('季度合计\n' + value)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            t = tokens(self)
            item.setForeground(QColor(t.profit if gain is not None and gain > 0 else t.loss if gain is not None and gain < 0 else t.muted))
            self.monthly_table.setItem(quarter, 3, item)

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.ToolTip:
            return True
        return super().eventFilter(watched, event)

    def calendar_clicked(self, row, col):
        item = self.calendar_table.item(row, col)
        if item and item.data(Qt.ItemDataRole.UserRole):
            key = item.data(Qt.ItemDataRole.UserRole)
            self.drill.emit("live_day" if key == self.live_day else "day", key)
