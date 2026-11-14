"""国内资产每日与每月持仓收益（计算引擎 + UI 页面）验收测试。"""
from PySide6.QtWidgets import QApplication

from portfolio.domestic.performance import DomesticPerformanceData
from portfolio.domestic.trades_query import classify_direction
from portfolio.ui.domestic_performance_page import DomesticDailyPerformancePage, DomesticMonthlyPerformancePage
from portfolio.ui.domestic_trades_page import DomesticTradesPage


def _sample_data() -> DomesticPerformanceData:
    daily_rows = [
        {
            "date": "2026-01-05", "month": "2026-01", "platform": "支付宝",
            "fund_code": "019172", "fund_name": "摩根纳斯达克100指数(QDII)A", "fund_type": "指数型-海外股票",
            "market_value": 10000.0, "daily_pnl": 200.0, "cum_pnl": 1200.0,
            "buy_amount": 1000.0, "sell_amount": 0.0, "net_cash_in": 1000.0,
            "cum_net_cash_in": 8800.0, "trade_count": 1,
        },
        {
            "date": "2026-01-06", "month": "2026-01", "platform": "天天基金",
            "fund_code": "000043", "fund_name": "嘉实美国成长股票(QDII)", "fund_type": "QDII-普通股票",
            "market_value": 5000.0, "daily_pnl": -80.0, "cum_pnl": 320.0,
            "buy_amount": 0.0, "sell_amount": 500.0, "net_cash_in": -500.0,
            "cum_net_cash_in": 4680.0, "trade_count": 1,
        },
    ]
    monthly_rows = [
        {
            "month": "2026-01", "platform": "支付宝",
            "fund_code": "019172", "fund_name": "摩根纳斯达克100指数(QDII)A", "fund_type": "指数型-海外股票",
            "end_mv": 10000.0, "month_pnl": 200.0, "cum_pnl": 1200.0,
            "buy_amount": 1000.0, "sell_amount": 0.0, "net_cash_in": 1000.0,
            "cum_net_cash_in": 8800.0, "trade_count": 1,
        },
        {
            "month": "2026-01", "platform": "天天基金",
            "fund_code": "000043", "fund_name": "嘉实美国成长股票(QDII)", "fund_type": "QDII-普通股票",
            "end_mv": 5000.0, "month_pnl": -80.0, "cum_pnl": 320.0,
            "buy_amount": 0.0, "sell_amount": 500.0, "net_cash_in": -500.0,
            "cum_net_cash_in": 4680.0, "trade_count": 1,
        },
    ]
    return DomesticPerformanceData(
        daily_rows=daily_rows,
        monthly_rows=monthly_rows,
        platforms=["天天基金", "支付宝"],
        fund_types=["QDII-普通股票", "指数型-海外股票"],
        as_of="2026-01-06",
    )


def test_domestic_daily_and_monthly_pages_render_and_filter(qtbot):
    _ = QApplication.instance()
    sample = _sample_data()

    daily_page = DomesticDailyPerformancePage()
    qtbot.addWidget(daily_page)
    daily_page._on_loaded(sample, "")
    assert len(daily_page._period_series) == 2
    assert daily_page._models[0].rowCount() == 1
    assert daily_page._models[2].rowCount() == 0  # 未切到 Tab 2 时不填充

    # 切换到 按日总览 Tab
    daily_page.table_tab_group.button(2).click()
    assert daily_page._models[2].rowCount() == 2

    # 点击渠道筛选
    daily_page._toggle_platform("支付宝")
    assert len(daily_page._filtered_rows) == 1
    daily_page._reset_filters()
    assert len(daily_page._filtered_rows) == 2

    monthly_page = DomesticMonthlyPerformancePage()
    qtbot.addWidget(monthly_page)
    monthly_page._on_loaded(sample, "")
    assert len(monthly_page._period_series) == 1
    assert monthly_page._models[0].rowCount() == 2
    assert "2026-01" in monthly_page.attr_title_lbl.text()

    # 切换图表模式并重绘
    for idx in range(4):
        monthly_page.chart_tab_group.button(idx).click()
        monthly_page.trend_canvas.repaint()
    daily_page.donut.repaint()
    monthly_page.donut.repaint()

    # 切换 按基金 / 按日期(Top 10) 维度并下钻
    daily_page.attr_dim_group.button(1).click()
    assert "极值排行" in daily_page.attr_title_lbl.text()
    daily_page._drill_period_from_top("2026-01-05")
    assert daily_page._attr_dim == "fund"
    assert daily_page._selected_period == "2026-01-05"


def test_domestic_trades_page_filters_and_kpis(qtbot):
    assert classify_direction("定时定额投资") == "买入"
    assert classify_direction("卖基金") == "卖出"
    assert classify_direction("红利再投资") == "分红"

    page = DomesticTradesPage()
    qtbot.addWidget(page)
    sample_trades = {
        "rows": [
            {
                "id": "1", "trade_date": "2026-11-11", "transaction_time": "2026-11-11 14:30:00",
                "confirmation_date": "2026-11-11", "platform": "天天基金",
                "fund_code": "016452", "fund_name": "南方纳斯达克100", "fund_type": "指数型-海外股票",
                "direction": "买入", "transaction_type_raw": "定时定额投资",
                "application_amount": 100.0, "confirmed_amount": 100.0,
                "confirmed_shares": 46.4, "confirmed_nav": 2.1552, "fee": 0.1,
                "status": "成功", "order_id": "ORD001",
            },
            {
                "id": "2", "trade_date": "2026-10-15", "transaction_time": "2026-10-15 10:00:00",
                "confirmation_date": "2026-10-15", "platform": "支付宝",
                "fund_code": "019172", "fund_name": "摩根纳斯达克100", "fund_type": "指数型-海外股票",
                "direction": "卖出", "transaction_type_raw": "用户卖出",
                "application_amount": None, "confirmed_amount": 500.0,
                "confirmed_shares": 300.0, "confirmed_nav": 1.6667, "fee": 0.5,
                "status": "成功", "order_id": "ORD002",
            },
        ],
        "platforms": ["天天基金", "支付宝"],
        "directions": ["买入", "卖出"],
        "raw_types": ["定时定额投资", "用户卖出"],
        "fund_types": ["指数型-海外股票"],
    }
    page._on_loaded(sample_trades, "")
    assert page.model.rowCount() == 2
    assert page._kpi_labels["count"][0].text() == "2"

    page.platform_combo.setCurrentIndex(page.platform_combo.findData("支付宝"))
    assert page.model.rowCount() == 1
    assert page._kpi_labels["sell"][0].text() == "¥500.00"
    page._reset_filters()
    assert page.model.rowCount() == 2
