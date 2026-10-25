"""Read-only Longbridge earnings calendar with bounded daily memory caching."""
import calendar
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from threading import RLock
from zoneinfo import ZoneInfo

from portfolio.services.earnings_links import default_target
from portfolio.services.live_quotes import credentials

SHANGHAI = ZoneInfo('Asia/Shanghai')
MARKET_ZONES = {'US': 'America/New_York', 'HK': 'Asia/Hong_Kong', 'SH': 'Asia/Shanghai', 'SZ': 'Asia/Shanghai'}


@dataclass(frozen=True)
class EarningsEvent:
    symbol: str
    name: str
    day: date
    time_label: str
    beijing: str
    content: str = ''
    confirmed_published: bool = False
    verification_source: str = ''
    session_change: float = None
    session_checked_at: datetime | None = None
    session_final: bool = False
    session_status: str = ''
    is_macro: bool = False
    rating: str = ''
    expectations: str = ''
    highlights: str = ''
    macro_ticker: str = ''


def reconciled_events(events, *, year=None, month=None):
    """Verified announcements correct or fill provider events in this month only."""
    import json
    from importlib.resources import files
    verified = json.loads(files('portfolio').joinpath('resources/verified_earnings.json').read_text(encoding='utf-8'))
    events = list(events)
    periods = {(year, month)} if year is not None and month is not None else {(e.day.year, e.day.month) for e in events}
    for item in verified:
        day = date.fromisoformat(item['day'])
        if (day.year, day.month) not in periods:
            continue
        if not any(e.symbol == item['symbol'] and abs((e.day-day).days) <= 1 for e in events):
            phase = item.get('phase', '')
            label = {'Post': '盘后', 'Pre': '盘前', 'Intraday': '盘中'}.get(phase, phase or '时间未公布')
            if item.get('time'):
                label = f"{item['time']} 美东 · {label}"
            elif label and not label.endswith('（美东）'):
                label = f"{label}（美东）"
            events.append(EarningsEvent(
                symbol=item['symbol'],
                name=item.get('name', item['symbol']),
                day=day,
                time_label=label,
                beijing='—',
                content=item.get('highlights', '长桥日历未返回该日程，依据已核实的公司公告/宏观日程补充。'),
                confirmed_published=item.get('confirmed_published', False),
                verification_source=item.get('source', ''),
                is_macro=item.get('is_macro', False),
                rating=item.get('rating', ''),
                expectations=item.get('expectations', ''),
                highlights=item.get('highlights', ''),
                macro_ticker=item.get('macro_ticker', ''),
            ))
    result = {}
    for event in events:
        for item in verified:
            day = date.fromisoformat(item['day'])
            if event.symbol != item['symbol'] or abs((event.day - day).days) > 1:
                continue
            phase = {'Post': '盘后', 'Pre': '盘前', 'Intraday': '盘中'}.get(item['phase'], item.get('phase', '时间未公布'))
            label_phase = f"{item['time']} 美东 · {phase}" if item.get('time') else f"{phase}（美东）"
            old_phase = 'Post' if '盘后' in event.time_label else 'Pre' if '盘前' in event.time_label else 'Intraday' if '盘中' in event.time_label else None
            same_session = event.day == day and old_phase == item['phase']
            change = event.session_change if same_session else None
            # A phase is a window, not an exact release time or earnings-call time.
            hour = 16 if item['phase'] == 'Post' else (8 if item['phase'] == 'Pre' else 10)
            local = datetime.combine(day, datetime.min.time().replace(hour=hour), ZoneInfo('America/New_York'))
            beijing_day = local.astimezone(SHANGHAI).strftime('%m-%d')
            event = replace(
                event,
                day=day,
                time_label=label_phase,
                beijing=beijing_day+' 北京时间（具体时刻未公布）' if not item.get('time') else local.astimezone(SHANGHAI).strftime('%m-%d %H:%M 北京时间'),
                confirmed_published=event.confirmed_published or item.get('confirmed_published', False),
                verification_source=item.get('source', '') if item.get('confirmed_published') or not event.confirmed_published else event.verification_source,
                session_change=change,
                session_checked_at=event.session_checked_at if same_session else None,
                session_final=event.session_final if same_session else False,
                is_macro=item.get('is_macro', getattr(event, 'is_macro', False)),
                rating=item.get('rating', getattr(event, 'rating', '')),
                expectations=item.get('expectations', getattr(event, 'expectations', '')),
                highlights=item.get('highlights', getattr(event, 'highlights', '')),
                macro_ticker=item.get('macro_ticker', getattr(event, 'macro_ticker', '')),
            )
            break
        if event.beijing == '—' and (event.symbol.endswith('.US') or getattr(event, 'is_macro', False)) and any(p in event.time_label for p in ('盘前', '盘后', '盘中')):
            hour = 16 if '盘后' in event.time_label else 8 if '盘前' in event.time_label else 10
            local = datetime.combine(event.day, datetime.min.time().replace(hour=hour), ZoneInfo('America/New_York'))
            event = replace(event, beijing=local.astimezone(SHANGHAI).strftime('%m-%d')+' 北京时间')
        key = (event.symbol, event.day, event.time_label)
        previous = result.get(key)
        if previous is None or event.session_change is not None or previous.session_change is None:
            result[key] = event
    return tuple(sorted(result.values(), key=lambda e: (e.day, e.symbol)))


