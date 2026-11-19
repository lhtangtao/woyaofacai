"""Monthly two-way matching with explicit Excel LIFO-buy/FIFO-sell policy.

Amounts are allocated from the original lot, not from a residual amount divided by
the original quantity (a bug present in the old script). Pair evidence is retained.
"""
from bisect import bisect_right
from collections import defaultdict, deque
from datetime import date, timedelta
from decimal import Decimal

from portfolio.domain.analytics import ZERO, Issue, Report, ReportRow
from portfolio.domain.models import exact_text, from_e8
from portfolio.domain.trade_time import event_order_time
from portfolio.engine.holdings import label
from portfolio.engine.replay import action_applies

MICRO_COLUMNS = ("月份", "渠道", "分类", "Ticker", "微操次数", "微操数量", "微操均买价($)", "微操均卖价($)", "每股价差($)", "买入金额($)", "卖出金额($)", "微操收益($)", "首笔微操时间", "最近微操时间")
PAIR_COLUMNS = ("月份", "渠道", "Ticker", "微操序号", "模式", "配对数量", "买入金额($)", "卖出金额($)", "分摊费用($)", "微操收益($)", "买入记录ID", "卖出记录ID", "买入时间", "卖出时间")
SHORT_PAIR_COLUMNS = ("归属月份", "渠道", "Ticker", "微操序号", "模式", "持仓交易日", "配对数量", "买入金额($)", "卖出金额($)", "分摊费用($)", "微操净收益($)", "是否跨月", "买入记录ID", "卖出记录ID", "买入时间", "卖出时间")
GROSS_AMOUNT_BROKERS = ("BBAE", "LONGBRIDGE")


def scalping(snapshot, *, policy="EXCEL_COMPAT", cancel=None):
    from portfolio.services.derived_engine import scalping_cached
    return scalping_cached(snapshot, policy, cancel, _scalping)


def _scalping(snapshot, *, policy="EXCEL_COMPAT", cancel=None):
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
    month_opening.update(snapshot.extras.get('micro_opening', {}))
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


def _trading_days_between(day1: str, day2: str, trading_days: tuple[str, ...] = ()) -> int:
    """计算两个交易日之间间隔的交易日数（同日为 0，次交易日为 1）。"""
    if not day1 or not day2 or day2 <= day1:
        return 0
    if trading_days and trading_days[0] <= day1 and day2 <= trading_days[-1]:
        return max(0, bisect_right(trading_days, day2) - bisect_right(trading_days, day1))
    d1 = date.fromisoformat(day1[:10])
    d2 = date.fromisoformat(day2[:10])
    total_days = (d2 - d1).days
    full_weeks, rem = divmod(total_days, 7)
    weekdays = full_weeks * 5
    cur = d1 + timedelta(days=full_weeks * 7)
    for _ in range(rem):
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            weekdays += 1
    return max(0, weekdays)


def _allocated_real_fee(event, matched_qty: Decimal, broker_code: str) -> tuple[Decimal, Decimal | None, bool]:
    """按匹配股数占比分摊真实已核实手续费，避免对已含费净额渠道重复扣费。

    返回: (deducted_fee, reported_fee, fee_verified)
    - deducted_fee: 需从 sell_amount - buy_amount 中扣减的费用（仅总价口径券商扣减，净额口径券商为 0）
    - reported_fee: 账本已记录的真实手续费分摊额（缺失时为 None）
    - fee_verified: 该笔订单费用是否已核实
    """
    meta = event.metadata if isinstance(event.metadata, dict) else {}
    fees_e8 = meta.get("fees_e8")
    raw_fee = from_e8(fees_e8) if fees_e8 is not None else None
    ratio = matched_qty / event.quantity if event.quantity > ZERO else ZERO
    reported = (raw_fee * ratio) if raw_fee is not None else None
    if str(broker_code).upper() in GROSS_AMOUNT_BROKERS:
        if reported is not None:
            return reported, reported, True
        return ZERO, None, False
    return ZERO, reported, (raw_fee is not None)


