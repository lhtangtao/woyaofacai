from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal as D
from threading import Event
from types import SimpleNamespace as NS

import pytest
from PySide6.QtCore import QDate

from portfolio.domain.analytics import Report, ReportRow
from portfolio.services.earnings_service import (
    EarningsEvent,
    EarningsMonth,
    EarningsService,
    holding_targets,
    parse_event,
)
from portfolio.ui.main_window import MainWindow


def event(**kwargs):
    return NS(symbol='TSLA.US', date='2026.10.21 (EST)', date_type='Post', financial_market_time='', datetime='1790812800', counter_name='Tesla', content='', **kwargs)


def test_earnings_date_placeholders_are_not_release_times_and_timezone_dst():
    raw = event()
    parsed = parse_event(raw, 'US')
    assert parsed.day == date(2026, 10, 21)
    assert parsed.time_label == '盘后' and parsed.beijing == '—'
    raw.financial_market_time = '16:30'
    parsed = parse_event(raw, 'US')
    assert parsed.beijing == '10-22 04:30 北京时间'
    raw.date = '2026.11.10 (EST)'
    assert parse_event(raw, 'US').beijing == '11-11 05:30 北京时间'
    raw.date = 'invalid'
    assert parse_event(raw, 'US') is None


def test_earnings_service_pages_deduplicates_caches_and_cancels():
    calls = []
    class Context:
        def finance_calendar(self, _, start, end, market, count, offset):
            calls.append((start, offset))
            assert start == end
            raw = event()
            raw.date = start
            raw.symbol = ('TSLA.US' if offset == 0 else 'MU.US')
            return NS(list=[NS(count=2, infos=[raw])])
    service = EarningsService(lambda _: (Context(), 'report'))
    result = service.fetch('fake', 2026, 2, ['US'])
    assert len(result.events) == 56 and len(calls) == 56
    assert service.fetch('fake', 2026, 2, ['US']) is result
    assert len(calls) == 56
    service.fetch('fake', 2026, 2, ['US'], force=True)
    assert len(calls) == 112
    cancel = Event()
    cancel.set()
    with pytest.raises(ValueError, match='取消'):
        service.fetch('fake', 2026, 3, ['US'], cancel=cancel)


def test_repeated_pages_warn_instead_of_silently_claiming_complete():
    class Context:
        def finance_calendar(self, _, start, end, market, **kw):
            raw = event()
            raw.date = start
            return NS(list=[NS(count=2, infos=[raw])])
    result = EarningsService(lambda _: (Context(), 'report')).fetch('fake', 2026, 2, ['US'])
    assert len(result.events) == 28 and '不完整' not in result.warning and '可能未返回' in result.warning


def test_holding_targets_deduplicate_exclude_closed_and_link_etf_explicitly():
    row = ReportRow(('LONG', '', 'TSLL', D(10)), {'instrument_id': 1})
    closed = ReportRow(('BBAE', '', 'MU', D(0)), {'instrument_id': 2})
    analytics = NS(reports={'当前持仓': Report('', (), (row, row, closed))})
    instruments = {1: {'symbol': 'TSLL', 'market': 'US', 'name': 'Fund'}, 2: {'symbol': 'MU', 'market': 'US'}}
    assert holding_targets(analytics, instruments, {}) == {'TSLL.US': ('TSLA.US', 'Fund')}
    assert holding_targets(analytics, instruments, {'TSLL.US': ''})['TSLL.US'][0] == ''


def test_calendar_shows_company_time_links_unknowns_and_day_filter(qtbot):
    window = MainWindow(settings=False)
    qtbot.addWidget(window)
    page = window.earnings_page
    page.month.setDate(QDate(2026, 10, 1))
    page.targets = {'TSLL.US': ('TSLA.US', 'TSLL'), 'MU.US': ('MU.US', 'MU'), 'TQQQ.US': ('TQQQ.US', 'ETF')}
    a = EarningsEvent('TSLA.US', 'Tesla', date(2026, 10, 21), '盘后', '—')
    b = replace(a, symbol='MU.US', name='Micron', day=date(2026, 10, 22))
    page.result = EarningsMonth((a, b), datetime.now(timezone.utc))
    page.draw()
    assert page.details.rowCount() == 2
    assert page.details.item(0, 1).text() == 'Tesla'
    assert page.details.item(0, 5).text() == 'TSLL.US'
    assert 'TQQQ.US' in page.unmatched.text()
    page.select_day(3, 2)  # October 21, Wednesday
    assert page.details.rowCount() == 1 and page.details.item(0, 1).text() == 'Tesla'
    page.select_day(3, 2)
    assert page.details.rowCount() == 2
    window.navigation.select(14)
    assert window.pages.currentWidget() is page
    assert window.date_button.isHidden()


