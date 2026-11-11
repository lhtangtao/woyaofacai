import React, { useEffect, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { api } from '../api/client';
import './ownership-analysis.css';

const money = (v: any) => v == null ? '—' : Number(v).toLocaleString(undefined, {minimumFractionDigits:2,maximumFractionDigits:2});
const price = (v:any) => v==null?'—':Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:8});
const qty = (v: any) => v == null ? '—' : Number(v).toLocaleString(undefined,{maximumFractionDigits:8});
const pct = (v: any) => v == null ? '—' : (Number(v)*100).toFixed(2)+'%';
const color = (v: any) => v == null ? undefined : Number(v)>0 ? 'var(--profit-color)' : Number(v)<0 ? 'var(--loss-color)' : undefined;
const headers = ['人员','持股数量','占比','成本价','成本总额','当前价','市值','收益率','累计盈亏','操作'];
const keys = ['person_name','quantity','share','cost','cost_total','price','market_value','rate','pnl'];
const closedHeaders = ['人员','历史买入股数','资金占比','买入均价','历史买入总额','卖出均价','历史卖出总额','收益率','清仓收益','操作'];
const closedKeys = ['person_name','buy_quantity','share','buy_avg','buy_total','sell_avg','sell_total','rate','pnl'];
const closedSortableCols = new Set([1,2,3,4,5,6,7,8]);
const openSortableCols = new Set([1,2,3,4,6,7,8]);
const today = () => new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});

