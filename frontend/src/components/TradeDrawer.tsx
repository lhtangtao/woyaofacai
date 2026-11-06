import React, {useEffect,useState} from 'react';
import {requestApi} from '../api/client';
import {tradeQuery,tradeMoney,tradeMoneyLabels,tradeCell} from '../views/trade-query';

export function TradeDrawer({recordId,query,onClose,onSelect}:{recordId:number;query:Record<string,any>;onClose:()=>void;onSelect:(id:number)=>void}) {
  const [data,setData]=useState<any>(null),[tab,setTab]=useState('交易概览'),[error,setError]=useState('');
  useEffect(()=>{const ctrl=new AbortController();setData(null);setError('');requestApi(`/api/v1/trade-center/${recordId}?${tradeQuery(query)}`,{signal:ctrl.signal}).then(setData).catch(e=>{if(!ctrl.signal.aborted)setError(e.message)});return()=>ctrl.abort()},[recordId,query]);
  const show=(v:any):React.ReactNode=>v==null||v===''?'—':typeof v==='object'?<pre>{JSON.stringify(v,null,2)}</pre>:String(v);
  return <aside className="tc-drawer" aria-label="交易详情"><header><strong>{data?.item?.symbol || '交易详情'} · {data?.item?.side}</strong><button className="btn" onClick={onClose} aria-label="关闭详情">×</button></header>
    <small>{data?.item?.trade_date}</small><nav>{['交易概览','成本变化','来源追溯','关联记录'].map(t=><button className={`btn ${tab===t?'active':''}`} key={t} onClick={()=>setTab(t)}>{t}</button>)}</nav>
    {error?<p role="alert">{error}</p>:!data?<p role="status">正在读取该笔交易与历史成本…</p>:tab==='关联记录'?<div><p>仅显示同一账户证券订单、已关联成交或同一人员划转来源。</p>{data.related?.length?data.related.map((r:any)=><button className="tc-related" key={r.record_id} onClick={()=>onSelect(r.record_id)}>{r.trade_date} · {r.symbol} · {r.person_name} · {r.side} · #{r.record_id}</button>):<p>没有可靠关联记录。</p>}{data.related_truncated&&<p>仅列出前200条关联记录。</p>}</div>:<dl>{Object.entries(data.sections?.[tab]||{}).map(([k,v])=><React.Fragment key={k}><dt>{k}</dt><dd>{tradeMoneyLabels.has(k)?tradeMoney(v):k==='每股成本变化'?tradeCell(data.item,'cost_change'):show(v)}</dd></React.Fragment>)}</dl>}
    <footer><button className="btn" disabled={!data?.navigation?.previous_id} onClick={()=>onSelect(data.navigation.previous_id)}>上一笔</button><span>{data?.navigation?.position?`${data.navigation.position} / ${data.navigation.matched}`:'关联记录'}</span><button className="btn" disabled={!data?.navigation?.next_id} onClick={()=>onSelect(data.navigation.next_id)}>下一笔</button></footer>
  </aside>;
}
