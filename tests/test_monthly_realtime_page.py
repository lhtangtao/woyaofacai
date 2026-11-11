import calendar
from datetime import date, datetime
from decimal import Decimal
import pytest
from zoneinfo import ZoneInfo

from portfolio.domain.live_quotes import UTC
from portfolio.services.charts_service import Chart, Series
from portfolio.ui.monthly_realtime_page import (
    MonthlyRealtimePnlPage,
    calculate_realtime_metrics,
)


def test_metrics_empty_points():
    """无有效数据时返回全空字典与提示"""
    m = calculate_realtime_metrics([], is_year=False, period_str="2026-11")
    assert m["valid_count"] == 0
    assert m["start_val"] is None
    assert m["latest_val"] is None
    assert m["delta_val"] is None
    assert m["range_val"] is None
    assert m["max_val"] is None
    assert m["min_val"] is None
    assert m["drawdown_val"] is None
    assert m["status_text"] == "无有效数据"


def test_metrics_calculation_and_drawdown_sequence():
    """验证各项核心指标与最大回撤的时序算法"""
    # 构造时序数据：
    # 11-01 10:00: -2000 (起点)
    # 11-02 10:00: +1000 (最高点 peak)
    # 11-03 10:00: -3000 (随后的谷底 trough, 回撤 = -3000 - (+1000) = -4000)
    # 11-04 10:00: -5000 (进一步跌到更深谷底, 回撤 = -5000 - (+1000) = -6000)
    # 11-05 10:00: -1000 (反弹)
    # 11-06 10:00: -1500 (最新点 latest)
    points = [
        ("2026-11-01T02:00:00Z", Decimal("-2000.00")),
        ("2026-11-02T02:00:00Z", Decimal("1000.00")),
        ("2026-11-03T02:00:00Z", Decimal("-3000.00")),
        ("2026-11-04T02:00:00Z", Decimal("-5000.00")),
        ("2026-11-05T02:00:00Z", Decimal("-1000.00")),
        ("2026-11-06T02:00:00Z", Decimal("-1500.00")),
        ("2026-11-07T02:00:00Z", None),  # 缺失点，不应补零
    ]

    m = calculate_realtime_metrics(points, is_year=False, period_str="2026-11")

    # 1. 月初起点
    assert m["valid_count"] == 6
    assert m["start_val"] == Decimal("-2000.00")
    assert "2026-11-01" in m["start_time_text"]

    # 2. 当前最新
    assert m["latest_val"] == Decimal("-1500.00")
    assert "11-06" in m["latest_time_text"]

    # 3. 本月变化 = -1500 - (-2000) = +500
    assert m["delta_val"] == Decimal("500.00")

    # 4. 极值与波动区间
    assert m["max_val"] == Decimal("1000.00")
    assert m["min_val"] == Decimal("-5000.00")
    # 波动区间 = max - min = 1000 - (-5000) = 6000
    assert m["range_val"] == Decimal("6000.00")
    assert "+1,000.00 ～ -5,000.00" in m["range_sub_text"]

    # 5. 最大回撤必须严格时序：峰值先于谷底
    # 历史峰值是 1000.00，随后跌到 -5000.00，最大回撤 = -5000 - 1000 = -6000
    assert m["drawdown_val"] == Decimal("-6000.00")
    assert "+1,000.00 至 -5,000.00" in m["drawdown_sub_text"]


def test_drawdown_monotonic_increase():
    """一路单调上涨时，最大回撤为 0"""
    points = [
        ("2026-11-01T02:00:00Z", Decimal("100.00")),
        ("2026-11-02T02:00:00Z", Decimal("200.00")),
        ("2026-11-03T02:00:00Z", Decimal("500.00")),
    ]
    m = calculate_realtime_metrics(points, is_year=False, period_str="2026-11")
    assert m["drawdown_val"] == Decimal("0")
    assert m["drawdown_sub_text"] == "(无回撤)"