def test_calendar_loads_current_holdings_and_ignores_stale_filter_results(qtbot, tmp_path):
    from threading import Event

    window = MainWindow(settings=False)
    qtbot.addWidget(window)
    page = window.earnings_page
    config = tmp_path/'config.py'
    config.write_text('')
    window.info = NS()
    window.live_panel.path = str(config)
    window.live_panel.instruments = {1: {'symbol': 'TSLL', 'market': 'US'}}
    holding = ReportRow(('LONG', '', 'TSLL', D(10)), {'instrument_id': 1})
    reports = {'当前持仓': Report('', (), (holding,))}
    window.analytics_service = NS(identity=lambda: ('same-source',), calculate=lambda scope, **kwargs: NS(scope=scope, reports=reports))
    submitted = []
    window._submit = lambda lane, task, ready: submitted.append((lane, task, ready))
    window.navigation.select(14)
    assert submitted[-1][0] == 'earnings_holdings'
    _, task, ready = submitted[-1]
    ready(task(Event()))
    assert page.targets == {'TSLL.US': ('TSLA.US', 'TSLL')}
    _, old_task, old_ready = submitted[-1]
    assert submitted[-1][0] == 'earnings_calendar'
    page.month.setDate(QDate(2026, 11, 1))
    old_ready((EarningsMonth((), datetime.now(timezone.utc)), ''))
    assert page.result is None  # previous month's completion cannot overwrite November
    window.info = None


def test_single_stock_funds_resolve_to_underlyings_and_index_funds_do_not():
    from portfolio.services.earnings_links import default_target, migrate_legacy_links
    expected = {'AAOX': 'AAOI', 'AVGX': 'AVGO', 'GGLL': 'GOOGL', 'INTW': 'INTC', 'LITX': 'LITE', 'MUU': 'MU', 'ORCX': 'ORCL', 'SNXX': 'SNDK', 'SPAL': 'SPCX', 'STXL': 'STX', 'TSLL': 'TSLA', 'WDCX': 'WDC'}
    for held, target in expected.items():
        assert default_target(held+'.US', '杠杆') == target+'.US'
    for held in ('TQQQ', 'SQQQ', 'QQQM', 'KORU'):
        assert default_target(held+'.US') == ''
    assert default_target('UNKNOWN.US', '杠杆') == ''
    assert default_target('MU.US', '正股') == 'MU.US'
    assert migrate_legacy_links({'AAOX.US': 'AAOX.US', 'TQQQ.US': 'TQQQ.US', 'MUU.US': 'CUSTOM.US', 'TSLL.US': ''}) == {'MUU.US': 'CUSTOM.US', 'TSLL.US': ''}


def test_underlying_earnings_deduplicate_etf_and_common_stock_keep_holding_names(qtbot):
    window = MainWindow(settings=False)
    qtbot.addWidget(window)
    page = window.earnings_page
    page.month.setDate(QDate(2026, 10, 1))
    instruments = {1: {'symbol': 'SNXX', 'market': 'US', 'category': '杠杆'}, 2: {'symbol': 'SNDK', 'market': 'US', 'category': '正股'}}
    rows = tuple(ReportRow(('LONG', '', info['symbol'], D(10)), {'instrument_id': i}) for i, info in instruments.items())
    page.targets = holding_targets(NS(reports={'当前持仓': Report('', (), rows)}), instruments, {})
    entry = EarningsEvent('SNDK.US', 'Sandisk', date(2026, 10, 29), '盘后', '—')
    page.result = EarningsMonth((entry,), datetime.now(timezone.utc))
    page.draw()
    assert len(page.visible_events()) == 1
    assert page.details.item(0, 5).text() == 'SNDK.US、SNXX.US'
    assert 'SNXX.US' in page.calendar.cellWidget(4, 3).text()


