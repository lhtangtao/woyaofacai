import sqlite3
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from flask import Flask, g, request

from portfolio.domain.models import Ledger, QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.closed_analytics_service import (
    build_closed_dashboard_analytics,
    filter_closed_rows,
    resolve_close_date_bounds,
)
from portfolio.services.holdings_management_service import (
    HoldingsManagementService,
    closed_columns,
)
from portfolio.ui.holdings_page import HoldingsPage
from portfolio.web import api_v1 as api_module
from portfolio.web.security.auth_context import build_auth_context


@pytest.fixture
def closed_dashboard_db(demo_path, monkeypatch):
    """构造包含多渠道（长桥/BBAE/嘉信）、盈/平/亏及跨月分批卖出的合成美股账本。"""
    monkeypatch.setattr(HoldingsManagementService, "get_latest_quotes_map", staticmethod(lambda: {}))
    with sqlite3.connect(demo_path) as db:
        db.execute("DELETE FROM person_operations")
        db.execute("DELETE FROM broker_trades")
        db.execute("UPDATE broker_accounts SET broker_code='LONGBRIDGE', display_name='长桥' WHERE id=1")
        db.execute("UPDATE broker_accounts SET broker_code='BBAE', display_name='BBAE' WHERE id=2")
        db.execute(
            "INSERT OR IGNORE INTO broker_accounts(id,broker_code,account_key,display_name,base_currency,book_timezone,day_cutoff,enabled,source_record_id) "
            "VALUES(3,'SCHWAB','SCHWAB_1','嘉信','USD','America/New_York','16:00',1,1)"
        )
        for identifier, symbol in enumerate(("NVDA", "TSLA", "FLAT", "OPEN"), 2):
            db.execute(
                "INSERT INTO instruments(id,market,symbol,name,currency,asset_type,category) VALUES(?,'US',?,?,'USD','STOCK','正股')",
                (identifier, symbol, symbol),
            )

        # 构造流水：
        # 1) (person=1, acc=1 长桥, inst=1 DEMO): 跨月分批卖出
        #    2025-10-10 BUY 100 @ 10 (=1000)
        #    2026-02-10 SELL 40 @ 15 (=600 -> 盈利 +200 归属 2026-02)
        #    2026-04-10 SELL 60 @ 8 (=480 -> 亏损 -120 归属 2026-04)
        #    总买入=1000, 总卖出=1080, realized_pnl=+80
        # 2) (person=1, acc=2 BBAE, inst=2 NVDA): 2026-06-05 BUY 10 @ 100 (=1000), 2026-06-15 SELL 10 @ 150 (=1500), pnl=+500
        # 3) (person=2, acc=3 嘉信, inst=3 TSLA): 2025-08-01 BUY 10 @ 50 (=500), 2025-08-15 SELL 10 @ 30 (=300), pnl=-200
        # 4) (person=1, acc=1 长桥, inst=4 FLAT): 2026-05-01 BUY 10 @ 20 (=200), 2026-05-10 SELL 10 @ 20 (=200), pnl=0 (持平)
        # 5) (person=1, acc=1 长桥, inst=5 OPEN): 2026-10-01 BUY 10 @ 10 (持有中，不应计入已清仓！)
        entries = [
            (1, 1, 1, "BUY", 100, 10, "2025-10-10"),
            (1, 1, 1, "SELL", 40, 15, "2026-02-10"),
            (1, 1, 1, "SELL", 60, 8, "2026-04-10"),
            (1, 2, 2, "BUY", 10, 100, "2026-06-05"),
            (1, 2, 2, "SELL", 10, 150, "2026-06-15"),
            (2, 3, 3, "BUY", 10, 50, "2025-08-01"),
            (2, 3, 3, "SELL", 10, 30, "2025-08-15"),
            (1, 1, 4, "BUY", 10, 20, "2026-05-01"),
            (1, 1, 4, "SELL", 10, 20, "2026-05-10"),
            (1, 1, 5, "BUY", 10, 10, "2026-10-01"),
        ]
        for n, (person, acc, inst, side, qty, price, day) in enumerate(entries, 1):
            db.execute(
                """INSERT INTO person_operations
                (id,operation_key,person_id,account_id,instrument_id,operation_kind,side,business_date,book_time,sequence_no,
                 quantity_e8,price_e8,amount_e8,amount_basis,currency,link_status,source_record_id)
                VALUES(?,?,?,?,?,'MARKET_TRADE',?,?,?,?,?,?,?,'GROSS','USD','LINKED',1)""",
                (
                    n, f"op-{n}", person, acc, inst, side, day, f"{day} 10:00:00", n,
                    int(D(qty) * 10**8), int(D(price) * 10**8), int(D(qty) * D(price) * 10**8),
                ),
            )
    svc = AnalyticsService(demo_path)
    return svc, svc.calculate(QueryScope(date(2026, 11, 12)))


