"""Read-only coverage of daily US closes required by the full broker ledger."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import sqlite3

from portfolio.db.connection import readonly_connection
from portfolio.domain.live_quotes import NY, UTC
from portfolio.engine.holdings import MIN_OPEN_QUANTITY


@dataclass(frozen=True)
class Calendar:
    days: frozenset[date]
    halves: frozenset[date]


@dataclass(frozen=True)
class Coverage:
    start: date
    end: date
    required: int
    missing: tuple[tuple[str, date], ...]
    legacy: tuple[tuple[str, date], ...]

    @property
    def message(self):
        return (f"{self.start}—{self.end}：需要 {self.required} 条股票日收盘价，"
                f"缺失 {len(self.missing)} 条，来源待核验 {len(self.legacy)} 条。")

    def details(self):
        lines = [self.message, "范围：全部人员、全部渠道的美股历史持仓及当日成交；仅已正式收盘的交易日。",
                 "盘前/盘后报价、月末价和实时估值不算正式日收盘价。"]
        if self.missing:
            lines += ["", "缺失日期 / 股票："] + [f"{day}  {symbol}" for symbol, day in self.missing]
        if self.legacy:
            lines += ["", "已落库但来源状态为旧数据、尚不能确认正式收盘口径："]
            lines += [f"{day}  {symbol}" for symbol, day in self.legacy]
        return "\n".join(lines)


def fetch_calendar(credentials_path, start, end):
    from longbridge.openapi import Market
    from portfolio.services.live_quotes import create_context
    context = create_context(credentials_path)
    days, halves = set(), set()
    # Keep requests below the provider's calendar-range limit.
    cursor = start
    while cursor <= end:
        stop = min(end, cursor + timedelta(days=19))
        result = context.trading_days(Market.US, cursor, stop)
        halves.update(result.half_trading_days)
        days.update(result.trading_days)
        cursor = stop + timedelta(days=1)
        if cursor <= end:
            import time as clock
            clock.sleep(.15)
    if (end - start).days >= 10 and not days and not halves:
        raise ValueError('交易所日历未返回足够信息，不能认定期间没有交易日。')
    return Calendar(frozenset(days | halves), frozenset(halves))


def cached_calendar(cache_path, start, end):
    """Load exchange calendar from local cache."""
    if not cache_path or not Path(cache_path).is_file():
        raise ValueError("未取得交易所日历，无法确认历史收盘数据是否完整。")
    with sqlite3.connect(Path(cache_path).as_uri() + '?mode=ro', uri=True) as c:
        rows = c.execute('SELECT day,half_day FROM us_calendar WHERE day BETWEEN ? AND ?',
                         (start.isoformat(), end.isoformat())).fetchall()
        if not rows:
            raise ValueError("交易所日历缓存未覆盖检查期间；请检查长桥凭证、权限和网络后重新检查。")
    return Calendar(frozenset(date.fromisoformat(d) for d, _ in rows),
                    frozenset(date.fromisoformat(d) for d, half in rows if half))


def check_coverage(database, cache_path, start, end, calendar, *, now=None):
    now = now or datetime.now(UTC)
    completed = sorted(day for day in calendar.days if start <= day <= end and
                       datetime.combine(day, time(13 if day in calendar.halves else 16), NY)
                       + timedelta(minutes=15) <= now)
    if not completed:
        return Coverage(start, end, 0, (), ())
    # Match the displayed/quoted holdings policy. Residuals below 0.5 shares are
    # treated as closed; an actual trade that day still requires that day's close.
    events, balances, expected = [], defaultdict(Fraction), set()
    with readonly_connection(database) as c:
        symbols = {r['id']: r['symbol'] + '.US' for r in c.execute("SELECT id,symbol FROM instruments WHERE market='US'")}
        for r in c.execute("SELECT * FROM broker_trades WHERE record_state='ACTIVE' AND accounting_role='ACCOUNT_LEDGER' AND business_date<=?", (end.isoformat(),)):
            if r['instrument_id'] not in symbols:
                continue
            events.append((r['business_date'], 1, r['book_time'], r['id'], dict(r)))
        for r in c.execute("SELECT * FROM corporate_actions WHERE record_state='ACTIVE' AND effective_date<=?", (end.isoformat(),)):
            events.append((r['effective_date'], 0, '', r['id'], dict(r)))
        accounts = {r['id']: r['broker_code'] for r in c.execute('SELECT * FROM broker_accounts')}
        events.sort(key=lambda r: r[:4])
        cursor = 0
        for day in completed:
            touched = set()
            # Take opening holdings before this day's transactions, after all
            # older events have been applied. Events older than start still matter.
            while cursor < len(events) and events[cursor][0] < day.isoformat():
                _, kind, _, _, r = events[cursor]
                _apply(r, kind, balances, accounts)
                cursor += 1
            touched.update(s for (a, s), q in balances.items() if abs(q) >= Fraction(MIN_OPEN_QUANTITY))
            while cursor < len(events) and events[cursor][0] == day.isoformat():
                _, kind, _, _, r = events[cursor]
                if kind == 1:
                    touched.add(r['instrument_id'])
                _apply(r, kind, balances, accounts)
                cursor += 1
            expected.update((symbols[s], day) for s in touched if s in symbols)
        saved, verified = set(), set()
        for r in c.execute("SELECT symbol,trade_date,price_status,price_e8 FROM market_prices WHERE price_type='DAILY_CLOSE' AND trade_date BETWEEN ? AND ?", (start.isoformat(), end.isoformat())):
            if r['price_e8'] <= 0 or not r['trade_date']:
                continue
            key = (r['symbol'] if r['symbol'].endswith('.US') else r['symbol'] + '.US', date.fromisoformat(r['trade_date']))
            if r['price_status'] in ('RAW_CLOSE', 'OFFICIAL_CLOSE'):
                saved.add(key); verified.add(key)
            elif r['price_status'] == 'LEGACY_UNKNOWN':
                saved.add(key)
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='history_prices'").fetchone():
            for r in c.execute("SELECT i.symbol, i.market, h.trade_date, h.price, h.confirmation FROM history_prices h JOIN instruments i ON i.id=h.instrument_id WHERE h.price_type='DAILY_CLOSE' AND h.adjustment='RAW' AND h.trade_date BETWEEN ? AND ?", (start.isoformat(), end.isoformat())):
                try:
                    val = Decimal(r['price'])
                    if not val.is_finite() or val <= 0:
                        continue
                    key = (r['symbol'] + '.' + r['market'], date.fromisoformat(r['trade_date']))
                    saved.add(key)
                    if r['confirmation'] == 'CONFIRMED':
                        verified.add(key)
                except (ValueError, ArithmeticError):
                    continue
    ordered = lambda keys: tuple(sorted(keys, key=lambda r: (r[1], r[0])))
    return Coverage(start, end, len(expected), ordered(expected - saved), ordered((expected & saved) - verified))


def _apply(r, kind, balances, accounts):
    if kind == 0:
        ratio = Fraction(r['ratio_numerator'], r['ratio_denominator'])
        for (account, security), value in list(balances.items()):
            if security == r['instrument_id'] and r['broker_scope'] in ('ALL', accounts[account]):
                balances[account, security] = value * ratio
    else:
        sign = 1 if r['side'] == 'BUY' else -1
        balances[r['account_id'], r['instrument_id']] += sign * Fraction(r['quantity_e8'], 100000000)
