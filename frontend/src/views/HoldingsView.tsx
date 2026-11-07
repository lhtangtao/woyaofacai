import React, { useEffect, useState, useRef } from "react";
import ReactECharts from "echarts-for-react";
import {
  Users,
  Building2,
  TrendingUp,
  TrendingDown,
  Layers,
  ArrowRight,
  Filter,
  Search,
  Download,
  Wallet,
  Activity,
  PieChart as PieIcon,
  Eye,
} from "lucide-react";
import { api } from "../api/client";
import { openHoldingColumns,closedHoldingColumns,holdingCell,holdingNumber,holdingTone,holdingQuantity } from "./holdings-presentation";
import "./holdings.css";
import { StockDrawer } from "../components/StockDrawer";

interface HoldingsViewProps {
  personId?: number | null;
  persons?: { id: number; name: string; enabled?: boolean }[];
  onPersonChange?: (id: number | null) => void;
  broker?: string;
  onBrokerChange?: (broker: string) => void;
  initialDimension?: "person" | "broker" | "stock";
  initialStatus?: string;
  initialMode?: "dashboard" | "detail";
  initialFilter?: {
    person_id?: number | null;
    broker?: string;
    ticker?: string;
  };
}

export const HoldingsView: React.FC<HoldingsViewProps> = ({
  personId,
  persons = [],
  onPersonChange,
  broker,
  onBrokerChange,
  initialDimension = "person",
  initialStatus = "持有中",
  initialMode = "dashboard",
  initialFilter = {},
}) => {
  // Mode: "dashboard" (仪表板总览) or "detail" (统一持仓明细页)
  const [viewMode, setViewMode] = useState<"dashboard" | "detail">(initialMode);
  const [dimension, setDimension] = useState<"person" | "broker" | "stock">(initialDimension);

  // Filters (仅支持持有中与已清仓两种状态，默认持有中)
  const [statusFilter, setStatusFilter] = useState<string>(initialStatus === "已清仓" ? "已清仓" : "持有中");
  const [selectedPersonId, setSelectedPersonId] = useState<number | null>(
    personId !== undefined ? personId : initialFilter.person_id ?? null
  );
  const [selectedBroker, setSelectedBroker] = useState<string>(
    broker !== undefined ? broker : initialFilter.broker || ""
  );
  const [selectedTicker, setSelectedTicker] = useState<string>(initialFilter.ticker || "");
  const [searchKeyword, setSearchKeyword] = useState<string>("");

  // Dashboard Data State
  const [dashboardData, setDashboardData] = useState<any>(null);
  const [dashLoading, setDashLoading] = useState(false);
  const [distTab, setDistTab] = useState<"by_person" | "by_broker" | "by_stock">("by_stock");

  // Detail Data State
  const [detailRows, setDetailRows] = useState<any[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailPage, setDetailPage] = useState(1);
  const [detailPageSize,setDetailPageSize] = useState(25);
  const [category,setCategory] = useState("");
  const [advanced,setAdvanced] = useState(false);
  const [hiddenColumns,setHiddenColumns] = useState<string[]>([]);
  const [selectedRow,setSelectedRow] = useState("");
  const [error,setError] = useState("");
  const dashRequest=useRef(0),detailRequest=useRef(0);
  const [sortField, setSortField] = useState<string>(initialStatus === "已清仓" ? "last_close_date" : "market_value");
  const [sortAsc, setSortAsc] = useState<boolean>(false);

  const toggleDetailSort = (field: string) => {
    if (sortField === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortField(field);
      setSortAsc(false);
    }
  };

  // Dashboard tables sorting state
  const [pSortField, setPSortField] = useState<string>("");
  const [pSortAsc, setPSortAsc] = useState<boolean>(true);
  const togglePSort = (field: string) => {
    if (pSortField === field) {
      setPSortAsc(!pSortAsc);
    } else {
      setPSortField(field);
      setPSortAsc(true);
    }
  };

  const [bSortField, setBSortField] = useState<string>("");
  const [bSortAsc, setBSortAsc] = useState<boolean>(true);
  const toggleBSort = (field: string) => {
    if (bSortField === field) {
      setBSortAsc(!bSortAsc);
    } else {
      setBSortField(field);
      setBSortAsc(true);
    }
  };

  const [sSortField, setSSortField] = useState<string>("");
  const [sSortAsc, setSSortAsc] = useState<boolean>(true);
  const toggleSSort = (field: string) => {
    if (sSortField === field) {
      setSSortAsc(!sSortAsc);
    } else {
      setSSortField(field);
      setSSortAsc(true);
    }
  };

  // Stock Drawer State (Right slide-out)
  const [drawerTicker, setDrawerTicker] = useState<string | null>(null);

  // Sync external personId changes
  useEffect(() => {
    if (personId !== undefined) {
      setSelectedPersonId(personId);
    }
  }, [personId]);

  // Sync external broker changes
  useEffect(() => {
    if (broker !== undefined) {
      setSelectedBroker(broker);
    }
  }, [broker]);

  // Load Dashboard Data
  const loadDashboard = () => {
    const request=++dashRequest.current;setDashLoading(true);setError("");
    api
      .holdingsDashboard(selectedPersonId,selectedBroker||undefined)
      .then((res) => {
        if(request!==dashRequest.current)return;
        if (res.status === "ok") {
          setDashboardData(res);
        }else throw new Error(res.message||"总览加载失败");
      })
      .catch((err) => {if(request===dashRequest.current)setError(err.message||"总览加载失败")})
      .finally(() => {if(request===dashRequest.current)setDashLoading(false)});
  };

  // Load Detail Rows
  const loadDetail = async () => {
    const request=++detailRequest.current;setDetailLoading(true);setError('');
    try{
      const params={dimension,person_id:selectedPersonId,broker:selectedBroker||undefined,ticker:selectedTicker||undefined,status:statusFilter,search:searchKeyword||undefined,page_size:200};
      const first=await api.holdingsDetail({...params,page:1});
      if(first.status!=='ok')throw new Error(first.message||'明细加载失败');
      const rows=[...(first.rows||[])];
      for(let page=2;rows.length<first.total;page++){
        if(request!==detailRequest.current)return;
        const next=await api.holdingsDetail({...params,page});
        if(next.status!=='ok'||!next.rows?.length)throw new Error(next.message||'未能读取全部匹配记录，请重试');
        rows.push(...next.rows);
      }
      if(request===detailRequest.current){setDetailRows(rows);setDetailPage(1)}
    }catch(err:any){if(request===detailRequest.current)setError(err.message||'持仓明细加载失败')}
    finally{if(request===detailRequest.current)setDetailLoading(false)}
  };
  useEffect(()=>{loadDashboard();return()=>{dashRequest.current++}},[selectedPersonId,selectedBroker]);
  useEffect(()=>{if(viewMode==='detail')loadDetail();return()=>{detailRequest.current++}},[viewMode,dimension,selectedPersonId,selectedBroker,selectedTicker,statusFilter,searchKeyword]);

  // Formatters
  const formatNumber = (val: any, decimals = 2) => {
    if (val == null || isNaN(Number(val))) return "—";
    return Number(val).toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  };

  const formatPercent = (val: any) => {
    if (val == null || isNaN(Number(val))) return "—";
    const num = Number(val) * 100;
    return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
  };

  const cards = dashboardData?.cards || {};
  const personSummary: any[] = dashboardData?.person_summary || [];
  const brokerSummary: any[] = dashboardData?.broker_summary || [];
  const stockSummary: any[] = dashboardData?.stock_summary || [];
  const distData: any[] = dashboardData?.distribution?.[distTab] || [];

  const drillToPerson = (pId: number | null) => {
    setSelectedPersonId(pId);
    if (onPersonChange) onPersonChange(pId);
    setDimension("person");
    setViewMode("detail");
  };

  const drillToBroker = (brokerName: string) => {
    setSelectedBroker(brokerName);
    if (onBrokerChange) onBrokerChange(brokerName);
    setDimension("broker");
    setDetailPage(1);
    setViewMode("detail");
  };

  const drillToStock = (tk: string) => {
    setSelectedTicker(tk);
    setDimension("stock");
    setDetailPage(1);
    setViewMode("detail");
  };

  // Sorted and paged detail rows
  const sortedDetailRows = detailRows.filter(r=>!category||r.category===category).sort((a, b) => {
    const valA = a[sortField];
    const valB = b[sortField];
    if (valA == null && valB == null) return 0;
    if (valA == null) return 1;
    if (valB == null) return -1;
    if (typeof valA === "number" && typeof valB === "number") {
      return sortAsc ? valA - valB : valB - valA;
    }
    return sortAsc ? String(valA).localeCompare(String(valB)) : String(valB).localeCompare(String(valA));
  });

  // Sorted Dashboard Summaries
  const sortedPersonSummary = [...personSummary].sort((a, b) => {
    if (!pSortField) return 0;
    const valA = a[pSortField];
    const valB = b[pSortField];
    if (valA == null && valB == null) return 0;
    if (valA == null) return 1;
    if (valB == null) return -1;
    if (typeof valA === "number" && typeof valB === "number") {
      return pSortAsc ? valA - valB : valB - valA;
    }
    return pSortAsc ? String(valA).localeCompare(String(valB)) : String(valB).localeCompare(String(valA));
  });

  const sortedBrokerSummary = [...brokerSummary].sort((a, b) => {
    if (!bSortField) return 0;
    const valA = a[bSortField];
    const valB = b[bSortField];
    if (valA == null && valB == null) return 0;
    if (valA == null) return 1;
    if (valB == null) return -1;
    if (typeof valA === "number" && typeof valB === "number") {
      return bSortAsc ? valA - valB : valB - valA;
    }
    return bSortAsc ? String(valA).localeCompare(String(valB)) : String(valB).localeCompare(String(valA));
  });

  const sortedStockSummary = [...stockSummary].sort((a, b) => {
    if (!sSortField) return 0;
    const valA = a[sSortField];
    const valB = b[sSortField];
    if (valA == null && valB == null) return 0;
    if (valA == null) return 1;
    if (valB == null) return -1;
    if (typeof valA === "number" && typeof valB === "number") {
      return sSortAsc ? valA - valB : valB - valA;
    }
    return sSortAsc ? String(valA).localeCompare(String(valB)) : String(valB).localeCompare(String(valA));
  });

  useEffect(()=>{setDetailPage(1)},[category,statusFilter,sortField,sortAsc]);
  const totalDetailCount = sortedDetailRows.length;
  const totalDetailPages = Math.max(1, Math.ceil(totalDetailCount / detailPageSize));
  const currentDetailRows = sortedDetailRows.slice((detailPage - 1) * detailPageSize, detailPage * detailPageSize);

  // Donut Ring Data with Top 5 + 其他 aggregation
  const sortedDist = [...distData].sort((a, b) => (Number(b.value) || 0) - (Number(a.value) || 0));
  const topSlices = sortedDist.slice(0, 5);
  const otherSlices = sortedDist.slice(5);
  const donutSlices = [...topSlices];
  if (otherSlices.length > 0) {
    const otherVal = otherSlices.reduce((acc, curr) => acc + (Number(curr.value) || 0), 0);
    if (otherVal > 0) {
      donutSlices.push({
        name: "其他",
        value: otherVal,
        percent: Number(((otherVal / (cards.total_market_value || 1)) * 100).toFixed(1)),
      });
    }
  }

  const donutColors = ["#2563EB", "#0D9488", "#D97706", "#8B5CF6", "#0284C7", "#94A3B8"];

  const donutOption = {
    color: donutColors,
    tooltip: { trigger: "item", formatter: "{b}: ${c} ({d}%)" },
    series: [
      {
        name: "资产分布",
        type: "pie",
        radius: ["58%", "78%"],
        center: ["42%", "50%"],
        avoidLabelOverlap: false,
        label: { show: false },
        data: donutSlices.map((d: any) => ({
          name: d.name,
          value: d.value,
        })),
      },
    ],
    graphic: [
      {
        type: "text",
        left: "41%",
        top: "43%",
        style: {
          text: `$${formatNumber(cards.total_market_value, 0)}`,
          textAlign: "center",
          fontSize: 15,
          fontWeight: "bold",
          fill: "#1E293B",
        },
      },
      {
        type: "text",
        left: "41%",
        top: "54%",
        style: {
          text: "USD",
          textAlign: "center",
          fontSize: 11,
          fill: "#64748B",
        },
      },
    ],
  };

  // Export detail rows to CSV
  const exportDetailCsv = () => {
    if (!sortedDetailRows.length) return;
    if (statusFilter === "已清仓") {
      const headers = [
        "归属人员",
        "券商渠道",
        "Ticker",
        "首次买入日期",
        "最后清仓日期",
        "清仓数量",
        "买入均价",
        "卖出均价",
        "清仓价",
        "当前价",
        "清仓后涨跌幅",
        "买入总额",
        "卖出总额",
        "已实现盈亏",
        "已实现收益率",
        "买入次数",
        "卖出次数",
      ];
      const lines = [headers.join(",")];
      for (const r of sortedDetailRows) {
        lines.push(
          [
            `"${r.person_name || ""}"`,
            `"${r.channel || ""}"`,
            `"${r.ticker || ""}"`,
            `"${r.first_buy_date || ""}"`,
            `"${r.last_close_date || ""}"`,
            r.closed_quantity ?? "",
            r.avg_buy_price ?? "",
            r.avg_sell_price ?? "",
            r.close_price ?? "",
            r.current_price ?? "",
            r.post_close_change != null ? `${(r.post_close_change * 100).toFixed(2)}%` : "",
            r.total_buy_amount ?? "",
            r.total_sell_amount ?? "",
            r.realized_pnl ?? "",
            r.realized_pnl_rate != null ? `${(r.realized_pnl_rate * 100).toFixed(2)}%` : "",
            r.buy_count ?? "",
            r.sell_count ?? "",
          ].join(",")
        );
      }
      const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `已清仓明细_${dimension}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      return;
    }

    const headers = [
      "归属人员",
      "券商渠道",
      "Ticker",
      "数量",
      "成本价",
      "成本总额",
      "当前最新价",
      "市值",
      "收益率",
      "累计盈亏",
      "买入次数",
      "卖出次数",
      "持仓占比",
    ];
    const lines = [headers.join(",")];
    for (const r of sortedDetailRows) {
      lines.push(
        [
          `"${r.person_name || ""}"`,
          `"${r.channel || ""}"`,
          `"${r.ticker || ""}"`,
          r.quantity ?? "",
          r.cost_price ?? "",
          r.cost_amount ?? "",
          r.current_price ?? "",
          r.market_value ?? "",
          r.pnl_rate != null ? `${(r.pnl_rate * 100).toFixed(2)}%` : "",
          r.total_pnl ?? "",
          r.buy_count ?? "",
          r.sell_count ?? "",
          r.weight != null ? `${(r.weight * 100).toFixed(2)}%` : "",
        ].join(",")
      );
    }
    const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `持仓明细_${dimension}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const enabledPersons = persons.filter((p) => p.enabled !== false);

  const columns=statusFilter==='已清仓'?closedHoldingColumns:openHoldingColumns;
  const categories=Array.from(new Set<string>(detailRows.map(r=>r.category).filter(Boolean)));
  const query=()=>{if(category||selectedTicker||searchKeyword)viewMode==='dashboard'?setViewMode('detail'):loadDetail();else viewMode==='dashboard'?loadDashboard():loadDetail()};
  const reset=()=>{setSelectedPersonId(null);onPersonChange?.(null);setSelectedBroker('');onBrokerChange?.('');setSelectedTicker('');setSearchKeyword('');setCategory('');setStatusFilter('持有中');setDetailPage(1)};
  const summaryTable=(title:string,rows:any[],nameKey:string,onRow:(r:any)=>void,sort:(key:string)=>void,sortKey:string,asc:boolean,person=false)=><section className="hp-card hp-summary"><header><h3>{title}</h3><span>点击下钻明细</span></header><div className="hp-summary-scroll"><table><thead><tr>{[[nameKey,person?'人员':nameKey==='channel'?'券商':'Ticker'],['market_value','市值'],['total_pnl','累计盈亏'],['pnl_rate','收益率'],...(person?[['open_stocks','股票数']]:[])].map(([k,label])=><th key={k} onClick={()=>sort(k)}>{label}{sortKey===k?(asc?' ↑':' ↓'):''}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr key={i} onClick={()=>onRow(r)}><td className={nameKey==='ticker'?'hp-ticker':''}>{r[nameKey]||'—'}</td><td>{holdingNumber(r.market_value)}</td><td className={holdingTone(r.total_pnl)}>{holdingCell(r,{key:'total_pnl',label:'',kind:'pnl'})}</td><td className={holdingTone(r.pnl_rate)}>{holdingCell(r,{key:'pnl_rate',label:'',kind:'rate'})}</td>{person&&<td>{r.open_stocks??'—'}</td>}</tr>)}</tbody></table>{!rows.length&&<p className="hp-empty">{dashLoading?'正在读取汇总…':'当前范围暂无汇总数据'}</p>}</div></section>;
  return <section className="hp-page">
    <div className="hp-toolbar"><div className="hp-segment"><button className={viewMode==='dashboard'?'active':''} onClick={()=>setViewMode('dashboard')}>总览</button><button className={viewMode==='detail'?'active':''} onClick={()=>setViewMode('detail')}>持仓明细</button></div><div><button className="hp-button" onClick={()=>{loadDashboard();if(viewMode==='detail')loadDetail()}}>↻ 刷新</button><button className="hp-button primary" disabled={!detailRows.length||viewMode!=='detail'} title={viewMode==='dashboard'?'进入持仓明细后导出当前筛选结果':''} onClick={exportDetailCsv}>↓ 导出明细</button></div></div>
    <div className="hp-metrics">
      <article className="hp-card hp-metric"><Wallet/><label>持仓总市值</label><strong>{dashLoading?'…':holdingNumber(cards.total_market_value)}</strong><small>浮动盈亏 <span className={holdingTone(cards.floating_pnl)}>{holdingCell(cards,{key:'floating_pnl',label:'',kind:'pnl'})}</span></small></article>
      <article className="hp-card hp-metric"><Layers/><label>持仓股票数量</label><strong>{dashLoading?'…':cards.open_stock_count??'—'}</strong><small>历史标的 {cards.total_stock_count??'—'} · 已清仓 {cards.closed_stock_count??'—'}</small></article>
      <article className="hp-card hp-metric"><TrendingUp/><label>累计盈亏（含已清仓）</label><strong className={holdingTone(cards.cumulative_pnl)}>{dashLoading?'…':holdingCell(cards,{key:'cumulative_pnl',label:'',kind:'pnl'})}</strong><small>收益率 <span className={holdingTone(cards.cumulative_pnl_rate)}>{holdingCell(cards,{key:'cumulative_pnl_rate',label:'',kind:'rate'})}</span></small></article>
      <article className="hp-card hp-metric"><Activity/><label>总投入 · 既定净投入口径</label><strong>{dashLoading?'…':holdingNumber(cards.total_invested)}</strong><small>已实现盈亏 <span className={holdingTone(cards.realized_pnl)}>{holdingCell(cards,{key:'realized_pnl',label:'',kind:'pnl'})}</span></small></article>
    </div>
    <div className="hp-card hp-filters">
      <div className="hp-filter-fields"><label>归属人员<select value={selectedPersonId??''} onChange={e=>{const id=e.target.value?Number(e.target.value):null;setSelectedPersonId(id);onPersonChange?.(id)}}><option value="">全部启用人员</option>{enabledPersons.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <label>券商渠道<select value={selectedBroker} onChange={e=>{setSelectedBroker(e.target.value);onBrokerChange?.(e.target.value)}}><option value="">全部渠道</option>{['长桥','BBAE','第一证券','嘉信'].map(b=><option key={b}>{b}</option>)}</select></label>
      <label>Ticker<input value={selectedTicker} placeholder="搜索股票代码" onChange={e=>setSelectedTicker(e.target.value.toUpperCase())} onKeyDown={e=>{if(e.key==='Enter')query()}}/></label>
      <label>持仓状态<select value={statusFilter} onChange={e=>{setStatusFilter(e.target.value);setViewMode('detail');setDetailPage(1)}}><option>持有中</option><option>已清仓</option></select></label>
      <label>股票分类<select value={category} onFocus={()=>{if(!detailRows.length&&viewMode==='dashboard')loadDetail()}} onChange={e=>{setCategory(e.target.value);setViewMode('detail');setDetailPage(1)}}><option value="">全部分类</option>{categories.map(c=><option key={c}>{c}</option>)}</select></label>
      <div className="hp-actions"><button className="hp-button primary" onClick={query}>查询</button><button className="hp-button" onClick={reset}>重置</button><button className="hp-button text" aria-expanded={advanced} onClick={()=>setAdvanced(!advanced)}>高级筛选 {advanced?'▴':'▾'}</button></div></div>
      {advanced&&<div className="hp-advanced"><label>组合搜索<input value={searchKeyword} placeholder="人员、券商或股票代码" onChange={e=>setSearchKeyword(e.target.value)}/></label><small>股票、状态和分类用于持仓明细；总览统计沿用当前人员及渠道范围，包含历史已清仓收益。</small></div>}
      <div className="hp-chips">{selectedPersonId!=null&&<button onClick={()=>{setSelectedPersonId(null);onPersonChange?.(null)}}>人员：{persons.find(p=>p.id===selectedPersonId)?.name||selectedPersonId} ×</button>}{selectedBroker&&<button onClick={()=>{setSelectedBroker('');onBrokerChange?.('')}}>渠道：{selectedBroker} ×</button>}{selectedTicker&&<button onClick={()=>setSelectedTicker('')}>股票：{selectedTicker} ×</button>}{category&&<button onClick={()=>setCategory('')}>分类：{category} ×</button>}<button onClick={()=>setStatusFilter('持有中')}>状态：{statusFilter} {statusFilter==='已清仓'?'×':''}</button>{searchKeyword&&<button onClick={()=>setSearchKeyword('')}>搜索：{searchKeyword} ×</button>}</div>
    </div>
    {error&&<div className="hp-error" role="alert">{error}<button className="hp-button" onClick={query}>重试</button></div>}
    {viewMode==='dashboard'?<>
      <div className="hp-summaries">
        {summaryTable('按人员汇总',sortedPersonSummary,'name',r=>drillToPerson(r.person_id),togglePSort,pSortField,pSortAsc,true)}
        {summaryTable('按券商汇总',sortedBrokerSummary,'channel',r=>drillToBroker(r.channel),toggleBSort,bSortField,bSortAsc)}
        {summaryTable('按股票汇总',sortedStockSummary,'ticker',r=>drillToStock(r.ticker),toggleSSort,sSortField,sSortAsc)}
      </div>
      <section className="hp-card hp-distribution"><header><div><h3>资产分布</h3><small>当前人员及渠道范围 · 持仓市值</small></div><div className="hp-segment">{[['by_person','按人员'],['by_broker','按券商'],['by_stock','按股票']].map(([key,label])=><button key={key} className={distTab===key?'active':''} onClick={()=>setDistTab(key as any)}>{label}</button>)}</div></header><div className="hp-distribution-body"><ReactECharts option={donutOption} style={{height:220,width:260,flexShrink:0}}/><div className="hp-legend">{donutSlices.map((d,i)=><div key={d.name}><span><i style={{background:donutColors[i%donutColors.length]}}/>{d.name}</span><strong>{holdingNumber(d.value)}</strong><span>{holdingNumber(d.percent,1)}%</span></div>)}{!donutSlices.length&&<p className="hp-empty">当前范围暂无有效资产分布</p>}</div></div></section>
    </>:<section className="hp-card hp-detail"><header><div><button className="hp-button text" onClick={()=>setViewMode('dashboard')}>← 返回总览</button><h3>{statusFilter==='已清仓'?'已清仓明细':'当前持仓明细'}</h3><small>{totalDetailCount} 条记录</small></div><div className="hp-actions"><div className="hp-segment">{[['person','按人员'],['broker','按券商'],['stock','按股票']].map(([k,label])=><button key={k} className={dimension===k?'active':''} onClick={()=>setDimension(k as any)}>{label}</button>)}</div><details className="hp-columns"><summary>☷ 显示列</summary><div>{columns.map(c=><label key={c.key}><input type="checkbox" checked={!hiddenColumns.includes(c.key)} onChange={()=>setHiddenColumns(h=>h.includes(c.key)?h.filter(k=>k!==c.key):[...h,c.key])}/>{c.label}</label>)}</div></details></div></header>
      <div className="hp-table-scroll"><table><thead><tr>{columns.filter(c=>!hiddenColumns.includes(c.key)).map(c=><th className={c.kind?'numeric':''} key={c.key} onClick={()=>toggleDetailSort(c.key)}>{c.label}{sortField===c.key?(sortAsc?' ↑':' ↓'):''}</th>)}</tr></thead><tbody>
      {detailLoading?<tr><td className="hp-empty" colSpan={columns.length}>正在读取当前范围的完整持仓明细…</td></tr>:currentDetailRows.map((r,i)=>{const key=`${r.person_id}-${r.account_id}-${r.ticker}`;return <tr key={key+'-'+i} className={selectedRow===key?'selected':''} onClick={()=>{setSelectedRow(key);setDrawerTicker(r.ticker)}}>{columns.filter(c=>!hiddenColumns.includes(c.key)).map(c=><td key={c.key} className={`${c.kind?'numeric':''} ${c.key==='ticker'?'hp-ticker':''} ${c.kind==='rate'||c.kind==='pnl'?holdingTone(r[c.key]):''}`} title={c.key==='close_price'&&r.is_split_adjusted?`原始清仓价 ${holdingNumber(r.orig_close_price)}；${r.split_notes||''}`:undefined}>{holdingCell(r,c)}</td>)}</tr>})}
      {!detailLoading&&!currentDetailRows.length&&<tr><td className="hp-empty" colSpan={columns.length}>没有符合当前筛选的持仓记录</td></tr>}</tbody></table></div>
      <footer><span>共 {totalDetailCount} 条 · 第 {detailPage} / {totalDetailPages} 页</span><div><select value={detailPageSize} onChange={e=>{setDetailPageSize(Number(e.target.value));setDetailPage(1)}}>{[25,50,100,200].map(n=><option key={n} value={n}>每页 {n} 条</option>)}</select><button className="hp-button" disabled={detailPage<=1} onClick={()=>setDetailPage(p=>p-1)}>上一页</button><button className="hp-button" disabled={detailPage>=totalDetailPages} onClick={()=>setDetailPage(p=>p+1)}>下一页</button></div></footer>
    </section>}
    <StockDrawer className="hp-stock-drawer" ticker={drawerTicker} personId={selectedPersonId} broker={selectedBroker} onClose={()=>setDrawerTicker(null)}/>
  </section>;
};