def test_closed_analytics_metrics_invariants_and_trade_level_monthly(closed_dashboard_db):
    svc, analytics = closed_dashboard_db
    rows = HoldingsManagementService.get_holdings_detail_rows(analytics, status="已清仓", db_path=svc.path)
    # OPEN 持有中不计入；已清仓共 4 条：DEMO(+80)、NVDA(+500)、TSLA(-200)、FLAT(0)
    assert len(rows) == 4

    dash = build_closed_dashboard_analytics(
        analytics, rows, mode="person", trend_window="all", today=date(2026, 11, 12)
    )
    cards = dash["cards"]
    assert cards["closed_count"] == 4
    assert cards["total_buy_amount"] == 2700.0
    assert cards["total_sell_amount"] == 3080.0
    assert cards["realized_profit"] == 580.0
    assert cards["realized_loss"] == -200.0
    assert cards["net_realized_pnl"] == 380.0
    assert cards["realized_pnl_rate"] == pytest.approx(380.0 / 2700.0)

    # 结构数量之和必须等于已清仓股票数
    st = dash["structure"]
    assert st["win_count"] == 2
    assert st["flat_count"] == 1
    assert st["loss_count"] == 1
    assert st["win_count"] + st["flat_count"] + st["loss_count"] == cards["closed_count"]

    # 券商汇总按净已实现盈亏倒序排列（BBAE +500 > 长桥 +80 > 嘉信 -200），且总和精确一致
    brokers = dash["broker_summary"]
    assert [b["channel"] for b in brokers] == ["BBAE", "长桥", "嘉信"]
    assert sum(b["net_pnl"] for b in brokers) == pytest.approx(cards["net_realized_pnl"])
    assert dash["broker_total"]["net_pnl"] == cards["net_realized_pnl"]

    # 跨月卖出严格按卖出交易归集，而非把 +80 全算进 2026-04
    monthly = {m["month"]: m["pnl"] for m in dash["monthly_trend"]}
    assert dash["attribution_mode"] == "trade_level"
    assert monthly["2025-08"] == pytest.approx(-200.0)
    assert monthly["2026-02"] == pytest.approx(200.0)
    assert monthly["2026-04"] == pytest.approx(-120.0)
    assert monthly["2026-05"] == pytest.approx(0.0)
    assert monthly["2026-06"] == pytest.approx(500.0)
    assert sum(monthly.values()) == pytest.approx(cards["net_realized_pnl"])


