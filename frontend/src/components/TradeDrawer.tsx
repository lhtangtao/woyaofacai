import {Fragment, useEffect, useState} from 'react';
import {X} from 'lucide-react';
import {requestApi} from '../api/client';
import {
  tradeQuery, tradeMoney, tradeMoneyLabels, tradeCostStatus, tradeTone, tradeTimeDisplay,
  tradeQuoteInfo, tradeSideTone, tradeOptionLabel,
} from '../views/trade-query';

const tabs = ['交易概览','成本变化','来源追溯','关联记录'] as const;
type Tab = typeof tabs[number];
const execution = ['成交金额','数量','成交价','当前价','手续费','方向/类型'];
const identity = ['人员','券商/账户','分类','交易时间','订单编号','交易记录ID'];
const source = ['数据来源','订单编号','交易记录ID','原始记录ID','来源文件','导入批次','成交编号','工作表','原始行号'];
const verification = ['归属关联状态','费用来源','费用核实状态','解析状态','记录状态','账务角色','解析/审计说明'];
const compared = ['成交前持仓','成交后持仓','成交前成本价','成交后成本价','成交后净投入'];
const costExplanation = ['每股成本变化','成本状态','计算说明'];
const fieldLabels:Record<string,string> = {'数量':'成交数量','方向/类型':'交易方向','人员':'归属人员','券商/账户':'券商渠道 / 账户','分类':'股票分类'};
type Fields = Record<string, any>;

function shownValue(key:string, value:any) {
  if (value == null || value === '') return '—';
  if (tradeMoneyLabels.has(key)) return tradeMoney(value);
  return ['数据来源','归属关联状态'].includes(key) ? tradeOptionLabel(String(value)) : String(value);
}

function DetailCard({title, fields, keys}: {title:string; fields:Fields; keys:string[]}) {
  const present = keys.filter(key => key in fields);
  if (!present.length) return null;
  return <section className="tc-detail-card">
    <h3>{title}</h3>
    <dl>{present.filter(key => !fields[key] || typeof fields[key] !== 'object').map(key => <Fragment key={key}>
      <dt>{fieldLabels[key] || key}</dt><dd>{shownValue(key,fields[key])}</dd>
    </Fragment>)}</dl>
    {present.filter(key => fields[key] && typeof fields[key] === 'object').map(key => <details key={key}>
      <summary>{key} · 展开证据</summary><pre>{JSON.stringify(fields[key],null,2)}</pre>
    </details>)}
  </section>;
}