def release_status(event, now=None):
    if getattr(event, 'is_macro', False):
        now = now or datetime.now(timezone.utc)
        ny = now.astimezone(ZoneInfo('America/New_York'))
        if ny.date() < event.day:
            return '待发布（宏观日程）'
        elif ny.date() == event.day:
            if '盘前' in event.time_label and (ny.hour < 8 or (ny.hour == 8 and ny.minute < 30)):
                return '待发布（盘前 08:30）'
            elif '14:00' in event.time_label and ny.hour < 14:
                return '待发布（盘中 14:00）'
            elif '10:00' in event.time_label and ny.hour < 10:
                return '待发布（盘初 10:00）'
            elif '13:00' in event.time_label and ny.hour < 13:
                return '待发布（盘中 13:00）'
            return '已发布（宏观数据）'
        return '已发布（历史宏观数据）'

    if event.confirmed_published:
        return '已发布（长桥实际财报确认）' if event.verification_source.startswith('longbridge:') else '已发布（公司公告确认）'
    now = now or datetime.now(timezone.utc)
    market = event.symbol.rsplit('.', 1)[-1]
    local = now.astimezone(ZoneInfo(MARKET_ZONES.get(market, 'America/New_York')))
    if local.date() < event.day:
        return '待发布'
    if local.date() == event.day and '盘后' in event.time_label and local.hour < 16:
        return '待发布（盘后）'
    if local.date() == event.day and '盘前' in event.time_label and local.hour < 9:
        return '待发布（盘前）'
    return '发布状态待确认'


@dataclass(frozen=True)
class EarningsMonth:
    events: tuple
    fetched_at: datetime
    warning: str = ''
    coverage_events: tuple = ()
    coverage_warning: str = ''


def session_window(event):
    from datetime import time
    if getattr(event, 'is_macro', False):
        if '盘前' in event.time_label:
            start, end = time(4), time(9, 30)
        elif '盘后' in event.time_label:
            start, end = time(16), time(20)
        else:
            start, end = time(9, 30), time(16)
        zone = ZoneInfo('America/New_York')
        return datetime.combine(event.day, start, zone), datetime.combine(event.day, end, zone)

    if not event.symbol.endswith('.US'):
        return None
    if '盘前' in event.time_label:
        start, end = time(4), time(9, 30)
    elif '盘后' in event.time_label:
        start, end = time(16), time(20)
    else:
        return None
    zone = ZoneInfo('America/New_York')
    return datetime.combine(event.day, start, zone), datetime.combine(event.day, end, zone)


def session_refresh_due(event, now=None):
    now = now or datetime.now(timezone.utc)
    window = session_window(event)
    if not window or now < window[0]:
        return False
    if event.session_change is not None and event.session_final:
        return False
    # Live sessions update once a minute; missing historical data retries every 5 minutes.
    delay = 60 if now < window[1] else 300
    return event.session_checked_at is None or (now-event.session_checked_at).total_seconds() >= delay