def test_closed_date_range_and_mac_dashboard_linkage(closed_dashboard_db, qtbot):
    svc, analytics = closed_dashboard_db
    host = SimpleNamespace()
    # 使用 QWidget 作为 parent host
    from PySide6.QtWidgets import QWidget
    host_widget = QWidget()
    host_widget.service = svc
    host_widget.analytics_service = svc
    host_widget.accounts = SimpleNamespace(selected_ids=lambda: None, current_broker_name=lambda: "")
    host_widget.quote_store = None
    host_widget.info = SimpleNamespace(persons=(), instruments=())
    qtbot.addWidget(host_widget)

    page = HoldingsPage(host_widget)
    page.resize(1440, 900)
    host_widget.show()
    page.show()
    page.set_analytics(analytics)
    page.open_closed_positions()

    assert not page.closed_dashboard.isHidden()
    assert page.closed_dashboard.card_count.val_lbl.text() == "4"
    assert page.closed_dashboard.card_net.val_lbl.text() == "+380.00"
    assert page.detail_model.rowCount() == 4
    # 原有 18 列完整保留
    assert tuple(page.detail_model.columns) == tuple(lbl for _, lbl, _ in closed_columns("person"))

    # 点击按渠道汇总表中 BBAE 单元格，联动筛选看板与下方明细表
    bbae_row = next(
        r for r in range(page.closed_dashboard.broker_table.rowCount())
        if page.closed_dashboard.broker_table.item(r, 0).text() == "BBAE"
    )
    page.closed_dashboard.broker_table.cellClicked.emit(bbae_row, 0)
    assert page.broker_combo.currentData() == "BBAE"
    assert page.closed_dashboard.card_count.val_lbl.text() == "1"
    assert page.closed_dashboard.card_net.val_lbl.text() == "+500.00"
    assert page.detail_model.rowCount() == 1
    assert page.detail_model.rows[0].metadata["ticker"] == "NVDA"

    # 点击「合计」行恢复全部渠道
    tot_row = page.closed_dashboard.broker_table.rowCount() - 1
    assert page.closed_dashboard.broker_table.item(tot_row, 0).text() == "合计"
    page.closed_dashboard.broker_table.cellClicked.emit(tot_row, 0)
    assert page.broker_combo.currentData() == ""
    assert page.closed_dashboard.card_count.val_lbl.text() == "4"
    assert page.detail_model.rowCount() == 4

    # 切换清仓日期范围「今年以来」应过滤掉 2025-08 TSLA，看板与明细同步变为 3 条
    page.close_date_combo.setCurrentIndex(page.close_date_combo.findData("ytd"))
    assert page.closed_dashboard.card_count.val_lbl.text() == "3"
    assert page.detail_model.rowCount() == 3
    page.close_date_combo.setCurrentIndex(page.close_date_combo.findData("all"))
    assert page.closed_dashboard.card_count.val_lbl.text() == "4"

    # 点击环形图「亏损股票」仅筛选下方明细表格（看板仍展示全部4只的结构）
    page.closed_dashboard.donut.category_clicked.emit("loss")
    assert page.detail_model.rowCount() == 1
    assert page.detail_model.rows[0].metadata["ticker"] == "TSLA"
    assert page.closed_dashboard.card_count.val_lbl.text() == "4"
    page.structure_chip.click()
    assert page.detail_model.rowCount() == 4

    # 切换至「当前持仓」时隐藏已清仓看板，保留原有持仓视图
    page.status_combo.setCurrentIndex(page.status_combo.findData("持有中"))
    assert page.closed_dashboard.isHidden()


def test_closed_dashboard_web_api(closed_dashboard_db, monkeypatch):
    svc, _ = closed_dashboard_db
    monkeypatch.setattr(api_module, "get_analytics_service", lambda: svc)
    monkeypatch.setattr(api_module, "get_today_service", lambda: SimpleNamespace(closed_report=lambda a: a.reports["清仓收益"]))
    app = Flask(__name__)
    app.register_blueprint(api_module.api_v1)

    @app.before_request
    def auth():
        g.auth_context = build_auth_context("LOCAL_LOOPBACK")

    client = app.test_client()
    resp = client.get(
        "/api/v1/holdings/detail",
        query_string=dict(status="已清仓", closed_mode="person", include_closed_dashboard="1"),
    ).get_json()
    assert resp["status"] == "ok"
    assert resp["total"] == 4
    cd = resp["closed_dashboard"]
    assert cd["cards"]["closed_count"] == 4
    assert cd["cards"]["net_realized_pnl"] == 380.0
    assert set(resp["available_brokers"]) == {"长桥", "BBAE", "嘉信"}

    # 清仓日期过滤近 6 个月（2026-05-12 ~ 2026-11-12，应命中 2026-06-15 NVDA；排除 2026-04-10/2026-05-10/2025-08-15）
    s6, e6 = resolve_close_date_bounds("6m", today=date(2026, 11, 12))
    assert s6 == "2026-05-12"
    filtered = filter_closed_rows(resp["rows"], start_date=s6, end_date=e6)
    assert [r["ticker"] for r in filtered] == ["NVDA"]

    # Web 端盈亏结构联动
    loss_only = client.get(
        "/api/v1/holdings/detail",
        query_string=dict(status="已清仓", pnl_status="loss", include_closed_dashboard="1"),
    ).get_json()
    assert loss_only["total"] == 1
    assert loss_only["rows"][0]["ticker"] == "TSLA"


