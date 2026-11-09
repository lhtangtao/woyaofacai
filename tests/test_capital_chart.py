from datetime import date
from decimal import Decimal as D

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domain.models import QueryScope
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

    # 构造 Chart 18：2026-01 盈利（高于本金），2026-02 亏损（低于本金）
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
    # 最新月份为 2026-02：股票市值 $45,000，累计净买入 $50,000，差额 -$5,000
    assert "$45,000" in page.card_market_val.lbl_value.text()
    assert "$50,000" in page.card_net_buy.lbl_value.text()
    assert "-$5,000" in page.card_diff.lbl_value.text()
    assert "↘" in page.card_diff.badge.text()
    assert "-10.0%" in page.card_diff.badge.text()
    assert len(page.card_market_val.sparkline.points_data) == 2


def test_capital_filter_card_interactions_and_chips(qtbot):
    page = ChartsPage()
    qtbot.addWidget(page)
    page.show()

    # 测试筛选卡片
    fc = page.filter_card
    assert fc.chip_person.isVisible()
    fc.combo_person.setCurrentText("家庭")
    assert "家庭" in fc.chip_person.text()

    # 重置筛选
    fc.reset_filters()
    assert fc.combo_person.currentText() == "个人"
    assert fc.combo_channel.currentIndex() == 0


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

    # 触发绘制产生 categories_x
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

    # 测试点击下钻信号
    drill_signals = []
    canvas.selected.connect(lambda link, key: drill_signals.append((link, key)))
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        canvas.hits[0][0].center(),
        canvas.hits[0][0].center(),
        canvas.hits[0][0].center(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    canvas.mousePressEvent(press_event)
    assert len(drill_signals) == 1
    assert drill_signals[0] == ("month", "2026-01")


def test_same_month_round_trip_trades_included_in_net_investments(demo_path):
    analytics = AnalyticsService(demo_path).calculate(QueryScope(date(2026, 1, 10)))
    chart_18 = analytics.charts[17]
    assert chart_18.kind == "capital_assets"
    # 验证当月净买入字典不为空
    assert len(chart_18.extra["monthly_investments"]) > 0
