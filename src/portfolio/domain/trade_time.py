"""Execution clocks shared by earnings engines; see docs/TIMEZONES.md.

Business dates remain the Excel ledger dates, never inferred from UTC dates.
Longbridge/BBAE shifted book clocks are UTC wall clocks, not Shanghai or NY.
"""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from portfolio.domain.live_quotes import NY, UTC

BOOK_CLOCK_ZONES = {'LONGBRIDGE': 'UTC', 'BBAE': 'UTC', 'SCHWAB': 'America/New_York',
                    'FIRSTRADE': 'America/New_York'}


def assumes_open(event):
    stamp = datetime.fromisoformat(event.time)
    return (not event.metadata.get('executed_at_utc')
            and event.metadata.get('broker_code') == 'FIRSTRADE'
            and stamp.tzinfo is None and stamp.time() == time(0))


def event_time_utc(event):
    explicit = event.metadata.get('executed_at_utc')
    if explicit:
        stamp = datetime.fromisoformat(explicit.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('成交 UTC 时间缺少时区')
        return stamp.astimezone(UTC)
    stamp = datetime.fromisoformat(event.time)
    if stamp.tzinfo is not None:
        return stamp.astimezone(UTC)
    if assumes_open(event):
        return datetime.combine(date.fromisoformat(event.day), time(9, 30), NY).astimezone(UTC)
    if stamp.time() == time(0):
        raise ValueError('流水只有日期，缺少成交时间')
    # book_timezone defines the ledger cutoff, NOT the already shifted clock.
    # A documented per-record override can identify legacy/manual NY clocks.
    zone = event.metadata.get('trade_clock_timezone') or BOOK_CLOCK_ZONES.get(event.metadata.get('broker_code'))
    if not zone:
        raise ValueError('未确认交易时间时区')
    return stamp.replace(tzinfo=ZoneInfo(zone)).astimezone(UTC)


def event_order_time(event):
    """Order inside a ledger day without changing raw display timestamps.

    Date-only transfers keep ledger sequence for daily replay but still cannot
    supply an exact intraday time. They are not silently labeled NY or UTC.
    """
    try:
        return event_time_utc(event).strftime('%Y-%m-%d %H:%M:%S.%f')
    except (ValueError, TypeError, KeyError):
        return event.time