def test_closed_ticker_top10_and_cumulative_monthly():
    from portfolio.services.closed_analytics_service import compute_closed_ticker_top10

    sample_rows = [
        {"ticker": "NVDA", "realized_pnl": 500.0, "broker": "BBAE"},
        {"ticker": "NVDA", "realized_pnl": 300.0, "broker": "长桥"},
        {"ticker": "AAPL", "realized_pnl": 200.0, "broker": "长桥"},
        {"ticker": "MSFT", "realized_pnl": 100.0, "broker": "嘉信"},
        {"ticker": "GOOG", "realized_pnl": 50.0, "broker": "长桥"},
        {"ticker": "AMZN", "realized_pnl": 20.0, "broker": "长桥"},
        {"ticker": "META", "realized_pnl": 10.0, "broker": "长桥"},
        {"ticker": "FLAT", "realized_pnl": 0.0, "broker": "长桥"},
        {"ticker": "TSLA", "realized_pnl": -300.0, "broker": "长桥"},
        {"ticker": "INTC", "realized_pnl": -150.0, "broker": "嘉信"},
        {"ticker": "AMD", "realized_pnl": -50.0, "broker": "长桥"},
    ]
    top10 = compute_closed_ticker_top10(sample_rows)
    assert top10["scope_profit_total"] == pytest.approx(1180.0)
    assert top10["scope_loss_total"] == pytest.approx(-500.0)
    assert top10["scope_net_pnl"] == pytest.approx(680.0)

    # 盈利 Top 5：NVDA (800), AAPL (200), MSFT (100), GOOG (50), AMZN (20)
    profits = top10["profit_top"]
    assert len(profits) == 5
    assert profits[0]["ticker"] == "NVDA"
    assert profits[0]["realized_pnl"] == pytest.approx(800.0)
    assert profits[0]["closed_count"] == 2
    assert set(profits[0]["brokers"]) == {"BBAE", "长桥"}
    assert profits[1]["ticker"] == "AAPL"
    assert profits[4]["ticker"] == "AMZN"

    # 亏损 Top 5：TSLA (-300), INTC (-150), AMD (-50)
    losses = top10["loss_top"]
    assert len(losses) == 3
    assert losses[0]["ticker"] == "TSLA"
    assert losses[0]["realized_pnl"] == pytest.approx(-300.0)
    assert losses[1]["ticker"] == "INTC"
    assert losses[2]["ticker"] == "AMD"

    # 0 盈亏不参与排名
    assert not any(p["ticker"] == "FLAT" for p in profits)
    assert not any(p["ticker"] == "FLAT" for p in losses)


def test_closed_top10_and_dual_axis_chart_ui(closed_dashboard_db, qtbot):
    from PySide6.QtWidgets import QWidget
    svc, analytics = closed_dashboard_db
    host_widget = QWidget()
    host_widget.service = svc
    host_widget.analytics_service = svc
    host_widget.accounts = SimpleNamespace(selected_ids=lambda: None, current_broker_name=lambda: "")
    host_widget.quote_store = None
    host_widget.info = SimpleNamespace(persons=(), instruments=())
    qtbot.addWidget(host_widget)

    page = HoldingsPage(host_widget)
    page.resize(1440, 900)
    host_widget.show()
    page.show()
    page.set_analytics(analytics)
    page.open_closed_positions()

    dash = page.closed_dashboard
    assert dash.top10_card is not None
    assert dash.top10_card.toggle_btn is not None

    # Top10 折叠展开测试
    assert not dash.top10_card.content_widget.isHidden()
    dash.top10_card.toggle_btn.click()
    assert dash.top10_card.content_widget.isHidden()
    dash.top10_card.toggle_btn.click()
    assert not dash.top10_card.content_widget.isHidden()

    # 月度盈亏与累计盈亏双轴图表图例切换测试
    chart = dash.bar_chart
    assert chart._show_bars is True
    assert chart._show_line is True
    dash.bar_legend_btn.click()
    assert chart._show_bars is False
    assert chart._show_line is True
    dash.cum_legend_btn.click()
    assert chart._show_line is False
    dash.bar_legend_btn.click()
    assert chart._show_bars is True
    dash.cum_legend_btn.click()
    assert chart._show_line is True

    # 累计收益检验：最后一个月的 cumulative_pnl 等于 net_realized_pnl (+380.00)
    trend = dash._cached_payload.get("monthly_trend", [])
    assert len(trend) > 0
    assert trend[-1]["cumulative_pnl"] == pytest.approx(380.0)

    # 点击 Top 10 NVDA 联动筛选明细表
    dash.ticker_clicked.emit("NVDA")
    assert page._closed_ticker_filter == "NVDA"
    assert page.detail_model.rowCount() == 1
    assert page.detail_model.rows[0].metadata["ticker"] == "NVDA"
    assert not page.closed_ticker_chip.isHidden()
    assert "NVDA" in page.closed_ticker_chip.text()

    # 点击筛选芯片清除 Top 10 筛选
    page.closed_ticker_chip.click()
    assert page._closed_ticker_filter == ""
    assert page.detail_model.rowCount() == 4
    assert page.closed_ticker_chip.isHidden()

    # 再次触发同代码自动取消筛选联动
    dash.top10_card.chart._on_bar_clicked("NVDA")
    assert page._closed_ticker_filter == "NVDA"
    assert page.detail_model.rowCount() == 1
    dash.top10_card.chart._on_bar_clicked("NVDA")
    assert page._closed_ticker_filter == ""
    assert page.detail_model.rowCount() == 4

    # Top N 动态参数调整测试（填 1 只展示 1 个最多收益与 1 个最多亏损；填 5 则展示最多 5 个）
    assert dash.top10_card.top_n_spin is not None
    assert dash.top10_card.top_n_spin.value() == 5
    dash.top10_card.top_n_spin.setValue(1)
    assert len(dash.top10_card.chart.winners) == 1
    assert len(dash.top10_card.chart.losers) == 1
    assert "Top 1" in dash.top10_card.win_pill.text()
    assert "Top 1" in dash.top10_card.loss_pill.text()
    dash.top10_card.top_n_spin.setValue(5)
    assert len(dash.top10_card.chart.winners) > 1
    assert "Top 5" in dash.top10_card.win_pill.text()


