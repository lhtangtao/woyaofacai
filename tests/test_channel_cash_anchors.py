"""券商实时现金 + 手动现金锚点推演及资产总览展示测试。"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from portfolio.domain.models import QueryScope


def to_e8(val: Decimal) -> int:
    return int(val * Decimal("100000000"))
from portfolio.services.channel_cash import (
    compute_anchored_channel_cash,
    is_cash_visible_scope,
    resolve_channel_cash_details,
)
from portfolio.services.quote_store import QuoteStore


def test_quote_store_manual_cash_anchors_crud(tmp_path):
    store = QuoteStore(tmp_path / "live_quotes.sqlite3")
    assert store.load_cash_anchors() == {}

    store.save_cash_anchor("BBAE", "2026-11-23", Decimal("248.00"), note="test")
    store.save_cash_anchor("FIRSTRADE", "2026-11-23", Decimal("1377.41"), note="test2")
    anchors = store.load_cash_anchors()
    assert anchors["BBAE"]["anchor_date"] == "2026-11-23"
    assert anchors["BBAE"]["amount"] == Decimal("248.00")
    assert anchors["FIRSTRADE"]["amount"] == Decimal("1377.41")

    # 覆盖更新
    store.save_cash_anchor("BBAE", "2026-11-25", Decimal("300.50"))
    assert store.load_cash_anchors()["BBAE"]["amount"] == Decimal("300.50")

    # 删除
    store.delete_cash_anchor("BBAE")
    assert "BBAE" not in store.load_cash_anchors()


def test_anchored_cash_rolls_forward_trades_and_events():
    """验证锚点日期之后的买入/卖出成交（含手续费）及现金事件能精确推算现金。"""
    snapshot = SimpleNamespace(
        scope=QueryScope(as_of=date(2026, 11, 30), person_ids=(1,)),
        accounts={
            10: {"broker_code": "BBAE"},
            11: {"broker_code": "FIRSTRADE"},
        },
        extras={
            "broker_trades": [
                # 锚点当天成交不重复计入
                {
                    "account_id": 10,
                    "side": "BUY",
                    "business_date": "2026-11-23",
                    "calculation_amount_e8": to_e8(Decimal("100.00")),
                    "fees_e8": to_e8(Decimal("1.00")),
                    "net_amount_e8": None,
                    "record_state": "ACTIVE",
                    "accounting_role": "ACCOUNT_LEDGER",
                },
                # 锚点之后买入：减少 100 + 1 = 101
                {
                    "account_id": 10,
                    "side": "BUY",
                    "business_date": "2026-11-25",
                    "calculation_amount_e8": to_e8(Decimal("100.00")),
                    "fees_e8": to_e8(Decimal("1.00")),
                    "net_amount_e8": None,
                    "record_state": "ACTIVE",
                    "accounting_role": "ACCOUNT_LEDGER",
                },
                # 锚点之后卖出：增加 250 - 1 = 249
                {
                    "account_id": 10,
                    "side": "SELL",
                    "business_date": "2026-11-28",
                    "calculation_amount_e8": to_e8(Decimal("250.00")),
                    "fees_e8": to_e8(Decimal("1.00")),
                    "net_amount_e8": None,
                    "record_state": "ACTIVE",
                    "accounting_role": "ACCOUNT_LEDGER",
                },
            ],
            "cash_events": [
                # 分红到账 +5.50
                {
                    "account_id": 10,
                    "event_type": "DIVIDEND",
                    "business_date": "2026-11-29",
                    "amount_e8": to_e8(Decimal("5.50")),
                    "record_state": "ACTIVE",
                    "accounting_role": "ACCOUNT_LEDGER",
                }
            ],
        },
    )
    anchor = {
        "channel": "BBAE",
        "anchor_date": "2026-11-23",
        "amount": Decimal("248.00"),
    }
    res = compute_anchored_channel_cash(snapshot, anchor)
    assert res is not None
    # 248.00 - 101.00 + 249.00 + 5.50 = 401.50
    assert res["amount"] == Decimal("401.50")
    assert res["trades_applied"] == 2
    assert res["events_applied"] == 1
    assert res["source"] == "锚点推算"


def test_anchored_cash_intraday_second_precision():
    """验证精确到时分秒的锚点：同一天锚点前的成交不重复计入，锚点后的成交精准计入。"""
    snapshot = SimpleNamespace(
        scope=QueryScope(as_of=date(2026, 11, 23), person_ids=(1,)),
        accounts={10: {"broker_code": "BBAE"}},
        extras={
            "broker_trades": [
                # 北京时间 18:30:00（UTC 10:30:00）—— 在 18:45:00 锚点之前，不应计入
                {
                    "account_id": 10,
                    "side": "BUY",
                    "business_date": "2026-11-23",
                    "executed_at_utc": "2026-11-23T10:30:00+00:00",
                    "book_time": "2026-11-23 10:30:00",
                    "calculation_amount_e8": to_e8(Decimal("50.00")),
                    "fees_e8": to_e8(Decimal("1.00")),
                    "net_amount_e8": None,
                    "record_state": "ACTIVE",
                    "accounting_role": "ACCOUNT_LEDGER",
                },
                # 北京时间 23:10:00（UTC 15:10:00）—— 在 18:45:00 锚点之后，必须计入
                {
                    "account_id": 10,
                    "side": "BUY",
                    "business_date": "2026-11-23",
                    "executed_at_utc": "2026-11-23T15:10:00+00:00",
                    "book_time": "2026-11-23 15:10:00",
                    "calculation_amount_e8": to_e8(Decimal("100.00")),
                    "fees_e8": to_e8(Decimal("1.00")),
                    "net_amount_e8": None,
                    "record_state": "ACTIVE",
                    "accounting_role": "ACCOUNT_LEDGER",
                },
            ],
            "cash_events": [],
        },
    )
    anchor = {
        "channel": "BBAE",
        "anchor_date": "2026-11-23 18:45:00",
        "amount": Decimal("248.00"),
    }
    res = compute_anchored_channel_cash(snapshot, anchor)
    assert res is not None
    # 248.00 - (100.00 + 1.00) = 147.00
    assert res["amount"] == Decimal("147.00")
    assert res["trades_applied"] == 1


def test_cash_visibility_restricted_to_personal_and_all():
    """仅「我个人(1)」和「全部人员(None)」展示现金，其他人员一律不展示。"""
    assert is_cash_visible_scope(QueryScope(as_of=date(2026, 11, 23), person_ids=None)) is True
    assert is_cash_visible_scope(QueryScope(as_of=date(2026, 11, 23), person_ids=(1,))) is True
    assert is_cash_visible_scope(QueryScope(as_of=date(2026, 11, 23), person_ids=(2,))) is False
    assert is_cash_visible_scope(QueryScope(as_of=date(2026, 11, 23), person_ids=(2, 3))) is False

    base_extras = {
        "channel_cash": {"LONGBRIDGE": Decimal("-936.76"), "SCHWAB": Decimal("159.19")},
        "manual_cash_anchors": {
            "BBAE": {"channel": "BBAE", "anchor_date": "2026-11-23", "amount": Decimal("248.00")},
            "FIRSTRADE": {"channel": "FIRSTRADE", "anchor_date": "2026-11-23", "amount": Decimal("1377.41")},
        },
    }
    other_snap = SimpleNamespace(scope=QueryScope(as_of=date(2026, 11, 23), person_ids=(2,)), extras=base_extras)
    assert resolve_channel_cash_details(other_snap) == {}

    personal_snap = SimpleNamespace(scope=QueryScope(as_of=date(2026, 11, 23), person_ids=(1,)), extras=base_extras)
    details = resolve_channel_cash_details(personal_snap)
    assert set(details.keys()) == {"LONGBRIDGE", "SCHWAB", "BBAE", "FIRSTRADE"}
    assert details["LONGBRIDGE"]["source"] == "API实时"
    assert details["BBAE"]["source"] == "锚点推算"
    assert details["FIRSTRADE"]["amount"] == Decimal("1377.41")


def test_cash_summary_banner_ui_offscreen():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from portfolio.ui.dashboard_page import CashSummaryBanner

    app = QApplication.instance() or QApplication([])
    banner = CashSummaryBanner()

    extras = {
        "channel_cash": {"LONGBRIDGE": Decimal("-936.76"), "SCHWAB": Decimal("159.19")},
        "manual_cash_anchors": {
            "BBAE": {"channel": "BBAE", "anchor_date": "2026-11-23", "amount": Decimal("248.00")},
            "FIRSTRADE": {"channel": "FIRSTRADE", "anchor_date": "2026-11-23", "amount": Decimal("1377.41")},
        },
    }
    personal_analytics = SimpleNamespace(scope=QueryScope(as_of=date(2026, 11, 23), person_ids=(1,)), extras=extras)
    banner.update_cash(personal_analytics, Decimal("45000.00"))
    assert not banner.isHidden()
    assert "847.84" in banner.lbl_total_cash.text()
    assert "45,847.84" in banner.lbl_total_equity.text()

    other_analytics = SimpleNamespace(scope=QueryScope(as_of=date(2026, 11, 23), person_ids=(2,)), extras=extras)
    banner.update_cash(other_analytics, Decimal("10000.00"))
    assert banner.isHidden()
    app.processEvents()


def test_fetch_longbridge_cash_includes_frozen_and_settling(monkeypatch):
    """验证长桥现金口径包含可用、挂单冻结与待结算美元现金之和。"""
    import sys
    from types import ModuleType

    from portfolio.services.channel_cash import fetch_longbridge_cash

    fake_usd = SimpleNamespace(
        currency="USD",
        available_cash=Decimal("1522.61"),
        frozen_cash=Decimal("2158.23"),
        settling_cash=Decimal("100.00"),
    )
    fake_hkd = SimpleNamespace(
        currency="HKD",
        available_cash=Decimal("500.00"),
        frozen_cash=Decimal("0"),
        settling_cash=Decimal("0"),
    )

    class _FakeConfig:
        @classmethod
        def from_apikey(cls, app_key, app_secret, access_token):
            return cls()

    class _FakeTradeContext:
        def __init__(self, cfg):
            pass

        def account_balance(self):
            return [SimpleNamespace(cash_infos=[fake_hkd, fake_usd])]

    fake_lb_mod = ModuleType("longbridge.openapi")
    fake_lb_mod.Config = _FakeConfig
    fake_lb_mod.TradeContext = _FakeTradeContext
    monkeypatch.setitem(sys.modules, "longbridge.openapi", fake_lb_mod)
    monkeypatch.setattr(
        "portfolio.sync.longbridge_credentials.load_longbridge_credentials",
        lambda: ("tok", "key", "sec"),
    )

    assert fetch_longbridge_cash() == Decimal("3780.84")
