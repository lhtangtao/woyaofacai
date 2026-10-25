from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from portfolio.domain.analytics import ZERO, Issue, Report, ReportRow, ratio, strict_sum
from portfolio.engine.holdings import grouped_positions, label
from portfolio.engine.replay import opening_positions, replay

DAILY_COLUMNS = ("日期", "渠道", "分类", "Ticker", "日末状态", "期初数量", "当日买入数量", "当日卖出数量", "日末持仓数量", "成本均价($)", "日末收盘价($)", "日末市值($)", "日末市值占比", "持仓盈亏比例", "回本涨幅", "期初市值($)", "当日买入金额($)", "当日卖出金额($)", "当日操作笔数", "当日收益($)", "累计买入金额($)", "累计卖出金额($)", "累计净投入($)")
MONTHLY_COLUMNS = ("月份", "渠道", "分类", "Ticker", "月末状态", "期初数量", "本月买入数量", "本月卖出数量", "月末持仓数量", "成本均价($)", "月末收盘价($)", "月末市值($)", "月末市值占比", "持仓盈亏比例", "回本涨幅", "期初市值($)", "本月买入金额($)", "本月卖出金额($)", "本月微操收益($)", "本月操作笔数", "本月收益($)", "累计买入金额($)", "累计卖出金额($)", "累计净投入($)")
NET_COLUMNS = ("月份", "渠道", "分类", "Ticker", "月初持仓数量", "月末持仓数量", "本月买入金额($)", "本月卖出金额($)", "本月净投入($)", "月末累计净投入($)")


