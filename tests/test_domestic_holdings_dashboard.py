from datetime import date
from decimal import Decimal

from portfolio.ui.domestic_holdings_dashboard import holding_metrics, trend_points


def test_sub_yuan_filter_only_hides_known_current_holdings():
    from portfolio.ui.domestic_holdings_page import show_in_overview

    assert not show_in_overview(dict(current_position_status="持有中", market_value="0.72"))
    assert show_in_overview(dict(current_position_status="持有中", market_value="1"))
    assert show_in_overview(dict(current_position_status="持有中", market_value=None))
    assert show_in_overview(dict(current_position_status="已清仓", market_value="0"))


def test_closed_table_shows_conversion_separately_from_redemption():
    from PySide6.QtWidgets import QApplication
    from portfolio.ui.domestic_holdings_page import CLOSED_POSITIONS, FundTable

    app = QApplication.instance() or QApplication([])
    table = FundTable(CLOSED_POSITIONS, "realized", expand_rows=True)
    table.rows = [dict(fund_name="基金一", fund_code="000001", sell_amount=None,
                       transfer_out_amount=Decimal("1796.24"))]
    table.set_page(0)
    fields = list(CLOSED_POSITIONS)
    redemption = table.table.item(0, fields.index("sell_amount"))
    transfer = table.table.item(0, fields.index("transfer_out_amount"))
    assert redemption.text() == "—" and "转换转出" in redemption.toolTip()
    assert transfer.text() == "1,796.24"


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


def test_contribution_bars_share_scale_and_open_selected_fund():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from portfolio.ui.domestic_holdings_dashboard import ContributionPlot

    app = QApplication.instance() or QApplication([])
    selected = []
    plot = ContributionPlot(selected.append)
    loss = dict(fund_code="000001", fund_name="基金一", platform="支付宝", floating=Decimal("-10"))
    gain = dict(fund_code="000002", fund_name="基金二", platform="天天基金", floating=Decimal("20"))
    plot.resize(800, 150)
    plot.set_data([loss], [gain], "floating")
    plot.show()
    app.processEvents()
    plot.grab()
    assert abs(plot.hits[0][0].width() * 2 - plot.hits[2][0].width()) < 0.01
    QTest.mouseClick(plot, Qt.MouseButton.LeftButton, pos=plot.hits[0][0].center().toPoint())
    assert selected == [loss]
    plot.close()


def test_contribution_top_count_can_change_for_both_modes():
    from PySide6.QtWidgets import QApplication
    from portfolio.ui.domestic_holdings_dashboard import Contribution

    app = QApplication.instance() or QApplication([])
    chart = Contribution(lambda row: None)
    rows = [dict(fund_code=f"{i:06}", floating=Decimal(i), realized=Decimal(-i)) for i in range(1, 9)]
    chart.render(rows)
    assert len(chart.chart.gains) == 5
    chart.top_n.setValue(3)
    assert [r["floating"] for r in chart.chart.gains] == [8, 7, 6]
    assert "Top 3" in chart.gain_label.text() and "36.00" in chart.gain_label.text()
    chart.render(rows, closed=True)
    assert [r["realized"] for r in chart.chart.losses] == [-8, -7, -6]
    chart.top_n.setValue(7)
    assert len(chart.chart.losses) == 7


def test_fund_detail_opens_drawer_and_preserves_list_state(monkeypatch):
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
    from portfolio.ui.domestic_holdings_page import DomesticHoldingsPage

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    layout = QVBoxLayout(host)
    page = DomesticHoldingsPage(host)
    layout.addWidget(page)
    host.resize(1400, 900)
    host.show()
    app.processEvents()
    monkeypatch.setattr(page, "reload", lambda: None)
    monkeypatch.setattr(page, "render_detail", lambda: None)
    page.search.setText("基金一")
    page.open_fund(dict(account_id="a", fund_code="000001"))
    app.processEvents()
    assert not page.drawer.isHidden() and page.stack.currentIndex() == 0
    assert page.selected == ("a", "000001")
    assert page.graph_grid.columns == 3
    assert page.detail_metric_grid.columns == 6
    assert len({card.y() for card in page.detail_metrics.values()}) == 1
    page.back()
    app.processEvents()
    assert page.drawer.isHidden() and page.selected is None
    assert page.search.text() == "基金一"
    assert (page.metric_grid.columns, page.graph_grid.columns) == (6, 3)
    page.modes.button(1).setChecked(True)
    page.render_overview()
    page.open_fund(dict(account_id="a", fund_code="000001"))
    app.processEvents()
    page.back()
    app.processEvents()
    assert (page.metric_grid.columns, page.graph_grid.columns) == (6, 3)
    host.close()


