"""Comprehensive UI/UX and calculation tests for Daily Performance Analysis Dashboard."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
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
    assert "up" in kpis and "down" in kpis and "avg_profit" in kpis and "avg_loss" in kpis

    # Total trading days should be > 400
    assert "天" in kpis["days"].value_label.text()
    days_cnt = int(kpis["days"].value_label.text().replace(" 天", ""))
    assert days_cnt > 400

    # Average profit / loss sub label should not be empty
    assert kpis["avg_profit"].sub_label.text() != "—"
    assert kpis["avg_loss"].sub_label.text() != "—"

    # 4. Verify Trend Canvas
    assert len(page.trend_canvas.data_points) == days_cnt
    page.switch_trend_mode("cum")
    assert page.trend_canvas.mode == "cum"
    page.switch_trend_mode("daily")
    assert page.trend_canvas.mode == "daily"

    # 5. Verify Table Tab 0 (按日期汇总)
    assert page.tab_bar.currentIndex() == 0
    assert page.table_model.columnCount() == 11
    # STRICT REQUIREMENT: NO "操作", NO "主要贡献股票", NO "累计收益率", with buy/sell/net/up_stocks/down_stocks
    assert "操作" not in page.table_model.columns
    assert "主要贡献股票" not in page.table_model.columns
    assert "累计收益率" not in page.table_model.columns
    assert "当日买入" in page.table_model.columns
    assert "当日卖出" in page.table_model.columns
    assert "当日净投入" in page.table_model.columns
    assert "上涨股票数" in page.table_model.columns
    assert "下跌股票数" in page.table_model.columns
    assert len(page.table_model.all_rows) == days_cnt

    # 6. Verify Table Tab 1 (按渠道明细: 与按日期汇总镜像对称)
    page.tab_bar.setCurrentIndex(1)
    page.on_table_tab_changed(1)
    assert page.table_model.columnCount() == 12
    assert "操作" not in page.table_model.columns
    assert "券商渠道" in page.table_model.columns
    assert "累计收益率" not in page.table_model.columns
    assert "当日买入" in page.table_model.columns
    assert "当日卖出" in page.table_model.columns
    assert "当日净投入" in page.table_model.columns
    assert "累计收益" in page.table_model.columns
    assert "上涨股票数" in page.table_model.columns
    assert "下跌股票数" in page.table_model.columns
    # Verify broker channels are displayed in Chinese
    broker_vals = [r[1] for r in page.table_model.all_rows]
    assert any("长桥" in str(b) or "第一证券" in str(b) or "嘉信" in str(b) for b in broker_vals)
    assert not any("LONGBRIDGE" == str(b) for b in broker_vals)

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

    # 11. Verify person combo population & switching (including "他人")
    assert page.person_combo.count() > 1
    person_texts = [page.person_combo.itemText(i) for i in range(page.person_combo.count())]
    assert "全部人员" in person_texts
    assert "他人" in person_texts
    # Switch to "他人"
    others_idx = page.person_combo.findData("OTHERS")
    assert others_idx >= 0
    page.person_combo.setCurrentIndex(others_idx)
    assert page.selected_person_id == "OTHERS"

    page.sync_person_from_main(1)
    assert page.selected_person_id == 1

    # 12. Verify open_date_drawer syncs right-bottom donut distribution and trend selection
    page.open_date_drawer("2026-11-05")
    assert page.selected_date == "2026-11-05"
    assert "2026-11-05" in page.dist_title.text()
    assert "2026-11-05" in page.drawer_title.text()
    assert page.trend_canvas.selected_date == "2026-11-05"

    # 13. Verify Header Click Sorting (first click: ascending, second click: descending)
    page.tab_bar.setCurrentIndex(0)
    page.on_table_tab_changed(0)
    # Click Date column header (col 0): first click -> ascending (earliest first)
    page.on_header_section_clicked(0)
    assert page.table_model.sort_column == 0
    assert page.table_model.sort_order == Qt.SortOrder.AscendingOrder
    assert "▲" in page.table_model.headerData(0, Qt.Orientation.Horizontal)
    first_date_asc = page.table_model.rows[0][0]
    last_date_asc = page.table_model.all_rows[-1][0]
    assert first_date_asc <= last_date_asc

    # Second click -> descending (latest first)
    page.on_header_section_clicked(0)
    assert page.table_model.sort_order == Qt.SortOrder.DescendingOrder
    assert "▼" in page.table_model.headerData(0, Qt.Orientation.Horizontal)
    first_date_desc = page.table_model.rows[0][0]
    assert first_date_desc >= first_date_asc

    # Click col 6 (当日收益): first click -> ascending (worst loss first)
    page.on_header_section_clicked(6)
    assert page.table_model.sort_column == 6
    assert page.table_model.sort_order == Qt.SortOrder.AscendingOrder
    pnl_first_asc = float(page.table_model.rows[0][6].replace("$", "").replace(",", "").replace("+", ""))
    pnl_last_asc = float(page.table_model.all_rows[-1][6].replace("$", "").replace(",", "").replace("+", ""))
    assert pnl_first_asc <= pnl_last_asc

    # Click col 6 again -> descending (best profit first)
    page.on_header_section_clicked(6)
    assert page.table_model.sort_order == Qt.SortOrder.DescendingOrder
    pnl_first_desc = float(page.table_model.rows[0][6].replace("$", "").replace(",", "").replace("+", ""))
    assert pnl_first_desc >= pnl_first_asc

    # 14. Verify Date Navigation Buttons (◀ 前一日 / 后一日 ▶)
    assert hasattr(page, "prev_date_btn") and hasattr(page, "next_date_btn")
    page.select_date("2026-11-06")
    page.update_date_nav_buttons()
    # At latest date: next button should be disabled, prev button enabled
    assert not page.next_date_btn.isEnabled()
    assert page.prev_date_btn.isEnabled()

    # Click prev date -> navigate to 2026-11-05
    page.prev_date_btn.click()
    assert page.selected_date == "2026-11-05"
    assert "2026-11-05" in page.dist_title.text()
    assert page.trend_canvas.selected_date == "2026-11-05"
    assert page.next_date_btn.isEnabled()

    # Click next date -> navigate back to 2026-11-06
    page.next_date_btn.click()
    assert page.selected_date == "2026-11-06"
    assert not page.next_date_btn.isEnabled()

    # 15. Verify Broker Overview Retention & Tab 1 Switching
    total_overview_brokers = page.broker_list_layout.count()
    assert total_overview_brokers >= 3  # Multiple brokers: Longbridge, Schwab, Firstrade, BBAE...
    
    # Click Longbridge in broker overview
    page.filter_by_broker("LONGBRIDGE")
    assert page.tab_bar.currentIndex() == 1  # Switched to Tab 1 (按渠道明细)
    assert page.selected_broker == "LONGBRIDGE"
    # CRITICAL: Overview list must STILL contain all brokers, NOT collapsed to only Longbridge!
    assert page.broker_list_layout.count() == total_overview_brokers
    # All displayed rows in table Tab 1 belong to Longbridge
    assert all("长桥" in str(r[1]) for r in page.table_model.all_rows)

    # Click Longbridge again -> toggle off (back to all brokers)
    page.filter_by_broker("LONGBRIDGE")
    assert page.selected_broker == ""
    assert page.broker_list_layout.count() == total_overview_brokers

    # 16. Verify Trend Canvas height allocation & legend removal
    trend_card_widget = page.trend_canvas.parent()
    assert trend_card_widget.height() >= 250
    assert not hasattr(page, "trend_legend_lbl")

    # 17. Verify Flat stocks button removed from donut section
    assert not hasattr(page, "donut_f_btn")
    assert hasattr(page, "donut_p_btn") and hasattr(page, "donut_l_btn")