def parse_event(info, market):
    import re
    symbol = str(getattr(info, 'symbol', '')).upper()
    if not symbol:
        return None
    if '.' not in symbol:
        symbol += '.'+market
    raw_date = str(getattr(info, 'date', ''))
    match = re.search(r'(\d{4})[.-](\d{2})[.-](\d{2})', raw_date)
    if not match:
        return None
    try:
        day = date(*map(int, match.groups()))
    except ValueError:
        return None
    phase = str(getattr(info, 'date_type', '')).strip()
    label = {'Pre': '盘前', 'Post': '盘后', 'Intraday': '盘中', 'BMO': '盘前', 'AMC': '盘后'}.get(phase, phase or '时间未公布')
    # Generic event timestamps can be date placeholders. Only an explicit
    # financial_market_time clock is treated as an announced release time.
    clock = str(getattr(info, 'financial_market_time', '')).strip()
    exact = re.fullmatch(r'([01]\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?', clock)
    beijing = '—'
    if exact:
        local = datetime.combine(day, datetime.strptime(clock[:5], '%H:%M').time(), ZoneInfo(MARKET_ZONES[market]))
        label = clock[:5]+' '+('美东' if market == 'US' else '当地')+(' · '+label if phase else '')
        beijing = local.astimezone(SHANGHAI).strftime('%m-%d %H:%M')+' 北京时间'
    elif clock:
        label += ' · '+clock
    from decimal import Decimal, InvalidOperation
    disclosed = False
    for value in getattr(info, 'data_kv', ()):
        if getattr(value, 'value_type', '') not in ('actual_eps', 'actual_revenue'):
            continue
        try:
            disclosed |= Decimal(str(value.value_raw)).is_finite()
        except (InvalidOperation, TypeError, ValueError):
            pass
    return EarningsEvent(symbol, str(getattr(info, 'counter_name', '')) or symbol, day, label, beijing,
                         str(getattr(info, 'content', '')), disclosed, 'longbridge:actual' if disclosed else '')


def holding_targets(analytics, instruments, overrides, include_verified=True):
    """Deduplicate current securities across channels; never infer ETF issuers."""
    result = {}
    if analytics and '当前持仓' in getattr(analytics, 'reports', {}):
        for row in analytics.reports['当前持仓'].rows:
            if row.values[3] is not None and row.values[3] <= 0:
                continue
            info = instruments.get(row.metadata['instrument_id'], {})
            market = info.get('market', 'US')
            symbol = str(info.get('symbol') or row.values[2]).upper()+'.'+market
            automatic = default_target(symbol, info.get('category', row.values[1]), str(info.get('name') or ''))
            target = overrides.get(symbol, automatic)
            result[symbol] = (target, str(info.get('name') or row.values[2]))
    if include_verified:
        import json
        from importlib.resources import files
        try:
            verified = json.loads(files('portfolio').joinpath('resources/verified_earnings.json').read_text(encoding='utf-8'))
            for item in verified:
                sym = item['symbol']
                if sym not in result and not any(v[0] == sym for v in result.values()):
                    label = item.get('name', sym)
                    if item.get('is_macro'):
                        result[sym] = (sym, f"{label} [宏观焦点]")
                    else:
                        result[sym] = (sym, f"{label} [重点盯盘]")
        except Exception:
            pass
    return result


def _fetch_day(context, category, market, day, first, last, cancel):
    offset, seen = 0, set()
    found = []
    warnings = set()
    for _ in range(20):
        if cancel and cancel.is_set():
            raise ValueError('财报查询已取消。')
        response = context.finance_calendar(category, day.isoformat(), day.isoformat(), market, count=200, offset=offset)
        groups = response.list
        raw = [info for group in groups for info in group.infos]
        keys = {(getattr(i, 'id', ''), getattr(i, 'symbol', ''), getattr(i, 'date', '')) for i in raw}
        fresh = keys - seen
        if raw and not fresh:
            warnings.add('接口分页重复，部分财报可能未返回')
            break
        seen.update(keys)
        for info in raw:
            event = parse_event(info, market)
            if event and first <= event.day <= last:
                found.append(event)
        count = sum(getattr(group, 'count', len(group.infos)) for group in groups)
        offset += len(raw)
        if not raw or offset >= count:
            break
    else:
        warnings.add('财报数据超过分页上限，结果可能不完整')
    return found, warnings


