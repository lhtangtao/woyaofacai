import React, {useEffect,useState} from 'react';
import {requestApi} from '../api/client';
import {tradeQuery,tradeMoney,tradeMoneyLabels,tradeCell,tradeTone} from '../views/trade-query';

const groups:Record<string,[string,string[]][]>={
  '交易概览':[['基本信息',['交易时间','订单编号','交易记录ID','人员','券商/账户','Ticker','分类','方向/类型','划转对象','微操标记']],['成交与行情',['当前价','报价时间','行情状态','成交价','数量','成交金额','手续费','币种']]],
  '成本变化':[['持仓与成本变化',['成交前持仓','成交后持仓','成交前成本价','成交后成本价','每股成本变化','成交后净投入','本笔已实现盈亏']],['核算状态',['成本状态','计算说明','已实现盈亏说明']]],
  '来源追溯':[['来源记录',['数据来源','原始记录ID','订单编号','成交编号','导入批次','来源文件','工作表','原始行号']],['核对状态',['归属关联状态','费用来源','费用核实状态','解析状态','记录状态','账务角色','解析/审计说明']]]
};
export function TradeDrawer({recordId,query,onClose,onSelect}:{recordId:number;query:Record<string,any>;onClose:()=>void;onSelect:(id:number)=>void}) {
  const [data,setData]=useState<any>(null),[tab,setTab]=useState('交易概览'),[error,setError]=useState('');
  useEffect(()=>{const ctrl=new AbortController();setData(null);setError('');requestApi(`/api/v1/trade-center/${recordId}?${tradeQuery(query)}`,{signal:ctrl.signal}).then(r=>{if(!ctrl.signal.aborted)setData(r)}).catch(e=>{if(!ctrl.signal.aborted)setError(e.message)});return()=>ctrl.abort()},[recordId,query]);
  useEffect(()=>{const close=(e:KeyboardEvent)=>{if(e.key==='Escape')onClose()};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close)},[onClose]);
  const fields=data?.sections?.[tab]||{};
  const sections=groups[tab]||[],used=new Set(sections.flatMap(([,keys])=>keys));
  const remaining=Object.keys(fields).filter(k=>!used.has(k));
  const show=(key:string,value:any)=>value==null||value===''?'—':tradeMoneyLabels.has(key)?tradeMoney(value):key==='每股成本变化'?tradeCell(data.item,'cost_change'):String(value);
  return <aside className="tc-drawer" aria-label="交易详情"><header><strong>{data?.item?.symbol || '交易详情'}{data?.item?.side&&` · ${data.item.side}`}</strong><button className="btn" onClick={onClose} aria-label="关闭详情">×</button></header>
    <small>{data?.item?.trade_date||'按需读取当前记录'}</small><nav aria-label="详情分类">{['交易概览','成本变化','来源追溯','关联记录'].map(t=><button className={`btn ${tab===t?'active':''}`} key={t} aria-pressed={tab===t} onClick={()=>setTab(t)}>{t}</button>)}</nav>
    <div className="tc-drawer-content">
      {error?<p role="alert" className="tc-error">{error}</p>:!data?<p role="status">正在读取该笔交易与历史成本…</p>:tab==='关联记录'?<div><p>同一账户证券订单、已关联成交或人员划转来源。</p>{data.related?.length?data.related.map((r:any)=><button className="tc-related" key={r.record_id} onClick={()=>onSelect(r.record_id)}><strong>{r.symbol} · {r.side}</strong><br/>{r.trade_date} · {r.person_name} · #{r.record_id}</button>):<p>没有可靠关联记录。</p>}{data.related_truncated&&<p>仅列出前200条关联记录。</p>}</div>:
        [...sections,...(remaining.length?[['补充信息',remaining] as [string,string[]]]:[])].map(([title,keys])=>keys.some(k=>k in fields)&&<section className="tc-detail-card" key={title}><h3>{title}</h3><dl>{keys.filter(k=>k in fields&&!(fields[k]&&typeof fields[k]==='object')).map(k=><React.Fragment key={k}><dt>{k}</dt><dd className={`${['成交金额','当前价','成交后成本价','每股成本变化'].includes(k)?'important':''} ${k==='每股成本变化'?tradeTone(data.item.cost_diff):''}`}>{show(k,fields[k])}</dd></React.Fragment>)}</dl>{keys.filter(k=>fields[k]&&typeof fields[k]==='object').map(k=><details key={k}><summary>{k} · 展开原始证据</summary><pre>{JSON.stringify(fields[k],null,2)}</pre></details>)}</section>)
      }
    </div>
    <footer><button className="btn" disabled={!data?.navigation?.previous_id} onClick={()=>onSelect(data.navigation.previous_id)}>上一笔</button><span>{data?.navigation?.position?`${data.navigation.position} / ${data.navigation.matched}`:'关联记录'}</span><button className="btn" disabled={!data?.navigation?.next_id} onClick={()=>onSelect(data.navigation.next_id)}>下一笔</button></footer>
  </aside>;
}
