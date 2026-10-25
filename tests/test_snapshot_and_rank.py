from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from PySide6.QtCore import QDate

from portfolio.domain.analytics import Report, ReportRow
from portfolio.engine.today_holdings import lifetime_summary
from portfolio.services.charts_service import Chart, Series, rank_chart
from portfolio.ui.charts_page import ChartsPage
from portfolio.ui.main_window import MainWindow


def test_snapshot_date_cuts_off_trades_and_prices_and_keeps_other_pages_today(qtbot, demo_path):
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.show()
    w.live_panel.enabled.setChecked(True)
    w.holding_date.setDate(QDate(2026, 1, 3))
    w.open_database(demo_path)
    qtbot.waitUntil(lambda: w.analytics is not None and w.asset_page.report is not None, timeout=15000)
    assert w.analytics.scope.as_of == date(2026, 1, 3)
    assert not w.holding_date.isHidden() and w.date_button.isHidden()
    assert all(r.values[0][:10] <= '2026-01-03' for r in w.analytics.reports['交易回放'].rows)
    assert all(r.metadata.get('valuation_date', '') <= '2026-01-03' for r in w.analytics.reports['当前持仓'].rows)
    w.holding_date.setDate(QDate(2026, 1, 10))
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.as_of == date(2026, 1, 10), timeout=15000)
    assert w.live_panel.analytics.scope.as_of == date(2026, 1, 10)
    assert '所选日期：2026-01-10' in w.asset_page.report.note
    assert '2026-01-09' in w.asset_page.report.note
    assert w.asset_page.cumulative_totals == lifetime_summary(w.analytics, w.analytics.reports['当前持仓'])
    expected = next(m.value for m in w.analytics.metrics if m.name == '全时期总盈亏')
    if expected is not None:
        assert f'{expected:+,.2f}' in w.asset_page.today_cards[3].text()
    assert '不连接实时行情' in w.live_panel.status.text()
    w.navigation.select(3, '股票汇总')
    assert w.holding_date.isHidden()
    assert w.make_scope().as_of == QDate.currentDate().toPython()
    w.navigation.select(3, '今日盈亏')
    assert w.make_scope().as_of == date(2026, 1, 10)
    w.holding_date.setDate(QDate.currentDate())
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.as_of == QDate.currentDate().toPython(), timeout=15000)
    assert w.holding_date.date() == QDate.currentDate()
    w.close()


def test_lifetime_totals_include_fully_closed_stocks_without_filling_gaps():
    from types import SimpleNamespace
    def held(channel, value):
        return ReportRow((channel, *([None] * 9), value))
    def closed(channel, value):
        return ReportRow((channel, *([None] * 6), value))
    a = SimpleNamespace(reports={'清仓收益': Report('清仓收益', (), (closed('LONGBRIDGE', D(40)), closed('BBAE', D(-5))))})
    result = lifetime_summary(a, Report('当前持仓', (), (held('LONGBRIDGE', D(50)), held('SCHWAB', None))))
    assert result == {'长桥': (D(40), D(50), D(90)), 'BBAE': (D(-5), D(0), D(-5)), '嘉信': (D(0), None, None)}


def ranking():
    points = tuple((f'S{i:02}', D(i-20)) for i in range(50)) + (('MISSING', None),)
    return Chart(16, '排行', 'horizontal', (Series('浮动盈亏', points),), '基准')


def test_ranking_orders_entire_dataset_before_paging_and_merges_ticker_series():
    chart = ranking()
    assert rank_chart(chart).series[0].points[0] == ('S49', D(29))
    assert rank_chart(chart, 'loss').series[0].points[0] == ('S00', D(-20))
    assert rank_chart(chart, 'magnitude').series[0].points[0] == ('S49', D(29))
    assert rank_chart(chart, 'profit', 24).series[0].points[0] == ('S25', D(5))
    assert rank_chart(chart, 'profit', 48).series[0].points[-1] == ('MISSING', None)
    split = replace(chart, id=17, series=(Series('盈利', (('A', D(10)), ('B', D(0)))), Series('亏损', (('A', D(0)), ('B', D(-100))))))
    assert rank_chart(split, 'magnitude').series[0].points == (('B', D(-100)), ('A', D(10)))
    assert chart.series[0].points[0] == ('S00', D(-20))


def test_rank_ui_sort_resets_page_and_hides_on_unrelated_charts(qtbot):
    page = ChartsPage()
    qtbot.addWidget(page)
    page.charts = (ranking(), replace(ranking(), id=18, kind='line'))
    page.selector.addItems(('排行', '走势'))
    page.show_chart()
    assert not page.rank_order.isHidden()
    page.offset.setCurrentIndex(1)
    page.rank_order.setCurrentIndex(1)
    assert page.offset.currentIndex() == 0
    assert page.canvas.chart.series[0].points[0] == ('S00', D(-20))
    assert '亏损最多' in page.canvas.chart.note
    page.selector.setCurrentIndex(1)
    assert page.rank_order.isHidden()