def test_unified_kpi_cards_appearance_and_values(closed_dashboard_db, qtbot):
    from PySide6.QtWidgets import QWidget

    svc, analytics = closed_dashboard_db
    host_widget = QWidget()
    host_widget.analytics_service = svc
    host_widget.accounts = SimpleNamespace(selected_ids=lambda: None, current_broker_name=lambda: "")
    host_widget.quote_store = None
    host_widget.info = SimpleNamespace(persons=(), instruments=())
    qtbot.addWidget(host_widget)

    page = HoldingsPage(host_widget)
    page.resize(1440, 900)
    host_widget.show()
    page.show()
    page.set_analytics(analytics)

    # 1. 验证「持有中」顶部 KPI 卡片为 6 张，且高度为 60
    open_bar = page.open_kpi_bar
    open_cards = [
        open_bar.card_mv,
        open_bar.card_cost,
        open_bar.card_pnl,
        open_bar.card_rate,
        open_bar.card_count,
        open_bar.card_top5,
    ]
    assert len(open_cards) == 6
    for c in open_cards:
        assert c.height() == 60 or c.maximumHeight() == 60

    # 2. 切换到「已清仓」页面
    page.open_closed_positions()
    dash = page.closed_dashboard
    closed_cards = [
        dash.card_sell,
        dash.card_buy,
        dash.card_net,
        dash.card_rate,
        dash.card_count,
        dash.card_win_ratio,
    ]
    # 验证「已清仓」顶部 KPI 卡片恰好为 6 张，且高度为 60
    assert len(closed_cards) == 6
    for c in closed_cards:
        assert c.height() == 60 or c.maximumHeight() == 60

    # 3. 验证已清仓 6 个指标数值与财务口径一致
    assert dash.card_sell.val_lbl.text() == "3,080.00"
    assert dash.card_buy.val_lbl.text() == "2,700.00"
    assert dash.card_net.val_lbl.text() == "+380.00"
    assert dash.card_rate.val_lbl.text() == "+14.07%"
    assert dash.card_count.val_lbl.text() == "4"
    # 4 只已清仓记录中 2 只盈利 (DEMO +80, NVDA +500)，占比 50.00%
    assert dash.card_win_ratio.val_lbl.text() == "50.00%"
    # 盈利占比 Tooltip 包含说明
    assert "当前有效筛选范围内盈利已清仓记录数占全部已清仓记录数的比例" in dash.card_win_ratio.toolTip()


