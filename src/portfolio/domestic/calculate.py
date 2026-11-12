"""Rebuild confirmed-share history from the independent raw ledger."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from itertools import groupby
from pathlib import Path
from uuid import uuid4

from .alipay import record_id
from .storage import ACCOUNT, CALC_FILE, CALC_SCHEMA, RAW_FILE, connect, insert, now

VERSION = "3"
ZERO = Decimal(0)
# 用户确认这三只支付宝基金在该期初日尚未持有。
ALIPAY_ZERO_OPENINGS = {date(2021, 11, 11): frozenset({"002611", "012414", "019172"})}


def amount(value):
    return Decimal(value) if value is not None else None


def exact(value):
    return format(value, "f") if value is not None else None


def match_transfers(rows):
    grouped = defaultdict(list)
    for row in rows:
        if row["transaction_type"] == "用户跨TA转换":
            grouped[row["account_id"], row["order_id"]].append(row)
    pairs = {}
    for (account, order), candidates in grouped.items():
        if len(candidates) != 2:
            continue
        outgoing = [row for row in candidates if amount(row["application_shares"]) not in (None, ZERO)]
        incoming = [row for row in candidates if row["application_shares"] is None]
        if len(outgoing) != 1 or len(incoming) != 1:
            continue
        sell, buy = outgoing[0], incoming[0]
        if (sell["fund_code"] == buy["fund_code"] or sell["transaction_time"] != buy["transaction_time"]
                or sell["confirmation_date"] != buy["confirmation_date"]
                or any(row["parse_status"] != "正常" or (amount(row["confirmed_shares"]) or ZERO) <= 0
                       for row in candidates)):
            continue
        group_id = record_id(account, order)
        pairs[sell["id"]] = ("TRANSFER_OUT", group_id, sell, buy)
        pairs[buy["id"]] = ("TRANSFER_IN", group_id, sell, buy)
    return pairs


def classify(row, pair):
    if row["parse_status"] != "正常":
        return "UNKNOWN", None, None
    kind = row["transaction_type"]
    shares = amount(row["confirmed_shares"])
    if kind in ("用户买入", "定投买入") and shares is not None and shares > 0:
        return "BUY", shares, "申购"
    if kind == "用户卖出" and shares is not None and shares > 0:
        return "SELL", -shares, "赎回"
    if kind == "用户跨TA转换" and pair:
        return pair[0], shares if pair[0] == "TRANSFER_IN" else -shares, "无外部现金流"
    if kind in ("机构分红", "红利再投资") and shares is not None and shares > 0:
        return "REINVEST", shares, "无外部现金流"
    if kind in ("机构分红", "现金分红") and shares in (None, ZERO) and amount(row["confirmed_amount"]):
        return "CASH_DIVIDEND", ZERO, "分红"
    return "UNKNOWN", None, None


ALIPAY_COVERAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS calc_alipay_statement_coverage (
    file_sha256 TEXT PRIMARY KEY, period_start TEXT NOT NULL, period_end TEXT NOT NULL
);
"""


def split_quantity(quantity, ratios, reported_delta=None):
    if quantity is None or not quantity:
        return quantity
    try:
        if len(ratios) != 1:
            return None
        left, right = map(Decimal, ratios[0].replace("：", ":").split(":"))
        if min(left, right) <= 0:
            return None
        expected = quantity * right / left
        if reported_delta is not None:
            # An exactly reconciled account adjustment will be applied in the ledger loop.
            return quantity if quantity + reported_delta == expected else None
        return expected
    except (InvalidOperation, ValueError):
        return None


def read_raw(root):
    with connect(root / RAW_FILE, readonly=True) as raw:
        raw.execute("BEGIN")
        openings = [dict(row) for row in raw.execute("SELECT * FROM raw_fund_opening_positions ORDER BY snapshot_date")]
        trades = [dict(row) for row in raw.execute("SELECT * FROM raw_alipay_fund_transactions ORDER BY confirmation_date, transaction_time, id")]
        nav = [dict(row) for row in raw.execute("SELECT * FROM raw_fund_nav ORDER BY nav_date, id")]
        batches = [tuple(row) for row in raw.execute("SELECT source_type,file_sha256 FROM raw_import_batches WHERE source_type IN ('opening_asset','transaction') ORDER BY 1,2")]
    return openings, trades, nav, batches


