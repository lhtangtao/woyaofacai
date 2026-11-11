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
    return dict(cost=exact(cost), realized_pnl=exact(realized), cash_dividend=exact(dividends),
                pnl=exact(pnl), rate=exact(pnl / contributed * 100))
