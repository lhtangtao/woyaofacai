import csv
import sqlite3
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from flask import Flask, g, request

from portfolio.domain.models import QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.holdings_management_service import HoldingsManagementService, closed_account_rows, closed_columns
from portfolio.ui.holdings_page import HoldingsPage
from portfolio.ui.sidebar import GROUPS
from portfolio.web import api_v1 as api_module
from portfolio.web.security.auth_context import build_auth_context


@pytest.fixture
def closed_ledger(demo_path, monkeypatch):
    monkeypatch.setattr(HoldingsManagementService, "get_latest_quotes_map", staticmethod(lambda: {}))
    with sqlite3.connect(demo_path) as db:
        db.execute("DELETE FROM person_operations")
        db.execute("DELETE FROM broker_trades")
        db.execute("UPDATE broker_accounts SET broker_code='LONGBRIDGE'")
        for identifier, symbol in enumerate(("OTHER", "RESIDUAL", "OPEN", "TRANSFER"), 2):
            db.execute("INSERT INTO instruments(id,market,symbol,name,currency,asset_type,category) VALUES(?,'US',?,?,'USD','ETF','正股')",
                       (identifier, symbol, symbol))
        entries = [
            (1, 1, 1, "BUY", 1, 10, 2), (1, 1, 1, "BUY", 3, 20, 3),
            (1, 1, 1, "SELL", 1, 20, 4), (1, 1, 1, "SELL", 3, 30, 5),
            (2, 1, 1, "BUY", 2, 30, 2), (2, 1, 1, "SELL", 2, 20, 6),
            (1, 2, 1, "BUY", 1, 22, 2), (1, 2, 1, "SELL", 1, 22, 6),
            (1, 1, 2, "BUY", 2, 50, 2), (1, 1, 2, "SELL", 2, 55, 6),
            (1, 1, 3, "BUY", 1, 60, 2), (1, 1, 3, "SELL", D(".7"), 61, 6),
            (1, 1, 4, "BUY", 3, 10, 2), (1, 1, 4, "SELL", 1, 11, 6),
            (2, 1, 4, "BUY", 1, 10, 2), (2, 1, 4, "SELL", 1, 11, 6),
            (1, 1, 5, "BUY", 2, 10, 2), (1, 1, 5, "TRANSFER_OUT", 2, 10, 3),
            (2, 1, 5, "TRANSFER_IN", 2, 10, 3), (2, 1, 5, "SELL", 2, 12, 6),
        ]
        for n, (person, account, instrument, side, quantity, price, day) in enumerate(entries, 1):
            is_transfer = side.startswith("TRANSFER")
            stamp = f"2026-01-{day:02d}"
            db.execute("""INSERT INTO person_operations
                (id,operation_key,person_id,account_id,instrument_id,operation_kind,side,business_date,book_time,sequence_no,
                 quantity_e8,price_e8,amount_e8,amount_basis,currency,counterparty_person_id,transfer_group_key,link_status,source_record_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'USD',?,?,'LEGACY_UNLINKED',1)""",
                       (n,str(n),person,account,instrument,"OWNER_TRANSFER" if is_transfer else "MARKET_TRADE",side,stamp,
                        stamp+" 10:00:00",n,int(D(quantity)*10**8),price*10**8,int(D(quantity)*price*10**8),
                        "TRANSFER_VALUE" if is_transfer else "GROSS",3-person if is_transfer else None,
                        "synthetic-transfer" if is_transfer else None))
        db.executemany("""INSERT INTO corporate_actions
            (action_key,instrument_id,effective_date,action_type,ratio_numerator,ratio_denominator,source_record_id)
            VALUES(?,1,?,?,?,?,1)""",
                       [("split","2026-01-07","SPLIT",4,1), ("reverse","2026-01-08","REVERSE_SPLIT",1,2)])
    service = AnalyticsService(demo_path)
    return service, service.calculate(QueryScope(date(2026,1,9)))


