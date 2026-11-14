"""Account-level returns for domestic funds with a verified zero opening."""
from decimal import Decimal

from .calculate import ZERO, exact


def confirmed_returns(transactions, shares, market_value, *, opening_shares=ZERO, opening_cost=ZERO,
                      allow_transfer_amount=False):
    """Use cash paid/received as recorded by Alipay and moving-average remaining cost."""
    quantity = opening_shares
    cost = opening_cost
    contributed = opening_cost
    realized = dividends = ZERO
    buy_kinds = ("BUY", "TRANSFER_IN") if allow_transfer_amount else ("BUY",)
    sell_kinds = ("SELL", "TRANSFER_OUT") if allow_transfer_amount else ("SELL",)
    for trade in transactions:
        kind, signed, cash = trade["transaction_type"], trade["signed_shares"], trade["confirmed_amount"]
        if trade["status"] != "正常" or signed is None:
            return {}
        change = Decimal(signed)
        if kind == "REINVEST" and change > ZERO:
            quantity += change
            continue
        if cash is None:
            return {}
        cash = Decimal(cash)
        if cash < ZERO:
            return {}
        if kind in buy_kinds and change > ZERO:
            quantity += change
            contributed += cash
            cost += cash
        elif kind in sell_kinds and ZERO < -change <= quantity:
            released = cost if -change == quantity else cost * -change / quantity
            cost -= released
            quantity += change
            realized += cash - released
        elif kind == "CASH_DIVIDEND" and change == ZERO:
            dividends += cash
        elif kind == "SHARE_REDUCE" and ZERO < -change <= quantity and cash == ZERO:
            # 分级基金下折等零现金份额缩减：仅减少份额，保留总成本不变
            quantity += change
        else:
            # An internal conversion requires a verified cost basis from the other fund.
            return {}
    if quantity != shares or contributed <= ZERO or (shares > ZERO and market_value is None):
        return {}
    pnl = (market_value or ZERO) - cost + realized + dividends
    return dict(cost=exact(cost), contributed=exact(contributed), realized_pnl=exact(realized), cash_dividend=exact(dividends),
                pnl=exact(pnl), rate=exact(pnl / contributed * 100))


def alipay_realized_events(transactions, *, opening_shares=ZERO, opening_cost=ZERO, allow_transfer_amount=False):
    """按确认日期生成支付宝单只基金的已实现盈亏事件序列。"""
    quantity = opening_shares
    cost = opening_cost
    buy_kinds = ("BUY", "TRANSFER_IN") if allow_transfer_amount else ("BUY",)
    sell_kinds = ("SELL", "TRANSFER_OUT") if allow_transfer_amount else ("SELL",)
    events = []
    for trade in transactions:
        kind, signed, cash = trade["transaction_type"], trade["signed_shares"], trade["confirmed_amount"]
        day = trade.get("effective_date")
        if trade["status"] != "正常" or signed is None:
            return None
        change = Decimal(signed)
        if kind == "REINVEST" and change > ZERO:
            quantity += change
            continue
        if cash is None:
            return None
        cash = Decimal(cash)
        if cash < ZERO:
            return None
        if kind in buy_kinds and change > ZERO:
            quantity += change
            cost += cash
        elif kind in sell_kinds and ZERO < -change <= quantity:
            released = cost if -change == quantity else cost * -change / quantity
            cost -= released
            quantity += change
            if day:
                events.append((day, exact(cash - released)))
        elif kind == "CASH_DIVIDEND" and change == ZERO:
            if day and cash != ZERO:
                events.append((day, exact(cash)))
        else:
            return None
    return events or None


def tiantian_returns(rows, shares, market_value):
    from collections import Counter
    from .tiantian_positions import BUY, SELL, movement, subscription_key

    confirmations = Counter(subscription_key(r) for r in rows if r["transaction_type_raw"] == "认购结果"
                            and r["confirmation_status"] == "成功" and r["parse_status"] == "正常")
    trades = []
    for row in rows:
        kind = row["transaction_type_raw"]
        delta = movement(row, confirmations)
        if delta is None:
            return {}
        cash = row["confirmed_amount"]
        if kind in BUY | SELL | {"超级转换-转入", "超级转换-转出"}:
            # Conversion legs carry acquisition/redemption value at fund level.
            side = "BUY" if kind in BUY or kind == "超级转换-转入" else "SELL"
            if any(row[k] is None for k in ("confirmed_amount", "fee", "confirmed_nav")):
                return {}
            amount, fee, nav = (Decimal(row[k]) for k in ("confirmed_amount", "fee", "confirmed_nav"))
            gross = abs(delta) * nav
            if fee < ZERO or nav <= ZERO:
                return {}
            signed_fee = fee if side == "BUY" else -fee
            # The statement's confirmed cash includes purchase fees and deducts
            # redemption fees. Allow for the disclosed cash/share rounding, and
            # allow IPO subscription interest converted to shares on 认购结果.
            tolerance = Decimal("0.005") * (1 + nav) + abs(delta) * Decimal("0.00005")
            diff = amount - gross - signed_fee
            is_ipo_interest = (
                kind == "认购结果" and nav == Decimal("1")
                and ZERO <= -diff <= max(Decimal("10"), amount * Decimal("0.01"))
            )
            if abs(diff) > tolerance and not is_ipo_interest:
                return {}
        elif kind in ("现金分红", "红利再投资"):
            side = "CASH_DIVIDEND" if kind == "现金分红" else "REINVEST"
        elif kind == "强减" and delta < ZERO and Decimal(cash or ZERO) == ZERO:
            side = "SHARE_REDUCE"
        elif delta == ZERO:
            continue
        else:
            return {}
        trades.append(dict(transaction_type=side, signed_shares=exact(delta), confirmed_amount=cash, status="正常"))
    return confirmed_returns(trades, shares, market_value)
