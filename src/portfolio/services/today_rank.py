"""Rank the displayed live amount against complete, scoped historical days."""
from collections import defaultdict
from decimal import Decimal, localcontext


def today_rank(reports, pnl, day, *, channel=None, ticker='', rate=None):
    title = '今日盈亏历史排名'
    note = '以当前持仓当日盈亏金额与收益率，对比当前人员及筛选范围的历史日表现（含当日清仓）；仅比较所选交易日前的有效日期，数值相同并列。'
    report = reports.get('每日持仓收益')
    grouped = defaultdict(list)
    for row in report.rows if report else ():
        v = row.values
        if str(v[0]) >= str(day) or (channel and v[1] != channel):
            continue
        if ticker.strip().casefold() not in (str(v[3]) + ' ' + str(v[1])).casefold():
            continue
        grouped[str(v[0])].append(row)

    days_pnl = []
    days_rate = []
    missing = 0
    with localcontext() as context:
        context.prec = 50
        for d, rows in grouped.items():
            if any(not isinstance(r.values[19], Decimal) or not r.values[19].is_finite() for r in rows):
                missing += 1
                continue
            day_pnl = sum(r.values[19] for r in rows)
            init_mv = sum(r.values[15] for r in rows if r.values[15] is not None)
            end_mv = sum(r.values[11] for r in rows if r.values[11] is not None)
            base = init_mv if init_mv > 0 else (end_mv - day_pnl)
            days_pnl.append(day_pnl)
            if base and base > 0:
                days_rate.append(day_pnl / base)

    rank = None
    rate_rank = None
    rate_label = None
    direction = None
    if pnl is None:
        text = '— 等待有效盈亏'
    elif pnl == 0:
        text = '平盘 · 不排名'
    elif not days_pnl:
        text = '— 暂无可比历史'
    else:
        direction = '盈利' if pnl > 0 else '亏损'
        rank = 1 + sum(value > pnl if pnl > 0 else value < pnl for value in days_pnl)
        rate_desc = ""
        if rate is not None and days_rate and rate != 0:
            rate_rank = 1 + sum(r > rate if rate > 0 else r < rate for r in days_rate)
            rate_label = f"{'亏损率' if rate < 0 else '收益率'}第 {rate_rank} 名"
            rate_desc = f" · {rate_label}"
        text = f'{direction}第 {rank} 名{rate_desc}'

    detail_note = note + f' 有效历史 {len(days_pnl)} 天；缺失/未知收益排除 {missing} 天。'
    if rank is not None:
        detail_note += f' 当日金额{direction}第 {rank} 名'
    if rate_label is not None:
        detail_note += f'，{rate_label}'
    detail_note += '。'

    return dict(
        title=title,
        text=text,
        rank=rank,
        rate_rank=rate_rank,
        direction=direction,
        valid_days=len(days_pnl),
        excluded_days=missing,
        note=detail_note,
    )
