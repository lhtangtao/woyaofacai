"""Date fields that open the native Qt calendar from the whole field."""
from PySide6.QtCore import QDate, QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent, QPainter
from PySide6.QtWidgets import QDateEdit, QGridLayout, QMenu, QPushButton, QSpinBox, QWidget, QWidgetAction

from portfolio.ui.icons import icon


class DatePicker(QDateEdit):
    def __init__(self, day):
        super().__init__(day)
        self.setCalendarPopup(True)
        self.setMinimumWidth(160)
        self.calendarWidget().setMinimumSize(320, 290)
        # The app's report-table cell padding otherwise hides two-digit days.
        self.calendarWidget().setStyleSheet('QCalendarWidget QTableView::item { padding:0; border:0; } QCalendarWidget QHeaderView::section { padding:0; }')
        self.lineEdit().installEventFilter(self)
        self.setToolTip('点击日期打开日历选择。')
        self.month_mode = False
        self.month_end = False

    def set_month_mode(self, enabled, *, month_end=False):
        self.month_mode, self.month_end = enabled, month_end
        self.setDisplayFormat('yyyy-MM' if enabled else 'yyyy-MM-dd')
        self.setToolTip('选择年份和月份。' if enabled else '点击日期打开日历选择。')

    def open_months(self):
        if hasattr(self, '_month_popup'):
            self.month_year.setRange(self.minimumDate().year(), self.maximumDate().year())
            self.month_year.setValue(self.date().year())
            self._update_month_buttons()
            self._month_popup.popup(self.mapToGlobal(self.rect().bottomLeft()))
            return
        menu = QMenu(self)
        self._month_popup = menu
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(12, 12, 12, 12)
        year = QSpinBox()
        self.month_year = year
        year.setRange(self.minimumDate().year(), self.maximumDate().year())
        year.setValue(self.date().year())
        year.setSuffix(' 年')
        grid.addWidget(year, 0, 0, 1, 3)
        buttons = []

        def choose(month):
            day = QDate(year.value(), month, 1)
            if self.month_end:
                day = day.addMonths(1).addDays(-1)
            self.setDate(max(self.minimumDate(), min(day, self.maximumDate())))
            menu.close()

        def update_buttons():
            for month, button in enumerate(buttons, 1):
                first = QDate(year.value(), month, 1)
                button.setEnabled(first <= self.maximumDate() and first.addMonths(1).addDays(-1) >= self.minimumDate())
                button.setChecked((year.value(), month) == (self.date().year(), self.date().month()))

        for month in range(1, 13):
            button = QPushButton(f'{month}月')
            button.setMinimumSize(64, 34)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, month=month: choose(month))
            grid.addWidget(button, (month - 1) // 3 + 1, (month - 1) % 3)
            buttons.append(button)
        year.valueChanged.connect(update_buttons)
        self._update_month_buttons = update_buttons
        update_buttons()
        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        menu.popup(self.mapToGlobal(self.rect().bottomLeft()))

    def open_calendar(self):
        if self.month_mode:
            self.open_months()
            return
        self.setFocus()
        self.calendarWidget().setDateRange(self.minimumDate(), self.maximumDate())
        arrow = QPointF(self.width() - 12, self.height() / 2)
        super().mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, arrow,
                                           QPointF(self.mapToGlobal(arrow.toPoint())),
                                           Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                           Qt.KeyboardModifier.NoModifier))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_calendar()
            event.accept()
        else:
            super().mousePressEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.open_calendar()
            return True
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        icon('calendar', self).paint(painter, QRect(self.width() - 27, (self.height() - 18) // 2, 18, 18))
