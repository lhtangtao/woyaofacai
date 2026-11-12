"""持仓管理右侧抽屉「收益分析」双轴组合图验收测试。"""
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from portfolio.services.holdings_management_service import HoldingsManagementService
from portfolio.ui.main_window import MainWindow
from portfolio.ui.pnl_analysis_charts import PnlAnalysisChartsWidget


def test_pnl_combo_charts_dual_axis_legend_window_and_hover(qtbot):
    w = PnlAnalysisChartsWidget()
    qtbot.addWidget(w)
    w.resize(520, 560)
    w.show()

    payload = {
        "valuation_note": "估值口径一致（截至 2026-11-11 收盘）",
        "chart_dates": ["2026-01-05", "2026-04-10", "2026-07-13", "2026-09-20", "2026-11-11"],
        "daily_pnls": [120.5, -85.2, 240.0, -310.4, -128.47],
        "cumulative_pnls": [120.5, 35.3, 873.26, 210.0, -299.35],
        "floating_pnls": [120.5, 35.3, 338.90, 0.0, -393.29],
        "daily_rates": [0.024, -0.017, 0.045, -0.052, -0.0142],
        "cumulative_rates": [0.024, 0.007, 0.2137, 0.051, -0.0823],
        "floating_rates": [0.024, 0.007, 0.0832, None, -0.1063],
    }
    w.set_data(payload)
    w.repaint()

    # 1. 双轴范围与 0 基准线对齐验证
    al = w.amount_canvas._left_bounds
    ar = w.amount_canvas._right_bounds
    assert al[0] < 0 < al[1] and ar[0] < 0 < ar[1]
    assert abs((-al[0] / (al[1] - al[0])) - (-ar[0] / (ar[1] - ar[0]))) < 1e-6

    rl = w.rate_canvas._left_bounds
    rr = w.rate_canvas._right_bounds
    assert rl[0] < 0 < rl[1] and rr[0] < 0 < rr[1]
    assert abs((-rl[0] / (rl[1] - rl[0])) - (-rr[0] / (rr[1] - rr[0]))) < 1e-6

    # 2. 时间范围切换同步更新两张图
    w.set_time_window("3m")
    assert w.amount_canvas._dates == ["2026-09-20", "2026-11-11"]
    assert w.rate_canvas._dates == ["2026-09-20", "2026-11-11"]
    w.set_time_window("all")
    assert len(w.amount_canvas._dates) == 5

    # 3. 图例点击独立隐藏/恢复且不改变其他系列比例
    saved_ar = w.amount_canvas._right_bounds
    w.amt_bar_btn.click()
    assert not w.amount_canvas.show_bar
    assert w.amount_canvas._right_bounds == saved_ar
    w.amt_cum_btn.click()
    w.amt_flt_btn.click()
    assert not (w.amount_canvas.show_bar or w.amount_canvas.show_cum or w.amount_canvas.show_flt)
    w.repaint()
    w.amt_bar_btn.click()
    w.amt_cum_btn.click()
    w.amt_flt_btn.click()
    assert w.amount_canvas.show_bar and w.amount_canvas.show_cum and w.amount_canvas.show_flt

    # 4. 两图悬停联动
    w.amount_canvas.hoverIndexChanged.emit(2)
    assert w.rate_canvas._hover_idx == 2
    w.amount_canvas.hoverIndexChanged.emit(None)
    assert w.rate_canvas._hover_idx is None

    # 5. 空数据与单点数据不报错
    w.set_data({"chart_dates": [], "daily_pnls": [], "cumulative_pnls": [], "floating_pnls": [], "daily_rates": [], "cumulative_rates": [], "floating_rates": []})
    w.repaint()
    w.set_data({"chart_dates": ["2026-07-13"], "daily_pnls": [0.0], "cumulative_pnls": [0.0], "floating_pnls": [None], "daily_rates": [None], "cumulative_rates": [None], "floating_rates": [None]})
    w.repaint()


def test_drawer_pnl_analysis_real_demo_parity(qtbot, demo_path):
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.open_database(demo_path)
    qtbot.waitUntil(lambda: w.analytics is not None, timeout=15000)

    pa = w.analytics.reports.get("人员资产")
    assert pa and pa.rows
    first_row = pa.rows[0]
    ticker = str(first_row.values[2])
    drawer_data = HoldingsManagementService.get_stock_drawer_data(
        str(demo_path), w.analytics, ticker, person_id=first_row.metadata.get("person_id")
    )
    pnl = drawer_data["pnl_analysis"]
    assert "daily_pnls" in pnl and "floating_pnls" in pnl and "daily_rates" in pnl
    assert len(pnl["chart_dates"]) == len(pnl["daily_pnls"]) == len(pnl["cumulative_pnls"]) == len(pnl["floating_pnls"])
    assert len(pnl["chart_dates"]) == len(pnl["daily_rates"]) == len(pnl["cumulative_rates"]) == len(pnl["floating_rates"])

    w.holdings_page.drawer.set_data(drawer_data)
    assert w.holdings_page.drawer.pnl_canvas.amount_canvas._dates == pnl["chart_dates"]