def test_monthly_realtime_page_ui_layout_and_cards(qtbot):
    """验证 UI 页面布局：4张卡片、右侧分析面板、无底部冗余说明、已选Chips"""
    page = MonthlyRealtimePnlPage()
    qtbot.addWidget(page)
    page.show()

    # 4 张指标卡片
    assert hasattr(page, "kpi_start")
    assert hasattr(page, "kpi_latest")
    assert hasattr(page, "kpi_delta")
    assert hasattr(page, "kpi_range")

    # 卡片初始状态
    assert page.kpi_start.val_lbl.text() == "—"
    assert page.kpi_latest.val_lbl.text() == "—"

    # 右侧分析卡片
    assert hasattr(page, "right_panel")
    assert page.right_panel.range_title_lbl.text() == "📅 本月区间"

    # 底部精简状态栏
    assert hasattr(page, "bottom_bar")
    assert hasattr(page, "bottom_status_lbl")
    # 旧大段说明不可见
    assert not page.notes_label.isVisible()

    # 模拟包含上月历史收益的 analytics
    class MockRow:
        def __init__(self, values):
            self.values = values

    class MockReport:
        def __init__(self, rows):
            self.rows = rows

    mock_analytics = type("Analytics", (), {
        "reports": {
            "每月持仓收益": MockReport([
                MockRow(["2026-10", "LONGBRIDGE", "美股", "AAPL", "已清仓", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, Decimal("-2346.18")]),
            ])
        }
    })()
    page.analytics = mock_analytics
    page.month_combo.addItem("2026-11")
    page.month_combo.setCurrentText("2026-11")

    # 模拟数据填充（相对当月初的变动量）
    points = [
        ("2026-11-01T02:00:00Z", Decimal("0.00")),
        ("2026-11-05T06:20:00Z", Decimal("-14635.38")),
        ("2026-11-09T08:50:00Z", Decimal("-11205.38")),
    ]
    chart = Chart(
        id=1,
        title="2026-11 累计盈亏走势",
        kind="line",
        series=(Series("累计盈亏", tuple(points)),),
        note="",
    )
    page._display_shared_chart(chart, {"baseline_state": "READY"}, year=False)

    # 1. 默认模式为起点为0（仅计算当月收益）
    assert page.zero_base is True
    assert "0.00" in page.kpi_start.val_lbl.text()
    assert "-11,205.38" in page.kpi_latest.val_lbl.text()
    assert "-11,205.38" in page.kpi_delta.val_lbl.text()
    assert "-11,205.38" in page.bottom_status_lbl.text()

    # 2. 切换为累计模式（含期初 -2,346.18）：最新累计为 -13,551.56
    page.set_zero_base(False)
    assert page.zero_base is False
    assert "-2,346.18" in page.kpi_start.val_lbl.text()
    assert "-13,551.56" in page.kpi_latest.val_lbl.text()
    # 本月变化: -13551.56 - (-2346.18) = -11205.38
    assert "-11,205.38" in page.kpi_delta.val_lbl.text()
    # 底部状态栏展示精简信息
    assert "有效采样 3" in page.bottom_status_lbl.text()
    assert "-13,551.56" in page.bottom_status_lbl.text()


def test_annual_tab_switch_adapts_kpi_titles(qtbot):
    """切换到年度走势详情 Tab 时，卡片与右侧面板标题自动适配为年初/本年"""
    page = MonthlyRealtimePnlPage()
    qtbot.addWidget(page)
    page.show()

    # 切换至年度 Tab (索引 1)
    page.tab_bar.setCurrentIndex(1)

    assert "年初累计盈亏" in page.kpi_start.title_lbl.text()
    assert "本年变化" in page.kpi_delta.title_lbl.text()
    assert "年内波动区间" in page.kpi_range.title_lbl.text()
    assert page.right_panel.range_title_lbl.text() == "📅 本年区间"


def test_embedded_mode_preserves_today_view_unaffected(qtbot):
    """内嵌在今日盈亏页时（set_parent_filters），隐藏卡片与侧栏，完全不影响今日盈亏"""
    page = MonthlyRealtimePnlPage(embedded=True)
    qtbot.addWidget(page)
    page.set_parent_filters("LONGBRIDGE", "AAPL")

    # 在内嵌模式下，顶部控制栏、Chips、4个指标卡片、右侧面板和底部栏均被完全隐藏
    assert not page.top_bar_widget.isVisible()
    assert not page.chips_bar.isVisible()
    assert not page.kpi_container.isVisible()
    assert not page.right_panel.isVisible()
    assert not page.bottom_bar.isVisible()


def test_negative_start_value_no_misleading_percentage():
    """月初为负数时不计算和展示误导性百分比，仅展示金额"""
    points = [
        ("2026-11-01T02:00:00Z", Decimal("-2346.18")),
        ("2026-11-09T08:50:00Z", Decimal("-13551.56")),
    ]
    m = calculate_realtime_metrics(points, is_year=False, period_str="2026-11")
    # 差额 = -13551.56 - (-2346.18) = -11205.38
    assert m["delta_val"] == Decimal("-11205.38")
    # 辅助文字为口径说明，不含误导性百分比符号
    assert "%" not in m["delta_sub_text"]


def test_filters_reset_and_chips_sync(qtbot):
    """筛选重置与 Chips 状态联动"""
    page = MonthlyRealtimePnlPage()
    qtbot.addWidget(page)
    page.show()

    # 改变筛选
    page.stock_combo.addItem("NVDA", "NVDA")
    page.stock_combo.setCurrentIndex(page.stock_combo.findText("NVDA"))
    page.search_edit.setText("NVDA")
    page._sync_chips()
    assert "NVDA" in page.chip_stock.text()

    # 点击重置
    page.reset_filters()
    assert page.stock_combo.currentIndex() == 0
    assert page.search_edit.text() == ""


