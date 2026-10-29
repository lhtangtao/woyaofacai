"""Read-only ownership and cost projection using existing financial engines."""
from collections import defaultdict
from decimal import Decimal,localcontext
from portfolio.domain.analytics import ratio,strict_sum
from portfolio.engine.live_valuation import live_holdings

COLUMNS=('人员','持股数量','占比','成本价','成本总额','当前价','市值','收益率','累计盈亏','操作')
BROKER_COLUMNS=('券商','持股数量','成本价','成本总额','当前价','市值','累计盈亏')


def choices(bundle):
    items={}
    for book in (bundle.broker,*(book for _,_,book in bundle.persons)):
        for row in book.reports['股票汇总'].rows:
            if row.values[4] is None or row.values[4]!=0:items[row.metadata['instrument_id']]=row.values[2]
    return [dict(id=i,symbol=s) for i,s in sorted(items.items(),key=lambda item:(item[1],item[0]))]


def analyze(bundle,instrument_id,instruments,quotes,session,now,search=''):
    available=choices(bundle)
    if instrument_id is None and available:instrument_id=available[0]['id']
    symbol=next((v['symbol'] for v in available if v['id']==instrument_id),'')
    if instrument_id is not None and not symbol:raise ValueError('所选股票不在该统计日期的实际持仓范围内。')
    def stocks(book):
        return [r for r in book.reports['股票汇总'].rows if r.metadata['instrument_id']==instrument_id]
    def unique_price(rows):
        values=[r['price'] for r in rows if r['quantity']!=0]
        return values[0] if values and all(isinstance(v,Decimal) and v.is_finite() and v>0 and v==values[0] for v in values) else None
    def summarize(rows):
        qty=strict_sum(r['quantity'] for r in rows);cost=strict_sum(r['cost_total'] for r in rows)
        price=unique_price(rows)
        valid_quantity=all(r['quantity'] is not None and r['quantity']>=0 for r in rows)
        cost_price=ratio(cost,qty) if valid_quantity else None
        return dict(quantity=qty,cost_total=cost,cost=cost_price,price=price,
                    quote_note='；'.join(dict.fromkeys(r.get('quote_note','') for r in rows if r.get('quote_note'))),
                    market_value=strict_sum(r['market_value'] for r in rows),
                    pnl=strict_sum(r['pnl'] for r in rows),
                    rate=ratio(price-cost_price,cost_price) if price is not None and cost_price is not None else None)
    with localcontext() as context:
        context.prec=50
        broker_qty=strict_sum(r.values[4] for r in stocks(bundle.broker))
        people=[];all_prices=[]
        for person,name,book in bundle.persons:
            live,*_=live_holdings(book,instruments,quotes,session,now)
            by_key={(r.metadata['account_id'],r.metadata['instrument_id']):r for r in live.rows}
            channel_rows=defaultdict(list)
            for stock in stocks(book):
                if stock.values[4]==0:continue
                row=by_key.get((stock.metadata['account_id'],instrument_id))
                qty=stock.values[4]
                item=dict(quantity=qty,cost_total=row.values[9] if row else stock.values[10],
                          price=row.values[4] if row else stock.values[5],
                          market_value=row.values[5] if row else stock.values[6],
                          pnl=row.values[10] if row else stock.values[17],
                          quote_note=(row.metadata.get('live_status','')+' · '+str(row.metadata.get('live_quote_time') if row.metadata.get('quote_source')!='reference' else row.metadata.get('valuation_date','未知日期'))) if row else '原账本参考价 · '+str(stock.metadata.get('valuation_date','未知日期')))
                if not isinstance(item['price'],Decimal) or not item['price'].is_finite() or item['price']<=0:
                    item.update(price=None,market_value=None,pnl=None,quote_note='缺少有效价格，估值与盈亏未知')
                channel_rows[stock.values[0]].append(item);all_prices.append(item)
            if not channel_rows:continue
            brokers=[dict(channel=channel,**summarize(rows)) for channel,rows in sorted(channel_rows.items())]
            total=summarize(brokers)
            total.update(person_id=person,person_name=name,share=ratio(total['quantity'],broker_qty),brokers=brokers)
            if total['quantity'] is None or total['quantity']<0:total['warning']='持仓数量未知或负仓异常，未修正原值。'
            people.append(total)
        assigned=strict_sum(r['quantity'] for r in people)
        difference=broker_qty-assigned if broker_qty is not None and assigned is not None else None
        needle=search.strip().casefold()
        shown=[r for r in people if needle in r['person_name'].casefold()]
        shown.sort(key=lambda r:(r['quantity'] is None,-(r['quantity'] or Decimal(0)),r['person_name']))
        total=summarize(shown)
        total['share']=ratio(total['quantity'],broker_qty)
        broker_live,*_=live_holdings(bundle.broker,instruments,quotes,session,now)
        physical_prices=[dict(quantity=r.values[3],price=r.values[4]) for r in broker_live.rows if r.metadata['instrument_id']==instrument_id]
        price=unique_price(physical_prices or all_prices)
        valid_costs=[r for r in shown if r['quantity'] is not None and r['quantity']>0 and r['cost'] is not None]
        complete_costs=all(r['quantity'] is not None and r['quantity']>=0 for r in shown) and len(valid_costs)==sum(r['quantity'] is not None and r['quantity']>0 for r in shown)
        lowest=min(valid_costs,key=lambda r:r['cost']) if valid_costs else None
        highest=max(valid_costs,key=lambda r:r['cost']) if valid_costs else None
        reconciliation=[]
        keys={r.metadata['account_id'] for r in stocks(bundle.broker)}
        keys.update(r.metadata['account_id'] for _,_,b in bundle.persons for r in stocks(b))
        for account in sorted(keys):
            b=strict_sum(r.values[4] for r in stocks(bundle.broker) if r.metadata['account_id']==account)
            p=strict_sum(r.values[4] for _,_,book in bundle.persons for r in stocks(book) if r.metadata['account_id']==account)
            reconciliation.append(dict(account_id=account,broker=b,assigned=p,difference=b-p if b is not None and p is not None else None))
        return dict(choices=available,instrument_id=instrument_id,symbol=symbol,as_of=str(bundle.aggregate.scope.as_of),
                    rows=shown,total=total,summary=dict(price=price,broker_quantity=broker_qty,cost_total=total['cost_total'],
                    market_value=broker_qty*price if broker_qty is not None and price is not None else None,pnl=total['pnl'],
                    holders=sum(r['quantity'] is not None and r['quantity']>0 for r in shown),quote_note=total['quote_note']),
                    reconciliation=dict(broker=broker_qty,assigned=assigned,difference=difference,accounts=reconciliation,
                    ok=all(r['difference']==0 and r['broker'] is not None and r['broker']>=0 and r['assigned'] is not None and r['assigned']>=0 for r in reconciliation)),
                    cost_analysis=dict(lowest=dict(name=lowest['person_name'],cost=lowest['cost']) if lowest else None,
                    highest=dict(name=highest['person_name'],cost=highest['cost']) if highest else None,
                    weighted=total['cost'],complete=complete_costs),
                    note='币种 USD，EXCEL_COMPAT。成本沿用当前持仓净投入口径（累计买入−累计卖出，含原账本基准及费用）；占比始终除以券商总股数。搜索同步过滤人员侧概览、图表和明细，券商股数与核对基准保持全部人员范围。当前价按实际行情有效时点估值，历史日期使用不晚于截止日期的原口径价格；未知不补零。')


def export_csv(data):
    import csv,io
    output=io.StringIO();writer=csv.writer(output)
    from portfolio.services.export_service import safe_text
    def write(row):writer.writerow([safe_text(v) for v in row])
    write(('股票',data['symbol'],'截止日期',data['as_of']))
    write(('说明',data['note']))
    write(('券商股数',data['reconciliation']['broker'],'全部人员股数',data['reconciliation']['assigned'],'差异',data['reconciliation']['difference']))
    write(COLUMNS[:-1])
    def person_row(name,r):return (name,r['quantity'],r['share'],r['cost'],r['cost_total'],r['price'],r['market_value'],r['rate'],r['pnl'])
    for row in data['rows']:write(person_row(row['person_name'],row))
    write(person_row('合计',data['total']))
    write(('占比/收益率为小数比例；例如0.1表示10%。',))
    for row in data['rows']:
        write(('人员',row['person_name']));write(BROKER_COLUMNS)
        for b in row['brokers']:write((b['channel'],b['quantity'],b['cost'],b['cost_total'],b['price'],b['market_value'],b['pnl']))
    return output.getvalue()
