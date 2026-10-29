from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QLayout, QToolButton, QVBoxLayout, QWidget

from portfolio.ui.theme import numeric_font


class MetricCard(QFrame):
    def __init__(self, name, hint, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumHeight(92)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        self.title = QLabel(name)
        self.title.setObjectName("metricName")
        self.value = QLabel("—")
        self.value.setObjectName("metricValue")
        self.value.setFont(numeric_font(28, bold=True))
        self.hint = QLabel(hint)
        self.hint.setObjectName("metricFootnote")
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def set_value(self, value, *, hint=None):
        from decimal import Decimal
        if value is None:
            self.value.setText("—")
        elif isinstance(value, (int, float, Decimal)):
            self.value.setText(f"{value:,}")
        else:
            self.value.setText(str(value))
        if hint is not None:
            self.hint.setText(hint)


class StatusLabel(QLabel):
    """One compact feedback surface; technical diagnostics live on source page."""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("banner")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def setText(self, text):
        super().setText(text)
        self.show()

    def show_status(self, text, state="normal"):
        self.show()
        self.setProperty("state", state)
        self.setText(text)
        self.style().unpolish(self)
        self.style().polish(self)


class FlowLayout(QLayout):
    """Wrap active filters without forcing the main window wider."""
    def __init__(self, parent):
        super().__init__(parent)
        self.items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(6)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.arrange(QRect(0, 0, width, 0), test=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.arrange(rect)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def arrange(self, rect, test=False):
        x, y, height = rect.x(), rect.y(), 0
        for item in self.items:
            size = item.sizeHint()
            if x + size.width() > rect.right() and height:
                x, y = rect.x(), y + height + self.spacing()
                height = 0
            if not test:
                item.setGeometry(QRect(x, y, size.width(), size.height()))
            x += size.width() + self.spacing()
            height = max(height, size.height())
        return y + height - rect.y()


class FilterChips(QWidget):
    cleared = Signal(str)

    def __init__(self):
        super().__init__()
        self.row = FlowLayout(self)

    def set_filters(self, filters):
        while self.row.count():
            item = self.row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for name, text in filters:
            chip = QToolButton()
            chip.setObjectName("filterChip")
            chip.setText(text + "  ×")
            chip.setToolTip("清除此条件：" + text)
            chip.clicked.connect(lambda checked=False, key=name: self.cleared.emit(key))
            self.row.addWidget(chip)
        self.setVisible(bool(filters))
