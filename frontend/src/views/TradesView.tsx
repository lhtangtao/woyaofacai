import React,{useEffect,useRef,useState} from 'react';
import {requestApi} from '../api/client';
import {TradeDrawer} from '../components/TradeDrawer';
import {tradeDefaults,tradeLabels,tradeQuery,tradeMoney,tradeCell,tradeBroker,tradeCount,tradeTone} from './trade-query';
import './trades.css';

type Filters=Record<string,any>;
export function TradesView({persons,refreshKey=0}:{persons:any[];accounts?:any[];personId?:number|null;refreshKey?:number}) {
  const [draft,setDraft]=useState<Filters>(tradeDefaults),[applied,setApplied]=useState<Filters>(tradeDefaults);
  const [page,setPage]=useState(1),[size,setSize]=useState(200),[sort,setSort]=useState({sort_col:'trade_time',sort_dir:'desc'});
  const [data,setData]=useState<any>(null),[loading,setLoading]=useState(false),[error,setError]=useState('');
  const [advanced,setAdvanced]=useState(false),[hidden,setHidden]=useState<string[]>(['micro_tag','post_net']),[selected,setSelected]=useState<number|null>(null);
  const [reload,setReload]=useState(0);
  const requestKey=JSON.stringify({...applied,page,size,...sort,refreshKey,reload}),sequence=useRef(0);
  useEffect(()=>{const seq=++sequence.current,ctrl=new AbortController();setLoading(true);setError('');requestApi(`/api/v1/trade-center?${tradeQuery({...applied,page,size,...sort})}`,{signal:ctrl.signal}).then(r=>{if(sequence.current===seq){setData(r);if(r.page!==page)setPage(r.page)}}).catch(e=>{if(!ctrl.signal.aborted)setError(e.message)}).finally(()=>{if(sequence.current===seq)setLoading(false)});return()=>ctrl.abort()},[requestKey]);
  const set=(k:string,v:any)=>setDraft(d=>({...d,[k]:v}));
  const run=(filters=draft)=>{setApplied({...filters});setPage(1);setSelected(null)};
  const quick=(key:string,value:any)=>{const next={...applied,[key]:applied[key]===value?'':value,...(key==='side'?{kind:'MARKET_TRADE'}:{})};setDraft(next);run(next)};
  const options=(key:string)=>Array.from(new Set<string>((data?.options||[]).map((r:any)=>r[key]).filter(Boolean)));
  const select=(key:string,values:(string|{value:string;label:string})[],all:string)=><label>{tradeLabels[key]}<select value={draft[key]||''} onChange={e=>set(key,e.target.value)}><option value="">{all}</option>{values.map(v=>typeof v==='string'?<option key={v} value={v}>{{MARKET_TRADE:'市场交易',OWNER_TRANSFER:'人员划转',EXTERNAL_TRANSFER:'外部划转',BOOK_SETTLEMENT:'账务结算'}[v]||(key==='broker'?tradeBroker(v):v)}</option>:<option key={v.value} value={v.value}>{v.label}</option>)}</select></label>;
  const input=(key:string,type='text')=><label key={key}>{tradeLabels[key]}<input type={type} step="any" value={draft[key]||''} placeholder={key==='symbol'?'搜索 Ticker':key==='search'?'订单号、交易 ID、来源':''} onChange={e=>set(key,e.target.value)}/></label>;
  const currencies=(key:string)=>(data?.stats?.currencies||[]).map((c:any)=>`${data.stats.currencies.length>1?c.currency+'：':''}${c[key]==null?'—（未完整核实）':tradeMoney(c[key])}`).join(' / ')||'—';
  const sortKey=(key:string)=>({trade_date:'trade_time',cost_change:'cost_diff'}[key]||key);
  const sortClick=(key:string)=>{if(key==='action')return;setSort({sort_col:sortKey(key),sort_dir:sort.sort_col===sortKey(key)&&sort.sort_dir==='desc'?'asc':'desc'});setPage(1)};
  const query=React.useMemo(()=>({...applied,...sort}),[applied,sort]);
  const clear=()=>{const d=tradeDefaults();setDraft(d);run(d)};
  const remove=(key:string)=>{const next={...applied};if(key==='start_date'||key==='end_date')next[key]=tradeDefaults()[key];else delete next[key];setDraft(next);run(next)};
  const chipValue=(key:string,value:any)=>key==='person_id'?(data?.persons||persons).find((p:any)=>String(p.id)===String(value))?.name||String(value):key==='broker'?tradeBroker(value):({BUY:'买入',SELL:'卖出',TRANSFER_IN:'转入',TRANSFER_OUT:'转出',MARKET_TRADE:'市场交易',OWNER_TRANSFER:'人员划转',EXTERNAL_TRANSFER:'外部划转',BOOK_SETTLEMENT:'账务结算',true:'是'}[String(value)]||String(value));
  return <section className="tc-page">
    <div className="tc-page-actions"><button className="btn tc-primary" disabled={loading||!data} onClick={()=>window.open(`/api/v1/trade-center/export?${tradeQuery(query)}`,'_blank')}>↓ 导出报表</button></div>
    <div className="tc-stats">
      {[
        {title:'匹配交易总数',value:tradeCount(data?.stats?.matched),hint:'当前筛选 · 全部匹配记录',icon:'≡',tone:'blue',key:'',target:''},
        {title:'买入记录',value:tradeCount(data?.stats?.buys),hint:'买入总额 '+currencies('buy_amount'),icon:'↗',tone:'green',key:'side',target:'BUY'},
        {title:'卖出记录',value:tradeCount(data?.stats?.sells),hint:'卖出总额 '+currencies('sell_amount'),icon:'↘',tone:'red',key:'side',target:'SELL'},
        {title:'手续费合计',value:currencies('fees'),hint:'按币种核实汇总',icon:'◇',tone:'slate',key:'',target:''},
        {title:'待核查记录',value:tradeCount(data?.stats?.review),hint:'关联、解析或导入异常',icon:'◷',tone:'slate',key:'review_only',target:true}
      ].map(card=><button className={`tc-stat ${card.tone} ${card.key&&applied[card.key]===card.target?'active':''}`} key={card.title} disabled={loading||!card.key} aria-pressed={card.key?applied[card.key]===card.target:undefined} onClick={()=>card.key&&quick(card.key,card.target)}><span className="tc-stat-icon" aria-hidden="true">{card.icon}</span><span className="tc-stat-label">{card.title}</span><strong className={String(card.value).length>16?'compact':''}>{loading?'…':card.value}</strong><small title={card.hint}>{card.hint}</small></button>)}
    </div>
    <form className="tc-filters" onSubmit={e=>{e.preventDefault();run()}}>
      <div className="tc-filter-main">
        {select('person_id',(data?.persons||persons).map(p=>({value:String(p.id),label:p.name+(p.enabled===0?'（停用）':'')})),'全部授权人员')}
        {select('broker',options('broker_code'),'全部渠道')}{input('symbol')}
        <div className="tc-date-range"><span>日期范围 · 账务日期</span><div>{input('start_date','date')}<span>至</span>{input('end_date','date')}</div></div>
        {input('search')}
      </div>
      <div className="tc-filter-secondary">
        {select('side',[{value:'BUY',label:'买入'},{value:'SELL',label:'卖出'},{value:'TRANSFER_IN',label:'转入'},{value:'TRANSFER_OUT',label:'转出'}],'全部方向')}
        {select('category',options('category'),'全部分类')}{select('kind',options('kind'),'全部类型')}
        <div className="tc-actions"><button className="btn tc-primary" type="submit">查询</button><button className="btn" type="button" onClick={clear}>重置</button><button className="btn tc-text-button" type="button" aria-expanded={advanced} onClick={()=>setAdvanced(!advanced)}>高级筛选 {advanced?'▴':'▾'}</button></div>
      </div>
      {advanced&&<div className="tc-advanced">{['quantity_min','quantity_max','price_min','price_max','amount_min','amount_max','cost_min','cost_max'].map(k=>input(k,'number'))}{select('source_kind',options('source_kind'),'全部来源')}{input('source_file')}{select('link_status',['LINKED','LEGACY_UNLINKED','REVIEW_REQUIRED','NOT_APPLICABLE'],'全部关联状态')}{['review_only','affects_only'].map(k=><label className="tc-check" key={k}><input type="checkbox" checked={!!draft[k]} onChange={e=>set(k,e.target.checked)}/>{tradeLabels[k]}</label>)}<small>仅使用可核验字段筛选；历史核算与来源状态以详情为准。</small></div>}
      <div className="tc-chips">
        <button className="tc-chip" type="button" onClick={()=>{const n={...applied,...tradeDefaults()};setDraft(n);run(n)}}>日期：{applied.start_date||'不限'} 至 {applied.end_date===tradeDefaults().end_date?'今':applied.end_date||'不限'} ×</button>
        {Object.entries(applied).filter(([k,v])=>!['start_date','end_date'].includes(k)&&v!==''&&v!=null&&v!==false).map(([k,v])=><button className="tc-chip" type="button" key={k} onClick={()=>remove(k)}>{tradeLabels[k]||k}：{chipValue(k,v)} ×</button>)}
      </div>
    </form>
    {error&&<p role="alert" className="tc-error">查询失败：{error} <button className="btn" onClick={()=>setReload(n=>n+1)}>重试</button></p>}
    <div className="tc-body" aria-busy={loading}><div className="tc-list">
      <div className="tc-table-tools"><div><strong>交易记录</strong><span role="status">{loading?'正在读取记录…':`${tradeCount(data?.total??0)} 条匹配记录`}</span></div><details><summary>☷ 显示列</summary><div className="tc-column-menu">{data?.keys?.map((k:string,i:number)=><label key={k}><input type="checkbox" checked={!hidden.includes(k)} onChange={()=>setHidden(h=>h.includes(k)?h.filter(v=>v!==k):[...h,k])}/>{data.columns[i]}</label>)}</div></details></div>
      <div className="tc-table-scroll"><table><thead><tr>{data?.keys?.map((k:string,i:number)=>!hidden.includes(k)&&<th key={k} data-column={k} aria-sort={sort.sort_col===sortKey(k)?sort.sort_dir==='desc'?'descending':'ascending':'none'} onClick={()=>sortClick(k)}>{data.columns[i]} {sort.sort_col===sortKey(k)?sort.sort_dir==='desc'?'↓':'↑':''}</th>)}</tr></thead><tbody>
        {!loading&&!data?.items?.length&&<tr><td className="tc-empty" colSpan={data?.keys?.length||16}>没有符合查询条件的交易记录，请调整筛选条件。</td></tr>}
        {data?.items?.map((t:any)=><tr key={t.record_id} tabIndex={0} aria-selected={selected===t.record_id} className={selected===t.record_id?'selected':''} onClick={()=>setSelected(t.record_id)} onKeyDown={e=>{if(e.key==='Enter')setSelected(t.record_id)}}>{data.keys.map((k:string)=>!hidden.includes(k)&&<td key={k} data-column={k} className={k==='cost_change'?`tc-cost ${tradeTone(t.cost_diff)}`:''} title={k==='current_price'?`${t.quote_status} · ${t.quote_time||'无报价时间'}`:k==='post_cost'||k==='cost_change'?t.cost_note:tradeCell(t,k)}>{k==='side'?<span className={`tc-side ${t.side_code==='BUY'?'buy':t.side_code==='SELL'?'sell':'transfer'}`}>{t.side}</span>:tradeCell(t,k)}</td>)}</tr>)}
      </tbody></table></div>
      <footer><span>共 {tradeCount(data?.total??0)} 条 · 第 {data?.page??page} / {data?.total_pages??1} 页</span><div><select aria-label="每页条数" value={size} onChange={e=>{setSize(Number(e.target.value));setPage(1)}}>{[25,50,100,200,500,1000].map(n=><option key={n} value={n}>每页 {n} 条</option>)}</select><button className="btn" disabled={loading||page<=1} onClick={()=>setPage(p=>p-1)}>上一页</button><button className="btn" disabled={loading||page>=(data?.total_pages||1)} onClick={()=>setPage(p=>p+1)}>下一页</button></div></footer>
    </div>{selected!=null&&<TradeDrawer recordId={selected} query={query} onSelect={setSelected} onClose={()=>setSelected(null)}/>}</div>
    <small className="tc-source">{data?.note}</small>
  </section>;
}