def test_legacy_account_totals_and_dimensions(closed_ledger):
    _, analytics = closed_ledger
    legacy = analytics.reports["清仓收益"]
    rows = closed_account_rows(legacy)
    assert len(rows) == len(legacy.rows)
    keyed = {(r["account_id"], r["instrument_id"]):r for r in rows}
    assert (1, 4) not in keyed
    keys = ("buy_quantity","sell_quantity","total_buy_amount","total_sell_amount","realized_pnl","operation_count","avg_buy_price","avg_sell_price","current_price")
    for row in legacy.rows:
        new = keyed[row.metadata["account_id"],row.metadata["instrument_id"]]
        assert tuple(new[k] for k in keys) == row.values[3:12]
    assert len(closed_account_rows(legacy, broker="长桥", ticker="DEMO")) == 2
    assert len(closed_account_rows(legacy, search="OTHER")) == 1
    assert len(closed_account_rows(legacy, account_ids=(2,))) == 1
    assert keyed[1,1]["total_buy_amount"] == 130 and keyed[1,1]["realized_pnl"] == 20
    assert keyed[1,1]["buy_quantity"] == keyed[1,1]["sell_quantity"] == 12
    assert keyed[1,1]["avg_buy_price"].quantize(D(".0001")) == D("10.8333")
    assert keyed[1,1]["avg_sell_price"] == D("12.5")
    assert keyed[2,1]["realized_pnl"] == 0 and keyed[1,2]["realized_pnl"] == 10
    assert keyed[1,2]["current_price"] is None and keyed[1,3]["sell_quantity"] == D(".7")
    assert keyed[1,5]["realized_pnl"] == 4 and keyed[1,5]["operation_count"] == 4
    assert keyed[1,5]["buy_quantity"] == keyed[1,5]["sell_quantity"] == 4


def test_person_closing_values_and_special_cases(closed_ledger):
    service, analytics = closed_ledger
    rows = HoldingsManagementService.get_holdings_detail_rows(analytics,status="已清仓",db_path=service.path)
    keyed = {(r["person_id"],r["account_id"],r["instrument_id"]):r for r in rows}
    assert len([k for k in keyed if k[1:] == (1,1)]) == 2
    win, loss, flat = (keyed[k] for k in ((1,1,1),(2,1,1),(1,2,1)))
    assert (win["realized_pnl"],loss["realized_pnl"],flat["realized_pnl"]) == (40,-20,0)
    assert win["realized_pnl_rate"] == pytest.approx(40/70)
    assert loss["realized_pnl_rate"] == pytest.approx(-20/60) and flat["realized_pnl_rate"] == 0
    assert (win["buy_quantity"], win["sell_quantity"], win["buy_count"], win["sell_count"]) == (8,8,2,2)
    assert (win["avg_buy_price"],win["avg_sell_price"]) == (8.75,13.75)
    assert win["orig_close_price"] == 30 and win["close_price"] == 15 and win["is_split_adjusted"]
    assert win["first_buy_date"] == "2026-01-02" and win["last_close_date"] == "2026-01-05"
    assert win["post_close_change"] == pytest.approx((110-15)/15)
    assert keyed[1,1,2]["current_price"] is None and keyed[1,1,2]["post_close_change"] is None
    assert keyed[1,1,3]["sell_quantity"] == .7 and (1,1,4) not in keyed
    assert keyed[2,1,4]["realized_pnl"] == 1
    assert keyed[1,1,5]["sell_count"] == 0 and keyed[1,1,5]["close_price"] is None
    assert keyed[2,1,5]["first_buy_date"] is None


def test_web_scope_schema_and_drawer(closed_ledger, monkeypatch):
    service, _ = closed_ledger
    monkeypatch.setattr(api_module,"get_analytics_service",lambda:service)
    monkeypatch.setattr(api_module,"get_today_service",lambda:SimpleNamespace(closed_report=lambda a:a.reports["清仓收益"]))
    monkeypatch.setattr("portfolio.web.security.audit.record_audit_event",lambda *a,**kw:None)
    app=Flask(__name__)
    app.register_blueprint(api_module.api_v1)

    @app.before_request
    def auth():
        g.auth_context = build_auth_context("REMOTE_PUBLIC",dict(user_id=1,username="synthetic",role="user",person_id=1)) if request.headers.get("X-Test-User") else build_auth_context("LOCAL_LOOPBACK")

    client=app.test_client()
    url="/api/v1/holdings/detail"
    query=dict(status="已清仓",ticker="DEMO",broker="长桥",closed_mode="account")
    data=client.get(url,query_string=query).get_json()
    assert data["total"] == 2 and data["columns"] == [list(c) for c in closed_columns("account")]
    scoped=client.get(url,query_string=query,headers={"X-Test-User":"1"}).get_json()
    by_account={row["account_id"]:row for row in scoped["rows"]}
    assert D(by_account[1]["realized_pnl"]) == 40
    denied=client.get(url,query_string={**query,"person_id":2},headers={"X-Test-User":"1"}).get_json()
    assert denied["total"] == 0
    detail=client.get("/api/v1/holdings/stock_drawer",query_string=dict(ticker="DEMO",account_id=2,instrument_id=1)).get_json()
    assert detail["status"] == "ok" and len(detail["trades"]) == 2
    assert all(trade["person_name"] == "示例甲" for trade in detail["trades"])
    assert all(row["name"] != "清仓收益" for row in client.get("/api/v1/reports").get_json()["reports"])


