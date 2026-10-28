"""Live quote records and US session selection. No Qt or network calls."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
UTC = timezone.utc
SESSION_LABELS = {'Pre': '盘前', 'Intraday': '盘中', 'Post': '盘后', 'Overnight': '夜盘'}


@dataclass(frozen=True)
class Session:
    name: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class LiveQuote:
    symbol: str
    price: Decimal
    timestamp: datetime
    session: str
    source: str = 'stream'


def us_session(now, trading_days, half_days=()):
    """Overnight belongs to the NEXT US trading day (Sunday evening included)."""
    local = now.astimezone(NY)
    day = local.date()
    def at(d, hour, minute=0):
        return datetime.combine(d, time(hour, minute), NY)
    if local.hour >= 20:
        target = day + timedelta(days=1)
        return Session('Overnight', at(day, 20), at(target, 4)) if target in trading_days else None
    if day not in trading_days:
        return None
    if local.hour < 4:
        return Session('Overnight', at(day - timedelta(days=1), 20), at(day, 4))
    if local < at(day, 9, 30):
        return Session('Pre', at(day, 4), at(day, 9, 30))
    close = 13 if day in half_days else 16
    post_close = 17 if day in half_days else 20
    if local < at(day, close):
        return Session('Intraday', at(day, 9, 30), at(day, close))
    if local < at(day, post_close):
        return Session('Post', at(day, close), at(day, post_close))
    return None


def quote_usable(quote, session, now):
    return (quote is not None and session is not None and quote.session == session.name
            and session.start <= now < session.end
            and session.start <= quote.timestamp <= now + timedelta(seconds=5)
            and quote.timestamp < session.end and quote.price.is_finite() and quote.price > 0)


def market_trade_day(now, trading_days, half_days=()):
    """US session date, including the next trading day's overnight session."""
    session=us_session(now,trading_days,half_days)
    if session is not None:
        return session.end.astimezone(NY).date() if session.name=='Overnight' else session.start.astimezone(NY).date()
    today=now.astimezone(NY).date()
    return max((d for d in trading_days if d<=today),default=today)
