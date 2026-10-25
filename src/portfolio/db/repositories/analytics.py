"""One read transaction supplies a consistent financial snapshot. No SQL views with floats."""
import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from portfolio.db.repositories.trades import _base_query, has_fee_data
from portfolio.domain.analytics import Event, Issue, Price, Snapshot
from portfolio.domain.models import Ledger, from_e8
from portfolio.domain.trade_time import event_order_time


def load_snapshot(connection, scope, *, cancel=None, with_prices=True):
    # Date-range/search filters must never remove the opening history of a position.
    full_scope = replace(scope, start_date=None, search="", audit=False)
    query, params = _base_query(full_scope, fees_available=has_fee_data(connection))
    raw = [dict(row) for row in connection.execute("SELECT * FROM (" + query + ") ORDER BY business_date,book_time,sequence_no,id", params)]
    accounts = {r["id"]: dict(r) for r in connection.execute("SELECT * FROM broker_accounts")}
    persons = {r["id"]: dict(r) for r in connection.execute("SELECT * FROM persons")}
    instruments = {r["id"]: dict(r) for r in connection.execute("SELECT * FROM instruments")}
    # Full metadata needed for transfers/grain checks is fetched inside this same snapshot.
    table = "person_operations" if scope.ledger == Ledger.PERSON else "broker_trades"
    originals = {r["id"]: dict(r) for r in connection.execute(f"SELECT * FROM {table} WHERE business_date<=?", (scope.as_of.isoformat(),))}
    if scope.search:
        needle = scope.search.casefold()
        matched = {(r.get("person_id"), r["account_id"], originals[r["id"]]["instrument_id"]) for r in raw
                   if any(needle in str(r.get(f, "")).casefold() for f in
                          ("symbol", "broker_code", "person_name", "order_id", "source_path", "side"))}
        raw = [r for r in raw if (r.get("person_id"), r["account_id"], originals[r["id"]]["instrument_id"]) in matched]
    events = []
    for r in raw:
        o = originals[r["id"]]
        metadata = {**o, **r}
        events.append(Event(r["id"], r["source_record_id"], r.get("person_id"), r["account_id"],
                            o["instrument_id"], r["business_date"], r["book_time"], r["sequence_no"],
                            r["side"], from_e8(r["quantity_e8"]), from_e8(r["amount_e8"]),
                            from_e8(r["price_e8"]), r["kind"], metadata))
    events.sort(key=lambda e: (e.day, event_order_time(e), e.sequence, e.id))
    ids = {e.instrument_id for e in events}
    ids.update(r["instrument_id"] for r in connection.execute("SELECT instrument_id FROM historical_baselines WHERE baseline_role='OPENING' AND instrument_id IS NOT NULL AND baseline_date<=?", (scope.as_of.isoformat(),)))
    aliases = [dict(r) for r in connection.execute("SELECT * FROM instrument_aliases")]
    names: dict[str, set[int]] = {}
    for identifier, instrument in instruments.items():
        names.setdefault(instrument["symbol"], set()).add(identifier)
        # Explicit Money2 provider adapter: database_prices.write_price_frames
        # stores US prices as SYMBOL.US. Market identity comes from instruments,
        # never from interpreting an arbitrary ticker prefix/suffix as a market.
        if instrument["market"] == "US":
            names.setdefault(instrument["symbol"] + ".US", set()).add(identifier)
    for alias in aliases:
        names.setdefault(alias["source_symbol"], set()).add(alias["instrument_id"])
    prices, issues = [], []
    price_symbols = [symbol for symbol, targets in names.items() if targets & ids]
    if price_symbols and with_prices:
        placeholders = ",".join("?" for _ in price_symbols)
        sql = f"""SELECT m.*,r.payload_json FROM market_prices m JOIN raw_records r ON r.id=m.source_record_id
                  WHERE m.symbol IN ({placeholders}) AND
                  (m.trade_date<=? OR (m.trade_date IS NULL AND m.price_type='LATEST') OR
                  (m.price_type='MONTH_CLOSE' AND m.period_key<=?))"""
        for row in connection.execute(sql, [*price_symbols, scope.as_of.isoformat(), scope.as_of.isoformat()[:7]]):
            if cancel and cancel.is_set():
                raise ValueError("计算已取消。")
            targets = names[row["symbol"]]
            if len(targets) != 1:
                issues.append(Issue("DQ015", "ERROR", f"行情 {row['symbol']} 对应多个市场，未使用该价格。",
                                    row["trade_date"] or (row["period_key"] if row["price_type"] == "MONTH_CLOSE" else ""),
                                    source_ids=(row["source_record_id"],)))
                continue
            identifier = next(iter(targets))
            try:
                payload = json.loads(row["payload_json"], parse_float=Decimal)
                fields, meta = payload.get("fields", {}), payload.get("metadata", {})
            except (ValueError, AttributeError):
                fields, meta = {}, {}
            day = row["trade_date"]
            if not day and row["price_type"] == "LATEST":
                # Source market-date evidence, never system-local conversion or guessed ticker prefix.
                day = str(fields.get("日期", ""))[:10]
            if not day and row["price_type"] == "MONTH_CLOSE":
                import calendar
                try:
                    y, m = map(int, row["period_key"].split("-"))
                    day = f"{y:04}-{m:02}-{calendar.monthrange(y, m)[1]:02}"
                except ValueError:
                    continue
            try:
                datetime.strptime(day or "", "%Y-%m-%d")
            except ValueError:
                continue  # Undated quotes cannot value a historical position.
            if day > scope.as_of.isoformat():
                continue
            basis = str(meta.get("price_basis") or fields.get("_price_basis") or "UNKNOWN").upper()
            prices.append(Price(identifier, day, from_e8(row["price_e8"]), row["price_type"],
                                row["price_status"], row["source_record_id"], row["quoted_at_utc"], basis))
    actions = [dict(r) for r in connection.execute("SELECT * FROM corporate_actions WHERE record_state='ACTIVE' AND effective_date<=? ORDER BY effective_date,id", (scope.as_of.isoformat(),)) if r["instrument_id"] in ids]
    baselines = [dict(r) for r in connection.execute("SELECT * FROM historical_baselines WHERE baseline_date<=? ORDER BY baseline_date,id", (scope.as_of.isoformat(),))]
    extras = {}
    for name in ("cash_events", "asset_adjustments", "fx_rates", "broker_trades", "person_operations", "import_batches"):
        extras[name] = [dict(r) for r in connection.execute(f"SELECT * FROM {name}")]
    extras["raw_review"] = [dict(r) for r in connection.execute("SELECT id,source_locator,parse_status,parse_message,payload_json FROM raw_records WHERE parse_status IN ('PENDING','REVIEW_REQUIRED')")]
    extras["aliases"] = aliases
    if with_prices:
        extras["raw_index"] = [dict(r) for r in connection.execute("SELECT id,import_batch_id,source_locator,source_sheet,source_row,record_kind,parse_status FROM raw_records ORDER BY id DESC")]
    return Snapshot(scope, events, instruments, accounts, persons, actions, baselines, prices, extras, issues)
