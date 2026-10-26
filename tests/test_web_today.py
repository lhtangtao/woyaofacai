"""Native/Web parity with isolated ledgers, cache and Flask blueprint.

Never imports the production Flask app or opens the user's security database.
"""
import hashlib
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from flask import Flask, g

from portfolio.domain.live_quotes import LiveQuote
from portfolio.domain.models import QueryScope
from portfolio.engine.live_valuation import live_holdings
from portfolio.engine.today_holdings import lifetime_summary, lifetime_total, today_holdings, today_summary
from portfolio.services.intraday_history import scope_key, source_version
from portfolio.services.quote_store import QuoteStore
from portfolio.services.web_today import WebTodayService, exact_json
from portfolio.ui.intraday_pnl import IntradayPnlChart
from portfolio.ui.report_page import ReportPage
from portfolio.web import api_v1 as api_module
from portfolio.web.security.auth_context import build_auth_context


@pytest.fixture
def today_service(demo_path, tmp_path):
    # A second channel in a NEW synthetic ledger, never user data.
    with sqlite3.connect(demo_path) as c:
        c.execute("""INSERT INTO broker_trades SELECT 10,'second-channel',2,
            instrument_id,'second-order',NULL,record_granularity,side,executed_at_utc,
            market_trade_date,business_date,book_time,quantity_e8,price_e8,gross_amount_e8,
            fees_e8,net_amount_e8,calculation_amount_e8,amount_basis,currency,
            record_state,accounting_role,assignment_text,source_record_id
            FROM broker_trades WHERE id=1""")
        c.execute("""INSERT INTO person_operations
            (id,operation_key,person_id,account_id,instrument_id,broker_trade_id,
             operation_kind,side,business_date,book_time,sequence_no,quantity_e8,
             price_e8,amount_e8,amount_basis,currency,link_status,source_record_id)
            VALUES(10,'second-person',1,2,1,10,'MARKET_TRADE','BUY','2026-01-02',
             '2026-01-02 10:00:00',10,1000000000,10000000000,100000000000,'GROSS','USD','LINKED',1)""")
    now = datetime.fromisoformat('2026-01-09T15:00:00+00:00')
    store = QuoteStore(tmp_path / 'quotes.sqlite3')
    days = {date(2026, 1, d) for d in (2, 5, 6, 7, 8, 9)}
    store.save_calendar(days, set(), (date(2026, 1, 1), date(2026, 1, 15)))
    store.save([LiveQuote('DEMO.US', Decimal('115.123456789'), now, 'Intraday')], now)
    svc = WebTodayService(demo_path, store=store, clock=lambda: now, network=False)
    yield svc
    svc.close()


def native_report(svc, scope, enabled=True):
    a = svc.analytics.calculate(scope)
    instruments = {1: {'symbol': 'DEMO', 'market': 'US'}}
    _, session, quotes = svc.quote_snapshot(a, instruments, enabled, svc.clock())
    live, *_ = live_holdings(a, instruments, quotes, session, svc.clock())
    return a, live, today_holdings(a, live, session), session


def seed_curve(svc, scope):
    first = datetime.fromisoformat('2026-01-09T14:35:00+00:00')
    rows = [(first + timedelta(minutes=5*i), 'History5', [
        {'channel': 'SIM_A', 'ticker': 'DEMO', 'pnl': p},
        {'channel': 'SIM_B', 'ticker': 'DEMO', 'pnl': '2.000000001'},
        {'channel': 'SIM_A', 'ticker': 'CLOSED', 'pnl': '3'},
    ]) for i, p in enumerate(('1.123456789', None, '-9.23456789'))]
    svc.store.save_historical_pnl(scope_key(svc.path, scope), scope.as_of.isoformat(), rows,
                                 source_version(svc.path), '盘中行情缺失：DEMO 测试区段')
    return rows


