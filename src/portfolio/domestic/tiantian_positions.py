"""Rebuild account-scoped TianTian share history from confirmed statement records."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext

from .alipay import record_id
from .calculate import split_quantity
from .storage import CALC_FILE, RAW_FILE, connect, now

VERSION = "4"
BUY = {"买基金", "定时定额投资", "认购结果"}
SELL = {"卖基金", "卖基金回活期宝", "活期宝转入—赎回", "活期宝即充即用", "活期宝即充即用调减", "快速取现", "普通取现"}
INCREASE = {"份额调增", "超级转换-转入", "超级转换份额调增", "卖基金充值", "红利再投资"}
DECREASE = {"超级转换-转出", "超级转换份额调减", "强减"}
NO_MOVEMENT = {"修改分红方式", "跨分账户份额转卡", "现金分红"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS calc_tiantian_no_trade_periods (
 account_id TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 PRIMARY KEY(account_id,start_date,end_date)
);
CREATE TABLE IF NOT EXISTS calc_tiantian_positions (
 account_id TEXT NOT NULL, fund_code TEXT NOT NULL, trade_date TEXT NOT NULL,
 holding_shares TEXT, PRIMARY KEY(account_id,fund_code,trade_date)
);
CREATE TABLE IF NOT EXISTS calc_tiantian_funds (
 account_id TEXT NOT NULL, fund_code TEXT NOT NULL, as_of TEXT NOT NULL,
 observed_shares TEXT, issue TEXT, buy_shares TEXT, sell_shares TEXT, cash_dividend TEXT,
 PRIMARY KEY(account_id,fund_code)
);
CREATE TABLE IF NOT EXISTS calc_tiantian_issues (
 account_id TEXT NOT NULL, fund_code TEXT NOT NULL, raw_id TEXT, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calc_tiantian_run (
 id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL, raw_version TEXT NOT NULL, completed_at TEXT NOT NULL
);
"""


def subscription_key(row):
    return tuple(row[k] for k in ("confirmation_date", "confirmed_shares", "confirmed_amount",
                                  "fee", "confirmed_nav", "linked_bank_card"))


def movement(row, confirmations):
    kind = row["transaction_type_raw"]
    try:
        shares = Decimal(row["confirmed_shares"]) if row["confirmed_shares"] is not None else None
    except InvalidOperation:
        return None
    if shares is not None and not shares.is_finite():
        return None
    if row["confirmation_status"] != "成功" and kind not in {"红利再投资", "现金分红"}:
        return None
    # The original statement has explicit confirmed deductions even when the importer flags the category.
    if row["parse_status"] != "正常" and kind != "强减":
        return None
    if kind in NO_MOVEMENT:
        return Decimal(0)
    if kind == "买新基金":
        key = subscription_key(row)
        if shares == 0:
            return Decimal(0)
        if confirmations[key]:
            confirmations[key] -= 1
            return Decimal(0)
        return None
    if shares is None or shares <= 0:
        return None
    if kind in BUY | INCREASE:
        return shares
    if kind in SELL | DECREASE:
        return -shares
    return None


def replay(rows, periods, splits, as_of, *, establishment=None, anchors=None):
    """Retain the observed balance after a gap, without presenting it as a confirmed position."""
    confirmed = Counter(subscription_key(r) for r in rows if r["transaction_type_raw"] == "认购结果"
                        and r["confirmation_status"] == "成功" and r["parse_status"] == "正常")
    daily = defaultdict(list)
    issues = []
    for row in rows:
        day = row["confirmation_date"]
        delta = movement(row, confirmed)
        if not day or delta is None:
            issues.append((row["id"], f"{day or '日期缺失'} {row['transaction_type_raw']} 待核对"))
        if day:
            daily[date.fromisoformat(day)].append((row, delta))
    anchors = anchors or {}
    first = min((*daily, *anchors), default=as_of)
    preceding = sorted((date.fromisoformat(start), date.fromisoformat(end)) for start, end in periods)
    latest_period = next(((start, end) for start, end in reversed(preceding) if start <= first <= end), None)
    if latest_period and latest_period == preceding[-1]:
        previous_end = max((end for _, end in preceding if end < latest_period[0]), default=None)
        if previous_end and previous_end + timedelta(days=1) < latest_period[0]:
            first_possible = max(previous_end + timedelta(days=1), establishment or date.min)
            if first_possible < latest_period[0]:
                issues.append((None, f"首笔已导入交易前缺档 {first_possible} 至 {latest_period[0] - timedelta(days=1)}"))
    covered = set()
    for start, end in periods:
        start, end = max(first, date.fromisoformat(start)), min(as_of, date.fromisoformat(end))
        covered.update(start + timedelta(days=i) for i in range((end - start).days + 1))
    current = first
    quantity = buy = sell = dividend = Decimal(0)
    reliable = bool(rows) and not any(raw_id is None or "日期缺失" in detail for raw_id, detail in issues)
    historical = []
    uncovered = []
    gap_since_anchor = not rows or any(raw_id is None for raw_id, _ in issues)
    while current <= as_of:
        trades = daily.get(current, ())
        if current not in covered:
            uncovered.append(current)
            reliable = False
            gap_since_anchor = True
        if current.isoformat() in splits:
            adjustments = [delta for r, delta in trades
                           if r["transaction_type_raw"] in {"强减", "份额调增"}]
            adjusted = split_quantity(quantity, splits[current.isoformat()],
                                      sum(adjustments, Decimal(0)) if adjustments and None not in adjustments else None)
            if adjusted is None or None in adjustments:
                reliable = False
                issues.append((None, f"{current} 拆分比例或账户份额调整待核对"))
            else:
                quantity = adjusted
        for row, delta in trades:
            if delta is None:
                reliable = False
                continue
            quantity += delta
            kind = row["transaction_type_raw"]
            if kind in BUY:
                buy += delta
            elif kind in SELL:
                sell -= delta
            elif kind == "现金分红" and row["confirmed_amount"] is not None:
                dividend += Decimal(row["confirmed_amount"])
        if quantity < 0 and reliable:
            reliable = False
            issues.append((None, f"{current} 观察到负份额，期初或历史交易待核对"))
        if current in anchors:
            stated = anchors[current]
            if quantity != stated:
                issues.append((None, f"{current} 快照差异：重建 {quantity}；对账单 {stated}；差异 {stated - quantity}"))
                if reliable:
                    reliable = False
                elif gap_since_anchor:
                    quantity, reliable = stated, True
            elif not reliable and gap_since_anchor:
                quantity, reliable = stated, True
            gap_since_anchor = False
        historical.append((current.isoformat(), format(quantity, "f") if reliable else None))
        current += timedelta(days=1)
    if uncovered:
        # Group disjoint ranges so the user can identify which statements are missing.
        start = previous = uncovered[0]
        for day in [*uncovered[1:], None]:
            if day is None or day != previous + timedelta(days=1):
                issues.append((None, f"对账单缺档 {start} 至 {previous}"))
                start = day
            previous = day
    return historical, quantity, buy, sell, dividend, issues