export function OwnershipAnalysisView({refresh=0,reset=0}: {refresh?:number;reset?:number}) {
  const [holdingStatus,setHoldingStatus]=useState<'holding'|'closed'|'all'>('holding');
  const [instrument,setInstrument]=useState('');const [day,setDay]=useState(today());const [search,setSearch]=useState('');
  const [choices,setChoices]=useState<any[]>([]);const [stockSearch,setStockSearch]=useState('');
  const [data,setData]=useState<any>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(false);
  const [revision,setRevision]=useState(0);const [mode,setMode]=useState<"primary"|"share"|"pnl">("primary");
  const [selected,setSelected]=useState<number|null>(null);const [expanded,setExpanded]=useState<Set<number>>(new Set());
  const [sort,setSort]=useState({key:'quantity',desc:true});
  const resetLocal=()=>{setHoldingStatus('holding');setInstrument('');setStockSearch('');setDay(today());setSearch('');setSelected(null);setExpanded(new Set());setMode('primary');setSort({key:'quantity',desc:true});setRevision(v=>v+1);};
  useEffect(()=>{if(reset)resetLocal();},[reset]);
  useEffect(()=>{
    let active=true;setData(null);
    const load=()=>{setLoading(true);return api.ownershipAnalysis({instrument_id:instrument,as_of:day,person_search:search,holding_status:holdingStatus}).then((r:any)=>{if(active){setData(r);setChoices(r.choices||[]);setError('');}}).catch((e:any)=>{if(active)setError(e.message || '读取失败');}).finally(()=>{if(active)setLoading(false);});};
    const timer=window.setTimeout(load,250);const poll=window.setInterval(load,10000);
    return()=>{active=false;window.clearTimeout(timer);window.clearInterval(poll);};
  },[instrument,day,search,holdingStatus,revision,refresh]);

  const isClosed = Boolean(data?.is_closed);
  const activeHeaders = isClosed ? closedHeaders : headers;
  const activeKeys = isClosed ? closedKeys : keys;
  const activeSortable = isClosed ? closedSortableCols : openSortableCols;
  const rows=[...(data?.rows||[])].sort((a:any,b:any)=>{
    const x=a[sort.key],y=b[sort.key];if(x==null)return y==null?0:1;if(y==null)return -1;
    return (Number(x)-Number(y))*(sort.desc?-1:1);
  });
  const toggle=(id:number)=>setExpanded(prev=>{const next=new Set(prev);next.has(id)?next.delete(id):next.add(id);return next;});
  const format=(row:any,key:string)=>{
    if(key==='person_name') return row?.is_closed && !isClosed && row?.person_name && row.person_name!=='合计' ? `${row.person_name}（已清仓）` : row.person_name;
    if(key==='quantity'||key==='buy_quantity') return qty(row[key]);
    if(['share','rate'].includes(key)) return pct(row[key]);
    if(['price','buy_avg','sell_avg'].includes(key)) return price(row[key]);
    if(key==='cost' && row?.is_closed && !isClosed) return '—';
    return money(row[key]);
  };
  const total=data?.total;const summary=data?.summary;const costs=data?.cost_analysis;const rec=data?.reconciliation;
  const query=new URLSearchParams({as_of:day,person_search:search,holding_status:holdingStatus,export:'csv'});if(instrument)query.set('instrument_id',instrument);

  const chartField = isClosed ? (mode==='share' ? 'share' : 'pnl') : (mode==='pnl' ? 'pnl' : mode==='share' ? 'share' : 'quantity');
  const chartTitle = isClosed ? (mode==='share' ? '人员买入资金占比' : '人员清仓收益分布') : (mode==='pnl' ? '人员累计盈亏分布' : '人员持股分布');

  const switchStatus=(next:'holding'|'closed'|'all')=>{
    if(next===holdingStatus) return;
    setHoldingStatus(next);
    setInstrument('');
    setSelected(null);
    setExpanded(new Set());
    setSort({key:next==='closed'?'pnl':'quantity',desc:true});
  };

  return <div className="ownership-analysis">
    <div className="card-panel ownership-controls">
      <div role="group" aria-label="持仓状态筛选" style={{display:'inline-flex',gap:4,background:'#f1f5f9',padding:2,borderRadius:6,border:'1px solid #e2e8f0'}}>
        {([['holding','持仓中'],['closed','已清仓'],['all','全部']] as const).map(([val,lbl])=>(
          <button
            key={val}
            type="button"
            className="btn"
            aria-pressed={holdingStatus===val}
            onClick={()=>switchStatus(val)}
            style={{
              background: holdingStatus===val ? '#2563eb' : 'transparent',
              color: holdingStatus===val ? '#ffffff' : '#475569',
              border: 'none',
              fontWeight: holdingStatus===val ? 600 : 500,
              padding: '4px 10px',
              fontSize: 12,
            }}
          >{lbl}</button>
        ))}
      </div>
      <input aria-label="Ticker搜索" placeholder="搜索 Ticker" value={stockSearch} onChange={e=>setStockSearch(e.target.value)}/>
      <label>股票 <select aria-label="选择股票" value={instrument||String(data?.instrument_id||'')} onChange={e=>{setInstrument(e.target.value);setSelected(null);setExpanded(new Set());}}><option value="">默认股票</option>{choices.filter((c:any)=>(c.label||c.symbol).toUpperCase().includes(stockSearch.toUpperCase())).map((c:any)=><option key={c.id} value={c.id}>{c.label||c.symbol}</option>)}</select></label>
      <label>统计截止日期 <input type="date" aria-label="统计截止日期" max={today()} value={day} onChange={e=>{setDay(e.target.value);setInstrument('');setSelected(null);setExpanded(new Set());}}/></label>
      <input aria-label="人员搜索" placeholder="搜索人员" value={search} onChange={e=>setSearch(e.target.value)}/>
      <button className="btn" onClick={()=>setRevision(v=>v+1)}>刷新</button><button className="btn" onClick={resetLocal}>重置筛选</button>
      <a className="btn" href={data?`/api/v1/ownership?${query}`:undefined} aria-disabled={!data}>导出报表</a>
    </div>
    {loading&&<div role="status"><progress/> 正在读取全部人员持仓与真实行情…</div>}
    {error&&<div role="alert" className="card-panel">{error}</div>}
    {data&&<>
      <div className="ownership-source">数据源：{data.source?.path} · 截止 {data.as_of} · {data.source?.scope||'全部历史人员／全部渠道'} · {data.quote_status}</div>
      {!data.symbol&&<div className="card-panel">当前筛选条件与统计日期下没有符合条件的股票，请切换状态或调整日期。</div>}
      <div className="ownership-cards">
        {(isClosed
          ? [['当前参考价','price'],['历史买入总额','buy_total'],['历史卖出总额','sell_total'],['总收益率','rate'],['清仓总收益（人员）','pnl'],['参与人数','holders']]
          : [['当前价','price'],['总股数（券商）','broker_quantity'],['总成本总额','cost_total'],['总市值（券商）','market_value'],['累计盈亏（人员）','pnl'],['持有人数','holders']]
        ).map(([title,key])=><div className="card-panel" key={key}><span>{title}</span><strong style={{color:(key==='pnl'||key==='rate')?color(summary?.[key]):undefined}}>{key==='holders'?summary?.[key]:key==='broker_quantity'?qty(summary?.[key]):key==='price'?price(summary?.[key]):key==='rate'?pct(summary?.[key]):money(summary?.[key])}</strong></div>)}
      </div>
      <div className="card-panel" style={{color:rec?.ok?'var(--profit-color)':'var(--loss-color)'}}>
        {isClosed
          ? `核对：当前均已清仓（0 股） · 券商清仓收益 ${money(rec?.broker_pnl)} · 全部人员清仓收益 ${money(rec?.assigned_pnl)} · 归属差异 ${money(rec?.pnl_difference)} · ${rec?.ok?'一致':'存在差异或缺失，请核对'}`
          : `核对：券商 ${qty(rec?.broker)} 股 · 全部人员 ${qty(rec?.assigned)} 股 · 未分配／归属差异 ${qty(rec?.difference)} 股 · ${rec?.ok?'一致':'存在账户级差异或未知数量，请核对'}`
        }
        {!rec?.ok&&<details><summary>账户核对详情</summary>{rec?.accounts?.map((a:any)=><div key={a.account_id}>账户 {a.account_id}：{isClosed ? `券商收益 ${money(a.broker_pnl)}／人员收益 ${money(a.assigned_pnl)}／差额 ${money(a.pnl_difference)}` : `券商 ${qty(a.broker)}／人员 ${qty(a.assigned)}／差额 ${qty(a.difference)}`}</div>)}</details>}
      </div>
      <div className="ownership-middle">
        <div className="card-panel"><div className="ownership-controls"><strong>{chartTitle}</strong></div>
          <div className="ownership-controls">
            {isClosed ? (
              <>
                <button className="btn" aria-pressed={chartField==='pnl'} onClick={()=>setMode('primary')}>清仓收益</button>
                <button className="btn" aria-pressed={chartField==='share'} onClick={()=>setMode('share')}>资金占比</button>
              </>
            ) : (
              <>
                <button className="btn" aria-pressed={chartField==='quantity'} onClick={()=>setMode('primary')}>持股数量</button>
                <button className="btn" aria-pressed={chartField==='share'} onClick={()=>setMode('share')}>占比</button>
                <button className="btn" aria-pressed={chartField==='pnl'} onClick={()=>setMode('pnl')}>累计盈亏</button>
              </>
            )}
          </div>
          <ReactECharts style={{height:Math.max(240,(data.rows?.length||0)*38+60)}} option={{tooltip:{trigger:'axis',formatter:(params:any)=>{const r=data.rows[params[0].dataIndex];return isClosed ? `${r.person_name}：清仓收益 ${money(r.pnl)} USD · 买占 ${pct(r.share)}` : `${r.person_name}${r.is_closed?'（已清仓）':''}：${qty(r.quantity)} 股 · ${pct(r.share)} · 盈亏 ${money(r.pnl)}`;}} ,grid:{left:75,right:170,top:18,bottom:20},xAxis:{type:'value',axisLabel:{formatter:(v:number)=>chartField==='share'?`${(v*100).toFixed(0)}%`:v}},yAxis:{type:'category',inverse:true,data:data.rows.map((r:any)=>r.person_name)},series:[{type:'bar',barMaxWidth:18,data:data.rows.map((r:any)=>{const val=r[chartField]==null?null:Number(r[chartField]);const barColor=chartField==='pnl'?(Number(val)>=0?'#16a34a':'#dc2626'):'#3979f5';return {value:val,itemStyle:{color:barColor,opacity:selected==null||selected===r.person_id?1:.35}};}),label:{show:true,position:'right',formatter:(p:any)=>{const r=data.rows[p.dataIndex];if(isClosed||chartField==='pnl') return `${Number(r.pnl)>=0?'+':''}${money(r.pnl)} · ${isClosed?`买占 ${pct(r.share)}`:(r.is_closed?'已清仓':`${qty(r.quantity)} 股`)}`;return `${qty(r.quantity)} 股 · ${pct(r.share)}`;}}}]}} onEvents={{click:(p:any)=>setSelected(selected===data.rows[p.dataIndex].person_id?null:data.rows[p.dataIndex].person_id)}}/>
        </div>
        {isClosed ? (
          <div className="card-panel ownership-cost-summary">
            <h3>清仓收益摘要</h3>
            <p>最高收益：{costs?.best_pnl ? `${costs.best_pnl.name} · ${money(costs.best_pnl.pnl)}` : '—'}</p>
            <p>最低收益：{costs?.worst_pnl ? `${costs.worst_pnl.name} · ${money(costs.worst_pnl.pnl)}` : '—'}</p>
            <p>加权买入均价：{price(costs?.weighted)}</p>
            <p>加权卖出均价：{price(costs?.weighted_sell)}</p>
          </div>
        ) : (
          <div className="card-panel ownership-cost-summary"><h3>成本分析摘要</h3><p>最低成本：{costs?.lowest?`${costs.lowest.name} · ${money(costs.lowest.cost)}`:'—'}</p><p>最高成本：{costs?.highest?`${costs.highest.name} · ${money(costs.highest.cost)}`:'—'}</p><p>加权平均成本：{money(costs?.weighted)}</p>{!costs?.complete&&<p>部分成本缺失，极值仅比较有效成本。</p>}</div>
        )}
      </div>
      <div className="card-panel ownership-table"><h3>{isClosed ? '人员清仓收益明细' : '人员成本分析明细'}</h3><table><thead><tr>{activeHeaders.map((h,i)=><th key={h}><button disabled={!activeSortable.has(i)} onClick={()=>setSort({key:activeKeys[i],desc:sort.key===activeKeys[i]?!sort.desc:true})}>{h}{sort.key===activeKeys[i]?(sort.desc?' ↓':' ↑'):''}</button></th>)}</tr></thead><tbody>
        {rows.map((r:any)=><React.Fragment key={r.person_id}><tr className={selected===r.person_id?'ownership-selected':''} onClick={()=>setSelected(r.person_id)}>{activeKeys.map(k=><td key={k} style={{color:['rate','pnl'].includes(k)?color(r[k]):undefined}} title={k==='price'?r.quote_note:r.warning}>{format(r,k)}</td>)}<td><button className="btn" onClick={e=>{e.stopPropagation();toggle(r.person_id);}}>{expanded.has(r.person_id)?'收起':'查看券商明细'}</button></td></tr>
          {expanded.has(r.person_id)&&<tr><td colSpan={10}><table><thead><tr>{(isClosed ? ['券商','历史买入股数','买入均价','历史买入总额','卖出均价','历史卖出总额','清仓收益'] : ['券商','持股数量','成本价','成本总额','当前价','市值','累计盈亏']).map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{r.brokers.map((b:any)=><tr key={b.channel}><td>{b.channel}{b.is_closed&&!isClosed?'（已清仓）':''}</td>{(isClosed ? ['buy_quantity','buy_avg','buy_total','sell_avg','sell_total','pnl'] : ['quantity','cost','cost_total','price','market_value','pnl']).map(k=><td key={k} style={{color:k==='pnl'?color(b[k]):undefined}}>{format(b,k)}</td>)}</tr>)}</tbody></table></td></tr>}</React.Fragment>)}
      </tbody><tfoot><tr>{activeKeys.map(k=><td key={k}>{k==='person_name'?'合计':format({...total,person_name:'合计'},k)}</td>)}<td>—</td></tr></tfoot></table></div>
      <div className="ownership-source">{data.note}<br/>行情状态：{summary?.quote_note}</div>
    </>}
  </div>;
}
