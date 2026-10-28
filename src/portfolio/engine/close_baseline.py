"""A daily movement must use the preceding US trading day's close."""
from datetime import date, timedelta


def admissible_close(day,kind,status,basis,expected):
    return day==expected and basis in ('RAW','UNKNOWN') and (kind=='DAILY_CLOSE' or kind=='ESTIMATE_CLOSE' and status=='CLOSE_ESTIMATE')


def intraday_closes(prices, expected):
    """Same prior trading date and admissible estimates as the native card."""
    result={}
    for price in prices:
        if not admissible_close(price.day,price.kind,price.status,price.basis,expected):continue
        current=result.get(price.instrument_id)
        if current is None or (current.kind!='DAILY_CLOSE' and price.kind=='DAILY_CLOSE'):
            result[price.instrument_id]=price
    return result


def previous_trading_day(analytics, day, calendar=None):
    if calendar:
        known = [d.isoformat() if hasattr(d, 'isoformat') else str(d) for d in calendar]
        prior = [d for d in known if d < day]
        if prior:
            return max(prior)
    days = getattr(analytics, 'trading_days', ())
    known = [d for d in days if d < day]
    if day in days and known:
        return max(known)
    prior = date.fromisoformat(day) - timedelta(days=1)
    while prior.weekday() >= 5:
        prior -= timedelta(days=1)
    return prior.isoformat()


def trusted_close(metadata, expected):
    return metadata.get('valuation_date') == expected and (
        metadata.get('valuation_source') == 'DAILY_CLOSE' or
        metadata.get('valuation_source') == 'ESTIMATE_CLOSE' and metadata.get('valuation_status') == 'CLOSE_ESTIMATE')


def valid_opening(row, expected):
    # A newly opened position has no prior-day exposure and needs no close.
    if row.values[5] is not None and row.values[5] <= 0 and row.values[15] == 0:
        return True
    return row.metadata.get('opening_day', expected) == expected and row.metadata.get('opening_status') != 'PARTIAL_ESTIMATE'