def rebuild(root, *, full=False):
    root = Path(root)
    openings, trades, nav, batches = read_raw(root)
    if not openings:
        raise ValueError("请先导入期初资产证明")
    manifest = {row["id"]: (row["fund_code"], record_id(row)) for row in (*openings, *trades, *nav)}
    with connect(root / RAW_FILE, readonly=True) as raw:
        action_table = raw.execute("SELECT 1 FROM sqlite_master WHERE name='raw_fund_corporate_actions'").fetchone()
        splits = [tuple(row) for row in raw.execute("""SELECT DISTINCT fund_code,event_date,conversion_ratio
            FROM raw_fund_corporate_actions WHERE event_type='SPLIT' AND conversion_ratio IS NOT NULL
            ORDER BY 1,2,3""")] if action_table else []
    for code, day, ratio in splits:
        manifest[record_id("split", code, day, ratio)] = (code, record_id(code, day, ratio))
    started = now()
    run = uuid4().hex
    pairs = match_transfers(trades)
    with connect(root / CALC_FILE) as db:
        db.executescript(CALC_SCHEMA + ALIPAY_COVERAGE_SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        digests = {digest for kind, digest in batches if kind == "transaction"}
        periods = [(start, end) for digest, start, end in db.execute(
            "SELECT * FROM calc_alipay_statement_coverage ORDER BY period_start") if digest in digests]
        version = record_id(batches, sorted(manifest.items()), periods)
        previous = db.execute("SELECT raw_version,calculation_version FROM calc_runs WHERE status='完成' ORDER BY completed_at DESC LIMIT 1").fetchone()
        if not full and previous and tuple(previous) == (version, VERSION):
            return {"status": "unchanged"}
        effective_dates = [row["confirmation_date"] for row in trades if row["confirmation_date"]]
        first = date.fromisoformat(openings[0]["snapshot_date"])
        last = max(first, date.fromisoformat(max(effective_dates))) if effective_dates else first
        if periods:
            last = first
        for start, end in periods:
            if date.fromisoformat(start) <= last + timedelta(days=1):
                last = max(last, date.fromisoformat(end))
        old = {row[0]: (row[1], row[2]) for row in db.execute("SELECT * FROM calc_raw_manifest")}
        all_funds = {value[0] for value in (*old.values(), *manifest.values())}
        changed_ids = {key for key in old.keys() | manifest.keys() if old.get(key) != manifest.get(key)}
        changed = {entries[key][0] for entries in (old, manifest) for key in changed_ids & entries.keys()}
        # New horizon affects every position. A change to a conversion may affect its counterparty.
        previous_horizon = db.execute("SELECT MIN(trade_date), MAX(trade_date) FROM calc_fund_positions_daily").fetchone()
        old_pairs = db.execute("SELECT outgoing_raw_id,incoming_raw_id FROM calc_fund_transfer_matches").fetchall()
        for outgoing, incoming in [*old_pairs, *((p[2]["id"], p[3]["id"]) for p in pairs.values())]:
            funds = {entry[0] for key in (outgoing, incoming)
                     if (entry := (manifest.get(key) or old.get(key)))}
            if funds & changed:
                changed.update(funds)
        full = full or not previous or previous[1] != VERSION
        if full or tuple(previous_horizon) != (first.isoformat(), last.isoformat()):
            changed = all_funds
        checks = []
        with localcontext() as context:
            context.prec = 60
            for fund in sorted(changed):
                rebuild_fund(db, fund, openings, trades, nav, pairs, first, last, checks, started, splits=splits)
        db.execute("DELETE FROM calc_fund_transfer_matches")
        db.executemany("INSERT INTO calc_fund_transfer_matches VALUES (?,?,?,?,?)", (
            (group, sell["order_id"], sell["id"], buy["id"], sell["confirmation_date"])
            for kind, group, sell, buy in pairs.values() if kind == "TRANSFER_OUT"
        ))
        db.execute("DELETE FROM calc_raw_manifest")
        db.executemany("INSERT INTO calc_raw_manifest VALUES (?,?,?)", ((key, *value) for key, value in manifest.items()))
        insert(db, "calc_runs", dict(id=run, calculation_type="全量" if full else "增量", is_full=int(full),
                                     raw_version=version, calculation_version=VERSION, started_at=started,
                                     completed_at=now(), status="完成", error=None))
        # Reuse unchanged per-fund checks from the previous successful generation.
        latest = db.execute("SELECT id FROM calc_runs WHERE status='完成' AND id<>? ORDER BY completed_at DESC LIMIT 1", (run,)).fetchone()
        if latest:
            for row in db.execute("SELECT check_code,fund_code,detail FROM calc_data_quality_checks WHERE run_id=?", (latest[0],)):
                if row[1] and row[1] not in changed:
                    checks.append(tuple(row))
        checks.append(("OPENING_BOUNDARY", None, str(sum(day == first.isoformat() for day in effective_dates))))
        db.executemany("INSERT INTO calc_data_quality_checks(run_id,check_code,fund_code,detail) VALUES (?,?,?,?)",
                       ((run, *item) for item in checks))
        return {"status": "complete", "funds_rebuilt": len(changed), "quality_checks": len(checks),
                "transfers": len(pairs) // 2, "through": last.isoformat()}


def rebuild_fund(db, fund, openings, trades, nav, pairs, first, last, checks, calculated, *, splits=()):
    for table in ("calc_fund_master", "calc_fund_transactions", "calc_fund_share_movements",
                  "calc_fund_positions_daily", "calc_fund_positions_monthly", "calc_fund_monthly_summary"):
        db.execute(f"DELETE FROM {table} WHERE fund_code=?", (fund,))
    fund_trades = [row for row in trades if row["fund_code"] == fund]
    snapshots = [row for row in openings if row["fund_code"] == fund]
    if not fund_trades and not snapshots:
        return
    opening = next((row for row in snapshots if row["snapshot_date"] == first.isoformat()), None)
    quantity = amount(opening["total_shares"]) if opening else None
    if quantity is None and fund in ALIPAY_ZERO_OPENINGS.get(first, ()):
        quantity = ZERO
    if fund and quantity is None:
        checks.append(("MISSING_OPENING", fund, "基金无期初证明份额"))
    dates = [row["transaction_time"][:10] for row in fund_trades if row["transaction_time"]]
    name = (fund_trades[-1]["fund_name"] if fund_trades else snapshots[0]["fund_name"])
    if fund:
        db.execute("INSERT INTO calc_fund_master VALUES (?, ?, NULL, 'CNY', ?, ?)",
                   (fund, name, min(dates, default=None), max(dates, default=None)))
    movements = defaultdict(list)
    for row in fund_trades:
        pair = pairs.get(row["id"])
        kind, signed, cash = classify(row, pair)
        day = row["confirmation_date"]
        db.execute("INSERT INTO calc_fund_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            row["id"], row["id"], row["account_id"], fund, row["transaction_time"][:10] if row["transaction_time"] else None,
            day, kind, row["confirmed_amount"], row["confirmed_shares"], row["fee"], cash,
            "正常" if signed is not None and day else "待核对",
        ))
        if signed is None or day is None:
            checks.append(("UNMATCHED_CONVERSION" if row["transaction_type"] == "用户跨TA转换" else "INVALID_TRANSACTION",
                           fund, row["id"]))
            if day is None:
                quantity = None
        else:
            db.execute("INSERT INTO calc_fund_share_movements VALUES (?,?,?,?,?,?,?)", (
                row["id"], row["id"], fund, day, kind, exact(signed), pair[1] if pair else None,
            ))
        if day and day > first.isoformat():
            movements[day].append((kind, signed, row))
    if not fund:
        return
    nav_by_date = defaultdict(set)
    for row in (*snapshots, *(r for r in nav if r["fund_code"] == fund)):
        nav_by_date[row.get("nav_date") or row["unit_nav_date"]].add(amount(row["unit_nav"]))
    split_dates = defaultdict(list)
    for code, day, ratio in splits:
        if code == fund and day > first.isoformat():
            split_dates[day].append(ratio)
    negative = None
    start_quantity = quantity
    amounts_by_type = {"BUY": (0, 2), "SELL": (1, 3)}
    for month, days in groupby((first + timedelta(days=offset) for offset in range((last-first).days+1)),
                               key=lambda day: day.isoformat()[:7]):
        totals = [ZERO] * 7
        for day in days:
            key = day.isoformat()
            if key in split_dates:
                before = quantity
                quantity = split_quantity(quantity, split_dates[key])
                if before is not None and quantity is None:
                    checks.append(("SPLIT_REVIEW", fund, key))
            for kind, signed, row in movements.get(key, ()):
                quantity = quantity + signed if quantity is not None and signed is not None else None
                if kind == "UNKNOWN":
                    totals = [None] * 7
                    continue
                increments = [(6, amount(row["fee"]))]
                if kind in amounts_by_type:
                    money_col, shares_col = amounts_by_type[kind]
                    increments.extend(((money_col, amount(row["confirmed_amount"])), (shares_col, abs(signed))))
                elif kind in ("TRANSFER_IN", "TRANSFER_OUT"):
                    increments.append((4 if kind == "TRANSFER_IN" else 5, abs(signed)))
                for index, value in increments:
                    totals[index] = totals[index] + value if totals[index] is not None and value is not None else None
            if negative is None and quantity is not None and quantity < 0:
                negative = key
            db.execute("INSERT INTO calc_fund_positions_daily VALUES (?,?,?,?,?,?)",
                       (key, ACCOUNT, fund, exact(quantity), VERSION, calculated))
        is_month_end = (day + timedelta(days=1)).month != day.month
        prices = nav_by_date[key] if is_month_end else set()
        price = next(iter(prices)) if len(prices) == 1 else None
        value = quantity * price if price is not None and quantity is not None and quantity >= 0 else None
        state = ("期间未结束" if not is_month_end else "持仓待核对" if quantity is None or quantity < 0 else
                 "缺少净值" if price is None else "已估值")
        db.execute("INSERT INTO calc_fund_positions_monthly VALUES (?,?,?,?,?,?,?,?)",
                   (month, ACCOUNT, fund, exact(quantity), exact(price), key if price is not None else None, exact(value), state))
        db.execute("INSERT INTO calc_fund_monthly_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (month, fund, exact(start_quantity), *(exact(v) for v in totals), exact(quantity), exact(value)))
        start_quantity = quantity
    if negative:
        checks.append(("NEGATIVE_POSITION", fund, negative))
