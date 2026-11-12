"""Read-only daily valuation inputs for the Mac domestic holdings dashboard."""
from bisect import bisect_right
from datetime import date
from decimal import Decimal, localcontext

from portfolio.paths import DOMESTIC_ASSETS_DIR
from .holdings_query import daily_nav, positive
from .storage import CALC_FILE, RAW_FILE, connect


class HistoricalValues(dict):
    def __init__(self):
        super().__init__()
        self.nav_dates = {}
        self.nav_sources = {}
        self.missing = {}


def daily_values(rows, *, root=DOMESTIC_ASSETS_DIR):
    """Value reconstructed shares on actual NAV dates; retain missing values."""
    if not rows or not (root / CALC_FILE).is_file():
        return {}
    cutoff = rows[0]["valuation_date"]
    with connect(root / RAW_FILE, readonly=True) as raw, connect(root / CALC_FILE, readonly=True) as calc:
        raw.execute("BEGIN")
        calc.execute("BEGIN")
        raw_tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        calc_tables = {r[0] for r in calc.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        navs = {}
        quantities = {}
        days = set()
        beginnings = {}
        nav_dates = {}
        for row in rows:
            key = row["account_id"], row["fund_code"]
            table = "calc_tiantian_positions" if row["platform"] == "天天基金" else "calc_fund_positions_daily"
            if table not in calc_tables or not row["position_as_of"]:
                quantities[key] = {}
                continue
            balances = calc.execute(f"""SELECT trade_date, holding_shares FROM {table}
                WHERE account_id=? AND fund_code=? AND trade_date<=?""", (*key, row["position_as_of"]))
            quantities[key] = {r[0]: Decimal(r[1]) if r[1] is not None else None for r in balances}
            if quantities[key]:
                first = min(quantities[key])
                # A zero opening proves the fund was not held before its first recorded day.
                if quantities[key][first] == 0:
                    beginnings[key] = first
                elif row["platform"] == "天天基金" and not row.get("observed_shares"):
                    first_trade = raw.execute("""SELECT confirmation_date,transaction_type_raw,parse_status,
                        confirmation_status FROM raw_fund_transactions WHERE account_id=? AND fund_code=?
                        ORDER BY confirmation_date,id LIMIT 1""", key).fetchone()
                    if first_trade and first_trade[0] == first and first_trade[1] in (
                            "买基金", "定时定额投资", "认购结果", "超级转换-转入") and first_trade[2] == "正常" and first_trade[3] == "成功":
                        beginnings[key] = first
            if key[1] not in navs:
                navs[key[1]] = {r["nav_date"]: (positive(r["unit_nav"]), r["source"])
                                for r in daily_nav(raw, raw_tables, key[1], cutoff, history=True)}
            nav_dates[key[1]] = sorted(navs[key[1]])
            days.update(navs[key[1]])
        days = sorted(days)
        result = HistoricalValues()
        with localcontext() as precision:
            precision.prec = 60
            for key, balances in quantities.items():
                prices = navs.get(key[1], {})
                for day in days:
                    quantity = balances.get(day)
                    if day not in balances and key in beginnings and day < beginnings[key]:
                        quantity = Decimal(0)
                    dates = nav_dates.get(key[1], [])
                    index = bisect_right(dates, day) - 1
                    price_day = dates[index] if index >= 0 else None
                    price_entry = prices.get(price_day) if price_day and (
                        date.fromisoformat(day) - date.fromisoformat(price_day )).days <= 7 else None
                    price = price_entry[0] if price_entry else None
                    item = (date.fromisoformat(day), key)
                    result.nav_dates[item] = price_day if price is not None else None
                    result.nav_sources[item] = price_entry[1] if price is not None else None
                    if quantity is None or quantity < 0:
                        result.missing[item] = "历史份额缺失或覆盖不足"
                    elif quantity > 0 and price is None:
                        result.missing[item] = "单位净值缺失、冲突或超过7天"
                    result.setdefault(date.fromisoformat(day), {})[key] = (
                        Decimal(0) if quantity == 0 else
                        quantity * price if quantity is not None and quantity > 0 and price is not None else None
                    )
        return result
