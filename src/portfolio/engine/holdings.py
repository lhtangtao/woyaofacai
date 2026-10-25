from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from portfolio.domain.analytics import ZERO, Issue, Position, Report, ReportRow, ratio, strict_sum

HOLDINGS_COLUMNS = ("渠道", "分类", "Ticker", "持仓数量", "当前价($)", "持仓市值($)", "持仓成本价($)", "累计买入金额($)", "累计卖出金额($)", "累计净投入($)", "持仓盈亏金额($)", "持仓盈亏比例", "回本涨幅", "持仓市值占比")
STOCK_COLUMNS = ("渠道", "分类", "Ticker", "当前状态", "当前持仓数量", "当前价/清仓价($)", "当前市值($)", "持仓成本价($)", "累计买入金额($)", "累计卖出金额($)", "累计净投入($)", "持仓盈亏金额($)", "持仓盈亏比例", "回本涨幅", "持仓市值占比", "累计清仓收益($)", "累计微操收益($)", "总收益贡献($)")
CLOSED_COLUMNS = ("渠道", "分类", "Ticker", "历史总买入数量", "历史总卖出数量", "历史总买入金额($)", "历史总卖出金额($)", "清仓收益($)", "操作笔数", "买入均价($)", "卖出均价($)", "首次买入时间", "最终清仓时间")

MIN_OPEN_QUANTITY = Decimal('0.5')


def display_quantity(quantity):
    return ZERO if ZERO <= quantity < MIN_OPEN_QUANTITY else quantity


def grouped_positions(positions):
    groups = defaultdict(list)
    for position in positions.values():
        groups[position.key[1:]].append(position)
    result = {}
    for key, items in groups.items():
        p = Position(
            key=(None, *key),
            quantity=sum(item.quantity for item in items),
            buy_quantity=sum(item.buy_quantity for item in items),
            sell_quantity=sum(item.sell_quantity for item in items),
            buy=strict_sum(item.buy for item in items),
            sell=strict_sum(item.sell for item in items),
            count=sum(item.count for item in items),
            first=min(i.first for i in items),
            last=max(i.last for i in items),
            last_price=max(items, key=lambda item: item.last).last_price,
            source_ids=[s for i in items for s in i.source_ids],
            warnings=set().union(*(i.warnings for i in items)),
        )
        result[p.key] = p
    return result


def current_price(snapshot, instrument_id):
    day = snapshot.scope.as_of.isoformat()
    candidates = [p for p in snapshot.prices if p.instrument_id == instrument_id and p.day <= day and p.kind in ("LATEST", "DAILY_CLOSE", "ESTIMATE_CLOSE")]
    if not candidates:
        return None
    # Historical dates use exact daily close; never today's quote, never a future close.
    if snapshot.scope.as_of < datetime.now(timezone.utc).date():
        exact = [p for p in candidates if p.kind in ("DAILY_CLOSE", "ESTIMATE_CLOSE") and p.day == day]
        if exact:
            return max(exact, key=lambda p: (p.kind == "DAILY_CLOSE", p.source_id))
    latest = [p for p in candidates if p.kind == "LATEST" and p.quoted_at]
    fresh = []
    for price in latest:
        try:
            quote_time = datetime.fromisoformat(price.quoted_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - quote_time).total_seconds()
            if 0 <= age <= snapshot.extras.get("quote_max_age_hours", 72) * 3600 and price.day == day:
                fresh.append(price)
        except (ValueError, TypeError):
            pass
    if fresh:
        return max(fresh, key=lambda p: p.quoted_at)
    closes = [p for p in candidates if p.kind in ("DAILY_CLOSE", "ESTIMATE_CLOSE")]
    return max(closes, key=lambda p: (p.day, p.kind == "DAILY_CLOSE", p.source_id)) if closes else None


def label(snapshot, key):
    instrument, account = snapshot.instruments[key[2]], snapshot.accounts[key[1]]
    return account["broker_code"], instrument["category"], instrument["symbol"]


