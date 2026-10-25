"""订单费用及人员份额，金额均为1e-8定点整数；不修改成交/收益金额。"""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
import json

VERSION = "fees-2026-07-01-v1"
BBAE_START = "2026-07-01"
SCALE = Decimal(100_000_000)
TRADE_FIELDS = (
    "account_id",
    "instrument_id",
    "broker_order_id",
    "side",
    "business_date",
    "book_time",
    "quantity_e8",
    "price_e8",
    "calculation_amount_e8",
    "currency",
    "record_state",
    "accounting_role",
)
OP_FIELDS = (
    "person_id",
    "account_id",
    "instrument_id",
    "broker_trade_id",
    "operation_kind",
    "side",
    "business_date",
    "book_time",
    "quantity_e8",
    "amount_e8",
    "currency",
)


def now():
    return datetime.now(timezone.utc).isoformat()


def e8(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("费用或成交数量不是有效非负数")
    return int((amount * SCALE).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def fingerprint(row, fields=TRADE_FIELDS):
    return json.dumps([row[k] for k in fields], separators=(",", ":"), ensure_ascii=False)


def split_amount(total, weighted):
    """Largest remainders, stable ID tie-break; exact sum even for one-third allocations."""
    denominator = sum(q for _, q in weighted)
    if total < 0 or denominator <= 0 or any(q <= 0 for _, q in weighted):
        raise ValueError("费用分摊数量无效")
    result = {key: total * q // denominator for key, q in weighted}
    remaining = total - sum(result.values())
    ranked = sorted(weighted, key=lambda pair: (-(total * pair[1] % denominator), pair[0]))
    for key, _ in ranked[:remaining]:
        result[key] += 1
    return result


def ensure_schema(c):
    # Optional additive tables keep old read-only databases compatible.
    c.execute("""CREATE TABLE IF NOT EXISTS order_fee_evidence (
        broker_code TEXT NOT NULL, account_id INTEGER NOT NULL REFERENCES broker_accounts(id),
        provider_order_id TEXT NOT NULL, payload_json TEXT NOT NULL, checked_at TEXT NOT NULL,
        PRIMARY KEY(broker_code,account_id,provider_order_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS broker_fee_records (
        broker_trade_id INTEGER PRIMARY KEY REFERENCES broker_trades(id),
        fees_e8 INTEGER CHECK(fees_e8>=0), source TEXT NOT NULL, status TEXT NOT NULL,
        fingerprint TEXT NOT NULL, provider_order_id TEXT, checked_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS person_trade_fees (
        operation_id INTEGER PRIMARY KEY REFERENCES person_operations(id),
        fees_e8 INTEGER CHECK(fees_e8>=0), broker_fee_e8 INTEGER, source TEXT NOT NULL,
        status TEXT NOT NULL, person_id INTEGER, account_id INTEGER, instrument_id INTEGER,
        broker_trade_id INTEGER, operation_kind TEXT, side TEXT, business_date TEXT, book_time TEXT,
        quantity_e8 INTEGER, amount_e8 INTEGER, currency TEXT, checked_at TEXT NOT NULL)""")


def trades(c):
    return [
        dict(r)
        for r in c.execute("""SELECT t.*,a.broker_code,i.symbol FROM broker_trades t
        JOIN broker_accounts a ON a.id=t.account_id JOIN instruments i ON i.id=t.instrument_id
        WHERE t.record_state='ACTIVE' AND t.accounting_role='ACCOUNT_LEDGER' ORDER BY t.id""")
    ]


def load_evidence(c):
    exists = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='order_fee_evidence'"
    ).fetchone()
    if not exists:
        return {}
    return {
        (r["broker_code"], r["account_id"], r["provider_order_id"]): json.loads(r["payload_json"])
        for r in c.execute("SELECT * FROM order_fee_evidence")
    }


def store_evidence(c, evidence):
    from portfolio.sync.sync_and_split_operations import _ticker

    ensure_schema(c)
    for (broker, account, identity), incoming in evidence.items():
        saved = c.execute(
            "SELECT payload_json FROM order_fee_evidence WHERE broker_code=? AND account_id=? AND provider_order_id=?",
            (broker, account, identity),
        ).fetchone()
        data = incoming
        if saved:
            data = json.loads(saved[0])
            if any(
                data.get(k) != incoming.get(k) for k in ("fees_e8", "currency", "quantity_e8", "side")
            ) or _ticker(data["symbol"]) != _ticker(incoming["symbol"]):
                raise ValueError("已确认的订单费用证据发生冲突；保留原值，需人工核对")
            for field in ("matches", "clock_evidence"):
                if field in incoming:
                    data.setdefault(field, {}).update(incoming[field])
            for field in ("updated_booktime", "executed_price", "api_symbol"):
                if field in incoming:
                    data.setdefault(field, incoming[field])
        c.execute(
            "INSERT OR REPLACE INTO order_fee_evidence VALUES (?,?,?,?,?)",
            (broker, account, identity, json.dumps(data, ensure_ascii=False), now()),
        )


def legacy_utc_clock(row):
    """A candidate for fee matching only; never an override of the source ledger clock."""
    from zoneinfo import ZoneInfo

    if (
        row["broker_code"] != "LONGBRIDGE"
        or row["record_granularity"] != "SOURCE_RECORD"
        or row["business_date"] >= "2026-07-01"
    ):
        return None
    stamp = datetime.fromisoformat(row["book_time"])
    if stamp.tzinfo is not None or stamp.time() == datetime.min.time():
        return None
    zone = ZoneInfo("America/New_York")
    first = stamp.replace(tzinfo=zone, fold=0)
    second = stamp.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        return None  # Ambiguous or nonexistent DST wall clock needs explicit evidence.
    return first.astimezone(timezone.utc).replace(tzinfo=None)


def _schwab_fees(activity):
    """Explicit currency fee legs from Schwab transaction API; no net-vs-gross guesses."""
    items = activity.get("transferItems")
    if not isinstance(items, list) or not items:
        raise ValueError("嘉信未返回费用明细")
    total = 0
    fee_items = []
    for item in items:
        if not item.get("feeType"):
            continue
        instrument = item.get("instrument") or {}
        if instrument.get("assetType") != "CURRENCY" or instrument.get("symbol") not in {
            "CURRENCY_USD",
            "USD",
        }:
            raise ValueError("嘉信费用币种不明确")
        if item.get("cost") is None:
            raise ValueError("嘉信费用金额缺失")
        # API fee debits may be negative. An explicit zero is valid evidence.
        value = e8(abs(Decimal(str(item["cost"]))))
        total += value
        fee_items.append(item)
    if not fee_items:
        raise ValueError("嘉信未返回任何费用项目，不能默认零费")
    return total, fee_items


def schwab_evidence(c, archive):
    from portfolio.sync.schwab_api import equity_symbol

    accounts = [
        r[0] for r in c.execute("SELECT id FROM broker_accounts WHERE broker_code='SCHWAB' AND enabled=1")
    ]
    if len(accounts) != 1:
        raise ValueError("嘉信账户范围不唯一")
    output = {}
    for activity in archive.get("transactions", []):
        if activity.get("type") != "TRADE" or activity.get("status") not in {"VALID", "POSTED", "SETTLED"}:
            continue
        securities = [
            i
            for i in activity.get("transferItems", [])
            if (i.get("instrument") or {}).get("assetType") not in {None, "CURRENCY"}
        ]
        if len(securities) != 1:
            continue
        item = securities[0]
        identity = "SCHWAB:" + str(activity["activityId"])
        try:
            total, fee_items = _schwab_fees(activity)
            output[("SCHWAB", accounts[0], identity)] = dict(
                fees_e8=total,
                currency="USD",
                quantity_e8=e8(abs(Decimal(str(item["amount"])))),
                symbol=equity_symbol(item["instrument"]),
                side="BUY" if Decimal(str(item["amount"])) > 0 else "SELL",
                provider_order_id=str(activity.get("orderId", "")),
                activity_id=str(activity["activityId"]),
                source="SCHWAB_TRANSACTION_API",
                fee_items=fee_items,
            )
        except (ValueError, KeyError):
            continue
    return output


def longbridge_evidence(c, orders, matches):
    from portfolio.sync.sync_and_split_operations import _ticker

    local_trades = {r["id"]: r for r in trades(c) if r["broker_code"] == "LONGBRIDGE"}
    accounts = {tid: r["account_id"] for tid, r in local_trades.items()}
    output = {}
    for trade_id, order_id in matches.items():
        if int(trade_id) not in accounts:
            continue
        detail = orders.get(str(order_id), {})
        charge = detail.get("charge_detail")
        if not charge or charge.get("currency") != "USD" or charge.get("total_amount") is None:
            continue
        data = dict(
            fees_e8=e8(charge["total_amount"]),
            currency="USD",
            quantity_e8=e8(detail["executed_quantity"]),
            symbol=_ticker(detail["symbol"]),
            api_symbol=detail["symbol"],
            side=detail["side"].split(".")[-1].upper(),
            source="LONGBRIDGE_ORDER_DETAIL_API",
            provider_order_id=str(order_id),
            charge_detail=charge,
            updated_booktime=detail.get("updated_booktime"),
            executed_price=detail.get("executed_price"),
            # Legacy rows lack IDs: bind matches to the exact imported trade snapshot.
            matches={},
        )
        key = ("LONGBRIDGE", accounts[int(trade_id)], str(order_id))
        if key in output:
            data = output[key]
        row = local_trades[int(trade_id)]
        data["matches"][str(trade_id)] = fingerprint(row)
        candidate = legacy_utc_clock(row)
        if (
            candidate is not None
            and detail.get("updated_booktime") not in {None, "NaT"}
            and datetime.fromisoformat(detail["updated_booktime"]) == candidate
        ):
            data.setdefault("clock_evidence", {})[str(trade_id)] = dict(
                source_clock="America/New_York",
                source_book_time=row["book_time"],
                api_utc=candidate.replace(tzinfo=timezone.utc).isoformat(),
                basis="exact UTC timestamp, symbol, side, executed quantity and price; fee binding only",
            )
        output[key] = data
    return output


def refresh_fees(c):
    from portfolio.sync.sync_and_split_operations import _ticker

    ensure_schema(c)
    rows = trades(c)
    evidence = load_evidence(c)
    groups = defaultdict(list)
    fees = {}
    sources = {}
    statuses = {}
    identities = {}
    matched_evidence = {}
    for (broker, account, identity), data in evidence.items():
        for identifier, signature in data.get("matches", {}).items():
            matched_evidence[int(identifier)] = (broker, account, identity, signature)
    for row in rows:
        tid = row["id"]
        broker = row["broker_code"]
        identity = row["broker_order_id"]
        if broker == "FIRSTRADE":
            fees[tid] = 0
            sources[tid] = "RULE:FIRSTRADE_ZERO"
            statuses[tid] = "KNOWN"
            continue
        if broker == "BBAE":
            # No order ID means one original broker record, never one fee per owner row.
            identity = identity or "SOURCE:" + row["trade_key"]
            groups[(broker, row["account_id"], identity, "")].append(row)
            continue
        if broker == "LONGBRIDGE" and not identity:
            candidate = matched_evidence.get(tid)
            if (
                candidate
                and candidate[:2] == (broker, row["account_id"])
                and candidate[3] == fingerprint(row)
            ):
                identity = candidate[2]
        if identity:
            groups[(broker, row["account_id"], str(identity), "")].append(row)
        else:
            fees[tid] = None
            sources[tid] = "API"
            statuses[tid] = "UNMATCHED_ORDER"
    for (broker, account, identity, day), members in groups.items():
        source = "API"
        status = "UNKNOWN"
        total = None
        if broker == "BBAE" and all(r["currency"] == "USD" for r in members):
            total = 100_000_000 if min(r["business_date"] for r in members) >= BBAE_START else 0
            source = "RULE:BBAE_" + BBAE_START
            status = "KNOWN"
        else:
            data = evidence.get((broker, account, identity))
            if (
                data
                and data["currency"] == "USD"
                and all(
                    r["currency"] == "USD"
                    and r["symbol"] == _ticker(data["symbol"])
                    and r["side"] == data["side"]
                    for r in members
                )
            ):
                if sum(r["quantity_e8"] for r in members) == data["quantity_e8"]:
                    total = data["fees_e8"]
                    source = data["source"]
                    status = "KNOWN"
                else:
                    status = "ORDER_QUANTITY_MISMATCH"
        allocated = (
            split_amount(total, [(r["id"], r["quantity_e8"]) for r in members]) if total is not None else {}
        )
        for row in members:
            tid = row["id"]
            fees[tid] = allocated.get(tid)
            sources[tid] = source
            statuses[tid] = status
            identities[tid] = identity
    stamp = now()
    for row in rows:
        tid = row["id"]
        c.execute("UPDATE broker_trades SET fees_e8=? WHERE id=?", (fees.get(tid), tid))
        c.execute(
            "INSERT OR REPLACE INTO broker_fee_records VALUES (?,?,?,?,?,?,?)",
            (
                tid,
                fees.get(tid),
                sources.get(tid, "API"),
                statuses.get(tid, "UNKNOWN"),
                fingerprint(row),
                identities.get(tid),
                stamp,
            ),
        )
    by_trade = defaultdict(list)
    operations = [dict(r) for r in c.execute("SELECT * FROM person_operations")]
    for row in operations:
        if row["operation_kind"] == "MARKET_TRADE":
            by_trade[row["broker_trade_id"]].append(row)
    allocations = {}
    allocation_status = {}
    lookup = {r["id"]: r for r in rows}
    for tid, members in by_trade.items():
        trade = lookup.get(tid)
        valid = (
            trade
            and sum(r["quantity_e8"] for r in members) == trade["quantity_e8"]
            and all(
                all(r[k] == trade[k] for k in ("account_id", "instrument_id", "side", "currency"))
                for r in members
            )
        )
        total = fees.get(tid)
        if valid and total is not None:
            allocations.update(split_amount(total, [(r["id"], r["quantity_e8"]) for r in members]))
        else:
            for row in members:
                allocation_status[row["id"]] = (
                    "ALLOCATION_MISMATCH" if trade and not valid else statuses.get(tid, "UNKNOWN")
                )
    for row in operations:
        oid = row["id"]
        internal = row["operation_kind"] == "OWNER_TRANSFER"
        fee = 0 if internal else allocations.get(oid)
        source = "RULE:INTERNAL_ZERO" if internal else sources.get(row["broker_trade_id"], "API")
        status = "KNOWN" if fee is not None else allocation_status.get(oid, "UNKNOWN")
        values = (
            oid,
            fee,
            fees.get(row["broker_trade_id"]),
            source,
            status,
            *(row[k] for k in OP_FIELDS),
            stamp,
        )
        c.execute(
            "INSERT OR REPLACE INTO person_trade_fees VALUES (" + ",".join("?" for _ in values) + ")", values
        )
    return {
        "rule_version": VERSION,
        "broker_trades": len(rows),
        "known_trades": sum(v is not None for v in fees.values()),
        "unknown_trades": sum(v is None for v in fees.values()),
        "person_operations": len(operations),
        "known_person_operations": sum(
            r["operation_kind"] == "OWNER_TRANSFER" or r["id"] in allocations for r in operations
        ),
        "internal_operations": sum(r["operation_kind"] == "OWNER_TRANSFER" for r in operations),
    }
