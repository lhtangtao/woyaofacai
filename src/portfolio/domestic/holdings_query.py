"""Read-only account-scoped data for the domestic holdings workspace."""
from contextlib import ExitStack
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from itertools import groupby
from zoneinfo import ZoneInfo

from portfolio.paths import DOMESTIC_ASSETS_DIR
from .calculate import ALIPAY_ZERO_OPENINGS
from .fund_returns import confirmed_returns, tiantian_returns
from .position_confirmation import read_confirmations
from .storage import ACCOUNT, CALC_FILE, RAW_FILE, connect

REVIEW = "待核对"
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



def confirmed_history(calc, table, cutoff, account="", fund=""):
    """Return the latest verified balance and the start of its uninterrupted zero run."""
    scope = "AND account_id=? AND fund_code=?" if account else ""
    rows = calc.execute(f"""
        SELECT account_id,fund_code,trade_date,holding_shares FROM {table}
        WHERE trade_date<=? {scope} ORDER BY account_id,fund_code,trade_date
    """, (cutoff, account, fund) if account else (cutoff,))
    history = {}
    for key, entries in groupby(rows, key=lambda row: (row["account_id"], row["fund_code"])):
        previous = None
        closed_date = None
        for row in entries:
            shares, day = row["holding_shares"], row["trade_date"]
            quantity = Decimal(shares) if shares is not None else None
            if quantity is None or quantity < 0:
                previous = None
                closed_date = None
                continue
            if quantity > 0:
                closed_date = None
            elif previous is not None and previous > 0:
                closed_date = day
            history[key] = dict(confirmed_shares=shares, confirmed_date=day, closed_date=closed_date)
            previous = quantity
    return history


def position_label(shares):
    return "无法确认" if shares is None else "已清仓" if Decimal(shares) == 0 else "持有中"


