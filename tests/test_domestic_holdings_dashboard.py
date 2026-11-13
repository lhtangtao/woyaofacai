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