def performance(snapshot, micro_profit=None, *, cancel=None):
    micro_profit = micro_profit or {}
    if not snapshot.events and not snapshot.baselines:
        return {}, {}, {}, []
    first = min([e.day for e in snapshot.events] + [b["baseline_date"] for b in snapshot.baselines if b["baseline_role"] == "OPENING"], default=snapshot.scope.as_of.isoformat())
    trading_days = sorted({p.day for p in snapshot.prices if p.kind in ("DAILY_CLOSE", "ESTIMATE_CLOSE") and first <= p.day <= snapshot.scope.as_of.isoformat()})

    def roll_to_trading_day(day_str: str) -> str:
        if not trading_days or day_str in trading_days:
            return day_str
        next_day = next((d for d in trading_days if d >= day_str), None)
        return next_day if next_day is not None else day_str

    effective_events = tuple(
        replace(e, day=roll_to_trading_day(e.day)) if roll_to_trading_day(e.day) != e.day else e
        for e in snapshot.events
    )
    days = sorted(set(trading_days) | {e.day for e in effective_events})
    # Missing price rows cannot move trades or corporate actions in the ledger.
    # Only the daily cash-flow display rolls to the next available valuation day;
    # captures include the original chronological events through that date.
    positions, traces, captures = replay(snapshot, capture_days=days, cancel=cancel)
    daily_prices = {(p.instrument_id, p.day): p for p in sorted(snapshot.prices, key=lambda p: p.kind == "DAILY_CLOSE") if p.kind in ("DAILY_CLOSE", "ESTIMATE_CLOSE")}
    events_by_day = defaultdict(list)
    for event in effective_events:
        events_by_day[event.day].append(event)
    initial, cutoffs = opening_positions(snapshot)
    previous, initial_values = {}, {}
    previous_quotes = {}
    for key, position in grouped_positions(initial).items():
        underlying = [b for b in snapshot.baselines if b["baseline_role"] == "OPENING" and b["account_id"] == key[1] and b["instrument_id"] == key[2]]
        from portfolio.domain.models import from_e8
        initial_values[key] = (position.quantity, strict_sum(from_e8(b["market_value_e8"]) for b in underlying))
    rows = []
    for day in days:
        if cancel and cancel.is_set():
            raise ValueError("计算已取消。")
        state = grouped_positions(captures[day])
        changes = defaultdict(list)
        for e in events_by_day[day]:
            if e.day > cutoffs.get(e.key, ""):
                changes[(None, e.account_id, e.instrument_id)].append(e)
        values = {}
        for key, position in state.items():
            relevant_cutoffs = [value for k, value in cutoffs.items() if k[1:] == key[1:]]
            if relevant_cutoffs and max(relevant_cutoffs) == day and not changes[key]:
                previous[key] = initial_values.get(key, (position.quantity, values[key][1]))
                continue
            price = daily_prices.get((key[2], day))
            if price and price.basis not in ("RAW", "UNKNOWN") and any(a["instrument_id"] == key[2] for a in snapshot.actions):
                price = None
            # Money2 Excel compatibility: retain the signed replay balance for
            # later buys to repay, but value debt shares at zero in this report.
            values[key] = (price, position.quantity * price.value if price and position.quantity > ZERO else ZERO if position.quantity <= ZERO else None)
            if "成交粒度冲突" in position.warnings:
                values[key] = (price, None)
        total = strict_sum(value for key, (_, value) in values.items() if state[key].quantity > ZERO)
        for key, position in state.items():
            prior_quantity, opening_value = previous.get(key, (ZERO, ZERO))
            opening_day, opening_status = previous_quotes.get(key, (None, None))
            operations = changes[key]
            if prior_quantity <= ZERO and position.quantity <= ZERO and not operations:
                continue
            price, ending_value = values[key]
            buys = [e for e in operations if e.incoming]
            sells = [e for e in operations if not e.incoming]
            buy_amt, sell_amt = strict_sum(e.amount for e in buys), strict_sum(e.amount for e in sells)
            profit = ending_value - opening_value - buy_amt + sell_amt if all(v is not None for v in (ending_value, opening_value, buy_amt, sell_amt)) else None
            if price is None and position.quantity > ZERO:
                snapshot.issues.append(Issue("DQ010", "WARN", "缺少该日收盘价；当日收益及受影响合计保持缺失。", day, account_id=key[1], instrument_id=key[2], source_ids=tuple(e.source_id for e in operations)))
            ch, cat, ticker = label(snapshot, key)
            pnl = ending_value - position.net if ending_value is not None and position.net is not None else None
            recovery = (position.cost - price.value) / price.value if price and position.cost is not None and position.cost > price.value else None
            meta = {"account_id": key[1], "instrument_id": key[2], "source_record_ids": tuple(position.source_ids),
                    "price_source_record_id": price.source_id if price else None, "price_basis": price.basis if price else None,
                    "valuation_date": day, "valuation_source": price.kind if price else None,
                    "historical_estimate": bool(price and price.kind == "ESTIMATE_CLOSE" or opening_status in ("CLOSE_ESTIMATE", "PARTIAL_ESTIMATE")),
                    "opening_day": opening_day, "opening_status": opening_status,
                    "price_status": price.status if price else None,
                    "completeness": "MISSING_PRICE_OR_COST" if profit is None else "REVIEW_REQUIRED" if position.warnings else "COMPLETE",
                    "currency": snapshot.scope.reporting_currency,
                    "raw_opening_quantity": prior_quantity, "raw_ending_quantity": position.quantity,
                    "quantity_valuation_policy": "Money2兼容：负数股数按0计市值，欠股仍由后续买入抵扣，非做空市值模型。"}
            untrusted = "成交粒度冲突" in position.warnings
            rows.append(ReportRow((day, ch, cat, ticker, "成交待核对" if untrusted else "持有中" if position.quantity > ZERO else "欠股按零估值" if position.quantity < ZERO else "当日已清仓",
                                  None if untrusted else max(prior_quantity, ZERO), None if untrusted else sum((e.quantity for e in buys), ZERO), None if untrusted else sum((e.quantity for e in sells), ZERO), None if untrusted else max(position.quantity, ZERO),
                                  position.cost, price.value if price else None, ending_value, ratio(ending_value, total),
                                  ratio(pnl, position.net) if position.quantity > ZERO else None, recovery, opening_value, buy_amt, sell_amt,
                                  len(operations), profit, position.buy, position.sell, position.net), meta))
            previous[key] = (position.quantity, ending_value)
            previous_quotes[key] = (day, price.status if price else None)
    by_month = defaultdict(list)
    for row in rows:
        by_month[(row.values[0][:7], row.metadata["account_id"], row.metadata["instrument_id"])].append(row)
    monthly, net, full_net = [], [], []
    for (month, account, instrument), group in sorted(by_month.items()):
        first_row, last_row = group[0], group[-1]
        f, last = first_row.values, last_row.values
        buy, sell = strict_sum(r.values[16] for r in group), strict_sum(r.values[17] for r in group)
        gain = strict_sum(r.values[19] for r in group)
        net_amount = buy - sell if buy is not None and sell is not None else None
        meta = {**last_row.metadata, "month": month, "completeness": "INCOMPLETE" if gain is None else last_row.metadata["completeness"]}
        monthly.append(ReportRow((month, *last[1:5], f[5], strict_sum(r.values[6] for r in group), strict_sum(r.values[7] for r in group),
                                  *last[8:15], f[15], buy, sell, micro_profit.get((month, account, instrument), ZERO),
                                  sum(r.values[18] for r in group), gain, *last[20:23]), meta))
        capital = ReportRow((month, *last[1:4], f[5], last[8], buy, sell, net_amount, last[22]), meta)
        full_net.append(capital)
        if f[5] is None or last[8] is None or f[5] > ZERO or last[8] > ZERO:
            net.append(capital)
        direct = last[11] - f[15] - buy + sell if all(v is not None for v in (last[11], f[15], buy, sell)) else None
        if gain is not None and direct is not None and abs(gain - direct) > Decimal("0.00000001"):
            snapshot.issues.append(Issue("DQ018", "WARN", "每日收益合计与月初/月末公式不一致，需核对开账/缺日。", month, account_id=account, instrument_id=instrument))
    return positions, traces, {"每日持仓收益": Report("每日持仓收益", DAILY_COLUMNS, tuple(rows)),
                                "每月持仓收益": Report("每月持仓收益", MONTHLY_COLUMNS, tuple(monthly)),
                                "月度净投入明细": Report("月度净投入明细", NET_COLUMNS, tuple(net)),
                                "完整交易净投入": Report("完整交易净投入", NET_COLUMNS, tuple(full_net))}, days
