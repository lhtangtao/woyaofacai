import React, { useEffect, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import { api, TodayFilters, TodaySnapshot, todayQuery } from "../api/client";
import "./today-pnl.css";
import { HistoricalTrendView } from "./HistoricalTrendView";

const beijingDate = () => new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
}).format(new Date());
const clock = (stamp: string) => new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false,
}).format(new Date(stamp));
const money = (value: string | number | null | undefined, percent = false) => value == null ? "—" :
  Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2,
    ...(percent ? { style: "percent" as const } : {}) });
const color = (v: string | number | null) => v == null ? "var(--text-muted)" :
  Number(v) > 0 ? "var(--profit-color)" : Number(v) < 0 ? "var(--loss-color)" : "var(--text-main)";
const signed = (value: string | number | null | undefined, percent = false) => value == null ? "—" :
  `${Number(value) < 0 ? "−" : Number(value) > 0 ? "+" : ""}${percent ? "" : "$"}${money(Math.abs(Number(value)), percent)}`;
const signStyle = (value: string | number | null | undefined) => value == null ? "unknown" :
  Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "neutral";
// Native ReportPage's visual column order; raw engine columns stay unchanged.
const order = [0, 1, 2, 9, 3, 4, 12, 11, 13, 5, 6, 10, 7, 8];

