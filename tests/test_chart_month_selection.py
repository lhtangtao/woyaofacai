from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domain.models import QueryScope
from portfolio.services.charts_service import build_charts
from portfolio.ui.charts_page import ChartsPage


def snapshots():
    reports = {name: Report(name, (), ()) for name in ('当前持仓', '清仓收益', '股票汇总', '每月持仓收益', '每日持仓收益', '每月微操收益', '月度净投入明细')}
    rows = []
    for month, category, ticker, amount in (('2026-05', '大盘', 'AAA', '100'), ('2026-06', '杠杆', 'BBB', '250'), ('2026-07', '正股', 'CCC', '300')):
        values = [D(0)] * 24
        values[:4] = [month, 'LONG', category, ticker]
        values[8], values[11], values[20] = D(10), D(amount), D(1)
        rows.append(ReportRow(tuple(values)))
    reports['每月持仓收益'] = Report('每月持仓收益', (), tuple(rows))
    holding = ['LONG', '正股', 'CURRENT', D(10), D(99), D(990), *([D(0)] * 8)]
    reports['当前持仓'] = Report('当前持仓', (), (ReportRow(tuple(holding)),))
    a = SimpleNamespace(reports=reports, latest_profit_day='2026-07-10', scope=QueryScope(date(2026, 7, 10)), issues=())
    a.charts = build_charts(a)
    return a


def test_month_snapshot_uses_selected_month_and_previous_month():
    a = snapshots()
    june = build_charts(a, selected_month='2026-06')
    assert dict(june[0].series[0].points) == {'BBB': D(250)}
    assert dict(june[1].series[0].points) == {'LONG': D(250)}
    assert dict(june[2].series[0].points) == {'杠杆': D(250)}
    assert '2026-06 月末' in june[2].title
    assert dict(june[3].series[0].points) == {'AAA': D(100)}
    assert '2026-05 月末' in june[3].title
    missing = build_charts(a, selected_month='2026-04')
    assert missing[2].series[0].points == ()
    july = build_charts(a, selected_month='2026-07')
    assert '截至 2026-07-10' in july[2].title
    assert dict(a.charts[2].series[0].points) == {'正股': D(990)}


def test_combo_refreshes_current_pie_and_retains_month_on_reload(qtbot):
    a = snapshots()
    page = ChartsPage()
    qtbot.addWidget(page)
    page.show()
    page.set_analytics(a)
    page.selector.setCurrentIndex(2)
    page.month.setCurrentIndex(page.month.findData('2026-06'))
    assert dict(page.canvas.chart.series[0].points) == {'杠杆': D(250)}
    assert not page.month.isHidden()
    page.set_analytics(a)
    assert page.month.currentData() == '2026-06'
    assert dict(page.canvas.chart.series[0].points) == {'杠杆': D(250)}
    page.month.setCurrentIndex(0)
    assert dict(page.canvas.chart.series[0].points) == {'正股': D(990)}
    page.selector.setCurrentIndex(17)
    assert page.month.isHidden() and page.month_label.isHidden()
    page.selector.setCurrentIndex(20)
    assert not page.month.isHidden()
    page.clear()
    assert page.month.isHidden()


def test_default_date_range_is_2025_to_today_and_drill_clears_on_charts(qtbot):
    from PySide6.QtCore import QDate
    from portfolio.ui.main_window import MainWindow

    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    # Default start must be 2025-01-01, end must be today
    assert w.start.date() == QDate(2025, 1, 1)
    assert w.as_of.date() == QDate.currentDate()
    assert not w.start_enabled.isChecked()

    # Simulate a single-day drill
    w.start.setDate(QDate(2026, 10, 12))
    w.start_enabled.setChecked(True)
    w.as_of.setDate(QDate(2026, 10, 12))

    # Navigating to ChartsPage (index 5) should clear single-day drill and restore 2025-01-01 to today
    w.navigation.select(5)
    assert not w.start_enabled.isChecked()
    assert w.start.date() == QDate(2025, 1, 1)
    assert w.as_of.date() == QDate.currentDate()
