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
