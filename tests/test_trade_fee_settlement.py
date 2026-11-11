import json
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from portfolio.domain.models import Ledger, QueryScope
from portfolio.services.trade_center import KEYS, TradeCenterService
from portfolio.sync.fee_api import fetch_missing
from portfolio.sync.trade_fees import (
    audit_recent_fees,
    load_evidence,
    longbridge_evidence,
    refresh_fees,
    store_evidence,
)
from portfolio.ui.trade_center_page import TradeCenterPage
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget


def _setup_multi_person_longbridge_db(db_path):
    """在 demo_path 中构造一条多人分摊的长桥 LITX 成交记录。"""
    today = date.today()
    recent_day = (today - timedelta(days=2)).isoformat()
    old_day = (today - timedelta(days=20)).isoformat()
    with sqlite3.connect(db_path) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("DELETE FROM person_operations")
        c.execute("DELETE FROM broker_trades")
        c.execute("DELETE FROM broker_accounts")
        c.execute("DELETE FROM instruments")
        c.execute("DELETE FROM persons WHERE id > 2")
        c.execute("INSERT OR IGNORE INTO persons(id, name, enabled) VALUES (3, '张薇', 1)")
        c.execute(
            "INSERT INTO broker_accounts(id, broker_code, account_key, display_name, base_currency, book_timezone, day_cutoff, enabled, source_record_id) "
            "VALUES (1, 'LONGBRIDGE', 'LEGACY_DEFAULT', '长桥', 'USD', 'Asia/Shanghai', '08:00', 1, 1)"
        )
        c.execute(
            "INSERT INTO instruments(id, market, symbol, name, currency, asset_type, category) "
            "VALUES (1, 'US', 'LITX', 'LITX', 'USD', 'ETF', '杠杆')"
        )
        # trade 1: 最近 2 天，120 股三人分摊
        c.execute(
            """INSERT INTO broker_trades
               (id, trade_key, account_id, instrument_id, broker_order_id, record_granularity, side,
                business_date, book_time, quantity_e8, price_e8, gross_amount_e8, fees_e8,
                calculation_amount_e8, amount_basis, currency, record_state, accounting_role, source_record_id)
               VALUES (1, 'lb-recent', 1, 1, 'OID_RECENT', 'ORDER_TOTAL', 'BUY', ?, ?, 12000000000, 858700000,
                       103044000000, NULL, 103044000000, 'GROSS', 'USD', 'ACTIVE', 'ACCOUNT_LEDGER', 1)""",
            (recent_day, recent_day + " 14:38:49"),
        )
        # trade 2: 20 天前已结算订单，50 股单人
        c.execute(
            """INSERT INTO broker_trades
               (id, trade_key, account_id, instrument_id, broker_order_id, record_granularity, side,
                business_date, book_time, quantity_e8, price_e8, gross_amount_e8, fees_e8,
                calculation_amount_e8, amount_basis, currency, record_state, accounting_role, source_record_id)
               VALUES (2, 'lb-old', 1, 1, 'OID_OLD', 'ORDER_TOTAL', 'BUY', ?, ?, 5000000000, 900000000,
                       45000000000, NULL, 45000000000, 'GROSS', 'USD', 'ACTIVE', 'ACCOUNT_LEDGER', 1)""",
            (old_day, old_day + " 10:00:00"),
        )
        shares = [
            (1, 1, 1, recent_day, recent_day + " 14:38:49", 1, 1026000000),   # 10.26
            (2, 3, 1, recent_day, recent_day + " 14:38:49", 2, 4000000000),   # 40.00
            (3, 2, 1, recent_day, recent_day + " 14:38:49", 3, 6974000000),   # 69.74
            (4, 1, 2, old_day, old_day + " 10:00:00", 1, 5000000000),         # 50.00
        ]
        for oid, pid, bt_id, bdate, btime, seq, qty_e8 in shares:
            c.execute(
                """INSERT INTO person_operations
                   (id, operation_key, person_id, account_id, instrument_id, broker_trade_id,
                    operation_kind, side, business_date, book_time, sequence_no, quantity_e8,
                    price_e8, amount_e8, amount_basis, currency, link_status, source_record_id)
                   VALUES (?, ?, ?, 1, 1, ?, 'MARKET_TRADE', 'BUY', ?, ?, ?, ?, 858700000, ?, 'GROSS', 'USD', 'LINKED', 1)""",
                (oid, f"op-{oid}", pid, bt_id, bdate, btime, seq, qty_e8, qty_e8 * 9),
            )
    return recent_day, old_day