def holdings(account="", fund="", *, root=DOMESTIC_ASSETS_DIR, valuation_date=None):
    target = date.fromisoformat(valuation_date) if valuation_date else datetime.now(ZoneInfo("Asia/Shanghai")).date()
    cutoff = target.isoformat()
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
            AND fund_code<>'' AND (confirmation_date IS NULL OR confirmation_date<=?)
            ORDER BY confirmation_date,id""", (cutoff,)):
            records[(row["account_id"], row["fund_code"])] = dict(row)
        openings = {}
        for row in raw.execute("SELECT * FROM raw_fund_opening_positions ORDER BY snapshot_date"):
            key = row["account_id"], row["fund_code"]
            openings[key] = row
            records.setdefault(key, dict(row, fund_type=None))
        accounts = {r["account_id"]: dict(r) for r in raw.execute("SELECT * FROM raw_asset_accounts")}
        positions = {}
        confirmed = {}
        latest_import = raw.execute("""SELECT MAX(created_at) FROM raw_import_batches
            WHERE source_type IN ('opening_asset','transaction')""").fetchone()[0]
        if calc:
            run = calc.execute("SELECT MAX(completed_at) FROM calc_runs WHERE status='完成'").fetchone()[0]
            if run and (not latest_import or run >= latest_import):
                positions = {(r["account_id"], r["fund_code"]): r for r in calc.execute("""
                    SELECT * FROM calc_fund_positions_daily
                    WHERE trade_date=(SELECT MAX(trade_date) FROM calc_fund_positions_daily WHERE trade_date<=?)""",
                    (cutoff,))}
                confirmed = confirmed_history(calc, "calc_fund_positions_daily", cutoff, account, fund)
        metadata = ({r["fund_code"]: dict(r) for r in raw.execute("SELECT * FROM raw_fund_info")}
                    if "raw_fund_info" in tables else {})
        tiantian = {}
        tt_positions = {}
        if calc and calc.execute("SELECT 1 FROM sqlite_master WHERE name='calc_tiantian_run'").fetchone():
            generation = calc.execute("SELECT completed_at FROM calc_tiantian_run").fetchone()
            imported = raw.execute("""SELECT MAX(b.created_at) FROM raw_fund_source_files f
                JOIN raw_import_batches b ON b.id=f.id WHERE f.source_platform='TIANTIAN'""").fetchone()[0]
            if generation and (not imported or generation[0] >= imported):
                tiantian = {(r["account_id"], r["fund_code"]): dict(r)
                            for r in calc.execute("SELECT * FROM calc_tiantian_funds")}
                tt_positions = {(r["account_id"], r["fund_code"]): dict(r) for r in calc.execute("""
                    SELECT p.* FROM calc_tiantian_positions p JOIN (
                        SELECT account_id,fund_code,MAX(trade_date) AS trade_date FROM calc_tiantian_positions
                        WHERE trade_date<=? GROUP BY account_id,fund_code
                    ) latest USING(account_id,fund_code,trade_date)""", (cutoff,))}
                confirmed.update(confirmed_history(calc, "calc_tiantian_positions", cutoff, account, fund))
        tt_coverage = dict(raw.execute("""
            SELECT account_id,MAX(MIN(period_end,?)) FROM raw_fund_source_files
            WHERE source_platform='TIANTIAN' AND period_start<=? GROUP BY account_id
        """, (cutoff, cutoff))) if "raw_fund_source_files" in tables else {}
        statements = {}
        if calc and calc.execute("SELECT 1 FROM sqlite_master WHERE name='calc_tiantian_statement_positions'").fetchone():
            for row in calc.execute("""SELECT p.* FROM calc_tiantian_statement_positions p
                WHERE as_of=(SELECT MAX(s.as_of) FROM calc_tiantian_statement_positions s
                             WHERE s.account_id=p.account_id AND s.as_of<=?)""", (cutoff,)):
                key = row["account_id"], row["fund_code"]
                statements[key] = dict(row)
                records.setdefault(key, dict(row))
        confirmations = read_confirmations(calc, cutoff)
        for key, record in records.items():
            if account and key != (account, fund):
                continue
            position = positions.get(key)
            opening = openings.get(key)
            recent = tiantian.get(key)
            position = tt_positions.get(key) or position
            as_of = position["trade_date"] if position else None
            shares = position["holding_shares"] if position else None
            history = confirmed.get(key)
            statement = statements.get(key)
            snapshot_conflict = None
            if (statement and statement["as_of"] == as_of and shares is not None
                    and Decimal(shares) != Decimal(statement["holding_shares"])):
                snapshot_conflict = f"{as_of} 快照差异：重建 {shares}；对账单 {statement['holding_shares']}"
                shares = None
                if history and history["confirmed_date"] == as_of:
                    previous = (date.fromisoformat(as_of) - timedelta(days=1)).isoformat()
                    history = confirmed_history(calc, "calc_tiantian_positions", previous, *key).get(key)
            last_confirmed_shares = history["confirmed_shares"] if history else None
            last_confirmed_date = history["confirmed_date"] if history else None
            last_confirmed_status = position_label(last_confirmed_shares)
            closed_date = history["closed_date"] if history else None
            position_issue = None
            coverage_through = as_of if key[0] == ACCOUNT else tt_coverage.get(key[0])
            confidence = "已确认至查询日期"
            if as_of is None:
                confidence, position_issue = "份额异常", "无可用的份额计算结果"
            elif shares is None or Decimal(shares) < 0:
                position_issue = snapshot_conflict or (recent["issue"] if recent else None) or "份额缺失或异常"
                if "快照差异" in position_issue:
                    confidence = "快照冲突"
                elif "缺档" in position_issue:
                    confidence = "历史交易缺档"
                else:
                    confidence = "份额异常"
            elif as_of < cutoff:
                confidence = "后续交易资料待核对"
                position_issue = f"交易资料覆盖截至 {as_of}，{cutoff} 的当前份额尚未确认"
            qty = Decimal(shares) if position_issue is None else None
            current_status = position_label(qty) if qty is not None else REVIEW
            status = current_status
            if position_issue:
                if confidence == "快照冲突":
                    status = "快照冲突待核对"
                elif last_confirmed_status == "已清仓":
                    status = "历史已清仓 · 当前待核对"
                elif last_confirmed_status == "持有中":
                    status = "历史持有 · 当前待核对"
                else:
                    status = "份额待核对"
            confirmation = confirmations.get(key)
            latest_trade = record.get("confirmation_date") or ""
            # New dated transactions return control to the ledger. Historical queries
            # before the confirmation date never receive the user's current status.
            if confirmation:
                day = confirmation["confirmed_date"]
                if (latest_trade > day
                        or (latest_trade == day and record.get("id") != confirmation["latest_transaction_id"])
                        or (statement and statement["as_of"] > day and qty is not None)):
                    confirmation = None
            user_closed = bool(confirmation and confirmation["status"] == "已清仓")
            user_held = bool(confirmation and confirmation["status"] == "持有中"
                             and confidence == "后续交易资料待核对"
                             and last_confirmed_status == "持有中"
                             and latest_trade <= last_confirmed_date)
            if user_closed:
                if qty is not None and qty > 0:
                    position_issue = "用户确认已清仓；账本仍有正份额"
                    confidence = "份额异常"
                    qty = None
                if confidence == "后续交易资料待核对":
                    position_issue = None
                    confidence = "用户确认当前状态"
                status = current_status = "已清仓"
            elif user_held:
                qty = Decimal(last_confirmed_shares)
                position_issue = None
                confidence = "用户确认当前状态"
                status = current_status = "持有中"
            nav_record = next(daily_nav(raw, tables, key[1], cutoff), {})
            if statement and not nav_record and statement["unit_nav"] and statement["nav_date"] and statement["nav_date"] <= cutoff:
                nav_record = dict(unit_nav=statement["unit_nav"], nav_date=statement["nav_date"],
                                  source="天天基金对账单期末快照")
            nav_date = nav_record.get("nav_date")
            nav = positive(nav_record.get("unit_nav"))
            nav_issue = nav_record.get("nav_issue")
            nav_status = "净值有效"
            if nav_issue:
                nav_status = "净值来源冲突"
            elif nav is None:
                nav_status = "净值缺失"
            elif (target - date.fromisoformat(nav_date)).days > 7:
                nav_status = "净值尚未更新"
            if qty is not None and qty > 0 and nav_status != "净值有效":
                nav_issue = nav_issue or nav_status
                if not user_held:
                    status = "净值待更新" if nav_status == "净值尚未更新" else nav_status
            review_reason = "；".join(filter(None, (position_issue, nav_issue if qty and qty > 0 else None))) or None
            value = "0" if qty == 0 else str(qty * nav) if qty is not None and nav is not None else None
            info = metadata.get(key[1], {})
            meta = accounts.get(key[0], {})
            returns = {}
            historical_close = qty is None and last_confirmed_status == "已清仓"
            return_shares = Decimal(0) if historical_close else qty
            return_day = last_confirmed_date if historical_close else as_of
            return_value = Decimal(0) if historical_close else Decimal(value) if value is not None else None
            if (key[0] == ACCOUNT and position and return_shares is not None
                    and key[1] in ALIPAY_ZERO_OPENINGS.get(date(2021, 11, 11), ()) and not opening):
                trades = calc.execute("""SELECT t.transaction_type,t.confirmed_amount,t.status,m.signed_shares
                    FROM calc_fund_transactions t LEFT JOIN calc_fund_share_movements m
                    ON m.raw_transaction_id=t.raw_transaction_id
                    WHERE t.account_id=? AND t.fund_code=? AND
                    (t.effective_date IS NULL OR t.effective_date>? AND t.effective_date<=?)
                    ORDER BY t.effective_date,t.raw_transaction_id""", (*key, "2021-11-11", return_day))
                returns = confirmed_returns(trades, return_shares, return_value)
            elif recent and not recent["issue"] and return_shares is not None and Decimal(recent["observed_shares"]) == return_shares:
                trades = [dict(r) for r in raw.execute("""SELECT * FROM raw_fund_transactions
                    WHERE account_id=? AND fund_code=? AND source_platform='TIANTIAN'
                    AND confirmation_date<=? ORDER BY confirmation_date,transaction_time,id""", (*key, return_day))]
                returns = tiantian_returns(trades, return_shares, return_value)
            if historical_close:
                returns = ({"last_confirmed_realized_pnl": returns["realized_pnl"]} if returns else {})
            return_issue = None if "pnl" in returns else (
                "天天基金历史流水或成本基准待核对" if meta.get("platform") == "天天基金" else
                "期初成本、交易或估值尚未完整核对")
            confirmation_note = None
            if user_closed and (qty is None or return_issue):
                confirmation_note = "用户确认；历史交易或收益尚未完整核算"
            elif user_held:
                confirmation_note = f"用户确认当前持有；估值份额及成本沿用 {last_confirmed_date} 的账本结果"
            result["rows"].append(dict(**returns, return_issue=return_issue, account_id=key[0], fund_code=key[1], fund_name=info.get("fund_name") or record["fund_name"],
                fund_type=info.get("fund_type") or record.get("fund_type") or "待分类", platform=meta.get("platform", "—"),
                account_name=meta.get("account_name", "—"), shares=str(qty) if qty is not None else None,
                nav=str(nav) if nav is not None else None, nav_date=nav_date, market_value=value,
                as_of=as_of, position_as_of=as_of, coverage_through=coverage_through, valuation_date=cutoff,
                last_confirmed_shares=last_confirmed_shares, last_confirmed_date=last_confirmed_date,
                last_confirmed_status=last_confirmed_status, last_confirmed_close_date=closed_date,
                current_position_status=current_status, position_status=confidence, position_issue=position_issue,
                status=status, review_reason=review_reason, confirmation_note=confirmation_note,
                user_confirmation_date=confirmation["confirmed_date"] if confirmation else None,
                latest_transaction_id=record.get("id"),
                valuation_status="已清仓" if qty == 0 else "份额待核对" if qty is None else
                nav_status if nav_status != "净值有效" else "已估值",
                issue=review_reason, accumulated_nav=nav_record.get("accumulated_nav"),
                daily_return_rate=nav_record.get("daily_return_rate"),
                nav_source=nav_record.get("source"), nav_status=nav_status,
                nav_field_status=nav_record.get("field_status"),
                observed_shares=recent["observed_shares"] if recent and recent["issue"] else None))
        if account and result["rows"]:
            result["transactions"] = [dict(r) for r in raw.execute(
                f"SELECT * FROM ({source}) WHERE account_id=? AND fund_code=? AND (confirmation_date IS NULL OR confirmation_date<=?) ORDER BY confirmation_date DESC,id",
                (account, fund, cutoff))]
            if (account, fund) in positions:
                totals = {"BUY": Decimal(0), "SELL": Decimal(0)}
                for move in calc.execute("""SELECT m.movement_type,m.signed_shares
                    FROM calc_fund_share_movements m JOIN calc_fund_transactions t
                    ON t.raw_transaction_id=m.raw_transaction_id
                    WHERE t.account_id=? AND m.fund_code=? AND m.effective_date<=? AND m.movement_type IN ('BUY','SELL')""",
                    (account, fund, cutoff)):
                    totals[move[0]] += abs(Decimal(move[1]))
                result["rows"][0].update(buy_shares=str(totals["BUY"]), sell_shares=str(totals["SELL"]))
            recent = tiantian.get((account, fund))
            if recent:
                if not recent["issue"] and cutoff >= recent["as_of"]:
                    result["rows"][0].update(buy_shares=recent["buy_shares"], sell_shares=recent["sell_shares"],
                                             cash_dividend=recent["cash_dividend"])
                table = "calc_tiantian_positions"
            elif (account, fund) in positions:
                table = "calc_fund_positions_daily"
            else:
                table = None
            if table:
                snapshots = {r["trade_date"]: r["holding_shares"] for r in calc.execute(
                    f"SELECT trade_date,holding_shares FROM {table} WHERE account_id=? AND fund_code=? AND trade_date<=?",
                    (account, fund, cutoff))}
                history = {}
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
    return result
