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
const today = () => new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});

export function OwnershipAnalysisView({refresh=0,reset=0}: {refresh?:number;reset?:number}) {
  const [instrument,setInstrument]=useState('');const [day,setDay]=useState(today());const [search,setSearch]=useState('');
  const [choices,setChoices]=useState<any[]>([]);const [stockSearch,setStockSearch]=useState('');
  const [data,setData]=useState<any>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(false);
  const [revision,setRevision]=useState(0);
  const [selected,setSelected]=useState<number|null>(null);const [expanded,setExpanded]=useState<Set<number>>(new Set());
  const [sort,setSort]=useState({key:'quantity',desc:true});
  const resetLocal=()=>{setInstrument('');setStockSearch('');setDay(today());setSearch('');setSelected(null);setExpanded(new Set());setSort({key:'quantity',desc:true});setRevision(v=>v+1);};
  useEffect(()=>{if(reset)resetLocal();},[reset]);
  useEffect(()=>{
    let active=true;setData(null);
    const load=()=>{setLoading(true);return api.ownershipAnalysis({instrument_id:instrument,as_of:day,person_search:search}).then((r:any)=>{if(active){setData(r);setChoices(r.choices||[]);setError('');}}).catch((e:any)=>{if(active)setError(e.message || '读取失败');}).finally(()=>{if(active)setLoading(false);});};
    const timer=window.setTimeout(load,250);const poll=window.setInterval(load,10000);
    return()=>{active=false;window.clearTimeout(timer);window.clearInterval(poll);};
  },[instrument,day,search,revision,refresh]);
  const rows=[...(data?.rows||[])].sort((a:any,b:any)=>{
    const x=a[sort.key],y=b[sort.key];if(x==null)return y==null?0:1;if(y==null)return -1;
    return (Number(x)-Number(y))*(sort.desc?-1:1);
  });
  const toggle=(id:number)=>setExpanded(prev=>{const next=new Set(prev);next.has(id)?next.delete(id):next.add(id);return next;});
  const format=(row:any,key:string)=>key==='person_name'?row.person_name:key==='quantity'?qty(row[key]):['share','rate'].includes(key)?pct(row[key]):key==='price'?price(row[key]):money(row[key]);
  const total=data?.total;const summary=data?.summary;const costs=data?.cost_analysis;const rec=data?.reconciliation;
  const query=new URLSearchParams({as_of:day,person_search:search,export:'csv'});if(instrument)query.set('instrument_id',instrument);
  return <div className="ownership-analysis">
    <div className="card-panel ownership-controls">
      <input aria-label="Ticker搜索" placeholder="搜索 Ticker" value={stockSearch} onChange={e=>setStockSearch(e.target.value)}/>
      <label>股票 <select aria-label="选择股票" value={instrument||String(data?.instrument_id||'')} onChange={e=>{setInstrument(e.target.value);setSelected(null);setExpanded(new Set());}}><option value="">默认股票</option>{choices.filter((c:any)=>(c.label||c.symbol).toUpperCase().includes(stockSearch.toUpperCase())).map((c:any)=><option key={c.id} value={c.id}>{c.label||c.symbol}</option>)}</select></label>
      <label>统计截止日期 <input type="date" aria-label="统计截止日期" max={today()} value={day} onChange={e=>setDay(e.target.value)}/></label>
      <input aria-label="人员搜索" placeholder="搜索人员" value={search} onChange={e=>setSearch(e.target.value)}/>
      <button className="btn" onClick={()=>setRevision(v=>v+1)}>刷新</button><button className="btn" onClick={resetLocal}>重置筛选</button>
      <a className="btn" href={data?`/api/v1/ownership?${query}`:undefined} aria-disabled={!data}>导出报表</a>
    </div>
    {loading&&<div role="status"><progress/> 正在读取全部人员持仓与真实行情…</div>}
    {error&&<div role="alert" className="card-panel">{error}</div>}
    {data&&<>
      <div className="ownership-source">数据源：{data.source?.path} · 截止 {data.as_of} · 全部历史人员／全部渠道 · {data.quote_status}</div>
      {!data.symbol&&<div className="card-panel">当前统计日期没有真实持仓股票，请调整日期。</div>}
      <div className="ownership-cards">{[['当前价','price'],['总股数（券商）','broker_quantity'],['总成本总额','cost_total'],['总市值（券商）','market_value'],['累计盈亏（人员）','pnl'],['持有人数','holders']].map(([title,key])=><div className="card-panel" key={key}><span>{title}</span><strong style={{color:key==='pnl'?color(summary?.[key]):undefined}}>{key==='holders'?summary?.[key]:key==='broker_quantity'?qty(summary?.[key]):key==='price'?price(summary?.[key]):money(summary?.[key])}</strong></div>)}</div>
      <div className="card-panel" style={{color:rec?.ok?'var(--profit-color)':'var(--loss-color)'}}>核对：券商 {qty(rec?.broker)} 股 · 全部人员 {qty(rec?.assigned)} 股 · 未分配／归属差异 {qty(rec?.difference)} 股 · {rec?.ok?'一致':'存在账户级差异或未知数量，请核对'}
        {!rec?.ok&&<details><summary>账户核对详情</summary>{rec?.accounts?.map((a:any)=><div key={a.account_id}>账户 {a.account_id}：券商 {qty(a.broker)}／人员 {qty(a.assigned)}／差额 {qty(a.difference)}</div>)}</details>}
      </div>
      <div className="ownership-middle">
        <div className="card-panel"><div className="ownership-controls"><strong>人员持股分布</strong></div>
          <ReactECharts style={{height:260}} option={{
            tooltip:{trigger:'item',formatter:(p:any)=>`${p.name}：${qty(p.value)} 股 (${pct(data.rows.find((x:any)=>x.person_name===p.name)?.share)})`},
            legend:{orient:'vertical',right:20,top:'middle',formatter:(name:string)=>{const r=data.rows.find((x:any)=>x.person_name===name);return r?`${name}   ${qty(r.quantity)} 股 · ${pct(r.share)}`:name;}},
            series:[{type:'pie',radius:['45%','70%'],center:['32%','50%'],itemStyle:{borderRadius:4,borderColor:'var(--panel-color,#fff)',borderWidth:2},label:{show:false},emphasis:{label:{show:true,fontSize:14,fontWeight:'bold',formatter:'{b}\n{d}%'}},data:data.rows.map((r:any)=>({name:r.person_name,value:Number(r.quantity),itemStyle:selected===r.person_id?{shadowBlur:12,shadowColor:'rgba(0,0,0,0.4)'}:undefined}))}]
          }} onEvents={{click:(p:any)=>{const r=data.rows[p.dataIndex];setSelected(selected===r?.person_id?null:r?.person_id);}}}/>
        </div>
        <div className="card-panel ownership-cost-summary"><h3>成本分析摘要</h3><p>最低成本：{costs?.lowest?`${costs.lowest.name} · ${money(costs.lowest.cost)}`:'—'}</p><p>最高成本：{costs?.highest?`${costs.highest.name} · ${money(costs.highest.cost)}`:'—'}</p><p>加权平均成本：{money(costs?.weighted)}</p>{!costs?.complete&&<p>部分成本缺失，极值仅比较有效成本。</p>}</div>
      </div>
      <div className="card-panel ownership-table"><h3>人员成本分析明细</h3><table><thead><tr>{headers.map((h,i)=><th key={h}><button disabled={![1,2,3,4,6,7,8].includes(i)} onClick={()=>setSort({key:keys[i],desc:sort.key===keys[i]?!sort.desc:true})}>{h}{sort.key===keys[i]?(sort.desc?' ↓':' ↑'):''}</button></th>)}</tr></thead><tbody>
        {rows.map((r:any)=><React.Fragment key={r.person_id}><tr className={selected===r.person_id?'ownership-selected':''} onClick={()=>setSelected(r.person_id)}>{keys.map(k=><td key={k} style={{color:['rate','pnl'].includes(k)?color(r[k]):undefined}} title={k==='price'?r.quote_note:r.warning}>{format(r,k)}</td>)}<td><button className="btn" onClick={e=>{e.stopPropagation();toggle(r.person_id);}}>{expanded.has(r.person_id)?'收起':'查看券商明细'}</button></td></tr>
          {expanded.has(r.person_id)&&<tr><td colSpan={10}><table><thead><tr>{['券商','持股数量','成本价','成本总额','当前价','市值','累计盈亏'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{r.brokers.map((b:any)=><tr key={b.channel}><td>{b.channel}</td>{['quantity','cost','cost_total','price','market_value','pnl'].map(k=><td key={k} style={{color:k==='pnl'?color(b[k]):undefined}}>{format(b,k)}</td>)}</tr>)}</tbody></table></td></tr>}</React.Fragment>)}
      </tbody><tfoot><tr>{keys.map(k=><td key={k}>{k==='person_name'?'合计':format(total,k)}</td>)}<td>—</td></tr></tfoot></table></div>
      <div className="ownership-source">{data.note}<br/>行情状态：{summary?.quote_note}</div>
    </>}
  </div>;
}
