"""Rank the displayed live amount against complete, scoped historical days."""
from collections import defaultdict
from decimal import Decimal,localcontext


def today_rank(reports,pnl,day,*,channel=None,ticker=''):
    title='今日盈亏历史排名'
    note='以当前持仓当日盈亏金额，对比当前人员及筛选范围的历史日收益（含当日清仓）；仅比较所选交易日前的有效日期，金额相同并列。'
    report=reports.get('每日持仓收益')
    grouped=defaultdict(list)
    for row in report.rows if report else ():
        v=row.values
        if str(v[0])>=str(day) or (channel and v[1]!=channel):continue
        if ticker.strip().casefold() not in (str(v[3])+' '+str(v[1])).casefold():continue
        grouped[str(v[0])].append(v[19])
    days=[];missing=0
    with localcontext() as context:
        context.prec=50
        for values in grouped.values():
            if any(not isinstance(v,Decimal) or not v.is_finite() for v in values):missing+=1
            else:days.append(sum(values,Decimal(0)))
    rank=None;direction=None
    if pnl is None:text='— 等待有效盈亏'
    elif pnl==0:text='平盘 · 不排名'
    elif not days:text='— 暂无可比历史'
    else:
        direction='盈利' if pnl>0 else '亏损'
        rank=1+sum(value>pnl if pnl>0 else value<pnl for value in days)
        text=f'{direction}第 {rank} 名'
    return dict(title=title,text=text,rank=rank,direction=direction,valid_days=len(days),
                excluded_days=missing,note=note+f' 有效历史 {len(days)} 天；缺失/未知收益排除 {missing} 天。')