def position_reports(snapshot, positions, *, micro_profit=None):
    micro_profit = micro_profit or {}
    grouped = grouped_positions(positions)
    residuals = {}
    for key, p in grouped.items():
        if ZERO < p.quantity < MIN_OPEN_QUANTITY and '成交粒度冲突' not in p.warnings:
            residuals[key] = p.quantity
            p.quantity = ZERO
    valuations = {}
    for key, p in grouped.items():
        if p.quantity > ZERO or "成交粒度冲突" in p.warnings:
            price = current_price(snapshot, key[2])
            value = p.quantity * price.value if price else None
            if "成交粒度冲突" in p.warnings:
                value = None
            valuations[key] = (price, value)
            if price is None or price.day != snapshot.scope.as_of.isoformat() or price.basis == "UNKNOWN":
                snapshot.issues.append(Issue("DQ010", "WARN", "持仓缺行情、使用上次收盘或历史复权口径未知。", snapshot.scope.as_of.isoformat(), account_id=key[1], instrument_id=key[2], source_ids=(price.source_id,) if price else ()))
            if price and price.basis not in ("RAW", "UNKNOWN") and any(a["instrument_id"] == key[2] for a in snapshot.actions):
                snapshot.issues.append(Issue("DQ013", "ERROR", "价格标为复权价而账本已重放拆股；拒绝二次复权估值。", instrument_id=key[2], source_ids=(price.source_id,)))
                valuations[key] = (price, None)
    total = strict_sum(value for _, value in valuations.values())
    holdings, stocks, closed = [], [], []
    for key, p in sorted(grouped.items(), key=lambda item: label(snapshot, item[0])):
        info = {"account_id": key[1], "instrument_id": key[2], "source_record_ids": tuple(p.source_ids),
                "person_ids": tuple(k[0] for k in positions if k[1:] == key[1:]), "warnings": sorted(p.warnings),
                "as_of": snapshot.scope.as_of.isoformat(), "currency": snapshot.scope.reporting_currency,
                "completeness": "REVIEW_REQUIRED" if p.warnings else "COMPLETE", "metric_mode": "EXCEL_COMPAT"}
        ch, cat, symbol = label(snapshot, key)
        untrusted = "成交粒度冲突" in p.warnings
        price, mv = valuations.get(key, (None, ZERO if p.quantity == ZERO else None))
        pnl = mv - p.net if mv is not None and p.net is not None and p.quantity >= ZERO else None
        recovery = (p.cost - price.value) / price.value if p.cost is not None and price and p.cost > price.value else None
        weight = ratio(mv, total) if p.quantity > ZERO else None
        info.update(valuation_status=price.status if price else None, valuation_date=price.day if price else None, valuation_source=price.kind if price else None,
                    quoted_at=price.quoted_at if price else None, price_status=price.status if price else None,
                    price_source_record_id=price.source_id if price else None)
        trusted_quantity = None if "成交粒度冲突" in p.warnings else p.quantity
        info["candidate_quantity"] = p.quantity
        if key in residuals:
            info.update(candidate_quantity=residuals[key], ignored_residual_quantity=residuals[key],
                        closure_policy='不足0.5股按已清仓展示，未生成卖出交易；收益按累计卖出减累计买入。')
        if p.quantity > ZERO or untrusted:
            holdings.append(ReportRow((ch, cat, symbol, trusted_quantity, price.value if price else None, mv, p.cost,
                                       p.buy, p.sell, p.net, pnl, ratio(pnl, p.net), recovery, weight), info))
        if p.quantity == ZERO and not untrusted:
            gain = -p.net if p.net is not None else None
            closed.append(ReportRow((ch, cat, symbol, p.buy_quantity, p.sell_quantity, p.buy, p.sell, gain,
                                     p.count, ratio(p.buy, p.buy_quantity), ratio(p.sell, p.sell_quantity), p.first, p.last), info))
        else:
            gain = ZERO if p.quantity > ZERO else None
        micro = strict_sum(value for (month, account, instrument), value in micro_profit.items() if (account, instrument) == key[1:])
        stocks.append(ReportRow((ch, cat, symbol, "成交待核对" if untrusted else "持有中" if p.quantity > ZERO else "已清仓" if p.quantity == ZERO else "负仓异常",
                                  trusted_quantity, price.value if price else p.last_price if p.quantity == ZERO else None,
                                  mv, p.cost, p.buy, p.sell, p.net, pnl if p.quantity > ZERO else None,
                                  ratio(pnl, p.net) if p.quantity > ZERO else None, recovery, weight,
                                  gain, micro, pnl if p.quantity > ZERO else gain), info))
    return {"当前持仓": Report("当前持仓", HOLDINGS_COLUMNS, tuple(holdings)),
            "股票汇总": Report("股票汇总", STOCK_COLUMNS, tuple(stocks)),
            "清仓收益": Report("清仓收益", CLOSED_COLUMNS, tuple(closed))}
