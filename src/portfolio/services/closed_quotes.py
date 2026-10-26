"""Shared quote-only projection for closed positions; realized P/L stays intact."""
from dataclasses import replace
from datetime import timedelta

from portfolio.domain.analytics import ReportRow
from portfolio.domain.live_quotes import NY, quote_usable


def closed_current_prices(report, instruments, quotes, now, session=None, currency='USD'):
    column = next((i for i, name in enumerate(report.columns) if name.startswith('当前价(')), None)
    if column is None:
        return report
    rows = []
    for row in report.rows:
        values, meta = list(row.values), dict(row.metadata)
        instrument = instruments.get(meta.get('instrument_id'), {})
        candidates = [q for (symbol, _), q in quotes.items()
                      if symbol == instrument.get('symbol', '') + '.US'
                      and instrument.get('market') == 'US' and currency == 'USD'
                      and q.price.is_finite() and q.price > 0 and q.timestamp <= now]
        if candidates:
            quote = max(candidates, key=lambda q: q.timestamp)
            # Do not replace a newer database close with an older cache quote.
            reference = str(meta.get('current_price_time') or '')
            reference_day = reference[:10]
            if not reference_day or quote.timestamp.astimezone(NY).date().isoformat() >= reference_day:
                values[column] = quote.price
                fresh = quote_usable(quote, session, now) and now - quote.timestamp <= timedelta(seconds=180)
                meta.update(current_price_time=quote.timestamp.isoformat(),
                            current_price_status='实时报价' if fresh else '缓存参考价（请关注时间）')
        rows.append(ReportRow(tuple(values), meta))
    stamps = [str(row.metadata['current_price_time']) for row in rows if row.metadata.get('current_price_time')]
    note = '当前价仅作清仓后参考，不参与历史清仓收益；优先使用最新可用长桥缓存，无报价显示—；参考价不代表实时价。'
    if stamps:
        note += f' 报价时间范围：{min(stamps)} 至 {max(stamps)}，逐行时点见当前价提示。'
    return replace(report, rows=tuple(rows), note=note)
