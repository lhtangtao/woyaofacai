"""测试持仓分布页面 V2.0 与数据计算服务"""

from datetime import date
from decimal import Decimal
import pytest
from PySide6.QtCore import Qt

from portfolio.domain.models import QueryScope, Ledger
from portfolio.paths import DATABASE_PATH
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.distribution_service import (
    calculate_distribution,
    get_available_months,
    get_stock_holding_sources,
)
from portfolio.ui.distribution_page import HoldingsDistributionPage


@pytest.fixture
def analytics():
    svc = AnalyticsService(DATABASE_PATH)
    return svc.calculate(QueryScope(as_of=date.today(), ledger=Ledger.PERSON, person_ids=(1,)))


def test_distribution_service_summary(analytics):
    summary = calculate_distribution(analytics)
    assert summary is not None
    # 验证股票市值大于 0
    assert summary.current_market_value > Decimal("0")
    # 验证去重标的数及变动
    assert summary.holding_count > 0
    assert isinstance(summary.holding_count_change, int)
    # 验证前五大集中度处于 0~100% 之间及变动
    assert Decimal("0") < summary.top5_concentration <= Decimal("100")
    assert isinstance(summary.top5_concentration_diff_pp, Decimal)
    # 验证 Top 5 股票列表存在
    assert len(summary.stocks_top) <= 6  # 5 + 其他
    # 验证渠道列表存在
    assert len(summary.channels) > 0
    # 验证分类列表存在
    assert len(summary.categories) > 0


def test_distribution_service_compare_month(analytics):
    months = get_available_months(analytics)
    assert len(months) >= 2
    obs_m = months[0]
    cmp_m = months[1]

    summary = calculate_distribution(analytics, selected_month=obs_m, compare_month=cmp_m)
    assert summary is not None
    assert summary.as_of_label == f"{obs_m} 月末"
    assert summary.prev_as_of_label == f"上月末 ({cmp_m})"
    # 检查各股票占比变化之和合理性
    for s in summary.stocks_top:
        assert isinstance(s.diff_pp, Decimal)


def test_stock_holding_sources(analytics):
    summary = calculate_distribution(analytics)
    assert summary is not None
    top_ticker = summary.all_stocks[0].name
    sources = get_stock_holding_sources(analytics, top_ticker)
    assert len(sources) > 0
    for src in sources:
        assert src["ticker"] == top_ticker
        assert src["shares"] > Decimal("0")
        assert src["market_value"] > Decimal("0")


def test_distribution_page_ui(qtbot, analytics):
    page = HoldingsDistributionPage()
    qtbot.addWidget(page)
    page.show()

    page.set_analytics(analytics)
    assert page.summary is not None
    assert page.kpi_mv.value_lbl.text() != "--"
    assert page.kpi_count.value_lbl.text() != "--"

    # 验证明细表格列结构
    assert page.table_stocks.columnCount() == 12
    stock_headers = [page.table_stocks.horizontalHeaderItem(i).text() for i in range(12)]
    assert stock_headers == [
        "Ticker", "分类", "渠道", "当期股数", "上期股数", "股数变化", "当前市值", "上期市值", "市值变化", "当前占比", "上期占比", "占比变化"
    ]

    assert page.table_channels.columnCount() == 9
    channel_headers = [page.table_channels.horizontalHeaderItem(i).text() for i in range(9)]
    assert channel_headers == [
        "渠道", "当期标的数", "上期标的数", "当前市值", "上期市值", "市值变化", "当前占比", "上期占比", "占比变化"
    ]

    assert page.table_categories.columnCount() == 9
    category_headers = [page.table_categories.horizontalHeaderItem(i).text() for i in range(9)]
    assert category_headers == [
        "分类", "当期标的数", "上期标的数", "当前市值", "上期市值", "市值变化", "当前占比", "上期占比", "占比变化"
    ]

    # 测试打开股票抽屉
    page._open_top_stock_drawer()
    assert page.drawer.isVisible()
    assert page.drawer.mode == page.drawer.MODE_STOCK

    # 测试关闭抽屉
    page.drawer.close_drawer()
    assert not page.drawer.isVisible()

    # 测试打开渠道抽屉
    page._open_top_broker_drawer()
    assert page.drawer.isVisible()
    assert page.drawer.mode == page.drawer.MODE_BROKER
    page.drawer.close_drawer()

    # 测试打开分类抽屉
    page._open_top_category_drawer()
    assert page.drawer.isVisible()
    assert page.drawer.mode == page.drawer.MODE_CATEGORY
    page.drawer.close_drawer()

    # 测试点击“其他”唤起非五大持仓抽屉
    page._open_stock_drawer_by_name("其他")
    assert page.drawer.isVisible()
    assert page.drawer.mode == page.drawer.MODE_STOCK
    assert "其他" in page.drawer.title_lbl.text()
    page.drawer.close_drawer()

    # 测试筛选重置
    page.reset_filters()
    assert page.obs_combo.currentIndex() == 0

    page.close()
