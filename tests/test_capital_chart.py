from datetime import date
from decimal import Decimal as D

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QSizePolicy

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domain.models import QueryScope
from portfolio.engine.performance import DAILY_COLUMNS, MONTHLY_COLUMNS
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.charts_service import Chart, Series, build_charts
from portfolio.ui.charts_page import ChartCanvas, ChartsPage, SparklineWidget


def test_capital_assets_stacks_segments_and_keeps_missing_gap(qtbot):
    chart = Chart(
        18,
        "累计本金与总资产变化",
        "capital_assets",
        (
            Series(
                "累计本金基柱",
                (("2026-01", D(100)), ("2026-02", D(80)), ("2026-03", None)),
            ),
            Series(
                "资产高于本金",
                (("2026-01", D(20)), ("2026-02", D(0)), ("2026-03", None)),
            ),
            Series(
                "资产低于本金",
                (("2026-01", D(0)), ("2026-02", D(20)), ("2026-03", None)),
            ),
            Series(
                "总资产",
                (("2026-01", D(120)), ("2026-02", D(80)), ("2026-03", None)),
            ),
        ),
        "EXCEL_COMPAT · USD",
    )
    canvas = ChartCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(1000, 600)
    canvas.chart = chart
    canvas.show()
    qtbot.wait(10)
    pixels = canvas.grab().toImage()
    bars = {
        (key, name): rect
        for rect, key, value, name in canvas.hits
        if name != "总资产"
    }
    assert len(bars) == 4
    assert all(key != "2026-03" for rect, key, value, name in canvas.hits)
    for month, gap_name, color in (
        ("2026-01", "资产高于本金", "#70AD47"),
        ("2026-02", "资产低于本金", "#C00000"),
    ):
        base, gap = bars[month, "累计本金基柱"], bars[month, gap_name]
        assert abs(base.top() - gap.bottom()) < 0.01
        assert base.center().x() == gap.center().x()
        assert pixels.pixelColor(gap.center().toPoint()) == QColor(color)
        assert pixels.pixelColor(base.center().toPoint()) == QColor("#B8CBD7")


def test_generated_capital_chart_uses_combination_renderer(demo_path):
    analytics = AnalyticsService(demo_path).calculate(QueryScope(date(2026, 1, 10)))
    chart_18 = analytics.charts[17]
    assert chart_18.kind == "capital_assets"
    assert "累计本金与总资产变化" in chart_18.title
    assert "Excel兼容口径" in chart_18.note or "EXCEL_COMPAT" in chart_18.note
    assert "monthly_investments" in chart_18.extra


def test_capital_kpi_cards_calculation_and_sparklines(qtbot):
    page = ChartsPage()
    qtbot.addWidget(page)
    page.resize(1000, 700)
    page.show()

    chart = Chart(
        18,
        "累计本金与总资产变化",
        "capital_assets",
        (
            Series("累计本金基柱", (("2026-01", D(50000)), ("2026-02", D(45000)))),
            Series("资产高于本金", (("2026-01", D(3200)), ("2026-02", D(0)))),
            Series("资产低于本金", (("2026-01", D(0)), ("2026-02", D(5000)))),
            Series("总资产", (("2026-01", D(53200)), ("2026-02", D(45000)))),
        ),
        "Excel兼容口径 · 币种 USD",
        extra={
            "monthly_investments": {"2026-01": D(50000), "2026-02": D(0)},
            "latest_profit_day": "2026-02-15",
        },
    )

    page.update_kpi_cards(chart)
    assert "$45,000" in page.card_market_val.lbl_value.text()
    assert "$50,000" in page.card_net_buy.lbl_value.text()
    assert "-$5,000" in page.card_diff.lbl_value.text()
    assert "↘" in page.card_diff.badge.text()
    assert "-10.0%" in page.card_diff.badge.text()
    assert len(page.card_market_val.sparkline.points_data) == 2
    # 验证折线图拉长：最小宽度 >= 140 且支持横向自适应伸展
    assert page.card_market_val.sparkline.minimumWidth() >= 140
    assert page.card_market_val.sparkline.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding


