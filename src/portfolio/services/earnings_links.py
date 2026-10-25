"""Issuer-verified single-stock ETF links. Never infer from ticker similarity."""
# (underlying, verification source). These are earnings interests, not accounting
# conversions: ETF holdings and P/L remain attached to the held security.
SINGLE_STOCK_LINKS = {
    'AAOX.US': ('AAOI.US', 'https://www.tradretfs.com/hubfs/Tradr%20ETFs%20Press%20Releases/20260324%20PRESS%20RELEASE%20Launch%20AAOX%20AMZO%20HLXX%20IBX.pdf'),
    'AVGX.US': ('AVGO.US', 'https://www.defianceetfs.com/avgx/'),
    'GGLL.US': ('GOOGL.US', 'https://www.direxion.com/product/daily-googl-bull-and-bear-leveraged-single-stock-etfs'),
    'INTW.US': ('INTC.US', 'https://www.graniteshares.com/institutional/us/en-us/fund-map/'),
    'LITX.US': ('LITE.US', 'https://www.tradretfs.com/news-and-media/tradr-debuts-leveraged-etfs-on-lumentum-sandisk-and-western-digital'),
    'MUU.US': ('MU.US', 'https://www.direxion.com/product/daily-mu-bull-and-bear-leveraged-single-stock-etfs'),
    'ORCX.US': ('ORCL.US', 'https://www.defianceetfs.com/orcx/'),
    'SNXX.US': ('SNDK.US', 'https://www.tradretfs.com/news-and-media/tradr-debuts-leveraged-etfs-on-lumentum-sandisk-and-western-digital'),
    'SPAL.US': ('SPCX.US', 'https://graniteshares.com/etfs/spal/'),
    'STXL.US': ('STX.US', 'https://www.defianceetfs.com/stxl/'),
    'TSLL.US': ('TSLA.US', 'https://www.direxion.com/product/daily-tsla-bull-and-bear-leveraged-single-stock-etfs'),
    'TSLS.US': ('TSLA.US', 'https://www.direxion.com/product/daily-tsla-bull-and-bear-leveraged-single-stock-etfs'),
    'WDCX.US': ('WDC.US', 'https://www.tradretfs.com/news-and-media/tradr-debuts-leveraged-etfs-on-lumentum-sandisk-and-western-digital'),
}
INDEX_FUNDS = {
    'KORU.US': 'https://www.direxion.com/product/daily-msci-south-korea-bull-3x-etf',
    'TQQQ.US': 'https://indexes.nasdaqomx.com/Index/Overview/NDX',
    'SQQQ.US': 'https://www.proshares.com/our-etfs/leveraged-and-inverse/sqqq',
    'QQQM.US': 'https://indexes.nasdaqomx.com/Index/Overview/NDX',
}
DEFAULT_LINKS = {held: value[0] for held, value in SINGLE_STOCK_LINKS.items()} | dict.fromkeys(INDEX_FUNDS, '')


def migrate_legacy_links(overrides):
    """Old editor saved untouched self-links too; migrate those once only."""
    return {held: target for held, target in overrides.items() if not (held in DEFAULT_LINKS and target == held)}


def default_target(symbol, category='', name=''):
    if symbol in DEFAULT_LINKS:
        return DEFAULT_LINKS[symbol]
    if category == '杠杆' or 'ETF' in name.upper():
        return ''  # unverified fund: ask for its company instead of guessing
    return symbol


def link_description(symbol, target, overrides):
    if symbol in overrides:
        return '手动关联' if target else '手动不关联'
    if symbol in INDEX_FUNDS:
        return '指数基金 · 无单一正股'
    if symbol in SINGLE_STOCK_LINKS:
        return '自动关联正股'
    return '公司自身' if target == symbol else '待关联正股'
