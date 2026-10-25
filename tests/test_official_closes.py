from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS

from test_replay import event, snapshot

from portfolio.domain.analytics import Price
from portfolio.domain.live_quotes import NY, UTC, LiveQuote
from portfolio.engine.performance import performance
from portfolio.services.live_quotes import QuoteFeed
from portfolio.services.quote_store import QuoteStore


def test_daily_candle_replaces_snapshot_not_postmarket_and_does_not_repeat(tmp_path):
    now = datetime(2026, 10, 20, 2, tzinfo=UTC)
    day = date(2026, 10, 19)
    store = QuoteStore(tmp_path/'cache.sqlite3')
    store.save((LiveQuote('TEST.US', D(999), datetime(2026,10,19,23,tzinfo=UTC), 'Post'),), now)
    calls = []
    def history(*args):
        calls.append(args)
        return [NS(timestamp=datetime(2026,10,19,tzinfo=NY), close=D('110.123456789')),
                NS(timestamp=datetime(2026,10,20,tzinfo=NY), close=D(9999))]
    feed = QuoteFeed('unused', store=store, clock=lambda: now)
    context = NS(history_candlesticks_by_date=history)
    feed._sync_closes(context, {'TEST.US'}, {day, day+timedelta(days=1)}, set(), now)
    assert not feed.close_error
    assert len(calls) == 1
    assert str(calls[0][1]) == 'Period.Day' and str(calls[0][2]) == 'AdjustType.NoAdjust'
    assert str(calls[0][-1]) == 'TradeSessions.Intraday'
    row = store.historical_quotes(day, now)[0]
    assert row[2:] == (D('110.123456789'), datetime(2026,10,19,tzinfo=NY).isoformat(), 'OFFICIAL_CLOSE')
    QuoteFeed('unused', store=store, clock=lambda: now)._sync_closes(context, {'TEST.US'}, {day}, set(), now)
    assert len(calls) == 1  # persisted once even across process restarts
    s = snapshot([event(1,'BUY','10','1000','2026-10-16')])
    s.scope = replace(s.scope, as_of=day)
    s.prices = [Price(1,'2026-10-16',D(100),'DAILY_CLOSE','RAW_CLOSE',1,basis='RAW')]
    store.supplement(s)
    row = performance(s)[2]['每日持仓收益'].rows[-1]
    assert not row.metadata['historical_estimate']
    assert row.values[10] == D('110.123456789')
    # The imported source close has highest priority over the API cache.
    s2 = snapshot(s.events)
    s2.scope = s.scope
    s2.prices = [Price(1,'2026-10-19',D(111),'DAILY_CLOSE','RAW_CLOSE',2,basis='RAW')]
    store.supplement(s2)
    assert len([p for p in s2.prices if p.day == '2026-10-19']) == 1


def test_unfinished_day_not_saved_and_failure_retries_are_throttled(tmp_path):
    day = date(2026,10,19)
    now = datetime(2026,10,19,19,tzinfo=UTC)
    store = QuoteStore(tmp_path/'cache.sqlite3')
    feed = QuoteFeed('unused', store=store, clock=lambda: now)
    calls = []
    def failure(*args):
        calls.append(1)
        raise ValueError('fake provider error')
    context = NS(history_candlesticks_by_date=failure)
    feed._sync_closes(context, {'TEST.US'}, {day}, set(), now)
    assert not calls
    now = datetime(2026,10,20,2,tzinfo=UTC)
    feed._sync_closes(context, {'TEST.US'}, {day}, set(), now)
    assert len(calls) == 1 and feed.close_error
    feed._sync_closes(context, {'TEST.US'}, {day}, set(), now+timedelta(seconds=10))
    assert len(calls) == 1
    feed._sync_closes(context, {'TEST.US'}, {day}, set(), now+timedelta(hours=1))
    assert len(calls) == 2
    feed._sync_closes(context, {'TEST.US'}, {day}, set(), now+timedelta(hours=1, minutes=45), closed=True)
    assert len(calls) == 2  # no repeated holiday/weekend polling
    assert not store.saved_close_days('TEST.US')


def test_backfills_unobserved_friday_and_restores_both_daily_profits(tmp_path):
    friday, monday = date(2026, 10, 16), date(2026, 10, 19)
    now = datetime(2026, 10, 20, 2, tzinfo=UTC)
    store = QuoteStore(tmp_path/'cache.sqlite3')
    store.save_official_close('TEST.US', monday, D(112), datetime(2026, 10, 19, tzinfo=NY), now)
    calls = []

    def history(*args):
        calls.append(args)
        return [NS(timestamp=datetime(2026, 10, 16, tzinfo=NY), close=D(110))]

    feed = QuoteFeed('unused', store=store, clock=lambda: now)
    feed._sync_closes(NS(history_candlesticks_by_date=history), {'TEST.US'}, {friday, monday}, set(), now)
    assert calls[0][3] == friday
    assert not feed.close_error
    assert store.saved_close_days('TEST.US') == {friday, monday}
    s = snapshot([event(1, 'BUY', '10', '1000', '2026-10-15')])
    s.scope = replace(s.scope, as_of=monday)
    s.prices = [Price(1, '2026-10-15', D(100), 'DAILY_CLOSE', 'RAW_CLOSE', 1, basis='RAW')]
    store.supplement(s)
    profits = {r.values[0]: r.values[19] for r in performance(s)[2]['每日持仓收益'].rows}
    assert profits['2026-10-16'] == D(100)
    assert profits['2026-10-19'] == D(20)
    QuoteFeed('unused', store=store, clock=lambda: now)._sync_closes(
        NS(history_candlesticks_by_date=history), {'TEST.US'}, {friday, monday}, set(), now)
    assert len(calls) == 1
