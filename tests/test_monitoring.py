import hashlib
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domain.models import QueryScope
from portfolio.domain.technical_indicators import DailyBar, rsi, technicals
from portfolio.services.monitor_mapping import normalize, resolve
from portfolio.services.monitor_service import (
    LongbridgeMonitorProvider,
    MonitorService,
    holding_sources,
    key_for,
)
from portfolio.services.monitor_store import MonitorStore

UTC = timezone.utc
NOW = datetime(2026, 10, 23, 18, tzinfo=UTC)  # regular NY session


def bars(count=500, end=date(2026, 10, 22)):
    days = []
    day = end
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [
        DailyBar(day, D(i + 100), D(i + 102), D(i + 99), D(i + 101), D(1000 + i))
        for i, day in enumerate(reversed(days))
    ]


class Provider:
    path = "synthetic"
    days = {date(2026, 10, 22), date(2026, 10, 23)}
    halves = set()

    def __init__(self):
        self.calls = []
        self.fail = False
        self.cutoff = date(2026, 10, 22)
        self.series = bars()

    def completed_day(self, now):
        if self.fail:
            raise RuntimeError("secret-never-exposed")
        return self.cutoff

    def history(self, symbol, start, end):
        self.calls.append(("history", symbol, start))
        if self.fail:
            raise RuntimeError("secret-never-exposed")
        return [b for b in self.series if (start is None or b.day >= start) and b.day <= end]

    def quotes(self, symbols):
        self.calls.append(("quotes", tuple(symbols)))
        if self.fail:
            raise RuntimeError("secret-never-exposed")
        return {
            s: dict(price="610", stamp=NOW.isoformat(), session="Intraday", prev_close="600") for s in symbols
        }


def sources(symbol="AAOX.US", account=1):
    return [
        dict(
            resolve(symbol).record(),
            market="US",
            account_id=account,
            person_id=1,
            quantity="10",
            holding_status="COMPLETE",
        )
    ]


def test_known_indicators_and_initialization():
    bs = bars(300)
    m, s = technicals(bs, D(410))
    for n in (5, 10, 20, 50, 60, 120, 200, 250):
        assert m["MA" + str(n)] == sum((b.close for b in bs[-n:]), D(0)) / n
    assert m["DEV200"] == (D(410) / m["MA200"] - 1) * 100
    assert m["RSI14"] == 100
    assert abs(m["DIF"] - 7) < D("1e-20")
    assert abs(m["DEA"] - 7) < D("1e-20")
    assert abs(m["MACD"]) < D("1e-20")
    assert m["HIGH52"] == max(b.high for b in bs[-252:])
    assert m["DRAWDOWN"] == (D(410) / m["AVAILABLE_HIGH"] - 1) * 100
    assert s["DIF"][24] is None and s["DIF"][25] is not None
    assert s["DEA"][32] is None and s["DEA"][33] is not None
    assert rsi([D(1)] * 20)[-1] == 50
    assert rsi([D(i) for i in range(20, 0, -1)])[-1] == 0


def test_insufficient_no_zero_padding():
    m, s = technicals(bars(5))
    assert m["MA20"] is None and m["MA200"] is None and m["MA250"] is None
    assert m["RSI14"] is None and m["DIF"] is None and m["HIGH52"] is None
    assert m["LONG"] == "数据不足"
    assert technicals([])[0]["AVAILABLE_HIGH"] is None
    with pytest.raises(ValueError):
        DailyBar(date.today(), D(3), D(2), D(1), D(3), D(0))


