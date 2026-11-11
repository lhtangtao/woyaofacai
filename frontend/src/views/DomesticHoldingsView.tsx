import { useEffect, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import "./domestic-holdings.css";

type Row = Record<string, string | null>;
type Data = { rows: Row[]; transactions: Row[]; history: Row[]; raw_path: string; calc_path: string };
const holdings = [["fund_name","基金名称"],["fund_code","基金代码"],["platform","交易平台"],["fund_type","基金类型"],["shares","持有份额"],["nav","最新净值"],["nav_date","净值日期"],["market_value","持仓市值（CNY）"],["pnl","累计盈亏"],["rate","收益率"],["weight","持仓占比"],["status","状态"]];
const trades = [["confirmation_date","确认日期"],["platform","交易平台"],["kind","交易类型"],["confirmed_shares","确认份额"],["confirmed_amount","确认金额"],["fee","手续费"],["confirmed_nav","确认净值"],["confirmation_status","确认状态"],["parse_error","备注"]];
const money = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const numeric = new Set(["pnl","rate","shares","nav","market_value","confirmed_shares","confirmed_amount","fee","confirmed_nav"]);
const value = (v: string | null | undefined) => v == null ? "—" : money.format(Number(v));
const sorted = (rows: Row[], key: string, asc: boolean) => [...rows].sort((a,b) => {
  const x = a[key], y = b[key];
  if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1;
  return (asc ? 1 : -1) * (numeric.has(key) ? Number(x) - Number(y) : x.localeCompare(y, "zh-CN"));
});
const options = (rows: Row[], key: string) => [...new Set(rows.map(r => r[key]).filter((v): v is string => !!v))].sort();

export function DomesticHoldingsView({ refreshKey }: { refreshKey: number }) {
  const [base,setBase] = useState<Data>();
  const [detail,setDetail] = useState<Data>();
  const [selected,setSelected] = useState<Row>();
  const [platform,setPlatform] = useState("");
  const [kind,setKind] = useState("");
  const [status,setStatus] = useState("持有中");
  const [search,setSearch] = useState("");
  const [sort,setSort] = useState(["market_value","desc"]);
  const [tradeSort,setTradeSort] = useState(["confirmation_date","desc"]);
  const [page,setPage] = useState(1);
  const [tradePage,setTradePage] = useState(1);
  const [size,setSize] = useState(20);
  const [tradeSize,setTradeSize] = useState(20);
  const [tradeKind,setTradeKind] = useState("");
  const [start,setStart] = useState("");
  const [end,setEnd] = useState("");
  const [trend,setTrend] = useState("12");
  const [trendStart,setTrendStart] = useState("");
  const [trendEnd,setTrendEnd] = useState("");
  const [group,setGroup] = useState("fund_name");
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(true);
  const scroll = useRef(0);
  const pane = useRef<HTMLDivElement>(null);
  const filter = (setter: (v:string) => void, v:string) => { setter(v); setPage(1); };

  useEffect(() => {
    const abort = new AbortController();
    const query = selected ? "?" + new URLSearchParams({ account: selected.account_id!, fund: selected.fund_code! }) : "";
    setError("");
    setBusy(true);
    fetch("/api/v1/domestic/holdings" + query, { signal: abort.signal }).then(async response => {
      if (!response.ok) throw Error("无法读取国内资产持仓");
      const data: Data = await response.json();
      if (!abort.signal.aborted) (selected ? setDetail : setBase)(data);
    }).catch(() => { if (!abort.signal.aborted) setError("无法读取国内资产持仓，请刷新重试。"); })
      .finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => abort.abort();
  }, [selected, refreshKey]);

  function open(row: Row) {
    scroll.current = pane.current?.scrollTop ?? 0;
    setDetail(undefined); setSelected(row); setTradeKind(""); setStart(""); setEnd(""); setTradePage(1);
    setTradeSort(["confirmation_date","desc"]); setTrend("12"); setTrendStart(""); setTrendEnd("");
    if (pane.current) pane.current.scrollTop = 0;
  }

  const all = base?.rows ?? [];
  const scope = all.filter(r => (!platform || r.platform === platform) && (!kind || r.fund_type === kind) &&
    (!search || (r.fund_name + " " + r.fund_code).toLowerCase().includes(search.trim().toLowerCase())));
  const pending = (r:Row) => r.status !== "已清仓" && r.market_value == null;
  const rows = scope.filter(r => !status || (status === "待估值" ? pending(r) : r.status === status));
  const priced = scope.filter(r => Number(r.market_value) > 0);
  const returns = scope.filter(r=>r.pnl != null);
  const total = priced.reduce((sum,r) => sum + Number(r.market_value),0);
  const missing = scope.filter(pending).length;
  const ordered = sort[0] === "market_value" || sort[0] === "weight"
    ? [...rows].sort((a,b) => a.market_value == null ? (b.market_value == null ? 0 : 1) :
      b.market_value == null ? -1 : (Number(a.market_value) - Number(b.market_value)) * (sort[1] === "asc" ? 1 : -1))
    : sorted(rows,sort[0],sort[1] === "asc");
  const account = detail?.rows[0] ?? selected;
  const tx = sorted((detail?.transactions ?? []).map(r => ({...r, platform: account?.platform ?? null}) as Row).filter(r => (!tradeKind || r.kind === tradeKind) &&
    ((!start && !end) || (!!r.confirmation_date && (!start || r.confirmation_date >= start) &&
      (!end || r.confirmation_date <= end)))),tradeSort[0],tradeSort[1] === "asc");
  const currentTradePage = Math.min(tradePage, Math.max(1, Math.ceil(tx.length/tradeSize)));
  const history = detail?.history ?? [];
  const points = trend === "custom" ? history.filter(r => (!trendStart || r.month! >= trendStart) &&
    (!trendEnd || r.month! <= trendEnd)) : trend ? history.slice(-Number(trend)) : history;

  const select = (label:string, current:string, choices:string[], change:(v:string)=>void) => <label>{label}
    <select value={current} onChange={e => change(e.target.value)}><option value="">全部</option>
      {choices.map(v => <option key={v}>{v}</option>)}</select></label>;
  const card = (label:string, number:string, hint?:string) => <section className="dh-card"><span>{label}</span>
    <strong>{number}</strong>{hint && <small>{hint}</small>}</section>;
  const paging = (n:number, current:number, per:number, change:(v:number)=>void, changeSize:(v:number)=>void) => {
    const max = Math.max(1,Math.ceil(n/per));
    const active = Math.min(max,current);
    return <div className="dh-paging">共 {n} 条 · 第 {active} / {max} 页
      <select aria-label="每页条数" value={per} onChange={e => { changeSize(Number(e.target.value)); change(1); }}>
        {[10,20,50,100].map(v => <option key={v} value={v}>{v} 条/页</option>)}</select>
      <button disabled={active === 1} onClick={() => change(active-1)}>上一页</button>
      <button disabled={active === max} onClick={() => change(active+1)}>下一页</button></div>;
  };
  function table(items: Row[], columns: string[][], order: string[], onSort: (v:string[])=>void, isHolding = false) {
    return <div className="dh-table"><table><thead><tr>{columns.map(([key,label]) => <th key={key}>
      <button onClick={() => onSort([key,order[0] === key && order[1] === "desc" ? "asc" : "desc"])}>{label}
        {order[0] === key ? order[1] === "asc" ? " ↑" : " ↓" : ""}</button></th>)}{isHolding && <th />}</tr></thead>
      <tbody>{items.map(r => <tr key={r.id ?? r.account_id + ":" + r.fund_code} onClick={isHolding ? () => open(r) : undefined}>
        {columns.map(([key]) => <td key={key} title={(["pnl","rate"].includes(key) ? r.return_issue : r.issue) ?? undefined} className={/shares|nav$|amount|fee|value|weight|pnl|rate/.test(key) ? "dh-number" : ""}>
          {["market_value","confirmed_amount","fee","pnl"].includes(key) ? value(r[key]) :
            key === "rate" && r.rate != null ? Number(r.rate).toFixed(2)+"%" :
            key === "weight" && total && r.market_value != null ? (100*Number(r.market_value)/total).toFixed(2)+"%" : r[key] ?? "—"}</td>)}
        {isHolding && <td><button aria-label={`查看 ${r.fund_name}`} onClick={e => { e.stopPropagation(); open(r); }}>›</button></td>}</tr>)}</tbody>
      {isHolding && <tfoot><tr><td colSpan={7}>已估值合计</td><td className="dh-number">{rows.some(r=>r.market_value != null) ? money.format(rows.reduce((sum,r)=>sum+Number(r.market_value ?? 0),0)) : "—"}</td><td colSpan={5} /></tr></tfoot>}</table>
      {!items.length && <p>当前筛选没有记录。</p>}</div>;
  }
  const groups = (key:string) => options(scope,key).map(name => {
    const matches = scope.filter(r => r[key] === name);
    const valued = matches.filter(r => r.market_value != null);
    return { name, count: new Set(matches.filter(r=>r.status==="持有中").map(r=>r.fund_code)).size, valued: valued.length, pending: matches.filter(pending).length, value: valued.length ? valued.reduce((n,r)=>n+Number(r.market_value),0) : null };
  });
  const summary = (key:string,title:string,setter:(v:string)=>void) => <section className="dh-card"><h3>{title}</h3>
    {groups(key).map(g => <button className="dh-summary" key={g.name} onClick={() => filter(setter,g.name)}>
      <span>{g.name} · 持有 {g.count}<br/><small>已估值 {g.valued} · 待估值 {g.pending} · 盈亏待计算</small></span><b>{g.value == null ? "未估值" : money.format(g.value)}</b>
      <small>{total && g.value != null ? (100*g.value/total).toFixed(2)+"%" : "—"}</small></button>)}</section>;
  const chartRows = groups(group).filter(g => g.value != null && g.value > 0);

  return <div className="dh-workspace" ref={pane}>
    {error && <p role="alert">{error}</p>}{busy && <p role="status">正在读取国内资产…</p>}
    <div hidden={!!selected}>
      <div className="dh-cards">
        {card(missing ? "已估值部分（CNY）" : "持仓总市值（CNY）",priced.length ? money.format(total) : "—")}
        {card("已确认持有基金数量",String(new Set(scope.filter(r=>r.status==="持有中").map(r=>r.fund_code)).size))}
        {card(returns.length === scope.length ? "累计盈亏（含已清仓）" : "累计盈亏（已核算部分）",returns.length ? money.format(returns.reduce((sum,r)=>sum+Number(r.pnl),0)) : "待计算")}<section className="dh-card"><span>待估值记录</span><strong>{missing}</strong><button onClick={()=>filter(setStatus,"待估值")}>查看待估值</button></section>
      </div>
      <div className="dh-card dh-filters">
        {select("交易平台",platform,options(all,"platform"),v=>filter(setPlatform,v))}
        {select("基金类型",kind,options(all,"fund_type"),v=>filter(setKind,v))}
        {select("持仓状态",status,["持有中","已清仓","待计算","待估值"],v=>filter(setStatus,v))}
        <label>基金搜索<input value={search} placeholder="基金名称或代码" onChange={e=>filter(setSearch,e.target.value)} /></label>
      </div>
      <section className="dh-card"><h3>基金持仓明细</h3>
        {table(ordered.slice((Math.min(page,Math.max(1,Math.ceil(rows.length/size)))-1)*size, Math.min(page,Math.max(1,Math.ceil(rows.length/size)))*size),holdings,sort,v=>{setSort(v);setPage(1);},true)}
        {paging(rows.length,page,size,setPage,setSize)}</section>
      <div className="dh-bottom">{summary("platform","平台持仓汇总",setPlatform)}{summary("fund_type","基金类型汇总",setKind)}
        <section className="dh-card"><h3>资产分布</h3><select aria-label="分布维度" value={group} onChange={e=>setGroup(e.target.value)}>
          <option value="fund_name">按基金</option><option value="platform">按平台</option><option value="fund_type">按基金类型</option></select>
          {chartRows.length ? <ReactECharts style={{ height: 220 }} option={{tooltip:{trigger:"item"},series:[{type:"pie",radius:["46%","70%"],data:chartRows} ]}}
            onEvents={{ click: ({name}:{name:string})=>filter(group==="platform"?setPlatform:group==="fund_type"?setKind:setSearch,name) }} /> : <p>暂无已估值资产可绘制。</p>}
          {chartRows.map(g=><button className="dh-summary" key={g.name} onClick={()=>filter(group==="platform"?setPlatform:group==="fund_type"?setKind:setSearch,g.name)}>
            <span>{g.name}</span><b>{money.format(g.value!)}</b><small>{(100*g.value!/total).toFixed(2)}%</small></button>)}
          {missing>0 && <small>{missing} 条未估值记录未计入分布</small>}</section>
      </div>
    </div>
    {account && <div>
      <div className="dh-paging"><button onClick={()=>{setSelected(undefined);requestAnimationFrame(()=>{if(pane.current)pane.current.scrollTop=scroll.current;});}}>← 返回持仓管理</button>
        <span>国内资产 / 持仓管理 / {account.fund_name}</span></div>
      <h2>持仓明细 · {account.fund_name}</h2>
      <p>{account.platform} · {account.account_name} · {account.fund_code} · 数据日期 {account.as_of ?? "—"} · {account.status}</p>
      <div className="dh-cards">{card("当前持有份额",account.shares ?? "—")}{card("最新单位净值",account.nav ?? "—",account.nav_date ?? "净值缺失")}
        {card("当前持仓市值（CNY）",value(account.market_value))}{card("累计盈亏",value(account.pnl))}{card("收益率",account.rate == null ? "—" : Number(account.rate).toFixed(2)+"%")}</div>
      <div className="dh-detail">
        <section className="dh-card"><h3>交易明细 · {account.platform}</h3><div className="dh-filters">
          <label>确认起日<input type="date" value={start} onChange={e=>{setStart(e.target.value);setTradePage(1);}} /></label>
          <label>确认止日<input type="date" value={end} onChange={e=>{setEnd(e.target.value);setTradePage(1);}} /></label>
          {select("交易类型",tradeKind,options(detail?.transactions ?? [],"kind"),v=>{setTradeKind(v);setTradePage(1);})}</div>
          {table(tx.slice((currentTradePage-1)*tradeSize,currentTradePage*tradeSize),trades,tradeSort,v=>{setTradeSort(v);setTradePage(1);})}
          {paging(tx.length,tradePage,tradeSize,setTradePage,setTradeSize)}</section>
        <section className="dh-card"><h3>持仓概览</h3><p>平台 {account.platform}</p><p>账户 {account.account_name}</p><p>基金代码 {account.fund_code}</p>
          <p>当前份额 {account.shares ?? "—"}</p><p>市值 {value(account.market_value)}</p><p>累计买入份额 {account.buy_shares ?? "—"}</p><p>累计卖出份额 {account.sell_shares ?? "—"}</p><p>累计分红 {value(account.cash_dividend)}</p><p>剩余持仓成本 {value(account.cost)}</p><p>已实现盈亏 {value(account.realized_pnl)}</p><p>{account.return_issue ?? "收益率 = 累计盈亏 ÷ 累计买入金额（非年化）"}</p>{account.issue && <p>{account.issue}</p>}{account.last_confirmed_date && <p>最近可核对份额 {account.last_confirmed_shares} · {account.last_confirmed_date}</p>}<p>累计净值 {account.accumulated_nav ?? "—"} · 来源 {account.nav_source ?? "—"}</p></section>
      </div>
      <section className="dh-card"><h3>份额 / 市值变化</h3><select aria-label="趋势范围" value={trend} onChange={e=>setTrend(e.target.value)}>
        <option value="6">近6个月</option><option value="12">近1年</option><option value="">全部</option><option value="custom">自定义</option></select>
        {trend==="custom" && <><input aria-label="趋势起月" type="month" value={trendStart} onChange={e=>setTrendStart(e.target.value)} />
          <input aria-label="趋势止月" type="month" value={trendEnd} onChange={e=>setTrendEnd(e.target.value)} /></>}
        {points.some(p=>p.holding_shares!=null || p.market_value!=null) ? <ReactECharts option={{
          tooltip:{trigger:"axis"},legend:{},xAxis:{type:"category",data:points.map(p=>p.month)},
          yAxis:[{type:"value",name:"份额"},{type:"value",name:"市值（CNY）"}],
          series:[{name:"份额",type:"line",data:points.map(p=>p.holding_shares)},
            {name:"市值",type:"line",yAxisIndex:1,data:points.map(p=>p.market_value)}]}} /> : <p>当前范围没有可用的历史持仓快照。</p>}
        {points.length>0 && !points.some(p=>p.market_value!=null) && <small>缺少历史净值，市值曲线暂无数据。</small>}</section>
    </div>}
    <small>只读数据源 {base?.raw_path ?? "—"} · {base?.calc_path ?? "—"}</small>
  </div>;
}