def _opening_holdings_map(snapshot) -> dict[tuple, Decimal]:
    """读取开账期初持仓（若有），确保初期反T卖出判断准确。"""
    baselines = getattr(snapshot, "baselines", None) or ()
    scope = getattr(snapshot, "scope", None)
    if not baselines or scope is None:
        return {}
    from portfolio.domain.models import Ledger
    is_person = scope.ledger == Ledger.PERSON
    expected_scope = "PERSON_ACCOUNT" if is_person else "ACCOUNT"
    grouped = defaultdict(list)
    for b in baselines:
        if b.get("baseline_role") != "OPENING" or b.get("instrument_id") is None:
            continue
        if b.get("scope_type") != expected_scope:
            continue
        key = (b.get("person_id") if is_person else None, b["account_id"], b["instrument_id"])
        grouped[key].append(b)
    result: dict[tuple, Decimal] = {}
    for key, rows in grouped.items():
        latest = max(rows, key=lambda r: (r["baseline_date"], r["id"]))
        qty_e8 = latest.get("quantity_e8")
        if qty_e8 is not None:
            result[key] = from_e8(qty_e8)
    return result


def short_term_scalping(snapshot, *, max_days: int = 5, cancel=None) -> dict:
    """实战短线微操引擎（支持跨月、交易日窗口、正T/反T严格拆分、未配对纠偏与真实费用）。

    核心规则：
    1. 同一 (person_id, account_id, instrument_id) 在连续时间轴上配对，允许跨自然月。
    2. 仅配对买卖间隔 <= max_days 个交易日的交易（0=日内T+0，5=超短线，20=短波段）。
    3. 正T（先买后卖）与反T（先卖后买）均优先 LIFO 配对最近短线单，避免错动长线底仓。
    4. 超过 max_days 仍未平仓的买入记为「正T转长持」，超过 max_days 未买回的卖出记为「反T未接回」。
    5. 收益归属平仓/接回日所在月份；跨拆合股按比例同步折算股数与单价。
    6. 手续费优先采用已核实真实手续费按股数比例分摊；缺失时显式标记含未核实费用。
    """
    if max_days < 0:
        raise ValueError("微操交易日窗口不能为负数。")
    trading_days = tuple(sorted(getattr(snapshot, "extras", {}).get("trading_days", ()) or ()))
    scope = getattr(snapshot, "scope", None)
    as_of_day = scope.as_of.isoformat() if scope is not None and getattr(scope, "as_of", None) else ""
    if not as_of_day and snapshot.events:
        as_of_day = max(e.day for e in snapshot.events)

    opening_map = _opening_holdings_map(snapshot)
    by_key = defaultdict(list)
    for event in snapshot.events:
        by_key[event.key].append(event)

    pair_records: list[dict] = []
    pair_rows: list[ReportRow] = []
    expired_buys: list[dict] = []
    expired_sells: list[dict] = []
    pending_buys: list[dict] = []
    pending_sells: list[dict] = []

    for key, history in sorted(by_key.items(), key=lambda item: str(item[0])):
        if cancel and cancel.is_set():
            raise ValueError("计算已取消。")
        ch, cat, ticker = label(snapshot, key)
        broker_code = snapshot.accounts[key[1]]["broker_code"]
        holdings = opening_map.get(key, ZERO)
        buys: list[dict] = []
        sells: list[dict] = []
        counter = 0

        actions = [a for a in snapshot.actions if action_applies(a, key, snapshot)]
        timeline = [(e.day, event_order_time(e), 1, e.sequence, "TRADE", e) for e in history]
        timeline += [(a["effective_date"], a["effective_date"] + " 00:00:00", 0, a["id"], "ACTION", a) for a in actions]

        def _expire(ref_day: str):
            nonlocal buys, sells
            if not ref_day:
                return
            keep_b = []
            for lot in buys:
                if _trading_days_between(lot["open_day"], ref_day, trading_days) > max_days:
                    ev = lot["event"]
                    orig_qty = lot["remaining"] / lot["factor"]
                    orig_amt = ev.amount * orig_qty / ev.quantity if ev.quantity > ZERO and ev.amount is not None else ZERO
                    expired_buys.append({
                        "month": lot["open_day"][:7],
                        "open_day": lot["open_day"],
                        "channel": ch,
                        "category": cat,
                        "ticker": ticker,
                        "qty": orig_qty,
                        "amount": orig_amt,
                        "event_id": ev.id,
                        "time": ev.time,
                        "account_id": key[1],
                        "instrument_id": key[2],
                        "person_id": key[0],
                    })
                else:
                    keep_b.append(lot)
            buys = keep_b

            keep_s = []
            for lot in sells:
                if _trading_days_between(lot["open_day"], ref_day, trading_days) > max_days:
                    ev = lot["event"]
                    orig_qty = lot["remaining"] / lot["factor"]
                    orig_amt = ev.amount * orig_qty / ev.quantity if ev.quantity > ZERO and ev.amount is not None else ZERO
                    expired_sells.append({
                        "month": lot["open_day"][:7],
                        "open_day": lot["open_day"],
                        "channel": ch,
                        "category": cat,
                        "ticker": ticker,
                        "qty": orig_qty,
                        "amount": orig_amt,
                        "event_id": ev.id,
                        "time": ev.time,
                        "account_id": key[1],
                        "instrument_id": key[2],
                        "person_id": key[0],
                    })
                else:
                    keep_s.append(lot)
            sells = keep_s

        for _, _, _, _, typ, obj in sorted(timeline, key=lambda r: r[:4]):
            if cancel and cancel.is_set():
                raise ValueError("计算已取消。")
            if typ == "ACTION":
                factor = Decimal(obj["ratio_numerator"]) / Decimal(obj["ratio_denominator"])
                holdings *= factor
                for lot in (*buys, *sells):
                    lot["remaining"] *= factor
                    lot["factor"] *= factor
                continue

            event = obj
            _expire(event.day)
            remaining = event.quantity
            if event.kind != "MARKET_TRADE" or event.amount is None or remaining <= ZERO:
                holdings += event.quantity if event.incoming else -event.quantity
                continue

            opposite = sells if event.incoming else buys
            matched = ZERO
            while opposite and remaining > ZERO:
                lot = opposite[-1]
                quantity = min(remaining, lot["remaining"])
                original_quantity = quantity / lot["factor"]
                other = lot["event"]
                amount_now = event.amount * quantity / event.quantity
                amount_other = other.amount * original_quantity / other.quantity
                buy, sell = (event, other) if event.incoming else (other, event)
                buy_amount, sell_amount = (amount_now, amount_other) if event.incoming else (amount_other, amount_now)

                deduct_now, rep_now, ver_now = _allocated_real_fee(event, quantity, broker_code)
                deduct_other, rep_other, ver_other = _allocated_real_fee(other, original_quantity, broker_code)
                deducted_fee = deduct_now + deduct_other
                reported_fee = (rep_now + rep_other) if (rep_now is not None and rep_other is not None) else None
                fee_verified = ver_now and ver_other

                net_pnl = sell_amount - buy_amount - deducted_fee
                hold_days = _trading_days_between(lot["open_day"], event.day, trading_days)
                mode_code = "REV_T" if event.incoming else "POS_T"
                mode_label = "反T(先卖后买)" if event.incoming else "正T(先买后卖)"
                close_month = event.day[:7]
                cross_month = (other.day[:7] != event.day[:7])

                counter += 1
                pair_info = {
                    "month": close_month,
                    "close_day": event.day,
                    "open_day": lot["open_day"],
                    "channel": ch,
                    "category": cat,
                    "ticker": ticker,
                    "seq": counter,
                    "mode": mode_code,
                    "mode_label": mode_label,
                    "hold_days": hold_days,
                    "qty": quantity,
                    "buy_amount": buy_amount,
                    "sell_amount": sell_amount,
                    "fee": deducted_fee,
                    "reported_fee": reported_fee,
                    "fee_verified": fee_verified,
                    "pnl": net_pnl,
                    "cross_month": cross_month,
                    "buy_id": buy.id,
                    "sell_id": sell.id,
                    "buy_time": buy.time,
                    "sell_time": sell.time,
                    "account_id": key[1],
                    "instrument_id": key[2],
                    "person_id": key[0],
                    "source_record_ids": (buy.source_id, sell.source_id),
                }
                pair_records.append(pair_info)
                pair_rows.append(
                    ReportRow(
                        (
                            close_month, ch, ticker, counter, mode_label, hold_days, quantity,
                            buy_amount, sell_amount, deducted_fee, net_pnl, "是" if cross_month else "否",
                            buy.id, sell.id, buy.time, sell.time,
                        ),
                        pair_info,
                    )
                )
                remaining -= quantity
                matched += quantity
                lot["remaining"] -= quantity
                if lot["remaining"] == ZERO:
                    opposite.pop()

            eligible = remaining if event.incoming else min(remaining, max(holdings - matched, ZERO))
            if eligible > ZERO:
                target = buys if event.incoming else sells
                target.append({"event": event, "remaining": eligible, "factor": Decimal(1), "open_day": event.day})
            holdings += event.quantity if event.incoming else -event.quantity

        # 截至 as_of 结算超时队列与仍在观察期的挂单
        _expire(as_of_day)
        for lot in buys:
            ev = lot["event"]
            orig_qty = lot["remaining"] / lot["factor"]
            orig_amt = ev.amount * orig_qty / ev.quantity if ev.quantity > ZERO and ev.amount is not None else ZERO
            pending_buys.append({
                "month": lot["open_day"][:7], "open_day": lot["open_day"],
                "channel": ch, "category": cat, "ticker": ticker,
                "qty": orig_qty, "amount": orig_amt, "event_id": ev.id, "time": ev.time,
                "account_id": key[1], "instrument_id": key[2], "person_id": key[0],
            })
        for lot in sells:
            ev = lot["event"]
            orig_qty = lot["remaining"] / lot["factor"]
            orig_amt = ev.amount * orig_qty / ev.quantity if ev.quantity > ZERO and ev.amount is not None else ZERO
            pending_sells.append({
                "month": lot["open_day"][:7], "open_day": lot["open_day"],
                "channel": ch, "category": cat, "ticker": ticker,
                "qty": orig_qty, "amount": orig_amt, "event_id": ev.id, "time": ev.time,
                "account_id": key[1], "instrument_id": key[2], "person_id": key[0],
            })

    # 按 (归属月份, 渠道, 分类, Ticker) 汇总统计
    exp_buy_by_group = defaultdict(list)
    for item in expired_buys:
        exp_buy_by_group[(item["month"], item["channel"], item["category"], item["ticker"])].append(item)
    exp_sell_by_group = defaultdict(list)
    for item in expired_sells:
        exp_sell_by_group[(item["month"], item["channel"], item["category"], item["ticker"])].append(item)

    pairs_by_group = defaultdict(list)
    for rec in pair_records:
        pairs_by_group[(rec["month"], rec["channel"], rec["category"], rec["ticker"])].append(rec)

    summary_rows: list[ReportRow] = []
    for (month, ch, cat, ticker), grp in sorted(pairs_by_group.items()):
        qty = sum((p["qty"] for p in grp), ZERO)
        buy_amt = sum((p["buy_amount"] for p in grp), ZERO)
        sell_amt = sum((p["sell_amount"] for p in grp), ZERO)
        deducted_fee = sum((p["fee"] for p in grp), ZERO)
        pnl = sum((p["pnl"] for p in grp), ZERO)

        pos_pairs = [p for p in grp if p["mode"] == "POS_T"]
        rev_pairs = [p for p in grp if p["mode"] == "REV_T"]
        win_pairs = [p for p in grp if p["pnl"] > ZERO]
        loss_pairs = [p for p in grp if p["pnl"] < ZERO]
        flat_count = len(grp) - len(win_pairs) - len(loss_pairs)

        win_rate = Decimal(len(win_pairs)) / Decimal(len(grp)) if grp else ZERO
        avg_win = (sum((p["pnl"] for p in win_pairs), ZERO) / Decimal(len(win_pairs))) if win_pairs else None
        avg_loss = (sum((abs(p["pnl"]) for p in loss_pairs), ZERO) / Decimal(len(loss_pairs))) if loss_pairs else None
        pl_ratio = (avg_win / avg_loss) if (avg_win is not None and avg_loss is not None and avg_loss > ZERO) else None
        avg_hold_days = sum((Decimal(p["hold_days"]) for p in grp), ZERO) / Decimal(len(grp)) if grp else ZERO
        cross_month_count = sum(1 for p in grp if p["cross_month"])
        fee_unverified_count = sum(1 for p in grp if not p["fee_verified"])
        reported_fees = [p["reported_fee"] for p in grp if p["reported_fee"] is not None]
        reported_fee_sum = sum(reported_fees, ZERO) if len(reported_fees) == len(grp) else None

        eb = exp_buy_by_group.get((month, ch, cat, ticker), [])
        es = exp_sell_by_group.get((month, ch, cat, ticker), [])

        meta = {
            "account_id": grp[0]["account_id"],
            "instrument_id": grp[0]["instrument_id"],
            "person_id": grp[0]["person_id"],
            "policy": f"SHORT_{max_days}",
            "max_days": max_days,
            "pair_count": len(grp),
            "pos_t_count": len(pos_pairs),
            "pos_t_pnl": sum((p["pnl"] for p in pos_pairs), ZERO),
            "rev_t_count": len(rev_pairs),
            "rev_t_pnl": sum((p["pnl"] for p in rev_pairs), ZERO),
            "win_count": len(win_pairs),
            "loss_count": len(loss_pairs),
            "flat_count": flat_count,
            "win_rate": win_rate,
            "profit_loss_ratio": pl_ratio,
            "avg_hold_days": avg_hold_days,
            "deducted_fee": deducted_fee,
            "reported_fee": reported_fee_sum,
            "fee_unverified_count": fee_unverified_count,
            "cross_month_count": cross_month_count,
            "expired_buy_count": len(eb),
            "expired_buy_amount": sum((x["amount"] for x in eb), ZERO),
            "expired_sell_count": len(es),
            "expired_sell_amount": sum((x["amount"] for x in es), ZERO),
            "pairs": tuple(grp),
        }
        summary_rows.append(
            ReportRow(
                (
                    month, ch, cat, ticker, len(grp), qty,
                    buy_amt / qty if qty > ZERO else ZERO,
                    sell_amt / qty if qty > ZERO else ZERO,
                    (sell_amt - buy_amt) / qty if qty > ZERO else ZERO,
                    buy_amt, sell_amt, pnl,
                    min(min(p["buy_time"], p["sell_time"]) for p in grp),
                    max(max(p["buy_time"], p["sell_time"]) for p in grp),
                ),
                meta,
            )
        )

    return {
        "mode": f"SHORT_{max_days}",
        "max_days": max_days,
        "report": Report(f"短线微操收益（≤{max_days}交易日）" if max_days > 0 else "短线微操收益（日内T+0）", MICRO_COLUMNS, tuple(summary_rows)),
        "pairs_report": Report("短线微操配对证据", SHORT_PAIR_COLUMNS, tuple(pair_rows)),
        "pairs": tuple(pair_records),
        "expired_buys": tuple(expired_buys),
        "expired_sells": tuple(expired_sells),
        "pending_buys": tuple(pending_buys),
        "pending_sells": tuple(pending_sells),
    }
