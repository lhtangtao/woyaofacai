"""Web adapter for the native today's P&L pipeline; source ledgers stay read-only.

Only orchestration and JSON projection live here. All financial values and stored
curves come from the same engines/cache used by LiveQuotesPanel/IntradayPnlChart.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo

from portfolio.db.connection import SourceError, readonly_connection
from portfolio.db.repositories.analytics import instrument_catalog, people_catalog
from portfolio.domain.analytics import ReportRow, strict_sum
from portfolio.domain.live_quotes import NY, SESSION_LABELS, UTC, us_session
from portfolio.engine.live_valuation import live_holdings
from portfolio.engine.today_holdings import (
    channel_summary,
    lifetime_summary,
    lifetime_total,
    today_holdings,
    today_summary,
    today_summary_row,
)
from portfolio.paths import CREDENTIALS_PATH
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.intraday_history import rebuild_history, scope_key, source_version
from portfolio.services.live_quotes import QuoteFeed
from portfolio.services.quote_store import QuoteStore


def desktop_quote_store():
    from portfolio.paths import quote_cache_path
    return QuoteStore(quote_cache_path())



def exact_json(value):
    """Keep Decimal exact on the wire; JS converts only for display/chart pixels."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: exact_json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [exact_json(v) for v in value]
    return value


