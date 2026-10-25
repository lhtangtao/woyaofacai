"""Date boundaries for quality checks; never truncate accounting history."""
import json
from collections import defaultdict
from dataclasses import replace
from datetime import date

from portfolio.domain.analytics import Issue

AUDIT_START_DATE = date(2026, 7, 1)


def record_day(value):
    text = str(value or "").strip().replace("/", "-")
    if len(text) == 7:
        text += "-01"
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def source_days(snapshot):
    dates = defaultdict(set)
    def add(source, value):
        day = record_day(value)
        if source is not None and day:
            dates[source].add(day)
    for event in snapshot.events:
        add(event.source_id, event.day)
    for price in snapshot.prices:
        add(price.source_id, price.day)
    for baseline in snapshot.baselines:
        add(baseline.get("source_record_id"), baseline.get("baseline_date"))
    for table in ("person_operations", "broker_trades", "cash_events", "asset_adjustments"):
        for row in snapshot.extras.get(table, ()):
            add(row.get("source_record_id"), row.get("business_date") or row.get("effective_from"))
    # Import time is deliberately excluded: reimporting old data does not make it new.
    keys = ("business_date", "trade_date", "book_time", "date", "transactionDate", "tradeDate",
            "日期", "交易日期", "成交日期", "账务日", "业务日期", "交易时间", "成交时间", "Trade Date", "Activity Date")
    for row in snapshot.extras.get("raw_review", ()):
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        fields = payload.get("fields", payload)
        if isinstance(fields, dict):
            for key in keys:
                add(row["id"], fields.get(key))
    return dates


def issues_in_period(snapshot, start_date):
    if start_date is None:
        return list(dict.fromkeys(snapshot.issues))
    start, end = start_date.isoformat(), snapshot.scope.as_of.isoformat()
    dates = source_days(snapshot)
    result, undated = [], set()
    for issue in snapshot.issues:
        if issue.severity == "INFO":
            result.append(issue)
            continue
        day = record_day(issue.day)
        if day:
            if start <= day <= end:
                result.append(issue)
        elif issue.source_ids:
            evidence = {day for source in issue.source_ids for day in dates.get(source, ())}
            matching = [day for day in evidence if start <= day <= end]
            if matching:
                result.append(replace(issue, day=max(matching)))
            elif not evidence:
                undated.update(issue.source_ids)
        elif start <= end:
            # Current-state checks (cash opening, currency/market prerequisites)
            # are evaluated as of the report date, rather than assigned to old trades.
            result.append(replace(issue, day=end))
    if undated:
        result.append(Issue("DQ007", "INFO", f"{len(undated)} 条告警来源缺少可确认的业务日期，未计入期间错误/待核对；原始记录保留在数据中心。",
                            source_ids=tuple(sorted(undated))))
    return list(dict.fromkeys(result))
