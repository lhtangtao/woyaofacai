import React, { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { api } from "../api/client";

export function HistoricalTrendView({ personId }: { personId: number | null }) {
  const [kind, setKind] = useState("month");
  const [period, setPeriod] = useState(new Date().toLocaleDateString("sv-SE").slice(0, 7));
  const [mode, setMode] = useState("cumulative");
  const [channels, setChannels] = useState<string[]>([]);
  const [ticker, setTicker] = useState("");
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    const load = () => api.historicalTrend({ kind, period, mode, channels, ticker, personId })
      .then((result: any) => { if (active) { setData(result); setError(""); } })
      .catch((err: any) => { if (active) { setData(null); setError(err.message || "历史数据读取失败"); } });
    setData(null); load();
    const timer = window.setInterval(load, 60000);
    return () => { active = false; window.clearInterval(timer); };
  }, [kind, period, mode, channels, ticker, personId, refresh]);
  const option = useMemo(() => {
    const points = data?.chart?.series?.[0]?.points || [];
    const valid = points.filter((p: any) => p[1] !== null).map((p: any) => [p[0], Number(p[1])]);
    const high = valid.reduce((a: any, b: any) => !a || b[1] > a[1] ? b : a, null);
    const low = valid.reduce((a: any, b: any) => !a || b[1] < a[1] ? b : a, null);
    return {
      animation: false, title: { text: data?.chart?.title || "历史走势", textStyle: { fontSize: 14 } },
      grid: { left: 85, right: 55, top: 70, bottom: 85 },
      tooltip: { trigger: "axis", valueFormatter: (v: any) => v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
      xAxis: { type: "time", min: points[0]?.[0], max: points[points.length - 1]?.[0], axisLabel: { formatter: (v: string) => new Date(v).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) } },
      yAxis: { type: "value", scale: true }, dataZoom: [{ type: "inside" }, { type: "slider" }],
      series: [{ name: data?.chart?.series?.[0]?.name || "盈亏", type: "line", showSymbol: false, connectNulls: false,
        data: points.map((p: any) => [p[0], p[1] === null ? null : Number(p[1])]),
        markPoint: { data: [high && { name: "最高", coord: high, value: high[1].toFixed(2), itemStyle: { color: "#16a34a" } }, low && { name: "最低", coord: low, value: low[1].toFixed(2), itemStyle: { color: "#dc2626" } }].filter(Boolean) },
        markLine: { symbol: "none", silent: true, data: (data?.chart?.boundaries || []).map((b: any) => ({ name: b[1], xAxis: b[0], label: { formatter: b[1], fontSize: 9 }, lineStyle: { color: "#dbe3ef", type: "dotted" } })) } }],
    };
  }, [data]);
  return <div style={{ overflow: "auto", display: "flex", flexDirection: "column", gap: 12 }}>
    <div className="card-panel" style={{ display: "flex", flexWrap: "wrap", gap: 12, padding: 12, alignItems: "center" }}>
      <select aria-label="月度或年度" value={kind} onChange={e => { const next = e.target.value; setKind(next); setPeriod(next === "year" ? period.slice(0, 4) : `${period.slice(0, 4)}-${new Date().toLocaleDateString("sv-SE").slice(5, 7)}`); }}><option value="month">月度走势详情</option><option value="year">年度走势详情</option></select>
      <input aria-label="走势期间" type={kind === "month" ? "month" : "number"} min={kind === "year" ? "2026" : "2026-01"} value={period} onChange={e => setPeriod(e.target.value)} />
      <select aria-label="走势模式" value={mode} onChange={e => setMode(e.target.value)}><option value="cumulative">累计盈亏走势</option><option value="intraday">每日日内盈亏</option></select>
      <details><summary>{channels.length ? channels.join("、") : "全部渠道"}</summary><label><input type="checkbox" checked={!channels.length} onChange={() => setChannels([])} />全部渠道</label>{(data?.channels || []).map((ch: string) => <label key={ch} style={{ display: "block" }}><input type="checkbox" checked={channels.includes(ch)} onChange={() => setChannels(current => current.includes(ch) ? current.filter(v => v !== ch) : [...current, ch])} />{ch}</label>)}</details>
      <input aria-label="Ticker筛选" list="history-tickers" placeholder="全部股票" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} /><datalist id="history-tickers">{(data?.tickers || []).map((t: string) => <option key={t} value={t} />)}</datalist>
      <button onClick={() => setRefresh(v => v + 1)}>刷新</button>
    </div>
    {error ? <div role="alert" className="card-panel">{error}</div> : !data ? <div>正在读取共享历史派生数据…</div> : <>
      <div role="status">{data.cache?.message}</div>
      <div className="card-panel"><ReactECharts option={option} notMerge style={{ height: 520 }} /></div>
      <div>{data.chart?.note}</div>
    </>}
  </div>;
}
