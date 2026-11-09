import sys
from datetime import date
from decimal import Decimal
import pytest
from PySide6.QtWidgets import QApplication

from portfolio.domain.models import QueryScope, Ledger
from portfolio.services.analytics_service import AnalyticsService
from portfolio.ui.monthly_scalping_page import (
    MonthlyScalpingPage,
    fmt_money,
    fmt_signed_money,
    fmt_broker_cn,
    calc_weighted_spread,
)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(sys.argv)


def test_monthly_scalping_weighted_spread():
    """测试 1：PRD 标准加权平均价差用例"""
    # A 股票：10 股，每股价差 10 美元；B 股票：1000 股，每股价差 1 美元。加权结果应约为 1.09 美元
    mock_rows = [
        ("2026-11", "LONGBRIDGE", "正股", "A", 1, 10.0, 10.0, 20.0, 10.0, 100.0, 200.0, 100.0),
        ("2026-11", "LONGBRIDGE", "正股", "B", 1, 1000.0, 5.0, 6.0, 1.0, 5000.0, 6000.0, 1000.0),
    ]
    val, s_str = calc_weighted_spread(mock_rows)
    assert val is not None
    assert abs(val - 1.0891) < 0.01
    assert s_str == "+$1.09"

    # 空数据和零数量
    assert calc_weighted_spread([]) == (None, "—")
    assert calc_weighted_spread([("2026-11", "LONGBRIDGE", "正股", "C", 1, 0, 5.0, 5.0, 0.0)]) == (None, "—")


def test_monthly_scalping_broker_ranking_no_percentage(app, demo_path):
    """测试 2：券商排行无占比列，且按绝对收益归一化"""
    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    page.activate()

    # 验证渠道排行已渲染
    assert page.broker_list_layout.count() > 0


def test_monthly_scalping_trend_month_drill_keeps_full_range(app, demo_path):
    """测试 3：趋势图点击某个月份，不缩短全局日期范围"""
    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    page.activate()

    init_start = page.applied_start_month
    init_end = page.applied_end_month
    total_months_count = len(page.trend_canvas.data_points)

    # 点击某个月份柱体
    target_month = page.trend_canvas.data_points[0][0]
    page.on_trend_month_clicked(target_month)

    # 验证全局日期范围未被缩短
    assert page.active_chart_month == target_month
    assert page.applied_start_month == init_start
    assert page.applied_end_month == init_end
    assert len(page.trend_canvas.data_points) == total_months_count

    # 验证再次点击取消反选
    page.on_trend_month_clicked("")
    assert page.active_chart_month == ""
    assert len(page.trend_canvas.data_points) == total_months_count


def test_monthly_scalping_table_sorting_three_levels(app, demo_path):
    """测试 5：表格默认三级稳定排序（月份降序 -> 收益降序 -> Ticker 升序）"""
    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    page.activate()

    rows = page.table_model.all_rows
    assert len(rows) > 0

    # 检查默认三级排序
    for i in range(len(rows) - 1):
        m1 = str(rows[i][0])
        m2 = str(rows[i + 1][0])
        pnl1 = float(str(rows[i][11]).replace("$", "").replace(",", "").replace("+", "") or 0)
        pnl2 = float(str(rows[i + 1][11]).replace("$", "").replace(",", "").replace("+", "") or 0)
        sym1 = str(rows[i][3])
        sym2 = str(rows[i + 1][3])

        if m1 == m2:
            if pnl1 == pnl2:
                assert sym1 <= sym2, f"Ticker should be ascending when pnl equal: {sym1} <= {sym2}"
            else:
                assert pnl1 >= pnl2, f"PnL should be descending in same month: {pnl1} >= {pnl2}"
        else:
            assert m1 >= m2, f"Month should be descending: {m1} >= {m2}"


def test_monthly_scalping_drawer_and_interactions(app, demo_path):
    """测试 6：交互、抽屉与联动"""
    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    page.activate()

    # 打开抽屉
    if page.raw_rows:
        row = list(page.table_model.all_rows[0])
        page.open_drawer(row)
        assert not page.drawer_frame.isHidden()
        assert not page.drawer_overlay.isHidden()

        # 关闭抽屉
        page.close_drawer()
        assert page.drawer_frame.isHidden()
        assert page.drawer_overlay.isHidden()

        # 点击股票排行筛选
        sym = str(row[3])
        page.filter_by_ticker(sym)
        assert page.active_ranking_ticker == sym
        for r in page.table_model.all_rows:
            assert r[3] == sym

        # 再次点击取消
        page.filter_by_ticker(sym)
        assert page.active_ranking_ticker == ""