def test_capital_filter_card_interactions_and_chips(qtbot):
    page = ChartsPage()
    qtbot.addWidget(page)
    page.show()

    fc = page.filter_card
    # 验证红框部分（内部人员下拉框）与高级筛选按钮已彻底移除
    assert not hasattr(fc, "combo_person")
    assert not hasattr(fc, "btn_advanced")
    # 验证多余的 db_label 哈希标识彻底不展示
    assert not hasattr(page, "db_label")
    # 验证红框内的图表切换下拉框已隐藏
    page.show_chart()
    assert not page.selector.isVisible()

    # 测试人员 Chips 标签同步
    assert fc.chip_person.isVisible()
    fc.sync_person("个人")
    assert "个人" in fc.chip_person.text()

    # 测试渠道选择与 Chips 联动
    fc.set_available_channels(["长桥", "盈立"])
    fc.combo_channel.setCurrentText("长桥")
    assert fc.chip_channel.isVisible()
    assert "长桥" in fc.chip_channel.text()

    # 重置筛选
    fc.reset_filters()
    assert fc.combo_channel.currentIndex() == 0
    assert not fc.chip_channel.isVisible()


def test_capital_canvas_hover_and_drill_events(qtbot):
    canvas = ChartCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(1000, 600)

    chart = Chart(
        18,
        "累计本金与总资产变化",
        "capital_assets",
        (
            Series("累计本金基柱", (("2026-01", D(50000)), ("2026-02", D(50000)))),
            Series("资产高于本金", (("2026-01", D(5000)), ("2026-02", D(8000)))),
            Series("资产低于本金", (("2026-01", D(0)), ("2026-02", D(0)))),
            Series("总资产", (("2026-01", D(55000)), ("2026-02", D(58000)))),
        ),
        "Excel兼容口径 · 币种 USD",
        link="month",
        extra={
            "monthly_investments": {"2026-01": D(50000), "2026-02": D(0)},
            "latest_profit_day": "2026-02-15",
        },
    )
    canvas.chart = chart
    canvas.show()
    qtbot.wait(10)

    canvas.repaint()
    assert len(canvas.categories_x) == 2

    # 模拟鼠标移动到第一个柱子上方
    x_pos = canvas.categories_x[0]
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(x_pos, 200),
        QPointF(x_pos, 200),
        QPointF(x_pos, 200),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    canvas.mouseMoveEvent(event)
    assert canvas.hover_index == 0

    # 模拟离开画布
    leave_ev = QEvent(QEvent.Type.Leave)
    canvas.leaveEvent(leave_ev)
    assert canvas.hover_index is None


def test_capital_drill_down_shows_daily_trend_and_returns(qtbot, demo_path):
    analytics = AnalyticsService(demo_path).calculate(QueryScope(date(2026, 1, 10)))
    page = ChartsPage()
    qtbot.addWidget(page)
    page.show()
    page.set_analytics(analytics)

    # 模拟用户点击月度柱状图（下钻到当月每天走势）
    target_month = "2026-01"
    page.drill_down_month(target_month)

    current_chart = page.canvas.chart
    assert current_chart is not None
    assert "2026-01" in current_chart.title
    assert current_chart.extra.get("is_daily_drill") is True

    # 验证数据点已转换为每日维度（日末股票市值与净投入）
    days_in_chart = [k for k, _ in current_chart.series[3].points]
    assert len(days_in_chart) > 0
    assert all(d.startswith("2026-01") for d in days_in_chart)

    # 模拟点击「✕ 返回月度总览」恢复
    page.restore_monthly_chart()
    restored_chart = page.canvas.chart
    assert restored_chart.extra.get("is_daily_drill", False) is False
    assert any(len(k) == 7 for k, _ in restored_chart.series[3].points)