@pytest.mark.parametrize('person', [None, 1, 2])
@pytest.mark.parametrize('channel', [None, 'SIM_A', 'SIM_B'])
def test_native_cards_rows_channels_same_snapshot(today_service, qtbot, person, channel):
    svc = today_service
    scope = QueryScope(date(2026, 1, 9), person_ids=(person,) if person else None)
    a, live, native, _ = native_report(svc, scope)
    before = repr(a)
    result = svc.snapshot(scope, channel=channel)
    rows = [r for r in native.rows if channel is None or r.values[0] == channel]
    pnl, rate, up, down, flat, unknown = today_summary(rows)
    assert result['summary'] == exact_json(dict(pnl=pnl, rate=rate, up=up, down=down,
        flat=flat, unknown=unknown, cumulative=lifetime_total(lifetime_summary(a, live), channel)))
    page = ReportPage(('今日盈亏',))
    qtbot.addWidget(page)
    page.show()
    page.set_reports({'今日盈亏': native})
    page.channel_overview.selected_channel = channel
    page.apply_local_filter()
    expected = page.sorted_rows if rows else ()
    assert result['rows'] == exact_json([r.values for r in expected])
    assert result['columns'] == list(native.columns)
    assert repr(a) == before
    assert all(r['_meta']['live_fresh'] for r in result['items'])


@pytest.mark.parametrize('ticker,channel', [('', None), ('DEMO', 'SIM_A'), ('CLOSED', 'SIM_A'), ('missing', None)])
def test_native_curve_every_sample_extrema_missing_and_filters(today_service, qtbot, ticker, channel):
    svc = today_service
    scope = QueryScope(date(2026, 1, 9), person_ids=(1,))
    seed_curve(svc, scope)
    a, _, report, session = native_report(svc, scope)
    chart = IntradayPnlChart()
    qtbot.addWidget(chart)
    chart.bind(svc.store, svc.path, scope, report, session, svc.clock(), trading_days=a.trading_days)
    chart.draw(ticker, channel)
    result = svc.snapshot(scope, ticker=ticker, channel=channel)['curve']
    expected = [(t, str(v) if v is not None else None) for t, v in chart.canvas.chart.series[0].points]
    assert [(p['time'], p['pnl']) for p in result['points']] == expected
    assert len(result['points']) == 289
    assert result['interval_minutes'] == 5
    assert '盘中行情缺失' in result['warning']
    valid = [p for p in result['points'] if p['pnl'] is not None]
    assert result['complete_points'] == len(valid)
    if valid:
        assert result['high'] == max(valid, key=lambda p: Decimal(p['pnl']))
        assert result['low'] == min(valid, key=lambda p: Decimal(p['pnl']))
        assert result['latest'] == valid[-1]
    else:
        assert result['latest'] is None


def test_nontrading_day_fallback_matches_native(today_service, qtbot):
    svc = today_service
    scope = QueryScope(date(2026, 1, 10), person_ids=(1,))
    # Native cache key uses selected date even when the curve falls back to Friday.
    svc.clock = lambda: datetime.fromisoformat('2026-01-12T15:00:00+00:00')
    key = scope_key(svc.path, scope)
    svc.store.save_historical_pnl(key, '2026-01-09', [], source_version(svc.path))
    a, _, report, _ = native_report(svc, scope)
    chart = IntradayPnlChart()
    qtbot.addWidget(chart)
    chart.bind(svc.store, svc.path, scope, report, None, svc.clock(), trading_days=a.trading_days)
    curve = svc.snapshot(scope)['curve']
    assert curve['day'] == chart.day == '2026-01-09'
    assert curve['fallback'] == chart.is_fallback is True


def test_stale_quote_no_network_and_disabled_never_masquerade_as_live(today_service):
    svc = today_service
    scope = QueryScope(date(2026, 1, 9), person_ids=(1,))
    svc.clock = lambda: datetime.fromisoformat('2026-01-09T15:05:00+00:00')
    stale = svc.snapshot(scope)
    assert stale['quote']['fresh'] == 0
    assert all('报价较旧' in r['_meta']['live_status'] for r in stale['items'])
    closed = svc.snapshot(scope, enabled=False)
    assert closed['quote']['session'] == '收盘参考'
    assert all(r['_meta']['quote_source'] == 'reference' for r in closed['items'])
    assert all(r['行情时段'] == '收盘' for r in closed['items'])
    assert svc.feed is None


