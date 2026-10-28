"""Dedicated Longbridge quote thread. Bounded shared cache and independent quote persistence."""
import ast
import json
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from portfolio.domain.live_quotes import NY, SESSION_LABELS, UTC, LiveQuote, us_session

# SDK getters lazily initialize native conversion types. Serialize conversion
# across snapshot/push threads (and replaced feeds); waiting here releases GIL.
_SDK_VALUES_LOCK = threading.RLock()

KEY_NAMES = ('LONGBRIDGE_APP_KEY', 'LONGBRIDGE_APP_SECRET', 'LONGBRIDGE_ACCESS_TOKEN')


def credentials(path):
    path = Path(path).expanduser()
    if not path.is_file():
        raise ValueError('请选择本机长桥凭证文件。')
    try:
        if path.suffix.lower() == '.json':
            raw = json.loads(path.read_text(encoding='utf-8'))
            values = raw.get('longbridge', raw) if isinstance(raw, dict) else {}
        else:
            # Read constant values, never execute credential-file Python code.
            values = {}
            for node in ast.parse(path.read_text(encoding='utf-8-sig')).body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            values[target.id] = node.value.value
        key = values.get('LONGBRIDGE_APP_KEY') or values.get('app_key') or values.get('key')
        secret = values.get('LONGBRIDGE_APP_SECRET') or values.get('app_secret') or values.get('secret')
        token = values.get('LONGBRIDGE_ACCESS_TOKEN') or values.get('access_token') or values.get('token')
        result = (key, secret, token)
        if not all(isinstance(value, str) and value.strip() for value in result):
            raise ValueError
        return tuple(value.strip() for value in result)
    except (SyntaxError, ValueError, TypeError, UnicodeError):
        raise ValueError('长桥配置须包含 App Key、App Secret、Access Token 三个常量字段。') from None


def create_context(path):
    from longbridge.openapi import Config, QuoteContext
    key, secret, token = credentials(path)
    return QuoteContext(Config.from_apikey(key, secret, token, enable_overnight=True,
                                          enable_print_quote_packages=False))


