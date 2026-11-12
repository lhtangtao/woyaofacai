from datetime import date
from decimal import Decimal
import sqlite3

from portfolio.domestic.holdings_query import daily_nav
from portfolio.domestic.nav_collection import SCHEMA
from portfolio.domestic.tiantian_positions import replay


def test_tiantian_stops_confirming_after_missing_statement():
    def trade(key, day, kind, shares, status="成功"):
        return dict(id=key, confirmation_date=day, transaction_type_raw=kind, confirmed_shares=shares,
                    parse_status="正常", confirmation_status=status, confirmed_amount="10", fee="0",
                    confirmed_nav="1", linked_bank_card="")

    rows = [
        trade("application", "2026-01-01", "买新基金", "10"),
        trade("confirm", "2026-01-01", "认购结果", "10"),
        trade("cash", "2026-01-02", "现金分红", None, None),
        trade("reinvest", "2026-01-02", "红利再投资", "1", None),
        trade("sell", "2026-01-04", "卖基金", "2"),
    ]
    periods = [("2026-01-01", "2026-01-02"), ("2026-01-04", "2026-01-04")]
    history, observed, buy, sell, dividend, issues = replay(rows, periods, {}, date(2026, 1, 4))
    assert history == [("2026-01-01", "10"), ("2026-01-02", "11"), ("2026-01-03", None), ("2026-01-04", None)]
    assert (observed, buy, sell, dividend) == tuple(map(Decimal, ("9", "10", "2", "10")))
    assert issues == [(None, "对账单缺档 2026-01-03 至 2026-01-03")]


def test_nav_cutoff_keeps_actual_date_and_flags_disagreement():
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)
        db.executemany("""INSERT INTO raw_fund_nav_daily
            (fund_code,nav_date,source,unit_nav,accumulated_nav,fetched_at,raw_payload,field_status)
            VALUES ('000001',?,?,?,'8','x','{}','{}')""", [
                ("2026-01-02", "AKShare/Eastmoney", "1.25"),
                ("2026-01-02", "second source", "1.30"),
                ("2026-01-05", "AKShare/Eastmoney", "2.50"),
            ])
        price = next(daily_nav(db, {"raw_fund_nav_daily"}, "000001", "2026-01-03"))
        assert (price["nav_date"], price["unit_nav"], price["accumulated_nav"]) == ("2026-01-02", "1.25", "8")
        assert price["nav_issue"]


def test_alipay_returns_use_confirmed_cash_and_reinvest_without_new_cost():
    from portfolio.domestic.fund_returns import confirmed_returns

    def trade(kind, shares, cash):
        return dict(transaction_type=kind, signed_shares=shares, confirmed_amount=cash, status="正常")

    trades = [trade("BUY", "10", "100"), trade("REINVEST", "2", None),
              trade("SELL", "-6", "70"), trade("CASH_DIVIDEND", "0", "3")]
    result = confirmed_returns(trades, Decimal("6"), Decimal("65"))
    assert Decimal(result["cost"]) == 50
    assert Decimal(result["realized_pnl"]) == 20
    assert Decimal(result["pnl"]) == Decimal(result["rate"]) == 38
    assert confirmed_returns(trades, Decimal("5"), Decimal("65")) == {}
    assert confirmed_returns([trade("TRANSFER_IN", "6", "50")], Decimal("6"), Decimal("65")) == {}


def test_tiantian_returns_use_net_cash_with_sub_cent_fee_ambiguity():
    from portfolio.domestic.fund_returns import tiantian_returns

    purchase = dict(transaction_type_raw="定时定额投资", confirmed_shares="4.64",
                    confirmed_amount="10.00", confirmed_nav="2.1521", fee="0.01",
                    confirmation_status="成功", parse_status="正常")
    result = tiantian_returns([purchase], Decimal("4.64"), Decimal("12"))
    assert Decimal(result["cost"]) == Decimal("10.00")
    assert Decimal(result["pnl"]) == 2
    assert tiantian_returns([dict(purchase, confirmed_amount="8")], Decimal("4.64"), Decimal("12")) == {}


def test_replay_carries_shares_and_applies_split_once():
    from portfolio.domestic.calculate import split_quantity

    buy = dict(id="buy", confirmation_date="2024-01-01", transaction_type_raw="买基金",
               confirmed_shares="1000", confirmed_amount="1000", confirmation_status="成功", parse_status="正常")
    periods = [("2024-01-01", "2026-01-02")]
    horizon = date(2026, 1, 2)
    assert replay([buy], periods, {}, horizon)[0][-1] == ("2026-01-02", "1000")
    assert replay([buy], periods, {"2026-01-01": ["1:2"]}, horizon)[0][-1] == ("2026-01-02", "2000")
    adjustment = dict(buy, id="adjust", confirmation_date="2026-01-01", transaction_type_raw="份额调增")
    assert replay([buy, adjustment], periods, {"2026-01-01": ["1:2"]}, horizon)[0][-1] == ("2026-01-02", "2000")
    assert split_quantity(Decimal("1000"), ["1:2"], Decimal("900")) is None


def test_same_day_snapshot_difference_and_gap_anchor():
    buy = dict(id="buy", confirmation_date="2025-01-01", transaction_type_raw="买基金",
               confirmed_shares="1180", confirmed_amount="1000", confirmation_status="成功", parse_status="正常")
    snapshot = date(2025, 1, 3)
    horizon = date(2025, 1, 4)
    anchors = {snapshot: Decimal("1200")}
    history, observed, *_, issues = replay([buy], [("2025-01-01","2025-01-04")], {}, horizon, anchors=anchors)
    assert observed == 1180 and history[-1][1] is None and "差异 20" in issues[0][1]
    history, *_ = replay([buy], [("2025-01-01","2025-01-01"),("2025-01-03","2025-01-04")],
                         {}, horizon, anchors=anchors)
    assert history[-3:] == [("2025-01-02", None), ("2025-01-03", "1200"), ("2025-01-04", "1200")]
