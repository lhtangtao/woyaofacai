from datetime import date
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from portfolio.domain.models import QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.holdings_management_service import HoldingsManagementService
from portfolio.services.open_holdings_analytics_service import (
    build_open_holdings_dashboard,
)
from portfolio.ui.holdings_page import OPEN_HOLDINGS_COLUMNS, HoldingsPage


REAL_DB_PATH = Path("/Users/tangtao/money3/数据库/money.sqlite3")


@pytest.fixture(scope="module")
def real_analytics():
    if not REAL_DB_PATH.exists():
        pytest.skip("真实数据库不存在")
    svc = AnalyticsService(REAL_DB_PATH)
    analytics = svc.calculate(QueryScope(as_of=date(2026, 11, 11)))
    return svc, analytics


def _make_host(qtbot, svc, analytics):
    host = QWidget()
    host.service = svc
    host.analytics_service = svc
    host.accounts = SimpleNamespace(selected_ids=lambda: None, current_broker_name=lambda: "")
    host.quote_store = None
    host.info = SimpleNamespace(
        persons=[
            {"id": 1, "name": "个人", "enabled": True},
            {"id": 2, "name": "老婆", "enabled": True},
        ],
        instruments=(),
    )
    qtbot.addWidget(host)
    page = HoldingsPage(host)
    page.resize(1440, 900)
    host.show()
    page.show()
    page.set_analytics(analytics)
    page.set_view("detail", status="持有中")
    return host, page


def test_1_and_2_real_db_all_persons_and_single_person_stats(real_analytics, qtbot):
    """验收 1 & 2：使用真实美股数据库验证全部人员持仓统计与单个人员持仓统计，并与现有美股计算口径完全一致。"""
    svc, analytics = real_analytics
    host, page = _make_host(qtbot, svc, analytics)

    # 1. 全部人员持仓统计
    page.sync_person_from_main(None)
    raw_all = HoldingsManagementService.get_holdings_detail_rows(
        analytics, status="持有中", person_id=None, db_path=REAL_DB_PATH
    )
    payload_all = build_open_holdings_dashboard(
        analytics, raw_all, db_path=REAL_DB_PATH, person_id=None
    )
    cards_all = payload_all["cards"]

    expected_mv = sum(D(str(r["market_value"] or 0)) for r in raw_all)
    expected_cost = sum(D(str(r["cost_amount"] or 0)) for r in raw_all)
    expected_pnl = sum(D(str(r["total_pnl"] or 0)) for r in raw_all)
    expected_tickers = len({str(r["ticker"]).upper() for r in raw_all if r.get("ticker")})

    assert abs(cards_all["total_market_value"] - float(expected_mv)) < 0.01
    assert abs(cards_all["total_cost_amount"] - float(expected_cost)) < 0.01
    assert abs(cards_all["floating_pnl"] - float(expected_pnl)) < 0.01
    assert abs(cards_all["return_rate"] - float(expected_pnl / expected_cost)) < 1e-6
    assert cards_all["stock_count"] == len(raw_all)
    assert cards_all["unique_ticker_count"] == expected_tickers
    assert 0 < cards_all["top5_concentration"] <= 1.0
    assert len(page._open_all_rows) == len(raw_all)

    # 2. 单个人员（个人 person_id=1 与 华 person_id=2）全局人员筛选联动
    for pid, expected_name in ((1, "个人"), (2, "华")):
        page.sync_person_from_main(pid)
        assert len(page._open_all_rows) > 0
        person_names = {r.values[0] for r in page._open_all_rows}
        assert person_names == {expected_name}
        # 选定单个人员时，各人员持仓情况汇总表隐藏
        assert page.open_person_card.isHidden()

        raw_p = HoldingsManagementService.get_holdings_detail_rows(
            analytics, status="持有中", person_id=pid, db_path=REAL_DB_PATH
        )
        p_mv = sum(D(str(r["market_value"] or 0)) for r in raw_p)
        assert f"{float(p_mv):,.2f}" == page.open_kpi_bar.card_mv.val_lbl.text()

    # 切换至「他人」(OTHERS) 时，展示各人员持仓情况汇总表
    page.sync_person_from_main("OTHERS")
    assert not page.open_person_card.isHidden()
    assert page.open_person_model.rowCount() >= 2  # 至少包含他人人员行 + 合计行

    # 恢复全部人员时，同样展示各人员持仓情况汇总表
    page.sync_person_from_main(None)
    assert len(page._open_all_rows) == len(raw_all)
    assert not page.open_person_card.isHidden()
    assert page.open_person_model.rowCount() >= 3  # 包含多个人行 + 合计行


