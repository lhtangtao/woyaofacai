"""Web adapter for the native today's P&L pipeline; source ledgers stay read-only.

Only orchestration and JSON projection live here. All financial values and stored
curves come from the same engines/cache used by LiveQuotesPanel/IntradayPnlChart.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo

from portfolio.db.connection import SourceError, readonly_connection
from portfolio.db.repositories.analytics import instrument_catalog
from portfolio.domain.analytics import ReportRow, strict_sum
from portfolio.domain.live_quotes import NY, SESSION_LABELS, UTC, us_session
from portfolio.engine.live_valuation import live_holdings
from portfolio.engine.today_holdings import (
    channel_summary,
    lifetime_summary,
    lifetime_total,
    today_holdings,
    today_summary,
)
from portfolio.paths import CREDENTIALS_PATH
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.intraday_history import rebuild_history, scope_key, source_version
from portfolio.services.live_quotes import QuoteFeed
from portfolio.services.quote_store import QuoteStore


def desktop_quote_store():
    # GenericDataLocation is independent of whether Flask has a QApplication.
    from PySide6.QtCore import QStandardPaths
    root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation))
    return QuoteStore(root / 'MoneyPortfolio' / 'Money Portfolio' / 'live_quotes.sqlite3')


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

    def quote_snapshot(self, analytics, instruments, enabled, now):
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
        end = datetime.combine(date.fromisoformat(day), time(20), NY)
        complete = (history and not warning and history[-1][0] >= end
                    and all(r['pnl'] is not None for _, _, rows in history for r in rows))
        if complete or not self.network:
            return {'pending': False, 'message': ''}
        key = (scope_key(self.path, analytics.scope), day, source_version(self.path))
        with self.lock:
            previous = self.jobs.get(key)
            if previous and (previous['pending'] or (now - previous['at']).total_seconds() < 60):
                return {k: previous[k] for k in ('pending', 'message')}
            if sum(j['pending'] for j in self.jobs.values()) >= 8:
                return {'pending': False, 'message': '历史查询繁忙，请稍后刷新；已有缓存保留。'}
            job = {'pending': True, 'at': now, 'message': '正在补取长桥五分钟行情，已有缓存优先展示。'}
            self.jobs[key] = job
            for old in list(self.jobs):
                if len(self.jobs) <= 64:
                    break
                if not self.jobs[old]['pending']:
                    del self.jobs[old]

            def task():
                try:
                    message = rebuild_history(self.path, analytics, self.credentials, self.store, day, now)
                except Exception:
                    message = '历史盈亏补取失败，请检查网络、长桥权限和流水时间；已有缓存保留。'
                with self.lock:
                    job.update(pending=False, message=message)
            self.pool.submit(task)
            return {'pending': True, 'message': job['message']}

    def curve(self, analytics, report, session, now, ticker='', channel=None):
        # Reuse the native non-trading-day resolution without instantiating Qt UI.
        from portfolio.ui.intraday_pnl import _resolve_effective_day
        day, target, fallback = _resolve_effective_day(analytics.scope.as_of, session, report, analytics.trading_days)
        key = scope_key(self.path, analytics.scope)
        history = self.store.load_historical_pnl(key, day, source_version(self.path))
        warning = self.store.load_historical_pnl_warning(key, day) if history else ''
        observations = history or self.store.load_minute_pnl(key, day)
        job = self._backfill(analytics, day, history, warning, now)
        samples = {}
        with localcontext() as context:
            context.prec = 50
            for stamp, phase, rows in observations:
                selected = [r for r in rows if (channel is None or r['channel'] == channel)
                            and ticker.casefold().strip() in r['ticker'].casefold()]
                value = (sum((Decimal(r['pnl']) for r in selected), Decimal(0))
                         if selected and all(r['pnl'] is not None for r in selected) else None)
                samples[stamp.astimezone(UTC)] = (value, phase)
        target_date = date.fromisoformat(day)
        start = datetime.combine(target_date - timedelta(days=1), time(20), NY).astimezone(UTC)
        end = datetime.combine(target_date, time(20), NY).astimezone(UTC)
        interval = timedelta(minutes=5 if history else 1)
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
                               interval_minutes=5 if history else 1, historical=bool(history),
                               warning=warning, **job,
                               boundaries=[{'time': t, 'label': s} for t, s in boundaries],
                               complete_points=len(valid), latest=valid[-1] if valid else None,
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
        if enabled and session is not None and session.start <= now < session.end and report.rows:
            # Same minute observations as the native panel; never mix them
            # with the separate five-minute reconstruction.
            self.store.save_minute_pnl(scope_key(self.path, scope),
                                      report.rows[0].metadata['trading_day'], now, session.name,
                                      [dict(channel=r.values[0], ticker=r.values[1],
                                            pnl=str(r.values[6]) if r.values[6] is not None
                                            and r.metadata.get('live_fresh') else None)
                                       for r in report.rows])
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
        selected_channels = tuple(c for c in channels if channel is None or c[0] == channel)
        held_symbols = {instruments[r.metadata['instrument_id']]['symbol'] + '.US'
                        for r in live.rows if r.metadata['instrument_id'] in instruments}
        quote_times = [q.timestamp for (symbol, _), q in quotes.items() if symbol in held_symbols
                       and q.timestamp <= now + timedelta(seconds=5)]
        result = dict(status='ok', name='今日盈亏', columns=report.columns,
                      rows=[r.values for r in rows], items=[{**dict(zip(report.columns, r.values)),
                           '_meta': r.metadata} for r in rows], total_rows=len(rows), note=report.note,
                      summary=dict(pnl=pnl, rate=rate, up=up, down=down, flat=flat, unknown=unknown,
                                   cumulative=cumulative),
                      channels=[dict(channel=c, pnl=p, asset=a) for c, p, a in channels],
                      selected_channels=[dict(channel=c, pnl=p, asset=a) for c, p, a in selected_channels],
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
