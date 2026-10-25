from datetime import date, datetime
from decimal import Decimal as D
from types import SimpleNamespace

from portfolio.domain.analytics import Metric, Report, ReportRow
from portfolio.domain.live_quotes import LiveQuote, us_session
from portfolio.domain.models import QueryScope
from portfolio.engine.dashboard import KPI_NAMES
from portfolio.engine.live_valuation import live_holdings, live_metrics


def fixture():
    now = datetime.fromisoformat('2026-10-19T02:30:00+00:00')
    session = us_session(now, {date(2026, 10, 19)})
    meta = {'instrument_id': 1, 'account_id': 1, 'valuation_source': 'DAILY_CLOSE', 'valuation_date': '2026-10-16'}
    holding = ReportRow(('LONG', '正股', 'DEMO', D(10), D(100), D(1000), D(90), D(900), D(0), D(900), D(100), D('.1'), None, D(1)), meta)
    reports = {'当前持仓': Report('当前持仓', tuple(str(i) for i in range(14)), (holding,)), '清仓收益': Report('清仓收益', (), ()), '每日持仓收益': Report('每日持仓收益', (), ())}
    a = SimpleNamespace(reports=reports, scope=QueryScope(date(2026, 10, 19)), latest_profit_day='2026-10-16', metrics=tuple(Metric(name, D(0), '', '2026-10-16') for name in KPI_NAMES))
    instruments = {1: {'symbol': 'DEMO', 'market': 'US'}}
    quote = LiveQuote('DEMO.US', D(110), now, 'Overnight')
    live, _, _, _ = live_holdings(a, instruments, {('DEMO.US', 'Overnight'): quote}, session, now)
    return a, live, session, now


def test_overnight_cards_use_monday_trade_day_and_prior_friday_close():
    a, live, session, now = fixture()
    before = repr((a.metrics, a.reports))
    result = {m.name: m for m in live_metrics(a, live, session, now)}
    assert result['当日收益率'].value == D('.1')
    assert result['今日盈亏'].value == D(100)
    assert result['当前总资产'].value == D(1100)
    assert result['浮动盈亏'].value == D(200)
    assert '2026-10-19 夜盘' in result['当日收益率'].day
    assert repr((a.metrics, a.reports)) == before


def test_today_trades_use_opening_value_and_cashflows_including_closed_positions():
    a, live, session, now = fixture()
    def daily(instrument, quantity, opening, bought, sold):
        values = [D(0)] * 23
        values[0], values[8] = '2026-10-19', D(quantity)
        values[15:18] = map(D, (opening, bought, sold))
        return ReportRow(tuple(values), {'account_id': 1, 'instrument_id': instrument})
    a.reports['每日持仓收益'] = Report('每日持仓收益', (), (daily(1, '10', '800', '300', '100'), daily(2, '0', '200', '0', '220')))
    result = {m.name: m for m in live_metrics(a, live, session, now)}
    assert result['今日盈亏'].value == D(120)  # 1100-800-300+100 + (220-200)
    assert result['本月盈亏'].value == D(120)


def test_missing_current_quote_is_missing_daily_return_instead_of_fake_zero():
    a, live, session, now = fixture()
    a.reports['当前持仓'].rows[0].metadata['valuation_date'] = '2026-10-15'
    result = {m.name: m for m in live_metrics(a, live, session, now)}
    assert result['今日盈亏'].value is None
    assert result['当日收益率'].value is None
    assert live_metrics(a, live, None, now) is a.metrics


def test_period_totals_keep_full_kpis_when_display_rows_have_start_filter():
    from dataclasses import replace
    a, live, session, now = fixture()
    a.scope = replace(a.scope, start_date=date(2026, 10, 1))
    a.metrics = tuple(replace(m, value=D(1000) if m.name == '本年盈亏' else D(200) if m.name == '本月盈亏' else m.value) for m in a.metrics)
    result = {m.name: m for m in live_metrics(a, live, session, now)}
    assert result['本年盈亏'].value == D(1100)
    assert result['本月盈亏'].value == D(300)


