from decimal import Decimal as D

from PySide6.QtGui import QColor

from portfolio.services.charts_service import Chart, Series
from portfolio.ui.charts_page import ChartCanvas


def test_capital_assets_stacks_segments_and_keeps_missing_gap(qtbot):
    chart = Chart(18, '累计本金与总资产变化', 'capital_assets', (
        Series('累计本金基柱', (('2026-01', D(100)), ('2026-02', D(80)), ('2026-03', None))),
        Series('资产高于本金', (('2026-01', D(20)), ('2026-02', D(0)), ('2026-03', None))),
        Series('资产低于本金', (('2026-01', D(0)), ('2026-02', D(20)), ('2026-03', None))),
        Series('总资产', (('2026-01', D(120)), ('2026-02', D(80)), ('2026-03', None))),
    ), 'EXCEL_COMPAT · USD')
    canvas = ChartCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(1000, 600)
    canvas.chart = chart
    canvas.show()
    qtbot.wait(10)
    pixels = canvas.grab().toImage()
    bars = {(key, name): rect for rect, key, value, name in canvas.hits if name != '总资产'}
    assert len(bars) == 4
    assert all(key != '2026-03' for rect, key, value, name in canvas.hits)
    for month, gap_name, color in (('2026-01', '资产高于本金', '#70AD47'), ('2026-02', '资产低于本金', '#C00000')):
        base, gap = bars[month, '累计本金基柱'], bars[month, gap_name]
        assert abs(base.top() - gap.bottom()) < .01
        assert base.center().x() == gap.center().x()
        assert pixels.pixelColor(gap.center().toPoint()) == QColor(color)
        assert pixels.pixelColor(base.center().toPoint()) == QColor('#B8CBD7')


def test_generated_capital_chart_uses_combination_renderer(demo_path):
    from datetime import date

    from portfolio.domain.models import QueryScope
    from portfolio.services.analytics_service import AnalyticsService

    analytics = AnalyticsService(demo_path).calculate(QueryScope(date(2026, 1, 10)))
    assert analytics.charts[17].kind == 'capital_assets'