def test_hover_dot_lies_exactly_on_curve_point(qtbot):
    """验证鼠标悬停时圆点 Y 坐标精确落在曲线顶点上，而非画布垂直中点"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent, QPixmap

    page = MonthlyRealtimePnlPage()
    qtbot.addWidget(page)
    page.resize(900, 600)
    page.show()

    points = [
        ("2026-11-01T02:00:00Z", Decimal("0.00")),
        ("2026-11-05T06:20:00Z", Decimal("12000.00")),
        ("2026-11-09T08:50:00Z", Decimal("-8000.00")),
    ]
    chart = Chart(
        id=1,
        title="",
        kind="line",
        series=(Series("", tuple(points)),),
        note="",
    )
    page._display_shared_chart(chart, {"baseline_state": "READY"}, year=False)
    page.canvas.resize(800, 420)
    # 触发一次离屏渲染以生成 hits 与 point_coords
    pixmap = QPixmap(page.canvas.size())
    page.canvas.render(pixmap)

    target_key = "2026-11-05T06:20:00Z"
    assert target_key in page.canvas.point_coords
    exact_pt = page.canvas.point_coords[target_key]
    hit_rect = next(r for r, k, _, _ in page.canvas.hits if k == target_key)
    # 最高点 (12000) 的真实 Y 坐标应当显著高于画布垂直中点 hit_rect.center().y()
    assert exact_pt.y() < hit_rect.center().y() - 30

    move_ev = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(exact_pt.x(), hit_rect.center().y()),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    page.canvas.mouseMoveEvent(move_ev)
    assert page.canvas.hover_point is not None
    assert abs(page.canvas.hover_point["x"] - exact_pt.x()) < 0.01
    assert abs(page.canvas.hover_point["y"] - exact_pt.y()) < 0.01



def test_available_years_and_months_include_2025(qtbot):
    """验证年度/月度走势页左上角年份与月份选择器默认包含 2025 与 2026 供切换。"""
    page = MonthlyRealtimePnlPage()
    qtbot.addWidget(page)
    page.tab_bar.setCurrentIndex(1)
    years = [page.year_combo.itemText(i) for i in range(page.year_combo.count())]
    assert "2026" in years
    assert "2025" in years

    page.tab_bar.setCurrentIndex(0)
    months = [page.month_combo.itemText(i) for i in range(page.month_combo.count())]
    assert any(m.startswith("2025-") for m in months)
    assert any(m.startswith("2026-") for m in months)

def test_total_return_tab_layout_and_metrics(qtbot):
    """验证年度走势详情右侧新增第 3 个「总收益走势」标签与全周期统计指标格式。"""
    page = MonthlyRealtimePnlPage()
    qtbot.addWidget(page)
    assert page.tab_bar.count() == 3
    assert page.tab_bar.tabText(0) == "月度走势详情"
    assert page.tab_bar.tabText(1) == "年度走势详情"
    assert page.tab_bar.tabText(2) == "总收益走势"

    page.tab_bar.setCurrentIndex(2)
    assert not page.month_combo.isVisible()
    assert not page.year_combo.isVisible()
    assert not page.baseline_switch_frame.isVisible()

    points = [
        ("2025-01-16T02:00:00Z", Decimal("0.00")),
        ("2025-11-05T06:20:00Z", Decimal("15000.00")),
        ("2026-11-11T08:50:00Z", Decimal("-8000.00")),
    ]
    chart = Chart(
        id=0,
        title="2025-01-16 ~ 2026-11-11 累计总收益走势",
        kind="line",
        series=(Series("累计盈亏", tuple(points)),),
        note="",
        calendar_axis="all",
    )
    page._shared_period = "2025-01-16 ~ 2026-11-11"
    page._display_shared_chart(chart, {"baseline_state": "READY"}, year=False, is_all=True)

    assert page.kpi_start.title_lbl.text().startswith("统计起点盈亏")
    assert page.kpi_latest.title_lbl.text().startswith("当前累计总收益")
    assert page.kpi_delta.title_lbl.text().startswith("累计总变化")
    assert page.kpi_range.title_lbl.text().startswith("全周期波动区间")
    assert page.right_panel.range_title_lbl.text() == "📅 全周期区间"
    assert "0.00" in page.kpi_start.val_lbl.text()
    assert "-8,000.00" in page.kpi_latest.val_lbl.text()
    assert "2025-01-16" in page.kpi_start.sub_lbl.text()
    assert "2025-11-05" in page.right_panel.max_time_lbl.text()
    assert "2026-11-11" in page.right_panel.min_time_lbl.text()
