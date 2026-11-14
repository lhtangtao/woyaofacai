"""Read-only daily valuation inputs for the Mac domestic holdings dashboard."""
from bisect import bisect_right
from collections import Counter
from datetime import date, timedelta
from importlib.resources import files
import json
from decimal import Decimal, localcontext

from portfolio.paths import DOMESTIC_ASSETS_DIR
from .holdings_query import ESTIMATED_NAV_SOURCES, daily_nav, positive
from .storage import ACCOUNT, CALC_FILE, RAW_FILE, connect
from .tiantian_positions import BUY, SELL, movement, subscription_key


class HistoricalValues(dict):
    def __init__(self):
        super().__init__()
        self.nav_dates = {}
        self.nav_sources = {}
        self.missing = {}
        self.cum_pnl = {}
        self.money_funds = set()
        self.calendar_incomplete = False


def _build_cum_net_cash_series(raw, calc, raw_tables, calc_tables, rows, cutoff):
    """为已核对成本的基金构建按日期递增的累计净投入现金序列。"""
    opening_costs = {}
    if "raw_fund_opening_cost_baselines" in raw_tables:
        for r in raw.execute("SELECT account_id, snapshot_date, fund_code, opening_cost FROM raw_fund_opening_cost_baselines"):
            opening_costs[(r[0], r[2])] = (r[1], Decimal(r[3]))

    series_by_key = {}
    for row in rows:
        if row.get("is_historical_baseline"):
            continue
        has_verified = (not row.get("return_issue")) or (row.get("pnl") is not None) or (row.get("last_confirmed_total_pnl") is not None)
        if not has_verified:
            continue
        key = row["account_id"], row["fund_code"]
        events = {}
        if key[0] == ACCOUNT and "calc_fund_transactions" in calc_tables:
            open_day, open_cost = opening_costs.get(key, ("2021-11-11", Decimal(0)))
            if open_cost != 0:
                events[open_day] = events.get(open_day, Decimal(0)) + open_cost
            for t in calc.execute(
                """SELECT effective_date, transaction_type, confirmed_amount, status
                   FROM calc_fund_transactions
                   WHERE account_id=? AND fund_code=? AND effective_date IS NOT NULL
                   AND effective_date>? AND effective_date<=?
                   ORDER BY effective_date, raw_transaction_id""",
                (*key, open_day, cutoff),
            ):
                if t[3] != "正常" or t[2] is None:
                    continue
                amt = Decimal(t[2])
                if t[1] in ("BUY", "TRANSFER_IN"):
                    events[t[0]] = events.get(t[0], Decimal(0)) + amt
                elif t[1] in ("SELL", "TRANSFER_OUT", "CASH_DIVIDEND"):
                    events[t[0]] = events.get(t[0], Decimal(0)) - amt
        elif row["platform"] == "天天基金" and "raw_fund_transactions" in raw_tables:
            trades = [dict(r) for r in raw.execute(
                """SELECT * FROM raw_fund_transactions
                   WHERE account_id=? AND fund_code=? AND source_platform='TIANTIAN'
                   AND confirmation_date IS NOT NULL AND confirmation_date<=?
                   ORDER BY confirmation_date, transaction_time, id""",
                (*key, cutoff),
            )]
            confirmations = Counter(
                subscription_key(r) for r in trades
                if r["transaction_type_raw"] == "认购结果"
                and r["confirmation_status"] == "成功" and r["parse_status"] == "正常"
            )
            for t in trades:
                delta = movement(t, confirmations)
                if delta is None:
                    continue
                kind = t["transaction_type_raw"]
                amt = Decimal(t["confirmed_amount"] or 0)
                day = t["confirmation_date"]
                if kind in BUY | {"超级转换-转入"} and delta > 0:
                    events[day] = events.get(day, Decimal(0)) + amt
                elif kind in SELL | {"超级转换-转出", "现金分红"}:
                    events[day] = events.get(day, Decimal(0)) - amt
        sorted_days = sorted(events)
        running = Decimal(0)
        cum_vals = []
        for d in sorted_days:
            running += events[d]
            cum_vals.append(running)
        series_by_key[key] = (sorted_days, cum_vals)
    return series_by_key


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
                navs[key[1]] = {}
                for r in daily_nav(raw, raw_tables, key[1], cutoff, history=True):
                    disclosed = r["nav_date"]
                    if r["source"] == "用户指定周净值沿用":
                        try:
                            disclosed = json.loads(r["raw_payload"])["原净值日期"]
                            date.fromisoformat(disclosed)
                        except (KeyError, TypeError, ValueError):
                            disclosed = r["nav_date"]
                    navs[key[1]][r["nav_date"]] = (positive(r["unit_nav"]), r["source"], disclosed)
                if "raw_fund_nav" in raw_tables:
                    for nav_day, value, source in raw.execute("""SELECT nav_date,unit_nav,source FROM raw_fund_nav
                            WHERE fund_code=? AND nav_date<=?""", (key[1], cutoff)):
                        price = positive(value)
                        if price is None:
                            continue
                        existing = navs[key[1]].get(nav_day)
                        if existing and existing[1] in ESTIMATED_NAV_SOURCES:
                            navs[key[1]][nav_day] = (price, source, nav_day)
                        elif existing and existing[0] != price:
                            navs[key[1]][nav_day] = (None, "净值来源冲突", nav_day)
                        elif not existing:
                            navs[key[1]][nav_day] = (price, source, nav_day)
                if "calc_tiantian_statement_positions" in calc_tables:
                    for snapshot in calc.execute("""SELECT nav_date,unit_nav FROM calc_tiantian_statement_positions
                            WHERE fund_code=? AND as_of<=? AND nav_date IS NOT NULL
                            AND nav_date<=as_of AND unit_nav IS NOT NULL""", (key[1], cutoff)):
                        nav_day, value = snapshot
                        price = positive(value)
                        if price is None:
                            continue
                        existing = navs[key[1]].get(nav_day)
                        if existing and existing[1] in ESTIMATED_NAV_SOURCES:
                            navs[key[1]][nav_day] = (price, "天天基金对账单", nav_day)
                        elif existing and existing[0] != price:
                            navs[key[1]][nav_day] = (None, "净值来源冲突", nav_day)
                        elif not existing:
                            navs[key[1]][nav_day] = (price, "天天基金对账单", nav_day)
            nav_dates[key[1]] = sorted(navs[key[1]])
            days.update(navs[key[1]])
        result = HistoricalValues()
        if days:
            first_day = min(days)
            try:
                calendar = json.loads(files("portfolio").joinpath("resources/china_market_sessions.json").read_text(encoding="utf-8"))
                sessions = set(calendar["sessions"])
                # Beyond the bundled calendar, retain known NAV dates and weekdays
                # as candidates so that an unknown holiday cannot hide missing data.
                result.calendar_incomplete = first_day < calendar["start"] or cutoff > calendar["end"]
                current = date.fromisoformat(first_day)
                end = date.fromisoformat(cutoff)
                selected = []
                while current <= end:
                    day = current.isoformat()
                    if day in sessions or ((day < calendar["start"] or day > calendar["end"]) and current.weekday() < 5):
                        selected.append(day)
                    current += timedelta(days=1)
                days = selected
            except (OSError, KeyError, ValueError):
                result.calendar_incomplete = True
                current = date.fromisoformat(first_day)
                end = date.fromisoformat(cutoff)
                days = []
                while current <= end:
                    if current.weekday() < 5:
                        days.append(current.isoformat())
                    current += timedelta(days=1)
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
            net_cash_series = _build_cum_net_cash_series(raw, calc, raw_tables, calc_tables, rows, cutoff)
            for key, balances in quantities.items():
                prices = navs.get(key[1], {})
                cash_days, cash_vals = net_cash_series.get(key, ([], []))
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
                    day_obj = date.fromisoformat(day)
                    item = (day_obj, key)
                    result.nav_dates[item] = price_entry[2] if price_entry is not None else None
                    result.nav_sources[item] = price_entry[1] if price_entry is not None else None
                    if quantity is None or quantity < 0:
                        result.missing[item] = "历史份额缺失或覆盖不足"
                    elif quantity > 0 and price is None:
                        result.missing[item] = "单位净值缺失、冲突或超过7天"
                    mv = (
                        Decimal(0) if quantity == 0 else
                        quantity * price if quantity is not None and quantity > 0 and price is not None else None
                    )
                    result.setdefault(day_obj, {})[key] = mv
                    if mv is not None and key in net_cash_series:
                        c_idx = bisect_right(cash_days, day) - 1
                        net_in = cash_vals[c_idx] if c_idx >= 0 else Decimal(0)
                        result.cum_pnl.setdefault(day_obj, {})[key] = mv - net_in
        return result