def test_closed_win_ratio_edge_cases(qtbot):
    from portfolio.services.closed_analytics_service import build_closed_dashboard_analytics
    from portfolio.ui.closed_dashboard import ClosedDashboardWidget

    widget = ClosedDashboardWidget()
    qtbot.addWidget(widget)

    # 1. 无数据空状态
    empty_payload = build_closed_dashboard_analytics(None, [])
    widget.set_data(empty_payload)
    assert widget.card_count.val_lbl.text() == "0"
    assert widget.card_win_ratio.val_lbl.text() == "—"

    # 2. 全部盈利（2/2）
    all_win_rows = [
        {"ticker": "NVDA", "total_buy_amount": 100, "total_sell_amount": 150, "realized_pnl": 50},
        {"ticker": "AAPL", "total_buy_amount": 200, "total_sell_amount": 260, "realized_pnl": 60},
    ]
    win_payload = build_closed_dashboard_analytics(None, all_win_rows)
    widget.set_data(win_payload)
    assert widget.card_win_ratio.val_lbl.text() == "100.00%"

    # 3. 全部亏损（0/2）
    all_loss_rows = [
        {"ticker": "NVDA", "total_buy_amount": 100, "total_sell_amount": 50, "realized_pnl": -50},
        {"ticker": "AAPL", "total_buy_amount": 200, "total_sell_amount": 100, "realized_pnl": -100},
    ]
    loss_payload = build_closed_dashboard_analytics(None, all_loss_rows)
    widget.set_data(loss_payload)
    assert widget.card_win_ratio.val_lbl.text() == "0.00%"

    # 4. 包含持平记录（1 赢，1 平，1 亏 -> 总数 3，胜率 33.33%）
    mixed_rows = [
        {"ticker": "NVDA", "total_buy_amount": 100, "total_sell_amount": 150, "realized_pnl": 50},
        {"ticker": "FLAT", "total_buy_amount": 100, "total_sell_amount": 100, "realized_pnl": 0},
        {"ticker": "TSLA", "total_buy_amount": 100, "total_sell_amount": 50, "realized_pnl": -50},
    ]
    mixed_payload = build_closed_dashboard_analytics(None, mixed_rows)
    widget.set_data(mixed_payload)
    assert widget.card_win_ratio.val_lbl.text() == "33.33%"


def test_holdings_local_filter_bar_unified_and_modes(closed_dashboard_db, qtbot):
    from PySide6.QtWidgets import QWidget
    from portfolio.ui.holdings_page import HoldingsLocalFilterBar

    svc, analytics = closed_dashboard_db
    host_widget = QWidget()
    host_widget.analytics_service = svc
    host_widget.accounts = SimpleNamespace(selected_ids=lambda: None, current_broker_name=lambda: "")
    host_widget.quote_store = None
    host_widget.info = SimpleNamespace(persons=(), instruments=())
    qtbot.addWidget(host_widget)

    page = HoldingsPage(host_widget)
    page.resize(1440, 900)
    host_widget.show()
    page.show()
    page.set_analytics(analytics)

    # 1. 验证 local_filter_bar 实例存在且类型为 HoldingsLocalFilterBar
    bar = page.local_filter_bar
    assert isinstance(bar, HoldingsLocalFilterBar)
    assert bar.height() == 32 or bar.maximumHeight() == 32 or bar.height() <= 50

    # 2. 持有中模式验证：
    # 股票类型与搜索框可见，清仓日期隐藏，币种筛选彻底不存在于局部栏中
    assert bar.lbl_stock_type.isVisible()
    assert bar.open_industry_combo.isVisible()
    assert not bar.lbl_close_date.isVisible()
    assert not bar.close_date_combo.isVisible()
    assert not hasattr(bar, "open_currency_combo")
    assert bar.search_input.placeholderText() == "搜索股票名称 / Ticker"

    # 在持有中模式输入搜索词
    bar.search_input.setText("TQQQ")
    assert bar.search_input.text() == "TQQQ"

    # 3. 切换至已清仓模式
    page.open_closed_positions()
    # 验证筛选栏仍然是同一个 bar 实例，且位于同一层次
    assert page.local_filter_bar is bar
    assert not bar.lbl_stock_type.isVisible()
    assert not bar.open_industry_combo.isVisible()
    assert bar.lbl_close_date.isVisible()
    assert bar.close_date_combo.isVisible()
    assert bar.search_input.placeholderText() == "搜索股票名称、Ticker、人员、券商或账号…"
    # 验证此时已清仓模式拥有独立的搜索词记忆（初始为空）
    assert bar.search_input.text() == ""

    # 在已清仓模式输入已清仓搜索词
    bar.search_input.setText("NVDA")
    bar.close_date_combo.setCurrentIndex(bar.close_date_combo.findData("ytd"))
    assert bar.search_input.text() == "NVDA"

    # 4. 切回持有中模式
    page.status_tabs.setCurrentIndex(0)
    # 自动恢复持有中之前的状态（TQQQ）
    assert bar.lbl_stock_type.isVisible()
    assert bar.search_input.text() == "TQQQ"

    # 5. 再切回已清仓模式
    page.open_closed_positions()
    # 自动恢复已清仓之前的状态（NVDA，今年以来）
    assert bar.lbl_close_date.isVisible()
    assert bar.search_input.text() == "NVDA"
    assert bar.close_date_combo.currentData() == "ytd"

    # 6. 测试局部重置筛选
    bar.reset_btn.click()
    assert bar.search_input.text() == ""
    assert bar.close_date_combo.currentData() == "all"