def fetch_session_change(quote_context, symbol, day, phase, is_macro=False, macro_ticker=''):
    from portfolio.services.live_quotes import _SDK_VALUES_LOCK
    with _SDK_VALUES_LOCK:
        return _fetch_session_change(quote_context, symbol, day, phase, is_macro=is_macro, macro_ticker=macro_ticker)


def _fetch_session_change(quote_context, symbol, day, phase, is_macro=False, macro_ticker=''):
    """Retrieve pre-market, post-market, or intraday change for an earnings release day or macro event.

    If released post-market, change is (post_close - intraday_close) / intraday_close.
    If released pre-market, change is (pre_close - prev_close) / prev_close.
    If released intraday, change is (intraday_close - prev_close) / prev_close.
    For macro events, tracked against QQQ.US or specified benchmark.
    """
    target_sym = (macro_ticker or 'QQQ.US') if is_macro else symbol
    if not quote_context or phase not in ('Pre', 'Post', 'Intraday'):
        return None
    try:
        from zoneinfo import ZoneInfo

        from longbridge.openapi import AdjustType, Period, TradeSessions
        NY = ZoneInfo('America/New_York')

        # Fast path: check current quote snapshot
        if hasattr(quote_context, 'quote'):
            quotes = quote_context.quote([target_sym])
            if quotes:
                q = quotes[0]
                if phase == 'Post' and getattr(q, 'post_market_quote', None):
                    pm = q.post_market_quote
                    if pm.timestamp.astimezone(NY).date() == day and pm.prev_close and pm.prev_close > 0 and pm.last_done:
                        return float((pm.last_done - pm.prev_close) / pm.prev_close)
                elif phase == 'Pre' and getattr(q, 'pre_market_quote', None):
                    pm = q.pre_market_quote
                    if pm.timestamp.astimezone(NY).date() == day and pm.prev_close and pm.prev_close > 0 and pm.last_done:
                        return float((pm.last_done - pm.prev_close) / pm.prev_close)
                elif phase == 'Intraday':
                    if getattr(q, 'prev_close', None) and q.prev_close > 0 and getattr(q, 'last_done', None):
                        return float((q.last_done - q.prev_close) / q.prev_close)

        # Historical / candle path
        if hasattr(quote_context, 'history_candlesticks_by_date'):
            start = day - timedelta(days=5 if phase == 'Pre' else 0)
            end = day + timedelta(days=1)
            candles = quote_context.history_candlesticks_by_date(
                target_sym, Period.Min_5, AdjustType.NoAdjust, start, end, TradeSessions.All
            )
            if phase == 'Post':
                intraday = [c for c in candles if c.timestamp.astimezone(NY).date() == day and 'Intraday' in str(c.trade_session)]
                post = [c for c in candles if c.timestamp.astimezone(NY).date() == day and 'Post' in str(c.trade_session)]
                if intraday and post and intraday[-1].close > 0:
                    return float((post[-1].close - intraday[-1].close) / intraday[-1].close)
            elif phase == 'Pre':
                pre = [c for c in candles if c.timestamp.astimezone(NY).date() == day and 'Pre' in str(c.trade_session)]
                prev_intraday = [c for c in candles if c.timestamp.astimezone(NY).date() < day and 'Intraday' in str(c.trade_session)]
                if pre and prev_intraday and prev_intraday[-1].close > 0:
                    return float((pre[-1].close - prev_intraday[-1].close) / prev_intraday[-1].close)
            elif phase == 'Intraday':
                today_c = [c for c in candles if c.timestamp.astimezone(NY).date() == day and 'Intraday' in str(c.trade_session)]
                prev_c = [c for c in candles if c.timestamp.astimezone(NY).date() < day and 'Intraday' in str(c.trade_session)]
                if today_c and prev_c and prev_c[-1].close > 0:
                    return float((today_c[-1].close - prev_c[-1].close) / prev_c[-1].close)
    except Exception:
        pass
    return None