def test_mapping_verified_candidates_and_proxy():
    assert resolve("AAOX.US").analysis_symbol == "AAOI.US"
    assert resolve("TSLL.US").leverage_multiple == "2"
    assert resolve("TSLS.US").direction == "空"
    proxy = resolve("TQQQ.US")
    assert proxy.relation_type == "INDEX_PROXY" and "Nasdaq-100" in proxy.benchmark
    assert resolve("QQQ.US", name="ETF").relation_type == "DIRECT"
    for held, underlying in (
        ("AVGX", "AVGO"),
        ("GGLL", "GOOGL"),
        ("MUU", "MU"),
        ("ORCX", "ORCL"),
        ("SPAL", "SPCX"),
        ("STXL", "STX"),
        ("INTW", "INTC"),
    ):
        verified = resolve(held + ".US")
        assert verified.analysis_symbol == underlying + ".US"
        assert verified.status == "VERIFIED" and verified.leverage_multiple == "2"
    korea = resolve("KORU.US")
    assert korea.status == "VERIFIED" and korea.relation_type == "INDEX_PROXY"
    assert korea.analysis_symbol == "EWY.US" and korea.leverage_multiple == "3"
    assert "MSCI Korea 25/50" in korea.benchmark and "代理" in korea.benchmark
    assert resolve("UNVERIFIED.US", "杠杆").analysis_symbol == "UNVERIFIED.US"
    assert normalize("aaox") == "AAOX.US"
    with pytest.raises(ValueError):
        normalize("bad;sql")


def test_only_current_positive_report_holdings():
    rows = (
        ReportRow(("channel", "杠杆", "AAOX", D(10)), dict(instrument_id=1, account_id=1, person_ids=(1,))),
        ReportRow(("channel", "股票", "OLD", D(0)), dict(instrument_id=2)),
        ReportRow(("channel", "股票", "REVIEW", None), dict(instrument_id=3)),
        ReportRow(("channel", "股票", "NEGATIVE", D(-2)), dict(instrument_id=4)),
    )
    analytics = SimpleNamespace(reports={"当前持仓": Report("当前持仓", (), rows)})
    instruments = {
        i: dict(symbol=s, market="US") for i, s in enumerate(("AAOX", "OLD", "REVIEW", "NEGATIVE"), 1)
    }
    ss = holding_sources(analytics, instruments, {})
    assert len(ss) == 1 and ss[0]["analysis_symbol"] == "AAOI.US"
    assert ss[0]["held_symbol"] == "AAOX.US"


def test_sync_dedup_archive_manual_and_zero_rules(tmp_path):
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    scope = QueryScope(date.today(), person_ids=(1,))
    key = key_for("demo", scope)
    assert key == key_for("demo", replace(scope, as_of=date(2027, 1, 1)))
    targets = store.sync(key, sources() + sources(account=2), NOW)
    assert len(targets) == 1 and len(targets[0]["sources"]) == 2
    assert targets[0]["alerts_enabled"] == 0
    store.sync("another-scope", sources("AAPL.US"), NOW)
    old = store.sync(key, [], NOW)
    assert old[0]["archive_state"] == "NOT_HELD" and len(old[0]["sources"]) == 2
    assert store.targets("another-scope")[0]["archive_state"] == "HELD"
    override = replace(resolve("AAOX.US"), analysis_symbol="AAOX.US", user_override=True, status="MANUAL")
    store.save_override("source1", override)
    store.sync(key, sources("AAPL.US"), NOW)
    assert store.overrides("source1")["AAOX.US"]["analysis_symbol"] == "AAOX.US"
    assert store.overrides("source2") == {}
    assert store.counts() == (0, 0)
    with store.connect() as db:
        with pytest.raises(Exception):
            db.execute("UPDATE watch_targets SET alerts_enabled=1")


def test_cache_once_and_quote_refresh_while_paused(tmp_path):
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    provider = Provider()
    clock = [NOW]
    service = MonitorService(store, provider, lambda: clock[0])
    targets = store.sync("scope", sources() + sources(account=2), NOW)
    first = service.load(targets, threading.Event())
    assert first["AAOI.US"]["price"] == 610 and first["AAOI.US"]["quote_status"] == "实时"
    assert len([c for c in provider.calls if c[0] == "history"]) == 1
    second = service.load(targets, threading.Event())
    assert provider.calls == [("quotes", ("AAOI.US",)), ("history", "AAOI.US", None)]
    assert first["AAOI.US"]["metrics"] == second["AAOI.US"]["metrics"]
    clock[0] += timedelta(seconds=61)
    service.load(targets, threading.Event())
    assert len([c for c in provider.calls if c[0] == "quotes"]) == 2
    assert store.counts() == (0, 0)


