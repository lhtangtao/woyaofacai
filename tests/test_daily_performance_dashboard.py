"""Comprehensive UI/UX and calculation tests for Daily Performance Analysis Dashboard."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication

from portfolio.domain.models import QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.ui.daily_performance_page import DailyPerformancePage


def test_daily_performance_dashboard_full_parity(qtbot):
    db_path = Path("数据库/money.sqlite3")
    if not db_path.exists():
        pytest.skip("Local test database not found")

    svc = AnalyticsService(str(db_path))
    res = svc.calculate(QueryScope(as_of=date(2026, 11, 6)))

    page = DailyPerformancePage()
    qtbot.addWidget(page)
    page.resize(1200, 800)
    page.show()

    # 1. Update data with real analytics
    page.update_data(res)

    # 2. Verify Data As of badge
    assert "2026-11-06" in page.as_of_badge.text()

    # 3. Verify 7 KPI Cards
    kpis = page.kpi_cards
    assert "total" in kpis and "days" in kpis and "avg" in kpis
    assert "up" in kpis and "down" in kpis and "max_profit" in kpis and "max_loss" in kpis

    # Total trading days should be > 400
    assert "天" in kpis["days"].value_label.text()
    days_cnt = int(kpis["days"].value_label.text().replace(" 天", ""))
    assert days_cnt > 400

    # Max profit / loss date should not be empty
    assert kpis["max_profit"].sub_label.text() != "—"
    assert kpis["max_loss"].sub_label.text() != "—"

    # 4. Verify Trend Canvas
    assert len(page.trend_canvas.data_points) == days_cnt
    page.switch_trend_mode("cum")
    assert page.trend_canvas.mode == "cum"
    page.switch_trend_mode("daily")
    assert page.trend_canvas.mode == "daily"

    # 5. Verify Table Tab 0 (按日期汇总)
    assert page.tab_bar.currentIndex() == 0
    assert page.table_model.columnCount() == 10
    # STRICT REQUIREMENT: NO "操作" column, NO "主要贡献股票", with buy/sell/net
    assert "操作" not in page.table_model.columns
    assert "主要贡献股票" not in page.table_model.columns
    assert "当日买入" in page.table_model.columns
    assert "当日卖出" in page.table_model.columns
    assert "当日净投入" in page.table_model.columns
    assert len(page.table_model.all_rows) == days_cnt

    # 6. Verify Table Tab 1 (按渠道明细: 与按日期汇总镜像对称)
    page.tab_bar.setCurrentIndex(1)
    page.on_table_tab_changed(1)
    assert page.table_model.columnCount() == 11
    assert "操作" not in page.table_model.columns
    assert "券商渠道" in page.table_model.columns
    assert "当日买入" in page.table_model.columns
    assert "当日卖出" in page.table_model.columns
    assert "当日净投入" in page.table_model.columns
    assert "累计收益" in page.table_model.columns

    # 7. Verify Table Tab 2 (按股票明细)
    page.tab_bar.setCurrentIndex(2)
    page.on_table_tab_changed(2)
    assert page.table_model.columnCount() == 10
    assert "操作" not in page.table_model.columns
    assert "Ticker" in page.table_model.columns

    # 8. Verify Selected Date Donut Distribution & Drawer
    page.select_date("2026-11-06")
    assert "2026-11-06" in page.dist_title.text()
    assert page.donut_canvas.total_profit != 0
    assert page.drawer_frame.isVisible()
    assert page.drawer_overlay.isVisible()
    assert "2026-11-06" in page.drawer_title.text()

    # 9. Verify Drawer sorting (descending by profit) & rate display
    assert page.drawer_list_layout.count() > 0
    first_item = page.drawer_list_layout.itemAt(0).widget()
    assert first_item is not None

    # 10. Click overlay to close drawer
    page.drawer_overlay.mousePressEvent(None)
    assert not page.drawer_frame.isVisible()
    assert not page.drawer_overlay.isVisible()

    # 11. Verify sync_person_from_main
    page.sync_person_from_main(1)
    assert page.selected_person_id == 1
