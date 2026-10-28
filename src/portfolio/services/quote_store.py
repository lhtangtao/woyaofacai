"""Latest provider quotes and exchange calendar, independent of source ledgers."""
import json
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from portfolio.domain.live_quotes import NY, UTC, LiveQuote


class QuoteStore:
    def __init__(self, path, *, source=None):
        self.path = Path(path).expanduser().resolve()
        self.source = Path(source).resolve() if source else None

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from portfolio.services.derived_store import ClosingConnection
        c = sqlite3.connect(self.path, timeout=30,factory=ClosingConnection)
        c.execute('CREATE TABLE IF NOT EXISTS latest_quotes (symbol TEXT NOT NULL, session TEXT NOT NULL, price TEXT NOT NULL, quoted_at TEXT NOT NULL, saved_at TEXT NOT NULL, provider TEXT NOT NULL, PRIMARY KEY(symbol,session))')
        c.execute('CREATE TABLE IF NOT EXISTS us_calendar (day TEXT PRIMARY KEY, half_day INTEGER NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS coverage (id INTEGER PRIMARY KEY CHECK(id=1), first_day TEXT NOT NULL, last_day TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS daily_quote_snapshots (symbol TEXT NOT NULL, day TEXT NOT NULL, session TEXT NOT NULL, price TEXT NOT NULL, quoted_at TEXT NOT NULL, PRIMARY KEY(symbol,day,session))')
        c.execute('CREATE TABLE IF NOT EXISTS official_daily_closes (symbol TEXT NOT NULL, day TEXT NOT NULL, price TEXT NOT NULL, candle_time TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(symbol,day))')
        c.execute('CREATE TABLE IF NOT EXISTS earnings_calendar_cache (cache_key TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, warning TEXT NOT NULL, payload_json TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS minute_pnl_snapshots (scope_key TEXT NOT NULL, day TEXT NOT NULL, minute TEXT NOT NULL, observed_at TEXT NOT NULL, session TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(scope_key,day,minute))')
        c.execute('CREATE TABLE IF NOT EXISTS intraday_candles (symbol TEXT NOT NULL, day TEXT NOT NULL, stamp TEXT NOT NULL, session TEXT NOT NULL, close TEXT NOT NULL, PRIMARY KEY(symbol,day,stamp))')
        c.execute('CREATE TABLE IF NOT EXISTS intraday_fetches (symbol TEXT NOT NULL, day TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(symbol,day))')
        c.execute('CREATE TABLE IF NOT EXISTS historical_pnl_points (scope_key TEXT NOT NULL, day TEXT NOT NULL, stamp TEXT NOT NULL, session TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(scope_key,day,stamp))')
        c.execute('CREATE TABLE IF NOT EXISTS historical_pnl_versions (scope_key TEXT NOT NULL, day TEXT NOT NULL, source_version TEXT NOT NULL, PRIMARY KEY(scope_key,day))')
        c.execute('CREATE TABLE IF NOT EXISTS historical_pnl_warnings (scope_key TEXT NOT NULL, day TEXT NOT NULL, warning TEXT NOT NULL, PRIMARY KEY(scope_key,day))')
        c.execute('CREATE TABLE IF NOT EXISTS channel_cash_snapshots (channel TEXT PRIMARY KEY, amount TEXT NOT NULL, currency TEXT NOT NULL, updated_at TEXT NOT NULL, note TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS quote_close_revisions(day TEXT PRIMARY KEY,revision INTEGER NOT NULL)')
        for table in ('official_daily_closes','daily_quote_snapshots'):
            for operation in ('INSERT','UPDATE','DELETE'):
                ref='OLD' if operation=='DELETE' else 'NEW'
                c.execute(f"CREATE TRIGGER IF NOT EXISTS close_revision_{table}_{operation.lower()} AFTER {operation} ON {table} BEGIN INSERT INTO quote_close_revisions VALUES ({ref}.day,1) ON CONFLICT(day) DO UPDATE SET revision=revision+1; END")
        c.execute('CREATE TABLE IF NOT EXISTS calendar_ranges(first_day TEXT NOT NULL,last_day TEXT NOT NULL,PRIMARY KEY(first_day,last_day))')
        c.execute('CREATE TABLE IF NOT EXISTS quote_day_revisions(day TEXT PRIMARY KEY,revision INTEGER NOT NULL)')
        for operation in ('INSERT','UPDATE','DELETE'):
            ref='OLD' if operation=='DELETE' else 'NEW'
            c.execute(f"CREATE TRIGGER IF NOT EXISTS quote_revision_{operation.lower()} AFTER {operation} ON intraday_candles BEGIN INSERT INTO quote_day_revisions VALUES ({ref}.day,1) ON CONFLICT(day) DO UPDATE SET revision=revision+1; END")
        return c

    def read_connection(self):
        from portfolio.services.derived_store import ClosingConnection
        c=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5,factory=ClosingConnection)
        c.execute('PRAGMA query_only=ON')
        return c

    def prefix_identity(self, day, until, instrument_ids=None):
        from portfolio.services.derived_store import digest
        from portfolio.services.history_facts import readonly
        from contextlib import closing
        with closing(readonly(self.source)) as c:
            instruments=[tuple(r) for r in c.execute("SELECT id,symbol||'.US' FROM instruments WHERE market='US' ORDER BY id")]
        values=[]
        for identifier,symbol in instruments:
            if instrument_ids is not None and identifier not in instrument_ids:continue
            rows,_=self.intraday_candles(symbol,day)
            values.append((identifier,[(stamp,phase,price) for stamp,phase,price in rows if stamp+timedelta(minutes=5)<=until]))
        return digest(values)

    def day_revision(self, day):
        from contextlib import closing
        with closing(self.read_connection()) as c:
            row=c.execute('SELECT revision FROM quote_day_revisions WHERE day=?',(day,)).fetchone()
            from portfolio.services.derived_store import digest
            calendar=digest(c.execute('SELECT day,half_day FROM us_calendar WHERE day<=? ORDER BY day',(day,)).fetchall())
            closes=digest(c.execute('SELECT day,revision FROM quote_close_revisions WHERE day<? ORDER BY day',(day,)).fetchall())
        facts=''
        if self.source:
            from portfolio.services.history_facts import readonly
            from portfolio.services.derived_store import digest
            with closing(readonly(self.source)) as c:
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='history_fact_revisions'").fetchone():
                    revision=c.execute('SELECT revision FROM history_fact_revisions WHERE day=?',(day,)).fetchone()
                    facts=revision[0] if revision else 0
        return (row[0] if row else 0,facts,calendar,closes)

    def intraday_candles(self, symbol, day):
        if not self.path.is_file():
            from portfolio.services.history_facts import HistoryFacts
            return HistoryFacts(self.source).bars(symbol,day) if self.source else ([],None)
        with self.read_connection() as c:
            rows = c.execute('SELECT stamp,session,close FROM intraday_candles WHERE symbol=? AND day=? ORDER BY stamp', (symbol, day)).fetchall()
            fetched = c.execute('SELECT fetched_at FROM intraday_fetches WHERE symbol=? AND day=?', (symbol, day)).fetchone()
        result = {stamp: (datetime.fromisoformat(stamp), phase, Decimal(value)) for stamp, phase, value in rows}
        fetched_at = datetime.fromisoformat(fetched[0]) if fetched else None
        if self.source:
            from portfolio.services.history_facts import HistoryFacts
            facts, confirmed_at = HistoryFacts(self.source).bars(symbol, day)
            for item in facts: result[item[0].isoformat()] = item
            fetched_at = max((v for v in (fetched_at, confirmed_at) if v), default=None)
        return sorted(result.values()), fetched_at

    def save_intraday_candles(self, symbol, day, rows, now):
        rows=list(rows)
        archived=False
        if self.source:
            from portfolio.services.history_facts import append_completed
            archived=append_completed(self.source,symbol,day,rows)
        with self.connect() as c:
            if not archived:
                c.executemany('INSERT OR REPLACE INTO intraday_candles VALUES (?,?,?,?,?)',
                              [(symbol,day,stamp.astimezone(UTC).isoformat(),phase,str(value)) for stamp,phase,value in rows])
            c.execute('INSERT OR REPLACE INTO intraday_fetches VALUES (?,?,?)',(symbol,day,now.astimezone(UTC).isoformat()))

    def save_historical_pnl(self, key, day, rows, source_version, warning='', *, input_revision=None,checkpoint=None,resume_after=None):
        with self.connect() as c:
            c.execute('DELETE FROM historical_pnl_points WHERE scope_key=? AND day=?', (key, day))
            c.executemany('INSERT INTO historical_pnl_points VALUES (?,?,?,?,?)',
                          [(key, day, stamp.astimezone(UTC).isoformat(), phase, json.dumps(values, ensure_ascii=False)) for stamp, phase, values in rows])
            c.execute('INSERT OR REPLACE INTO historical_pnl_versions VALUES (?,?,?)', (key, day, source_version))
            c.execute('INSERT OR REPLACE INTO historical_pnl_warnings VALUES (?,?,?)', (key, day, warning))

    def load_historical_pnl_warning(self, key, day):
        with self.connect() as c:
            row = c.execute('SELECT warning FROM historical_pnl_warnings WHERE scope_key=? AND day=?', (key, day)).fetchone()
        return row[0] if row else ''

    def load_historical_pnl(self, key, day, source_version=None):
        with self.connect() as c:
            version = c.execute('SELECT source_version FROM historical_pnl_versions WHERE scope_key=? AND day=?', (key, day)).fetchone()
            if source_version is not None and (not version or version[0] != source_version):
                return ()
            return tuple((datetime.fromisoformat(stamp), phase, json.loads(payload)) for stamp, phase, payload in c.execute(
                'SELECT stamp,session,payload_json FROM historical_pnl_points WHERE scope_key=? AND day=? ORDER BY stamp', (key, day)))

    def save_minute_pnl(self, scope_key, day, now, session, rows):
        """Observed portfolio P&L, decimal strings; never a source-ledger write."""
        stamp = now.astimezone(UTC)
        minute = stamp.replace(second=0, microsecond=0).isoformat()
        with self.connect() as c:
            c.execute('INSERT INTO minute_pnl_snapshots VALUES (?,?,?,?,?,?) ON CONFLICT(scope_key,day,minute) DO UPDATE SET observed_at=excluded.observed_at,session=excluded.session,payload_json=excluded.payload_json WHERE excluded.observed_at>=minute_pnl_snapshots.observed_at',
                      (scope_key, day, minute, stamp.isoformat(), session, json.dumps(rows, ensure_ascii=False)))

    def load_minute_pnl(self, scope_key, day):
        if not self.path.exists():
            return ()
        with self.connect() as c:
            return tuple((datetime.fromisoformat(minute), phase, json.loads(payload)) for minute, phase, payload in c.execute(
                'SELECT minute,session,payload_json FROM minute_pnl_snapshots WHERE scope_key=? AND day=? ORDER BY minute', (scope_key, day)))

    def load(self, now):
        if not self.path.exists():
            return {}, set(), set(), None
        quotes, days, halves = {}, set(), set()
        with self.read_connection() as c:
            for symbol, phase, value, stamp in c.execute('SELECT symbol,session,price,quoted_at FROM latest_quotes'):
                try:
                    price, quoted = Decimal(value), datetime.fromisoformat(stamp).astimezone(UTC)
                    if price.is_finite() and price > 0 and now-timedelta(days=14) <= quoted <= now+timedelta(seconds=5):
                        quotes[(symbol, phase)] = LiveQuote(symbol, price, quoted, phase, 'cache')
                except (ValueError, ArithmeticError):
                    continue
            for day, half in c.execute('SELECT day,half_day FROM us_calendar'):
                days.add(date.fromisoformat(day))
                if half:
                    halves.add(date.fromisoformat(day))
            row = c.execute('SELECT first_day,last_day FROM coverage WHERE id=1').fetchone()
            covered = tuple(date.fromisoformat(v) for v in row) if row else None
        return quotes, days, halves, covered

    def save(self, quotes, now):
        rows = [(q.symbol, q.session, str(q.price), q.timestamp.astimezone(UTC).isoformat(), now.astimezone(UTC).isoformat(), 'Longbridge') for q in quotes if q.price.is_finite() and q.price > 0 and q.timestamp <= now+timedelta(seconds=5)]
        if not rows:
            return
        with self.connect() as c:
            self._archive(c, rows)
            c.executemany('INSERT INTO latest_quotes VALUES (?,?,?,?,?,?) ON CONFLICT(symbol,session) DO UPDATE SET price=excluded.price,quoted_at=excluded.quoted_at,saved_at=excluded.saved_at WHERE excluded.quoted_at>=latest_quotes.quoted_at', rows)
            c.execute('DELETE FROM latest_quotes WHERE quoted_at<?', ((now-timedelta(days=14)).astimezone(UTC).isoformat(),))
            c.execute('DELETE FROM latest_quotes WHERE rowid NOT IN (SELECT rowid FROM latest_quotes ORDER BY quoted_at DESC LIMIT 2000)')

    def save_calendar(self, days, halves, covered):
        with self.connect() as c:
            c.execute('DELETE FROM us_calendar WHERE day>=? AND day<=?', tuple(d.isoformat() for d in covered))
            c.executemany('INSERT OR REPLACE INTO us_calendar VALUES (?,?)', [(d.isoformat(), int(d in halves)) for d in days])
            c.execute('INSERT INTO coverage VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET first_day=excluded.first_day,last_day=excluded.last_day', tuple(d.isoformat() for d in covered))
            c.execute('INSERT OR IGNORE INTO calendar_ranges VALUES (?,?)',tuple(d.isoformat() for d in covered))

    @staticmethod
    def _archive(connection, rows):
        values = []
        for symbol, phase, price, stamp, *_ in rows:
            local = datetime.fromisoformat(stamp).astimezone(NY)
            day = local.date() + (timedelta(days=1) if phase == 'Overnight' and local.hour >= 20 else timedelta())
            values.append((symbol, day.isoformat(), phase, price, stamp))
        connection.executemany('INSERT INTO daily_quote_snapshots VALUES (?,?,?,?,?) ON CONFLICT(symbol,day,session) DO UPDATE SET price=excluded.price,quoted_at=excluded.quoted_at WHERE excluded.quoted_at>daily_quote_snapshots.quoted_at OR (excluded.quoted_at=daily_quote_snapshots.quoted_at AND excluded.price<>daily_quote_snapshots.price)', values)

    def historical_quotes(self, as_of, now=None):
        """Archive latest-cache migration; completed US days only, never official closes.

        Prefer regular-session snapshots over extended hours. A regular tick
        within two minutes of the exchange close can supply an estimated baseline.
        Earlier/extended ticks remain explicitly partial historical estimates.
        """
        from datetime import time
        now = now or datetime.now(UTC)
        if not self.path.exists():
            return ()
        with self.connect() as c:
            self._archive(c, c.execute('SELECT symbol,session,price,quoted_at FROM latest_quotes').fetchall())
            halves = {row[0] for row in c.execute('SELECT day FROM us_calendar WHERE half_day=1')}
            official = c.execute('SELECT symbol,day,price,candle_time FROM official_daily_closes WHERE day<=?', (as_of.isoformat(),)).fetchall()
            rows = c.execute('SELECT symbol,day,session,price,quoted_at FROM daily_quote_snapshots WHERE day<=? ORDER BY day,quoted_at', (as_of.isoformat(),)).fetchall()
        chosen = {}
        for symbol, day, phase, value, stamp in rows:
            try:
                price = Decimal(value)
                quoted = datetime.fromisoformat(stamp).astimezone(UTC)
                close = datetime.combine(date.fromisoformat(day), time(13 if day in halves else 16), NY)
                if not price.is_finite() or price <= 0 or quoted > now or now < close:
                    continue
                near_close = phase == 'Intraday' and abs((quoted-close).total_seconds()) <= 120
                rank = (phase == 'Intraday', near_close, quoted)
                key = symbol, day
                if key not in chosen or rank > chosen[key][0]:
                    chosen[key] = (rank, (symbol, day, price, stamp, 'CLOSE_ESTIMATE' if near_close else 'PARTIAL_ESTIMATE'))
            except (ValueError, ArithmeticError):
                continue
        for symbol, day, value, stamp in official:
            chosen[(symbol, day)] = (None, (symbol, day, Decimal(value), stamp, 'OFFICIAL_CLOSE'))
        return tuple(chosen[key][1] for key in sorted(chosen))

    def history_identity(self):
        return tuple((symbol, day, str(price), stamp, status) for symbol, day, price, stamp, status in self.historical_quotes(datetime.now(NY).date()))

    def save_channel_cash(self, channel: str, amount: Decimal, currency: str = 'USD', note: str = ''):
        now_str = datetime.now(UTC).isoformat()
        with self.connect() as c:
            c.execute(
                'INSERT INTO channel_cash_snapshots VALUES (?,?,?,?,?) ON CONFLICT(channel) DO UPDATE SET amount=excluded.amount,currency=excluded.currency,updated_at=excluded.updated_at,note=excluded.note',
                (channel.upper(), str(amount), currency.upper(), now_str, note),
            )

    def load_channel_cash(self) -> dict[str, Decimal]:
        if not self.path.exists():
            return {}
        try:
            with self.connect() as c:
                rows = c.execute('SELECT channel, amount FROM channel_cash_snapshots').fetchall()
                return {ch: Decimal(amt) for ch, amt in rows}
        except Exception:
            return {}

    def supplement(self, snapshot):
        from portfolio.domain.analytics import Issue, Price
        instruments = {i['symbol']+'.US': key for key, i in snapshot.instruments.items() if i.get('market') == 'US'}
        if self.source:
            from portfolio.services.history_facts import HistoryFacts
            for identifier, day, value, stamp in HistoryFacts(self.source).closes(snapshot.scope.as_of):
                snapshot.prices = [p for p in snapshot.prices if not (p.instrument_id == identifier and p.day == day and p.kind == 'DAILY_CLOSE')]
                snapshot.prices.append(Price(identifier, day, Decimal(value), 'DAILY_CLOSE', 'OFFICIAL_CLOSE', 0, stamp, 'RAW'))
        official = {(p.instrument_id, p.day) for p in snapshot.prices if p.kind == 'DAILY_CLOSE'}
        if self.path.exists():
            with self.connect() as c:
                snapshot.extras['trading_days'] = tuple(r[0] for r in c.execute('SELECT day FROM us_calendar ORDER BY day'))
                try:
                    c_rows = c.execute('SELECT channel, amount FROM channel_cash_snapshots').fetchall()
                    snapshot.extras['channel_cash'] = {r[0]: Decimal(r[1]) for r in c_rows}
                except Exception:
                    pass
        count = 0
        for symbol, day, price, stamp, status in self.historical_quotes(snapshot.scope.as_of):
            identifier = instruments.get(symbol)
            if identifier is not None and (identifier, day) not in official:
                snapshot.prices.append(Price(identifier, day, price, 'DAILY_CLOSE' if status == 'OFFICIAL_CLOSE' else 'ESTIMATE_CLOSE', status, 0, stamp, 'RAW'))
                count += status != 'OFFICIAL_CLOSE'
        if count:
            snapshot.issues.append(Issue('LIVE_HISTORY', 'WARN', '包含本机保存的历史行情估值，非正式收盘数据；正式收盘数据到齐后优先替换。'))

    def saved_close_days(self, symbol):
        with self.connect() as c:
            result={date.fromisoformat(r[0]) for r in c.execute('SELECT day FROM official_daily_closes WHERE symbol=?',(symbol,))}
        if self.source:
            from portfolio.services.history_facts import HistoryFacts,readonly
            from contextlib import closing
            with closing(readonly(self.source)) as c:
                identifier=c.execute("SELECT id FROM instruments WHERE symbol||'.'||market=?",(symbol,)).fetchone()
            if identifier:
                result.update(date.fromisoformat(day) for i,day,_,_ in HistoryFacts(self.source).closes(date.today()) if i==identifier[0])
        return result

    def pending_close_days(self, symbol, target, *, required_days=()):
        saved = self.saved_close_days(symbol)
        with self.connect() as c:
            observed = {date.fromisoformat(r[0]) for r in c.execute('SELECT DISTINCT day FROM daily_quote_snapshots WHERE symbol=? AND day<=?', (symbol, target.isoformat()))}
        # A recently added holding may have no local ticks on an earlier day.
        # Exchange-calendar dates must also be eligible for official backfill.
        required = {day for day in required_days if day <= target}
        return (observed | required | {target}) - saved

    def save_official_close(self, symbol, day, price, candle_time, now):
        """Only the daily regular-session history endpoint may call this path."""
        if not price.is_finite() or price<=0 or day>now.astimezone(NY).date():raise ValueError('无效日K收盘数据')
        if self.source:
            from portfolio.services.history_facts import append_completed
            if append_completed(self.source,symbol,day,[(candle_time,'Intraday',price)],kind='DAILY_CLOSE',confirmation='CONFIRMED'):return
        with self.connect() as c:
            c.execute('INSERT INTO official_daily_closes VALUES (?,?,?,?,?) ON CONFLICT(symbol,day) DO UPDATE SET price=excluded.price,candle_time=excluded.candle_time,fetched_at=excluded.fetched_at',
                      (symbol,day.isoformat(),str(price),candle_time.isoformat(),now.astimezone(UTC).isoformat()))

    def load_earnings(self, key, max_age_seconds=86400):
        if not self.path.exists():
            return None
        with self.connect() as c:
            row = c.execute('SELECT fetched_at, warning, payload_json FROM earnings_calendar_cache WHERE cache_key=?', (key,)).fetchone()
        if not row:
            return None
        fetched_at_str, warning, payload_json = row
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
            if max_age_seconds is not None and (datetime.now(UTC) - fetched_at.astimezone(UTC)).total_seconds() > max_age_seconds:
                return None
            data = json.loads(payload_json)
            from portfolio.services.earnings_service import EarningsEvent, EarningsMonth
            events = tuple(
                EarningsEvent(
                    symbol=item['symbol'],
                    name=item['name'],
                    day=date.fromisoformat(item['day']),
                    time_label=item['time_label'],
                    beijing=item['beijing'],
                    content=item.get('content', ''),
                    confirmed_published=bool(item.get('confirmed_published', False)),
                    verification_source=item.get('verification_source', ''),
                    session_change=float(item['session_change']) if item.get('session_change') is not None else None,
                    session_checked_at=datetime.fromisoformat(item['session_checked_at']) if item.get('session_checked_at') else None,
                    session_final=bool(item.get('session_final', False)),
                    session_status=item.get('session_status', ''),
                )
                for item in data
            )
            return EarningsMonth(events, fetched_at, warning)
        except Exception:
            return None

    def save_earnings(self, key, month_result):
        if not month_result or not month_result.fetched_at:
            return
        payload = [
            {
                'symbol': e.symbol,
                'name': e.name,
                'day': e.day.isoformat(),
                'time_label': e.time_label,
                'beijing': e.beijing,
                'content': e.content,
                'confirmed_published': e.confirmed_published,
                'verification_source': e.verification_source,
                'session_change': e.session_change,
                'session_checked_at': e.session_checked_at.isoformat() if e.session_checked_at else None,
                'session_final': e.session_final,
                'session_status': e.session_status,
            }
            for e in month_result.events
        ]
        payload_json = json.dumps(payload, ensure_ascii=False)
        fetched_at_str = month_result.fetched_at.astimezone(UTC).isoformat()
        with self.connect() as c:
            c.execute(
                'INSERT INTO earnings_calendar_cache VALUES (?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET fetched_at=excluded.fetched_at, warning=excluded.warning, payload_json=excluded.payload_json',
                (key, fetched_at_str, month_result.warning, payload_json),
            )
