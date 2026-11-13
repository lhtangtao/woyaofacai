"""Read-only summaries of confirmed domestic fund transactions."""
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from .fund_returns import tiantian_returns
from .tiantian_positions import BUY, SELL, movement, subscription_key


def _number(value):
    try:
        result = Decimal(str(value)) if value is not None else None
        return result if result is not None and result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def _total(values):
    return sum(values, Decimal(0)) if values else None


def confirmed_holding_days(calc, table, account, code, start, end):
    """Count actual held days only when the full daily share history exists."""
    if table not in ('calc_tiantian_positions', 'calc_fund_positions_daily'):
        raise ValueError('未知的国内基金持仓表')
    if end < start:
        return None
    rows = list(calc.execute(f"""SELECT trade_date,holding_shares FROM {table}
        WHERE account_id=? AND fund_code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date""",
        (account, code, start.isoformat(), end.isoformat())))
    if (len(rows) != (end - start).days + 1 or any(row['holding_shares'] is None for row in rows)):
        return None
    quantities = [_number(row['holding_shares']) for row in rows]
    return sum(value > 0 for value in quantities) if all(value is not None for value in quantities) else None


def tiantian_return_issue(rows):
    """Explain why the existing return engine cannot validate a transaction."""
    confirmations = Counter(subscription_key(row) for row in rows
                            if row['transaction_type_raw'] == '认购结果'
                            and row['confirmation_status'] == '成功' and row['parse_status'] == '正常')
    for row in rows:
        kind = row['transaction_type_raw']
        delta = movement(row, confirmations)
        if delta is None:
            return f"{row.get('confirmation_date') or '日期未知'} {kind} 的交易状态或份额待核对"
        if kind in BUY | SELL | {'超级转换-转入', '超级转换-转出'}:
            amount, fee, nav = (_number(row.get(key)) for key in ('confirmed_amount', 'fee', 'confirmed_nav'))
            if amount is None or fee is None or nav is None or nav <= 0:
                return f"{row.get('confirmation_date')} {kind} 的金额、手续费或确认净值缺失"
            gross = abs(delta) * nav
            signed_fee = fee if kind in BUY | {'超级转换-转入'} else -fee
            tolerance = Decimal('0.005') * (1 + nav) + abs(delta) * Decimal('0.00005')
            if abs(amount - gross - signed_fee) > tolerance:
                return f"{row.get('confirmation_date')} {kind} 的确认金额、份额、净值和手续费未能对账"
        elif kind not in ('现金分红', '红利再投资') and delta != 0:
            return f"{row.get('confirmation_date')} {kind} 的份额调整或资金转换成本缺少核算依据"
    return '期初成本、交易覆盖或历史份额尚未完整核对'


