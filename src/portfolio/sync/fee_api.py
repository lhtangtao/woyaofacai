"""只读获取缺失的券商订单费用；已确认费用永远复用，不重复请求。"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import time

from portfolio.paths import CREDENTIALS_PATH
from portfolio.sync.trade_fees import (
    e8,
    fingerprint,
    longbridge_evidence,
    schwab_evidence,
    trades,
    legacy_utc_clock,
)


def normalized_order(order):
    from portfolio.sync.sync_and_split_operations import _beijing_bookkeeping_time, _ticker

    return dict(
        order_id=str(order.order_id),
        symbol=_ticker(str(order.symbol)),
        side=str(order.side).split(".")[-1].upper(),
        executed_quantity=str(order.executed_quantity),
        executed_price=str(order.executed_price),
        updated_booktime=str(_beijing_bookkeeping_time(order.updated_at)),
        status=str(order.status),
    )


def normalized_detail(detail):
    data = normalized_order(detail)
    charge = detail.charge_detail
    data["charge_detail"] = (
        None
        if charge is None
        else dict(
            total_amount=str(charge.total_amount),
            currency=str(charge.currency),
            items=[
                dict(
                    code=str(item.code),
                    name=str(item.name),
                    fees=[
                        dict(code=str(f.code), amount=str(f.amount), currency=str(f.currency))
                        for f in item.fees
                    ],
                )
                for item in charge.items
            ],
        )
    )
    return data


def match_orders(rows, orders):
    candidates = defaultdict(list)
    sources = defaultdict(list)
    for identity, item in orders.items():
        if Decimal(item["executed_quantity"]) <= 0:
            continue
        key = (
            item["updated_booktime"][:10],
            item["symbol"],
            item["side"],
            e8(item["executed_quantity"]),
            e8(item["executed_price"]),
        )
        candidates[key].append(str(identity))
    for row in rows:
        if not row["broker_order_id"]:
            sources[
                (row["business_date"], row["symbol"], row["side"], row["quantity_e8"], row["price_e8"])
            ].append(row)
    matches = {}
    for row in rows:
        if row["broker_order_id"]:
            matches[str(row["id"])] = str(row["broker_order_id"])
            continue
        key = (row["business_date"], row["symbol"], row["side"], row["quantity_e8"], row["price_e8"])
        choices = candidates[key]
        exact = [
            oid for oid in choices if orders[oid]["updated_booktime"] == row["book_time"].replace("T", " ")
        ]
        legacy = legacy_utc_clock(row)
        if legacy is not None:
            for oid, item in orders.items():
                if item.get("updated_booktime") in {None, "NaT"}:
                    continue
                if (
                    item["symbol"],
                    item["side"],
                    e8(item["executed_quantity"]),
                    e8(item["executed_price"]),
                ) == (
                    row["symbol"],
                    row["side"],
                    row["quantity_e8"],
                    row["price_e8"],
                ) and datetime.fromisoformat(item["updated_booktime"]) == legacy:
                    exact.append(oid)
        exact = list(set(exact))
        if len(exact) == 1:
            matches[str(row["id"])] = exact[0]
        elif len(exact) > 1:
            continue
        elif len(choices) == 1 and len(sources[key]) == 1:
            matches[str(row["id"])] = choices[0]
    return matches


def fetch_missing(c, existing, persist, *, progress=lambda message: print(message, flush=True)):
    """Network outside database write transactions. Persist every successful order immediately."""
    rows = trades(c)
    output = {}
    warnings = []

    def covered(row):
        direct = (row["broker_code"], row["account_id"], str(row["broker_order_id"]))
        if row["broker_order_id"] and direct in existing:
            return True
        return any(
            key[:2] == (row["broker_code"], row["account_id"])
            and data.get("matches", {}).get(str(row["id"])) == fingerprint(row)
            for key, data in existing.items()
        )

    missing = [row for row in rows if row["broker_code"] in {"LONGBRIDGE", "SCHWAB"} and not covered(row)]
    if any(r["broker_code"] == "SCHWAB" for r in missing):
        try:
            from portfolio.sync.schwab_api import get_service
            from portfolio.sync.database_sync import _load_schwab_archive

            service = get_service()
            if service is None:
                raise ValueError("嘉信API未启用")
            archive = service.history(archived=_load_schwab_archive(c), use_local_archive=False)
            evidence = schwab_evidence(c, archive)
            persist(evidence)
            output.update(evidence)
            progress(f"[FEES] 嘉信已持久化 {len(evidence)} 条API费用明细。")
        except Exception as exc:
            warnings.append(f"嘉信费用补取失败（{type(exc).__name__}），缺失保持未知。")
    targets = [r for r in missing if r["broker_code"] == "LONGBRIDGE"]
    if not targets:
        return output, warnings
    if len({r["account_id"] for r in targets}) != 1:
        return output, warnings + ["长桥账户范围不唯一，未自动合并账户。"]
    try:
        from longbridge.openapi import TradeContext, Config
        from portfolio.services.live_quotes import credentials

        key, secret, token = credentials(CREDENTIALS_PATH)
        context = TradeContext(Config.from_apikey(key, secret, token, enable_print_quote_packages=False))
        orders = {}
        last_request = 0

        def request(method, **kwargs):
            nonlocal last_request
            for attempt in range(5):
                # Serial pacing plus backoff: never fan out order-detail requests.
                time.sleep(max(0, 1.1 - (time.monotonic() - last_request)))
                last_request = time.monotonic()
                try:
                    return method(**kwargs)
                except Exception:
                    if attempt == 4:
                        raise
                    time.sleep(min(30, 2 ** (attempt + 1)))

        for month in sorted({r["business_date"][:7] for r in targets if not r["broker_order_id"]}):
            y, m = map(int, month.split("-"))
            start = datetime(y, m, 1, tzinfo=timezone.utc)
            end = (start + timedelta(days=32)).replace(day=1)
            try:
                batch = request(context.history_orders, start_at=start-timedelta(days=1), end_at=end+timedelta(days=1))
                if len(batch) >= 1000:
                    warnings.append(month + "长桥历史订单可能截断，未据此猜测费用。")
                    continue
                orders.update(
                    (str(order.order_id), normalized_order(order))
                    for order in batch
                    if order.executed_quantity > 0
                )
            except Exception as exc:
                warnings.append(month + f"历史订单查询失败（{type(exc).__name__}）。")
        matches = match_orders(targets, orders)
        for index, identity in enumerate(sorted(set(matches.values())), 1):
            try:
                cached = existing.get(("LONGBRIDGE", targets[0]["account_id"], identity))
                if cached:
                    linked = dict(cached)
                    linked["matches"] = dict(cached.get("matches", {}))
                    linked["matches"].update(
                        {
                            tid: fingerprint(row)
                            for row in targets
                            for tid, oid in matches.items()
                            if oid == identity and str(row["id"]) == tid
                        }
                    )
                    persist({("LONGBRIDGE", targets[0]["account_id"], identity): linked})
                    continue
                detail = normalized_detail(request(context.order_detail, order_id=identity))
                # Still-open partially filled orders do not have immutable final fees yet.
                if detail["status"].split(".")[-1] not in {
                    "Filled",
                    "PartialWithdrawal",
                    "Canceled",
                    "Rejected",
                    "Expired",
                    "PartialFilledCanceled",
                }:
                    continue
                evidence = longbridge_evidence(
                    c, {identity: detail}, {tid: oid for tid, oid in matches.items() if oid == identity}
                )
                if evidence:
                    persist(evidence)
                    output.update(evidence)
            except Exception as exc:
                warnings.append(f"长桥费用请求失败（{type(exc).__name__}），未按零费处理。")
            if index % 20 == 0:
                progress(f"[FEES] 长桥补取进度 {index}/{len(set(matches.values()))}，成功费用已落库。")
        unmatched = len(targets) - len(matches)
        if unmatched:
            warnings.append(f"{unmatched}条长桥历史成交没有唯一订单匹配，保留待核对。")
    except Exception as exc:
        warnings.append(f"长桥费用补取失败（{type(exc).__name__}），已获取费用保留。")
    return output, sorted(set(warnings))
