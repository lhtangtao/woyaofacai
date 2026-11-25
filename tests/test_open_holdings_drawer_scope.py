import sqlite3
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

from PySide6.QtWidgets import QWidget

from portfolio.domain.models import QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.holdings_management_service import HoldingsManagementService
from portfolio.ui.holdings_page import OPEN_HOLDINGS_COLUMNS, HoldingsPage


def test_open_holdings_row_click_scopes_drawer_to_channel(demo_path, monkeypatch, qtbot):
    """验证持仓中点击特定渠道（如嘉信）的标的行时，右侧抽屉仅展示该渠道的数据，不混入其他渠道。"""
    assert "详情" not in OPEN_HOLDINGS_COLUMNS
    monkeypatch.setattr(HoldingsManagementService, "get_latest_quotes_map", staticmethod(lambda: {}))
    with sqlite3.connect(demo_path) as db:
        db.execute("DELETE FROM person_operations")
        db.execute("DELETE FROM broker_trades")
        db.execute("UPDATE broker_accounts SET broker_code='LONGBRIDGE', display_name='长桥' WHERE id=1")
        db.execute("UPDATE broker_accounts SET broker_code='SCHWAB', display_name='嘉信' WHERE id=2")
        db.execute("UPDATE instruments SET symbol='TQQQ', name='TQQQ' WHERE id=1")

        # 同一人(person_id=1)在长桥买入 260 股 TQQQ，在嘉信买入 90 股 TQQQ
        entries = [
            (1, 1, 1, "BUY", 260, 41, "2026-01-10"),
            (1, 2, 1, "BUY", 90, 67, "2026-02-15"),
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
            db.execute(
                """INSERT INTO broker_trades
                (id,trade_key,account_id,instrument_id,record_granularity,side,business_date,book_time,
                 quantity_e8,price_e8,gross_amount_e8,fees_e8,net_amount_e8,calculation_amount_e8,amount_basis,currency,source_record_id)
                VALUES(?,?,?,?,'FILL',?,?,?,?,?,?,0,?,?,'GROSS','USD',1)""",
                (
                    n, f"tr-{n}", acc, inst, side, day, f"{day} 10:00:00",
                    int(D(qty) * 10**8), int(D(price) * 10**8),
                    int(D(qty) * D(price) * 10**8),
                    -int(D(qty) * D(price) * 10**8),
                    int(D(qty) * D(price) * 10**8),
                ),
            )

    svc = AnalyticsService(demo_path)
    analytics = svc.calculate(QueryScope(date(2026, 11, 12)))

    host_widget = QWidget()
    host_widget.service = SimpleNamespace(path=demo_path)
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

    # 找到「嘉信 | TQQQ」所在行并点击
    schwab_row_idx = next(
        i for i, r in enumerate(page.detail_model.rows)
        if r.values[1] == "嘉信" and r.values[2] == "TQQQ"
    )
    page._on_detail_row_clicked(page.detail_model.index(schwab_row_idx, 0))

    # 1. 顶部持仓数量应仅为嘉信的 90 股（而非全部渠道合计 350 股）
    assert page.drawer.m_qty.val_lbl.text() == "90 股"

    # 2. 交易记录 Tab 应仅包含嘉信渠道的 1 笔交易，不包含长桥
    assert len(page.drawer.raw_trades_data) == 1
    assert page.drawer.raw_trades_data[0]["channel"] == "嘉信"
    assert page.drawer.raw_trades_data[0]["quantity"] == 90.0

    # 3. 持仓分析（持仓与成本轨迹）应仅包含嘉信的 90 股轨迹（2026-02-15），不含长桥（2026-01-10 的 260 股）
    assert page.drawer.holdings_canvas.canvas._dates == ["2026-02-15"]
    assert page.drawer.holdings_canvas.canvas._quantities == [90.0]


def test_closed_holdings_pnl_quick_filter_buttons(demo_path, monkeypatch, qtbot):
    """验证已清仓明细顶部具备『全部 / 盈利 / 亏损』快捷切换开关，且计数与过滤准确联动。"""
    monkeypatch.setattr(HoldingsManagementService, "get_latest_quotes_map", staticmethod(lambda: {}))
    with sqlite3.connect(demo_path) as db:
        db.execute("DELETE FROM person_operations")
        db.execute("DELETE FROM broker_trades")
        db.execute("UPDATE broker_accounts SET broker_code='SCHWAB', display_name='嘉信' WHERE id=1")
        db.execute("UPDATE instruments SET symbol='WIN1', name='WIN1' WHERE id=1")
        for idx, sym in ((2, "LOSS1"), (3, "LOSS2")):
            db.execute(
                "INSERT INTO instruments(id,market,symbol,name,currency,asset_type,category) VALUES(?,'US',?,?,'USD','STOCK','正股')",
                (idx, sym, sym),
            )

        # WIN1: 买 10 @ 100, 卖 10 @ 120 (+200)
        # LOSS1: 买 10 @ 100, 卖 10 @ 80 (-200)
        # LOSS2: 买 10 @ 50, 卖 10 @ 40 (-100)
        entries = [
            (1, 1, 1, "BUY", 10, 100, "2026-01-05"),
            (1, 1, 1, "SELL", 10, 120, "2026-01-10"),
            (1, 1, 2, "BUY", 10, 100, "2026-02-05"),
            (1, 1, 2, "SELL", 10, 80, "2026-02-10"),
            (1, 1, 3, "BUY", 10, 50, "2026-03-05"),
            (1, 1, 3, "SELL", 10, 40, "2026-03-10"),
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
    analytics = svc.calculate(QueryScope(date(2026, 11, 12)))

    host_widget = QWidget()
    host_widget.service = SimpleNamespace(path=demo_path)
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

    assert not page.closed_table_header.isHidden()
    assert page.open_table_header.isHidden()
    assert page._closed_pnl_btns[""].text() == "全部 (3)"
    assert page._closed_pnl_btns["win"].text() == "盈利 (1)"
    assert page._closed_pnl_btns["loss"].text() == "亏损 (2)"
    assert len(page.detail_model.rows) == 3

    page._closed_pnl_btns["win"].click()
    assert page._closed_pnl_filter == "win"
    assert page._closed_pnl_btns["win"].isChecked()
    assert len(page.detail_model.rows) == 1
    assert page.detail_model.rows[0].metadata["ticker"] == "WIN1"

    page._closed_pnl_btns["loss"].click()
    assert page._closed_pnl_filter == "loss"
    assert page._closed_pnl_btns["loss"].isChecked()
    assert len(page.detail_model.rows) == 2
    assert {r.metadata["ticker"] for r in page.detail_model.rows} == {"LOSS1", "LOSS2"}

    page._closed_pnl_btns[""].click()
    assert page._closed_pnl_filter == ""
    assert page._closed_pnl_btns[""].isChecked()
    assert len(page.detail_model.rows) == 3
