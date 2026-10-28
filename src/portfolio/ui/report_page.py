import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QDate, QStringListModel, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.analytics import ReportRow
from portfolio.domain.models import exact_text
from portfolio.engine.today_holdings import lifetime_total, today_summary, today_summary_row
from portfolio.ui.channel_overview import ChannelOverview
from portfolio.ui.finance_table import FinanceModel
from portfolio.ui.metric_card import MetricCard
from portfolio.ui.report_table import ReportTable
from portfolio.ui.theme import numeric_font, tokens


class ReportModel(FinanceModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns = ()

    def columnCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal and section < len(self.columns):
            return self.columns[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section] if orientation == Qt.Orientation.Horizontal and section < len(self.columns) else str(section + 1)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, column = self.rows[index.row()], self.columns[index.column()]
        value = row.values[index.column()] if index.column() < len(row.values) else None
        if row.metadata.get('display_summary'):
            if role == Qt.ItemDataRole.BackgroundRole:
                return QColor(tokens(self.parent()).alternate)
            if role == Qt.ItemDataRole.FontRole:
                font = numeric_font(12 if getattr(self.parent(), 'compact', False) else 13)
                font.setBold(True)
                return font
        if role == Qt.ItemDataRole.DisplayRole:
            if isinstance(value, Decimal):
                if column == "持有股数":
                    return format(value, ",.8f").rstrip("0").rstrip(".")
                if any(word in column for word in ("比例", "占比", "收益率", "年化", "涨幅", "涨跌幅", "胜率", "涨跌")):
                    if "涨跌" in column and len(row.values) > 4 and row.values[4] == '已清仓':
                        return f"{value * 100:+,.2f}%"
                    return f"{value * 100:,.2f}%"
                if "ID" in column:
                    return exact_text(value)
                return f"{value:,.2f}"
            return exact_text(value)
        if role == Qt.ItemDataRole.ToolTipRole:
            if column.startswith('当前价(') and row.metadata.get('current_price_status'):
                return f"{row.metadata['current_price_status']} · {row.metadata.get('current_price_time') or '时间未知'}"
            return None
        if role == Qt.ItemDataRole.FontRole and isinstance(value, Decimal):
            return numeric_font(12 if getattr(self.parent(), "compact", False) else 13)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if isinstance(value, (Decimal, int)) else Qt.AlignmentFlag.AlignLeft))
        if role == Qt.ItemDataRole.ForegroundRole:
            if value is None:
                return QColor(tokens(self.parent()).subtle)
            if isinstance(value, Decimal) and any(w in column for w in ("盈亏", "收益", "价差", "涨跌", "涨幅")):
                if "回本" in column and not (len(row.values) > 4 and row.values[4] == '已清仓'):
                    return None
                return QColor(tokens(self.parent()).profit if value > 0 else tokens(self.parent()).loss if value < 0 else tokens(self.parent()).muted)
        return None