def test_missing_quotes_null_summary_and_sort_unknown_last(today_service):
    svc = today_service
    scope = QueryScope(date(2026, 1, 9), person_ids=(1,))
    with svc.store.connect() as c:
        c.execute('DELETE FROM latest_quotes')
    result = svc.snapshot(scope, sort_col='今日盈亏($)', descending=True)
    assert result['summary']['pnl'] is None
    assert result['summary']['rate'] is None
    assert result['summary']['unknown'] == 1
    assert all(r[6] is None for r in result['rows'])
    assert result['curve']['complete_points'] == 0


def test_source_version_invalidates_old_curve(today_service):
    svc = today_service
    scope = QueryScope(date(2026, 1, 9), person_ids=(1,))
    seed_curve(svc, scope)
    assert svc.snapshot(scope)['curve']['historical']
    # Only change this test's synthetic source; old curve must not survive.
    with sqlite3.connect(svc.path) as c:
        c.execute("UPDATE persons SET notes='synthetic source revision' WHERE id=1")
    result = svc.snapshot(scope)
    assert not result['curve']['historical']


@pytest.fixture
def today_client(today_service, monkeypatch):
    app = Flask('isolated-today-test')
    app.testing = True
    app.register_blueprint(api_module.api_v1)
    monkeypatch.setattr(api_module, 'get_today_service', lambda: today_service)
    monkeypatch.setattr('portfolio.web.security.audit.record_audit_event', lambda *a, **kw: None)

    @app.before_request
    def auth():
        from flask import request
        mode = request.headers.get('X-Test-Role', 'lan')
        if mode == 'lan':
            g.auth_context = build_auth_context('LOCAL_LOOPBACK')
        elif mode == 'remote':
            g.auth_context = build_auth_context('REMOTE_PUBLIC')
        else:
            g.auth_context = build_auth_context('REMOTE_PUBLIC', dict(user_id=2,
                username='synthetic', role='user', person_id=1 if mode == 'user' else None))

    return app.test_client()


def test_api_export_filters_and_ordinary_user_isolation(today_service, today_client):
    client = today_client
    before = hashlib.sha256(today_service.path.read_bytes()).hexdigest()
    qs = '?as_of=2026-01-09&channel=SIM_A&ticker=DEMO&live=0&person_id=1'
    payload = client.get('/api/v1/holdings/today' + qs).get_json()
    assert payload['status'] == 'ok'
    assert payload['total_rows'] == 1
    assert payload['source']['person_ids'] == [1]
    assert client.get('/api/v1/reports/今日盈亏' + qs).get_json()['rows'] == payload['rows']
    export = client.get('/api/v1/export/report/今日盈亏' + qs)
    assert export.status_code == 200 and 'SIM_A' in export.data.decode('utf-8-sig')
    assert 'SIM_B' not in export.data.decode('utf-8-sig')
    forbidden = client.get('/api/v1/holdings/today?as_of=2026-01-09&person_id=2', headers={'X-Test-Role': 'user'}).get_json()
    assert forbidden['rows'] == []
    assert forbidden['source']['person_ids'] == [-99999]
    assert forbidden['curve']['complete_points'] == 0
    unbound = client.get('/api/v1/holdings/today?as_of=2026-01-09', headers={'X-Test-Role': 'unbound'}).get_json()
    assert unbound['rows'] == []
    assert unbound['source']['person_ids'] == [-99999]
    assert client.get('/api/v1/holdings/today', headers={'X-Test-Role': 'remote'}).status_code == 401
    assert client.post('/api/v1/holdings/today').status_code == 405
    assert hashlib.sha256(today_service.path.read_bytes()).hexdigest() == before


def test_invalid_date_and_cache_error_are_explicit(today_client, monkeypatch):
    assert today_client.get('/api/v1/holdings/today?as_of=bad').status_code == 400
    assert today_client.get('/api/v1/holdings/today?as_of=2099-01-01').status_code == 400
    class Broken:
        def snapshot(self, *args, **kwargs):
            raise OSError('private-token-must-not-leak')
    monkeypatch.setattr(api_module, 'get_today_service', lambda: Broken())
    response = today_client.get('/api/v1/holdings/today')
    assert response.status_code == 503
    assert 'private-token' not in response.data.decode()
