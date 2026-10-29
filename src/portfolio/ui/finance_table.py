from decimal import Decimal

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QHeaderView, QTableView

from portfolio.domain.models import TRADE_COLUMNS, exact_text


class FinanceModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = ()

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(TRADE_COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        value = row.values[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            if isinstance(value, Decimal):
                return f"{value:,.2f}"
            return exact_text(value)
        if role == Qt.ItemDataRole.ToolTipRole:
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if isinstance(value, Decimal) else Qt.AlignmentFlag.AlignLeft))
        if role == Qt.ItemDataRole.ForegroundRole and value is None:
            return QColor("#9299A5")
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return TRADE_COLUMNS[section] if orientation == Qt.Orientation.Horizontal else str(section + 1)
        return None


class FinanceTable(QTableView):
    """虚拟化本页模型，冻结首列；不为每个单元格创建 QWidget。"""
    def __init__(self, parent=None, freeze=True):
        super().__init__(parent)
        self.finance_model = FinanceModel(self)
        self.setModel(self.finance_model)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setMouseTracking(True)
        self.horizontalHeader().setFixedHeight(42)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(40)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.setColumnWidth(0, 176)
        for column in range(1, len(TRADE_COLUMNS)):
            self.setColumnWidth(column, 130 if column not in (14, 15) else 240)
        if freeze:
            self.frozen = QTableView(self)
            self.frozen.viewport().installEventFilter(self)
            self.frozen.setModel(self.finance_model)
            self.frozen.setSelectionModel(self.selectionModel())
            self.frozen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.frozen.verticalHeader().hide()
            self.frozen.verticalHeader().setDefaultSectionSize(40)
            self.frozen.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            self.frozen.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.frozen.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.frozen.setAlternatingRowColors(True)
            self.frozen.setShowGrid(False)
            self.frozen.setWordWrap(False)
            self.frozen.horizontalHeader().setFixedHeight(42)
            self.frozen.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
            self.frozen.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
            for column in range(1, len(TRADE_COLUMNS)):
                self.frozen.setColumnHidden(column, True)
            self.verticalScrollBar().valueChanged.connect(self.frozen.verticalScrollBar().setValue)
            self.frozen.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
            self.horizontalHeader().sectionResized.connect(lambda *_: self._frozen_geometry())
        else:
            self.frozen = None
        self.copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self)
        self.copy_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self.copy_selection)

    def setModel(self, model):
        super().setModel(model)
        if getattr(self, "frozen", None) is not None:
            self.frozen.setModel(model)
            if hasattr(self, "selectionModel"):
                self.frozen.setSelectionModel(self.selectionModel())
            if model is not None:
                for column in range(1, model.columnCount()):
                    self.frozen.setColumnHidden(column, True)

    def _frozen_geometry(self):
        if not getattr(self, "frozen", None):
            return
        self.frozen.setColumnWidth(0, self.columnWidth(0))
        self.frozen.setGeometry(self.frameWidth(), self.frameWidth(), self.columnWidth(0),
                                self.viewport().height() + self.horizontalHeader().height())
        self.frozen.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._frozen_geometry()

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            return True
        return super().viewportEvent(event)

    def eventFilter(self, watched, event):
        if getattr(self, 'frozen', None) is not None and watched is self.frozen.viewport() and event.type() == QEvent.Type.ToolTip:
            return True
        return super().eventFilter(watched, event)

    def copy_selection(self):
        indexes = self.selectionModel().selectedIndexes()
        rows = sorted({index.row() for index in indexes})
        columns = sorted({index.column() for index in indexes})
        text = "\n".join("\t".join(exact_text(self.finance_model.rows[row].values[col]) for col in columns) for row in rows)
        QApplication.clipboard().setText(text)
