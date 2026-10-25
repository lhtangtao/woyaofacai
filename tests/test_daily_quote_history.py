from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal as D

from test_live_metrics import fixture
from test_replay import event, snapshot

from portfolio.domain.analytics import Price
from portfolio.domain.live_quotes import LiveQuote, us_session
from portfolio.engine.close_baseline import previous_trading_day
from portfolio.engine.holdings import position_reports
from portfolio.engine.live_valuation import live_holdings, live_metrics
from portfolio.engine.performance import performance
from portfolio.engine.today_holdings import today_holdings
from portfolio.services.quote_store import QuoteStore


def dt(value):
    return datetime.fromisoformat(value)


def test_archive_survives_next_day_restart_and_prefers_regular_close(tmp_path):
    store = QuoteStore(tmp_path/'quotes.sqlite3')
    close = LiveQuote('TEST.US', D('110.123456789123'), dt('2026-10-19T20:00:01+00:00'), 'Intraday')
    post = LiveQuote('TEST.US', D(500), dt('2026-10-19T23:59:59+00:00'), 'Post')
    now = dt('2026-10-20T02:00:00+00:00')
    store.save((close, post), now)
    next_day = LiveQuote('TEST.US', D(200), dt('2026-10-20T20:00:00+00:00'), 'Intraday')
    store.save((next_day,), next_day.timestamp)
    restarted = QuoteStore(store.path)
    history = restarted.historical_quotes(date(2026, 10, 20), dt('2026-10-21T00:00:00+00:00'))
    assert len(history) == 2
    assert history[0][1:3] == ('2026-10-19', close.price)
    assert history[0][-1] == 'CLOSE_ESTIMATE'
    assert history[1][1] == '2026-10-20'
    # Old ticks cannot replace yesterday's saved close either.
    store.save((replace(close, price=D(1), timestamp=close.timestamp.replace(hour=19)),), now)
    assert restarted.historical_quotes(date(2026, 10, 19), now)[0][2] == close.price


def test_migrate_old_cache_and_replay_people_cashflows_official_close_wins(tmp_path):
    store = QuoteStore(tmp_path/'quotes.sqlite3')
    with store.connect() as c:
        c.execute('INSERT INTO latest_quotes VALUES (?,?,?,?,?,?)', ('TEST.US', 'Intraday', '110', '2026-10-19T20:00:00+00:00', '2026-10-19T20:00:00+00:00', 'Longbridge'))
    for person, quantity in ((1, '10'), (2, '3')):
        s = snapshot([event(1, 'BUY', quantity, str(D(quantity)*100), '2026-10-16', person=person), event(2, 'SELL', '1', '115', '2026-10-19', person=person)])
        s.scope = replace(s.scope, as_of=date(2026, 10, 20))
        s.prices = [Price(1, '2026-10-16', D(100), 'DAILY_CLOSE', 'RAW_CLOSE', 1, basis='RAW')]
        store.supplement(s)
        positions, _, reports, days = performance(s)
        row = reports['每日持仓收益'].rows[-1]
        assert row.values[19] == (D(quantity)-1)*10+15
        assert row.metadata['historical_estimate']
        assert row.metadata['opening_day'] == '2026-10-16'
        assert position_reports(s, positions)['当前持仓'].rows[0].metadata['valuation_source'] == 'ESTIMATE_CLOSE'
        assert days[-1] == '2026-10-19'
        s.prices.append(Price(1, '2026-10-19', D(112), 'DAILY_CLOSE', 'RAW_CLOSE', 2, basis='RAW'))
        row = performance(s)[2]['每日持仓收益'].rows[-1]
        assert not row.metadata['historical_estimate']
        assert row.values[10] == D(112)


def test_overnight_maps_next_trade_day_no_future_or_partial_close_baseline(tmp_path):
    store = QuoteStore(tmp_path/'quotes.sqlite3')
    now = dt('2026-10-20T02:00:00+00:00')
    overnight = LiveQuote('TEST.US', D(120), now, 'Overnight')
    partial = LiveQuote('TEST.US', D(105), dt('2026-10-19T17:00:00+00:00'), 'Intraday')
    store.save((overnight, partial), now)
    history = store.historical_quotes(date(2026, 10, 20), now)
    assert len(history) == 1 and history[0][1] == '2026-10-19'
    assert history[0][-1] == 'PARTIAL_ESTIMATE'
    assert not store.historical_quotes(date(2026, 10, 16), now)


