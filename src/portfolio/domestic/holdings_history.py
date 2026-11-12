"""Read-only daily valuation inputs for the Mac domestic holdings dashboard."""
from bisect import bisect_right
from datetime import date, timedelta
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
        self.money_funds = set()


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
        periods = {}
        if "raw_fund_source_files" in raw_tables:
            for account, start, end in raw.execute("""SELECT account_id,period_start,period_end
                    FROM raw_fund_source_files WHERE source_platform='TIANTIAN'
                    AND period_start IS NOT NULL AND period_end IS NOT NULL"""):
                periods.setdefault(account, []).append((date.fromisoformat(start), date.fromisoformat(end)))
        if "calc_tiantian_no_trade_periods" in calc_tables:
            for account, start, end in calc.execute("SELECT account_id,start_date,end_date FROM calc_tiantian_no_trade_periods"):
                periods.setdefault(account, []).append((date.fromisoformat(start), date.fromisoformat(end)))
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
                if row["platform"] == "天天基金" and not row.get("observed_shares"):
                    first_trade = raw.execute("""SELECT confirmation_date,transaction_type_raw,parse_status,
                        confirmation_status FROM raw_fund_transactions WHERE account_id=? AND fund_code=?
                        ORDER BY confirmation_date,id LIMIT 1""", key).fetchone()
                    if first_trade and first_trade[0] == first and first_trade[1] in (
                            "买新基金", "买基金", "定时定额投资", "认购结果", "超级转换-转入") and first_trade[2] == "正常" and first_trade[3] == "成功" and (
                            first_trade[1] != "买新基金" or quantities[key][first] == 0):
                        covered_from = date.fromisoformat(first)
                        for start, end in sorted(periods.get(key[0], []), reverse=True):
                            if start <= covered_from and end + timedelta(days=1) >= covered_from:
                                covered_from = min(covered_from, start)
                        beginnings[key] = (covered_from.isoformat(), first)
            if key[1] not in navs:
                navs[key[1]] = {r["nav_date"]: (positive(r["unit_nav"]), r["source"])
                                for r in daily_nav(raw, raw_tables, key[1], cutoff, history=True)}
                if "raw_fund_nav" in raw_tables:
                    for nav_day, value, source in raw.execute("""SELECT nav_date,unit_nav,source FROM raw_fund_nav
                            WHERE fund_code=? AND nav_date<=?""", (key[1], cutoff)):
                        price = positive(value)
                        if price is None:
                            continue
                        existing = navs[key[1]].get(nav_day)
                        if existing and existing[0] != price:
                            navs[key[1]][nav_day] = (None, "净值来源冲突")
                        elif not existing:
                            navs[key[1]][nav_day] = (price, source)
                if "calc_tiantian_statement_positions" in calc_tables:
                    for snapshot in calc.execute("""SELECT nav_date,unit_nav FROM calc_tiantian_statement_positions
                            WHERE fund_code=? AND as_of<=? AND nav_date IS NOT NULL
                            AND nav_date<=as_of AND unit_nav IS NOT NULL""", (key[1], cutoff)):
                        nav_day, value = snapshot
                        price = positive(value)
                        if price is None:
                            continue
                        existing = navs[key[1]].get(nav_day)
                        if existing and existing[0] != price:
                            navs[key[1]][nav_day] = (None, "净值来源冲突")
                        elif not existing:
                            navs[key[1]][nav_day] = (price, "天天基金对账单")
            nav_dates[key[1]] = sorted(navs[key[1]])
            days.update(navs[key[1]])
        if days:
            first_day = date.fromisoformat(min(days))
            last_day = date.fromisoformat(cutoff)
            days = [(first_day + timedelta(days=i)).isoformat()
                    for i in range((last_day - first_day).days + 1)]
        result = HistoricalValues()
        if "raw_fund_nav_daily" in raw_tables:
            result.money_funds = {r[0] for r in raw.execute("""
                SELECT DISTINCT fund_code FROM raw_fund_nav_daily
                WHERE unit_nav IS NULL AND (income_per_10k IS NOT NULL OR yield_7d IS NOT NULL)
            """)}
        result.money_funds.update(row["fund_code"] for row in rows
                                  if "货币" in (row.get("fund_type") or "")
                                  or "货币" in (row.get("fund_name") or ""))
        with localcontext() as precision:
            precision.prec = 60
            for key, balances in quantities.items():
                prices = navs.get(key[1], {})
                for day in days:
                    quantity = balances.get(day)
                    if day not in balances and key in beginnings:
                        coverage_start, first = beginnings[key]
                        if coverage_start and coverage_start <= day < first:
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
