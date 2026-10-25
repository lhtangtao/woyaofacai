from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from portfolio.domain.analytics import Event, Snapshot
from portfolio.domain.models import QueryScope
from portfolio.engine.holdings import position_reports
from portfolio.engine.replay import replay


def test_aaox_uses_source_balance_without_retired_override():
    sale = replace(event(2, 'SELL', '99', '1165.23', '2026-10-05'), time='2026-10-05 14:37:00')
    s = snapshot([event(1, 'BUY', '90', '900', '2026-10-01'), sale])
    s.persons[1]['name'] = '个人'
    s.accounts[1]['broker_code'] = 'LONGBRIDGE'
    s.instruments[1]['symbol'] = 'AAOX'
    _, traces, _ = replay(s)
    assert traces[2][0] == -9
    assert not any(i.rule == 'USER_QUANTITY_OVERRIDE' for i in s.issues)
    s.events[0] = replace(s.events[0], quantity=D(99))
    s.issues.clear()
    assert replay(s)[1][2][0] == 0
    assert not any(i.rule == 'DQ001' for i in s.issues)


@pytest.mark.parametrize('extra', ['0.00000000000002', '0.49', '0.5'])
def test_user_approved_negative_share_tolerance_keeps_cash_and_cost(extra):
    s = snapshot([event(1, 'BUY', '10', '1000'), event(2, 'SELL', str(D(10) + D(extra)), '150')])
    p, traces, _ = replay(s)
    assert p[(1, 1, 1)].quantity == 0
    assert traces[2] == (D(0), D(850), None)
    assert not any(i.rule == 'DQ001' for i in s.issues)
    assert s.events[1].quantity == D(10) + D(extra)


def test_tolerance_does_not_hide_larger_shortfall_or_legitimate_fraction():
    s = snapshot([event(1, 'SELL', '0.50000001', '10')])
    p, _, _ = replay(s)
    assert p[(1, 1, 1)].quantity == D('-0.50000001')
    assert any(i.rule == 'DQ001' for i in s.issues)
    s = snapshot([event(1, 'BUY', '0.2', '10')])
    p, _, _ = replay(s)
    assert p[(1, 1, 1)].quantity == D('0.2') and p[(1, 1, 1)].cost == D(50)


def snapshot(events, actions=(), baselines=()):
    return Snapshot(QueryScope(date(2026, 2, 2)), list(events),
                    {1: {"symbol": "TEST", "category": "大盘", "currency": "USD", "market": "US"}},
                    {1: {"broker_code": "BBAE"}}, {1: {"name": "甲", "enabled": 1}, 2: {"name": "乙", "enabled": 1}},
                    list(actions), list(baselines), [])


def event(id, side, qty, amount, day="2026-01-02", person=1, kind="MARKET_TRADE", **meta):
    return Event(id, id, person, 1, 1, day, day + " 10:00:00", id, side, D(qty),
                 D(amount) if amount is not None else None, None, kind, meta)


def test_negative_net_cost_and_reopening_never_reset_lifetime():
    s = snapshot([event(1, "BUY", "10", "100"), event(2, "SELL", "9", "180"),
                  event(3, "SELL", "1", "20"), event(4, "BUY", "2", "10")])
    positions, traces, _ = replay(s)
    p = positions[(1, 1, 1)]
    assert p.quantity == 2 and p.net == -90 and p.cost == -45
    assert traces[2] == (D(1), D(-80), D(-80))
    assert not position_reports(s, positions)["清仓收益"].rows


def test_split_before_same_day_trade_and_unchanged_cash():
    action = dict(id=1, instrument_id=1, effective_date="2026-01-03", broker_scope="BBAE", ratio_numerator=2, ratio_denominator=1)
    s = snapshot([event(1, "BUY", "10", "100"), event(2, "SELL", "5", "40", "2026-01-03")], [action])
    p, _, _ = replay(s)
    assert p[(1, 1, 1)].quantity == 15 and p[(1, 1, 1)].buy_quantity == 20
    assert p[(1, 1, 1)].net == 60


def test_reverse_split_fractional_shares_no_round_to_four_places():
    a = dict(id=1, instrument_id=1, effective_date="2026-01-03", broker_scope="ALL", ratio_numerator=1, ratio_denominator=10)
    s = snapshot([event(1, "BUY", "0.00000001", "1")], [a])
    assert replay(s)[0][(1, 1, 1)].quantity == D("0.000000001")


def test_transfer_conserves_owner_quantity_and_cost():
    s = snapshot([event(1, "BUY", "10", "100"),
                  event(2, "TRANSFER_OUT", "3", "30", kind="OWNER_TRANSFER", amount_basis="TRANSFER_VALUE"),
                  event(3, "TRANSFER_IN", "3", "30", person=2, kind="OWNER_TRANSFER", amount_basis="TRANSFER_VALUE")])
    p, _, _ = replay(s)
    assert sum(x.quantity for x in p.values()) == 10 and sum(x.net for x in p.values()) == 100
    assert p[(2, 1, 1)].net == 30


def test_unknown_transfer_cost_remains_missing():
    s = snapshot([event(1, "TRANSFER_IN", "3", None, kind="OWNER_TRANSFER")])
    p, _, _ = replay(s)
    assert p[(1, 1, 1)].net is None


def test_negative_position_reported_at_event_and_missing_price_not_zero():
    s = snapshot([event(1, "SELL", "3", "30"), event(2, "BUY", "5", "40")])
    p, _, _ = replay(s)
    assert any(i.rule == "DQ001" for i in s.issues)
    r = position_reports(s, p)["当前持仓"].rows[0]
    assert r.values[5] is None and r.values[10] is None


def test_closed_gain_lifetime_and_no_current_position():
    s = snapshot([event(1, "BUY", "3", "30"), event(2, "SELL", "3", "35")])
    p, _, _ = replay(s)
    reports = position_reports(s, p)
    assert not reports["当前持仓"].rows
    assert reports["清仓收益"].rows[0].values[7] == 5