def test_longbridge_unsettled_zero_upgrades_and_allocates_next_day(demo_path):
    """交易当日 0 费空明细应记为待结算，次日结算后可安全升级并按多人份额精确分摊。"""
    _setup_multi_person_longbridge_db(demo_path)

    provisional_orders = {
        "OID_RECENT": {
            "symbol": "LITX",
            "side": "BUY",
            "executed_quantity": "120",
            "executed_price": "8.587",
            "updated_booktime": "NaT",
            "charge_detail": {
                "total_amount": "0.00",
                "currency": "USD",
                "items": [
                    {"code": "ChargeCategoryCode.Broker", "name": "Broker Fees", "fees": []},
                    {"code": "ChargeCategoryCode.Third", "name": "Third-party Fees", "fees": []},
                ],
            },
        },
        "OID_OLD": {
            "symbol": "LITX",
            "side": "BUY",
            "executed_quantity": "50",
            "executed_price": "9.00",
            "updated_booktime": "NaT",
            "charge_detail": {
                "total_amount": "1.16",
                "currency": "USD",
                "items": [
                    {
                        "code": "ChargeCategoryCode.Broker",
                        "name": "Broker Fees",
                        "fees": [{"code": "PlatformFee", "amount": "1.00", "currency": "USD"}],
                    },
                    {
                        "code": "ChargeCategoryCode.Third",
                        "name": "Third-party Fees",
                        "fees": [{"code": "ClearingFee", "amount": "0.16", "currency": "USD"}],
                    },
                ],
            },
        },
    }

    with sqlite3.connect(demo_path) as c:
        c.row_factory = sqlite3.Row
        store_evidence(c, longbridge_evidence(c, provisional_orders, {"1": "OID_RECENT", "2": "OID_OLD"}))
        summary1 = refresh_fees(c)
        assert summary1["pending_settlement_trades"] == 1
        assert summary1["known_trades"] == 1

        bt1 = c.execute("SELECT fees_e8 FROM broker_trades WHERE id=1").fetchone()["fees_e8"]
        bfr1 = c.execute("SELECT status, fees_e8 FROM broker_fee_records WHERE broker_trade_id=1").fetchone()
        assert bt1 is None
        assert bfr1["status"] == "PENDING_SETTLEMENT"
        assert bfr1["fees_e8"] is None

        audit1 = audit_recent_fees(c, days=7)
        assert audit1["total_trades"] == 1
        assert audit1["pending_settlement_trades"] == 1
        assert audit1["multi_person_orders"] == 1

    # 检查 TradeCenter 在存在待结算手续费时保留 known_fees 且不丢失已确认汇总
    page = TradeCenterService(demo_path).page(
        QueryScope(as_of=date.today(), ledger=Ledger.PERSON),
        {"page": 1, "size": 20},
    )
    cur = page["stats"]["currencies"][0]
    assert cur["fees"] is None
    assert cur["known_fees"] == Decimal("1.16")
    assert cur["unknown_fees"] == 3  # 最近一单3条分摊记录待结算

    # 次日券商完成费用结算：返回 1.37 美元真实明细，应顺利升级覆盖并分摊给 3 人
    settled_orders = {
        "OID_RECENT": {
            **provisional_orders["OID_RECENT"],
            "charge_detail": {
                "total_amount": "1.37",
                "currency": "USD",
                "items": [
                    {
                        "code": "ChargeCategoryCode.Broker",
                        "name": "Broker Fees",
                        "fees": [{"code": "PlatformFee", "amount": "1.00", "currency": "USD"}],
                    },
                    {
                        "code": "ChargeCategoryCode.Third",
                        "name": "Third-party Fees",
                        "fees": [{"code": "ClearingFee", "amount": "0.37", "currency": "USD"}],
                    },
                ],
            },
        }
    }

    with sqlite3.connect(demo_path) as c:
        c.row_factory = sqlite3.Row
        store_evidence(c, longbridge_evidence(c, settled_orders, {"1": "OID_RECENT"}))
        summary2 = refresh_fees(c)
        assert summary2["pending_settlement_trades"] == 0
        assert summary2["known_trades"] == 2

        ptf_rows = c.execute(
            "SELECT operation_id, fees_e8, status FROM person_trade_fees WHERE broker_trade_id=1 ORDER BY operation_id"
        ).fetchall()
        assert len(ptf_rows) == 3
        assert all(r["status"] == "KNOWN" and r["fees_e8"] > 0 for r in ptf_rows)
        assert sum(r["fees_e8"] for r in ptf_rows) == 137000000

        audit2 = audit_recent_fees(c, days=7)
        assert audit2["known_trades"] == 1
        assert audit2["pending_settlement_trades"] == 0
        assert audit2["allocation_mismatches"] == 0

        # 已结算的非零费用若再被恶意或异常改成其他值，仍应触发冲突保护
        conflicting = {
            "OID_RECENT": {
                **settled_orders["OID_RECENT"],
                "charge_detail": {
                    "total_amount": "2.50",
                    "currency": "USD",
                    "items": settled_orders["OID_RECENT"]["charge_detail"]["items"],
                },
            }
        }
        with pytest.raises(ValueError, match="证据发生冲突"):
            store_evidence(c, longbridge_evidence(c, conflicting, {"1": "OID_RECENT"}))


