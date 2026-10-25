from PySide6.QtCore import QDate
from PySide6.QtWidgets import QPushButton, QSpinBox

from portfolio.ui.date_picker import DatePicker
from portfolio.ui.main_window import MainWindow


def test_monthly_scope_uses_whole_month_and_daily_keeps_day(qtbot):
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.start.setDate(QDate(2025, 2, 17))
    w.as_of.setDate(QDate(2025, 3, 12))
    w.start_enabled.setChecked(True)
    w.navigation.select(4, '每月持仓收益')
    assert w.start.month_mode and w.as_of.month_mode
    assert w.date_button.text() == '2025-02 – 2025-03'
    assert w.start.displayFormat() == 'yyyy-MM'
    scope = w.make_scope()
    assert scope.start_date.isoformat() == '2025-02-01'
    assert scope.as_of.isoformat() == '2025-03-31'
    w.navigation.select(4, '每日持仓收益')
    assert not w.as_of.month_mode
    assert w.make_scope().as_of.isoformat() == '2025-03-12'
    assert w.make_scope().start_date.isoformat() == '2025-02-17'


def test_month_popup_has_year_and_twelve_months(qtbot):
    picker = DatePicker(QDate(2025, 1, 12))
    qtbot.addWidget(picker)
    picker.setDateRange(QDate(2025, 1, 1), QDate(2026, 10, 18))
    picker.set_month_mode(True, month_end=True)
    picker.show()
    picker.open_calendar()
    popup = picker._month_popup
    year = popup.findChild(QSpinBox)
    buttons = popup.findChildren(QPushButton)
    assert len(buttons) == 12
    year.setValue(2026)
    assert not next(b for b in buttons if b.text() == '11月').isEnabled()
    next(b for b in buttons if b.text() == '10月').click()
    assert picker.date() == QDate(2026, 10, 18)
    picker.open_calendar()
    popup = picker._month_popup
    popup.findChild(QSpinBox).setValue(2025)
    next(b for b in popup.findChildren(QPushButton) if b.text() == '2月').click()
    assert picker.date() == QDate(2025, 2, 28)
