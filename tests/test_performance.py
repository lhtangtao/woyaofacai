from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from test_replay import event, snapshot

from portfolio.domain.analytics import Price
from portfolio.domain.models import QueryScope
from portfolio.engine.dashboard import overview
from portfolio.engine.holdings import position_reports
from portfolio.engine.performance import performance
from portfolio.engine.scalping import scalping
from portfolio.services.analytics_service import AnalyticsService


def prices(s, values):
    s.prices = [Price(1, day, D(price), "DAILY_CLOSE", "RAW_CLOSE", 1, basis="RAW") for day, price in values]
    return s


def test_daily_example_and_month_direct_formula():
    s = prices(snapshot([event(1, "BUY", "10", "1000"), event(2, "BUY", "2", "210", "2026-01-03"),
                         event(3, "SELL", "3", "345", "2026-01-03")]), [("2026-01-02", "100"), ("2026-01-03", "110")])
    positions, _, reports, _ = performance(s)
    second = reports["每日持仓收益"].rows[1].values
    assert len(second) == 23 and second[8] == 9 and second[19] == 125
    monthly = reports["每月持仓收益"].rows[0].values
    assert len(monthly) == 24 and monthly[20] == 125
    reports.update(position_reports(s, positions))
    reports["每月微操收益"] = scalping(s)[0]
    metrics, additions, latest = overview(s, reports)
    assert len(metrics) == 18 and latest == "2026-01-03"
    assert metrics[7].value == 125 and metrics[6].value == 0
    assert additions["季度矩阵"].rows[0].values[5] is None


def test_round_trip_excluded_only_from_compat_net_investment():
    s = prices(snapshot([event(1, "BUY", "1", "100"), event(2, "SELL", "1", "110", "2026-01-03")]), [("2026-01-02", "100"), ("2026-01-03", "110")])
    reports = performance(s)[2]
    assert not reports["月度净投入明细"].rows
    assert reports["完整交易净投入"].rows[0].values[8] == -10


def test_missing_history_never_uses_future_or_latest_and_recovers_next_day():
    s = prices(snapshot([event(1, "BUY", "10", "1000")]), [("2026-01-02", "100"), ("2026-01-04", "110"), ("2026-01-05", "112")])
    # A second security's price date creates a real calendar date with missing TEST.
    s.prices.append(Price(99, "2026-01-03", D(10), "DAILY_CLOSE", "RAW_CLOSE", 2, basis="RAW"))
    rows = performance(s)[2]["每日持仓收益"].rows
    assert [r.values[19] for r in rows] == [0, None, None, 20]


def test_split_changes_quantity_not_daily_profit():
    a = dict(id=1, instrument_id=1, effective_date="2026-01-03", broker_scope="ALL", ratio_numerator=2, ratio_denominator=1)
    s = prices(snapshot([event(1, "BUY", "10", "1000")], [a]), [("2026-01-02", "100"), ("2026-01-03", "50")])
    row = performance(s)[2]["每日持仓收益"].rows[-1]
    assert row.values[8] == 20 and row.values[19] == 0


def test_money2_oversell_zero_valuation_retains_debt_and_all_cash():
    s = prices(snapshot([event(1, 'BUY', '10', '1000'),
                         event(2, 'SELL', '15', '1500', '2026-01-03'),
                         event(3, 'BUY', '8', '800', '2026-01-05')]),
               [('2026-01-02', '100'), ('2026-01-03', '100'),
                ('2026-01-04', '100'), ('2026-01-05', '100')])
    positions, traces, reports, _ = performance(s)
    rows = reports['每日持仓收益'].rows
    assert len(rows) == 3  # No idle zero-valued debt row on January 4.
    assert traces[2][0] == -5 and traces[3][0] == 3
    assert positions[(1, 1, 1)].quantity == 3
    assert rows[1].values[8] == 0 and rows[1].values[11] == 0
    assert rows[1].metadata['raw_ending_quantity'] == -5
    assert rows[2].values[5] == 0 and rows[2].metadata['raw_opening_quantity'] == -5
    assert [r.values[19] for r in rows] == [0, 500, -500]
    assert reports['每月持仓收益'].rows[0].values[20] == 0
    assert any(i.rule == 'DQ001' for i in s.issues)  # Debt remains traceable.


def test_debt_month_end_and_next_month_buy_follow_same_daily_formula():
    s = prices(snapshot([event(1, 'SELL', '3', '300', '2026-01-30'),
                         event(2, 'BUY', '5', '500', '2026-02-02')]),
               [('2026-01-30', '100'), ('2026-02-02', '100')])
    _, traces, reports, _ = performance(s)
    assert traces[1][0] == -3 and traces[2][0] == 2
    monthly = reports['每月持仓收益'].rows
    assert monthly[0].values[8] == 0 and monthly[0].values[20] == 300
    assert monthly[1].values[5] == 0 and monthly[1].values[20] == -300