def test_fetch_missing_rechecks_unsettled_and_skips_settled(demo_path, monkeypatch):
    """fetch_missing 应跳过已结算老订单，只重新查询未结算/近期 0 费长桥订单。"""
    _setup_multi_person_longbridge_db(demo_path)

    initial = {
        "OID_RECENT": {
            "symbol": "LITX",
            "side": "BUY",
            "executed_quantity": "120",
            "executed_price": "8.587",
            "updated_booktime": "NaT",
            "charge_detail": {
                "total_amount": "0.00",
                "currency": "USD",
                "items": [
                    {"code": "ChargeCategoryCode.Broker", "name": "Broker Fees", "fees": []},
                    {"code": "ChargeCategoryCode.Third", "name": "Third-party Fees", "fees": []},
                ],
            },
        },
        "OID_OLD": {
            "symbol": "LITX",
            "side": "BUY",
            "executed_quantity": "50",
            "executed_price": "9.00",
            "updated_booktime": "NaT",
            "charge_detail": {
                "total_amount": "1.16",
                "currency": "USD",
                "items": [
                    {
                        "code": "ChargeCategoryCode.Broker",
                        "name": "Broker Fees",
                        "fees": [{"code": "PlatformFee", "amount": "1.00", "currency": "USD"}],
                    }
                ],
            },
        },
    }
    with sqlite3.connect(demo_path) as c:
        c.row_factory = sqlite3.Row
        store_evidence(c, longbridge_evidence(c, initial, {"1": "OID_RECENT", "2": "OID_OLD"}))

    queried_order_ids = []

    class FakeContext:
        def __init__(self, _cfg):
            pass

        def history_orders(self, **kwargs):
            return []

        def order_detail(self, order_id):
            queried_order_ids.append(order_id)
            return SimpleNamespace(
                order_id=order_id,
                symbol="LITX.US",
                side="OrderSide.Buy",
                executed_quantity="120",
                executed_price="8.587",
                updated_at="NaT",
                status="OrderStatus.Filled",
                charge_detail=SimpleNamespace(
                    total_amount="1.37",
                    currency="USD",
                    items=[
                        SimpleNamespace(
                            code="ChargeCategoryCode.Broker",
                            name="Broker Fees",
                            fees=[SimpleNamespace(code="PlatformFee", amount="1.00", currency="USD")],
                        ),
                        SimpleNamespace(
                            code="ChargeCategoryCode.Third",
                            name="Third-party Fees",
                            fees=[SimpleNamespace(code="ClearingFee", amount="0.37", currency="USD")],
                        ),
                    ],
                ),
            )

    import longbridge.openapi as lb_api
    import portfolio.services.live_quotes as lq

    monkeypatch.setattr(lq, "credentials", lambda _p: ("k", "s", "t"))
    monkeypatch.setattr(lb_api, "TradeContext", FakeContext)
    monkeypatch.setattr(lb_api, "Config", SimpleNamespace(from_apikey=lambda *a, **kw: object()))

    def persist(evidence):
        with sqlite3.connect(demo_path) as w:
            w.row_factory = sqlite3.Row
            store_evidence(w, evidence)

    with sqlite3.connect(demo_path) as r:
        r.row_factory = sqlite3.Row
        _, warnings = fetch_missing(r, load_evidence(r), persist, recent_days=7)

    assert not warnings
    assert queried_order_ids == ["OID_RECENT"]

    with sqlite3.connect(demo_path) as c:
        c.row_factory = sqlite3.Row
        summary = refresh_fees(c)
        assert summary["pending_settlement_trades"] == 0
        assert c.execute("SELECT fees_e8 FROM broker_trades WHERE id=1").fetchone()[0] == 137000000