def test_3_and_4_real_db_broker_and_industry_filtering(real_analytics, qtbot):
    """验收 3 & 4：使用真实数据库验证券商筛选与类型筛选联动统计卡片、图表与列表。"""
    svc, analytics = real_analytics
    _, page = _make_host(qtbot, svc, analytics)
    total_all = len(page._open_all_rows)
    assert total_all > 0

    # 3. 券商筛选（长桥 / BBAE / 嘉信 / 第一证券）
    for broker_name in ("长桥", "BBAE", "嘉信", "第一证券"):
        idx = page.open_broker_combo.findData(broker_name)
        assert idx > 0, f"券商下拉框应动态包含 {broker_name}"
        page.open_broker_combo.setCurrentIndex(idx)
        assert len(page._open_all_rows) > 0
        assert len(page._open_all_rows) < total_all
        assert all(r.values[1] == broker_name for r in page._open_all_rows)
        assert not page.active_broker_chip.isHidden()

    page.open_broker_combo.setCurrentIndex(0)
    assert len(page._open_all_rows) == total_all
    assert page.active_broker_chip.isHidden()

    # 4. 类型筛选（真实分类：大盘 / 正股 / 杠杆）
    for ind_name in ("大盘", "正股", "杠杆"):
        idx = page.open_industry_combo.findData(ind_name)
        assert idx > 0, f"类型下拉框应包含真实分类 {ind_name}"
        page.open_industry_combo.setCurrentIndex(idx)
        assert len(page._open_all_rows) > 0
        assert len(page._open_all_rows) < total_all
        assert all(r.values[3] == ind_name for r in page._open_all_rows)
        assert not page.active_industry_chip.isHidden()
        assert "类型:" in page.active_industry_chip.text()

    page.open_industry_combo.setCurrentIndex(0)
    assert len(page._open_all_rows) == total_all
    assert page.active_industry_chip.isHidden()


def test_5_real_db_profit_and_loss_toggle(real_analytics, qtbot):
    """验收 5：盈利/亏损切换只改变列表对应记录的展示范围，统计卡片保持当前筛选口径不变。"""
    svc, analytics = real_analytics
    _, page = _make_host(qtbot, svc, analytics)

    mv_card_before = page.open_kpi_bar.card_mv.val_lbl.text()
    total_rows = len(page._open_all_rows)

    # 切换到盈利
    page._on_open_pnl_tab_clicked("profit")
    profit_rows = list(page._open_all_rows)
    assert len(profit_rows) > 0
    assert all(D(str(r.values[10])) > 0 for r in profit_rows)
    # 盈利股票当前价 >= 持仓均价，离回本需涨为 0（显示为「已回本」）
    assert all(D(str(r.values[7] or 0)) == 0 for r in profit_rows)
    assert page.detail_model.data(page.detail_model.index(0, 7), Qt.ItemDataRole.DisplayRole) == "已回本"
    assert page.open_kpi_bar.card_mv.val_lbl.text() == mv_card_before

    # 切换到亏损
    page._on_open_pnl_tab_clicked("loss")
    loss_rows = list(page._open_all_rows)
    assert len(loss_rows) > 0
    assert all(D(str(r.values[10])) < 0 for r in loss_rows)
    # 亏损股票当前价 < 持仓均价，离回本需涨 > 0（显示为 +X.XX%）
    assert all(D(str(r.values[7] or 0)) > 0 for r in loss_rows)
    breakeven_text = page.detail_model.data(page.detail_model.index(0, 7), Qt.ItemDataRole.DisplayRole)
    assert breakeven_text.startswith("+") and breakeven_text.endswith("%")
    assert page.open_kpi_bar.card_mv.val_lbl.text() == mv_card_before
    assert len(profit_rows) + len(loss_rows) <= total_rows

    # 切回全部
    page._on_open_pnl_tab_clicked("all")
    assert len(page._open_all_rows) == total_rows


