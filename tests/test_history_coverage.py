from datetime import date, datetime
from decimal import Decimal
import hashlib
import sqlite3
from types import SimpleNamespace
from dataclasses import replace

import pytest

from portfolio.domain.live_quotes import NY, UTC
from portfolio.services.history_coverage import Calendar, Coverage, cached_calendar, check_coverage
from portfolio.services.quote_store import QuoteStore
from portfolio.ui.main_window import MainWindow
from portfolio.services.trades_service import TradesService


@pytest.fixture
def closed_position(demo_path):
    with sqlite3.connect(demo_path) as c:
        c.execute("UPDATE broker_trades SET business_date='2026-01-05',book_time='2026-01-05 10:00:00',quantity_e8=1000000000 WHERE id=2")
        c.execute('DELETE FROM market_prices')
    return demo_path


def calendar():
    return Calendar(frozenset([date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]), frozenset())


def test_checks_historical_closed_positions_not_only_current_and_is_readonly(closed_position):
    before = hashlib.sha256(closed_position.read_bytes()).hexdigest()
    result = check_coverage(closed_position, None, date(2026, 1, 1), date(2026, 1, 6), calendar(),
                            now=datetime(2026, 1, 7, tzinfo=UTC))
    assert result.required == 2
    assert result.missing == (('DEMO.US', date(2026, 1, 2)), ('DEMO.US', date(2026, 1, 5)))
    assert not result.legacy
    assert hashlib.sha256(closed_position.read_bytes()).hexdigest() == before


def test_cache_closes_merge_with_source_and_legacy_does_not_claim_verified(closed_position, tmp_path):
    with sqlite3.connect(closed_position) as c:
        c.execute("INSERT INTO market_prices(symbol,price_type,trade_date,period_key,price_e8,updated_at_utc,price_status,source_record_id) VALUES('DEMO.US','DAILY_CLOSE','2026-01-02','2026-01-02',10000000000,'now','LEGACY_UNKNOWN',1)")
    store = QuoteStore(tmp_path/'quotes.sqlite3')
    store.save_official_close('DEMO.US', date(2026, 1, 5), Decimal(110), datetime(2026, 1, 5, tzinfo=NY), datetime(2026, 1, 7, tzinfo=UTC))
    result = check_coverage(closed_position, store.path, date(2026, 1, 1), date(2026, 1, 6), calendar(), now=datetime(2026, 1, 7, tzinfo=UTC))
    assert result.required == 2 and not result.missing
    assert result.legacy == (('DEMO.US', date(2026, 1, 2)),)
    with sqlite3.connect(closed_position) as c:
        c.execute("UPDATE market_prices SET price_status='RAW_CLOSE'")
    assert not check_coverage(closed_position, store.path, date(2026, 1, 1), date(2026, 1, 6), calendar(), now=datetime(2026, 1, 7, tzinfo=UTC)).legacy


def test_unclosed_and_half_days_use_exchange_close_plus_grace(closed_position):
    cal = Calendar(calendar().days, frozenset([date(2026, 1, 5)]))
    def run(hour, minute):
        return check_coverage(closed_position, None, date(2026, 1, 1), date(2026, 1, 5), cal,
                              now=datetime(2026, 1, 5, hour, minute, tzinfo=NY)).required
    assert run(13, 14) == 1
    assert run(13, 15) == 2


def test_incomplete_calendar_is_failure_not_zero_missing(tmp_path):
    store = QuoteStore(tmp_path/'quotes.sqlite3')
    store.save_calendar({date(2026, 1, 5)}, set(), (date(2026, 1, 4), date(2026, 1, 6)))
    with pytest.raises(ValueError, match='未覆盖'):
        cached_calendar(store.path, date(2026, 1, 1), date(2026, 1, 6))


@pytest.mark.parametrize('failure', [False, True])
def test_startup_check_failure_or_missing_gets_persistent_status_and_warning(qtbot, closed_position, monkeypatch, failure):
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.info = SimpleNamespace(path=closed_position, is_demo=False)
    w.live_panel.path = 'unused'
    import portfolio.services.history_coverage as service
    if failure:
        monkeypatch.setattr(service, 'fetch_calendar', lambda *args: (_ for _ in ()).throw(ValueError('SECRET')))
    else:
        monkeypatch.setattr(service, 'fetch_calendar', lambda *args: calendar())
        monkeypatch.setattr(service, 'check_coverage', lambda *args: Coverage(date(2026, 1, 1), date(2026, 1, 6), 2, (('DEMO.US', date(2026, 1, 2)),), ()))
    w.check_monthly_history()
    qtbot.waitUntil(lambda: w._history_coverage_dialog is not None, timeout=5000)
    assert w.history_coverage_button.text().startswith('⚠')
    assert 'SECRET' not in w._history_coverage_details
    assert ('检查失败' if failure else '缺失 1 条') in w.history_coverage_button.text()
    w.close()


def test_existing_legacy_closes_do_not_trigger_warning(qtbot, closed_position, monkeypatch):
    import portfolio.services.history_coverage as service
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.info = SimpleNamespace(path=closed_position, is_demo=False)
    monkeypatch.setattr(service, 'fetch_calendar', lambda *args: calendar())
    monkeypatch.setattr(service, 'check_coverage', lambda *args: Coverage(
        date(2026, 1, 1), date(2026, 1, 6), 2, (), (('DEMO.US', date(2026, 1, 2)),)))
    w.check_monthly_history()
    qtbot.waitUntil(lambda: '完整' in w.history_coverage_button.text(), timeout=5000)
    assert w._history_coverage_dialog is None
    assert '⚠' not in w.history_coverage_button.text()
    w.close()


def test_real_source_ready_triggers_monthly_check_on_each_open(qtbot, demo_path, monkeypatch):
    original = TradesService.source_info
    monkeypatch.setattr(TradesService, 'source_info', lambda self, **kw: replace(original(self, **kw), is_demo=False))
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    calls = []
    monkeypatch.setattr(w, 'check_monthly_history', lambda: calls.append(w.info.path))
    w.open_database(demo_path)
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=5000)
    w.open_database(demo_path)
    qtbot.waitUntil(lambda: len(calls) == 2, timeout=5000)
    assert calls == [demo_path.resolve(), demo_path.resolve()]
    w.close()
