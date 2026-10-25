from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from portfolio.domain.live_quotes import UTC, LiveQuote, quote_usable, us_session
from portfolio.domain.models import QueryScope
from portfolio.engine.live_valuation import live_holdings
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.live_quotes import QuoteFeed, credentials
from portfolio.services.trades_service import TradesService


@pytest.mark.parametrize('stamp,days,half,expected', [
    ('2026-10-17T12:00:00-04:00', (), (), None),  # Saturday
    ('2026-10-18T19:59:00-04:00', (19,), (), None),
    ('2026-10-18T20:00:00-04:00', (19,), (), 'Overnight'),  # Sunday -> Monday
    ('2026-10-19T03:59:00-04:00', (19,), (), 'Overnight'),
    ('2026-10-19T04:00:00-04:00', (19,), (), 'Pre'),
    ('2026-10-19T09:30:00-04:00', (19,), (), 'Intraday'),
    ('2026-10-19T16:00:00-04:00', (19,), (), 'Post'),
    ('2026-10-23T20:00:00-04:00', (23,), (), None),  # Friday overnight closed
    ('2026-10-19T13:00:00-04:00', (19,), (19,), 'Post'),
    ('2026-10-19T17:00:00-04:00', (19,), (19,), None),
])
def test_us_session_boundaries(stamp, days, half, expected):
    trading = {date(2026, 10, d) for d in days}
    halves = {date(2026, 10, d) for d in half}
    session = us_session(datetime.fromisoformat(stamp), trading, halves)
    assert (session.name if session else None) == expected


def test_holiday_and_dst_are_not_weekday_or_fixed_beijing_guesses():
    assert us_session(datetime.fromisoformat('2026-01-18T21:00:00-05:00'), set()) is None
    winter = us_session(datetime.fromisoformat('2026-01-20T14:30:00+00:00'), {date(2026, 1, 20)})
    summer = us_session(datetime.fromisoformat('2026-07-06T13:30:00+00:00'), {date(2026, 7, 6)})
    assert winter.name == summer.name == 'Intraday'
    assert winter.start.utcoffset() != summer.start.utcoffset()


def test_quotes_must_match_current_session_time_and_positive_decimal():
    now = datetime.fromisoformat('2026-10-19T15:00:00+00:00')
    session = us_session(now, {date(2026, 10, 19)})
    quote = LiveQuote('AAPL.US', Decimal('123.456789'), now, 'Intraday')
    assert quote_usable(quote, session, now)
    assert not quote_usable(replace(quote, session='Pre'), session, now)
    assert not quote_usable(replace(quote, timestamp=now - timedelta(days=1)), session, now)
    assert not quote_usable(replace(quote, timestamp=now + timedelta(hours=1)), session, now)
    assert not quote_usable(replace(quote, price=Decimal(0)), session, now)


class FakeContext:
    def __init__(self, days):
        self.days, self.calls = days, []

    def set_on_quote(self, callback):
        self.callback = callback

    def trading_days(self, *args):
        self.calls.append('calendar')
        return SimpleNamespace(trading_days=self.days, half_trading_days=[])

    def subscribe(self, *args):
        self.calls.append('subscribe')

    def unsubscribe(self, *args):
        self.calls.append('unsubscribe')

    def quote(self, symbols):
        self.calls.append('quote')
        return []


def test_closed_market_gets_one_calendar_check_and_no_repeated_quote_requests():
    context = FakeContext([date(2026, 10, 19)])
    feed = QuoteFeed('unused', factory=lambda _: context,
                     clock=lambda: datetime.fromisoformat('2026-10-18T14:00:00+00:00'))
    feed.set_symbols(['AAPL.US'])
    count = []
    def wait(_):
        count.append(1)
        if len(count) == 3:
            feed.stop()
    feed._wait = wait
    feed._run()
    assert context.calls == ['calendar']
    assert '休市' in feed.snapshot()[0]


def test_stream_subscribes_once_then_stops_at_closed_session():
    context = FakeContext([date(2026, 10, 23)])
    clock = [datetime.fromisoformat('2026-10-23T15:00:00-04:00')]
    feed = QuoteFeed('unused', factory=lambda _: context, clock=lambda: clock[0])
    feed.set_symbols(['AAPL.US'])
    calls = []
    def wait(_):
        calls.append(1)
        if len(calls) == 2:
            clock[0] = datetime.fromisoformat('2026-10-23T20:01:00-04:00')
        if len(calls) == 3:
            feed.stop()
    feed._wait = wait
    feed._run()
    assert context.calls == ['calendar', 'quote', 'subscribe', 'unsubscribe']


