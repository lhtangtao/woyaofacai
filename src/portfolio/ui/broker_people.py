"""Broker/person daily and lifetime attribution, with clickable horizontal bars."""
from dataclasses import replace
from decimal import Decimal

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from portfolio.ui.metric_card import MetricCard
from portfolio.ui.report_table import ReportTable
from portfolio.ui.theme import tokens


class PeopleBars(QWidget):
    selected = Signal(object)

    def __init__(self):
        super().__init__()
        self.rows, self.hits, self.person, self.column = (), {}, None, 1
        self.quantity_mode = False
        self.label_mode = 'both'
        self.share_total, self.symbol = None, ''
        self.setMinimumHeight(220)
        self.setMouseTracking(True)

    def set_rows(self, rows, column):
        # Shares retain every nonzero fraction; P/L hides displayed zero amounts.
        def visible(row):
            value = row.values[column]
            return value is None or (value != 0 if self.quantity_mode else Decimal(format(value, '.2f')) != 0)
        self.rows = tuple(r for r in rows if visible(r))
        self.column = column
        self.setFixedHeight(max(380, 80+len(self.rows)*36) if self.quantity_mode else max(220, 52+len(self.rows)*44))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        t = tokens(self)
        p.fillRect(self.rect(), QColor(t.panel))
        self.hits = {}
        if self.quantity_mode:
            from portfolio.ui.ownership_pie import draw_ownership
            draw_ownership(p, QRectF(self.rect()), self.rows, self.share_total, self.symbol, t)
            return
        values = [r.values[self.column] for r in self.rows if r.values[self.column] is not None]
        low, high = min([Decimal(0), *values]), max([Decimal(0), *values])
        span = high-low or Decimal(1)
        labels = {}
        for i, row in enumerate(self.rows):
            value = row.values[self.column]
            text = f'{value:+,.2f}' if value is not None else '—'
            rate = None
            if self.column == 1:
                rate = row.values[2]
            elif self.column == 5:
                net = row.values[8] if len(row.values) > 8 else None
                rate = (row.values[5] / net) if (net and net > 0 and row.values[5] is not None) else None
            if self.label_mode == 'rate':
                text = f'{rate:+.2%}' if rate is not None else '—'
            elif self.label_mode == 'both' and rate is not None:
                text += f'（{rate:+.2%}）'
            elif self.label_mode == 'both' and value is not None and self.column in (1, 5):
                text += '（—）'
            person_key = row.metadata.get('person_id', i) if hasattr(row, 'metadata') and row.metadata else i
            labels[person_key] = text
        label_width = max((p.fontMetrics().horizontalAdvance(text) for text in labels.values()), default=95) + 12
        left = 190 + (label_width + 10 if low < 0 else 0)
        width = max(1, self.width()-left-label_width-25)
        def x(value):
            return left+float((value-low)/span)*width
        zero = x(Decimal(0))
        p.setPen(QColor(t.text))
        p.drawLine(int(zero), 24, int(zero), self.height()-8)
        p.drawText(QRectF(zero-15, 3, 30, 20), Qt.AlignmentFlag.AlignCenter, '0')
        for i, row in enumerate(self.rows):
            value = row.values[self.column]
            person = row.metadata.get('person_id', i) if hasattr(row, 'metadata') and row.metadata else i
            y = 30+i*44
            hit = QRectF(0, y, self.width(), 40)
            self.hits[person] = hit
            p.setPen(QColor(t.text))
            name = p.fontMetrics().elidedText(row.values[0], Qt.TextElideMode.ElideRight, 170)
            p.drawText(QRectF(8, y, 170, 40), Qt.AlignmentFlag.AlignVCenter, name)
            if value is not None:
                end = x(value)
                p.fillRect(QRectF(min(zero, end), y+9, max(2, abs(end-zero)), 22), QColor(t.subtle if self.person == person else t.profit if value >= 0 else t.loss))
                p.drawText(QRectF(end+5 if value >= 0 else end-label_width-5, y, label_width, 40),
                           Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if value >= 0 else Qt.AlignmentFlag.AlignRight),
                           labels.get(person, ''))
            else:
                p.drawText(QRectF(zero+8, y, 180, 40), Qt.AlignmentFlag.AlignVCenter, '— 数据不完整')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for person, rect in self.hits.items():
                if rect.contains(event.position()):
                    self.person = None if person == self.person else person
                    self.selected.emit(self.person)
                    self.update()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.setCursor(Qt.CursorShape.PointingHandCursor if any(r.contains(event.position()) for r in self.hits.values()) else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)