def test_6_chart_click_linkage_and_mv_trend_incomplete_flag(real_analytics, qtbot):
    """验收 6：点击券商分布图或类型分布图联动筛选持仓列表，且筛选条件可组合；市值趋势图真实反映历史快照及缺失提示。"""
    svc, analytics = real_analytics
    _, page = _make_host(qtbot, svc, analytics)
    total_rows = len(page._open_all_rows)

    # 点击券商环形图选中 "长桥"
    page.open_charts_row.broker_donut.item_clicked.emit("长桥")
    assert page.open_broker_combo.currentData() == "长桥"
    longbridge_count = len(page._open_all_rows)
    assert 0 < longbridge_count < total_rows
    assert all(r.values[1] == "长桥" for r in page._open_all_rows)

    # 组合点击类型环形图选中 "杠杆"
    page.open_charts_row.industry_donut.item_clicked.emit("杠杆")
    assert page._open_industry == "杠杆"
    combined_count = len(page._open_all_rows)
    assert 0 < combined_count <= longbridge_count
    assert all(r.values[1] == "长桥" and r.values[3] == "杠杆" for r in page._open_all_rows)

    # 再次点击取消筛选
    page.open_charts_row.industry_donut.item_clicked.emit("杠杆")
    assert page._open_industry == ""
    page.open_charts_row.broker_donut.item_clicked.emit("长桥")
    assert page.open_broker_combo.currentData() == ""
    assert len(page._open_all_rows) == total_rows

    # 验证市值趋势图切换（近1月/近3月/近6月/今年/全部）及不补造缺失快照数据
    for win in ("1m", "3m", "6m", "ytd", "all"):
        page._on_open_trend_window_changed(win)
        series = page.open_charts_row.trend_chart.points
        assert len(series) > 0
        # 任何交易日的历史市值点均大于 0，绝不将缺失收盘价的交易日补造为 0.0
        assert all(pt["market_value"] > 0 for pt in series)
    if not page.open_charts_row.incomplete_lbl.isHidden():
        assert "缺少完整收盘价快照" in page.open_charts_row.incomplete_lbl.text()


