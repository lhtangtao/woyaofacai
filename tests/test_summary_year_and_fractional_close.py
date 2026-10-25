from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest
from test_live_metrics import fixture
from test_performance import prices
from test_replay import event, snapshot

from portfolio.domain.analytics import Report, ReportRow
from portfolio.engine.dashboard import SUMMARY_COLUMNS, overview
from portfolio.engine.holdings import position_reports
from portfolio.engine.live_valuation import live_summaries
from portfolio.engine.performance import performance
from portfolio.engine.replay import replay


@pytest.mark.parametrize('quantity,closed', [('0.2', True), ('0.49999999', True), ('0.5', False), ('0.7', False)])
def test_fractional_position_is_closed_only_in_reports(quantity, closed):
    s = snapshot([event(1, 'BUY', quantity, '20')])
    positions = replay(s)[0]
    before = repr(positions)
    reports = position_reports(s, positions)
    assert bool(reports['清仓收益'].rows) is closed
    assert bool(reports['当前持仓'].rows) is not closed
    assert reports['股票汇总'].rows[0].values[3] == ('已清仓' if closed else '持有中')
    assert repr(positions) == before
    if closed:
        row = reports['清仓收益'].rows[0]
        assert row.values[7] == -D(20)
        assert row.metadata['ignored_residual_quantity'] == D(quantity)


def test_summary_year_is_daily_pnl_not_lifetime_gain():
    s = prices(snapshot([event(1, 'BUY', '10', '1000', '2025-12-31')]),
               [('2025-12-31', '105'), ('2026-01-02', '110')])
    s.scope = replace(s.scope, as_of=date(2026, 1, 2))
    positions, _, reports, _ = performance(s)
    reports.update(position_reports(s, positions))
    reports['每月微操收益'] = Report('每月微操收益', (), ())
    metrics, additions, _ = overview(s, reports)
    row = additions['渠道汇总'].rows[0]
    assert additions['渠道汇总'].columns[7] == '本年盈亏'
    assert additions['渠道汇总'].columns[8] == '总盈亏'
    assert row.values[7] == D(50)   # this year: 1100 - previous close 1050
    assert row.values[8] == D(100)  # lifetime: 1100 - 1000
    assert next(m.value for m in metrics if m.name == '本年盈亏') == row.values[7]


def test_live_summary_updates_annual_and_preserves_cash():
    a, live, session, now = fixture()
    base = ('LONG', D(1000), D(900), 1, D(1), D(0), D(100), D(200), D(100), 1, 0, D(0), D(123))
    a.reports['渠道汇总'] = Report('渠道汇总', SUMMARY_COLUMNS, (ReportRow(base),))
    summaries = live_summaries(a, live, D(1100), session, now)
    row = summaries['渠道汇总'].rows[0]
    assert row.values[7] == D(300)  # 200 YTD plus live 100 for new trade day
    assert row.values[12] == D(123)
    # A residual moved out of current holdings must not make live YTD unknown.
    vals = [D(0)] * 23
    vals[0], vals[8], vals[15] = '2026-10-19', D('.2'), D(20)
    a.reports['每日持仓收益'] = Report('每日持仓收益', (), (ReportRow(tuple(vals), {'account_id': 1, 'instrument_id': 2}),))
    # Category/chan fields are required to select this daily row.
    vals[1], vals[2] = 'LONG', '正股'
    a.reports['每日持仓收益'] = Report('每日持仓收益', (), (ReportRow(tuple(vals), {'account_id': 1, 'instrument_id': 2}),))
    assert live_summaries(a, live, D(1100), session, now)['渠道汇总'].rows[0].values[7] == D(280)
