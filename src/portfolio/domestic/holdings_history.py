"""Read-only daily valuation inputs for the Mac domestic holdings dashboard."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext

from portfolio.paths import DOMESTIC_ASSETS_DIR
from .holdings_query import daily_nav, positive
from .storage import CALC_FILE, RAW_FILE, connect


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
        for row in rows:
            key = row["account_id"], row["fund_code"]
            table = "calc_tiantian_positions" if row["platform"] == "天天基金" else "calc_fund_positions_daily"
            if table not in calc_tables or not row["position_as_of"]:
                quantities[key] = {}
                continue
            balances = calc.execute(f"""SELECT trade_date, holding_shares FROM {table}
                WHERE account_id=? AND fund_code=? AND trade_date<=?""", (*key, row["position_as_of"]))
            quantities[key] = {r[0]: Decimal(r[1]) for r in balances if r[1] is not None}
            if key[1] not in navs:
                navs[key[1]] = {r["nav_date"]: positive(r["unit_nav"])
                                for r in daily_nav(raw, raw_tables, key[1], cutoff, history=True)}
            days.update(navs[key[1]])
        days = sorted(days)
        result = defaultdict(dict)
        with localcontext() as precision:
            precision.prec = 60
            for key, balances in quantities.items():
                prices = navs.get(key[1], {})
                for day in days:
                    quantity, price = balances.get(day), prices.get(day)
                    result[date.fromisoformat(day)][key] = (
                        Decimal(0) if quantity == 0 else
                        quantity * price if quantity is not None and quantity > 0 and price is not None else None
                    )
        return dict(result)