def test_quote_store_earnings_cache_survives_and_expires(tmp_path):
    from portfolio.services.quote_store import QuoteStore
    store = QuoteStore(tmp_path / 'quotes.sqlite3')
    ev = EarningsEvent('TSLA.US', 'Tesla', date(2026, 10, 21), '盘后', '10-22 04:30 北京时间', 'Q3')
    from datetime import timedelta
    now = datetime.now(timezone.utc) - timedelta(seconds=5)
    month_res = EarningsMonth((ev,), now, 'test warning')
    key = 'test_key_oct'
    store.save_earnings(key, month_res)

    loaded = store.load_earnings(key, max_age_seconds=86400)
    assert loaded is not None
    assert loaded.warning == 'test warning'
    assert len(loaded.events) == 1
    assert loaded.events[0].symbol == 'TSLA.US'
    assert loaded.events[0].day == date(2026, 10, 21)

    # Expired cache returns None
    assert store.load_earnings(key, max_age_seconds=1) is None


def test_earnings_calendar_reuses_existing_window_analytics(qtbot, tmp_path):
    window = MainWindow(settings=False)
    qtbot.addWidget(window)
    page = window.earnings_page
    config = tmp_path / 'config.py'
    config.write_text('')
    window.info = NS()
    window.live_panel.path = str(config)
    window.live_panel.instruments = {1: {'symbol': 'TSLA', 'market': 'US'}}
    scope = window.make_scope()
    holding = ReportRow(('LONG', '', 'TSLA', D(10)), {'instrument_id': 1})
    window.analytics = NS(scope=scope, reports={'当前持仓': Report('', (), (holding,))})
    window.analytics_service = NS(identity=lambda: ('same-source',), calculate=lambda s, **kw: pytest.fail("Should reuse window.analytics!"))

    submitted = []
    window._submit = lambda lane, task, ready: submitted.append(lane)
    page.activate()
    # It should not have submitted 'earnings_holdings' because window.analytics was reused
    assert 'earnings_holdings' not in submitted
    assert page.targets == {'TSLA.US': ('TSLA.US', 'TSLA')}


def test_earnings_session_change_calculation_and_display(qtbot):
    from portfolio.services.earnings_service import fetch_session_change

    # 1. Quote mock for Post-market
    post_quote = NS(last_done=373.65, prev_close=386.47, timestamp=datetime(2026, 10, 21, 23, 59, tzinfo=timezone.utc))
    ctx_quote = NS(quote=lambda symbols: [NS(symbol='TSLA.US', post_market_quote=post_quote, pre_market_quote=None)])
    post_change = fetch_session_change(ctx_quote, 'TSLA.US', date(2026, 10, 21), 'Post')
    assert post_change is not None
    assert round(post_change, 4) == round((373.65 - 386.47) / 386.47, 4)

    # 2. Quote mock for Pre-market
    pre_quote = NS(last_done=205.0, prev_close=200.0, timestamp=datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc))
    ctx_pre = NS(quote=lambda symbols: [NS(symbol='TSM.US', post_market_quote=None, pre_market_quote=pre_quote)])
    pre_change = fetch_session_change(ctx_pre, 'TSM.US', date(2026, 10, 22), 'Pre')
    assert pre_change is not None
    assert round(pre_change, 4) == 0.025

    # 3. Candles mock fallback
    c_intraday = NS(close=100.0, timestamp=datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc), trade_session='Intraday')
    c_post = NS(close=105.0, timestamp=datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc), trade_session='Post')
    ctx_candle = NS(quote=lambda symbols: [],
                    history_candlesticks_by_date=lambda *a, **k: [c_intraday, c_post])
    candle_change = fetch_session_change(ctx_candle, 'XYZ.US', date(2026, 10, 21), 'Post')
    assert candle_change == 0.05

    # 4. Display in EarningsCalendarPage
    window = MainWindow(settings=False)
    qtbot.addWidget(window)
    page = window.earnings_page
    page.targets = {'TSLA.US': ('TSLA.US', 'Tesla')}
    page.month.setDate(QDate(2026, 10, 1))

    ev = EarningsEvent('TSLA.US', 'Tesla', date(2026, 10, 21), '盘后（美东）', '10-22 北京时间',
                       confirmed_published=True, session_change=-0.0332)
    page.result = EarningsMonth((ev,), datetime.now(timezone.utc), '')
    page.draw()

    # Verify details table column 7
    assert page.details.columnCount() == 8
    assert page.details.item(0, 7).text() == '盘后 -3.32%'

    # Verify calendar tile has 盘后涨跌
    # 2026-10-21 is Wednesday of week 3 (row 2, column 2)
    found_text = False
    for r in range(page.calendar.rowCount()):
        for c in range(page.calendar.columnCount()):
            widget = page.calendar.cellWidget(r, c)
            if widget and '盘后涨跌：' in widget.text() and '-3.32%' in widget.text():
                found_text = True
                break
    assert found_text