def test_middle_analytics_unified_layout_and_broker_summary_table(closed_dashboard_db, qtbot):
    """验证持有中与已清仓中部分析区域三列统一布局及渠道汇总列表功能。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidget, QWidget
    from portfolio.ui.open_holdings_dashboard import OpenHoldingsChartsRow, RatioProgressBarCell

    svc, analytics = closed_dashboard_db
    host_widget = QWidget()
    host_widget.analytics_service = svc
    host_widget.accounts = SimpleNamespace(selected_ids=lambda: None, current_broker_name=lambda: "")
    host_widget.quote_store = None
    host_widget.info = SimpleNamespace(persons=(), instruments=())
    qtbot.addWidget(host_widget)

    page = HoldingsPage(host_widget)
    page.resize(1440, 900)
    host_widget.show()
    page.show()
    page.set_analytics(analytics)

    # 1. 持有中：验证左侧已改为 QTableWidget (broker_table)
    open_row: OpenHoldingsChartsRow = page.open_charts_row
    assert hasattr(open_row, "broker_table")
    assert isinstance(open_row.broker_table, QTableWidget)

    # 验证列名：券商渠道、市值占比、持仓成本、持仓市值、浮动盈亏、持仓数量
    headers = [open_row.broker_table.horizontalHeaderItem(i).text() for i in range(open_row.broker_table.columnCount())]
    assert headers == ["券商渠道", "市值占比", "持仓成本", "持仓市值", "浮动盈亏", "持仓数量"]

    # 验证列表至少有数据行与合计行
    row_count = open_row.broker_table.rowCount()
    assert row_count >= 2
    # 末行是合计行：当前环境无有效行情（total_mv=0）时，验证合计占比按规则显示 "—"（不硬编码100.0%）
    tot_row = row_count - 1
    assert open_row.broker_table.item(tot_row, 0).text() == "合计"
    assert open_row.broker_table.item(tot_row, 1).data(Qt.ItemDataRole.UserRole) == "—"
    assert open_row.broker_table.item(tot_row, 4) is not None  # 浮动盈亏合计单元格存在

    # 验证单元格内嵌的进度条组件
    cell_widget = open_row.broker_table.cellWidget(0, 1)
    assert isinstance(cell_widget, RatioProgressBarCell)
    assert cell_widget.pbar.maximum() == 1000
    assert cell_widget.pbar.value() >= 0

    tot_widget = open_row.broker_table.cellWidget(tot_row, 1)
    assert isinstance(tot_widget, RatioProgressBarCell)
    assert tot_widget.pbar.value() == 0

    # 2. 点击券商渠道行联动测试
    first_broker = open_row.broker_table.item(0, 0).text()
    open_row.broker_table.cellClicked.emit(0, 0)
    # 再次点击取消联动
    open_row.broker_table.cellClicked.emit(0, 0)
    # 点击合计行取消联动
    open_row.broker_table.cellClicked.emit(tot_row, 0)

    # 3. 验证已清仓看板中部分析区域三列布局统一
    page.open_closed_positions()
    closed_dash = page.closed_dashboard
    assert hasattr(closed_dash, "broker_table")
    # 验证已清仓 8 个财务字段保持完整
    closed_headers = [closed_dash.broker_table.horizontalHeaderItem(i).text() for i in range(closed_dash.broker_table.columnCount())]
    assert closed_headers == ["渠道", "股票数", "买入总额", "卖出总额", "已实现盈利", "已实现亏损", "净已实现盈亏", "收益率"]

    # 4. 验证卡片最小高度一致性 (265)
    assert open_row.broker_card.minimumHeight() == 265
    assert open_row.industry_card.minimumHeight() == 265
    assert open_row.trend_card.minimumHeight() == 265