def test_failure_cached_stale_and_backoff(tmp_path):
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    provider = Provider()
    clock = [NOW]
    service = MonitorService(store, provider, lambda: clock[0])
    targets = store.sync("scope", sources(), NOW)
    service.load(targets, threading.Event())
    provider.fail = True
    clock[0] += timedelta(minutes=3)
    value = service.load(targets, threading.Event())["AAOI.US"]
    assert value["price"] == 610 and value["quote_status"] == "已过期" and value["stale"]
    assert "secret" not in value["error"] and value["bars"]
    before = len(provider.calls)
    service.load(targets, threading.Event())
    assert len(provider.calls) == before
    assert store.counts() == (0, 0)


def test_adjustment_revision_reloads_full_history(tmp_path):
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    provider = Provider()
    clock = [NOW]
    service = MonitorService(store, provider, lambda: clock[0])
    targets = store.sync("scope", sources(), NOW)
    service.load(targets, threading.Event())
    clock[0] += timedelta(days=1)
    provider.cutoff = date(2026, 10, 23)
    provider.series = bars(end=provider.cutoff)  # overlap changes: simulated split revision
    service.load(targets, threading.Event())
    history = [c for c in provider.calls if c[0] == "history"]
    assert history[-2][2] is not None and history[-1][2] is None
    assert store.bars("AAOI.US") == provider.series


def test_cancel_and_no_network_demo(tmp_path):
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    provider = Provider()
    service = MonitorService(store, provider, lambda: NOW)
    targets = store.sync("scope", sources(), NOW)
    value = service.load(targets, threading.Event(), network=False)["AAOI.US"]
    assert value["price"] is None and provider.calls == []
    event = threading.Event()
    event.set()
    assert service.load(targets, event) == {}
    assert store.counts() == (0, 0)


def test_provider_calendar_half_day_and_naive_candle_timezone(monkeypatch):
    class Context:
        def trading_days(self, *args):
            return SimpleNamespace(trading_days=[date(2026, 11, 27)], half_trading_days=[date(2026, 11, 27)])

    provider = LongbridgeMonitorProvider("not-read")
    provider.context = Context()
    assert provider.completed_day(datetime(2026, 11, 27, 18, 16, tzinfo=UTC)) == date(2026, 11, 27)
    with pytest.raises(ValueError):
        provider.completed_day(datetime(2026, 11, 27, 18, 0, tzinfo=UTC))


def test_page_zero_rules_and_chart_controls(qtbot, tmp_path):
    from PySide6.QtCore import QThreadPool

    from portfolio.ui.monitor_page import MonitoringPage

    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    window = SimpleNamespace(pool=QThreadPool(), info=None)
    page = MonitoringPage(window, store=store)
    qtbot.addWidget(page)
    page.resize(1440, 900)
    page.show()
    page.targets = store.sync("scope", sources(), NOW)
    page.market = MonitorService(store, Provider(), lambda: NOW).load(page.targets, threading.Event())
    page.draw_table()
    qtbot.wait(20)
    assert page.table.rowCount() == 1 and page.stats[2].text().startswith("0")
    assert page.chart.mas == {20, 50, 200}
    page.ma_checks[250].setChecked(True)
    assert 250 in page.chart.mas
    page.panel.setCurrentText("MACD")
    assert page.chart.panel == "MACD"
    page.range.setCurrentIndex(0)
    assert page.chart.count == 21
    page.search.setText("not-found")
    assert page.table.rowCount() == 0
    page.search.clear()
    assert page.table.rowCount() == 1
    assert store.counts() == (0, 0)
    page.close()


