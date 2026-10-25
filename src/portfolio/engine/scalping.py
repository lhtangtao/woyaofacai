"""Monthly two-way matching with explicit Excel LIFO-buy/FIFO-sell policy.

Amounts are allocated from the original lot, not from a residual amount divided by
the original quantity (a bug present in the old script). Pair evidence is retained.
"""
from collections import defaultdict, deque
from decimal import Decimal

from portfolio.domain.analytics import ZERO, Issue, Report, ReportRow
from portfolio.domain.models import exact_text
from portfolio.domain.trade_time import event_order_time
from portfolio.engine.holdings import label
from portfolio.engine.replay import action_applies

MICRO_COLUMNS = ("月份", "渠道", "分类", "Ticker", "微操次数", "微操数量", "微操均买价($)", "微操均卖价($)", "每股价差($)", "买入金额($)", "卖出金额($)", "微操收益($)", "首笔微操时间", "最近微操时间")
PAIR_COLUMNS = ("月份", "渠道", "Ticker", "微操序号", "模式", "配对数量", "买入金额($)", "卖出金额($)", "分摊费用($)", "微操收益($)", "买入记录ID", "卖出记录ID", "买入时间", "卖出时间")


def scalping(snapshot, *, policy="EXCEL_COMPAT", cancel=None):
    if policy not in ("EXCEL_COMPAT", "FIFO"):
        raise ValueError("未知微操配对策略。")
    groups = defaultdict(list)
    for event in snapshot.events:
        groups[(event.key, event.day[:7])].append(event)
    by_key = defaultdict(list)
    for event in snapshot.events:
        by_key[event.key].append(event)
    month_opening = {}
    for key, history in by_key.items():
        holdings = ZERO
        timeline = [(e.day, event_order_time(e), 1, e.sequence, "TRADE", e) for e in history]
        relevant = [a for a in snapshot.actions if action_applies(a, key, snapshot)]
        timeline += [(a["effective_date"], a["effective_date"] + " 00:00:00", 0, a["id"], "ACTION", a) for a in relevant]
        timeline += [(mo + "-01", mo + "-01 00:00:00", -1, 0, "OPEN", mo) for mo in {e.day[:7] for e in history}]
        for _, _, _, _, typ, obj in sorted(timeline, key=lambda r: r[:4]):
            if typ == "OPEN":
                month_opening[(key, obj)] = holdings
            elif typ == "ACTION":
                holdings = holdings * Decimal(obj["ratio_numerator"]) / Decimal(obj["ratio_denominator"])
            else:
                holdings += obj.quantity if obj.incoming else -obj.quantity
    pairs, tags, summary, profit_map = [], defaultdict(list), [], defaultdict(lambda: ZERO)
    for (key, month), trades in sorted(groups.items(), key=lambda item: (item[0][1], str(item[0][0]))):
        if cancel and cancel.is_set():
            raise ValueError("计算已取消。")
        buys, sells, counter, holdings = deque(), deque(), 0, month_opening[(key, month)]
        # Month opening quantity is replayed in chronological split units, never
        # sum(all historical quantities) * product(all historical split ratios).
        actions = [a for a in snapshot.actions if action_applies(a, key, snapshot)]
        timeline = [(e.day, event_order_time(e), 1, e.sequence, "TRADE", e) for e in trades]
        timeline += [(a["effective_date"], a["effective_date"] + " 00:00:00", 0, a["id"], "ACTION", a) for a in actions if a["effective_date"][:7] == month]
        group_pairs = []
        for _, _, _, _, typ, obj in sorted(timeline, key=lambda r: r[:4]):
            if cancel and cancel.is_set():
                raise ValueError("计算已取消。")
            if typ == "ACTION":
                factor = Decimal(obj["ratio_numerator"]) / Decimal(obj["ratio_denominator"])
                holdings *= factor
                for lot in [*buys, *sells]:
                    lot["remaining"] *= factor
                    lot["factor"] *= factor
                continue
            event = obj
            remaining = event.quantity
            if event.kind != "MARKET_TRADE" or event.amount is None:
                holdings += event.quantity if event.incoming else -event.quantity
                tags[event.id].append(("权益变动（不配微操）", event.quantity))
                continue
            opposite = sells if event.incoming else buys
            matched = ZERO
            while opposite and remaining > ZERO:
                index = 0 if event.incoming or policy == "FIFO" else -1
                lot = opposite[index]
                quantity = min(remaining, lot["remaining"])
                original_quantity = quantity / lot["factor"]
                other = lot["event"]
                amount_now = event.amount * quantity / event.quantity
                amount_other = other.amount * original_quantity / other.quantity
                buy, sell = (event, other) if event.incoming else (other, event)
                buy_amount, sell_amount = (amount_now, amount_other) if event.incoming else (amount_other, amount_now)
                broker = snapshot.accounts[event.account_id]["broker_code"]
                fee = (quantity / event.quantity + original_quantity / other.quantity) if broker in ("BBAE", "LONGBRIDGE") else ZERO
                counter += 1
                tags[event.id].append((f"微操{counter}", quantity))
                tags[other.id].append((f"微操{counter}", original_quantity))
                ch, cat, ticker = label(snapshot, key)
                meta = {"account_id": key[1], "instrument_id": key[2], "person_id": key[0], "policy": policy,
                        "source_record_ids": (buy.source_id, sell.source_id), "fee_basis": "每张订单 1 USD 按原始股数分摊" if fee else "沿用成交金额，不追加固定订单费"}
                row = ReportRow((month, ch, ticker, counter, "先卖后买" if event.incoming else "先买后卖", quantity,
                                 buy_amount, sell_amount, fee, sell_amount - buy_amount - fee, buy.id, sell.id, buy.time, sell.time), meta)
                pairs.append(row)
                group_pairs.append(row)
                remaining -= quantity
                matched += quantity
                lot["remaining"] -= quantity
                if lot["remaining"] == ZERO:
                    opposite.popleft() if index == 0 else opposite.pop()
            eligible = remaining if event.incoming else min(remaining, max(holdings - matched, ZERO))
            if eligible > ZERO:
                target = buys if event.incoming else sells
                target.append({"event": event, "remaining": eligible, "factor": Decimal(1)})
            if remaining > eligible:
                tags[event.id].append(("异常卖出", remaining - eligible))
            holdings += event.quantity if event.incoming else -event.quantity
        for number, lot in enumerate(buys, 1):
            tags[lot["event"].id].append((f"持仓{number}", lot["remaining"] / lot["factor"]))
        for lot in sells:
            tags[lot["event"].id].append(("减仓", lot["remaining"] / lot["factor"]))
        if group_pairs:
            qty = sum((r.values[5] for r in group_pairs), ZERO)
            buy_amt = sum((r.values[6] for r in group_pairs), ZERO)
            sell_amt = sum((r.values[7] for r in group_pairs), ZERO)
            profit = sum((r.values[9] for r in group_pairs), ZERO)
            ch, cat, ticker = label(snapshot, key)
            summary.append(ReportRow((month, ch, cat, ticker, len(group_pairs), qty, buy_amt / qty, sell_amt / qty,
                                      (sell_amt - buy_amt) / qty, buy_amt, sell_amt, profit,
                                      min(min(r.values[12:14]) for r in group_pairs), max(max(r.values[12:14]) for r in group_pairs)),
                                     {**group_pairs[0].metadata, "pair_count": len(group_pairs)}))
            profit_map[(month, key[1], key[2])] += profit
    rendered = {id: " / ".join(f"{name}（{exact_text(qty)}股）" for name, qty in parts) for id, parts in tags.items()}
    if any(snapshot.actions) and snapshot.prices:
        snapshot.issues.append(Issue("DQ018", "INFO", "微操为月内分析切片；兼容策略先买后卖按最近买入、先卖后买按 FIFO。配对采用原始金额比例，保留旧程序残额分摊缺陷的差异，不叠加到组合收益。"))
    return Report("每月微操收益", MICRO_COLUMNS, tuple(summary)), Report("微操配对证据", PAIR_COLUMNS, tuple(pairs)), rendered, dict(profit_map)
