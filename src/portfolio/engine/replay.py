"""Excel net-investment replay. No Qt, float, implicit price or silent deduplication."""
from collections import defaultdict
from copy import copy, deepcopy
from decimal import Decimal, localcontext

from portfolio.domain.analytics import ZERO, Issue, Position, plus
from portfolio.domain.models import Ledger, from_e8
from portfolio.domain.trade_time import event_order_time

NEGATIVE_QUANTITY_TOLERANCE = Decimal('0.01')


def normalize_quantity(quantity):
    # [Antigravity] 容差微调至 0.01 股：仅清除合股/拆股运算产生的微小尾差（如 0.0033 股），
    # 避免将 0.20、0.38 等正常流水分数误截断，导致在中间交易步骤人为垫付虚增股数。
    return ZERO if -NEGATIVE_QUANTITY_TOLERANCE <= quantity < ZERO else quantity


def grain_conflicts(snapshot):
    groups, blocked = defaultdict(list), set()
    for row in snapshot.extras.get("broker_trades", []):
        if row["record_state"] == "ACTIVE" and row["accounting_role"] == "ACCOUNT_LEDGER":
            if row["broker_order_id"]:
                groups[(row["account_id"], row["broker_order_id"])].append(row)
    for rows in groups.values():
        grains = {r["record_granularity"] for r in rows}
        executions = [r["broker_execution_id"] for r in rows if r["broker_execution_id"]]
        unresolved_same_order = len(rows) > 1 and (len(executions) != len(rows) or len(grains) != 1 or "ORDER_TOTAL" in grains)
        if len(grains) > 1 or len(executions) != len(set(executions)) or unresolved_same_order:
            blocked.update(r["id"] for r in rows)
            snapshot.issues.append(Issue("DQ005", "ERROR", "相同订单存在多个成交粒度、重复执行 ID 或缺少执行关联证据；相关持仓金额暂不可确认。",
                                         account_id=rows[0]["account_id"], source_ids=tuple(r["source_record_id"] for r in rows)))
    return blocked


def action_applies(action, key, snapshot):
    return action["instrument_id"] == key[2] and action["broker_scope"] in ("ALL", snapshot.accounts[key[1]]["broker_code"])


def opening_positions(snapshot):
    positions, cutoffs, candidates = {}, {}, defaultdict(list)
    for baseline in snapshot.baselines:
        if baseline["baseline_role"] != "OPENING" or baseline["instrument_id"] is None:
            continue
        person = snapshot.scope.ledger == Ledger.PERSON
        kind = "PERSON_ACCOUNT" if person else "ACCOUNT"
        if baseline["scope_type"] != kind:
            snapshot.issues.append(Issue("DQ014", "WARN", "开账基准只有汇总层级，无法分摊到证券/账户，未用于持仓。", source_ids=(baseline["source_record_id"],)))
            continue
        key = (baseline["person_id"] if person else None, baseline["account_id"], baseline["instrument_id"])
        s = snapshot.scope
        if ((s.person_ids is not None and key[0] not in s.person_ids) or
            (s.account_ids is not None and key[1] not in s.account_ids) or
            (s.instrument_ids is not None and key[2] not in s.instrument_ids) or
            baseline["currency"] != s.reporting_currency or
            (not s.include_disabled and person and not snapshot.persons.get(key[0], {}).get("enabled", 0)) or
            (s.category and snapshot.instruments[key[2]]["category"] != s.category)):
            continue
        candidates[key].append(baseline)
    for key, rows in candidates.items():
        latest = rows[-1]
        if latest["quantity_e8"] is None:
            snapshot.issues.append(Issue("DQ014", "ERROR", "开账基准缺少数量，不能按零持仓开账。", source_ids=(latest["source_record_id"],)))
            continue
        if sum(r["baseline_date"] == latest["baseline_date"] for r in rows) > 1:
            snapshot.issues.append(Issue("DQ014", "ERROR", "同一证券/范围存在多份同日开账基准，成本未确认。", *(), source_ids=tuple(r["source_record_id"] for r in rows)))
            continue
        buy, sell = from_e8(latest["cumulative_buy_e8"]), from_e8(latest["cumulative_sell_e8"])
        net = from_e8(latest["net_cost_e8"])
        if buy is None or sell is None:
            # An explicit net cost is sufficient for compatibility, not proof of lifetime buy/sell totals.
            buy, sell = net, ZERO if net is not None else None
        pos = Position(key, quantity=normalize_quantity(from_e8(latest["quantity_e8"]) or ZERO), buy=buy, sell=sell,
                       first=latest["baseline_date"], last=latest["baseline_date"], source_ids=[latest["source_record_id"]])
        pos.warnings.add("OPENING 基准；历史成交累计可能不完整")
        positions[key], cutoffs[key] = pos, latest["baseline_date"]
        if any(e.key == key and e.day <= latest["baseline_date"] for e in snapshot.events):
            snapshot.issues.append(Issue("DQ014", "WARN", "开账日及之前的交易已明确排除，避免与 OPENING 重复。", source_ids=(latest["source_record_id"],)))
    return positions, cutoffs


