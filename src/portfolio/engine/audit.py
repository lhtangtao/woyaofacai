from collections import defaultdict
from decimal import Decimal

from portfolio.domain.analytics import ZERO, Issue, Report, ReportRow
from portfolio.domain.audit_period import issues_in_period
from portfolio.domain.models import from_e8
from portfolio.engine.replay import replay

ISSUE_COLUMNS = ("规则ID", "严重度", "日期", "人员", "账户", "证券", "说明", "原始记录", "建议动作")


def audit(snapshot, positions, *, start_date=None):
    asof = snapshot.scope.as_of.isoformat()
    extras = snapshot.extras
    for table, condition, rule, text in (
        ("person_operations", lambda r: r["link_status"] != "LINKED" and r["business_date"] <= asof, "DQ006", "人员操作缺少可靠券商关联或归属待审"),
        ("raw_review", lambda r: True, "DQ007", "原始行尚未完成解析/审核"),
        ("cash_events", lambda r: r["accounting_role"] == "REVIEW_REQUIRED" and r["record_state"] == "ACTIVE" and r["business_date"] <= asof, "DQ008", "资金事件归属或性质待审"),
        ("asset_adjustments", lambda r: r["status"] == "REVIEW_REQUIRED" and r["effective_from"] <= asof, "DQ009", "资产调整待审，未用于股票市值"),
    ):
        for row in extras.get(table, []):
            if condition(row):
                snapshot.issues.append(Issue(rule, "WARN", text, row.get("business_date") or row.get("effective_from", ""), row.get("person_id"), row.get("account_id"), row.get("instrument_id"), (row.get("source_record_id", row["id"]),)))
    trades = {r["id"]: r for r in extras.get("broker_trades", []) if r["record_state"] == "ACTIVE" and r["accounting_role"] == "ACCOUNT_LEDGER" and r["business_date"] <= asof}
    linked, transfers = defaultdict(list), defaultdict(list)
    operations = [r for r in extras.get("person_operations", []) if r["business_date"] <= asof and (r["broker_trade_id"] is None or r["broker_trade_id"] in trades)]
    for row in operations:
        if row["operation_kind"] == "MARKET_TRADE" and row["broker_trade_id"] in trades:
            linked[row["broker_trade_id"]].append(row)
        if row["operation_kind"] == "OWNER_TRANSFER":
            transfers[row["transfer_group_key"] or f"UNPAIRED:{row['id']}"].append(row)
    for id, rows in linked.items():
        trade = trades[id]
        if sum(r["quantity_e8"] for r in rows) > trade["quantity_e8"] or any(r["side"] != trade["side"] or r["account_id"] != trade["account_id"] or r["instrument_id"] != trade["instrument_id"] for r in rows):
            snapshot.issues.append(Issue("DQ003", "ERROR", "关联成交的人员数量超分配，或方向/账户/证券不一致。", trade["business_date"], account_id=trade["account_id"], instrument_id=trade["instrument_id"], source_ids=tuple(r["source_record_id"] for r in rows)))
    for rows in transfers.values():
        incoming = [r for r in rows if r["side"] == "TRANSFER_IN"]
        outgoing = [r for r in rows if r["side"] == "TRANSFER_OUT"]
        valid = (len(incoming) == len(outgoing) == 1 and incoming[0]["quantity_e8"] == outgoing[0]["quantity_e8"]
                 and all(incoming[0][field] == outgoing[0][field] for field in ("account_id", "instrument_id", "business_date", "amount_e8"))
                 and incoming[0]["person_id"] == outgoing[0]["counterparty_person_id"] and outgoing[0]["person_id"] == incoming[0]["counterparty_person_id"])
        if not valid:
            r = rows[0]
            snapshot.issues.append(Issue("DQ004", "ERROR", "人员划转缺少一一配对、数量/金额/账户/人员不守恒。", r["business_date"], r["person_id"], r["account_id"], r["instrument_id"], tuple(r["source_record_id"] for r in rows)))
    for r in trades.values():
        gross, fee, net = (from_e8(r[k]) for k in ("gross_amount_e8", "fees_e8", "net_amount_e8"))
        if gross is not None and net is not None and fee is not None:
            expected = -gross - fee if r["side"] == "BUY" else gross - fee
            if abs(net - expected) > Decimal("0.00000001"):
                snapshot.issues.append(Issue("DQ017", "WARN", "净现金额与总额/费用不一致；兼容收益仍使用 calculation_amount。", r["business_date"], account_id=r["account_id"], instrument_id=r["instrument_id"], source_ids=(r["source_record_id"],)))
    symbols = defaultdict(set)
    for i in snapshot.instruments.values():
        symbols[i["symbol"]].add(i["market"])
    for symbol, markets in symbols.items():
        if len(markets) > 1:
            snapshot.issues.append(Issue("DQ015", "WARN", f"同代码 {symbol} 存在多个市场；未按代码前缀猜测市场。"))
    if not any(b["baseline_role"] == "OPENING" and b["baseline_kind"] == "CASH_BALANCE" for b in snapshot.baselines):
        snapshot.issues.append(Issue("DQ016", "WARN", "没有可靠现金期初余额；仅展示现金事件净变动，不作为余额或股票市值。"))
    selected_instruments = {e.instrument_id for e in snapshot.events}
    if any(i["currency"] != snapshot.scope.reporting_currency for id, i in snapshot.instruments.items() if id in selected_instruments):
        snapshot.issues.append(Issue("DQ011", "WARN", "选中范围含其他币种，缺少逐日可溯源汇率时不能合并。"))
    for a in extras.get("asset_adjustments", []):
        if a["status"] == "ACTIVE" and a["target_metric"] not in ("CASH_BALANCE", "CASH_FINANCING_BALANCE"):
            snapshot.issues.append(Issue("DQ009", "WARN", "未知调整指标，未套用到股票市值或收益。", a["effective_from"], a["person_id"], a["account_id"], a["instrument_id"], (a["source_record_id"],)))
    # Compare the full historical ownership ledger (including disabled people) to
    # the independent physical ledger. Never compare just the selected person.
    comparison = extras.get("ledger_comparison")
    if comparison:
        broker_snapshot, owner_snapshot = comparison
        broker_positions = replay(broker_snapshot)[0]
        owner_positions = replay(owner_snapshot)[0]
        broker_qty, owner_qty = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
        for p in broker_positions.values():
            broker_qty[p.key[1:]] += p.quantity
        for p in owner_positions.values():
            owner_qty[p.key[1:]] += p.quantity
        for key in broker_qty.keys() | owner_qty.keys():
            if abs(broker_qty[key] - owner_qty[key]) >= Decimal('1'):
                snapshot.issues.append(Issue("DQ002", "ERROR", f"全历史人员合计 {owner_qty[key]} 股，券商 {broker_qty[key]} 股，差额 {owner_qty[key] - broker_qty[key]}。", asof, account_id=key[0], instrument_id=key[1]))
    # [Antigravity] 负仓异常检查：若当前持仓存在负数，按即期严重错误记录，避免被审计期间过滤，并在启动及异常中心告警
    for key, p in positions.items():
        if p.quantity < ZERO:
            if not any(i.rule == "DQ001" and (i.person_id, i.account_id, i.instrument_id) == key and "当前持仓为负数" in i.message for i in snapshot.issues):
                orig = next((i for i in snapshot.issues if i.rule == "DQ001" and (i.person_id, i.account_id, i.instrument_id) == key and "当前持仓为负数" not in i.message), None)
                trigger_info = f"（最早于 {orig.day} 划转/交易后变为负数，流水ID {orig.source_ids[0]}）" if orig and orig.day and orig.source_ids else ""
                snapshot.issues.append(Issue(
                    "DQ001",
                    "ERROR",
                    f"当前持仓为负数（{p.quantity} 股）{trigger_info}；未启用做空模型，导致该标的成本、收益及人员汇总无法计算。",
                    asof,
                    person_id=key[0],
                    account_id=key[1],
                    instrument_id=key[2],
                    source_ids=orig.source_ids if orig else tuple(p.source_ids[-1:] if p.source_ids else ()),
                    action="核对原始交易或划转流水（检查是否误记券商账户）；更正源数据后重新导入。"
                ))
    snapshot.issues.append(Issue("DQ018", "INFO", "当前仅开放 Excel 净投入兼容口径；标准会计 FIFO 收益未开放，不能混称未实现损益。"))
    unique = issues_in_period(snapshot, start_date)
    snapshot.issues[:] = unique
    result = []
    for i in sorted(unique, key=lambda i: ({"ERROR": 0, "WARN": 1, "INFO": 2}[i.severity], i.rule, i.day)):
        result.append(ReportRow((i.rule, i.severity, i.day, snapshot.persons.get(i.person_id, {}).get("name", ""),
                                snapshot.accounts.get(i.account_id, {}).get("display_name", ""), snapshot.instruments.get(i.instrument_id, {}).get("symbol", ""),
                                i.message, ",".join(map(str, i.source_ids)), i.action), {"source_record_ids": i.source_ids, "rule": i.rule}))
    period = f"检查范围：{start_date.isoformat()} 至 {asof}。" if start_date else "规则按全库检查。"
    return Report("数据异常", ISSUE_COLUMNS, tuple(result), period + "历史记录仍用于成本回放；当前持仓/账本一致性仍按报告日期核验。无明确业务日期的来源不猜测归期。只诊断，不自动修复。")
