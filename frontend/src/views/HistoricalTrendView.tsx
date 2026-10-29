import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import { api } from "../api/client";

export function HistoricalTrendView({ personId, initialKind = "month", embedded = false, selectedChannel = "", selectedTicker = "", search = "" }: {
  personId: number | null; initialKind?: string; embedded?: boolean; selectedChannel?: string; selectedTicker?: string; search?: string;
}) {
  const [kind, setKind] = useState(initialKind);
  const [period, setPeriod] = useState(initialKind === "year" ? new Date().getFullYear().toString() : new Date().toLocaleDateString("sv-SE").slice(0, 7));
  const [mode, setMode] = useState("cumulative");
  const [channels, setChannels] = useState<string[]>([]);
  const [ticker, setTicker] = useState("");
  const [storedData, setData] = useState<any>(null);
  const [dataKey, setDataKey] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [maintenance, setMaintenance] = useState<any>(null);
  const generation = useRef<string | null>(null);
  const periodChosen = useRef(false);
  const marketDay = useRef("");
  const filterChannels = embedded ? (selectedChannel ? [selectedChannel] : []) : channels;
  const filterTicker = embedded ? selectedTicker : ticker;
  const requestKey = JSON.stringify([kind, period, mode, filterChannels, filterTicker, personId, search]);
  const data = dataKey === requestKey ? storedData : null;
  useEffect(() => {
    let active = true;
    const poll = () => api.derivedProgress().then((result: any) => {
      if (!active) return;
      setMaintenance(result);
      if (result.market_day) {
        marketDay.current = result.market_day;
        if (!periodChosen.current) setPeriod(initialKind === "year" ? result.market_day.slice(0, 4) : result.market_day.slice(0, 7));
      }
      if (result.ready && result.generation && result.generation !== generation.current) {
        generation.current = result.generation;
        setRefresh(value => value + 1);
      }
    }).catch(() => { if (active) setMaintenance({ progress: { state: "UNAVAILABLE", message: "计算进度暂时读取失败，正在重试。" } }); });
    poll();
    const timer = window.setInterval(poll, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [initialKind]);
  useEffect(() => {
    let active = true;
    const load = () => { setLoading(true); return api.historicalTrend({ kind, period, mode, channels: filterChannels, ticker: filterTicker, personId, search })
      .then((result: any) => { if (active) { setData(result); setDataKey(requestKey); setError(""); } })
      .catch((err: any) => { if (active) { setData(null); setError(err.message || "历史数据读取失败"); } })
      .finally(() => { if (active) setLoading(false); }); };
    load();
    const timer = window.setInterval(load, 60000);
    return () => { active = false; window.clearInterval(timer); };
  }, [kind, period, mode, JSON.stringify(filterChannels), filterTicker, personId, search, refresh]);
  const option = useMemo(() => {
    const points = data?.chart?.series?.[0]?.points || [];
    const valid = points.filter((p: any) => p[1] !== null).map((p: any) => [p[0], Number(p[1])]);
    const high = valid.reduce((a: any, b: any) => !a || b[1] > a[1] ? b : a, null);
    const low = valid.reduce((a: any, b: any) => !a || b[1] < a[1] ? b : a, null);
    return {
      graphic: !valid.length && !loading && !maintenance?.running ? [{ type: "text", left: "center", top: "middle", style: { text: `暂无有效历史曲线\n${data?.cache?.message || "请查看行情覆盖、累计基准和任务状态。"}`, fill: "#64748b", textAlign: "center", fontSize: 13 } }] : [],
      animation: false, title: { text: data?.chart?.title || "历史走势", textStyle: { fontSize: 14 } },
      grid: { left: 85, right: 55, top: 70, bottom: 85 },
      tooltip: { trigger: "axis", renderMode: "richText", formatter: (params: any[]) => {
        const item = params[0]; if (!item) return "—";
        const stamp = new Date(item.axisValue).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
        const value = item.value?.[1];
        return `${stamp} 北京时间\n${item.seriesName}：${value == null ? "—" : Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD`;
      } },
      xAxis: { type: "category", boundaryGap: false, data: points.map((p: any) => p[0]),
        axisLabel: { interval: 0, hideOverlap: true, formatter: (v: string) => data?.chart?.x_labels?.[v] || "" },
        axisTick: { show: false } },
      yAxis: { type: "value", scale: true }, dataZoom: [{ type: "inside" }, { type: "slider" }],
      series: [{ name: data?.chart?.series?.[0]?.name || "盈亏", type: "line", showSymbol: false, connectNulls: false,
        lineStyle: { width: data?.chart?.line_width || 1.5 },
        data: points.map((p: any) => [p[0], p[1] === null ? null : Number(p[1])]),
        markPoint: { data: [high && { name: "最高", coord: high, value: high[1].toFixed(2), itemStyle: { color: "#16a34a" } }, low && { name: "最低", coord: low, value: low[1].toFixed(2), itemStyle: { color: "#dc2626" } }].filter(Boolean) },
        markLine: { symbol: "none", silent: true, data: (data?.chart?.boundaries || []).map((b: any) => ({ name: b[1], xAxis: b[0], label: { formatter: b[1], fontSize: 9 }, lineStyle: { color: "#dbe3ef", type: "dotted" } })) } }],
    };
  }, [data, loading, maintenance?.running]);
  return <div style={{ overflow: "auto", display: "flex", flexDirection: "column", gap: 12 }}>
    <div className="card-panel" style={{ display: "flex", flexWrap: "wrap", gap: 12, padding: 12, alignItems: "center" }}>
      {!embedded && <select aria-label="月度或年度" value={kind} onChange={e => { const next = e.target.value; periodChosen.current = true; setKind(next); setPeriod(next === "year" ? period.slice(0, 4) : `${period.slice(0, 4)}-${(marketDay.current || new Date().toLocaleDateString("sv-SE")).slice(5, 7)}`); }}><option value="month">月度走势详情</option><option value="year">年度走势详情</option></select>}
      <input aria-label="走势期间" type={kind === "month" ? "month" : "number"} min={kind === "year" ? "2026" : "2026-01"} value={period} onChange={e => { periodChosen.current = true; setPeriod(e.target.value); }} />
      <select aria-label="走势模式" value={mode} onChange={e => setMode(e.target.value)}><option value="cumulative">累计盈亏走势</option><option value="intraday">每日日内盈亏</option></select>
      {!embedded && <><details><summary>{channels.length ? channels.join("、") : "全部渠道"}</summary><label><input type="checkbox" checked={!channels.length} onChange={() => setChannels([])} />全部渠道</label>{(data?.channels || []).map((ch: string) => <label key={ch} style={{ display: "block" }}><input type="checkbox" checked={channels.includes(ch)} onChange={() => setChannels(current => current.includes(ch) ? current.filter(v => v !== ch) : [...current, ch])} />{ch}</label>)}</details>
      <input aria-label="Ticker筛选" list="history-tickers" placeholder="全部股票" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} /><datalist id="history-tickers">{(data?.tickers || []).map((t: string) => <option key={t} value={t} />)}</datalist></>}
      <button onClick={() => setRefresh(v => v + 1)}>刷新</button>
    </div>
    {maintenance?.progress && <div role="status">
      {maintenance.progress.message}
      {maintenance.running && maintenance.progress.total_units > 0 && <>
        <progress max={maintenance.progress.total_units} value={maintenance.progress.completed_units || 0} />
        {maintenance.progress.completed_units || 0}/{maintenance.progress.total_units}（{maintenance.progress.percent}%）
      </>}
    </div>}
    {error ? <div role="alert" className="card-panel">{error}</div> : !data ? <div role="status"><progress aria-label="历史曲线加载中" /> 正在读取共享历史派生数据…</div> : <>
      <div role="status">{data.cache?.message}</div>
      {kind === "year" && <div>上半年已保存明细：{Object.entries(data.cache?.months || {}).filter(([month]) => month.slice(5, 7) <= "06").map(([month, info]: [string, any]) => `${month.slice(5)}月 ${info.saved_days}日／${info.complete_points}个完整点（待生成${info.not_generated_days}日，失效/失败${info.invalid_days}日）`).join("；") || "尚无已发布分区"}。缺失区段留空，不能代表整月无原始行情。</div>}
      <div className="card-panel"><ReactECharts option={option} notMerge
        showLoading={loading || (!!maintenance?.running && !data?.chart?.series?.some((s: any) => s.points?.some((p: any) => p[1] !== null)))}
        loadingOption={{ text: loading ? "正在读取历史曲线…" : `${maintenance?.progress?.message || "正在生成历史盈亏"}${maintenance?.progress?.total_units ? ` ${maintenance.progress.completed_units || 0}/${maintenance.progress.total_units}` : ""}` }}
        style={{ height: 520 }} /></div>
      <div>{data.chart?.note}</div>
      <div>最近有效采样：{(() => {
        const points = data.chart?.series?.flatMap((s: any) => s.points || []).filter((p: any) => p[1] !== null).sort((a: any, b: any) => a[0].localeCompare(b[0])) || [];
        return points.length ? new Date(points[points.length - 1][0]).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) + "（北京时间）" : "暂无可信累计采样";
      })()}</div>
      <div>来源：共享五分钟派生数据库；月度30分钟／年度2小时采样，缺失区段留空。</div>
    </>}
  </div>;
}
