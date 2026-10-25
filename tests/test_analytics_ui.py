from datetime import date

from portfolio.domain.models import QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.ui.charts_page import ChartCanvas
from portfolio.ui.main_window import MainWindow


def test_all_27_charts_render_and_export_without_float_finance(qtbot, demo_path, tmp_path):
    a = AnalyticsService(demo_path).calculate(QueryScope(date(2026, 1, 10)))
    canvas = ChartCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(1050, 600)
    canvas.show()
    assert [c.id for c in a.charts] == list(range(1, 28))
    for chart in a.charts:
        canvas.chart = chart
        canvas.repaint()
        qtbot.wait(1)
        path = tmp_path / f"chart-{chart.id:02}.png"
        canvas.export_png(path)
        assert path.read_bytes().startswith(b"\x89PNG") and path.stat().st_size > 5000


def test_financial_pages_filter_source_and_clear_stale_data(qtbot, demo_path):
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.show()
    w.open_database(demo_path)
    w.navigation.setCurrentRow(2)
    qtbot.waitUntil(lambda: w.analytics is not None, timeout=15000)
    assert len(w.dashboard.cards) == 18 and len(w.charts_page.charts) == 27
    assert w.asset_page.report.name == "今日盈亏"
    assert w.asset_page.table.finance_model.columnCount() == 13
    w.person.setCurrentIndex(w.person.findData(2))
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.person_ids == (2,))
    assert w.analytics.reports["当前持仓"].rows[0].values[3] == 4
    w.drill_chart("symbol", "DEMO")
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.instrument_ids == (1,))
    assert w.navigation.currentRow() == 10
    assert w.stock_detail.canvas.chart.id == 28
    w.navigation.setCurrentRow(5)
    w.charts_page.selector.setCurrentIndex(15)
    qtbot.wait(10)
    assert w.charts_page.canvas.hits
    w.open_database(demo_path.parent / "missing.sqlite")
    qtbot.waitUntil(lambda: "不存在" in w.banner.text())
    assert w.analytics is None and not w.asset_page.table.finance_model.rows
    w.close()


def test_report_sort_pagination_and_frozen_first_column(qtbot, demo_path):
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.show()
    w.open_database(demo_path)
    w.navigation.setCurrentRow(2)
    qtbot.waitUntil(lambda: w.analytics is not None, timeout=15000)
    p = w.performance_page
    p.selector.setCurrentText("每日持仓收益")
    assert p.table.finance_model.columnCount() == 23
    assert p.table.frozen.isColumnHidden(22)
    p.sort_column(19)
    assert p.table.finance_model.rows[0].values[19] >= p.table.finance_model.rows[-1].values[19]
    w.close()


def test_preferences_restore_only_for_same_source(qtbot, demo_path, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from portfolio.ui import main_window
    settings = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *args: settings)
    first = MainWindow(settings=True)
    qtbot.addWidget(first)
    first.show()
    first.open_database(demo_path)
    first.navigation.setCurrentRow(2)
    qtbot.waitUntil(lambda: first.analytics is not None)
    first.person.setCurrentIndex(first.person.findData(2))
    qtbot.waitUntil(lambda: first.analytics is not None and first.analytics.scope.person_ids == (2,))
    first.close()
    second = MainWindow(settings=True)
    qtbot.addWidget(second)
    second.show()
    second.open_database(demo_path)
    second.navigation.setCurrentRow(2)
    qtbot.waitUntil(lambda: second.analytics is not None)
    assert second.analytics.scope.person_ids == (2,)
    second.close()


def test_holdings_distribution_page_displays_six_pie_charts_directly(qtbot, demo_path, tmp_path):
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.show()
    w.open_database(demo_path)
    w.navigation.setCurrentRow(15)
    qtbot.waitUntil(lambda: w.analytics is not None, timeout=15000)
    assert w.navigation.currentRow() == 15
    assert w.heading.text() == "持仓分布"
    assert len(w.distribution_page.canvases) == 6
    assert [c.chart.id for c in w.distribution_page.canvases if c.chart] == [1, 2, 3, 4, 5, 6]
    # No 27-chart dropdown on distribution page
    assert not hasattr(w.distribution_page, "selector")
    w.close()


def test_trend_charts_page_displays_trend_charts_directly(qtbot, demo_path, tmp_path):
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.show()
    w.open_database(demo_path)
    w.navigation.setCurrentRow(16)
    qtbot.waitUntil(lambda: w.analytics is not None, timeout=15000)
    assert w.navigation.currentRow() == 16
    assert w.heading.text() == "走势图"
    assert len(w.trend_page.canvases) == 3
    canvas_chart_ids = [c.chart.id for c in w.trend_page.canvases.values() if c.chart]
    assert canvas_chart_ids == [7, 8, 10]
    w.close()


def test_asset_trend_chart_tab_routes_to_chart_18(qtbot, demo_path):
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.show()
    w.open_database(demo_path)
    w.navigation.select(5, 17)
    qtbot.waitUntil(lambda: w.analytics is not None, timeout=15000)
    assert w.pages.currentIndex() == 5
    assert w.charts_page.selector.currentIndex() == 17
    assert w.charts_page.canvas.chart.id == 18
    assert "累计本金与总资产变化" in w.charts_page.canvas.chart.title
    w.close()
