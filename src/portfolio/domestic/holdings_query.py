"""Read-only account-scoped data for the domestic holdings workspace."""
from contextlib import ExitStack
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from itertools import groupby

from portfolio.paths import DOMESTIC_ASSETS_DIR
from .calculate import ALIPAY_ZERO_OPENINGS
from .fund_returns import confirmed_returns
from .storage import ACCOUNT, CALC_FILE, RAW_FILE, connect

PENDING = "待计算"
SOURCE_PRIORITY = {"AKShare/Eastmoney": 0, "AKShare/Eastmoney Hong Kong": 0}


def positive(value):
    try:
        result = Decimal(value) if value is not None else None
        return result if result is not None and result.is_finite() and result > 0 else None
    except InvalidOperation:
        return None


def daily_nav(raw, tables, code, cutoff, *, history=False):
    if "raw_fund_nav_daily" in tables:
        rows = raw.execute("""SELECT * FROM raw_fund_nav_daily WHERE fund_code=? AND nav_date<=?
            AND unit_nav IS NOT NULL ORDER BY nav_date DESC""", (code, cutoff))
        for day, entries in groupby(rows, key=lambda row: row["nav_date"]):
            valid = [dict(row) for row in entries if positive(row["unit_nav"]) is not None]
            if not valid:
                continue
            valid.sort(key=lambda row: (SOURCE_PRIORITY.get(row["source"], 1), row["source"]))
            preferred = valid[0]
            conflict = len({positive(row["unit_nav"]) for row in valid}) > 1
            equal_priority_conflict = any(SOURCE_PRIORITY.get(row["source"], 1) ==
                SOURCE_PRIORITY.get(preferred["source"], 1) and positive(row["unit_nav"]) !=
                positive(preferred["unit_nav"]) for row in valid)
            preferred["nav_issue"] = "同日来源净值冲突" if conflict else None
            if equal_priority_conflict:
                preferred["unit_nav"] = None
            yield preferred
            if not history:
                return
        if history:
            return
    if "raw_fund_nav" in tables:
        rows = raw.execute("""SELECT * FROM raw_fund_nav WHERE fund_code=? AND nav_date<=?
            ORDER BY nav_date DESC""", (code, cutoff))
        for _, entries in groupby(rows, key=lambda row: row["nav_date"]):
            valid = [dict(row) for row in entries if positive(row["unit_nav"]) is not None]
            if valid:
                selected = valid[0]
                if len({positive(row["unit_nav"]) for row in valid}) != 1:
                    selected.update(unit_nav=None, nav_issue="旧表同日净值冲突")
                yield selected
                if not history:
                    return



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
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        unified = "raw_fund_transactions" in tables
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
        metadata = ({r["fund_code"]: dict(r) for r in raw.execute("SELECT * FROM raw_fund_info")}
                    if "raw_fund_info" in tables else {})
        tiantian = {}
        split_dates = {}
        if "raw_fund_corporate_actions" in tables:
            for code, day in raw.execute("SELECT fund_code,event_date FROM raw_fund_corporate_actions WHERE event_type='SPLIT'"):
                split_dates.setdefault(code, []).append(day)
        if calc and calc.execute("SELECT 1 FROM sqlite_master WHERE name='calc_tiantian_run'").fetchone():
            generation = calc.execute("SELECT completed_at FROM calc_tiantian_run").fetchone()
            imported = raw.execute("""SELECT MAX(b.created_at) FROM raw_fund_source_files f
                JOIN raw_import_batches b ON b.id=f.id WHERE f.source_platform='TIANTIAN'""").fetchone()[0]
            if generation and (not imported or generation[0] >= imported):
                tiantian = {(r["account_id"], r["fund_code"]): dict(r)
                            for r in calc.execute("SELECT * FROM calc_tiantian_funds")}
        statements = {}
        if calc and calc.execute("SELECT 1 FROM sqlite_master WHERE name='calc_tiantian_statement_positions'").fetchone():
            for row in calc.execute("""SELECT p.* FROM calc_tiantian_statement_positions p
                WHERE as_of=(SELECT MAX(s.as_of) FROM calc_tiantian_statement_positions s
                             WHERE s.account_id=p.account_id)"""):
                key = row["account_id"], row["fund_code"]
                statements[key] = dict(row)
                records.setdefault(key, dict(row))
        for key, record in records.items():
            if account and key != (account, fund):
                continue
            position = positions.get(key)
            opening = openings.get(key)
            recent = tiantian.get(key)
            as_of = recent["as_of"] if recent else position["trade_date"] if position else None
            shares = (recent["observed_shares"] if not recent["issue"] else None) if recent else (
                position["holding_shares"] if position else None)
            issue = recent["issue"] if recent else None
            statement = statements.get(key)
            if statement and (not as_of or statement["as_of"] >= as_of or not shares):
                as_of, shares = statement["as_of"], statement["holding_shares"]
                issue = "份额来自天天基金对账单期末快照"
                if recent and recent["issue"]:
                    issue += "；历史流水缺档"
            else:
                statement = None
            if not as_of:
                issue = "交易已入库，持仓结果待更新"
            if as_of and record.get("confirmation_date") and record["confirmation_date"] > as_of:
                shares, issue = None, "有尚未纳入计算的确认交易"
            if position and any((opening["snapshot_date"] if opening else "2021-11-11") < day <= as_of
                                for day in split_dates.get(key[1], ())):
                shares, issue = None, "支付宝拆分与账户份额变动待核对"
            nav_record = next(daily_nav(raw, tables, key[1], as_of or date.today().isoformat()), {})
            if statement and not nav_record and statement["unit_nav"]:
                nav_record = dict(unit_nav=statement["unit_nav"], nav_date=statement["nav_date"],
                                  source="天天基金对账单期末快照")
            nav_date = nav_record.get("nav_date")
            nav = positive(nav_record.get("unit_nav"))
            qty = Decimal(shares) if shares is not None else None
            status = PENDING if qty is None or qty < 0 else "已清仓" if qty == 0 else "持有中"
            if status == PENDING and not issue:
                issue = "当前份额待核对"
            if nav_record.get("nav_issue"):
                issue = "；".join(filter(None, (issue, nav_record["nav_issue"])))
            if qty is not None and qty > 0 and nav is None:
                issue = "；".join(filter(None, (issue, "缺少有效单位净值")))
            if nav_date and (date.today() - date.fromisoformat(nav_date)).days > 7:
                issue = "；".join(filter(None, (issue, "净值日期距今超过 7 天")))
            value = str(qty * nav) if status == "持有中" and nav is not None else None
            info = metadata.get(key[1], {})
            meta = accounts.get(key[0], {})
            returns = {}
            if (key[0] == ACCOUNT and position and status != PENDING
                    and key[1] in ALIPAY_ZERO_OPENINGS.get(date(2021, 11, 11), ()) and not opening):
                trades = calc.execute("""SELECT t.transaction_type,t.confirmed_amount,t.status,m.signed_shares
                    FROM calc_fund_transactions t LEFT JOIN calc_fund_share_movements m
                    ON m.raw_transaction_id=t.raw_transaction_id
                    WHERE t.account_id=? AND t.fund_code=? AND
                    (t.effective_date IS NULL OR t.effective_date>? AND t.effective_date<=?)
                    ORDER BY t.effective_date,t.raw_transaction_id""", (*key, "2021-11-11", as_of))
                returns = confirmed_returns(trades, qty, Decimal(value) if value is not None else None)
            return_issue = None if returns else (
                "天天基金历史流水或成本基准待核对" if meta.get("platform") == "天天基金" else
                "期初成本、交易或估值尚未完整核对")
            result["rows"].append(dict(**returns, return_issue=return_issue, account_id=key[0], fund_code=key[1], fund_name=info.get("fund_name") or record["fund_name"],
                fund_type=info.get("fund_type") or record.get("fund_type") or "待分类", platform=meta.get("platform", "—"),
                account_name=meta.get("account_name", "—"), shares=shares, nav=str(nav) if nav is not None else None,
                nav_date=nav_date, market_value=value, as_of=as_of, status=status, issue=issue,
                accumulated_nav=nav_record.get("accumulated_nav"), daily_return_rate=nav_record.get("daily_return_rate"),
                nav_source=nav_record.get("source"), nav_status=nav_record.get("field_status"),
                observed_shares=recent["observed_shares"] if recent and recent["issue"] else None))
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
            recent = tiantian.get((account, fund))
            if recent:
                if not recent["issue"]:
                    result["rows"][0].update(buy_shares=recent["buy_shares"], sell_shares=recent["sell_shares"],
                                             cash_dividend=recent["cash_dividend"])
                table = "calc_tiantian_positions"
            elif (account, fund) in positions:
                table = "calc_fund_positions_daily"
            else:
                table = None
            if table:
                snapshots = {r["trade_date"]: r["holding_shares"] for r in calc.execute(
                    f"SELECT trade_date,holding_shares FROM {table} WHERE account_id=? AND fund_code=?",
                    (account, fund))}
                if recent and recent["issue"]:
                    confirmed_day = max((day for day, qty in snapshots.items() if qty is not None), default=None)
                    if confirmed_day:
                        result["rows"][0].update(last_confirmed_date=confirmed_day,
                                                 last_confirmed_shares=snapshots[confirmed_day])
                history = {}
                if not recent and split_dates.get(fund):
                    baseline = openings.get((account, fund))
                    start = baseline["snapshot_date"] if baseline else "2021-11-11"
                    first_split = min((day for day in split_dates[fund] if day > start), default=None)
                    if first_split:
                        snapshots = {day: qty if day < first_split else None for day, qty in snapshots.items()}
                for day, qty in sorted(snapshots.items()):
                    history[day[:7]] = dict(month=day[:7], holding_shares=qty, market_value=None)
                seen_months = set()
                for price in daily_nav(raw, tables, fund, max(snapshots, default=""), history=True):
                    day = price["nav_date"]
                    month = day[:7]
                    if month in seen_months or day not in snapshots:
                        continue
                    seen_months.add(month)
                    qty, nav = snapshots[day], positive(price["unit_nav"])
                    if qty is not None and nav is not None and Decimal(qty) >= 0 and history[month]["holding_shares"] is not None:
                        history[month]["market_value"] = str(Decimal(qty) * nav)
                        history[month]["nav_date"] = day
                result["history"] = list(history.values())
            row = result["rows"][0]
            statement = statements.get((account, fund))
            if statement and row["shares"] is not None and row["as_of"] == statement["as_of"]:
                month = statement["as_of"][:7]
                result["history"] = [r for r in result["history"] if r["month"] != month]
                result["history"].append(dict(month=month, holding_shares=row["shares"], market_value=row["market_value"],
                                              nav_date=row["nav_date"]))
    return result