export function TradeDrawer({recordId, query, onClose, onSelect}: {
  recordId:number; query:Record<string,any>; onClose:()=>void; onSelect:(id:number)=>void;
}) {
  const [tab,setTab] = useState<Tab>('交易概览');
  const [retry,setRetry] = useState(0);
  const [result,setResult] = useState<{url:string; retry:number; data?:any; error?:string}>();
  const url = `/api/v1/trade-center/${recordId}?${tradeQuery(query)}`;
  const current = result?.url === url && result.retry === retry ? result : undefined;
  const data = current?.data;
  const item = data?.item;
  const cost = item ? tradeCostStatus(item) : '';
  const overview = data?.sections?.['交易概览'] || {};
  const time = item ? tradeTimeDisplay(item.trade_date, overview['真实成交UTC'] || null) : '';
  useEffect(() => {
    const controller = new AbortController();
    requestApi(url,{signal:controller.signal})
      .then(response => {if (!controller.signal.aborted) setResult({url,retry,data:response});})
      .catch(error => {if (!controller.signal.aborted) setResult({url,retry,error:error.message});});
    return () => controller.abort();
  },[url,retry]);
  useEffect(() => {
    const close = (event:globalThis.KeyboardEvent) => {if (event.key === 'Escape') onClose();};
    window.addEventListener('keydown',close);
    return () => window.removeEventListener('keydown',close);
  },[onClose]);

  const fields:Fields = data?.sections?.[tab] || {};
  const extra = (used:string[]) => Object.keys(fields).filter(key => !used.includes(key));
  const side = item && <span className={`tc-side ${tradeSideTone(item.side_code,item.kind_code)}`}>{item.side}</span>;

  return <aside className="tc-drawer" aria-label="交易详情">
    <header><strong>交易详情</strong><button className="btn tc-drawer-close" onClick={onClose} aria-label="关闭详情"><X size={16}/></button></header>
    {item && <div className="tc-drawer-identity">
      <div><strong>{item.symbol}</strong>{side}<span className={`tc-cost-status ${tradeTone(item.cost_diff)}`}>{cost}</span></div>
      <small>{[time,item.person_name,item.broker].filter(Boolean).join(' · ')}</small>
    </div>}
    <nav aria-label="详情分类">{tabs.map(name => <button className={`btn ${tab === name ? 'active' : ''}`} key={name} aria-pressed={tab === name} onClick={() => setTab(name)}>{name}</button>)}</nav>
    <div className="tc-drawer-content" key={url} aria-busy={!current}>
      {current?.error ? <p role="alert" className="tc-error">详情读取失败 · {current.error} <button className="btn" onClick={() => setRetry(n => n+1)}>重试</button></p>
      : !item ? <p role="status">正在读取该笔交易详情…</p>
      : tab === '交易概览' ? <>
        <section className="tc-detail-card">
          <h3>成交信息</h3>
          <dl className="tc-execution">{execution.map(key => <div key={key}>
            <dt>{fieldLabels[key] || key}</dt>
            <dd className={key === '成交金额' ? 'tc-execution-total' : ''}>
              {key === '方向/类型' ? side : shownValue(key,fields[key])}
            </dd>
            {key === '成交金额' && item.currency && <small>{item.currency}</small>}
            {key === '当前价' && <small>{tradeQuoteInfo(item)}</small>}
            {key === '手续费' && fields[key] == null && <small>尚未核实</small>}
          </div>)}</dl>
        </section>
        <DetailCard title="基本信息" fields={{...fields,'交易时间':time}} keys={identity}/>
        <DetailCard title="补充信息" fields={fields} keys={extra([...execution,...identity,'报价时间','行情状态'])}/>
      </> : tab === '成本变化' ? <>
        <section className="tc-detail-card">
          <h3>持仓与成本</h3>
          <table className="tc-cost-table">
            <thead><tr><th scope="col">指标</th><th scope="col">成交前</th><th scope="col">成交后</th></tr></thead>
            <tbody>{[
              ['持仓数量','成交前持仓','成交后持仓'],
              ['每股成本价','成交前成本价','成交后成本价'],
              ['净投入',null,'成交后净投入'],
            ].map(([label,before,after]) => <tr key={label}>
              <th scope="row">{label}</th><td>{before ? shownValue(before,fields[before]) : '—'}</td><td>{shownValue(after!,fields[after!])}</td>
            </tr>)}</tbody>
          </table>
        </section>
        <div className={`tc-cost-callout ${tradeTone(item.cost_diff)}`}>
          <strong>成本变化 · {cost}</strong>
          <p>{fields['计算说明'] || '暂无可用的成本说明。'}</p>
        </div>
        <DetailCard title="核算信息" fields={fields} keys={['成本状态',...extra([...compared,...costExplanation])]}/>
      </> : tab === '来源追溯' ? <>
        <DetailCard title="来源记录" fields={{...fields,'交易记录ID':item.record_id}} keys={source}/>
        <DetailCard title="核查状态" fields={fields} keys={verification}/>
        <DetailCard title="来源证据" fields={fields} keys={extra([...source,...verification])}/>
      </> : <>
        <p className="tc-related-note">依据原始成交、同账户证券订单或人员划转来源关联。</p>
        {data.related?.length ? data.related.map((row:any) => <button className="tc-related" key={row.record_id} onClick={() => onSelect(row.record_id)}>
          <strong>{row.symbol} · {row.side}</strong><small>{tradeTimeDisplay(row.trade_date)} · {row.person_name || '—'} · #{row.record_id}</small>
        </button>) : <p>没有可靠关联记录。</p>}
        {data.related_truncated && <p>仅列出前 200 条关联记录。</p>}
      </>}
    </div>
    <footer>
      <button className="btn" disabled={!data?.navigation?.previous_id} onClick={() => onSelect(data.navigation.previous_id)}>上一笔</button>
      <span>{data?.navigation?.position ? `${data.navigation.position} / ${data.navigation.matched}` : item ? '关联记录' : ''}</span>
      <button className="btn" disabled={!data?.navigation?.next_id} onClick={() => onSelect(data.navigation.next_id)}>下一笔</button>
    </footer>
  </aside>;
}
