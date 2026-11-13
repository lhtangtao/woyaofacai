"""持仓管理「持仓市值趋势」双轴组合图验收测试。"""
from datetime import date

from portfolio.domain.models import QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.open_holdings_analytics_service import compute_open_mv_trend
from portfolio.ui.open_holdings_dashboard import OpenHoldingsChartsRow


def test_open_mv_trend_combo_modes_zeroing_and_legends(qtbot):
    row = OpenHoldingsChartsRow()
    qtbot.addWidget(row)
    row.resize(1200, 360)
    row.show()

    pts = [
        {"date": "2026-08-12", "market_value": 50000.0, "daily_pnl": -500.0, "cumulative_pnl": -10000.0, "incomplete": False},
        {"date": "2026-09-12", "market_value": 55000.0, "daily_pnl": 1200.0, "cumulative_pnl": -7000.0, "incomplete": False},
        {"date": "2026-10-12", "market_value": 48000.0, "daily_pnl": -2100.0, "cumulative_pnl": -12000.0, "incomplete": False},
        {"date": "2026-11-12", "market_value": 60423.35, "daily_pnl": 950.0, "cumulative_pnl": -8000.0, "incomplete": False},
    ]
    row.set_charts_data(
        {
            "cards": {"total_market_value": 60423.35},
            "broker_distribution": [],
            "industry_distribution": [],
            "mv_trend": {"points": pts, "has_incomplete_data": False},
        },
        trend_window="3m",
    )
    row.repaint()

    # 1. 默认：累计盈亏 + 真实累计，左轴非负，右轴包含负数
    tc = row.trend_chart
    assert tc.pnl_metric == "cum" and tc.cum_mode == "real"
    assert tc._mv_bounds[0] >= 0.0
    assert tc._pnl_bounds[0] < 0.0
    assert tc._display_pnl == [-10000.0, -7000.0, -12000.0, -8000.0]

    # 2. 切换至「区间归零」：首点为 0，其余点减去首点，且不修改原始 cumulative_pnl
    row.cum_mode_buttons["zeroed"].click()
    assert tc.cum_mode == "zeroed"
    assert tc._display_pnl == [0.0, 3000.0, -2000.0, 2000.0]
    assert [p["cumulative_pnl"] for p in tc.points] == [-10000.0, -7000.0, -12000.0, -8000.0]

    # 3. 切换至「每日盈亏」：隐藏累计模式控件，显示每日盈亏；切回时恢复之前的「区间归零」
    row.metric_buttons["daily"].click()
    assert tc.pnl_metric == "daily"
    assert row.cum_mode_lbl.isHidden()
    assert tc._display_pnl == [-500.0, 1200.0, -2100.0, 950.0]

    row.metric_buttons["cum"].click()
    assert tc.pnl_metric == "cum"
    assert not row.cum_mode_lbl.isHidden()
    assert tc.cum_mode == "zeroed"
    assert tc._display_pnl == [0.0, 3000.0, -2000.0, 2000.0]

    # 4. 图例独立隐藏/恢复
    row.mv_legend_btn.click()
    assert not tc.show_mv and tc.show_pnl
    row.pnl_legend_btn.click()
    assert not tc.show_mv and not tc.show_pnl
    row.repaint()
    row.mv_legend_btn.click()
    row.pnl_legend_btn.click()
    assert tc.show_mv and tc.show_pnl

    # 5. 空数据不报错
    row.set_charts_data({"cards": {}, "mv_trend": {"points": []}}, trend_window="all")
    row.repaint()


def test_compute_open_mv_trend_real_cumulative_independent_of_window(demo_path):
    svc = AnalyticsService(str(demo_path))
    analytics = svc.calculate(QueryScope(as_of=date(2026, 1, 20), person_ids=(1,)))
    all_res = compute_open_mv_trend(analytics, person_id=1, trend_window="all")
    win_res = compute_open_mv_trend(analytics, person_id=1, trend_window="1m")

    assert all_res["points"]
    assert win_res["points"]
    all_by_date = {p["date"]: p for p in all_res["points"]}
    for p in win_res["points"]:
        ref = all_by_date[p["date"]]
        assert p["market_value"] == ref["market_value"]
        assert p["daily_pnl"] == ref["daily_pnl"]
        assert p["cumulative_pnl"] == ref["cumulative_pnl"]
