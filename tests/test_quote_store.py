from datetime import date, datetime, timedelta
from decimal import Decimal as D
from threading import Event
from types import SimpleNamespace as NS

from PySide6.QtCore import QDate

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domain.live_quotes import LiveQuote, quote_usable
from portfolio.domain.models import QueryScope
from portfolio.services.live_quotes import QuoteFeed
from portfolio.services.quote_store import QuoteStore
from portfolio.ui.main_window import MainWindow


def test_persistent_quotes_survive_restart_and_old_ticks_cannot_overwrite_new(tmp_path):
    now = datetime.fromisoformat('2026-10-19T15:00:00+00:00')
    store = QuoteStore(tmp_path/'quotes.sqlite3')
    day = date(2026, 10, 19)
    store.save_calendar({day}, set(), (day, day+timedelta(days=1)))
    latest = LiveQuote('AAA.US', D('123.123456789123'), now, 'Intraday')
    store.save((latest,), now)
    store.save((LiveQuote('AAA.US', D(10), now-timedelta(seconds=1), 'Intraday'),), now)
    feed = QuoteFeed('unused', clock=lambda: now, store=store)
    feed.set_symbols({'AAA.US'})
    _, session, quotes = feed.snapshot()
    assert quotes[('AAA.US', 'Intraday')].price == latest.price
    assert quotes[('AAA.US', 'Intraday')].source == 'cache'
    assert quote_usable(quotes[('AAA.US', 'Intraday')], session, now)
    tomorrow = QuoteFeed('unused', clock=lambda: now+timedelta(days=1), store=store)
    _, next_session, old = tomorrow.snapshot()
    assert not quote_usable(old[('AAA.US', 'Intraday')], next_session, now+timedelta(days=1))


def test_quote_filters_reuse_other_people_and_flush_on_shutdown(tmp_path):
    from test_live_quotes import FakeContext
    now = datetime.fromisoformat('2026-10-19T15:00:00+00:00')
    context = FakeContext([date(2026, 10, 19)])
    store = QuoteStore(tmp_path/'quotes.sqlite3')
    feed = QuoteFeed('unused', factory=lambda _: context, clock=lambda: now, store=store)
    feed.set_symbols({'AAA.US'})
    feed._store('AAA.US', D(100), now, 'Intraday')
    calls = []
    def wait(_):
        calls.append(1)
        if len(calls) == 1:
            feed.set_symbols({'BBB.US'})
            feed._store('BBB.US', D(200), now, 'Intraday')
        else:
            feed.stop()
    feed._wait = wait
    feed._run()
    assert set(feed.quotes) == {('AAA.US', 'Intraday'), ('BBB.US', 'Intraday')}
    assert set(store.load(now)[0]) == set(feed.quotes)


def test_switching_owner_keeps_cache_and_warms_all_holdings(qtbot):
    window = MainWindow(settings=False)
    qtbot.addWidget(window)
    panel = window.live_panel
    panel.is_demo = False
    panel.enabled.setChecked(True)
    panel.path = 'configured'
    row = ReportRow(('LONG', '', 'BBB', D(10)), {'instrument_id': 2})
    panel.instruments = {2: {'symbol': 'BBB', 'market': 'US'}}
    scope = QueryScope(QDate.currentDate().toPython(), person_ids=(1,))
    panel.analytics = NS(scope=scope)
    feed = NS(symbols={'AAA.US'})
    panel.feed = feed
    panel.invalidate()
    assert feed.symbols == {'AAA.US'}
    panel.feed = None
    panel.analytics = NS(scope=scope)
    seen = []
    def calculate(scope, **kwargs):
        seen.append(scope)
        return NS(reports={'当前持仓': Report('', (), (row,))})
    window.analytics_service = NS(identity=lambda: ('same',), calculate=calculate)
    window._submit = lambda name, task, ready: ready(task(Event()))
    panel.render = lambda: None
    panel.warm_universe()
    assert panel.universe_symbols == {'BBB.US'}
    assert seen[0].person_ids is None and seen[0].account_ids is None
    panel.analytics.scope = QueryScope(scope.as_of, person_ids=(2,))
    panel.warm_universe()
    assert len(seen) == 1  # switching owners doesn't recalculate/replace shared quotes


def test_binding_new_owner_uses_cached_quotes_first_and_cold_start_waits(qtbot, monkeypatch):
    from test_live_metrics import fixture

    from portfolio.ui import live_quotes
    a, live, session, now = fixture()
    window = MainWindow(settings=False)
    qtbot.addWidget(window)
    panel = window.live_panel
    panel.is_demo = False
    panel.path = 'configured'
    panel.instruments = {1: {'symbol': 'DEMO', 'market': 'US'}}
    window.dashboard.analytics = a
    window.asset_page.set_reports(a.reports)
    monkeypatch.setattr(live_quotes, 'QDate', NS(currentDate=lambda: QDate(now.year, now.month, now.day)))
    panel.enabled.setChecked(True)
    quote = LiveQuote('DEMO.US', D(110), now, 'Overnight', 'cache')
    snapshot = ['cached', session, {('DEMO.US', 'Overnight'): quote}]
    panel.feed = NS(set_symbols=lambda _: None, stop=lambda: None, snapshot=lambda: tuple(snapshot))
    monkeypatch.setattr(live_quotes, 'datetime', NS(now=lambda tz: now))
    panel.bind(a)
    assert '+100.00' in window.dashboard.cards[7].text()
    assert '2026-10-19' in window.dashboard.cards[7].text()
    assert '2026-10-16' not in window.dashboard.cards[7].text()
    assert window.asset_page.reports['实时持仓'].rows[0].metadata['quote_source'] == 'cache'
    snapshot[:] = ['connecting', None, {}]
    panel.bind(a)
    assert '正在读取本时段行情' in window.dashboard.cards[7].text()
    assert '2026-10-16' not in window.dashboard.cards[7].text()