def test_live_today_replaces_recorded_today_instead_of_double_counting():
    from dataclasses import replace
    a, live, session, now = fixture()
    a.latest_profit_day = '2026-10-19'
    a.metrics = tuple(replace(m, value=D(20) if m.name == '今日盈亏' else D(200) if m.name == '本月盈亏' else m.value, day='2026-10-19') for m in a.metrics)
    values = [D(0)] * 23
    values[0], values[8], values[15] = '2026-10-19', D(10), D(1000)
    a.reports['每日持仓收益'] = Report('每日持仓收益', (), (ReportRow(tuple(values), {'account_id': 1, 'instrument_id': 1}),))
    result = {m.name: m for m in live_metrics(a, live, session, now)}
    assert result['今日盈亏'].value == D(100)
    assert result['本月盈亏'].value == D(280)  # 200 - recorded 20 + live 100


def test_live_calendar_replaces_today_and_periods_without_mutating_history():
    from dataclasses import replace

    from portfolio.engine.dashboard import live_daily_rows, monthly_gains, quarterly_gains
    a, live, session, now = fixture()
    def daily(day, gain):
        values = [None]*23
        values[0], values[19] = day, gain
        return ReportRow(tuple(values), {})
    history = (daily('2026-10-16', D(200)), daily('2026-10-19', D(20)), daily('2026-10-19', D(30)))
    metrics = live_metrics(a, live, session, now)
    rows, day = live_daily_rows(history, metrics)
    assert day == '2026-10-19'
    assert len(rows) == 2
    assert rows[-1].values[19] == D(100)
    assert monthly_gains(rows, 2026)[0][9] == D(300)
    assert quarterly_gains(rows, 2026)[3] == D(300)
    assert [r.values[19] for r in history] == [D(200), D(20), D(30)]
    assert live_daily_rows(history, metrics)[0] == rows  # refresh does not accumulate
    unknown = tuple(replace(m, value=None) if m.name == '今日盈亏' else m for m in metrics)
    missing, _ = live_daily_rows(history, unknown)
    assert monthly_gains(missing, 2026)[0][9] is None
    assert live_daily_rows(history, a.metrics) == (history, None)


def test_dashboard_calendar_and_month_grid_follow_live_cards_and_restore(qtbot):
    from PySide6.QtCore import Qt

    from portfolio.ui.dashboard_page import DashboardPage
    a, live, session, now = fixture()
    a.valuation_days = ('2026-10-16',)
    values = [None]*23
    values[0], values[19] = '2026-10-16', D(200)
    a.reports['每日持仓收益'] = Report('每日持仓收益', (), (ReportRow(tuple(values), {}),))
    page = DashboardPage()
    qtbot.addWidget(page)
    page.set_analytics(a)
    assert '2026-10-19' not in [r.values[0] for r in page.daily_rows]
    page.set_metrics(live_metrics(a, live, session, now))
    today_item = next(page.calendar_table.item(r, c) for r in range(page.calendar_table.rowCount()) for c in range(5) if page.calendar_table.item(r, c).data(Qt.ItemDataRole.UserRole) == '2026-10-19')
    assert '+100.00' in today_item.text() and '实时估值' in today_item.text()
    assert '+300.00' in page.monthly_table.item(3, 0).text()
    assert '实时估值' in page.monthly_table.item(3, 3).text()
    with qtbot.waitSignal(page.drill) as signal:
        page.calendar_clicked(today_item.row(), today_item.column())
    assert signal.args == ['live_day', '2026-10-19']
    # A refresh preserves historical month/year selections, then disabling live restores closing data.
    page.set_metrics(a.metrics)
    assert page.live_day is None
    assert '+200.00' in page.monthly_table.item(3, 0).text()
    assert '实时估值' not in page.monthly_table.item(3, 0).text()


def test_live_calendar_adds_new_year_month_using_trading_day():
    from dataclasses import replace

    from portfolio.engine.dashboard import live_daily_rows, monthly_gains
    a, live, session, now = fixture()
    metrics = tuple(replace(m, day='2027-01-04 夜盘', value=D(25)) if m.name == '今日盈亏' else m for m in live_metrics(a, live, session, now))
    rows, day = live_daily_rows((), metrics)
    assert day == '2027-01-04'
    assert monthly_gains(rows, 2027)[0][0] == D(25)
    assert monthly_gains(rows, 2026)[1] is None