def test_7_and_8_person_column_preserved_and_drawer_open_close(real_analytics, qtbot):
    """验收 7 & 8：保留归属人员字段、不重复展示人员筛选控件，点击股票展开详情抽屉且点击外部自动收起。"""
    svc, analytics = real_analytics
    _, page = _make_host(qtbot, svc, analytics)

    # 7. 持仓列表第一列必须为「归属人员」，移除冗余「股票名称」「股票类型」，「行业」改为「类型」，当前价格后新增「离回本需涨」
    assert page.detail_model.columns == OPEN_HOLDINGS_COLUMNS
    assert page.detail_model.columns[0] == "归属人员"
    assert "股票名称" not in OPEN_HOLDINGS_COLUMNS
    assert "股票类型" not in OPEN_HOLDINGS_COLUMNS
    assert page.detail_model.columns[3] == "类型"
    assert page.detail_model.columns[6] == "当前价格"
    assert page.detail_model.columns[7] == "离回本需涨"
    assert page.open_type_combo.isHidden()
    assert page.closed_person_combo.isHidden()
    assert page.closed_person_lbl.isHidden()
    first_person = page.detail_model.data(page.detail_model.index(0, 0), Qt.ItemDataRole.DisplayRole)
    assert first_person in ("个人", "华", "辉", "波")

    # 8. 点击第一行打开右侧抽屉，点击抽屉外部区域自动收起
    page._on_detail_row_clicked(page.detail_model.index(0, 2))
    assert not page.drawer.isHidden()
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(5, 5),
        QPointF(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(page.open_filter_bar, ev)
    assert page.drawer.isHidden()


def test_9_us_core_calculation_results_unchanged(real_analytics):
    """验收 9：原有美股计算结果不发生变化。"""
    _, analytics = real_analytics
    person_report = analytics.reports["人员资产"]
    holdings_report = analytics.reports["当前持仓"]
    stock_summary = analytics.reports["股票汇总"]
    closed_report = analytics.reports["清仓收益"]
    daily_report = analytics.reports["每日持仓收益"]

    assert len(person_report.rows) >= 2
    assert len(holdings_report.rows) == 27
    assert len(stock_summary.rows) == 85
    open_rows = HoldingsManagementService.get_holdings_detail_rows(
        analytics, status="持有中", db_path=REAL_DB_PATH
    )
    assert len(open_rows) == 47
    assert len(closed_report.rows) == 58
    assert len(daily_report.rows) > 1000


def test_10_default_detail_view_and_status_tabs(real_analytics, qtbot):
    """验收 10：移除仪表板总览入口，默认直接展示持仓中/已清仓双 Tab，且持仓中放在前面展示。"""
    svc, analytics = real_analytics
    host = QWidget()
    host.service = svc
    host.analytics_service = svc
    host.accounts = SimpleNamespace(selected_ids=lambda: None, current_broker_name=lambda: "")
    host.quote_store = None
    host.info = SimpleNamespace(
        persons=[
            {"id": 1, "name": "个人", "enabled": True},
            {"id": 2, "name": "老婆", "enabled": True},
        ],
        instruments=(),
    )
    qtbot.addWidget(host)
    page = HoldingsPage(host)
    page.set_analytics(analytics)
    host.show()

    # 默认直接进入统一详情视图，且顶部仪表板总览切换框已隐藏
    assert page.view_mode == "detail"
    assert page.stack.currentIndex() == 1
    assert page.mode_box.isHidden()

    # 顶部展示「持仓中」和「已清仓」两个 Tab，且「持仓中」排在第一位并默认选中
    assert page.status_tabs.count() == 2
    assert page.status_tabs.tabText(0) == "持仓中"
    assert page.status_tabs.tabText(1) == "已清仓"
    assert page.status_tabs.currentIndex() == 0
    assert page.open_table_title_lbl.text() == "持仓明细 (共 47 只)"
    assert page._open_pnl_btns["all"].text() == "全部 (47)"

    # 点击切换到「已清仓」Tab
    page.status_tabs.setCurrentIndex(1)
    assert page.status_combo.currentData() == "已清仓"
    assert not page.closed_dashboard.isHidden()
    assert page.open_kpi_bar.isHidden()

    # 切回「持仓中」Tab
    page.status_tabs.setCurrentIndex(0)
    assert page.status_combo.currentData() == "持有中"
    assert not page.open_kpi_bar.isHidden()
    assert page.closed_dashboard.isHidden()

    # 验证「持仓中」盈亏贡献 Top 10 卡片存在、可见并可交互联动
    assert page.open_top10_card is not None
    assert not page.open_top10_card.isHidden()
    assert page.open_top10_card.top_n_spin.value() == 5

    # 折叠与展开
    page.open_top10_card.toggle_btn.click()
    assert page.open_top10_card.content_widget.isHidden()
    page.open_top10_card.toggle_btn.click()
    assert not page.open_top10_card.content_widget.isHidden()

    # 动态参数调整（如设为 2）
    page.open_top10_card.top_n_spin.setValue(2)
    assert len(page.open_top10_card.chart.winners) <= 2
    assert "Top 2" in page.open_top10_card.win_pill.text()
    page.open_top10_card.top_n_spin.setValue(5)

    # 点击 Top 榜单股票联动筛选持仓明细表
    if page.open_top10_card.chart.winners:
        test_tk = page.open_top10_card.chart.winners[0]["ticker"]
        page.open_top10_card.ticker_clicked.emit(test_tk)
        assert page._open_ticker_filter == test_tk
        assert not page.open_ticker_chip.isHidden()
        assert test_tk in page.open_ticker_chip.text()
        assert all(r.metadata.get("ticker") == test_tk for r in page.detail_model.rows)

        # 点击芯片清除筛选
        page.open_ticker_chip.click()
        assert page._open_ticker_filter == ""
        assert page.open_ticker_chip.isHidden()