def test_async_import_self_filters_empty_and_source_unchanged(qtbot, demo_path, tmp_path):
    import sqlite3

    from PySide6.QtCore import QThreadPool

    from portfolio.services.analytics_service import AnalyticsService
    from portfolio.services.trades_service import TradesService
    from portfolio.ui.filters import AccountFilter
    from portfolio.ui.monitor_page import MonitoringPage

    # Prepare the synthetic acceptance fixture, never a real portfolio source.
    with sqlite3.connect(demo_path) as db:
        db.execute("UPDATE persons SET name='个人' WHERE id=1")
        db.execute("UPDATE instruments SET symbol='AAOX' WHERE id=1")
    before = hashlib.sha256(demo_path.read_bytes()).hexdigest()
    info = TradesService(demo_path).source_info()
    win = SimpleNamespace(
        pool=QThreadPool(),
        info=info,
        analytics_service=AnalyticsService(demo_path),
        analytics=None,
        accounts=AccountFilter(),
        live_panel=SimpleNamespace(path="", feed=None, observation_symbols=set()),
    )
    win.accounts.populate(info.accounts)
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    page = MonitoringPage(win, store=store)
    qtbot.addWidget(page)
    page.show()
    page.activate()
    qtbot.waitUntil(lambda: not page.busy, timeout=20000)
    assert page.targets and all(not t["alerts_enabled"] for t in page.targets)
    assert len(page.targets) == 1 and page.targets[0]["analysis_symbol"] == "AAOI.US"
    assert all(s["person_id"] == 1 for t in page.targets for s in t["sources"])
    assert all(v["price"] is None for v in page.market.values())  # never inject demo prices
    page.owner.setCurrentIndex(1)
    qtbot.waitUntil(lambda: not page.busy, timeout=20000)
    assert page.owner.currentText().startswith("全部账户")
    assert store.counts() == (0, 0)
    page.clear()
    assert page.table.rowCount() == 0
    assert hashlib.sha256(demo_path.read_bytes()).hexdigest() == before
    win.pool.waitForDone(1000)
    page.close()


def test_invalid_future_quote_never_becomes_live(tmp_path):
    provider = Provider()
    provider.quotes = lambda symbols: {
        s: dict(price="NaN", stamp=(NOW + timedelta(hours=1)).isoformat(), session="Intraday")
        for s in symbols
    }
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    service = MonitorService(store, provider, lambda: NOW)
    result = service.load(store.sync("s", sources(), NOW), threading.Event())["AAOI.US"]
    assert result["quote_status"] != "实时" and result["price"] == bars()[-1].close
    assert store.counts() == (0, 0)


def test_main_navigation_and_source_switch_clear(qtbot, tmp_path, monkeypatch):
    import portfolio.services.quote_store as quote_module
    from portfolio.services.quote_store import QuoteStore
    from portfolio.ui.live_quotes import LiveQuotesPanel
    from portfolio.ui.main_window import MainWindow

    monkeypatch.setattr(
        quote_module, "QuoteStore", lambda *a, **kw: QuoteStore(tmp_path / "isolated_quotes.sqlite3")
    )
    monkeypatch.setattr(LiveQuotesPanel, "refresh_channel_cash", lambda *a, **kw: None)
    win = MainWindow(settings=False)
    qtbot.addWidget(win)
    win.show()
    win.navigation.select(21, None)
    assert win.pages.currentIndex() == 21 and win.heading.text() == "行情监控中心"
    assert win.pages.count() == 22 and win.stock_analysis_page is win.pages.widget(20)
    for index in (19, 20, 0, 21):
        win.navigation.select(index, None)
        assert win.pages.currentIndex() == index
    win.monitor_page.clear()
    assert win.monitor_page.table.rowCount() == 0
    win.close()


