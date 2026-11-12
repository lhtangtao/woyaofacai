from datetime import date
from decimal import Decimal

from portfolio.ui.domestic_holdings_dashboard import holding_metrics, trend_points


def test_domestic_dashboard_aggregates_matched_costs_and_preserves_history_gaps():
    rows = [
        dict(account_id="a", fund_code="000001", shares="10", market_value="100", cost="50"),
        dict(account_id="b", fund_code="000001", shares="20", market_value="100", cost="100"),
        dict(account_id="b", fund_code="000002", shares="30", market_value="150", cost=None),
        dict(account_id="b", fund_code="000003", shares="40", market_value=None, cost="20"),
    ]
    prepared, stats = holding_metrics(rows)
    assert (stats["market"], stats["cost"], stats["floating"], stats["count"], stats["matched"]) == (350, 150, 50, 3, 2)
    assert stats["rate"].quantize(Decimal(".01")) == Decimal("33.33")
    assert prepared[2]["floating"] is None and prepared[3]["weight"] is None

    rows += [dict(account_id="b", fund_code=f"{i:06}", market_value="100") for i in range(4, 8)]
    _, stats = holding_metrics(rows)
    assert stats["top"].quantize(Decimal(".01")) == Decimal("86.67")

    first, second = date(2026, 2, 1), date(2026, 2, 2)
    history = {
        first: {("a", "000001"): Decimal(45), ("b", "000001"): Decimal(0)},
        second: {("a", "000001"): Decimal(50), ("b", "000001"): None},
    }
    assert trend_points(history, rows[:2], "year", second) == [(first, Decimal(45)), (second, None)]
    assert trend_points(history, rows[:1], "year", second) == [(first, Decimal(45)), (second, Decimal(50))]


def test_history_area_does_not_draw_gap_edges():
    from PySide6.QtCharts import QAreaSeries, QLineSeries
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from portfolio.ui.domestic_holdings_dashboard import TrendView

    app = QApplication.instance() or QApplication([])
    view = TrendView()
    view.render([(date(2022, 1, day), value) for day, value in enumerate(
        (Decimal(10), Decimal(11), None, Decimal(12), Decimal(13)), 1)])
    areas = [series for series in view.chart().series() if isinstance(series, QAreaSeries)]
    lines = [series for series in view.chart().series() if isinstance(series, QLineSeries)]
    assert len(areas) == len(lines) == 2
    assert all(area.pen().style() == Qt.PenStyle.NoPen for area in areas)
    assert all(line.count() == 2 for line in lines)