export const TodayPnlView: React.FC<{ personId: number | null }> = ({ personId }) => {
  const [curveKind, setCurveKind] = useState("day");
  const [day, setDay] = useState(beijingDate);
  const [ticker, setTicker] = useState("");
  const [channel, setChannel] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [live, setLive] = useState(true);
  const [showQuoteColumns, setShowQuoteColumns] = useState(false);
  const [sort, setSort] = useState({ col: "今日盈亏($)", dir: "desc" });
  const [data, setData] = useState<TodaySnapshot | null>(null);
  const [dataKey, setDataKey] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [savedCurve, setSavedCurve] = useState<TodaySnapshot["curve"] | null>(null);
  const [savedCurveKey, setSavedCurveKey] = useState("");
  const [curveError, setCurveError] = useState("");
  const generation = useRef(0);
  const filters: TodayFilters = { person_id: personId, as_of: day, ticker, channel, search,
    live: live ? "1" : "0", sort_col: sort.col, sort_dir: sort.dir };
  const key = todayQuery(filters);
  const curveKey = JSON.stringify([personId, day, channel, ticker, search]);
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      try {
        const result = await api.savedTodayCurve(filters, controller.signal);
        if (!controller.signal.aborted) { setSavedCurve(result.curve); setSavedCurveKey(curveKey); setCurveError(""); }
      } catch (err: any) {
        if (!controller.signal.aborted) setCurveError(err.message || "五分钟曲线读取失败");
      } finally { busy = false; }
    };
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [curveKey, refresh]);

  useEffect(() => {
    const controller = new AbortController();
    const version = ++generation.current;
    let busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      setLoading(true);
      try {
        const result = await api.holdingsToday(filters, controller.signal);
        if (controller.signal.aborted || generation.current !== version) return;
        if (result.status !== "ok") throw new Error("今日盈亏读取失败，请重试");
        setData(result); setDataKey(key); setError("");
      } catch (err: any) {
        if (!controller.signal.aborted && generation.current === version) setError(err.message || "今日盈亏请求失败");
      } finally {
        busy = false;
        if (!controller.signal.aborted && generation.current === version) setLoading(false);
      }
    };
    void load();
    // Historical backfill also needs polling; financial scope changes abort old requests.
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 10000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [key, refresh]);

  // Never flash the previous person's data while a new scope is loading.
  const snapshot = dataKey === key ? data : null;
  const curve = savedCurveKey === curveKey ? savedCurve : snapshot?.curve;
  const visibleColumns = showQuoteColumns ? order : order.filter(i => i !== 7 && i !== 8);
  const selectChannel = (name: string) => setChannel(current => current === name ? "" : name);
  const channelEvents = { click: (p: any) => {
    if (p.componentType === "series" || p.componentType === "xAxis" || p.componentType === "yAxis") selectChannel(String(p.name || p.value));
  } };
  const channelStyle = (name: string, base: string) => ({
    color: base, opacity: !channel || channel === name ? 1 : 0.3,
    borderColor: channel === name ? "#2563EB" : base,
    borderWidth: channel === name ? 3 : 0,
  });
  const summary = snapshot?.summary;
  const presets = [
    ["盈利最多", "今日盈亏($)", "desc"], ["亏损最多", "今日盈亏($)", "asc"],
    ["涨幅最大", "今日涨跌幅", "desc"], ["跌幅最大", "今日涨跌幅", "asc"], ["占比最高", "占比", "desc"],
  ];
  const chartOptions = curve && {
    animation: false,
    aria: { enabled: true, description: "当日盈亏走势，北京时间，从夜盘至盘后，缺失点断线。" },
    grid: { left: 80, right: 35, top: 55, bottom: 60 },
    tooltip: { trigger: "axis", valueFormatter: (v: any) => money(v) + " USD" },
    xAxis: { type: "category", boundaryGap: false, data: curve.points.map(p => p.time),
      axisLabel: { formatter: clock, interval: curve.interval_minutes === 5 ? 11 : 59 } },
    yAxis: { type: "value", scale: true, splitLine: { lineStyle: { color: "#E5EAF1" } },
      axisLine: { onZero: true }, axisLabel: { formatter: (v: number) => money(v) } },
    series: [{ name: "当时的今日累计盈亏（USD）", type: "line", showSymbol: false, connectNulls: false,
      data: curve.points.map(p => p.pnl == null ? null : Number(p.pnl)),
      lineStyle: { width: 2, color: "#2563EB" },
      markLine: { symbol: "none", silent: true, data: [
        { yAxis: 0, lineStyle: { color: "#334155", width: 2, type: "solid" }, label: { formatter: "0" } },
        ...curve.boundaries.map(b => ({ xAxis: b.time, label: { formatter: `${b.label} ${clock(b.time)}`, rotate: 0, position: "insideEndTop" },
          lineStyle: { color: "#94A3B8", type: "dashed" } })),
      ] },
      markPoint: { symbol: "circle", symbolSize: 7, data: [
        ...(curve.high ? [{ name: "最高", coord: [curve.high.time, Number(curve.high.pnl)], value: Number(curve.high.pnl),
          itemStyle: { color: "#16A34A" }, label: { color: "#15803D", backgroundColor: "#ECFDF3", borderColor: "#16A34A", position: "top" } }] : []),
        ...(curve.low ? [{ name: "最低", coord: [curve.low.time, Number(curve.low.pnl)], value: Number(curve.low.pnl),
          itemStyle: { color: "#DC2626" }, label: { color: "#DC2626", backgroundColor: "#FEF2F2", borderColor: "#DC2626", position: "right" } }] : []),
      ], label: { show: true, formatter: (p: any) => `${p.name} ${money(p.value)}`, fontSize: 11,
        borderWidth: 1, borderRadius: 3, padding: 4 } },
    }],
  };

  return <div className="today-pnl" data-testid="today-pnl">
    <div className="card-panel today-toolbar">
      <label>持仓日期 <input aria-label="持仓日期" type="date" className="input-control" value={day} max={beijingDate()}
        onChange={e => { if (e.target.value) setDay(e.target.value); }} /></label>
      <label>渠道 <select aria-label="渠道" className="input-control" value={channel} onChange={e => setChannel(e.target.value)}>
        <option value="">全部渠道</option>
        {[...new Set([...(snapshot?.available_channels || []), ...(channel ? [channel] : [])])].map(c => <option key={c}>{c}</option>)}
      </select></label>
      <input className="input-control" aria-label="Ticker 筛选" placeholder="输入 Ticker 自动提示 / 模糊筛选…"
        list="today-tickers" value={ticker} onChange={e => setTicker(e.target.value)} />
      <datalist id="today-tickers">{(snapshot?.tickers || []).map(t => <option key={t} value={t} />)}</datalist>
      <form onSubmit={e => { e.preventDefault(); setSearch(searchDraft.trim()); }}>
        <input className="input-control" aria-label="组合搜索" placeholder="组合搜索：Ticker、渠道、人员…" value={searchDraft}
          onChange={e => setSearchDraft(e.target.value)} /> <button className="btn">搜索</button>
      </form>
      <select aria-label="排序方式" className="input-control" value={presets.findIndex(p => p[1] === sort.col && p[2] === sort.dir)}
        onChange={e => { const p = presets[Number(e.target.value)]; if (p) setSort({ col: p[1], dir: p[2] }); }}>
        <option value={-1}>自定义排序</option>{presets.map((p, i) => <option key={p[0]} value={i}>{p[0]}</option>)}
      </select>
      <button className="btn" onClick={() => { setChannel(""); setTicker(""); setSearch(""); setSearchDraft(""); }}>清除筛选</button>
      <button className="btn" onClick={() => setRefresh(v => v + 1)} disabled={loading}>刷新</button>
      <a className="btn" href={`/api/v1/export/report/${encodeURIComponent("今日盈亏")}?${key}`}>导出 CSV</a>
    </div>
    <div className="card-panel today-quote" role="status">
      <label><input type="checkbox" checked={live} onChange={e => setLive(e.target.checked)} /> 长桥实时行情 · 10秒</label>
      <span>{snapshot?.quote.status || (loading ? "正在读取当前筛选…" : "等待数据")}</span>
      {snapshot && <span>新鲜报价 {snapshot.quote.fresh}/{snapshot.quote.count} · 最近刷新 {clock(snapshot.quote.refreshed_at)} 北京时间</span>}
      {snapshot && <span>{snapshot.quote.latest_at ? `报价截至 ${new Date(snapshot.quote.latest_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })} 北京时间` :
        `参考估值日期 ${snapshot.quote.reference_dates.join("、") || "未知"}`}</span>}
    </div>
    {error && <div role="alert" className="today-warning">{error}。{snapshot ? "保留上次成功快照，请关注实际报价时间。" : "当前筛选尚未取得数据。"}
      <button className="btn" onClick={() => setRefresh(v => v + 1)}>重试</button></div>}
    <div className="today-cards" data-testid="today-cards">
      {[
        [day === beijingDate() ? "今日盈亏" : "当日盈亏", money(summary?.pnl), summary?.pnl],
        ["当日收益率", money(summary?.rate, true), summary?.rate],
        ["上涨 / 下跌标的", summary ? `${summary.up} / ${summary.down}　平盘 ${summary.flat}　待报价 ${summary.unknown}` : "—", null],
        ["累计盈亏（含已清仓）", money(summary?.cumulative), summary?.cumulative],
      ].map(([title, value, raw]) => <div className="card-panel today-card" key={String(title)}>
        <span title={title === "累计盈亏（含已清仓）" ? "沿用桌面：当前人员、日期和渠道的全部历史盈亏；本表 Ticker 筛选不裁切此项。" : undefined}>{title}</span>
        <strong style={{ color: color(raw == null ? null : String(raw)) }}>{value}</strong>
      </div>)}
    </div>
    <div role="tablist" aria-label="盈亏走势期间">
      {[["day", "当日走势"], ["month", "当月"], ["year", "当年"]].map(([value, label]) =>
        <button key={value} role="tab" aria-selected={curveKind === value} className="btn" onClick={() => setCurveKind(value)}>{label}</button>)}
    </div>
    {curveKind !== "day" ? <HistoricalTrendView key={curveKind} personId={personId} initialKind={curveKind} embedded selectedChannel={channel} selectedTicker={ticker} search={search} /> : <section className="card-panel today-curve" aria-label="当日盈亏走势图">
      <h3>当日五分钟盈亏走势（USD） · 实盘交易日 {curve?.day || day}</h3>
      {curveError && <p role="alert" className="today-warning">{curveError}</p>}
      {curve?.fallback && <p className="today-warning">所选日期 {curve.target_day} 非美股交易日，已回溯至 {curve.day}</p>}
      {chartOptions && <ReactECharts option={chartOptions} notMerge style={{ height: 420 }} />}
      {!curve?.complete_points && <p className="today-warning">{curve?.pending ? "后台正在采集并计算五分钟数据…" : "当前筛选暂无有效曲线数据；未生成模拟点。"}</p>}
      {curve && <p>{curve.complete_points} 个完整五分钟历史点
        {curve.latest && ` · 最近有效盈亏 ${money(curve.latest.pnl)} USD · 截至 ${clock(curve.latest.time)} 北京时间`}</p>}
      {curve?.high && curve.low && <p>最高 {money(curve.high.pnl)}（{clock(curve.high.time)}） · 最低 {money(curve.low.pnl)}（{clock(curve.low.time)}）</p>}
      {curve?.warning && <p role="alert" className="today-warning">{curve.warning}</p>}
      {curve?.message && <p className="today-warning">{curve.message}</p>}
      <p>北京时间；完整交易日从夜盘至盘后，跨日后时钟归零。缺失断线，未到时段留白；含当日清仓，可能与仅当前持仓的上方卡片不同。</p>
    </section>}
    <section className="card-panel today-channels" aria-label="各渠道盈亏与资产汇总">
      <div><h3>各渠道当日盈亏（USD）</h3>
        <ReactECharts style={{ height: 285 }} notMerge option={{ animation: false,
          grid: { left: 70, right: 25, top: 60, bottom: 40 },
          tooltip: { trigger: "axis", renderMode: "richText", formatter: (params: any[]) => {
            const c = snapshot?.channels[params[0]?.dataIndex];
            return c ? `${c.channel}\n当日盈亏 ${signed(c.pnl)}\n当日收益率 ${signed(c.rate, true)}` : "—";
          } },
          xAxis: { type: "category", data: (snapshot?.channels || []).map(c => c.channel), triggerEvent: true },
          yAxis: { type: "value" }, series: [{ type: "bar", barMaxWidth: 65,
            data: (snapshot?.channels || []).map(c => ({ value: c.pnl == null ? null : Number(c.pnl),
              itemStyle: channelStyle(c.channel, Number(c.pnl) >= 0 ? "#16A34A" : "#DC2626") })),
            label: { show: true, position: "top", lineHeight: 18, formatter: (p: any) => {
              const c = snapshot?.channels[p.dataIndex];
              return c ? `{${signStyle(c.pnl)}|${signed(c.pnl)}}\n{${signStyle(c.rate)}|${signed(c.rate, true)}}` : "—";
            }, rich: { positive: { color: "#16A34A" }, negative: { color: "#DC2626" },
              neutral: { color: "#475569" }, unknown: { color: "#94A3B8" } } },
            markLine: { symbol: "none", data: [{ yAxis: 0 }], lineStyle: { color: "#334155", width: 2 } },
          }] }} onEvents={channelEvents} />
        {snapshot?.channels.filter(c => c.pnl == null).map(c => <p key={c.channel} className="today-warning">{c.channel}：缺报价/基准，盈亏未知</p>)}
      </div>
      <div><h3>各渠道持仓资产（USD）</h3>
        <ReactECharts style={{ height: 285 }} notMerge option={{ animation: false,
          grid: { left: 85, right: 95, top: 35, bottom: 40 },
          tooltip: { trigger: "axis", valueFormatter: (v: any) => `${money(v)} USD` },
          xAxis: { type: "value" },
          yAxis: { type: "category", inverse: true, data: (snapshot?.channels || []).map(c => c.channel), triggerEvent: true },
          series: [{ type: "bar", barMaxWidth: 28,
            data: (snapshot?.channels || []).map(c => ({ value: c.asset == null ? null : Number(c.asset),
              itemStyle: channelStyle(c.channel, "#2563EB") })),
            label: { show: true, position: "right", formatter: (p: any) => money(p.value) },
          }] }} onEvents={channelEvents} />
        <p>{channel || "全部渠道"}持仓市值 {money(snapshot?.asset_total)} USD · 不含现金 · 点击任一渠道柱体筛选，再点恢复全部</p>
        {snapshot?.channels.filter(c => c.asset == null).map(c => <p key={c.channel} className="today-warning">{c.channel}：持仓市值数据缺失</p>)}
        <table className="finance-table"><thead><tr><th>当前范围</th><th>当日盈亏</th><th>持仓市值</th></tr></thead>
          <tbody>{snapshot?.selected_channels.map(c => <tr key={c.channel}><td>{c.channel}</td><td>{money(c.pnl)}</td><td>{money(c.asset)}</td></tr>)}</tbody></table>
        {channel && <button className="btn" onClick={() => setChannel("")}>恢复全部渠道</button>}
      </div>
    </section>
    <p className="today-note">{snapshot?.note || "正在读取今日盈亏，缺报价或基准的指标保持未知。"}</p>
    <label><input type="checkbox" checked={showQuoteColumns} onChange={e => setShowQuoteColumns(e.target.checked)} /> 显示逐行行情时段与报价时间</label>
    <div className="today-table"><table className="finance-table" data-testid="today-holdings">
      <thead><tr>{visibleColumns.map(i => <th key={i} onClick={() => { const col = snapshot?.columns[i]; if (col) setSort({ col,
        dir: sort.col === col && sort.dir === "desc" ? "asc" : "desc" }); }}>
        {snapshot?.columns[i] || ["渠道", "Ticker", "持仓数量", "昨收价($)", "最新价($)", "今日涨跌幅", "今日盈亏($)", "行情时段", "报价时间", "成本价($)", "累计盈亏($)", "当前市值($)", "回本涨幅", "占比"][i]}
        {snapshot?.columns[i] === sort.col ? sort.dir === "desc" ? " ↓" : " ↑" : " ↕"}</th>)}</tr></thead>
      <tbody>{snapshot && <tr className="today-summary-row">{visibleColumns.map(i => <td key={i}
        className={![0, 1, 7, 8].includes(i) ? "num" : ""}
        style={{ color: [5, 6, 10].includes(i) ? color(snapshot.summary_row?.[i] ?? null) : undefined }}>
        {i === 0 ? snapshot.summary_row?.[0] ?? `${channel || "全部渠道"}汇总` :
          money(snapshot.summary_row?.[i], [5, 12, 13].includes(i))}
      </td>)}</tr>}
      {snapshot?.rows.map((row, ri) => <tr key={ri}>{visibleColumns.map(i => <td key={i} className={![0, 1, 7, 8].includes(i) ? "num" : ""}
        style={{ color: [5, 6, 10].includes(i) ? color(row[i]) : undefined }}>
        {[0, 1, 7, 8].includes(i) ? row[i] ?? "—" : money(row[i], [5, 12, 13].includes(i))}
        {i === 7 && snapshot.items[ri]?._meta.live_status && <small> · {snapshot.items[ri]._meta.live_status}</small>}
      </td>)}</tr>)}
      {!snapshot?.rows.length && <tr><td colSpan={visibleColumns.length}>{loading ? "正在计算当前筛选…" : "当前筛选暂无持仓明细"}</td></tr>}</tbody>
    </table></div>
    {snapshot && <p className="today-source">只读来源：{snapshot.source.path} · 所选日期 {snapshot.source.selected_day}
      · 有效收益日 {snapshot.source.profit_day || "无数据"} · {snapshot.total_rows} 条持仓 · 快照 {snapshot.source.identity}</p>}
  </div>;
};