def test_credentials_are_parsed_without_running_python_or_exposing_values(tmp_path):
    marker, path = tmp_path / 'executed', tmp_path / 'config.py'
    path.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n'
                    'LONGBRIDGE_APP_KEY="key"\nLONGBRIDGE_APP_SECRET="secret"\nLONGBRIDGE_ACCESS_TOKEN="token"\n')
    assert credentials(path) == ('key', 'secret', 'token')
    assert not marker.exists()


def test_live_overlay_updates_values_but_never_historical_reports_or_source(demo_path):
    before = demo_path.read_bytes()
    a = AnalyticsService(demo_path).calculate(QueryScope(date(2026, 1, 10)))
    original = a.reports['当前持仓'].rows
    instruments = {i['id']: i for i in TradesService(demo_path).source_info().instruments}
    now = datetime.fromisoformat('2026-10-19T15:00:00+00:00')
    session = us_session(now, {date(2026, 10, 19)})
    quote = LiveQuote('DEMO.US', Decimal('120.12345678'), now, 'Intraday')
    report, total, floating, count = live_holdings(a, instruments, {('DEMO.US', 'Intraday'): quote}, session, now)
    assert total == Decimal(6) * quote.price
    assert floating == total - Decimal(520)
    assert count == 1 and len(report.columns) == 17
    assert report.rows[0].values[-3] == '盘中'
    assert a.reports['当前持仓'].rows == original and '实时持仓' not in a.reports
    assert demo_path.read_bytes() == before


def test_closed_or_old_session_uses_labelled_reference_not_fake_live_price(demo_path):
    a = AnalyticsService(demo_path).calculate(QueryScope(date(2026, 1, 10)))
    instruments = {i['id']: i for i in TradesService(demo_path).source_info().instruments}
    now = datetime(2026, 10, 18, 14, tzinfo=UTC)
    quote = LiveQuote('DEMO.US', Decimal(999), now - timedelta(days=2), 'Intraday')
    report, total, _, count = live_holdings(a, instruments, {('DEMO.US', 'Intraday'): quote}, None, now)
    assert total == 660 and count == 0
    assert report.rows[0].values[-3] == '收盘参考'
    assert '参考价' in report.rows[0].values[-1]


def test_feed_errors_do_not_leak_tokens_in_display():
    def fail(_):
        raise RuntimeError('access_token=private-secret')
    feed = QuoteFeed('unused', factory=fail)
    feed.set_symbols(['AAPL.US'])
    feed._wait = lambda _: feed.stop()
    feed._run()
    assert 'private-secret' not in feed.snapshot()[0]
    assert feed.snapshot()[1] is None


def test_callback_keeps_newest_session_quote_and_ignores_future_or_removed_symbols():
    from longbridge.openapi import TradeSession
    now = datetime(2026, 10, 19, 14, tzinfo=UTC)
    feed = QuoteFeed('unused', clock=lambda: now)
    feed.set_symbols(['AAPL.US'])
    quote = SimpleNamespace(trade_session=TradeSession.Intraday, last_done=Decimal('120.12'), timestamp=now)
    feed._on_quote('AAPL.US', quote)
    quote.last_done, quote.timestamp = Decimal(10), now - timedelta(seconds=10)
    feed._on_quote('AAPL.US', quote)
    assert feed.snapshot()[2][('AAPL.US', 'Intraday')].price == Decimal('120.12')
    feed._on_quote('OTHER.US', quote)
    assert len(feed.snapshot()[2]) == 1


