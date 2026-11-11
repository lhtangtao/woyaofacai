"""Read-only ownership and cost projection using existing financial engines."""
from collections import defaultdict
from decimal import Decimal,localcontext
from portfolio.domain.analytics import ratio,strict_sum
from portfolio.domain.live_quotes import quote_usable
from portfolio.engine.live_valuation import live_holdings

COLUMNS=('人员','持股数量','占比','成本价','成本总额','当前价','市值','收益率','累计盈亏','操作')
CLOSED_COLUMNS=('人员','历史买入股数','资金占比','买入均价','历史买入总额','卖出均价','历史卖出总额','收益率','清仓收益','操作')
BROKER_COLUMNS=('券商','持股数量','成本价','成本总额','当前价','市值','累计盈亏')
CLOSED_BROKER_COLUMNS=('券商','历史买入股数','买入均价','历史买入总额','卖出均价','历史卖出总额','清仓收益')
VALID_STATUSES=('holding','closed','all')


def _validate_status(status):
    status=(status or 'holding').strip().lower()
    if status not in VALID_STATUSES:
        raise ValueError('未知持仓状态筛选。')
    return status


def choices(bundle,status='holding'):
    status=_validate_status(status)
    items={}
    open_holders=defaultdict(list)
    closed_holders=defaultdict(list)
    has_open=defaultdict(bool)
    has_closed=defaultdict(bool)

    def scan_book(book,person_name=None):
        if '股票汇总' in book.reports:
            for row in book.reports['股票汇总'].rows:
                inst_id=row.metadata['instrument_id']
                sym=row.values[2]
                qty=row.values[4]
                items[inst_id]=sym
                if qty is None or qty!=0:
                    has_open[inst_id]=True
                    if person_name and person_name not in open_holders[inst_id]:
                        open_holders[inst_id].append(person_name)
                else:
                    has_closed[inst_id]=True
                    if person_name and person_name not in closed_holders[inst_id]:
                        closed_holders[inst_id].append(person_name)
        if '当前持仓' in book.reports:
            for row in book.reports['当前持仓'].rows:
                qty=row.values[3] if len(row.values)>3 else None
                if qty is None or qty!=0:
                    inst_id=row.metadata['instrument_id']
                    sym=row.values[2]
                    items[inst_id]=sym
                    has_open[inst_id]=True
                    if person_name and person_name not in open_holders[inst_id]:
                        open_holders[inst_id].append(person_name)

    for person_id,name,book in bundle.persons:
        scan_book(book,name)
    for book in (bundle.aggregate,bundle.broker):
        scan_book(book,None)

    result=[]
    for inst_id,sym in sorted(items.items(),key=lambda item:(item[1],item[0])):
        is_open=has_open[inst_id]
        is_closed=(not is_open) and has_closed[inst_id]
        if status=='holding' and not is_open:
            continue
        if status=='closed' and not is_closed:
            continue
        if status=='all' and not (is_open or is_closed):
            continue
        if is_closed:
            h_list=list(closed_holders.get(inst_id,[]))
        elif status=='all':
            h_list=list(open_holders.get(inst_id,[]))+[p for p in closed_holders.get(inst_id,[]) if p not in open_holders.get(inst_id,[])]
        else:
            h_list=list(open_holders.get(inst_id,[]))
        h_str='、'.join(h_list) if h_list else '券商'
        suffix=' · 已清仓' if (is_closed and status=='all') else ''
        result.append(dict(id=inst_id,symbol=sym,holders=h_list,is_closed=is_closed,
                           holding_status='closed' if is_closed else 'holding',
                           label=f"{sym}（{h_str}{suffix}）"))
    return result


