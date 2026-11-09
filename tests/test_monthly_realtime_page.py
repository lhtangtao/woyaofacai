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

    # 验证卡片数据同步更新
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