def test_closed_date_filters_share_row_and_chart_legend_is_centered():
    from PySide6.QtCharts import QChartView
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
    from portfolio.ui.domestic_holdings_page import DomesticHoldingsPage

    app = QApplication.instance() or QApplication([])
    host = QWidget(); layout = QVBoxLayout(host)
    page = DomesticHoldingsPage(host); layout.addWidget(page)
    host.resize(1600, 900); host.show(); app.processEvents()
    page.modes.button(1).setChecked(True); page.render_overview(); app.processEvents()
    assert page.close_dates.isVisible()
    assert page.close_start.mapTo(host, QPoint()).y() == page.search.mapTo(host, QPoint()).y()
    page.modes.button(0).setChecked(True)
    page.by_type.render([dict(fund_code="000001", fund_type="指数", market_value=Decimal("10"))], "fund_type", Decimal("10"))
    app.processEvents()
    plot = page.by_type.findChild(QChartView)
    assert abs(plot.geometry().center().y() - page.by_type.legend.geometry().center().y()) <= 2
    host.close()


def test_holding_metrics_includes_partial_realized_pnl_when_available():
    rows = [
        dict(
            account_id="a", fund_code="040046", shares="100",
            market_value="150", cost="100", contributed="200", pnl="80", rate="40",
        ),
    ]
    prepared, stats = holding_metrics(rows)
    assert prepared[0]["floating"] == Decimal("80")
    assert prepared[0]["floating_rate"] == Decimal("40")
    assert stats["floating"] == Decimal("80")
    assert stats["rate"] == Decimal("40")


def test_trend_combo_points_computes_cumulative_and_daily_pnl():
    from portfolio.domestic.holdings_history import HistoricalValues
    from portfolio.ui.domestic_holdings_dashboard import trend_combo_points

    history = HistoricalValues()
    d1, d2, d3 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)
    key = ("ALIPAY_FUND", "019172")
    history[d1] = {key: Decimal("1000")}
    history[d2] = {key: Decimal("1050")}
    history[d3] = {key: Decimal("1030")}
    history.cum_pnl[d1] = {key: Decimal("100")}
    history.cum_pnl[d2] = {key: Decimal("150")}
    history.cum_pnl[d3] = {key: Decimal("130")}

    pts = trend_combo_points(history, [dict(account_id="ALIPAY_FUND", fund_code="019172")], "year", d3)
    assert len(pts) == 3
    assert pts[0]["cumulative_pnl"] == 100.0 and pts[0]["daily_pnl"] is None
    assert pts[1]["cumulative_pnl"] == 150.0 and pts[1]["daily_pnl"] == 50.0
    assert pts[2]["cumulative_pnl"] == 130.0 and pts[2]["daily_pnl"] == -20.0


def test_closed_trend_extends_last_confirmed_result_to_cutoff():
    from datetime import date
    from decimal import Decimal
    from portfolio.ui.domestic_holdings_dashboard import closed_trend_points

    rows = [{'closed_events': [('2026-10-09', '-100')]}]
    points, covered = closed_trend_points(rows, 'year', date(2026, 11, 14))
    assert points[-1] == (date(2026, 11, 14), Decimal('-100'))
    assert covered == 1
    recent, _ = closed_trend_points(rows, 1, date(2026, 11, 14))
    assert recent == [(date(2026, 10, 14), Decimal('-100')), (date(2026, 11, 14), Decimal('-100'))]