def closed_transaction_summaries(raw, calc, keys, cutoff):
    """Sum only identifiable confirmed transactions; conversions are separate.

    Amounts describe available imported transactions and may be incomplete.
    Never treat an absent transaction category as a proven zero.
    """
    keys = set(keys)
    groups = defaultdict(list)
    if not keys:
        return {}
    for item in raw.execute("""SELECT * FROM raw_fund_transactions
            WHERE confirmation_date IS NOT NULL AND confirmation_date<=?
            ORDER BY confirmation_date,transaction_time,id""", (cutoff,)):
        row = dict(item)
        key = row['account_id'], row['fund_code']
        if key in keys:
            groups[key].append(row)
    alipay = defaultdict(list)
    if calc is not None and calc.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='calc_fund_transactions'").fetchone():
        for item in calc.execute("""SELECT account_id,fund_code,effective_date,transaction_type,confirmed_amount,fee,status
                FROM calc_fund_transactions WHERE effective_date IS NOT NULL AND effective_date<=?""", (cutoff,)):
            key = item['account_id'], item['fund_code']
            if key in keys:
                alipay[key].append(dict(item))
    result = {}
    for key in keys:
        buckets = defaultdict(list)
        issues = []
        dates = []
        if groups[key] and groups[key][0]['source_platform'] == 'ALIPAY':
            transactions = alipay[key]
            classified = ((row, row['transaction_type'] if row['status'] == '正常' else None) for row in transactions)
        else:
            transactions = groups[key]
            confirmations = Counter(subscription_key(row) for row in transactions
                                    if row['transaction_type_raw'] == '认购结果'
                                    and row['confirmation_status'] == '成功' and row['parse_status'] == '正常')
            def tiantian_events():
                for row in transactions:
                    delta = movement(row, confirmations)
                    kind = row['transaction_type_raw']
                    if delta is None:
                        yield row, None
                    elif delta > 0 and kind in BUY:
                        yield row, 'BUY'
                    elif delta < 0 and kind in SELL:
                        yield row, 'SELL'
                    elif kind == '超级转换-转入' and delta > 0:
                        yield row, 'TRANSFER_IN'
                    elif kind == '超级转换-转出' and delta < 0:
                        yield row, 'TRANSFER_OUT'
                    elif kind == '现金分红':
                        yield row, 'CASH_DIVIDEND'
                    else:
                        yield row, 'OTHER'
            classified = tiantian_events()
        for row, kind in classified:
            if kind is None:
                issues.append('存在无法确认或待核对的交易')
                continue
            field = {'BUY':'buy_amount', 'SELL':'sell_amount', 'TRANSFER_IN':'transfer_in_amount',
                     'TRANSFER_OUT':'transfer_out_amount', 'CASH_DIVIDEND':'dividend_amount'}.get(kind)
            if field:
                value = _number(row.get('confirmed_amount'))
                if value is None:
                    issues.append(f'{field} 存在确认金额缺失')
                else:
                    buckets[field].append(value)
            if kind in ('BUY', 'TRANSFER_IN'):
                dates.append(row.get('effective_date') or row.get('confirmation_date'))
            if kind in ('BUY', 'SELL', 'TRANSFER_IN', 'TRANSFER_OUT'):
                fee = _number(row.get('fee'))
                if fee is not None:
                    buckets['fee_amount'].append(fee)
        summary = {name: _total(buckets[name]) for name in ('buy_amount','sell_amount','transfer_in_amount',
                   'transfer_out_amount','dividend_amount','fee_amount')}
        summary.update(first_trade_date=min((day for day in dates if day), default=None),
                       cash_summary_issue='；'.join(dict.fromkeys(issues)) or None,
                       cash_summary_scope='已导入且确认的流水；转换和分红单独统计，可能不含缺失历史')
        result[key] = summary
    return result


def tiantian_realized_events(calc, rows, account, code, final_date, expected_pnl):
    """Replay existing return calculation through actual redemption/dividend dates.

    A missing balance, unverified prefix or final mismatch invalidates the entire
    event series; no partial or invented event dates are returned.
    """
    rows = [row for row in rows if row.get('confirmation_date') and row['confirmation_date'] <= final_date]
    event_types = SELL | {'超级转换-转出', '现金分红'}
    days = sorted({row['confirmation_date'] for row in rows if row['transaction_type_raw'] in event_types})
    if not days:
        return None
    previous = Decimal(0)
    events = []
    for day in days:
        position = calc.execute("""SELECT holding_shares FROM calc_tiantian_positions
            WHERE account_id=? AND fund_code=? AND trade_date=?""", (account, code, day)).fetchone()
        if not position or position['holding_shares'] is None:
            return None
        shares = _number(position['holding_shares'])
        if shares is None:
            return None
        prefix = [row for row in rows if row['confirmation_date'] <= day]
        # Market value is only an input to the discarded unrealized result;
        # realized proceeds and cost use the existing confirmed cash rules.
        calculated = tiantian_returns(prefix, shares, Decimal(0))
        if not calculated:
            return None
        accumulated = Decimal(calculated['realized_pnl']) + Decimal(calculated['cash_dividend'])
        change = accumulated - previous
        if change:
            events.append((day, change))
        previous = accumulated
    if previous != Decimal(expected_pnl):
        return None
    return events
