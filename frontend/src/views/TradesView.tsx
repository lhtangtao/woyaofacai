import {useEffect, useMemo, useRef, useState, type KeyboardEvent} from 'react';
import {ArrowDownRight, ArrowUpRight, Clock, Coins, Layers, Settings2} from 'lucide-react';
import {requestApi} from '../api/client';
import {TradeDrawer} from '../components/TradeDrawer';
import {
  tradeDefaults, tradeLabels, tradeQuery, tradeMoney, tradeCell, tradeBroker, tradeCount,
  tradeTone, tradePresets, tradeNumericKeys, tradeSortKey, tradeOptionLabel, tradeQuoteInfo,
  tradeSideTone, type TradePreset,
} from './trade-query';
import './trades.css';

type Filters = Record<string, any>;
const presetNames = Object.keys(tradePresets) as TradePreset[];
const bounds = ['quantity_min','quantity_max','price_min','price_max','amount_min','amount_max','cost_min','cost_max'];

export function TradesView({persons, refreshKey=0}: {persons:any[]; accounts?:any[]; personId?:number|null; refreshKey?:number}) {
  const [draft, setDraft] = useState<Filters>(tradeDefaults);
  const [applied, setApplied] = useState<Filters>(tradeDefaults);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(50);
  const [sort, setSort] = useState({sort_col:'trade_time',sort_dir:'desc'});
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [advanced, setAdvanced] = useState(false);
  const [preset, setPreset] = useState<TradePreset>('日常查看');
  const [custom, setCustom] = useState<Partial<Record<TradePreset, string[]>>>({});
  const [selected, setSelected] = useState<number|null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [reload, setReload] = useState(0);
  const tableScroll = useRef<HTMLDivElement>(null);
  const query = useMemo(() => ({...applied, ...sort}), [applied, sort]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    requestApi(`/api/v1/trade-center?${tradeQuery({...query,page,size})}`, {signal:controller.signal})
      .then(result => {
        if (controller.signal.aborted) return;
        setData(result);
        if (result.page !== page) setPage(result.page);
      })
      .catch(err => { if (!controller.signal.aborted) setError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, page, size, refreshKey, reload]);

  const set = (key:string, value:any) => setDraft(previous => ({...previous,[key]:value}));
  const run = (filters=draft) => {
    setApplied({...filters});
    setPage(1);
    setSelected(null);
    setDrawerOpen(false);
    if (tableScroll.current) tableScroll.current.scrollTop = 0;
  };
  const applyAndSync = (filters:Filters) => { setDraft(filters); run(filters); };
  const quick = (key:string, value:any) => {
    const active = applied[key] === value && (key !== 'side' || applied.kind === 'MARKET_TRADE');
    applyAndSync({...applied,[key]:active ? '' : value,...(key === 'side' ? {kind:'MARKET_TRADE'} : {})});
  };
  const open = (id:number) => { setSelected(id); setDrawerOpen(true); };
  const openRow = (id:number) => { if (!loading && !error) open(id); };
  const close = () => {
    setDrawerOpen(false);
    tableScroll.current?.querySelector<HTMLElement>('tr[aria-selected="true"]')?.focus({preventScroll:true});
  };
  const people = data?.persons || persons;
  const options = (key:string) => Array.from(new Set<string>((data?.options || []).map((row:any) => row[key]).filter(Boolean)));
  const optionText = (key:string, value:string) => {
    if (key === 'broker') return tradeBroker(value);
    return ['side','kind','source_kind','link_status'].includes(key) ? tradeOptionLabel(value) : value;
  };
  const select = (key:string, values:string[], all:string) => (
    <label>{tradeLabels[key]}
      <select value={draft[key] || ''} onChange={e => set(key, e.target.value)}>
        <option value="">{all}</option>
        {values.map(value => <option key={value} value={value}>{optionText(key,value)}</option>)}
      </select>
    </label>
  );
  const input = (key:string, type='text') => (
    <label key={key}>{tradeLabels[key]}
      <input type={type} step="any" value={draft[key] || ''} placeholder={key === 'symbol' ? '搜索 Ticker' : key === 'search' ? '订单号、交易 ID、来源' : undefined} onChange={e => set(key,e.target.value)}/>
    </label>
  );
  const remove = (key:string) => {
    const next = {...applied};
    delete next[key];
    applyAndSync(next);
  };
  const chipValue = (key:string, value:any) => key === 'person_id'
    ? people.find((person:any) => String(person.id) === String(value))?.name || String(value)
    : value === true ? '是' : optionText(key, String(value));
  const stats = data?.stats;
  const currencies = (key:string) => (stats?.currencies || []).map((entry:any) => {
    const currency = stats.currencies.length > 1 ? entry.currency + ' ' : '';
    return currency + (entry[key] == null ? '—（未完整核实）' : tradeMoney(entry[key]));
  }).join(' / ') || '—';
  const cards = [
    {title:'匹配交易总数',value:tradeCount(stats?.matched),hint:stats ? `买入 ${tradeCount(stats.buys)} · 卖出 ${tradeCount(stats.sells)} · 其他 —` : '买入 · 卖出 · 其他',tooltip:'汇总接口未提供其他交易类型笔数',Icon:Layers,tone:'blue'},
    {title:'买入记录',value:tradeCount(stats?.buys),hint:'买入总额 ' + currencies('buy_amount'),Icon:ArrowUpRight,tone:'green',key:'side',target:'BUY'},
    {title:'卖出记录',value:tradeCount(stats?.sells),hint:'卖出总额 ' + currencies('sell_amount'),Icon:ArrowDownRight,tone:'red',key:'side',target:'SELL'},
    {title:'手续费合计',value:currencies('fees'),hint:'按币种核实汇总',Icon:Coins,tone:'blue'},
    {title:'待核查记录',value:tradeCount(stats?.review),hint:'关联、解析或导入异常',Icon:Clock,tone:'blue',key:'review_only',target:true},
  ];
  const allKeys:string[] = data?.keys || [];
  const baseOrder = [...(tradePresets[preset] || allKeys)];
  const columnOrder = [...baseOrder, ...allKeys.filter(key => !baseOrder.includes(key))];
  const shown = custom[preset] || baseOrder;
  const visible = columnOrder.filter(key => shown.includes(key) && allKeys.includes(key));
  const title = (key:string) => key === 'category' ? '股票分类'
    : key === 'cost_change' && preset === '成本分析' ? '每股成本变化' : data.columns[allKeys.indexOf(key)];
  const sortColumn = (key:string) => {
    const sort_col = tradeSortKey(key);
    setSort({sort_col, sort_dir:sort.sort_col === sort_col && sort.sort_dir === 'desc' ? 'asc' : 'desc'});
    setPage(1);
  };
  const rowKey = (event:KeyboardEvent, id:number) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openRow(id); }
  };

  return <section className="tc-page">
    <div className="tc-main">
      <div className="tc-page-actions"><button className="btn tc-primary" disabled={loading || !data} onClick={() => window.open(`/api/v1/trade-center/export?${tradeQuery(query)}`,'_blank')}>↓ 导出报表</button></div>
      <div className="tc-stats">
        {cards.map(({title,value,hint,tooltip,Icon,tone,key,target}) => {
          const content = <>
            <span className="tc-stat-icon"><Icon size={18} strokeWidth={1.8} aria-hidden="true"/></span>
            <span className="tc-stat-label">{title}</span>
            <strong className={String(value).length > 16 ? 'compact' : ''} title={value}>{loading ? '…' : value}</strong>
            <small title={tooltip ? hint + '。' + tooltip : hint}>{hint}</small>
          </>;
          if (!key) return <div key={title} className={`tc-stat ${tone}`}>{content}</div>;
          const active = applied[key] === target && (key !== 'side' || applied.kind === 'MARKET_TRADE');
          return <button key={title} className={`tc-stat ${tone} ${active ? 'active' : ''}`} disabled={loading || !data} aria-pressed={active} onClick={() => quick(key,target)}>{content}</button>;
        })}
      </div>
      <form className="tc-filters" onSubmit={event => {event.preventDefault(); run();}}>
        <div className="tc-filter-main">
          <label>{tradeLabels.person_id}<select value={draft.person_id || ''} onChange={e => set('person_id',e.target.value)}>
            <option value="">全部授权人员</option>
            {people.map((person:any) => <option key={person.id} value={person.id}>{person.name}{person.enabled === 0 ? '（停用）' : ''}</option>)}
          </select></label>
          {select('broker',options('broker_code'),'全部渠道')}
          {input('symbol')}
          <div className="tc-date-range"><span>交易日期 · 账务日期</span><div>{input('start_date','date')}<span>至</span>{input('end_date','date')}</div></div>
          {input('search')}
        </div>
        <div className="tc-actions">
          <button className="btn tc-primary" type="submit">查询</button>
          <button className="btn" type="button" onClick={() => applyAndSync(tradeDefaults())}>重置</button>
          <button className="btn tc-text-button" type="button" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}>高级筛选 {advanced ? '▴' : '▾'}</button>
        </div>
        {advanced && <div className="tc-advanced">
          {select('side',['BUY','SELL','TRANSFER_IN','TRANSFER_OUT'],'全部方向')}
          {select('category',options('category'),'全部分类')}
          {select('kind',options('kind'),'全部类型')}
          {bounds.map(key => input(key,'number'))}
          {select('source_kind',options('source_kind'),'全部来源')}
          {input('source_file')}
          {select('link_status',['LINKED','LEGACY_UNLINKED','REVIEW_REQUIRED','NOT_APPLICABLE'],'全部关联状态')}
          {['review_only','affects_only'].map(key => <label className="tc-check" key={key}><input type="checkbox" checked={!!draft[key]} onChange={e => set(key,e.target.checked)}/>{tradeLabels[key]}</label>)}
        </div>}
        <div className="tc-chips">
          <span>已选条件</span>
          <button className="tc-chip" type="button" title="恢复默认日期范围" onClick={() => applyAndSync({...applied,...tradeDefaults()})}>日期 {applied.start_date || '不限'} 至 {applied.end_date || '不限'} ×</button>
          {Object.entries(applied).filter(([key,value]) => !['start_date','end_date'].includes(key) && value !== '' && value != null && value !== false)
            .map(([key,value]) => <button className="tc-chip" type="button" key={key} onClick={() => remove(key)}>{tradeLabels[key] || key} {chipValue(key,value)} ×</button>)}
        </div>
      </form>
      {error && <p role="alert" className="tc-error">查询失败 · {error} <button className="btn" onClick={() => setReload(n => n+1)}>重试</button></p>}
      <div className="tc-list" aria-busy={loading}>
        <div className="tc-table-tools">
          <div><strong>交易记录</strong><span role="status">{loading ? '正在读取记录…' : `共 ${tradeCount(data?.total ?? 0)} 条`}</span></div>
          <div className="tc-segments" role="group" aria-label="显示方案">
            {presetNames.map(name => <button key={name} className="btn" aria-pressed={name === preset} onClick={() => setPreset(name)}>{name}</button>)}
          </div>
          <details className="tc-columns"><summary><Settings2 size={14}/> 显示列</summary>
            <div className="tc-column-menu">
              {columnOrder.filter(key => allKeys.includes(key)).map(key => <label key={key}>
                <input type="checkbox" checked={visible.includes(key)} disabled={visible.length === 1 && visible.includes(key)}
                  onChange={() => setCustom({...custom,[preset]:visible.includes(key) ? visible.filter(column => column !== key) : [...visible,key]})}/>{title(key)}
              </label>)}
              {custom[preset] && <button className="btn" onClick={() => setCustom({...custom,[preset]:undefined})}>恢复方案列</button>}
            </div>
          </details>
        </div>
        <div className="tc-table-scroll" ref={tableScroll}><table>
          <thead><tr>{visible.map(key => {
            const sorted = sort.sort_col === tradeSortKey(key);
            return <th key={key} data-column={key} className={tradeNumericKeys.has(key) ? 'numeric' : ''}
              aria-sort={sorted ? sort.sort_dir === 'desc' ? 'descending' : 'ascending' : undefined}>
              {key === 'micro_tag' ? title(key) : <button onClick={() => sortColumn(key)}>{title(key)} {sorted ? sort.sort_dir === 'desc' ? '↓' : '↑' : ''}</button>}
            </th>;
          })}</tr></thead>
          <tbody>
            {!loading && !error && !data?.items?.length && <tr><td className="tc-empty" colSpan={visible.length || 1}>没有符合查询条件的交易记录，请调整筛选条件。</td></tr>}
            {data?.items?.map((item:any) => <tr key={item.record_id} tabIndex={0} aria-selected={selected === item.record_id} className={selected === item.record_id ? 'selected' : ''}
              onClick={() => openRow(item.record_id)} onKeyDown={event => rowKey(event,item.record_id)}>
              {visible.map(key => <td key={key} data-column={key} className={key === 'cost_change' ? `numeric tc-cost ${tradeTone(item.cost_diff)}` : tradeNumericKeys.has(key) ? 'numeric' : ''}
                title={key === 'current_price' ? tradeQuoteInfo(item) : key === 'fees' && item.fees == null ? '手续费尚未核实' : ['post_quantity','post_cost','cost_change'].includes(key) ? item.cost_note : tradeCell(item,key)}>
                {key === 'side' ? <span className={`tc-side ${tradeSideTone(item.side_code,item.kind_code)}`}>{item.side}</span> : tradeCell(item,key)}
              </td>)}
            </tr>)}
          </tbody>
        </table></div>
        <footer><span>共 {tradeCount(data?.total ?? 0)} 条 · 第 {data?.page ?? page} / {data?.total_pages ?? 1} 页</span><div>
          <select aria-label="每页条数" value={size} onChange={e => {setSize(Number(e.target.value)); setPage(1);}}>
            {[25,50,100,200,500,1000].map(n => <option key={n} value={n}>每页 {n} 条</option>)}
          </select>
          <button className="btn" disabled={loading || page <= 1} onClick={() => setPage(p => p-1)}>上一页</button>
          <button className="btn" disabled={loading || page >= (data?.total_pages || 1)} onClick={() => setPage(p => p+1)}>下一页</button>
        </div></footer>
      </div>
      <small className="tc-source">{data?.note}</small>
    </div>
    {drawerOpen && selected != null && <TradeDrawer recordId={selected} query={query} onSelect={open} onClose={close}/>}
  </section>;
}
