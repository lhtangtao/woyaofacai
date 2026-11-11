"""Read-only account-scoped data for the domestic holdings workspace."""
from contextlib import ExitStack
from decimal import Decimal, localcontext

from portfolio.paths import DOMESTIC_ASSETS_DIR
from .storage import CALC_FILE, RAW_FILE, connect

PENDING = "交易数据已导入，持仓计算待接入"


def holdings(account="", fund="", *, root=DOMESTIC_ASSETS_DIR):
    result = dict(rows=[], transactions=[], history=[], raw_path=str(root / RAW_FILE),
                  calc_path=str(root / CALC_FILE))
    if not (root / RAW_FILE).exists():
        return result
    with ExitStack() as stack, localcontext() as precision:
        precision.prec = 60
        raw = stack.enter_context(connect(root / RAW_FILE, readonly=True))
        raw.execute("BEGIN")
        calc = stack.enter_context(connect(root / CALC_FILE, readonly=True)) if (root / CALC_FILE).exists() else None
        if calc:
            calc.execute("BEGIN")
        unified = raw.execute("SELECT 1 FROM sqlite_master WHERE name='raw_fund_transactions'").fetchone()
        source = """SELECT account_id,fund_code,fund_name,fund_type,confirmation_date,
            transaction_type_raw AS kind,confirmed_shares,confirmed_amount,fee,
            confirmed_nav,confirmation_status,parse_status,parse_error,id,source_page
            FROM raw_fund_transactions""" if unified else """SELECT account_id,fund_code,fund_name,
            NULL AS fund_type,confirmation_date,transaction_type AS kind,confirmed_shares,
            confirmed_amount,fee,NULL AS confirmed_nav,NULL AS confirmation_status,
            parse_status,parse_error,id,source_page FROM raw_alipay_fund_transactions"""
        records = {}
        for row in raw.execute(f"""SELECT * FROM ({source}) WHERE fund_code IS NOT NULL
            AND fund_code<>'' ORDER BY confirmation_date,id"""):
            records[(row["account_id"], row["fund_code"])] = dict(row)
        openings = {}
        for row in raw.execute("SELECT * FROM raw_fund_opening_positions ORDER BY snapshot_date"):
            key = row["account_id"], row["fund_code"]
            openings[key] = row
            records.setdefault(key, dict(row, fund_type=None))
        accounts = {r["account_id"]: dict(r) for r in raw.execute("SELECT * FROM raw_asset_accounts")}
        positions = {}
        latest_import = raw.execute("""SELECT MAX(created_at) FROM raw_import_batches
            WHERE source_type IN ('opening_asset','transaction')""").fetchone()[0]
        if calc:
            run = calc.execute("SELECT MAX(completed_at) FROM calc_runs WHERE status='完成'").fetchone()[0]
            if run and (not latest_import or run >= latest_import):
                positions = {(r["account_id"], r["fund_code"]): r for r in calc.execute("""
                    SELECT * FROM calc_fund_positions_daily
                    WHERE trade_date=(SELECT MAX(trade_date) FROM calc_fund_positions_daily)""")}
        navs = {}
        for row in raw.execute("SELECT fund_code,nav_date,unit_nav FROM raw_fund_nav ORDER BY nav_date"):
            code = row["fund_code"]
            if code not in navs or navs[code][0] != row["nav_date"]:
                navs[code] = (row["nav_date"], set())
            navs[code][1].add(Decimal(row["unit_nav"]))
        for key, record in records.items():
            if account and key != (account, fund):
                continue
            position = positions.get(key)
            shares = position["holding_shares"] if position else None
            opening = openings.get(key)
            nav_date, prices = navs.get(key[1], ("", set()))
            nav = next(iter(prices)) if len(prices) == 1 else None
            if opening and opening["unit_nav_date"] > nav_date:
                nav_date, nav = opening["unit_nav_date"], Decimal(opening["unit_nav"])
            status = "待核对" if shares is None and position else PENDING
            value = None
            if shares is not None:
                qty = Decimal(shares)
                status = "已清仓" if qty == 0 else "持有中" if qty > 0 else "待核对"
                # A historical NAV cannot value shares confirmed after that NAV date.
                if qty == 0:
                    value = "0"
                elif qty > 0 and nav is not None and nav_date >= position["trade_date"]:
                    value = str(qty * nav)
            meta = accounts.get(key[0], {})
            result["rows"].append(dict(account_id=key[0], fund_code=key[1], fund_name=record["fund_name"],
                fund_type=record.get("fund_type") or "未分类", platform=meta.get("platform", "—"),
                account_name=meta.get("account_name", "—"), shares=shares, nav=str(nav) if nav is not None else None,
                nav_date=nav_date if nav is not None else None, market_value=value,
                as_of=position["trade_date"] if position else None, status=status))
        if account and result["rows"]:
            result["transactions"] = [dict(r) for r in raw.execute(
                f"SELECT * FROM ({source}) WHERE account_id=? AND fund_code=? ORDER BY confirmation_date DESC,id",
                (account, fund))]
            if (account, fund) in positions:
                totals = {"BUY": Decimal(0), "SELL": Decimal(0)}
                for move in calc.execute("""SELECT m.movement_type,m.signed_shares
                    FROM calc_fund_share_movements m JOIN calc_fund_transactions t
                    ON t.raw_transaction_id=m.raw_transaction_id
                    WHERE t.account_id=? AND m.fund_code=? AND m.movement_type IN ('BUY','SELL')""",
                    (account, fund)):
                    totals[move[0]] += abs(Decimal(move[1]))
                result["rows"][0].update(buy_shares=str(totals["BUY"]), sell_shares=str(totals["SELL"]))
                result["history"] = [dict(r) for r in calc.execute("""SELECT month,holding_shares,market_value
                    FROM calc_fund_positions_monthly WHERE account_id=? AND fund_code=? ORDER BY month""",
                    (account, fund))]
    return result
