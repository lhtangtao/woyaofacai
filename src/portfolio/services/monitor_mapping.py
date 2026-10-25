"""Observation mappings, separate from earnings links and source instruments."""

import re
from dataclasses import asdict, dataclass

VERIFIED = "2026-10-25"
MAPPING_VERSION = "2026-10-26.1"


@dataclass(frozen=True)
class Mapping:
    held_symbol: str
    analysis_symbol: str
    relation_type: str = "DIRECT"
    leverage_multiple: str = "1"
    direction: str = "多"
    source_url: str = ""
    verified_at: str = ""
    status: str = "DIRECT"
    product_name: str = ""
    benchmark: str = ""
    user_override: bool = False

    def record(self):
        return asdict(self)


def normalize(symbol, market="US"):
    value = str(symbol).strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,24}", value):
        raise ValueError("证券代码格式无效")
    return value if value.endswith("." + market) else value + "." + market


_TSLA = "https://www.direxion.com/product/daily-tsla-bull-and-bear-leveraged-single-stock-etfs"
BUILTINS = {
    "AAOX.US": Mapping(
        "AAOX.US",
        "AAOI.US",
        "LEVERAGED_LONG",
        "2",
        "多",
        "https://www.tradretfs.com/aaox",
        VERIFIED,
        "VERIFIED",
        "Tradr 2X Long AAOI Daily ETF",
    ),
    "TSLL.US": Mapping(
        "TSLL.US",
        "TSLA.US",
        "LEVERAGED_LONG",
        "2",
        "多",
        _TSLA,
        VERIFIED,
        "VERIFIED",
        "Direxion Daily TSLA Bull 2X ETF",
    ),
    "TSLS.US": Mapping(
        "TSLS.US",
        "TSLA.US",
        "LEVERAGED_SHORT",
        "1",
        "空",
        _TSLA,
        VERIFIED,
        "VERIFIED",
        "Direxion Daily TSLA Bear 1X ETF",
    ),
    "TQQQ.US": Mapping(
        "TQQQ.US",
        "QQQ.US",
        "INDEX_PROXY",
        "3",
        "多",
        "https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq",
        VERIFIED,
        "VERIFIED",
        "ProShares UltraPro QQQ",
        "Nasdaq-100；QQQ仅为代理基准",
    ),
}
# These product labels are visible on the issuer's reviewed product directory.
for held, underlying in (("LITX", "LITE"), ("SNXX", "SNDK"), ("WDCX", "WDC")):
    BUILTINS[held + ".US"] = Mapping(
        held + ".US",
        underlying + ".US",
        "LEVERAGED_LONG",
        "2",
        "多",
        "https://www.tradretfs.com/aaox",
        VERIFIED,
        "VERIFIED",
        f"Tradr 2X Long {underlying} Daily ETF",
    )

# Issuer pages/fact sheets reviewed on 2026-10-26. Keep original held
# symbols and user overrides independent of these observation targets.
for held, underlying, product, source in (
    ("AVGX", "AVGO", "Defiance Daily Target 2X Long AVGO ETF", "https://www.defianceetfs.com/avgx/"),
    (
        "GGLL",
        "GOOGL",
        "Direxion Daily GOOGL Bull 2X ETF",
        "https://www.direxion.com/product/daily-googl-bull-and-bear-leveraged-single-stock-etfs",
    ),
    ("MUU", "MU", "Direxion Daily MU Bull 2X ETF", "https://www.direxion.com/uploads/MUU-MUD-Fact-Sheet.pdf"),
    ("ORCX", "ORCL", "Defiance Daily Target 2X Long ORCL ETF", "https://www.defianceetfs.com/orcx/"),
    ("SPAL", "SPCX", "GraniteShares 2x Long SpaceX Daily ETF", "https://graniteshares.com/etfs/spal/"),
    ("STXL", "STX", "Defiance Daily Target 2X Long STX ETF", "https://www.defianceetfs.com/stxl/"),
    ("INTW", "INTC", "GraniteShares 2x Long INTC Daily ETF", "https://graniteshares.com/etfs/intw/"),
):
    BUILTINS[held + ".US"] = Mapping(
        held + ".US",
        underlying + ".US",
        "LEVERAGED_LONG",
        "2",
        "多",
        source,
        "2026-10-26",
        "VERIFIED",
        product,
    )

BUILTINS["KORU.US"] = Mapping(
    "KORU.US",
    "EWY.US",
    "INDEX_PROXY",
    "3",
    "多",
    "https://www.direxion.com/product/daily-msci-south-korea-bull-3x-etf",
    "2026-10-26",
    "VERIFIED",
    "Direxion Daily MSCI South Korea Bull 3X ETF",
    "底层：MSCI Korea 25/50 Index；EWY仅为相关ETF代理，不是正股或指数本身。"
    "EWY公告将在2026-11-09前后更换基准，代理与指数可能偏离。",
)


def resolve(symbol, category="", name="", override=None):
    if override:
        return Mapping(**override)
    if symbol in BUILTINS:
        return BUILTINS[symbol]
    if symbol in ("QQQ.US", "QQQM.US", "SPY.US", "DIA.US", "IWM.US"):
        return Mapping(symbol, symbol, product_name=name)
    # Existing earnings links are useful candidates, never evidence of leverage.
    from portfolio.services.earnings_links import INDEX_FUNDS, SINGLE_STOCK_LINKS

    candidate = SINGLE_STOCK_LINKS.get(symbol)
    if candidate or symbol in INDEX_FUNDS or "杠杆" in category or "ETF" in name.upper() or "反向" in name:
        return Mapping(
            symbol,
            symbol,
            "UNKNOWN",
            "",
            "",
            candidate[1] if candidate else "",
            "",
            "PENDING",
            name,
            ("候选：" + candidate[0] + "，待人工确认") if candidate else "",
        )
    return Mapping(symbol, symbol, product_name=name)
