from decimal import Decimal

from PySide6.QtCore import QModelIndex, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHeaderView, QLabel, QMenu, QPushButton, QStyle, QStyledItemDelegate, QStyleOptionHeader, QVBoxLayout, QWidget

from portfolio.domain.models import TRADE_COLUMNS, exact_text, from_e8
from portfolio.ui.finance_table import FinanceModel, FinanceTable
from portfolio.ui.theme import numeric_font, tokens

COLUMNS = (*TRADE_COLUMNS, "券商账户", "归属人员", "手续费", "成本变化", "当前价($)")


class TradeModel(FinanceModel):
    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def value(self, row, column):
        if column < 16:
            return row.values[column]
        if column == 16:
            return row.metadata.get("display_name")
        if column == 17:
            return row.metadata.get("person_name")
        if column == 18:
            return from_e8(row.metadata.get("fees_e8"))
        if column == 19:
            return row.metadata.get("cost_diff")
        if column == 20:
            return row.metadata.get("current_price")
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal:
            if section == 9:
                return "该笔操作完成当时，该人员在该渠道、该证券的持仓数量；券商账本按真实账户回放。"
            if section == 11:
                return "净投入成本价＝（累计买入金额－累计卖出金额）÷操作后持仓数量。清仓或数据不完整时显示—，负成本保留。"
            if section == 19:
                return "本次交易导致持仓成本的变化量（本次操作后成本－上一次操作后成本）。首笔建仓或清仓显示—。"
            if section == 20:
                return "标的当前最新市场价（实时行情或收盘价）；放在成交价前方便对比买卖决策优劣。"
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                if section == 11:
                    return "操作后成本($)"
                if section == 19:
                    return "成本变化($)"
                if section == 20:
                    return "当前价($)"
                return COLUMNS[section]
            return str(section + 1)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row, column = self.rows[index.row()], index.column()
        value = self.value(row, column)
        if role == Qt.ItemDataRole.DisplayRole:
            if isinstance(value, Decimal):
                if column == 18 and value != value.quantize(Decimal('0.01')):
                    return f"{value:,.8f}".rstrip('0')
                if column == 19:
                    return f"{value:+,.2f}" if value != 0 else "0.00"
                if column == 20:
                    return f"{value:,.2f}"
                return f"{value:,.2f}"
            if column == 18 and value is None:
                return '待核对' if row.metadata.get('fee_status') in {'UNMATCHED_ORDER','ORDER_QUANTITY_MISMATCH','ALLOCATION_MISMATCH'} else '待获取'
            if column == 0:
                return str(value).replace("T", " ")[:16]
            return exact_text(value) if value not in (None, "") else "—"
        if role == Qt.ItemDataRole.ToolTipRole:
            if column == 9:
                return exact_text(value) + "\n" + row.metadata.get('post_quantity_note', '')
            if column in (10, 11):
                return exact_text(value) + "\n" + row.metadata.get('post_cost_note', '')
            if column == 18 and value is None:
                return "人员份额手续费未知；不重复使用券商整笔费用。"
            if column == 8:
                return "净投入与微操配对详情见收益报表 → 交易回放。"
            if column == 19 and isinstance(value, Decimal):
                direction = "增加" if value > 0 else "降低摊薄" if value < 0 else "未变化"
                return f"持仓成本{direction} {abs(value):,.2f} 美元"
            if column == 20 and isinstance(value, Decimal):
                trade_price = row.values[6]
                if isinstance(trade_price, Decimal) and trade_price > 0:
                    diff = value - trade_price
                    diff_pct = diff / trade_price
                    is_buy = row.metadata.get("side") in ("BUY", "TRANSFER_IN")
                    if is_buy:
                        eval_text = "买入后上涨（做对）" if diff > 0 else "买入后下跌（做错）" if diff < 0 else "持平"
                    else:
                        eval_text = "卖出后下跌（卖得好）" if diff < 0 else "卖出后继续上涨（卖早了）" if diff > 0 else "持平"
                    return f"当前价: ${value:,.2f}\n成交价: ${trade_price:,.2f}\n价差: {diff:+,.2f} ({diff_pct:+,.2%})\n交易评估: {eval_text}"
                return f"当前价: ${value:,.2f}"
            return exact_text(value)
        if role == Qt.ItemDataRole.FontRole and (isinstance(value, Decimal) or column in (3, 20)):
            return numeric_font(bold=column in (3, 20))
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if isinstance(value, Decimal) else Qt.AlignmentFlag.AlignLeft))
        if role == Qt.ItemDataRole.ForegroundRole and value in (None, ""):
            return QColor(tokens(self.parent()).subtle)
        if role == Qt.ItemDataRole.ForegroundRole and column == 9 and value < 0:
            return QColor(tokens(self.parent()).loss)
        if role == Qt.ItemDataRole.ForegroundRole and column == 19 and isinstance(value, Decimal):
            if value > 0:
                return QColor(tokens(self.parent()).loss)
            if value < 0:
                return QColor(tokens(self.parent()).profit)
            return QColor(tokens(self.parent()).muted)
        if role == Qt.ItemDataRole.ForegroundRole and column == 20 and isinstance(value, Decimal):
            trade_price = row.values[6]
            if isinstance(trade_price, Decimal) and trade_price > 0:
                is_buy = row.metadata.get("side") in ("BUY", "TRANSFER_IN")
                if is_buy:
                    return QColor(tokens(self.parent()).profit if value > trade_price else tokens(self.parent()).loss if value < trade_price else tokens(self.parent()).muted)
                else:
                    return QColor(tokens(self.parent()).profit if value < trade_price else tokens(self.parent()).loss if value > trade_price else tokens(self.parent()).muted)
            return QColor(tokens(self.parent()).muted)
        return None


class TradeDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.column() != 4:
            return super().paint(painter, option, index)
        t = tokens(self.parent())
        row = index.model().rows[index.row()]
        label = index.data()
        buy = row.metadata["side"] in ("BUY", "TRANSFER_IN")
        color, background = (t.profit, t.buy_bg) if buy else (t.loss, t.sell_bg)
        painter.save()
        painter.fillRect(option.rect, QColor(t.selected if option.state & QStyle.StateFlag.State_Selected else t.alternate if index.row() % 2 else t.panel))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = min(option.fontMetrics.horizontalAdvance(label) + 18, option.rect.width() - 14)
        rect = QRectF(option.rect.x() + 8, option.rect.y() + 9, width, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QColor(color))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()


class TradeHeader(QHeaderView):
    def __init__(self, parent):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFixedHeight(42)
        self.setSectionsClickable(True)

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
        active = self.isSortIndicatorShown() and self.sortIndicatorSection() == logical
        t = tokens(self)
        painter.setPen(QColor(t.brand if active else t.muted))
        font = self.font()
        if active:
            font.setBold(True)
            arrow = " ↑" if self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder else " ↓"
            text = f"{text}{arrow}"
        painter.setFont(font)
        painter.drawText(rect.adjusted(6, 2, -6, -2), Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class TradeTable(FinanceTable):
    def __init__(self):
        super().__init__()
        self.setHorizontalHeader(TradeHeader(self))
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().sectionResized.connect(lambda *_: self._frozen_geometry())
        self.frozen.setHorizontalHeader(TradeHeader(self.frozen))
        self.frozen.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.finance_model = TradeModel(self)
        self.setModel(self.finance_model)
        self.frozen.setModel(self.finance_model)
        self.frozen.setSelectionModel(self.selectionModel())
        self.setItemDelegate(TradeDelegate(self))
        header = self.horizontalHeader()
        header.setSectionsMovable(True)
        header.setFirstSectionMovable(False)
        for h in (header, self.frozen.horizontalHeader()):
            h.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            h.customContextMenuRequested.connect(lambda pos, hdr=h: self.column_menu().exec(hdr.mapToGlobal(pos)))
        visible = (0, 1, 17, 3, 2, 4, 20, 6, 5, 9, 11, 19, 7, 18)
        for position, column in enumerate(visible):
            header.moveSection(header.visualIndex(column), position)
        widths = {0: 148, 1: 100, 17: 76, 3: 68, 2: 60, 4: 62, 20: 82, 6: 86, 5: 80, 9: 96, 11: 112, 19: 96, 7: 104, 18: 66}
        for column in range(len(COLUMNS)):
            self.setColumnHidden(column, column not in visible)
            self.setColumnWidth(column, widths.get(column, 140))
            self.frozen.setColumnHidden(column, column != 0)
        self.empty = QWidget(self.viewport())
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title = QLabel("连接本地数据库，开始查看交易")
        self.empty_title.setObjectName("sectionTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        self.empty_action = QPushButton("选择数据源")
        empty_layout.addWidget(self.empty_action, alignment=Qt.AlignmentFlag.AlignCenter)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "empty"):
            self.empty.setGeometry(self.viewport().rect())

    def show_empty(self, text, action=None):
        self.empty_title.setText(text)
        self.empty_action.setVisible(action is not None)
        self.empty_action.setText(action or "")
        self.empty.setGeometry(self.viewport().rect())
        self.empty.show()

    def column_menu(self):
        menu = QMenu(self)
        for column, name in enumerate(COLUMNS):
            if column == 16:
                continue  # One account per channel; omit redundant account display.
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(not self.isColumnHidden(column))
            action.setEnabled(column != 0)
            action.toggled.connect(lambda checked, column=column: self.setColumnHidden(column, not checked))
        return menu

    def copy_selection(self):
        from PySide6.QtWidgets import QApplication
        indexes = self.selectionModel().selectedIndexes()
        rows = sorted({i.row() for i in indexes})
        columns = sorted({i.column() for i in indexes if not self.isColumnHidden(i.column())})
        QApplication.clipboard().setText("\n".join("\t".join(exact_text(self.finance_model.value(self.finance_model.rows[row], col)) for col in columns) for row in rows))