class ReportPage(QWidget):
    drill = Signal(object)
    export_requested = Signal(object, object)
    provenance_requested = Signal(object, object)

    def __init__(self, names=(), parent=None, *, broker_people=False, show_selector=True):
        super().__init__(parent)
        self.reports, self.report, self.page, self.descending = {}, None, 0, False
        self.sorted_rows = ()
        self.overview_rows = ()
        self.cumulative_totals = {}
        self.snapshot_date = ''
        self.sort_orders = {}
        self.sort_menu = QMenu(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self.broker_page = None
        if broker_people:
            from portfolio.ui.broker_people import BrokerPeoplePage
            self.broker_page = BrokerPeoplePage()
            self.broker_page.hide()
            outer.addWidget(self.broker_page)
        content = QWidget()
        self.scroll.setWidget(content)
        layout = QVBoxLayout(content)
        controls = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.addItems(names)
        self.selector.setVisible(show_selector)
        controls.addWidget(self.selector)
        self.stock_status_filter = QComboBox()
        self.stock_status_filter.setMinimumWidth(105)
        self.stock_status_filter.setToolTip('按当前状态筛选（全部 / 持有中 / 已清仓），可与搜索和列排序同时使用。')
        self.stock_status_filter.addItem('全部', None)
        for state in ('持有中', '已清仓'):
            self.stock_status_filter.addItem(state, state)
        self.stock_status_filter.setCurrentIndex(self.stock_status_filter.findData('持有中'))
        self.stock_status_filter.currentIndexChanged.connect(self.apply_local_filter)
        self.stock_status_filter.hide()
        controls.addWidget(self.stock_status_filter)
        self.today_sort = QComboBox()
        self.today_sort.addItem('排序方式', None)
        for label, column, descending in (('盈利最多', 6, True), ('亏损最多', 6, False), ('涨幅最大', 5, True), ('跌幅最大', 5, False), ('占比最高', 13, True)):
            self.today_sort.addItem(label, (column, descending))
        self.today_sort.hide()
        self.today_sort.currentIndexChanged.connect(self.sort_today)
        controls.addWidget(self.today_sort)
        self.local_search = QLineEdit()
        self.local_search.setPlaceholderText("筛选 Ticker…")
        self.local_search.setClearButtonEnabled(True)
        self.ticker_completer = QCompleter([], self)
        self.ticker_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.ticker_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.ticker_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.ticker_completer.setMaxVisibleItems(10)
        self.ticker_completer.popup().setMinimumWidth(180)
        self.local_search.setCompleter(self.ticker_completer)
        self.local_search.textEdited.connect(self._on_local_search_edited)
        self.local_search.textChanged.connect(self.apply_local_filter)
        self.ticker_completer.activated.connect(self._on_completer_activated)
        controls.addWidget(self.local_search)
        controls.addStretch()
        self.status = QLabel("尚未连接数据库")
        controls.addWidget(self.status)
        self.export_mode = QComboBox()
        self.export_mode.addItems(["当前筛选全部行", "本页"])
        controls.addWidget(self.export_mode)
        button = QPushButton("导出…")
        button.clicked.connect(self.export)
        controls.addWidget(button)
        layout.addLayout(controls)
        self.today_overview = QWidget()
        self.today_overview.setFixedHeight(72)
        overview = QHBoxLayout(self.today_overview)
        overview.setContentsMargins(0, 0, 0, 0)
        self.today_cards = [MetricCard('—') for _ in range(4)]
        for card in self.today_cards:
            card.setMinimumHeight(68)
            overview.addWidget(card, 1)
        self.today_overview.hide()
        layout.insertWidget(0, self.today_overview)
        from portfolio.ui.intraday_pnl import IntradayPnlChart
        self.intraday_chart = IntradayPnlChart()
        layout.insertWidget(1, self.intraday_chart)
        self.channel_overview = ChannelOverview(self)
        self.channel_overview.channel_clicked.connect(self.apply_local_filter)
        self.channel_overview.hide()
        layout.insertWidget(2, self.channel_overview)
        self.note = QLabel("显示列保留原 Excel 顺序；双击穿透证券；右键表头隐藏列。")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        split = QSplitter(Qt.Orientation.Vertical)
        self.split = split
        self.table = ReportTable()
        self.table.finance_model = ReportModel(self.table)
        self.table.setModel(self.table.finance_model)
        self.table.frozen.setModel(self.table.finance_model)
        self.table.frozen.setSelectionModel(self.table.selectionModel())
        self.table.horizontalHeader().setSectionsMovable(True)
        for header in (self.table.horizontalHeader(), self.table.frozen.horizontalHeader()):
            header.setSectionsClickable(True)
            header.sectionClicked.connect(lambda column, header=header: self.open_sort_menu(column, header))
        self.table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self.column_menu)
        self.table.doubleClicked.connect(lambda index: self.drill.emit(self.table.finance_model.rows[index.row()].metadata)
                                         if not self.table.finance_model.rows[index.row()].metadata.get('display_summary') else None)
        self.table.selectionModel().currentRowChanged.connect(self.show_details)
        split.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("点击记录查看精确原值、估值日期、完整性和原始记录。")
        split.addWidget(self.details)
        split.setSizes([490, 110])
        layout.addWidget(split)
        footer = QHBoxLayout()
        self.total = QLabel()
        footer.addWidget(self.total)
        footer.addStretch()
        prev, next = QPushButton("上一页"), QPushButton("下一页")
        self.paging_buttons = (prev, next)
        prev.clicked.connect(lambda: self.turn(-1))
        next.clicked.connect(lambda: self.turn(1))
        footer.addWidget(prev)
        footer.addWidget(next)
        layout.addLayout(footer)
        self.selector.currentTextChanged.connect(self.select_report)

    def clear(self, message="正在计算当前筛选…"):
        if self.broker_page:
            self.broker_page.clear()
        self.reports, self.report, self.sorted_rows = {}, None, ()
        self.overview_rows = ()
        self.table.finance_model.replace(())
        self.details.clear()
        self.cumulative_totals = {}
        self.stock_status_filter.hide()
        self.today_overview.hide()
        self.channel_overview.hide()
        self.intraday_chart.hide()
        self.intraday_chart.key = None
        if getattr(self, 'trend_widget', None) is not None:
            self.trend_widget.hide()
        self.today_sort.hide()
        self.status.setText(message)

    def _on_local_search_edited(self, text):
        if text.strip():
            self.ticker_completer.setCompletionPrefix(text.strip())
            self.ticker_completer.complete()
        else:
            self.ticker_completer.popup().hide()

    def _on_completer_activated(self, text):
        self.local_search.setText(text)
        self.apply_local_filter()

    def update_ticker_completer(self):
        if self.report and 'Ticker' in self.report.columns:
            ticker_col = self.report.columns.index('Ticker')
            tickers = sorted({str(r.values[ticker_col]) for r in self.report.rows if r.values[ticker_col]})
            self.ticker_completer.setModel(QStringListModel(tickers, self))
            self.local_search.setPlaceholderText("输入 Ticker 自动提示 / 模糊筛选…")
        else:
            self.ticker_completer.setModel(QStringListModel([], self))
            self.local_search.setPlaceholderText("本表筛选…")

    def set_reports(self, reports):
        self.reports = reports
        self.select_report(self.selector.currentText())
        self.update_ticker_completer()

    def select_report(self, name):
        if self.broker_page:
            people = name in ('渠道汇总', '股票归属')
            self.scroll.setVisible(not people)
            self.broker_page.setVisible(people)
            if people:
                if name == '股票归属':
                    if self.broker_page.mode.currentText() != '股票归属':
                        self.broker_page.mode.setCurrentText('股票归属')
                elif self.broker_page.mode.currentText() == '股票归属':
                    self.broker_page.mode.setCurrentIndex(0)
                self.report = self.reports.get(name)
                return
        was_today = bool(self.report and self.report.name == '今日盈亏')
        if name != '今日盈亏':
            self.overview_rows = ()
        self.report = self.reports.get(name)
        self.stock_status_filter.setVisible(bool(self.report and name in ('股票汇总', '人员资产') and '当前状态' in self.report.columns))
        is_today = bool(self.report and name == '今日盈亏')
        self.details.setVisible(not is_today)
        self.today_overview.setVisible(is_today)
        trend_mode = getattr(self, 'trend_mode', 0)
        self.intraday_chart.setVisible(is_today and '$' in self.report.columns[6] and trend_mode == 0)
        if getattr(self, 'trend_widget', None) is not None:
            self.trend_widget.setVisible(is_today and trend_mode > 0)
        self.channel_overview.setVisible(is_today)
        self.today_sort.setVisible(is_today)
        if is_today:
            self.sort_orders.setdefault(name, (self.report.columns[6], True))
        elif self.report and name not in self.sort_orders:
            date_col = next((c for c in ("交易时间", "日期", "月份", "最终清仓时间", "首次买入时间", "折算日期", "rate_date", "business_date", "effective_from", "imported_at_utc") if c in self.report.columns), None)
            if not date_col:
                date_col = next((c for c in self.report.columns if any(k in c for k in ("日期", "月份", "时间", "effective_from", "effective_to", "business_date", "imported_at_utc", "报价UTC", "rate_date"))), None)
            if date_col:
                self.sort_orders[name] = (date_col, True)
        self.page = 0
        self.sorted_rows = self.report.rows if self.report else ()
        model = self.table.finance_model
        model.beginResetModel()
        model.columns = self.report.columns if self.report else ()
        model.rows = ()
        model.endResetModel()
        header = self.table.horizontalHeader()
        for logical in range(header.count()):
            header.moveSection(header.visualIndex(logical), logical)
        if is_today:
            order = [i for i in (0, 1, 2, 9, 3, 4, 12, 11, 13, 5, 6, 10, 7, 8) if i < header.count()]
            for visual, logical in enumerate(order):
                header.moveSection(header.visualIndex(logical), visual)
        elif name in ('当前持仓', '实时持仓'):
            preferred = ("渠道", "分类", "Ticker", "持仓数量", "当前价($)", "回本涨幅", "持仓市值($)", "持仓成本价($)", "累计净投入($)", "持仓盈亏金额($)")
            order = [model.columns.index(c) for c in preferred if c in model.columns]
            order += [i for i in range(len(model.columns)) if i not in order]
            for visual, logical in enumerate(order):
                header.moveSection(header.visualIndex(logical), visual)
        for column in range(len(model.columns)):
            col_name = model.columns[column]
            is_holdings = name in ('当前持仓', '实时持仓')
            hidden = (is_holdings and column in (7, 8, 11, 13)) or (col_name in ('报价时间', '行情状态', '行情时段'))
            self.table.setColumnHidden(column, hidden)
            self.table.frozen.setColumnHidden(column, column != 0)
        self.update_ticker_completer()
        self.note.setText(self.report.note if self.report and self.report.note else "双击进入该证券的交易回放；明细保留所有来源 ID。")
        self.apply_local_filter()

    def show_page(self):
        is_today = bool(self.report and self.report.name == '今日盈亏')
        rows = self.sorted_rows if is_today else self.sorted_rows[self.page * 200:(self.page + 1) * 200]
        if is_today:
            rows = (today_summary_row(rows, self.report.columns, self.channel_overview.selected_channel), *rows)
        self.table.finance_model.replace(rows)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if is_today else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if is_today else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        for button in self.paging_buttons:
            button.setVisible(not is_today)
        if is_today:
            height = self.table.horizontalHeader().height() + sum(self.table.rowHeight(i) for i in range(len(rows))) + 6
            self.table.setFixedHeight(height)
            self.split.setFixedHeight(height)
        else:
            for widget in (self.table, self.split):
                widget.setMinimumHeight(0)
                widget.setMaximumHeight(16777215)
        self.table.schedule_fit()
        self.table._frozen_geometry()
        channel = self.channel_overview.selected_channel if is_today else None
        self.status.setText(f"共 {len(self.sorted_rows):,} 条" + ((f" · {channel}" if channel else " · 全部展示") if is_today else f" · 第 {self.page + 1} 页"))
        if self.report and self.report.name == '今日盈亏':
            self.intraday_chart.draw(self.local_search.text(), self.channel_overview.selected_channel)
            if getattr(self,'trend_widget',None) is not None:
                self.trend_widget.set_parent_filters(self.channel_overview.selected_channel,self.local_search.text())
            self.channel_overview.set_rows(self.overview_rows, 'CNY' if '¥' in self.report.columns[6] else 'USD')
            pnl, rate, up, down, flat, unknown = today_summary(self.sorted_rows)
            totals = self.cumulative_totals
            selected = self.channel_overview.selected_channel
            cumulative = lifetime_total(totals, selected)
            val_time = next((r.metadata.get('valuation_time') for r in self.sorted_rows if r.metadata.get('valuation_time')), '')
            report_day = next((r.metadata.get('trading_day') for r in self.sorted_rows if r.metadata.get('trading_day')), '')
            curr_date_str = QDate.currentDate().toString('yyyy-MM-dd')
            snap_str = self.snapshot_date or curr_date_str
            is_today = not self.snapshot_date or self.snapshot_date == curr_date_str
            trend_mode = getattr(self, 'trend_mode', 0)
            if trend_mode in (1, 2):
                from collections import defaultdict
                from portfolio.domain.analytics import ratio, strict_sum
                is_year = (trend_mode == 2)
                period_str = snap_str[:4] if is_year else snap_str[:7]
                period_label = '当年' if is_year else '当月'
                search_tk = self.local_search.text().strip().upper()
                tw = getattr(self, 'trend_widget', None)
                daily_rep = None
                if tw is not None and getattr(tw, '_shared_analytics', None) is not None and getattr(tw, '_shared_period', None) == period_str and getattr(tw, '_shared_year', None) == is_year:
                    daily_rep = tw._shared_analytics.reports.get('每日持仓收益')
                if daily_rep is None:
                    daily_rep = self.reports.get('每日持仓收益')
                period_daily = [
                    r for r in (daily_rep.rows if daily_rep else ())
                    if str(r.values[0]).startswith(period_str)
                    and (selected is None or r.values[1] == selected)
                    and (not search_tk or search_tk in str(r.values[3]).upper())
                ]
                period_pnl = None
                if tw is not None and getattr(tw, '_shared_period', None) == period_str and getattr(tw, '_shared_year', None) == is_year and tw.canvas.chart:
                    valid_pts = [v for s in tw.canvas.chart.series for _, v in s.points if v is not None]
                    if valid_pts:
                        period_pnl = valid_pts[-1]
                if period_pnl is None and period_daily:
                    period_pnl = strict_sum(r.values[19] for r in period_daily if r.values[19] is not None)
                    max_day = max((str(r.values[0]) for r in period_daily), default='')
                    if pnl is not None and report_day and report_day.startswith(period_str) and report_day > max_day:
                        period_pnl = (period_pnl or Decimal(0)) + pnl
                current_mv = strict_sum(
                    r.values[11] if isinstance(r.values[11], Decimal)
                    else (r.values[2] * r.values[4] if r.values[2] is not None and r.values[4] is not None else None)
                    for r in self.sorted_rows
                )
                base_cap = (current_mv - period_pnl) if (current_mv is not None and period_pnl is not None) else None
                if base_cap is None or base_cap <= 0:
                    first_day = min((str(r.values[0]) for r in period_daily), default='')
                    open_mv = sum((r.values[15] for r in period_daily if str(r.values[0]) == first_day and isinstance(r.values[15], Decimal)), Decimal(0))
                    buy_mv = sum((r.values[16] for r in period_daily if isinstance(r.values[16], Decimal)), Decimal(0))
                    base_cap = open_mv + buy_mv if (open_mv + buy_mv) > 0 else None
                period_rate = ratio(period_pnl, base_cap) if (period_pnl is not None and base_cap is not None and base_cap > 0) else None
                tk_gains = defaultdict(Decimal)
                max_daily = max((str(r.values[0]) for r in period_daily), default='')
                for r in period_daily:
                    if r.values[19] is not None:
                        tk_gains[r.values[3]] += r.values[19]
                if report_day and report_day.startswith(period_str) and report_day > max_daily:
                    for r in self.sorted_rows:
                        if r.values[6] is not None:
                            tk_gains[r.values[1]] += r.values[6]
                p_up = sum(1 for v in tk_gains.values() if v > 0)
                p_down = sum(1 for v in tk_gains.values() if v < 0)
                p_flat = sum(1 for v in tk_gains.values() if v == 0)
                values = ((f'{period_label}累计盈亏（{period_str}）', f'{period_pnl:+,.2f}' if period_pnl is not None else '— 正在计算…', period_pnl),
                          (f'{period_label}收益率', f'{period_rate:+.2%}' if period_rate is not None else '— 正在计算…', period_rate),
                          (f'{period_label}盈利 / 亏损标的', f'{p_up} / {p_down}　平盘 {p_flat}', None),
                          ('累计盈亏（含已清仓）', f'{cumulative:+,.2f}' if cumulative is not None else '— 数据不完整', cumulative))
            else:
                if val_time:
                    day_title = f'{"当前" if is_today else "所选日"}持仓估值（截至 {val_time}）'
                elif report_day and report_day != snap_str:
                    day_title = f'有效收盘日 ({report_day}) 持仓盈亏'
                else:
                    day_title = '当前持仓当日盈亏' if is_today else '所选日持仓盈亏'
                values = ((day_title, f'{pnl:+,.2f}' if pnl is not None else '— 等待报价/基准', pnl),
                          ('当日收益率', f'{rate:+.2%}' if rate is not None else '— 等待报价/基准', rate),
                          ('上涨 / 下跌标的', f'{up} / {down}　平盘 {flat}　待报价 {unknown}', None),
                          ('累计盈亏（含已清仓）', f'{cumulative:+,.2f}' if cumulative is not None else '— 数据不完整', cumulative))
            t = tokens(self)
            for card, (title, text, number) in zip(self.today_cards, values):
                card.setText(title + '\n' + text)
                color = t.profit if number is not None and number > 0 else t.loss if number is not None and number < 0 else t.heading
                card.setStyleSheet(f'QPushButton {{text-align:left;padding:12px;border:1px solid {t.border};border-radius:12px;background:{t.panel};color:{color};font-size:15px;}}')
            saved = self.sort_orders.get(self.report.name)
            index = next((i for i in range(1, self.today_sort.count()) if saved == (self.report.columns[self.today_sort.itemData(i)[0]], self.today_sort.itemData(i)[1])), 0)
            self.today_sort.blockSignals(True)
            self.today_sort.setCurrentIndex(index)
            self.today_sort.blockSignals(False)
        if not self.sorted_rows:
            self.total.setText("该筛选组合暂无数据 / 未发现有效行情。")
        else:
            totals = []
            for index, column in enumerate(self.report.columns):
                if any(word in column for word in ("市值($)", "持仓盈亏($)", "收益($)", "盈亏金额($)", "净投入($)")):
                    values = [r.values[index] for r in self.sorted_rows]
                    if all(v is None for v in values):
                        continue
                    if all(isinstance(v, Decimal) for v in values):
                        totals.append(f"{column}合计 {sum(values, Decimal(0)):,.2f}")
                    elif any(isinstance(v, Decimal) for v in values):
                        valid = [v for v in values if isinstance(v, Decimal)]
                        totals.append(f"{column}合计 {sum(valid, Decimal(0)):,.2f}")
                    elif any(v is None for v in values):
                        totals.append(f"{column}合计不完整")
            self.total.setText(" · ".join(totals[:3]) or "金额原值见鼠标提示及导出；缺失显示 —。")

    def apply_local_filter(self):
        if not self.report:
            return
        search = self.local_search.text().casefold().strip()
        state = self.stock_status_filter.currentData() if self.report.name in ('股票汇总', '人员资产') and '当前状态' in self.report.columns else None
        state_column = self.report.columns.index('当前状态') if state else None
        ticker_column = self.report.columns.index('Ticker') if self.report and 'Ticker' in self.report.columns else None

        def row_matches(row):
            if state is not None and row.values[state_column] != state:
                return False
            if not search:
                return True
            if ticker_column is not None:
                val = row.values[ticker_column]
                return search in str(val or '').casefold()
            return any(search in exact_text(v).casefold() for v in row.values)

        self.sorted_rows = tuple(row for row in self.report.rows if row_matches(row))
        if self.report.name == '今日盈亏':
            self.overview_rows = self.sorted_rows
            channel = self.channel_overview.selected_channel
            if channel not in {r.values[0] for r in self.overview_rows}:
                self.channel_overview.selected_channel = None
            elif channel is not None:
                self.sorted_rows = tuple(r for r in self.overview_rows if r.values[0] == channel)

        # 动态计算「占比」：选单一渠道展示占单一渠道比例，选全部渠道展示占总持仓比例
        if self.sorted_rows and self.report and '占比' in self.report.columns and '当前市值($)' in self.report.columns:
            from decimal import Decimal
            ratio_idx = self.report.columns.index('占比')
            mv_idx = self.report.columns.index('当前市值($)')
            channel_col = self.report.columns.index('渠道') if '渠道' in self.report.columns else 0
            distinct_channels = {r.values[channel_col] for r in self.sorted_rows}
            channel = getattr(self.channel_overview, 'selected_channel', None) if hasattr(self, 'channel_overview') and self.channel_overview.isVisible() else None
            if self.report.name == '今日盈亏':
                base_rows = self.overview_rows if channel is None and getattr(self, 'overview_rows', ()) else self.sorted_rows
            elif len(distinct_channels) == 1:
                base_rows = self.sorted_rows
            else:
                base_rows = self.report.rows

            denom = sum((r.values[mv_idx] for r in base_rows if len(r.values) > mv_idx and isinstance(r.values[mv_idx], Decimal)), Decimal(0))

            if denom and denom > Decimal(0):
                updated = []
                for r in self.sorted_rows:
                    cur_mv = r.values[mv_idx] if len(r.values) > mv_idx else None
                    prop = (cur_mv / denom) if (isinstance(cur_mv, Decimal) and cur_mv > Decimal(0)) else None
                    new_vals = list(r.values)
                    new_vals[ratio_idx] = prop
                    updated.append(ReportRow(tuple(new_vals), r.metadata))
                self.sorted_rows = tuple(updated)
        self.apply_saved_sort()
        self.page = 0
        self.show_page()

    def turn(self, direction):
        self.page = min(max(0, self.page + direction), max(0, (len(self.sorted_rows) - 1) // 200))
        self.show_page()

    def sort_today(self):
        choice = self.today_sort.currentData()
        if self.report and self.report.name == '今日盈亏' and choice:
            self.sort_column(*choice)

    def open_sort_menu(self, column, header=None):
        if not self.report or not 0 <= column < len(self.report.columns):
            return
        col_name = self.report.columns[column]
        if any(k in col_name for k in ("日期", "月份", "时间", "effective_from", "effective_to", "business_date", "imported_at_utc", "报价UTC", "rate_date")):
            self.sort_column(column)
            return
        header = header or self.table.horizontalHeader()
        self.sort_menu.clear()
        self.sort_menu.addSection(col_name)
        saved = self.sort_orders.get(self.report.name)
        for label, descending in (('正序（从小到大）', False), ('倒序（从大到小）', True)):
            action = self.sort_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(saved == (col_name, descending))
            action.triggered.connect(lambda checked=False, column=column, descending=descending: self.sort_column(column, descending))
        from PySide6.QtCore import QPoint
        self.sort_menu.popup(header.mapToGlobal(QPoint(header.sectionViewportPosition(column), header.height())))

    def sort_column(self, column, descending=None):
        if not self.report or not 0 <= column < len(self.report.columns):
            return
        col_name = self.report.columns[column]
        saved = self.sort_orders.get(self.report.name)
        if descending is None:
            if saved and saved[0] == col_name:
                self.descending = not saved[1]
            else:
                self.descending = True if any(k in col_name for k in ("日期", "月份", "时间", "effective_from", "effective_to", "business_date", "imported_at_utc", "报价UTC", "rate_date")) else False
        else:
            self.descending = descending
        self.sort_orders[self.report.name] = (col_name, self.descending)
        self.page = 0
        self.apply_local_filter()

    def apply_saved_sort(self):
        saved = self.sort_orders.get(self.report.name)
        active = saved and saved[0] in self.report.columns
        for header in (self.table.horizontalHeader(), self.table.frozen.horizontalHeader()):
            header.setSortIndicatorShown(bool(active))
            if active:
                header.setSortIndicator(self.report.columns.index(saved[0]), Qt.SortOrder.DescendingOrder if saved[1] else Qt.SortOrder.AscendingOrder)
        if not active:
            return
        column, descending = self.report.columns.index(saved[0]), saved[1]
        known = [r for r in self.sorted_rows if r.values[column] is not None]
        missing = [r for r in self.sorted_rows if r.values[column] is None]

        def key(row):
            value = row.values[column]
            return (0, value) if isinstance(value, (Decimal, int)) else (1, exact_text(value).casefold())

        self.sorted_rows = tuple(sorted(known, key=key, reverse=descending) + missing)

    def column_menu(self, point):
        if not self.report:
            return
        menu = QMenu(self)
        for index, name in enumerate(self.report.columns):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(index))
            action.toggled.connect(lambda checked, col=index: (self.table.setColumnHidden(col, not checked), self.table.schedule_fit()))
        menu.exec(self.table.horizontalHeader().mapToGlobal(point))

    def show_details(self, current, previous):
        if not current.isValid() or current.row() >= len(self.table.finance_model.rows):
            return
        row = self.table.finance_model.rows[current.row()]
        self.details.setPlainText(json.dumps({"原值": dict(zip(self.report.columns, row.values)), "元信息": row.metadata}, ensure_ascii=False, indent=2, default=exact_text))
        ids = row.metadata.get("source_record_ids", ())
        if ids:
            self.provenance_requested.emit(ids[0], self.details)

    def export(self):
        if not self.report:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出计算结果（不覆盖已有文件）", self.report.name + ".xlsx", "Excel (*.xlsx);;CSV (*.csv);;JSON (*.json)")
        if path:
            rows = (tuple(row for row in self.table.finance_model.rows if not row.metadata.get('display_summary'))
                    if self.export_mode.currentIndex() == 1 else self.sorted_rows)
            self.export_requested.emit(replace(self.report, rows=tuple(rows)), Path(path))
