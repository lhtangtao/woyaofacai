from datetime import date
from decimal import Decimal

from portfolio.domain.analytics import ReportRow
from portfolio.domain.models import QueryScope
from portfolio.engine.dashboard import (
    annual_rate,
    compound_rates,
    daily_rates,
    metric_gap,
    monthly_gains,
    monthly_rates,
    quarterly_gains,
    quarterly_rates,
)
from portfolio.services.analytics_service import AnalyticsService


def row(day, gain):
    return ReportRow((day, *([None] * 18), gain))


def test_monthly_gains_include_all_people_rows_and_exclude_other_years():
    rows = (row('2026-01-02', Decimal('10.12')), row('2026-01-02', Decimal('-2.01')),
            row('2026-03-01', Decimal('0')), row('2025-12-31', Decimal('900')))
    gains, total = monthly_gains(rows, 2026)
    assert len(gains) == 12
    assert gains[0] == Decimal('8.11') and gains[1] is None and gains[2] == Decimal(0)
    assert total == Decimal('8.11')


def test_missing_daily_value_never_becomes_zero_or_partial_month_total():
    gains, total = monthly_gains((row('2026-01-02', Decimal(10)), row('2026-01-03', None),
                                  row('2026-02-01', Decimal(20))), 2026)
    assert gains[0] is None and gains[1] == Decimal(20) and total is None
    assert monthly_gains((), 2026) == ((None,) * 12, None)


def test_monthly_overview_matches_year_kpi_under_person_filter(demo_path):
    result = AnalyticsService(demo_path).calculate(QueryScope(date(2026, 1, 10), person_ids=(1,)))
    gains, total = monthly_gains(result.reports['每日持仓收益'].rows, 2026)
    metric = next(m for m in result.metrics if m.name == '本年盈亏')
    assert gains[0] == total == metric.value
    assert all(v is None for v in gains[1:])


def test_missing_period_reason_distinguishes_no_history_and_incomplete_history():
    rows = (row('2026-01-02', Decimal(10)),)
    label, explanation = metric_gap('去年盈亏', rows, date(2026, 12, 2))
    assert label == '该期间无收益记录' and '2025' in explanation
    missing = ReportRow(('2026-02-02', 'BBAE', '杠杆', 'MUU', *([None] * 16)))
    label, explanation = metric_gap('本年盈亏', (*rows, missing), date(2026, 12, 2))
    assert label == '数据未完整'
    assert 'BBAE / MUU' in explanation and '2026-02-02' in explanation
    assert '1条' in explanation


def test_gap_reason_uses_latest_profit_year_like_kpis_not_future_as_of_year():
    label, explanation = metric_gap('去年盈亏', (row('2026-10-16', Decimal(10)),), date(2027, 1, 2))
    assert label == '该期间无收益记录' and '2025' in explanation


def test_quarter_totals_keep_missing_values_and_partial_quarter_observations():
    rows = (row('2026-01-02', Decimal(10)), row('2026-03-31', Decimal(-3)),
            row('2026-04-01', None), row('2026-05-01', Decimal(8)),
            row('2026-10-16', Decimal(20)), row('2025-01-01', Decimal(900)))
    assert quarterly_gains(rows, 2026) == (Decimal(7), None, None, Decimal(20))


def test_daily_rates_and_compound_rates_calculations():
    # Day with opening=1000, gain=50 -> rate=+5%
    r1 = ReportRow(('2026-01-05', *([None]*10), Decimal('1050'), None, None, None, Decimal('1000'), None, None, None, Decimal('50')))
    # Day with opening=1050, gain=-21 -> rate=-2%
    r2 = ReportRow(('2026-01-06', *([None]*10), Decimal('1029'), None, None, None, Decimal('1050'), None, None, None, Decimal('-21')))
    dr = daily_rates((r1, r2))
    assert dr['2026-01-05'] == Decimal('0.05')
    assert dr['2026-01-06'] == Decimal('-0.02')

    # Compounding 5% and -2% -> (1.05 * 0.98) - 1 = 1.029 - 1 = +2.9%
    cr = compound_rates(('2026-01-05', '2026-01-06'), dr)
    assert cr == Decimal('0.029')

    # Monthly rates
    mr = monthly_rates((r1, r2), 2026, dr)
    assert mr[0] == Decimal('0.029')
    assert mr[1] is None

    # Quarterly rates
    qr = quarterly_rates((r1, r2), 2026, dr)
    assert qr[0] == Decimal('0.029')
    assert qr[1] is None

    # Annual rate
    ar = annual_rate((r1, r2), 2026, dr)
    assert ar == Decimal('0.029')