def test_tuesday_live_profit_uses_monday_not_friday_and_unknown_stays_unknown():
    a, _, _, _ = fixture()
    now = dt('2026-10-20T02:00:00+00:00')
    a.scope = replace(a.scope, as_of=date(2026, 10, 20))
    session = us_session(now, {date(2026, 10, 20)})
    instruments = {1: {'symbol': 'DEMO', 'market': 'US'}}
    quotes = {('DEMO.US', 'Overnight'): LiveQuote('DEMO.US', D(112), now, 'Overnight')}
    live, *_ = live_holdings(a, instruments, quotes, session, now)
    assert next(m.value for m in live_metrics(a, live, session, now) if m.name == '今日盈亏') is None
    assert today_holdings(a, live, session).rows[0].values[6] is None
    old = a.reports['当前持仓'].rows[0]
    values = list(old.values)
    values[4], values[5] = D(110), D(1100)
    a.reports['当前持仓'] = replace(a.reports['当前持仓'], rows=(replace(old, values=tuple(values), metadata={**old.metadata, 'valuation_date': '2026-10-19', 'valuation_source': 'ESTIMATE_CLOSE', 'valuation_status': 'CLOSE_ESTIMATE'}),))
    a.latest_profit_day = '2026-10-19'
    live, *_ = live_holdings(a, instruments, quotes, session, now)
    assert next(m.value for m in live_metrics(a, live, session, now) if m.name == '今日盈亏') == D(20)
    assert today_holdings(a, live, session).rows[0].values[6] == D(20)
    a.reports['当前持仓'].rows[0].metadata['valuation_status'] = 'PARTIAL_ESTIMATE'
    live, *_ = live_holdings(a, instruments, quotes, session, now)
    assert next(m.value for m in live_metrics(a, live, session, now) if m.name == '今日盈亏') is None


def test_holiday_calendar_and_half_day_close(tmp_path):
    a, *_ = fixture()
    a.trading_days = ('2026-10-09', '2026-10-13')
    assert previous_trading_day(a, '2026-10-13') == '2026-10-09'
    store = QuoteStore(tmp_path/'quotes.sqlite3')
    day = date(2026, 11, 27)
    store.save_calendar({day}, {day}, (day, day))
    now = dt('2026-11-27T18:00:00+00:00')
    store.save((LiveQuote('TEST.US', D(100), now, 'Intraday'),), now)
    assert store.historical_quotes(day, now)[0][-1] == 'CLOSE_ESTIMATE'


def test_ui_reloads_completed_history_and_advances_default_today_on_rollover(qtbot, monkeypatch):
    from types import SimpleNamespace as NS

    from PySide6.QtCore import QDate

    from portfolio.ui import live_quotes
    from portfolio.ui.main_window import MainWindow
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    panel = w.live_panel
    panel.is_demo = False
    panel.analytics = NS(scope=object())
    w.info = NS()
    version = [1]
    w.quote_store = NS(history_identity=lambda: tuple(version))
    current = [QDate(2026, 10, 19)]
    monkeypatch.setattr(live_quotes, 'QDate', NS(currentDate=lambda: current[0]))
    w.holding_date.setDate(current[0])
    calls = []
    w.clear_analytics = lambda: calls.append('clear')
    w.refresh = lambda: calls.append('refresh')
    assert not panel.refresh_history()
    assert not panel.refresh_history()
    version[0] = 2
    assert panel.refresh_history()
    assert calls == ['clear', 'refresh']
    calls.clear()
    current[0] = QDate(2026, 10, 20)
    assert panel.refresh_history()
    assert w.holding_date.date() == current[0]
    # A manually selected historical date stays historical on the next day.
    w.holding_date.setDate(QDate(2026, 10, 16))
    current[0] = QDate(2026, 10, 21)
    assert panel.refresh_history()
    assert w.holding_date.date() == QDate(2026, 10, 16)


def test_later_calendar_refresh_keeps_old_half_day_snapshot_status(tmp_path):
    store = QuoteStore(tmp_path/'quotes.sqlite3')
    day = date(2026, 11, 27)
    store.save_calendar({day}, {day}, (day, day))
    close = dt('2026-11-27T18:00:00+00:00')
    store.save((LiveQuote('TEST.US', D(100), close, 'Intraday'),), close)
    later = date(2026, 12, 21)
    store.save_calendar({later}, set(), (later, later))
    assert store.historical_quotes(later, dt('2026-12-21T22:00:00+00:00'))[0][-1] == 'CLOSE_ESTIMATE'