def test_ui_timer_renders_cache_without_replaying_history(qtbot, demo_path, monkeypatch):
    from PySide6.QtCore import QDate

    from portfolio.ui import live_quotes
    from portfolio.ui.main_window import MainWindow

    class FakeFeed:
        def __init__(self, path, *, store=None):
            self.starts, self.stops = 0, 0
        def set_symbols(self, symbols):
            self.symbols = set(symbols)
        def start(self):
            self.starts += 1
        def stop(self):
            self.stops += 1
        def snapshot(self):
            return '模拟休市 · 暂停请求', None, {}

    monkeypatch.setattr(live_quotes, 'QuoteFeed', FakeFeed)
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    panel = w.live_panel
    info = TradesService(demo_path).source_info()
    panel.source_changed(SimpleNamespace(path=info.path, instruments=info.instruments, is_demo=False))
    panel.path = str(demo_path)  # Existing path; FakeFeed never parses it.
    a = AnalyticsService(demo_path).calculate(QueryScope(QDate.currentDate().toPython()))
    original = dict(a.reports)
    w.asset_page.set_reports(a.reports)
    panel.bind(a)
    panel.enabled.setChecked(True)
    feed = panel.feed
    assert feed.starts == 1 and feed.symbols == {'DEMO.US'}
    w.asset_page.selector.setCurrentText('实时持仓')
    def no_replay(*args, **kwargs):
        raise AssertionError('Timer must not replay historical analytics')
    monkeypatch.setattr(AnalyticsService, 'calculate', no_replay)
    panel.timer.timeout.emit()
    panel.timer.timeout.emit()
    assert panel.timer.interval() == 10000 and panel.timer.isActive()
    assert feed.starts == 1
    assert w.asset_page.report.name == '实时持仓'
    assert w.asset_page.table.finance_model.columnCount() == 17
    assert a.reports == original and '实时持仓' not in a.reports
    panel.enabled.setChecked(False)
    assert feed.stops == 1 and panel.feed is None
    assert w.asset_page.selector.currentText() == '当前持仓'
    w.close()


def test_snapshot_decimal_is_read_before_subscription_can_push():
    now = datetime.fromisoformat('2026-10-19T15:00:00+00:00')
    class Quote:
        symbol, timestamp = 'AAPL.US', now
        @property
        def last_done(self):
            context.calls.append('decimal')
            return Decimal('123.456789')
    class Context(FakeContext):
        def quote(self, symbols):
            self.calls.append('quote')
            return [Quote()]
        def subscribe(self, *args):
            assert 'decimal' in self.calls
            super().subscribe(*args)
    context = Context([date(2026, 10, 19)])
    feed = QuoteFeed('unused', factory=lambda _: context, clock=lambda: now)
    feed.set_symbols(['AAPL.US'])
    feed._wait = lambda _: feed.stop()
    feed._run()
    assert context.calls[:4] == ['calendar', 'quote', 'decimal', 'subscribe']
    assert feed.snapshot()[2][('AAPL.US', 'Intraday')].price == Decimal('123.456789')


def test_sdk_value_conversion_is_serial_across_snapshot_and_push_threads():
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    now = datetime.fromisoformat('2026-10-19T15:00:00+00:00')
    counter_lock = threading.Lock()
    gate = threading.Barrier(2)
    state = {'active': 0, 'peak': 0}
    class Quote:
        symbol, timestamp, trade_session = 'AAPL.US', now, 'Intraday'
        @property
        def last_done(self):
            with counter_lock:
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
            time.sleep(.03)  # Model the native first getter releasing the GIL.
            with counter_lock:
                state['active'] -= 1
            return Decimal('123.456789')
    first, second = (QuoteFeed('unused', clock=lambda: now) for _ in range(2))
    for feed in (first, second):
        feed.set_symbols(['AAPL.US'])
    def snapshot():
        gate.wait(timeout=2)
        first._seed(SimpleNamespace(quote=lambda _: [Quote()]), {'AAPL.US'})
    def push():
        gate.wait(timeout=2)
        second._on_quote('AAPL.US', Quote())
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(snapshot), pool.submit(push)]
        for task in tasks:
            task.result(timeout=3)
    assert state == {'active': 0, 'peak': 1}
    assert all(feed.snapshot()[2][('AAPL.US', 'Intraday')].price == Decimal('123.456789') for feed in (first, second))


def test_sdk_naive_timestamp_is_machine_local_not_utc(monkeypatch):
    import time
    original = __import__('os').environ.get('TZ')
    monkeypatch.setenv('TZ', 'Asia/Shanghai')
    time.tzset()
    try:
        now = datetime.fromisoformat('2026-10-19T02:44:21+00:00')
        feed = QuoteFeed('unused', clock=lambda: now)
        feed.set_symbols(['AAPL.US'])
        feed._store('AAPL.US', Decimal('123'), datetime(2026, 10, 19, 10, 44, 21), 'Overnight')
        quote = feed.snapshot()[2][('AAPL.US', 'Overnight')]
        assert quote.timestamp == now
        assert quote_usable(quote, us_session(now, {date(2026, 10, 19)}), now)
    finally:
        if original is None:
            monkeypatch.delenv('TZ')
        else:
            monkeypatch.setenv('TZ', original)
        time.tzset()
