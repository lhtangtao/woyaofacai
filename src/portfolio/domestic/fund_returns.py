"""Account-level returns for domestic funds with a verified zero opening."""
from decimal import Decimal

from .calculate import ZERO, exact


def confirmed_returns(transactions, shares, market_value):
    """Use cash paid/received as recorded by Alipay and moving-average remaining cost."""
    quantity = cost = contributed = realized = dividends = ZERO
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
        if kind == "BUY" and change > ZERO:
            quantity += change
            contributed += cash
            cost += cash
        elif kind == "SELL" and ZERO < -change <= quantity:
            released = cost if -change == quantity else cost * -change / quantity
            cost -= released
            quantity += change
            realized += cash - released
        elif kind == "CASH_DIVIDEND" and change == ZERO:
            dividends += cash
        else:
            # An internal conversion requires a verified cost basis from the other fund.
            return {}
    if quantity != shares or contributed <= ZERO or (shares > ZERO and market_value is None):
        return {}
    pnl = (market_value or ZERO) - cost + realized + dividends
    return dict(cost=exact(cost), contributed=exact(contributed), realized_pnl=exact(realized), cash_dividend=exact(dividends),
                pnl=exact(pnl), rate=exact(pnl / contributed * 100))


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
            # redemption fees. Allow for the disclosed cash/share rounding.
            tolerance = Decimal("0.005") * (1 + nav) + abs(delta) * Decimal("0.00005")
            if abs(amount - gross - signed_fee) > tolerance:
                return {}
        elif kind in ("现金分红", "红利再投资"):
            side = "CASH_DIVIDEND" if kind == "现金分红" else "REINVEST"
        elif delta == ZERO:
            continue
        else:
            return {}
        trades.append(dict(transaction_type=side, signed_shares=exact(delta), confirmed_amount=cash, status="正常"))
    return confirmed_returns(trades, shares, market_value)