def analyze(bundle,instrument_id,instruments,quotes,session,now,search='',status='holding'):
    status=_validate_status(status)
    available=choices(bundle,status=status)
    if instrument_id is None and available:instrument_id=available[0]['id']
    choice_item=next((v for v in available if v['id']==instrument_id),None)
    symbol=choice_item['symbol'] if choice_item else ''
    if instrument_id is not None and not symbol:
        raise ValueError('所选股票不在该统计日期的实际持仓范围内。' if status=='holding' else '所选股票不在当前筛选条件与统计日期的范围内。')
    def stocks(book):
        return [r for r in book.reports['股票汇总'].rows if r.metadata['instrument_id']==instrument_id]
    def closed_rows(book):
        report=book.reports.get('清仓收益')
        return [r for r in report.rows if r.metadata['instrument_id']==instrument_id] if report else []
    def unique_price(rows):
        values=[r['price'] for r in rows if r['quantity']!=0]
        return values[0] if values and all(isinstance(v,Decimal) and v.is_finite() and v>0 and v==values[0] for v in values) else None
    def summarize(rows):
        qty=strict_sum(r['quantity'] for r in rows);cost=strict_sum(r['cost_total'] for r in rows)
        buy_total=strict_sum(r.get('buy_total') for r in rows)
        sell_total=strict_sum(r.get('sell_total') for r in rows)
        buy_qty=strict_sum(r.get('buy_quantity') for r in rows)
        sell_qty=strict_sum(r.get('sell_quantity') for r in rows)
        buy_avg=ratio(buy_total,buy_qty) if buy_qty else None
        sell_avg=ratio(sell_total,sell_qty) if sell_qty else None
        price=unique_price(rows)
        all_closed=bool(rows) and all(r.get('is_closed') for r in rows)
        valid_quantity=all(r['quantity'] is not None and r['quantity']>=0 for r in rows)
        cost_price=buy_avg if all_closed else (ratio(cost,qty) if valid_quantity else None)
        pnl_sum=strict_sum(r['pnl'] for r in rows)
        rate=ratio(pnl_sum,buy_total) if all_closed else (ratio(price-cost_price,cost_price) if price is not None and cost_price is not None else None)
        return dict(quantity=qty,cost_total=cost,cost=cost_price,price=price,
                    buy_total=buy_total,sell_total=sell_total,buy_quantity=buy_qty,sell_quantity=sell_qty,
                    buy_avg=buy_avg,sell_avg=sell_avg,is_closed=all_closed,
                    quote_note='；'.join(dict.fromkeys(r.get('quote_note','') for r in rows if r.get('quote_note'))),
                    market_value=strict_sum(r['market_value'] for r in rows),
                    pnl=pnl_sum,rate=rate)
    with localcontext() as context:
        context.prec=50
        broker_stocks=stocks(bundle.broker)
        broker_qty=strict_sum(r.values[4] for r in broker_stocks)
        people=[];all_prices=[]
        for person,name,book in bundle.persons:
            live,*_=live_holdings(book,instruments,quotes,session,now)
            by_key={(r.metadata['account_id'],r.metadata['instrument_id']):r for r in live.rows}
            closed_by_key={(r.metadata['account_id'],r.metadata['instrument_id']):r for r in closed_rows(book)}
            channel_rows=defaultdict(list)
            for stock in stocks(book):
                qty=stock.values[4]
                if status=='holding' and qty==0:continue
                if status=='closed' and qty!=0:continue
                account_id=stock.metadata['account_id']
                if qty==0:
                    c_row=closed_by_key.get((account_id,instrument_id))
                    buy_total=stock.values[8];sell_total=stock.values[9]
                    buy_qty=c_row.values[3] if c_row else None
                    sell_qty=c_row.values[4] if c_row else None
                    ref_price=(c_row.values[11] if c_row else None) or stock.values[5]
                    pnl_val=c_row.values[7] if c_row else stock.values[15]
                    item=dict(quantity=Decimal(0),cost_total=stock.values[10],
                              buy_total=buy_total,sell_total=sell_total,
                              buy_quantity=buy_qty,sell_quantity=sell_qty,
                              price=ref_price,market_value=Decimal(0),pnl=pnl_val,
                              is_closed=True,
                              quote_note=(c_row.metadata.get('current_price_status','已清仓参考价') if c_row else '已清仓参考价')+' · '+str(stock.metadata.get('valuation_date','未知日期')))
                else:
                    row=by_key.get((account_id,instrument_id))
                    item=dict(quantity=qty,cost_total=row.values[9] if row else stock.values[10],
                              buy_total=stock.values[8],sell_total=stock.values[9],
                              buy_quantity=None,sell_quantity=None,
                              price=row.values[4] if row else stock.values[5],
                              market_value=row.values[5] if row else stock.values[6],
                              pnl=row.values[10] if row else stock.values[17],
                              is_closed=False,
                              quote_note=(row.metadata.get('live_status','')+' · '+str(row.metadata.get('live_quote_time') if row.metadata.get('quote_source')!='reference' else row.metadata.get('valuation_date','未知日期'))) if row else '原账本参考价 · '+str(stock.metadata.get('valuation_date','未知日期')))
                    if not isinstance(item['price'],Decimal) or not item['price'].is_finite() or item['price']<=0:
                        item.update(price=None,market_value=None,pnl=None,quote_note='缺少有效价格，估值与盈亏未知')
                channel_rows[stock.values[0]].append(item);all_prices.append(item)
            if not channel_rows:continue
            brokers=[dict(channel=channel,**summarize(rows)) for channel,rows in sorted(channel_rows.items())]
            total=summarize(brokers)
            total.update(person_id=person,person_name=name,share=ratio(total['quantity'],broker_qty),brokers=brokers)
            if total['quantity'] is None or total['quantity']<0:total['warning']='持仓数量未知或负仓异常，未修正原值。'
            elif total['is_closed']:total['warning']='该人员在此股票下已清仓（当前持仓为 0 股），盈亏为历史已实现清仓收益。'
            people.append(total)

        is_closed_view=bool((choice_item and choice_item.get('is_closed')) or (people and all(r['is_closed'] for r in people) and (broker_qty is None or broker_qty==0)))
        if is_closed_view:
            total_buy_people=strict_sum(r['buy_total'] for r in people)
            for r in people:
                r['share']=ratio(r['buy_total'],total_buy_people) if total_buy_people else None
        assigned=strict_sum(r['quantity'] for r in people)
        difference=broker_qty-assigned if broker_qty is not None and assigned is not None else None
        broker_pnl=strict_sum((r.values[15] if r.values[4]==0 else r.values[17]) for r in broker_stocks)
        assigned_pnl=strict_sum(r['pnl'] for r in people)
        pnl_difference=broker_pnl-assigned_pnl if broker_pnl is not None and assigned_pnl is not None else None

        needle=search.strip().casefold()
        shown=[r for r in people if needle in r['person_name'].casefold()]
        if is_closed_view:
            shown.sort(key=lambda r:(r['pnl'] is None,-(r['pnl'] or Decimal(0)),r['person_name']))
        else:
            shown.sort(key=lambda r:(r['quantity'] is None,-(r['quantity'] or Decimal(0)),r['pnl'] is None,-(r['pnl'] or Decimal(0)),r['person_name']))
        total=summarize(shown)
        if is_closed_view:
            total_buy_people=strict_sum(r['buy_total'] for r in people)
            total['share']=ratio(total['buy_total'],total_buy_people) if total_buy_people else None
        else:
            total['share']=ratio(total['quantity'],broker_qty)

        broker_live,*_=live_holdings(bundle.broker,instruments,quotes,session,now)
        physical_prices=[dict(quantity=r.values[3],price=r.values[4]) for r in broker_live.rows if r.metadata['instrument_id']==instrument_id]
        price=unique_price(physical_prices or all_prices)
        if price is None and is_closed_view and instrument_id is not None:
            inst=instruments.get(instrument_id,{})
            sym_us=inst.get('symbol','')+'.US'
            quote=quotes.get((sym_us,session.name)) if (session and quotes) else None
            if inst.get('market')=='US' and quote_usable(quote,session,now):
                price=quote.price
            else:
                broker_closed=closed_rows(bundle.broker)
                closed_prices=[r.values[11] for r in broker_closed if isinstance(r.values[11],Decimal) and r.values[11].is_finite() and r.values[11]>0]
                if closed_prices:price=closed_prices[0]
                else:
                    ref_prices=[r['price'] for r in all_prices if isinstance(r.get('price'),Decimal) and r['price'].is_finite() and r['price']>0]
                    if ref_prices:price=ref_prices[0]

        valid_costs=[r for r in shown if r['quantity'] is not None and r['quantity']>0 and r['cost'] is not None]
        complete_costs=all(r['quantity'] is not None and r['quantity']>=0 for r in shown) and len(valid_costs)==sum(r['quantity'] is not None and r['quantity']>0 for r in shown)
        lowest=min(valid_costs,key=lambda r:r['cost']) if valid_costs else None
        highest=max(valid_costs,key=lambda r:r['cost']) if valid_costs else None
        valid_pnls=[r for r in shown if r['pnl'] is not None]
        best_pnl=max(valid_pnls,key=lambda r:r['pnl']) if valid_pnls else None
        worst_pnl=min(valid_pnls,key=lambda r:r['pnl']) if valid_pnls else None

        reconciliation=[]
        keys={r.metadata['account_id'] for r in broker_stocks}
        keys.update(r.metadata['account_id'] for _,_,b in bundle.persons for r in stocks(b))
        for account in sorted(keys):
            b=strict_sum(r.values[4] for r in broker_stocks if r.metadata['account_id']==account)
            p=strict_sum(r.values[4] for _,_,book in bundle.persons for r in stocks(book) if r.metadata['account_id']==account)
            bpnl=strict_sum((r.values[15] if r.values[4]==0 else r.values[17]) for r in broker_stocks if r.metadata['account_id']==account)
            ppnl=strict_sum((r.values[15] if r.values[4]==0 else r.values[17]) for _,_,book in bundle.persons for r in stocks(book) if r.metadata['account_id']==account)
            reconciliation.append(dict(account_id=account,broker=b,assigned=p,difference=b-p if b is not None and p is not None else None,
                                       broker_pnl=bpnl,assigned_pnl=ppnl,pnl_difference=bpnl-ppnl if bpnl is not None and ppnl is not None else None))

        if is_closed_view:
            rec_ok=(all(r['difference']==0 and r['broker']==0 and r['assigned']==0 for r in reconciliation)
                    and pnl_difference is not None and abs(pnl_difference)<Decimal('0.01'))
            cost_analysis_payload=dict(lowest=dict(name=worst_pnl['person_name'],cost=worst_pnl['pnl']) if worst_pnl else None,
                                       highest=dict(name=best_pnl['person_name'],cost=best_pnl['pnl']) if best_pnl else None,
                                       best_pnl=dict(name=best_pnl['person_name'],pnl=best_pnl['pnl']) if best_pnl else None,
                                       worst_pnl=dict(name=worst_pnl['person_name'],pnl=worst_pnl['pnl']) if worst_pnl else None,
                                       weighted=total['cost'],weighted_sell=total['sell_avg'],complete=all(r['pnl'] is not None for r in shown))
        else:
            rec_ok=all(r['difference']==0 and r['broker'] is not None and r['broker']>=0 and r['assigned'] is not None and r['assigned']>=0 for r in reconciliation)
            cost_analysis_payload=dict(lowest=dict(name=lowest['person_name'],cost=lowest['cost']) if lowest else None,
                                       highest=dict(name=highest['person_name'],cost=highest['cost']) if highest else None,
                                       best_pnl=dict(name=best_pnl['person_name'],pnl=best_pnl['pnl']) if best_pnl else None,
                                       worst_pnl=dict(name=worst_pnl['person_name'],pnl=worst_pnl['pnl']) if worst_pnl else None,
                                       weighted=total['cost'],weighted_sell=total['sell_avg'],complete=complete_costs)

        return dict(choices=available,instrument_id=instrument_id,symbol=symbol,as_of=str(bundle.aggregate.scope.as_of),
                    holding_status=status,is_closed=is_closed_view,
                    rows=shown,total=total,
                    summary=dict(price=price,broker_quantity=broker_qty,
                                 cost_total=total['buy_total'] if is_closed_view else total['cost_total'],
                                 buy_total=total['buy_total'],sell_total=total['sell_total'],
                                 market_value=broker_qty*price if broker_qty is not None and price is not None else None,
                                 pnl=total['pnl'],rate=total['rate'],
                                 holders=len(shown) if is_closed_view else sum(r['quantity'] is not None and r['quantity']>0 for r in shown),
                                 closed_holders=sum(1 for r in shown if r['is_closed']),
                                 quote_note=total['quote_note']),
                    reconciliation=dict(broker=broker_qty,assigned=assigned,difference=difference,
                                        broker_pnl=broker_pnl,assigned_pnl=assigned_pnl,pnl_difference=pnl_difference,
                                        accounts=reconciliation,ok=rec_ok),
                    cost_analysis=cost_analysis_payload,
                    note='币种 USD，EXCEL_COMPAT。' + (
                        '已清仓模式：展示该股票历史已实现清仓收益在各人员之间的归属与买入/卖出均价；资金占比按各人员历史买入总额除以全部人员买入总额。'
                        if is_closed_view else
                        '成本沿用当前持仓净投入口径（累计买入−累计卖出，含原账本基准及费用）；占比始终除以券商总股数。搜索同步过滤人员侧概览、图表和明细，券商股数与核对基准保持全部人员范围。当前价按实际行情有效时点估值，历史日期使用不晚于截止日期的原口径价格；未知不补零。'
                    ))


