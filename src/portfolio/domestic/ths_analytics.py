"""同花顺（THS）国内股票/ETF 交易份额、已实现盈亏与持仓辅助计算。"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .calculate import ZERO
from .fund_returns import alipay_realized_events, confirmed_returns

THS_ACCOUNT = "THS_STOCK"
THS_PLATFORM = "同花顺"
THS_SOURCE = "THS"


def _dec(value):
    try:
        d = Decimal(str(value)) if value is not None else None
        return d if d is not None and d.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def classify_ths_row(row):
    """将 raw_fund_transactions 中的同花顺记录归类为标准事件类型。"""
    if row.get("parse_status") != "正常" or row.get("confirmation_status") != "成功":
        return None
    kind = str(row.get("transaction_type_raw") or "")
    shares = _dec(row.get("confirmed_shares"))
    amount = _dec(row.get("confirmed_amount"))
    fee = _dec(row.get("fee")) or ZERO
    if kind in ("现金分红", "分红"):
        if amount is None or amount < ZERO:
            return None
        return ("CASH_DIVIDEND", ZERO, amount)
    if kind == "红利再投资":
        if shares is None or shares <= ZERO:
            return None
        return ("REINVEST", shares, ZERO)
    if shares is None or amount is None or amount < ZERO:
        return None
    if shares > ZERO:
        return ("BUY", shares, amount + fee)
    if shares < ZERO:
        return ("SELL", shares, max(ZERO, amount - fee))
    return None


def ths_trade_events(raw, account_id, fund_code, cutoff):
    """读取并校验指定同花顺标的截至 cutoff 的全部交易事件。"""
    rows = [
        dict(r)
        for r in raw.execute(
            """SELECT * FROM raw_fund_transactions
               WHERE account_id=? AND fund_code=? AND source_platform=?
                 AND confirmation_date IS NOT NULL AND confirmation_date<=?
               ORDER BY confirmation_date, transaction_time, id""",
            (account_id, fund_code, THS_SOURCE, cutoff),
        )
    ]
    trades = []
    running = ZERO
    first_holding_day = None
    last_close_day = None
    day_deltas = []
    for row in rows:
        parsed = classify_ths_row(row)
        if parsed is None:
            return {"issue": f"{row.get('confirmation_date') or '未知日期'} 同花顺交易待核对", "trades": []}
        tx_type, signed_shares, cash_amount = parsed
        day = row["confirmation_date"]
        trades.append(
            {
                "effective_date": day,
                "transaction_type": tx_type,
                "signed_shares": str(signed_shares),
                "confirmed_amount": str(cash_amount),
                "status": "正常",
            }
        )
        if signed_shares != ZERO:
            prev = running
            running += signed_shares
            if running < ZERO:
                return {"issue": f"{day} 卖出份额超出当前持仓", "trades": []}
            day_deltas.append((date.fromisoformat(day), signed_shares))
            if prev == ZERO and running > ZERO and first_holding_day is None:
                first_holding_day = day
            if prev > ZERO and running == ZERO:
                last_close_day = day
            elif running > ZERO:
                last_close_day = None
    holding_days = None
    if running == ZERO and first_holding_day and last_close_day:
        start_d = date.fromisoformat(first_holding_day)
        end_d = date.fromisoformat(last_close_day)
        # 统计日历区间内每日收盘后持仓份额 > 0 的天数，与 confirmed_holding_days 口径一致
        cur_shares = ZERO
        idx = 0
        held = 0
        d = start_d
        while d <= end_d:
            while idx < len(day_deltas) and day_deltas[idx][0] == d:
                cur_shares += day_deltas[idx][1]
                idx += 1
            if cur_shares > ZERO:
                held += 1
            d += timedelta(days=1)
        holding_days = held
    return {
        "issue": None,
        "trades": trades,
        "shares": running,
        "as_of": cutoff if rows else None,
        "first_holding_day": first_holding_day,
        "closed_date": last_close_day if running == ZERO else None,
        "holding_days": holding_days,
    }


def ths_returns_and_events(trades, shares, market_value, is_closed):
    """基于 confirmed_returns 与 alipay_realized_events 计算同花顺收益与清仓事件。"""
    if not trades:
        return {}, None
    returns = confirmed_returns(trades, shares, market_value)
    events = alipay_realized_events(trades) if (is_closed and returns) else None
    return returns, events