def replay(snapshot, *, capture_days=(), cancel=None):
    with localcontext() as context:
        context.prec = 50
        return _replay(snapshot, capture_days, cancel)


def _replay(snapshot, capture_days, cancel):
    seeds, cutoffs = opening_positions(snapshot)
    positions = {}
    traces, daily_states = {}, {}
    blocked = grain_conflicts(snapshot)
    events = [(e.day, event_order_time(e), 1, e.sequence, e.id, "TRADE", e) for e in snapshot.events]
    events += [(a["effective_date"], a["effective_date"] + " 00:00:00", 0, 0, a["id"], "ACTION", a) for a in snapshot.actions]
    events += [(cutoffs[key], cutoffs[key] + " 99:00:00", 0, 0, 0, "OPENING", p) for key, p in seeds.items()]
    events += [(day, day + " 99:99:99", 2, 0, 0, "CAPTURE", day) for day in capture_days]
    events.sort(key=lambda row: row[:5])
    for day, _, _, _, _, kind, event in events:
        if cancel and cancel.is_set():
            raise ValueError("计算已取消。")
        if kind == "CAPTURE":
            captured = {}
            for key, p in positions.items():
                item = copy(p)
                item.warnings = set(p.warnings)
                # Daily lineage identifies the last contributing event; the
                # full cost trail remains available by key in the trade replay.
                # Do not copy an ever-growing 100k-id list for every price day.
                item.source_ids = p.source_ids[-1:]
                captured[key] = item
            daily_states[day] = captured
            continue
        if kind == "OPENING":
            positions[event.key] = deepcopy(event)
            continue
        if kind == "ACTION":
            factor = Decimal(event["ratio_numerator"]) / Decimal(event["ratio_denominator"])
            for key, position in positions.items():
                if action_applies(event, key, snapshot) and day > cutoffs.get(key, ""):
                    position.quantity = normalize_quantity(position.quantity * factor)
                    position.buy_quantity *= factor
                    position.sell_quantity *= factor
            continue
        e = event
        if e.day <= cutoffs.get(e.key, ""):
            continue
        p = positions.setdefault(e.key, Position(e.key, first=e.time))
        prior_cost = p.cost
        p.last, p.last_price = e.time, e.price
        p.count += 1
        p.source_ids.append(e.source_id)
        if e.kind == "BOOK_SETTLEMENT":
            # Source explicitly calls this a historic book settlement, not a physical broker fill.
            if e.metadata.get("amount_basis") != "LEGACY" or not e.metadata.get("legacy_transaction_type"):
                p.warnings.add("未确认账面结算")
                p.buy = p.sell = None
            else:
                p.warnings.add("沿用历史账面结算数量/金额；不记券商成交")
        linked = e.id if snapshot.scope.ledger == Ledger.BROKER else e.metadata.get("broker_trade_id")
        if linked in blocked:
            p.warnings.add("成交粒度冲突")
            p.buy = p.sell = None
        if e.kind in ("OWNER_TRANSFER", "EXTERNAL_TRANSFER"):
            if e.amount is None or e.metadata.get("amount_basis") not in ("TRANSFER_VALUE", "LEGACY"):
                p.warnings.add("待人工核定划转成本")
                p.buy = p.sell = None
            if e.metadata.get("link_status") == "REVIEW_REQUIRED":
                p.warnings.add("划转归属待审核；数值为源记录候选值")
        if e.incoming:
            p.quantity += e.quantity
            p.buy_quantity += e.quantity
            p.buy = plus(p.buy, e.amount)
        else:
            p.quantity -= e.quantity
            p.sell_quantity += e.quantity
            p.sell = plus(p.sell, e.amount)
        p.quantity = normalize_quantity(p.quantity)
        if p.quantity < ZERO:
            p.warnings.add("负数持仓")
            snapshot.issues.append(Issue("DQ001", "ERROR", "有效事件后出现负数持仓；未启用做空模型。", e.day, e.person_id, e.account_id, e.instrument_id, (e.source_id,)))
        post_cost = p.cost
        cost_diff = (post_cost - prior_cost) if (post_cost is not None and prior_cost is not None) else None
        e.metadata["cost_diff"] = cost_diff
        snapshot.extras.setdefault("cost_diffs", {})[e.id] = cost_diff
        traces[e.id] = (None, None, None) if "成交粒度冲突" in p.warnings else (p.quantity, p.net, p.cost)
    return positions, traces, daily_states