def test_empty_list_and_calendar_failure_backoff(tmp_path):
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    provider = Provider()
    clock = [NOW]
    calendar_calls = []

    def broken(now):
        calendar_calls.append(now)
        raise RuntimeError("network")

    provider.completed_day = broken
    service = MonitorService(store, provider, lambda: clock[0])
    assert service.load([], threading.Event()) == {} and calendar_calls == []
    targets = store.sync("scope", sources(), NOW)
    service.load(targets, threading.Event())
    clock[0] += timedelta(seconds=10)
    service.load(targets, threading.Event())
    assert len(calendar_calls) == 1
    clock[0] += timedelta(minutes=5)
    service.load(targets, threading.Event())
    assert len(calendar_calls) == 2 and store.counts() == (0, 0)


def test_non_us_holdings_retained_without_wrong_us_calendar(tmp_path):
    row = ReportRow(("channel", "股票", "00700", D(10)), dict(instrument_id=1, account_id=1, person_ids=(1,)))
    analytics = SimpleNamespace(reports={"当前持仓": Report("当前持仓", (), (row,))})
    ss = holding_sources(analytics, {1: dict(symbol="00700", market="HK")}, {})
    assert ss[0]["analysis_symbol"] == "00700.HK"
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    provider = Provider()
    result = MonitorService(store, provider, lambda: NOW).load(store.sync("s", ss, NOW), threading.Event())
    assert result["00700.HK"]["price"] is None and provider.calls == []


def test_intraday_split_rechecks_adjusted_overlap(tmp_path):
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    provider = Provider()
    clock = [NOW]
    service = MonitorService(store, provider, lambda: clock[0])
    targets = store.sync("scope", sources(), NOW)
    service.load(targets, threading.Event())
    # Same last completed daily date, new US session. A forward adjustment changed.
    clock[0] = datetime(2026, 10, 23, 21, tzinfo=UTC)
    provider.series = [
        DailyBar(b.day, b.open / 2, b.high / 2, b.low / 2, b.close / 2, b.volume) for b in provider.series
    ]
    service.load(targets, threading.Event())
    assert store.bars("AAOI.US")[-1].close == D(300)
    assert len([c for c in provider.calls if c[0] == "history"]) == 3


def test_archived_target_history_view_uses_cache_only(tmp_path):
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    provider = Provider()
    service = MonitorService(store, provider, lambda: NOW)
    service.load(store.sync("scope", sources(), NOW), threading.Event())
    before = list(provider.calls)
    archived = store.sync("scope", [], NOW)
    result = service.load(archived, threading.Event())["AAOI.US"]
    assert result["bars"] and result["quote_status"] == "已不再持有 · 缓存"
    assert provider.calls == before and store.counts() == (0, 0)


def test_sdk_naive_timestamp_uses_system_local_not_utc(monkeypatch):
    import time
    from types import SimpleNamespace

    provider = LongbridgeMonitorProvider("not-read")
    sample = SimpleNamespace(
        timestamp=datetime(2026, 10, 24, 4, 0), open=D(10), high=D(12), low=D(9), close=D(11), volume=100
    )

    class Context:
        def history_candlesticks_by_offset(self, *args):
            return [sample]

    provider.context = Context()
    try:
        with monkeypatch.context() as env:
            env.setenv("TZ", "Asia/Shanghai")
            time.tzset()
            values = provider.history("AAOI.US", None, date(2026, 10, 23))
            assert values[0].day == date(2026, 10, 23)
    finally:
        time.tzset()


def test_multi_owner_sources_keep_person_quantity_not_group_total():
    row = ReportRow(
        ("channel", "杠杆", "AAOX", D(10)), dict(instrument_id=1, account_id=1, person_ids=(1, 2))
    )
    analytics = SimpleNamespace(
        reports={"当前持仓": Report("当前持仓", (), (row,))},
        positions={(1, 1, 1): SimpleNamespace(quantity=D(6)), (2, 1, 1): SimpleNamespace(quantity=D(4))},
    )
    values = holding_sources(analytics, {1: dict(symbol="AAOX", market="US")}, {})
    assert [(s["person_id"], s["quantity"]) for s in values] == [(1, "6"), (2, "4")]
