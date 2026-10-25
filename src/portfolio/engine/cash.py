"""Cash is separate from stock KPI. Only evidenced openings produce balances."""
from portfolio.domain.analytics import Issue
from portfolio.domain.models import Ledger, from_e8


def cash_balance(snapshot, account):
    day = snapshot.scope.as_of.isoformat()
    owner = None
    if snapshot.scope.ledger == Ledger.PERSON:
        if not snapshot.scope.person_ids or len(snapshot.scope.person_ids) != 1:
            return None
        owner = snapshot.scope.person_ids[0]
    openings = []
    for b in snapshot.baselines:
        if (b["baseline_role"] == "OPENING" and b["baseline_kind"] == "CASH_BALANCE" and b["account_id"] == account
                and b["person_id"] == owner and b["baseline_date"] <= day and b["currency"] == "USD" and b["market_value_e8"] is not None):
            openings.append((b["baseline_date"], from_e8(b["market_value_e8"]), b["source_record_id"]))
    for adjustment in snapshot.extras.get("asset_adjustments", []):
        if (adjustment["status"] == "ACTIVE" and adjustment["target_metric"] == "CASH_BALANCE" and adjustment["adjustment_mode"] == "BALANCE_ANCHOR"
            and adjustment["account_id"] == account and adjustment["person_id"] == owner and adjustment["currency"] == "USD"
            and adjustment["effective_from"] <= day and (not adjustment["effective_to"] or adjustment["effective_to"] >= day)):
            openings.append((adjustment["effective_from"], from_e8(adjustment["amount_e8"]), adjustment["source_record_id"]))
    if not openings:
        return None
    last_day = max(item[0] for item in openings)
    anchors = [item for item in openings if item[0] == last_day]
    if len(anchors) != 1:
        snapshot.issues.append(Issue("DQ009", "WARN", "现金同日有多个余额锚点，无法确认余额。", day, owner, account))
        return None
    value = anchors[0][1]
    if owner is not None:
        # Owner operations have no reliable net-cash contract; do not allocate
        # physical account cash by equity weights or reuse gross trade amounts.
        if any(e.account_id == account and e.day > last_day for e in snapshot.events):
            return None
    else:
        for trade in snapshot.extras.get("broker_trades", []):
            if trade["account_id"] == account and trade["record_state"] == "ACTIVE" and trade["accounting_role"] == "ACCOUNT_LEDGER" and last_day < trade["business_date"] <= day:
                if trade["net_amount_e8"] is None:
                    return None
                value += from_e8(trade["net_amount_e8"])
    for event in snapshot.extras.get("cash_events", []):
        if event["account_id"] != account or event["record_state"] != "ACTIVE" or not last_day < event["business_date"] <= day:
            continue
        if owner is not None and event["person_id"] != owner:
            continue
        if event["accounting_role"] == "REVIEW_REQUIRED":
            return None
        if event["accounting_role"] == "INFORMATIONAL":
            continue
        if event["broker_trade_id"] and event["event_type"] in ("FEE", "TAX"):
            snapshot.issues.append(Issue("DQ017", "WARN", "费用现金事件关联净额成交，可能重复扣费；现金余额暂不可确认。", event["business_date"], owner, account, source_ids=(event["source_record_id"],)))
            return None
        value += from_e8(event["amount_e8"])
    for adjustment in snapshot.extras.get("asset_adjustments", []):
        if (adjustment["status"] == "ACTIVE" and adjustment["target_metric"] == "CASH_BALANCE" and adjustment["adjustment_mode"] == "DELTA"
            and adjustment["account_id"] == account and adjustment["person_id"] == owner and last_day < adjustment["effective_from"] <= day
            and (not adjustment["effective_to"] or adjustment["effective_to"] >= day)):
            value += from_e8(adjustment["amount_e8"])
    return value