def test_zero_valued_debt_does_not_fabricate_unknown_sale_amount():
    s = prices(snapshot([event(1, 'SELL', '3', None)]), [('2026-01-02', '100')])
    rows = performance(s)[2]['每日持仓收益'].rows
    assert rows[0].values[11] == 0 and rows[0].values[19] is None


def test_micro_fee_allocated_per_order_not_share():
    s = snapshot([event(1, "BUY", "10", "100"), event(2, "SELL", "4", "48")])
    summary, pairs, tags, _ = scalping(s)
    assert summary.rows[0].values[11] == D("6.6")
    assert pairs.rows[0].values[8] == D("1.4")
    assert "微操" in tags[1] and "持仓" in tags[1]


def test_sell_then_buy_uses_existing_position_and_matches_fifo():
    s = snapshot([event(1, "BUY", "10", "100", "2025-12-02"), event(2, "SELL", "3", "36"), event(3, "BUY", "2", "20")])
    _, pairs, _, _ = scalping(s)
    assert pairs.rows[0].values[4] == "先卖后买"
    assert pairs.rows[0].values[5] == 2


def test_micro_policies_are_explicit_and_different():
    s = snapshot([event(1, "BUY", "1", "10"), event(2, "BUY", "1", "20"), event(3, "SELL", "1", "25")])
    assert scalping(s, policy="EXCEL_COMPAT")[1].rows[0].values[6] == 20
    assert scalping(s, policy="FIFO")[1].rows[0].values[6] == 10


def test_partial_lot_amount_conserves_original_basis():
    s = snapshot([event(1, "BUY", "10", "100", "2025-12-02"), event(2, "SELL", "2", "24"), event(3, "BUY", "5", "50"), event(4, "SELL", "3", "36")])
    _, pairs, _, _ = scalping(s)
    assert sum((r.values[6] for r in pairs.rows), D(0)) == 50


def test_service_returns_contracts_no_source_writes(demo_path):
    import hashlib
    before = hashlib.sha256(demo_path.read_bytes()).hexdigest()
    s = AnalyticsService(demo_path, audit_start=None)
    result = s.calculate(QueryScope(date(2026, 1, 10)))
    assert len(result.metrics) == 18 and len(result.charts) == 27
    for name, count in (("当前持仓", 14), ("股票汇总", 18), ("每日持仓收益", 23), ("每月持仓收益", 24), ("每月微操收益", 14), ("清仓收益", 13), ("交易回放", 16), ("月度净投入明细", 10)):
        report = result.reports[name]
        assert len(report.columns) == count and all(len(r.values) == count for r in report.rows)
    assert any(i.rule == "DQ003" for i in result.issues)
    holding = result.reports["当前持仓"].rows[0].values
    assert holding[3] == 6 and holding[5] == 660 and holding[9] == 520 and holding[10] == 140
    assert result.metrics[9].value == 140 and result.metrics[12].value == 80
    assert before == hashlib.sha256(demo_path.read_bytes()).hexdigest()
    assert s.calculate(result.scope) is result


def test_start_date_does_not_destroy_cost_history(demo_path):
    service = AnalyticsService(demo_path)
    a = service.calculate(QueryScope(date(2026, 1, 10)))
    b = service.calculate(replace(a.scope, start_date=date(2026, 1, 3)))
    assert a.reports["当前持仓"].rows == b.reports["当前持仓"].rows
    assert len(b.reports["交易回放"].rows) == 1


def test_weekend_event_rolls_to_next_trading_day_without_price_gap():
    # Friday 2026-06-12 close, Sunday 2026-06-14 buy event, Monday 2026-06-15 close
    s = prices(
        snapshot([event(1, "BUY", "20", "2440", "2026-06-14")]),
        [("2026-06-12", "120"), ("2026-06-15", "125")],
    )
    s = replace(s, scope=replace(s.scope, as_of=date(2026, 6, 20)))
    positions, traces, reports, days = performance(s)
    daily_rows = reports["每日持仓收益"].rows
    # Sunday 2026-06-14 should not be a valuation day with missing price
    assert all(r.values[0] != "2026-06-14" for r in daily_rows)
    # The event should be captured on Monday 2026-06-15 without None profit
    monday_row = next(r for r in daily_rows if r.values[0] == "2026-06-15")
    assert monday_row.values[19] is not None
    assert monday_row.values[8] == D("20")
    assert monday_row.values[11] == D("20") * D("125")