def export_csv(data):
    import csv,io
    output=io.StringIO();writer=csv.writer(output)
    from portfolio.services.export_service import safe_text
    def write(row):writer.writerow([safe_text(v) for v in row])
    is_closed=bool(data.get('is_closed'))
    write(('股票',data['symbol'],'截止日期',data['as_of'],'筛选状态',data.get('holding_status','holding')))
    write(('说明',data['note']))
    if is_closed:
        write(('券商清仓收益',data['reconciliation'].get('broker_pnl'),'全部人员清仓收益',data['reconciliation'].get('assigned_pnl'),'差异',data['reconciliation'].get('pnl_difference')))
        write(CLOSED_COLUMNS[:-1])
        def closed_person_row(name,r):return (name,r.get('buy_quantity'),r.get('share'),r.get('buy_avg'),r.get('buy_total'),r.get('sell_avg'),r.get('sell_total'),r.get('rate'),r.get('pnl'))
        for row in data['rows']:write(closed_person_row(row['person_name'],row))
        write(closed_person_row('合计',data['total']))
        write(('占比/收益率为小数比例；例如0.1表示10%。',))
        for row in data['rows']:
            write(('人员',row['person_name']));write(CLOSED_BROKER_COLUMNS)
            for b in row['brokers']:write((b['channel'],b.get('buy_quantity'),b.get('buy_avg'),b.get('buy_total'),b.get('sell_avg'),b.get('sell_total'),b.get('pnl')))
    else:
        write(('券商股数',data['reconciliation']['broker'],'全部人员股数',data['reconciliation']['assigned'],'差异',data['reconciliation']['difference']))
        write(COLUMNS[:-1])
        def person_row(name,r):return (name,r['quantity'],r['share'],r['cost'],r['cost_total'],r['price'],r['market_value'],r['rate'],r['pnl'])
        for row in data['rows']:
            label=f"{row['person_name']}（已清仓）" if row.get('is_closed') else row['person_name']
            write(person_row(label,row))
        write(person_row('合计',data['total']))
        write(('占比/收益率为小数比例；例如0.1表示10%。',))
        for row in data['rows']:
            write(('人员',row['person_name']));write(BROKER_COLUMNS)
            for b in row['brokers']:write((b['channel'],b['quantity'],b['cost'],b['cost_total'],b['price'],b['market_value'],b['pnl']))
    return output.getvalue()