class QuoteFeed:
    def __init__(self, path, *, factory=create_context, clock=None, store=None):
        self.path, self.factory = path, factory
        self.clock = clock or (lambda: datetime.now(UTC))
        self.store, self.cache_error = store, False
        self.days, self.halves, self.covered = set(), set(), None
        self.lock = threading.RLock()
        self.wake, self.stopped = threading.Event(), threading.Event()
        self.symbols, self.quotes = set(), {}
        self.status, self.session = '等待持仓', None
        self.thread = None
        self.close_attempts = {}
        self.close_error = False
        if store:
            try:
                self.quotes, self.days, self.halves, self.covered = store.load(self.clock())
                self.session = us_session(self.clock(), self.days, self.halves) if self.covered else None
            except Exception:
                self.cache_error = True

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name='money3-longbridge-quotes')
        self.thread.start()

    def set_symbols(self, symbols):
        with self.lock:
            changed = self.symbols != set(symbols)
            self.symbols = set(symbols)
        if changed:
            self.wake.set()

    def stop(self):
        self.stopped.set()
        self.wake.set()

    def snapshot(self):
        with self.lock:
            session = us_session(self.clock(), self.days, self.halves) if self.covered else self.session
            return self.status, session, dict(self.quotes)

    def _state(self, status, session=None):
        with self.lock:
            self.status, self.session = status, session

    def _store(self, symbol, price, stamp, session):
        # SDK naive datetimes represent machine-local time, not UTC.
        stamp = stamp.astimezone(UTC)
        price = Decimal(str(price))
        if price.is_finite() and price > 0 and stamp <= self.clock() + timedelta(seconds=5):
            with self.lock:
                if symbol not in self.symbols:
                    return
                key = (symbol, session)
                old = self.quotes.get(key)
                if old is None or stamp >= old.timestamp:
                    self.quotes[key] = LiveQuote(symbol, price, stamp, session)
                    if len(self.quotes) > 2000:
                        self.quotes.pop(min(self.quotes, key=lambda k: self.quotes[k].timestamp))

    def _on_quote(self, symbol, event):
        try:
            with _SDK_VALUES_LOCK:
                session = str(event.trade_session).rsplit('.', 1)[-1]
                self._store(symbol, event.last_done, event.timestamp, session)
        except (AttributeError, ValueError, TypeError, ArithmeticError):
            # Do not print SDK payloads or auth data from native callbacks.
            return

    def _seed(self, context, symbols):
        quotes = context.quote(sorted(symbols))
        with _SDK_VALUES_LOCK:
            for quote in quotes:
                self._store(quote.symbol, quote.last_done, quote.timestamp, 'Intraday')
                for name, attr in (('Pre', 'pre_market_quote'), ('Post', 'post_market_quote'), ('Overnight', 'overnight_quote')):
                    part = getattr(quote, attr, None)
                    if part is not None:
                        self._store(quote.symbol, part.last_done, part.timestamp, name)

    def _flush(self):
        if self.store:
            try:
                with self.lock:
                    quotes = tuple(self.quotes.values())
                self.store.save(quotes, self.clock())
                self.cache_error = False
            except Exception:
                self.cache_error = True

    def _wait(self, seconds):
        self.wake.wait(seconds)
        self.wake.clear()

    def _sync_closes(self, context, symbols, days, halves, now, *, closed=False):
        """Once per symbol/day; no polling for prices that have already settled."""
        from datetime import time

        from longbridge.openapi import AdjustType, Period, TradeSessions
        if self.store is None or not hasattr(context, 'history_candlesticks_by_date'):
            return
        completed = [d for d in days if datetime.combine(d, time(13 if d in halves else 16), NY)+timedelta(minutes=15) <= now]
        if not completed:
            return
        target = max(completed)
        recent = sorted(completed)[-5:]
        failed = False
        all_symbols = set(symbols)
        if self.store and self.store.source:
            from portfolio.services.history_facts import readonly
            from contextlib import closing
            try:
                with closing(readonly(self.store.source)) as c:
                    all_symbols.update(r[0] + '.' + r[1] for r in c.execute("SELECT symbol, market FROM instruments WHERE market='US'"))
            except Exception:
                pass
        for symbol in sorted(all_symbols):
            if self.stopped.is_set():
                return
            pending = self.store.pending_close_days(symbol, target, required_days=recent)
            if not pending:
                continue
            key = symbol, tuple(sorted(pending))
            attempted = self.close_attempts.get(key)
            if attempted and ((closed and attempted.astimezone(NY).date() == now.astimezone(NY).date()) or now-attempted < timedelta(minutes=30)):
                failed = True
                continue
            self.close_attempts[key] = now
            try:
                with _SDK_VALUES_LOCK:
                    candles = context.history_candlesticks_by_date(symbol, Period.Day, AdjustType.NoAdjust, min(pending), target+timedelta(days=1), TradeSessions.Intraday)
                    found = set()
                    for candle in candles:
                        stamp = candle.timestamp
                        # SDK naive datetimes represent system-local time, not UTC.
                        day = stamp.astimezone(NY).date()
                        price = candle.close
                        if day in pending and price.is_finite() and price > 0:
                            self.store.save_official_close(symbol, day, price, stamp, now)
                            found.add(day)
                    if found != pending:
                        for m_day in (pending - found):
                            with self.store.connect() as c:
                                snap = c.execute(
                                    "SELECT price, quoted_at FROM daily_quote_snapshots WHERE symbol=? AND day=? AND session='Intraday' ORDER BY quoted_at DESC LIMIT 1",
                                    (symbol, str(m_day))
                                ).fetchone()
                            if snap:
                                try:
                                    s_price = Decimal(snap[0])
                                    s_time = datetime.fromisoformat(snap[1])
                                    if s_price > 0:
                                        self.store.save_official_close(symbol, m_day, s_price, s_time, now)
                                        found.add(m_day)
                                except Exception:
                                    pass
                    if found != pending:
                        failed = True
            except Exception:
                failed = True
            # Keep well below the provider's request limit; interruptible on exit.
            self.stopped.wait(.15)
        self.close_error = failed

    def _run(self):
        from longbridge.openapi import Market, SubType
        context, active, last_session = None, set(), None
        days, halves, covered = self.days, self.halves, self.covered
        retries = 0
        try:
            while not self.stopped.is_set():
                try:
                    with self.lock:
                        symbols = set(self.symbols)
                        # Keep quotes for other people across filter changes.
                        self.quotes = {k: v for k, v in self.quotes.items() if v.timestamp >= self.clock()-timedelta(days=14)}
                    if not symbols:
                        if context and active:
                            context.unsubscribe(sorted(active), [SubType.Quote])
                        active = set()
                        self._state('等待当前美股持仓，历史日期不启用实时估值')
                        self._wait(30)
                        continue
                    if context is None:
                        self._state('正在连接长桥行情…', us_session(self.clock(), days, halves) if covered else None)
                        context = self.factory(self.path)
                        context.set_on_quote(self._on_quote)
                    now = self.clock()
                    day = now.astimezone(NY).date()
                    if covered is None or not (covered[0] <= day and day + timedelta(days=1) <= covered[1]):
                        begin, end = day - timedelta(days=10), day + timedelta(days=12)
                        calendar = context.trading_days(Market.US, begin, end)
                        halves = set(calendar.half_trading_days)
                        days, covered = set(calendar.trading_days) | halves, (begin, end)
                        self.days, self.halves, self.covered = days, halves, covered
                        if self.store:
                            try:
                                self.store.save_calendar(days, halves, covered)
                            except Exception:
                                self.cache_error = True
                    session = us_session(now, days, halves)
                    self._sync_closes(context, symbols, days, halves, now, closed=session is None)
                    if session is None:
                        if active:
                            context.unsubscribe(sorted(active), [SubType.Quote])
                        active, last_session = set(), None
                        self._state('美股休市 · 已暂停行情订阅和查询'+(' · 正式收盘补取未完成' if self.close_error else ''))
                        self._wait(30)
                        continue
                    removed, added = active - symbols, symbols - active
                    if removed:
                        context.unsubscribe(sorted(removed), [SubType.Quote])
                    # One snapshot at subscribe/session transition; all subsequent
                    # updates come from push, not repeated 10-second HTTP requests.
                    # Convert the initial SDK Decimal values before subscribing.
                    # In the native SDK, concurrent first access from snapshot and
                    # push threads can deadlock its lazy Decimal initializer/GIL.
                    if added or last_session != (session.name, session.start):
                        self._seed(context, symbols if last_session != (session.name, session.start) else added)
                    if added:
                        context.subscribe(sorted(added), [SubType.Quote])
                    active = symbols
                    last_session = session.name, session.start
                    self._flush()
                    self._state(f'长桥{SESSION_LABELS[session.name]} · 已订阅{len(active)}只 · 10秒刷新及保存'+(' · 缓存写入失败' if self.cache_error else '')+(' · 正式收盘补取未完成，将重试' if self.close_error else ''), session)
                    retries = 0
                    self._wait(10)
                except Exception:
                    # Never surface SDK exception strings: they may contain keys.
                    context, active, last_session, covered = None, set(), None, None
                    retries += 1
                    delay = min(300, 30 * 2 ** min(retries - 1, 4))
                    self._state(f'长桥行情连接失败：请检查网络、Token及OpenAPI权限；{delay}秒后重试', us_session(self.clock(), days, halves) if covered else None)
                    self._wait(delay)
        finally:
            self._flush()
            if context and active:
                try:
                    context.unsubscribe(sorted(active), [SubType.Quote])
                except Exception:
                    pass
