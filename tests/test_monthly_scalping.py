import sys
from datetime import date
from decimal import Decimal
import pytest
from PySide6.QtWidgets import QApplication

from portfolio.domain.models import QueryScope, Ledger
from portfolio.services.analytics_service import AnalyticsService
from portfolio.ui.monthly_scalping_page import MonthlyScalpingPage, fmt_money, fmt_signed_money, fmt_broker_cn


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(sys.argv)


def test_monthly_scalping_page_render_and_metrics(app, demo_path):
    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    page.activate()

    # 1. 验证 14 列字段
    assert len(page.table_model.columns) == 14
    expected_cols = [
        "月份", "券商渠道", "分类", "Ticker", "微操次数",
        "微操数量", "微操均买价($)", "微操均卖价($)", "每股价差($)",
        "买入金额($)", "卖出金额($)", "微操收益($)", "首笔微操时间", "最近微操时间"
    ]
    assert list(page.table_model.columns) == expected_cols

    # 2. 验证 KPI 指标卡
    assert "pnl" in page.kpi_cards
    assert "trades" in page.kpi_cards
    assert "buy" in page.kpi_cards
    assert "sell" in page.kpi_cards
    assert "spread" in page.kpi_cards

    # 3. 验证抽屉展开与关闭
    if page.raw_rows:
        row = list(page.raw_rows[0])
        page.open_drawer(row)
        assert not page.drawer_frame.isHidden()
        assert not page.drawer_overlay.isHidden()

        page.close_drawer()
        assert page.drawer_frame.isHidden()
        assert page.drawer_overlay.isHidden()

    # 4. 验证 Ticker 联动筛选
    if page.raw_rows:
        sym = page.raw_rows[0][3]
        page.filter_by_ticker(sym)
        assert page.ticker_edit.text() == sym
        for r in page.current_filtered_rows:
            assert r[3].upper() == sym.upper()

        # 再次点击反选
        page.filter_by_ticker(sym)
        assert page.ticker_edit.text() == ""


def test_monthly_scalping_formatters():
    assert fmt_broker_cn("LONGBRIDGE") == "长桥"
    assert fmt_broker_cn("FIRSTTRADE") == "第一证券"
    assert fmt_broker_cn("SCHWAB") == "嘉信"
    assert fmt_money(1234.56) == "1,234.56"
    assert fmt_signed_money(1234.56) == "+1,234.56"
    assert fmt_signed_money(-78.9) == "-78.90"
