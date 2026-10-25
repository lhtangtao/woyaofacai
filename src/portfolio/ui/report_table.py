"""Fit report columns to the available space without hiding financial fields."""
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QHeaderView, QStyle, QStyleOptionHeader

from portfolio.ui.finance_table import FinanceTable
from portfolio.ui.theme import tokens


class WrappedHeader(QHeaderView):
    def __init__(self, parent):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFixedHeight(54)
        self.setMinimumSectionSize(40)

    def paintSection(self, painter, rect, logical):
        if not rect.isValid():
            return
        painter.save()
        option = QStyleOptionHeader()
        self.initStyleOptionForIndex(option, logical)
        option.rect = rect
        text = option.text
        option.text = ""
        option.sortIndicator = QStyleOptionHeader.SortIndicator.None_
        self.style().drawControl(QStyle.ControlElement.CE_Header, option, painter, self)
        # Split long Chinese headings at a meaningful boundary; values and exports stay exact.
        for prefix in ("累计买入", "累计卖出", "累计净投入", "持仓盈亏", "持仓成本", "回本涨幅", "最后交易", "首次交易", "当前持仓"):
            if text.startswith(prefix) and len(text) > len(prefix):
                text = prefix + "\n" + text[len(prefix):]
                break
        painter.setPen(tokens(self).muted)
        painter.setFont(self.font())
        painter.drawText(rect.adjusted(4, 3, -17, -3), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)
        active = self.isSortIndicatorShown() and self.sortIndicatorSection() == logical
        painter.setPen(tokens(self).brand if active else tokens(self).muted)
        x, y = rect.right() - 9, rect.center().y()
        direction = -1 if active and self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder else 1
        painter.drawLine(x - 3, y - direction, x, y + direction * 2)
        painter.drawLine(x, y + direction * 2, x + 3, y - direction)
        painter.restore()


class ReportTable(FinanceTable):
    def __init__(self):
        self.compact = False
        super().__init__()
        self.setHorizontalHeader(WrappedHeader(self))
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().sectionResized.connect(lambda *_: self._frozen_geometry())
        self.frozen.setHorizontalHeader(WrappedHeader(self.frozen))
        self.frozen.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.fit_timer = QTimer(self)
        self.fit_timer.setSingleShot(True)
        self.fit_timer.timeout.connect(self.fit_columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_fit()

    def viewportEvent(self, event):
        result = super().viewportEvent(event)
        if event.type() == QEvent.Type.Resize:
            self.schedule_fit()
        return result

    def schedule_fit(self):
        if hasattr(self, "fit_timer"):
            self.fit_timer.start(0)

    def fit_columns(self):
        model = self.model()
        if not model or not model.columnCount() or self.viewport().width() < 100:
            return
        compact = self.viewport().width() < 1250
        if compact != self.compact:
            self.compact = compact
            size = 12 if compact else 13
            padding = 2 if compact else 6
            style = f"QTableView {{font-size:{size}px;}} QTableView::item {{padding:0 {padding}px;}}"
            self.setStyleSheet(style)
            self.frozen.setStyleSheet(style)
        columns = [i for i in range(model.columnCount()) if not self.isColumnHidden(i)]
        if not columns:
            return
        minimum, preferred = [], []
        for col in columns:
            # Qt's delegate includes the actual styled font and cell padding in its hint.
            width = max(48, self.sizeHintForColumn(col) + 4)
            heading = str(model.headerData(col, Qt.Orientation.Horizontal) or "")
            if "数量" in heading or "股数" in heading:
                width = min(max(76, width), 104)
            # Long descriptive text retains a tooltip; monetary values are never scaled or rounded here.
            width = min(width, 200)
            minimum.append(width)
            preferred.append(max(width, min(112, self.fontMetrics().horizontalAdvance(heading) + 18)))
        available = self.viewport().width() - 2
        base = sum(minimum)
        if base >= available:
            widths = minimum  # Truly dense reports retain a scrollbar rather than unreadable amounts.
        else:
            slack = available - base
            wanted = [max(12, p - m) for p, m in zip(preferred, minimum)]
            total = sum(wanted)
            widths = [m + slack * extra // total for m, extra in zip(minimum, wanted)]
            widths[-1] += available - sum(widths)
        for col, width in zip(columns, widths):
            if self.columnWidth(col) != width:
                self.setColumnWidth(col, width)
        self._frozen_geometry()