def rebuild_tiantian(root):
    with connect(root / RAW_FILE, readonly=True) as raw:
        raw.execute("BEGIN")
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "raw_fund_transactions" not in tables:
            return {}
        rows = [dict(r) for r in raw.execute("""SELECT * FROM raw_fund_transactions
            WHERE source_platform='TIANTIAN' ORDER BY confirmation_date,transaction_time,id""")]
        if not rows:
            return {}
        periods = defaultdict(list)
        for account, start, end in raw.execute("""SELECT account_id,period_start,period_end FROM raw_fund_source_files
            WHERE source_platform='TIANTIAN' AND period_start IS NOT NULL AND period_end IS NOT NULL"""):
            periods[account].append((start, end))
        split_rows = [tuple(r) for r in raw.execute("""SELECT DISTINCT fund_code,event_date,conversion_ratio
            FROM raw_fund_corporate_actions WHERE event_type='SPLIT' AND conversion_ratio IS NOT NULL""")
        ] if "raw_fund_corporate_actions" in tables else []
        established = dict(raw.execute("SELECT fund_code,establishment_date FROM raw_fund_info")) if "raw_fund_info" in tables else {}

    groups = defaultdict(list)
    splits = defaultdict(lambda: defaultdict(list))
    for code, day, ratio in split_rows:
        splits[code][day].append(ratio)
    for row in rows:
        groups[row["account_id"], row["fund_code"]].append(row)
    with connect(root / CALC_FILE) as db, localcontext() as precision:
        precision.prec = 60
        db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
        for account, start, end in db.execute("SELECT * FROM calc_tiantian_no_trade_periods"):
            if not any(r["account_id"] == account and start <= (r["confirmation_date"] or "") <= end for r in rows):
                periods[account].append((start, end))
        snapshot_rows = [tuple(r) for r in db.execute("""SELECT account_id,fund_code,as_of,holding_shares
            FROM calc_tiantian_statement_positions ORDER BY 1,2,3""")] if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='calc_tiantian_statement_positions'").fetchone() else []
        anchors = defaultdict(dict)
        for account, fund, day, shares in snapshot_rows:
            anchors[account, fund][date.fromisoformat(day)] = Decimal(shares)
            groups.setdefault((account, fund), [])
        version = record_id(rows, dict(periods), split_rows, established, snapshot_rows)
        if db.execute("SELECT 1 FROM calc_tiantian_run WHERE version=? AND raw_version=?", (VERSION, version)).fetchone():
            return {"status": "unchanged"}
        for table in ("calc_tiantian_positions", "calc_tiantian_funds", "calc_tiantian_issues"):
            db.execute(f"DELETE FROM {table}")
        for (account, fund), trades in groups.items():
            ends = [date.fromisoformat(end) for _, end in periods[account]]
            ends.extend(date.fromisoformat(r["confirmation_date"]) for r in trades if r["confirmation_date"])
            if not fund or not ends:
                db.executemany("INSERT INTO calc_tiantian_issues VALUES (?,?,?,?)",
                               ((account, fund or "", r["id"], "基金代码或确认日期缺失") for r in trades))
                continue
            last = max((*ends, *anchors[account, fund]))
            inception = date.fromisoformat(established[fund]) if established.get(fund) else None
            history, observed, buy, sell, dividend, issues = replay(
                trades, periods[account], splits[fund], last, establishment=inception, anchors=anchors[account, fund])
            db.executemany("INSERT INTO calc_tiantian_positions VALUES (?,?,?,?)",
                           ((account, fund, day, shares) for day, shares in history))
            issue = "；".join(dict.fromkeys(detail for _, detail in issues)) or None
            db.execute("INSERT INTO calc_tiantian_funds VALUES (?,?,?,?,?,?,?,?)",
                       (account, fund, last.isoformat(), str(observed), issue, str(buy), str(sell), str(dividend)))
            db.executemany("INSERT INTO calc_tiantian_issues VALUES (?,?,?,?)",
                           ((account, fund, raw_id, detail) for raw_id, detail in issues))
        db.execute("INSERT OR REPLACE INTO calc_tiantian_run VALUES (1,?,?,?)", (VERSION, version, now()))
    return {"status": "complete", "funds": len(groups)}