class WebTodayService:
    def __init__(self, path, *, store=None, credentials_path=CREDENTIALS_PATH,
                 clock=None, network=True):
        self.path = Path(path).expanduser().resolve()
        self.store = store if store is not None else desktop_quote_store()
        self.store.source = self.path
        self.credentials = Path(credentials_path) if credentials_path else None
        self.clock = clock or (lambda: datetime.now(UTC))
        self.network = network
        self.analytics = AnalyticsService(self.path, quote_store=self.store)
        self.lock = RLock()
        self.feed = None
        self.symbols = set()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='web-intraday')
        self.jobs = {}

    def close(self):
        if self.feed:
            self.feed.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def people_snapshot(self, scope, *, enabled=True):
        """Native person P/L, with permissions applied before any calculation."""
        from portfolio.domain.models import Ledger
        from portfolio.engine.people_pnl import PeopleBundle, people_reports
        version = source_version(self.path)
        aggregate = self.analytics.calculate(scope)
        with readonly_connection(self.path) as connection:
            instruments = instrument_catalog(connection)
            people = people_catalog(connection, scope)
        persons = tuple((person, name, self.analytics.calculate(replace(scope, person_ids=(person,))))
                        for person, name in people)
        # Restricted users cannot access the real broker ledger or residuals
        # belonging to other people; use their aggregate as the reconciliation base.
        broker = (self.analytics.calculate(replace(scope, ledger=Ledger.BROKER, person_ids=None))
                  if scope.person_ids is None else aggregate)
        now = self.clock()
        status, session, quotes = self.quote_snapshot(aggregate, instruments, enabled, now)
        summary, details, broker_report = people_reports(PeopleBundle(aggregate, persons, broker), instruments, quotes, session, now)
        if source_version(self.path) != version:
            raise SourceError('源库在生成快照期间发生变化，请刷新重试。')
        return exact_json(dict(status='ok', columns=summary.columns,
                               rows=[r.values for r in summary.rows],
                               broker=broker_report.values if broker_report else None,
                               detail_columns=details.columns if details else None,
                               detail_rows=[r.values for r in details.rows] if details else None,
                               note=summary.note,
                               quote_status=status, as_of=scope.as_of))

    def closed_report(self, analytics):
        from portfolio.services.closed_quotes import closed_current_prices
        now = self.clock()
        with readonly_connection(self.path) as connection:
            instruments = instrument_catalog(connection)
        report = analytics.reports['清仓收益']
        symbols = {instruments[r.metadata['instrument_id']]['symbol'] + '.US'
                   for r in report.rows if instruments.get(r.metadata['instrument_id'], {}).get('market') == 'US'}
        _, session, quotes = self.quote_snapshot(analytics, instruments, True, now, extra_symbols=symbols)
        cached = self.store.load(now)[0]
        return closed_current_prices(report, instruments, {**cached, **quotes}, now, session,
                                     analytics.scope.reporting_currency)

    def quote_snapshot(self, analytics, instruments, enabled, now, *, extra_symbols=()):
        quotes, days, halves, covered = self.store.load(now)
        current = analytics.scope.as_of == now.astimezone(ZoneInfo('Asia/Shanghai')).date()
        valid_calendar = covered and covered[0] <= now.astimezone(NY).date() <= covered[1]
        session = us_session(now, days, halves) if valid_calendar else None
        if not current or not enabled:
            return '历史截止日期：使用收盘数据' if not current else '实时行情已关闭：使用收盘数据', None, {}
        status = '本机行情缓存 · 10秒刷新' if session else ('美股休市 · 收盘参考' if valid_calendar else '交易日历未就绪 · 收盘参考')
        if self.network and self.credentials and self.credentials.is_file():
            symbols = {instruments[r.metadata['instrument_id']]['symbol'] + '.US'
                       for r in analytics.reports['当前持仓'].rows
                       if instruments.get(r.metadata['instrument_id'], {}).get('market') == 'US'}
            symbols |= set(extra_symbols)
            with self.lock:
                self.symbols |= symbols
                if len(self.symbols) > 500:
                    return '超过长桥500只订阅上限，请缩小范围；使用缓存', session, quotes
                if self.feed is None:
                    self.feed = QuoteFeed(self.credentials, store=self.store)
                    self.feed.set_symbols(self.symbols)
                    self.feed.start()
                else:
                    self.feed.set_symbols(self.symbols)
                status, session, quotes = self.feed.snapshot()
        elif self.network:
            status += ' · 未配置长桥凭证，仅使用缓存'
        return status, session, quotes

    def _backfill(self, analytics, day, history, warning, now):
        from portfolio.services.intraday_pipeline import pipeline_status
        status=pipeline_status(self.path)
        return {'pending':status.get('state') in ('INITIALIZING','STARTING','COLLECTING'),
                'message':status.get('message','五分钟明细由后台统一任务生成。')}

    def curve(self, analytics, report, session, now, ticker='', channel=None):
        # Reuse the native non-trading-day resolution without instantiating Qt UI.
        from portfolio.ui.intraday_pnl import _resolve_effective_day
        day, target, fallback = _resolve_effective_day(analytics.scope.as_of, session, report, analytics.trading_days)
        from portfolio.services.derived_queries import day_history
        history, cache_state = day_history(self.path, self.store, analytics.scope, day)
        report_day = next((r.metadata.get('trading_day') for r in report.rows if r.metadata.get('trading_day')), '') if report and report.rows else ''
        if not history and report_day and report_day != day and session is None:
            fallback_hist, fb_state = day_history(self.path, self.store, analytics.scope, report_day)
            if fallback_hist:
                history = fallback_hist
                day = report_day
                fallback = True
                cache_state = fb_state
        warning = cache_state.get('message', '')
        observations=history
        job = self._backfill(analytics, day, history, warning, now)
        samples = {};parts={}
        with localcontext() as context:
            context.prec = 50
            for stamp, phase, rows in observations:
                selected = [r for r in rows if (channel is None or r['channel'] == channel)
                            and ticker.casefold().strip() in r['ticker'].casefold()]
                value = (sum((Decimal(r['pnl']) for r in selected), Decimal(0))
                         if selected and all(r['pnl'] is not None for r in selected) else None)
                samples[stamp.astimezone(UTC)] = (value, phase)
                from portfolio.engine.today_holdings import intraday_scope_parts
                parts[stamp.astimezone(UTC)]=intraday_scope_parts(selected)
        target_date = date.fromisoformat(day)
        start = datetime.combine(target_date - timedelta(days=1), time(20), NY).astimezone(UTC)
        end = datetime.combine(target_date, time(20), NY).astimezone(UTC)
        interval = timedelta(minutes=5)
        points, stamp = [], start
        while stamp <= end:
            value, phase = samples.get(stamp, (None, None))
            points.append({'time': stamp, 'pnl': value, 'session': phase})
            stamp += interval
        valid = [p for p in points if p['pnl'] is not None]
        boundaries = [(start, '夜盘'),
                      (datetime.combine(target_date, time(4), NY).astimezone(UTC), '盘前'),
                      (datetime.combine(target_date, time(9, 30), NY).astimezone(UTC), '盘中'),
                      (datetime.combine(target_date, time(16), NY).astimezone(UTC), '盘后')]
        return exact_json(dict(day=day, target_day=target, fallback=fallback, points=points,
                               interval_minutes=5, historical=True,
                               warning=warning, **job,
                               boundaries=[{'time': t, 'label': s} for t, s in boundaries],
                               complete_points=len(valid), latest=valid[-1] if valid else None,
                               pnl_scope='ALL_DAY_TRADES',scope_parts=parts.get(valid[-1]['time']) if valid else None,
                               high=max(valid, key=lambda p: p['pnl']) if valid else None,
                               low=min(valid, key=lambda p: p['pnl']) if valid else None))

    def snapshot(self, scope, *, ticker='', channel=None, enabled=True, sort_col=None,
                 descending=True, include_curve=True):
        now = self.clock()
        version = source_version(self.path)
        analytics = self.analytics.calculate(scope)
        with readonly_connection(self.path) as connection:
            instruments = instrument_catalog(connection)
        status, session, quotes = self.quote_snapshot(analytics, instruments, enabled, now)
        live, total, floating, fresh = live_holdings(analytics, instruments, quotes, session, now)
        report = today_holdings(analytics, live, session)
        if session is None:
            from portfolio.ui.intraday_pnl import _resolve_effective_day
            from portfolio.services.derived_queries import day_history
            from portfolio.engine.today_holdings import align_with_intraday_history
            eff_day, _, is_fb = _resolve_effective_day(analytics.scope.as_of, session, report, analytics.trading_days)
            hist, _ = day_history(self.path, self.store, analytics.scope, eff_day)
            report_day = next((r.metadata.get('trading_day') for r in report.rows if r.metadata.get('trading_day')), '')
            if not hist and report_day and report_day != eff_day:
                hist, _ = day_history(self.path, self.store, analytics.scope, report_day)
                if hist:
                    eff_day = report_day
                    is_fb = True
            if hist and not is_fb:
                report = align_with_intraday_history(report, hist)
        overview_rows = tuple(r for r in report.rows if ticker.casefold().strip() in str(r.values[1]).casefold())
        rows = [r for r in overview_rows if channel is None or r.values[0] == channel]
        # ReportPage re-bases the displayed weight after its local ticker/channel
        # filter (including its Decimal display precision); engine values stay intact.
        denominator = sum((r.values[11] for r in rows if isinstance(r.values[11], Decimal)), Decimal(0))
        if denominator > 0:
            rows = [ReportRow((*r.values[:13], r.values[11] / denominator
                              if isinstance(r.values[11], Decimal) and r.values[11] > 0 else None),
                             r.metadata) for r in rows]
        index = report.columns.index(sort_col) if sort_col in report.columns else 6
        # Unknown values always last, including descending order.
        rows = (sorted((r for r in rows if r.values[index] is not None),
                       key=lambda r: (0, r.values[index]) if isinstance(r.values[index], (Decimal, int))
                       else (1, str(r.values[index]).casefold()), reverse=descending)
                + [r for r in rows if r.values[index] is None])
        pnl, rate, up, down, flat, unknown = today_summary(rows)
        cumulative = lifetime_total(lifetime_summary(analytics, live), channel)
        curve = self.curve(analytics, report, session, now, ticker, channel) if include_curve else None
        channels = channel_summary(overview_rows)
        channel_rates = {
            name: today_summary(tuple(r for r in overview_rows if r.values[0] == name))[1]
            for name, _, _ in channels
        }
        selected_channels = tuple(c for c in channels if channel is None or c[0] == channel)
        # Aggregate only the visible holdings. The cumulative card intentionally
        # retains the native all-history scope, including closed securities.
        summary_row = today_summary_row(rows, report.columns, channel).values
        held_symbols = {instruments[r.metadata['instrument_id']]['symbol'] + '.US'
                        for r in live.rows if r.metadata['instrument_id'] in instruments}
        quote_times = [q.timestamp for (symbol, _), q in quotes.items() if symbol in held_symbols
                       and q.timestamp <= now + timedelta(seconds=5)]
        result = dict(status='ok', name='今日盈亏', columns=report.columns,
                      rows=[r.values for r in rows], items=[{**dict(zip(report.columns, r.values)),
                           '_meta': r.metadata} for r in rows], total_rows=len(rows), note=report.note,
                      summary=dict(pnl=pnl, rate=rate, up=up, down=down, flat=flat, unknown=unknown,
                                   cumulative=cumulative),
                      summary_row=summary_row,
                      channels=[dict(channel=c, pnl=p, asset=a, rate=channel_rates[c]) for c, p, a in channels],
                      selected_channels=[dict(channel=c, pnl=p, asset=a, rate=channel_rates[c]) for c, p, a in selected_channels],
                      asset_total=strict_sum(c[2] for c in selected_channels),
                      available_channels=sorted({r.values[0] for r in report.rows}),
                      tickers=sorted({r.values[1] for r in report.rows}),
                      quote=dict(status=status, session=SESSION_LABELS.get(session.name) if session else '收盘参考',
                                 fresh=fresh, count=len(live.rows), total_asset=total, floating=floating,
                                 refreshed_at=now, enabled=enabled,
                                 latest_at=max(quote_times) if quote_times else None,
                                 reference_dates=sorted({r.metadata['valuation_date'] for r in live.rows
                                                       if r.metadata.get('valuation_date')})),
                      source=dict(path=str(self.path), identity=analytics.source_identity,
                                  selected_day=scope.as_of, profit_day=analytics.latest_profit_day,
                                  person_ids=scope.person_ids), curve=curve)
        if source_version(self.path) != version:
            raise SourceError('源库在生成快照期间发生变化，请刷新重试。')
        return exact_json(result)