class EarningsService:
    def __init__(self, factory=None, store=None, quote_factory=None):
        self.factory = factory
        self.store = store
        self.quote_factory = quote_factory
        self.cache, self.lock = {}, RLock()

    def cache_key(self, path, year, month, markets):
        return f"{path}|{year}-{month:02d}|{','.join(sorted(markets))}"

    def get_cached(self, path, year, month, markets, max_age_seconds=3600):
        key = (str(path), year, month, tuple(sorted(markets)))
        now = datetime.now(timezone.utc)
        with self.lock:
            cached = self.cache.get(key)
            if cached and (max_age_seconds is None or (now - cached.fetched_at).total_seconds() < max_age_seconds):
                return cached
        if self.store:
            db_key = self.cache_key(path, year, month, markets)
            stored = self.store.load_earnings(db_key, max_age_seconds=max_age_seconds)
            if stored:
                with self.lock:
                    if len(self.cache) >= 12:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[key] = stored
                return stored
        return None

    def get_portfolio_cached(self, path, year, month, markets, targets):
        """Render persisted months immediately, even when network refresh is due."""
        results = []
        for offset in range(3):
            index = year*12+month-1+offset
            cached = self.get_cached(path, index//12, index%12+1, markets, max_age_seconds=None)
            if offset == 0 and cached is None:
                # If cached is None, check verified events for this month to render immediately
                verified = reconciled_events([], year=year, month=month)
                if verified:
                    events = tuple(e for e in verified if e.symbol in targets)
                    now = datetime.now(timezone.utc)
                    return EarningsMonth(events, now, '已加载焦点日程，后台更新中…', coverage_events=events, coverage_warning='')
                return None
            if cached is not None:
                results.append(cached)
        events = tuple(e for result in results for e in result.events if e.symbol in targets)
        missing = sorted(set(targets)-{e.symbol for e in events})
        warning = '未缓存的公司日程正在后台补查：'+'、'.join(missing) if missing else ''
        return replace(results[0], events=tuple(e for e in results[0].events if e.symbol in targets),
                       fetched_at=min(result.fetched_at for result in results),
                       coverage_events=events, coverage_warning=warning)

    def _get_quote_context(self, path):
        if self.quote_factory:
            return self.quote_factory(path)
        try:
            from portfolio.paths import CREDENTIALS_PATH
            from portfolio.services.live_quotes import create_context
            effective_path = path or CREDENTIALS_PATH
            return create_context(effective_path)
        except Exception:
            return None

    def _update_cache(self, key, path, year, month, markets, result):
        with self.lock:
            if len(self.cache) >= 12:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = result
        if self.store:
            try:
                self.store.save_earnings(self.cache_key(path, year, month, markets), result)
            except Exception:
                pass

    def _with_session_changes(self, events, path, targets, cancel=None, *, year=None, month=None):
        # Correct dates and merge duplicate provider events before querying prices.
        # An old percentage from a corrected date must never be reused.
        updated = list(reconciled_events(events, year=year, month=month))
        quote_ctx = None
        for idx, event in enumerate(updated):
            if cancel and cancel.is_set():
                raise ValueError('财报查询已取消。')
            if event.symbol not in (targets or ()) or not session_refresh_due(event):
                continue
            if release_status(event).startswith('待发布'):
                continue
            phase = 'Pre' if '盘前' in event.time_label else 'Post' if '盘后' in event.time_label else 'Intraday' if '盘中' in event.time_label else None
            if phase:
                if quote_ctx is None:
                    quote_ctx = self._get_quote_context(path)
                change = fetch_session_change(
                    quote_ctx,
                    event.symbol,
                    event.day,
                    phase,
                    is_macro=getattr(event, 'is_macro', False),
                    macro_ticker=getattr(event, 'macro_ticker', '')
                )
                now = datetime.now(timezone.utc)
                window = session_window(event)
                updated[idx] = replace(
                    event,
                    session_change=change,
                    session_checked_at=now,
                    session_final=change is not None and bool(window and now >= window[1]),
                    session_status='时段已结束' if change is not None and window and now >= window[1] else '时段内持续更新' if change is not None else '对应时段行情暂缺，5分钟后重试'
                )
        return tuple(updated)

    def fetch_portfolio(self, path, year, month, markets, *, targets, cancel=None, force=False):
        """Every company is checked over three months; a missing one stays visible."""
        results, warnings = [], []
        for offset in range(3):
            index = year*12+month-1+offset
            y, m = index//12, index%12+1
            try:
                results.append(self.fetch(path, y, m, markets, cancel=cancel, force=force, targets=targets))
            except Exception:
                if offset == 0 or (cancel and cancel.is_set()):
                    raise
                warnings.append(f'{y}-{m:02d}查询失败，覆盖不完整')
        events = tuple(e for result in results for e in result.events if e.symbol in targets)
        missing = sorted(set(targets)-{e.symbol for e in events})
        if missing:
            warnings.append('未获取财报日程，需补查：'+'、'.join(missing))
        return replace(results[0], coverage_events=events, coverage_warning='；'.join(warnings))

    def fetch(self, path, year, month, markets, *, cancel=None, force=False, targets=None):
        key = (str(path), year, month, tuple(sorted(markets)))
        now = datetime.now(timezone.utc)
        persisted = self.get_cached(path, year, month, markets, max_age_seconds=None)
        if not force:
            cached = self.get_cached(path, year, month, markets)
            if cached:
                updated_events = self._with_session_changes(cached.events, path, targets, cancel, year=year, month=month)
                if updated_events != cached.events:
                    result = EarningsMonth(updated_events, cached.fetched_at, cached.warning)
                    self._update_cache(key, path, year, month, markets, result)
                    return result
                return cached
        if self.factory:
            context, category = self.factory(path)
        else:
            from longbridge.openapi import CalendarCategory, CalendarContext, Config
            key_value, secret, token = credentials(path)
            context = CalendarContext(Config.from_apikey(key_value, secret, token, enable_print_quote_packages=False))
            category = CalendarCategory.Report
        found, warnings = {}, set()
        first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        valid_markets = [m for m in markets if m in MARKET_ZONES]
        for m in markets:
            if m not in MARKET_ZONES:
                warnings.add(f'{m}市场暂不支持')

        if self.factory:
            for market in valid_markets:
                day = first
                while day <= last:
                    day_events, day_warnings = _fetch_day(context, category, market, day, first, last, cancel)
                    for e in day_events:
                        found[(e.symbol, e.day, e.time_label)] = e
                    warnings.update(day_warnings)
                    day += timedelta(days=1)
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
            tasks = [(market, day) for market in valid_markets for day in days]
            if tasks:
                with ThreadPoolExecutor(max_workers=min(8, len(tasks))) as executor:
                    futures = [
                        executor.submit(_fetch_day, context, category, m, d, first, last, cancel)
                        for m, d in tasks
                    ]
                    for future in as_completed(futures):
                        if cancel and cancel.is_set():
                            raise ValueError('财报查询已取消。')
                        day_events, day_warnings = future.result()
                        for e in day_events:
                            found[(e.symbol, e.day, e.time_label)] = e
                        warnings.update(day_warnings)

        incoming = reconciled_events(found.values(), year=year, month=month)
        finals = {(e.symbol, e.day, e.time_label): e for e in persisted.events
                  if e.session_final and e.session_change is not None} if persisted else {}
        merged = []
        for event in incoming:
            previous = finals.get((event.symbol, event.day, event.time_label))
            if previous:
                event = replace(event, session_change=previous.session_change,
                                session_checked_at=previous.session_checked_at, session_final=True,
                                session_status=previous.session_status,
                                confirmed_published=event.confirmed_published or previous.confirmed_published,
                                verification_source=event.verification_source or previous.verification_source)
            merged.append(event)
        incoming_symbols = {event.symbol for event in incoming}
        merged.extend(event for event in finals.values()
                      if event.confirmed_published and event.symbol not in incoming_symbols
                      and event.day.year == year and event.day.month == month)
        events = self._with_session_changes(merged, path, targets, cancel, year=year, month=month)
        result = EarningsMonth(events, now, '；'.join(sorted(warnings)))
        self._update_cache(key, path, year, month, markets, result)
        return result