def test_trade_center_page_pending_fee_display_and_tooltip(demo_path, qtbot):
    """验证 Mac 端交易明细页面对待结算订单提示与已核实手续费合计卡片展示。"""
    _setup_multi_person_longbridge_db(demo_path)
    provisional_orders = {
        "OID_RECENT": {
            "symbol": "LITX",
            "side": "BUY",
            "executed_quantity": "120",
            "executed_price": "8.587",
            "updated_booktime": "NaT",
            "charge_detail": {
                "total_amount": "0.00",
                "currency": "USD",
                "items": [
                    {"code": "ChargeCategoryCode.Broker", "name": "Broker Fees", "fees": []},
                    {"code": "ChargeCategoryCode.Third", "name": "Third-party Fees", "fees": []},
                ],
            },
        },
        "OID_OLD": {
            "symbol": "LITX",
            "side": "BUY",
            "executed_quantity": "50",
            "executed_price": "9.00",
            "updated_booktime": "NaT",
            "charge_detail": {
                "total_amount": "1.16",
                "currency": "USD",
                "items": [
                    {
                        "code": "ChargeCategoryCode.Broker",
                        "name": "Broker Fees",
                        "fees": [{"code": "PlatformFee", "amount": "1.00", "currency": "USD"}],
                    }
                ],
            },
        },
    }
    with sqlite3.connect(demo_path) as c:
        c.row_factory = sqlite3.Row
        store_evidence(c, longbridge_evidence(c, provisional_orders, {"1": "OID_RECENT", "2": "OID_OLD"}))
        refresh_fees(c)

    data = TradeCenterService(demo_path).page(
        QueryScope(as_of=date.today(), ledger=Ledger.PERSON),
        {"page": 1, "size": 20},
    )
    host = QWidget()
    page = TradeCenterPage(host)
    qtbot.addWidget(host)
    qtbot.addWidget(page)
    page.show_page(data)

    fee_col = KEYS.index("fees")
    model = page.table.finance_model
    assert model.data(model.index(0, fee_col), Qt.ItemDataRole.DisplayRole) == "—"
    assert "次日结算" in model.data(model.index(0, fee_col), Qt.ItemDataRole.ToolTipRole)
    assert "1.16" in page.cards[3].value.text()
    assert "3 笔待核实/结算" in page.cards[3].hint.text()