class BrokerPeoplePage(QWidget):
    def __init__(self):
        super().__init__()
        # Import lazily: ReportPage embeds this widget.
        from portfolio.ui.report_page import ReportModel
        self.bundle, self.result = None, None
        self.table_sorts = {}
        self.mode = QComboBox()
        self.mode.addItems(('当日盈亏', '累计盈亏', '股票归属'))
        self.stock = QComboBox()
        self.stock.setMinimumWidth(180)
        self.stock.setToolTip('选择股票，查看各人员持有股数及占比；渠道使用上方筛选。')
        self.stock.hide()
        self.order = QComboBox()
        self.order.addItems(('盈利最多', '亏损最多', '盈亏幅度最大'))
        self.label_mode = QComboBox()
        self.label_mode.addItem('金额和收益率', 'both')
        self.label_mode.addItem('仅金额', 'amount')
        self.label_mode.addItem('仅收益率', 'rate')
        self.label_mode.setToolTip('切换柱体旁的标签；柱长与排序仍按盈亏金额。')
        outer = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.title_label = QLabel('全部人员 · 权益账本')
        controls.addWidget(self.title_label)
        controls.addWidget(self.mode)
        controls.addWidget(self.stock)
        controls.addWidget(self.order)
        controls.addWidget(self.label_mode)
        controls.addStretch()
        self.export = QPushButton('导出人员汇总…')
        controls.addWidget(self.export)
        outer.addLayout(controls)
        self.ownership_warning = QLabel()
        self.ownership_warning.setWordWrap(True)
        self.ownership_warning.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ownership_warning.hide()
        outer.addWidget(self.ownership_warning)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        content = QWidget()
        self.content = content
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        card_panel = QWidget()
        card_panel.setFixedHeight(80)
        cards = QHBoxLayout(card_panel)
        cards.setContentsMargins(0, 0, 0, 0)
        self.cards = [MetricCard('—') for _ in range(4)]
        for card in self.cards:
            card.setMinimumHeight(72)
            cards.addWidget(card)
        layout.addWidget(card_panel)
        self.note = QLabel('等待人员盈亏计算…')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.chart = PeopleBars()
        layout.addWidget(self.chart)
        self.summary = ReportTable()
        self.detail = ReportTable()
        for table in (self.summary, self.detail):
            table.finance_model = ReportModel(table)
            table.setModel(table.finance_model)
            table.frozen.setModel(table.finance_model)
            table.frozen.setSelectionModel(table.selectionModel())
            table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            for header in (table.horizontalHeader(), table.frozen.horizontalHeader()):
                header.setSectionsClickable(True)
                header.sectionClicked.connect(lambda column, table=table: self.sort_table(table, column))
        layout.addWidget(self.summary)
        self.detail_label = QLabel('全部人员股票明细')
        layout.addWidget(self.detail_label)
        layout.addWidget(self.detail)
        self.chart.selected.connect(lambda *_: self.show_result())
        self.mode.currentIndexChanged.connect(self.show_result)
        self.stock.currentIndexChanged.connect(self.show_result)
        self.order.currentIndexChanged.connect(self.show_result)
        self.label_mode.currentIndexChanged.connect(self.show_result)
        self.summary.clicked.connect(self.select_person)
        self.summary.frozen.clicked.connect(self.select_person)
        self.export.clicked.connect(self.export_summary)
        self.clear()

    def clear(self):
        self.bundle, self.result = None, None
        self.ownership_warning.hide()
        self.stock.blockSignals(True)
        self.stock.clear()
        self.stock.blockSignals(False)
        self.chart.set_rows((), 1)
        self.chart.person = None
        for table in (self.summary, self.detail):
            table.finance_model.replace(())
            table.setFixedHeight(60)
        for card in self.cards:
            card.setText('计算中…')
        self.note.setText('按所选渠道与日期计算各人员盈亏…')
        if hasattr(self, 'title_label'):
            self.title_label.setText('股票归属 · 人员占比' if self.mode.currentIndex() == 2 else '全部人员 · 权益账本')

    def render(self, instruments, quotes, session, now):
        if self.bundle is None:
            return
        from portfolio.engine.people_pnl import people_reports
        self.result = people_reports(self.bundle, instruments, quotes, session, now)
        self.show_result()

    def select_person(self, index):
        row = self.summary.finance_model.rows[index.row()]
        person = row.metadata['person_id']
        self.chart.person = None if person == self.chart.person else person
        self.show_result()

    def sort_table(self, table, column):
        columns = table.finance_model.columns
        if not 0 <= column < len(columns):
            return
        key = (table, self.mode.currentIndex())
        previous = self.table_sorts.get(key)
        self.table_sorts[key] = (columns[column], not previous[1] if previous and previous[0] == columns[column] else True)
        self.show_result()

    def fill(self, table, columns, rows):
        rows = tuple(rows)
        saved = self.table_sorts.get((table, self.mode.currentIndex()))
        active = bool(saved and saved[0] in columns)
        if active:
            column, descending = columns.index(saved[0]), saved[1]
            known = [row for row in rows if row.values[column] is not None]
            missing = [row for row in rows if row.values[column] is None]
            rows = tuple(sorted(known, key=lambda row: row.values[column], reverse=descending) + missing)
        for header in (table.horizontalHeader(), table.frozen.horizontalHeader()):
            header.setSortIndicatorShown(active)
            if active:
                header.setSortIndicator(column, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)
        model = table.finance_model
        model.beginResetModel()
        model.columns, model.rows = columns, tuple(rows)
        model.endResetModel()
        for i in range(len(columns)):
            table.frozen.setColumnHidden(i, i != 0)
        table.setFixedHeight(table.horizontalHeader().height()+sum(table.rowHeight(i) for i in range(len(rows)))+6)
        table.schedule_fit()

    def show_result(self, *_):
        if self.result is None:
            return
        from portfolio.engine.stock_ownership import ownership_alert_rows
        alerts = ownership_alert_rows(self.bundle)
        def quantity(value):
            return '未知' if value is None else format(value, '.8f').rstrip('0').rstrip('.')
        # [Antigravity] 负仓异常检查：若存在负数持仓，明确列出人员、渠道、标的、数量及触发流水，解释为何展示“数据不完整”
        negatives = []
        if self.bundle and '数据异常' in self.bundle.aggregate.reports:
            for r in self.bundle.aggregate.reports['数据异常'].rows:
                if r.values[0] == 'DQ001' and '当前持仓为负数' in str(r.values[6]):
                    negatives.append(f"{r.values[3]} · {r.values[4]} · {r.values[5]}：{r.values[6]}")
        warning_sections = []
        if negatives:
            warning_sections.append(
                f"🚨 负仓异常（{len(negatives)}项）：因未启用做空模型，出现负仓时无法估值，导致人员汇总与未分配差异展示为「数据不完整」：\n"
                + "\n".join(f"  • {item}" for item in negatives)
                + "\n建议：请核对源账本流水更正划转/交易券商账户。"
            )
        if alerts:
            warning_sections.append(
                f'⚠ 归属数量告警（{len(alerts)}项）。差额＝券商股数－人员合计；负数表示人员超分配。\n' +
                '\n'.join(f'{channel} · {symbol}：券商 {quantity(broker)} 股，人员 {quantity(assigned)} 股，差额 {quantity(difference)} 股。'
                          for channel, symbol, broker, assigned, difference in alerts) +
                '\n请核对交易流水中的人员分配、内部划转日期及拆并股；不足1股按既定尾差规则不告警。'
            )
        self.ownership_warning.setText("\n\n".join(warning_sections))
        t = tokens(self)
        self.ownership_warning.setStyleSheet(f'QLabel {{ color:{t.loss}; background:{t.error_bg}; border:1px solid {t.loss}; border-radius:8px; padding:10px; }}')
        self.ownership_warning.setVisible(bool(warning_sections))
        ownership = self.mode.currentIndex() == 2
        self.stock.setVisible(ownership)
        self.order.setVisible(not ownership)
        self.label_mode.setVisible(not ownership)
        self.chart.label_mode = self.label_mode.currentData()
        self.chart.quantity_mode = ownership
        if hasattr(self, 'title_label'):
            self.title_label.setText('股票归属 · 人员占比' if ownership else '全部人员 · 权益账本')
        if ownership:
            self.show_ownership()
            return
        self.summary.setVisible(True)
        self.detail.setVisible(True)
        self.detail_label.setVisible(True)
        self.export.setText('导出人员汇总…')
        summary, details, broker = self.result
        column = 1 if self.mode.currentIndex() == 0 else 5
        rows = sorted(summary.rows, key=lambda r: r.values[0])
        known = [r for r in rows if r.values[column] is not None]
        known.sort(key=lambda r: r.values[column].copy_abs() if self.order.currentIndex() == 2 else r.values[column], reverse=self.order.currentIndex() != 1)
        rows = known+[r for r in rows if r.values[column] is None]
        if self.chart.person not in {r.metadata['person_id'] for r in rows}:
            self.chart.person = None
        self.chart.set_rows(rows, column)
        self.fill(self.summary, summary.columns, rows)
        selected = self.chart.person
        detail_rows = [r for r in details.rows if (selected is None or r.metadata['person_id'] == selected) and (column == 5 or r.metadata.get('daily_active'))]
        detail_column = 7 if column == 1 else 9
        known_detail = [r for r in detail_rows if r.values[detail_column] is not None]
        known_detail.sort(key=lambda r: r.values[detail_column].copy_abs() if self.order.currentIndex() == 2 else r.values[detail_column], reverse=self.order.currentIndex() != 1)
        detail_rows = known_detail+[r for r in detail_rows if r.values[detail_column] is None]
        self.fill(self.detail, details.columns, detail_rows)
        name = next((r.values[0] for r in rows if r.metadata['person_id'] == selected), '全部人员')
        self.detail_label.setText(name+' · 股票盈亏明细（点击人员柱或汇总行切换，再次点击恢复全部）')
        self.note.setText(summary.note)
        self.content.setMinimumHeight(self.chart.height()+self.summary.height()+self.detail.height()+260)
        self.detail_report = details
        self.summary_report = replace(summary, rows=tuple(self.summary.finance_model.rows))
        if column == 1:
            positive = sum(r.values[1] is not None and r.values[1] > 0 for r in rows if r.metadata['person_id'] != 'residual')
            negative = sum(r.values[1] is not None and r.values[1] < 0 for r in rows if r.metadata['person_id'] != 'residual')
            values = [('渠道当日盈亏', broker.values[1], False), ('当日收益率', broker.values[2], True), ('盈利人数 / 亏损人数', f'{positive} / {negative}', False), ('持仓市值（不含现金）', broker.values[3], False)]
        else:
            values = [('渠道累计盈亏', broker.values[5], False), ('已清仓收益', broker.values[6], False), ('持仓浮动盈亏', broker.values[7], False), ('持仓市值（不含现金）', broker.values[3], False)]
        t = tokens(self)
        for card, (title, value, percent) in zip(self.cards, values):
            text = '— 数据不完整' if value is None else f'{value:+.2%}' if percent else f'{value:,.2f}' if isinstance(value, Decimal) else value
            card.setText(title+'\n'+text)
            color = t.profit if isinstance(value, Decimal) and value > 0 else t.loss if isinstance(value, Decimal) and value < 0 else t.heading
            card.setStyleSheet(f'QPushButton {{text-align:left;padding:12px;border:1px solid {t.border};border-radius:12px;background:{t.panel};color:{color};}}')

    def show_ownership(self):
        from portfolio.engine.stock_ownership import ownership_choices, ownership_report
        choices = ownership_choices(self.bundle)
        current = self.stock.currentData()
        self.stock.blockSignals(True)
        self.stock.clear()
        for identifier, symbol in choices:
            self.stock.addItem(symbol, identifier)
        index = self.stock.findData(current)
        self.stock.setCurrentIndex(index if index >= 0 else 0)
        self.stock.blockSignals(False)
        report, broker, assigned, difference = ownership_report(self.bundle, self.stock.currentData())
        rows = sorted(report.rows, key=lambda r: (r.values[1] is None, -(r.values[1] or Decimal(0)), r.values[0]))
        self.chart.person = None
        self.chart.share_total, self.chart.symbol = broker, self.stock.currentText()
        self.chart.set_rows(rows, 1)
        self.fill(self.summary, report.columns, rows)
        self.summary_report = replace(report, rows=tuple(rows))
        self.detail.hide()
        self.detail_label.hide()
        self.export.setText('导出股票归属…')
        self.note.setText(self.stock.currentText()+' · '+report.note if choices else '当前渠道没有他人持有的股票（全为个人持仓的标的已不在此展示）。')
        count = sum(r.metadata['person_id'] != 'residual' for r in rows)
        values = [('券商总股数', broker), ('人员合计股数', assigned), ('未分配／归属差异', difference), ('持有人数', count)]
        for card, (title, value) in zip(self.cards, values):
            text = '— 数据不完整' if value is None else format(value, ',.8f').rstrip('0').rstrip('.')+' 股' if isinstance(value, Decimal) else str(value)
            card.setText(title+'\n'+text)
            card.setStyleSheet('')
        self.content.setMinimumHeight(self.chart.height()+self.summary.height()+220)

    def export_summary(self):
        if self.result is None:
            return
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog, QMessageBox

        from portfolio.services.export_service import export_report
        path, _ = QFileDialog.getSaveFileName(self, '导出人员盈亏', '人员盈亏.xlsx', 'Excel (*.xlsx);;CSV (*.csv)')
        if path:
            try:
                scope = self.bundle.aggregate.scope
                export_report(self.summary_report, Path(path), parameters={'截止日期': scope.as_of.isoformat(), '渠道账户ID': scope.account_ids, '口径': 'EXCEL_COMPAT / PERSON', '视图': self.mode.currentText(), '股票': self.stock.currentText() if self.mode.currentIndex() == 2 else '', '说明': self.summary_report.note})
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '导出未完成', str(exc))