def test_mac_modes_filter_sort_export_and_drawer(closed_ledger, qtbot, monkeypatch, tmp_path):
    service, analytics = closed_ledger
    from portfolio.ui.holdings_page import QFileDialog
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    host=QWidget()
    host.service=service
    host.analytics_service=service
    host.accounts=SimpleNamespace(selected_ids=lambda:None,current_broker_name=lambda:"")
    host.quote_store=None
    host.info=SimpleNamespace(persons=(),instruments=())
    qtbot.addWidget(host)
    page=HoldingsPage(host)
    page.resize(1400,800)
    host.show()
    page.show()
    page.set_analytics(analytics)
    page.open_closed_positions()
    assert page.closed_mode.currentIndex() == 0
    assert "历史总买入数量" in page.detail_model.columns
    page.search_input.setText("DEMO")
    assert len(page.detail_model.rows) == 3
    page.closed_mode.setCurrentIndex(1)
    assert page.search_input.text() == "DEMO" and len(page.detail_model.rows) == 2
    page.detail_model.toggle_sort(page.detail_model.columns.index("清仓收益"))
    assert page.detail_model.rows[0].metadata["account_id"] == 2
    page.closed_mode.setCurrentIndex(0)
    page.closed_mode.setCurrentIndex(1)
    assert page.detail_model.rows[0].metadata["account_id"] == 2
    assert page.detail_model.sort_order == Qt.SortOrder.AscendingOrder
    page._on_detail_row_clicked(page.detail_model.index(0,0))
    assert not page.drawer.isHidden()
    assert page.drawer.trade_model.rowCount() == 2
    assert page.drawer.trade_model.rows[0].values[2:4] == ("长桥", "卖出")
    out=tmp_path/"closed.csv"
    monkeypatch.setattr(QFileDialog,"getSaveFileName",lambda *a:(str(out),""))
    page._export_detail()
    with out.open(encoding="utf-8-sig",newline="") as stream:
        rows=list(csv.reader(stream))
    assert rows[0] == [label for _,label,_ in closed_columns("account")]
    assert rows[1][0].endswith("#2")
    page.sync_person_from_main(1)
    assert next(r for r in page.detail_model.rows if r.metadata["account_id"] == 1).metadata["realized_pnl"] == 40
    assert all(label != "清仓收益" for _,entries in GROUPS for label,*_ in entries)


def test_currency_projection_preserves_original_return(closed_ledger):
    service, original = closed_ledger
    with sqlite3.connect(service.path) as db:
        db.execute("INSERT INTO fx_rates(rate_date,base_currency,quote_currency,rate_e8,provider,source_record_id) "
                   "VALUES('2026-01-09','USD','CNY',700000000,'synthetic',1)")
    analytics = service.calculate(QueryScope(original.scope.as_of, reporting_currency="CNY"))
    rows = HoldingsManagementService.get_holdings_detail_rows(analytics,status="已清仓",db_path=service.path)
    row = next(r for r in rows if (r["person_id"],r["account_id"],r["instrument_id"]) == (1,1,1))
    assert row["currency"] == "CNY" and row["realized_pnl"] == 280 and row["total_buy_amount"] == 490
    assert row["realized_pnl_rate"] == pytest.approx(40/70)
    assert row["close_price"] == 105 and row["current_price"] == 770
    account = closed_account_rows(analytics.reports["清仓收益"],ticker="DEMO",account_ids=(1,))[0]
    assert account["currency"] == "CNY" and account["realized_pnl"] == 140


def test_kpi_and_legacy_navigation_open_integrated_closed_mode(qtbot):
    from portfolio.ui.main_window import MainWindow

    window = MainWindow(settings=False)
    qtbot.addWidget(window)
    window.drill_chart("metric", "3")
    assert window.pages.currentWidget() is window.holdings_page
    assert window.holdings_page.view_mode == "detail"
    assert window.holdings_page.status_combo.currentData() == "已清仓"
    assert window.holdings_page.closed_mode.currentIndex() == 0
    assert window.performance_page.selector.findText("清仓收益") == -1
    window.route_changed(4, "清仓收益")
    assert window.pages.currentWidget() is window.holdings_page
