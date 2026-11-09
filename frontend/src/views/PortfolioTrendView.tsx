import { Fragment, useEffect, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import { api } from "../api/client";
import "./portfolio-trend.css";

type Amount = string | number | null;
type Kind = "money" | "ratio" | "count" | "pp";
type Metric = [keyof Metrics, string, Kind];
type Metrics = Record<"market_value" | "net_buy" | "difference" | "large" | "leveraged" | "equity" | "concentration" | "count", Amount>;
type Part = { broker: string; account: number | null; category: string; quantity: Amount; price: Amount; market_value: Amount };
type StockAmounts = { quantity: Amount; market_value: Amount };
type Stock = StockAmounts & { ticker: string; category: string; share: Amount; parts: Part[];
  by_channel: Record<string, StockAmounts>; by_category: Record<string, StockAmounts> };
type Month = Metrics & {
  month: string; valuation_date: string; categories: Record<string, Amount>; channels: Record<string, Amount>;
  stocks: Stock[]; holdings_incomplete: boolean;
};
type Chart = { id: number; title: string; kind: string; ratio: boolean; series: { name: string; points: [string, Amount][] }[] };
type Data = { months: Month[]; charts: Chart[]; currency: string; note: string; metrics: Metric[] };
type Detail = { chart: number; month: string; name: string };

const colors = ["#2563EB", "#F59E0B", "#10B981", "#7C3AED", "#DB2777", "#0891B2"];
const amount = (v: Amount | undefined) => v == null ? null : Number(v);
const difference = (a: Amount | undefined, b: Amount | undefined) => a == null || b == null ? null : Number(a) - Number(b);
const share = (v: Amount | undefined, total: Amount | undefined) =>
  v == null || total == null || Number(total) <= 0 ? null : Number(v) / Number(total);
const fmt = (value: Amount | undefined, kind: Kind = "money") => {
  if (value == null) return "—";
  const v = Number(value);
  if (kind === "ratio") return `${(v * 100).toFixed(2)}%`;
  if (kind === "pp") return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)} pp`;
  return v.toLocaleString("en-US", { minimumFractionDigits: kind === "count" ? 0 : 2, maximumFractionDigits: kind === "count" ? 0 : 2 });
};
const monthOffset = (month: string, offset: number) => {
  const [year, number] = month.split("-").map(Number);
  const index = year * 12 + number - 1 + offset;
  return `${Math.floor(index / 12)}-${String(index % 12 + 1).padStart(2, "0")}`;
};

function chartOption(chart: Chart, rows: Month[], selected = "") {
  const stacked = chart.kind === "stacked_area";
  const percentStack = stacked && chart.ratio;
  const kind = chart.ratio ? "ratio" : chart.id === 14 ? "count" : "money";
  const months = [];
  if (rows.length) {
    for (let month = rows[0].month; month <= rows[rows.length - 1].month; month = monthOffset(month, 1)) months.push(month);
  }
  const series = chart.series.map(({ name, points }, i) => {
    const values = new Map(points);
    const line = chart.kind === "line" || stacked;
    const color = colors[i % colors.length];
    return {
      name, type: line ? "line" : "bar", stack: stacked ? "allocation" : undefined,
      areaStyle: stacked ? { color, opacity: 0.57 } : undefined,
      connectNulls: false, showSymbol: true, symbolSize: stacked ? 3 : 6, barMaxWidth: 36,
      itemStyle: { color }, lineStyle: { color, width: stacked ? 1 : 2 },
      data: months.map(month => {
        const value = amount(values.get(month));
        if (value == null) return null;
        const latest = chart.id === 13 && month === months[months.length - 1];
        return {
          value, symbolSize: stacked ? 3 : month === selected ? 9 : 6,
          itemStyle: { borderColor: "#1E293B", borderWidth: !stacked && month === selected ? 1 : 0 },
          label: latest || chart.id === 14 ? { show: true, position: "top", formatter: fmt(value, kind) } : undefined,
        };
      }),
    };
  });
  return {
    animation: false,
    legend: series.length > 1 ? { type: "scroll", bottom: 0 } : undefined,
    grid: { top: 18, left: 20, right: 22, bottom: series.length > 1 ? 43 : 22, containLabel: true },
    tooltip: { trigger: "axis", renderMode: "richText", valueFormatter: (value: number) => fmt(value, kind) },
    xAxis: { type: "category", boundaryGap: stacked ? false : undefined, data: months, axisLabel: { hideOverlap: true, color: "#64748B" } },
    yAxis: { type: "value", min: stacked ? 0 : undefined, max: percentStack ? 1 : undefined, interval: percentStack ? 0.2 : undefined, minInterval: chart.id === 14 ? 1 : undefined,
      axisLabel: { color: "#64748B", formatter: (value: number) => fmt(value, kind) },
      splitLine: { lineStyle: { color: "#E8EDF5" } } },
    series,
  };
}

function download(url: string, name: string) {
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
}

export function PortfolioTrendView({ personId, broker, refreshKey }: { personId: number | null; broker: string; refreshKey: number }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [range, setRange] = useState(0);
  const [selected, setSelected] = useState("");
  const [sort, setSort] = useState<{ key: "month" | keyof Metrics; asc: boolean }>({ key: "month", asc: false });
  const [detail, setDetail] = useState<Detail | null>(null);
  const charts = useRef<Record<number, ReactECharts | null>>({});
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setDetail(null);
    setError("");
    api.portfolioTrend(personId, broker, controller.signal).then(result => {
      if (!controller.signal.aborted) setData(result);
    }).catch(() => {
      if (!controller.signal.aborted) setError("走势图查询失败，请刷新后重试。");
    });
    return () => controller.abort();
  }, [personId, broker, refreshKey]);

  const close = () => setDetail(null);
  useEffect(() => {
    if (!detail) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setDetail(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detail]);

  if (!data) return <section className="portfolio-trend" role="status">{error || "正在读取月末持仓走势…"}</section>;
  const latest = data.months[data.months.length - 1];
  const previous = latest && data.months.find(row => row.month === monthOffset(latest.month, -1));
  const first = latest && range ? monthOffset(latest.month, 1 - range) : "";
  const rows = data.months.filter(row => row.month >= first);
  const visibleSelection = rows.some(row => row.month === selected) ? selected : latest?.month;
  const sorted = [...rows].sort((a, b) => {
    const left = a[sort.key], right = b[sort.key];
    if (left == null || right == null) return left == null ? right == null ? 0 : 1 : -1;
    const order = sort.key === "month" ? String(left).localeCompare(String(right)) : Number(left) - Number(right);
    return sort.asc ? order : -order;
  });
  const activate = (chart: number, month: string, name: string) => {
    setSelected(month);
    setDetail({ chart, month, name });
  };
  const exportCsv = () => {
    const lines = [["月份", ...data.metrics.map(([, label]) => label)],
      ...sorted.map(row => [row.month, ...data.metrics.map(([key, , kind]) => fmt(row[key], kind))])];
    const blob = new Blob(["\uFEFF", lines.map(line => line.map(cell => `"${cell.replace(/"/g, '""')}"`).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    download(url, "关键指标月度趋势.csv");
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  const exportPng = async () => {
    const images = await Promise.all(data.charts.map(chart => {
      const url = charts.current[chart.id]?.getEchartsInstance().getDataURL({ pixelRatio: 2, backgroundColor: "#fff" });
      if (!url) return null;
      const image = new Image();
      image.src = url;
      return image.decode().then(() => ({ image, title: chart.title }));
    }));
    const available = images.filter((image): image is NonNullable<typeof image> => image != null);
    if (!available.length) return;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(...available.map(({ image }) => image.width)) + 40;
    canvas.height = available.reduce((height, { image }) => height + image.height + 56, 20);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#1E293B";
    ctx.font = "bold 24px sans-serif";
    let top = 20;
    for (const { image, title } of available) {
      ctx.fillText(title, 20, top + 26);
      ctx.drawImage(image, 20, top + 40);
      top += image.height + 56;
    }
    download(canvas.toDataURL(), "走势图.png");
  };
  const drawerRow = detail && rows.find(row => row.month === detail.month);
  const headers: [typeof sort.key, string][] = [["month", "月份"], ...data.metrics.map(([key, label]): [keyof Metrics, string] => [key, label])];

  return <div className="portfolio-trend">
    <div className="pt-toolbar">
      <label>统计范围 <select value={range} onChange={event => { setRange(Number(event.target.value)); close(); }}>
        <option value={0}>全部时间</option><option value={12}>最近12个月</option><option value={24}>最近24个月</option>
      </select></label>
      <span>数据截至 {latest?.valuation_date || "—"} · {data.currency}</span>
      <button disabled={!rows.length} onClick={() => { void exportPng().catch(() => setError("PNG 导出失败，请重试。")); }}>导出 PNG</button>
    </div>
    {error && <p role="status">{error}</p>}
    {!rows.length && <p role="status">当前筛选范围暂无已结束月份的持仓快照。</p>}
    <div className="pt-kpis">
      {([["market_value", "当前股票市值", "money"], ["net_buy", "累计净买入", "money"],
        ["concentration", "前五大持仓集中度", "ratio"], ["count", "持仓标的数", "count"]] as Metric[]).map(([key, label, kind]) => {
        const delta = difference(latest?.[key], previous?.[key]);
        return <article className="pt-card" key={key}><span>{label}</span><strong>{fmt(latest?.[key], kind)} {kind === "money" && data.currency}</strong>
          {delta != null && <small className={delta < 0 ? "pt-loss" : delta > 0 ? "pt-profit" : ""}>较上月 {delta > 0 && kind !== "ratio" ? "+" : ""}{fmt(delta, kind === "ratio" ? "pp" : kind)}</small>}</article>;
      })}
    </div>
    <div className="pt-grid">
      {data.charts.map(chart => <article className="pt-card" key={chart.id}>
        <div className="pt-toolbar pt-chart-heading"><h3>{chart.title}</h3>
          {chart.id === 11 && <button disabled={!visibleSelection} onClick={() => activate(11, visibleSelection, "大盘")}>查看详情 ›</button>}
        </div>
        <ReactECharts ref={element => { charts.current[chart.id] = element; }}
          option={chartOption(chart, rows, visibleSelection)} style={{ height: 220 }}
          notMerge onEvents={{ click: ({ name, seriesName }: { name: string; seriesName: string }) => activate(chart.id, name, seriesName) }} />
      </article>)}
    </div>
    <article className="pt-card">
      <div className="pt-toolbar"><h3>关键指标月度趋势</h3><button disabled={!rows.length} onClick={exportCsv}>导出 CSV</button></div>
      <div className="pt-table-scroll pt-month-table"><table><thead><tr>{headers.map(([key, label]) =>
        <th key={key} aria-sort={sort.key === key ? sort.asc ? "ascending" : "descending" : "none"}>
          <button onClick={() => setSort({ key, asc: sort.key === key && !sort.asc })}>{label}{sort.key === key ? sort.asc ? " ▲" : " ▼" : ""}</button></th>)}</tr></thead>
        <tbody>{sorted.map(row => <tr key={row.month} className={row.month === visibleSelection ? "pt-selected" : ""} onClick={() => setSelected(row.month)}>
          <td><button onClick={() => activate(18, row.month, "")}>{row.month}</button></td>
          {data.metrics.map(([key, , kind]) => <td key={key}>{fmt(row[key], kind)}</td>)}</tr>)}</tbody>
      </table></div>
    </article>
    {drawerRow && detail && <TrendDrawer key={`${detail.chart}:${detail.month}:${detail.name}`} detail={detail} row={drawerRow} rows={rows} data={data} onClose={close} />}
  </div>;
}

function TrendDrawer({ detail, row, rows, data, onClose }: { detail: Detail; row: Month; rows: Month[]; data: Data; onClose: () => void }) {
  const isCategory = detail.chart === 11;
  const isChannel = detail.chart === 12;
  const isSeries = isCategory || isChannel;
  const ranked = detail.chart === 13;
  const [byShare, setByShare] = useState(isCategory);
  const [expanded, setExpanded] = useState<string | null>(null);
  const prev = (item: Month) => data.months.find(candidate => candidate.month === monthOffset(item.month, -1));
  const previous = prev(row);
  const value = (item?: Month) => (isCategory ? item?.categories : item?.channels)?.[detail.name];
  const percent = (item?: Month) => share(value(item), item?.market_value);
  const groupTitle = detail.chart === 18 ? "市值与累计净买入" : ranked ? "前五大持仓详情" : "月末持仓标的";
  const title = isSeries ? `${detail.name} 持仓趋势详情` : `${row.month} ${groupTitle}`;
  const stocks = ranked ? row.concentration == null ? [] : row.stocks.slice(0, 5) : row.stocks;
  return <div className="pt-drawer-layer">
    <button className="pt-mask" aria-label="关闭详情" onClick={onClose} />
    <aside className="pt-drawer" role="dialog" aria-modal="true" aria-label={title}>
      <div className="pt-toolbar"><h2>{title}</h2><button autoFocus onClick={onClose} aria-label="关闭详情">✕</button></div>
      <small>{row.month} · {row.valuation_date || "估值日期缺失"} · {data.currency}</small>
      {detail.chart === 18 && <>
        <p>{data.note}</p>
        <TrendTable headers={["指标", "当月", "较上月"]} rows={data.metrics.map(([key, label, kind]) =>
          [label, fmt(row[key], kind), fmt(difference(row[key], previous?.[key]), kind === "ratio" ? "pp" : kind)])} />
      </>}
      {isSeries && <>
        <TrendTable headers={["月末市值", "占比", isCategory ? "上月占比" : "上月市值", "较上月变化"]} rows={[[
          fmt(value(row)), fmt(percent(row), "ratio"), isCategory ? fmt(percent(previous), "ratio") : fmt(value(previous)),
          isCategory ? fmt(difference(percent(row), percent(previous)), "pp") : fmt(difference(value(row), value(previous))),
        ]]} />
        <div className="pt-toolbar">
          <button aria-pressed={byShare} onClick={() => setByShare(true)}>占比走势</button>
          <button aria-pressed={!byShare} onClick={() => setByShare(false)}>市值走势</button>
        </div>
        <ReactECharts notMerge option={chartOption({ id: detail.chart, kind: "line", title, ratio: byShare,
          series: [{ name: detail.name, points: rows.map(item => [item.month, byShare ? percent(item) : value(item) ?? null]) }] },
          rows, row.month)} style={{ height: 220 }} />
        <TrendTable headers={["月份", "月末市值", "占比", "占比变化"]} rows={[...rows].reverse().map(item =>
          [item.month, fmt(value(item)), fmt(percent(item), "ratio"), fmt(difference(percent(item), percent(prev(item))), "pp")])} />
      </>}
      {detail.chart !== 18 && <>
        <h3>{row.month} 持仓明细</h3>
        {row.holdings_incomplete && <p>部分持仓数量或估值缺失。</p>}
        {ranked && row.concentration == null && <p>当前月份缺少可确认的前五大排名。</p>}
        <div className="pt-table-scroll"><table>
          <thead><tr>{ranked && <th>排名</th>}<th>Ticker</th><th>分类</th><th>股数</th>
            {isChannel && <th>月末价格</th>}<th>月末市值</th><th>占比</th><th>涉及渠道</th>
          </tr></thead>
          <tbody>{stocks.map((stock, index) => {
            const parts = stock.parts.filter(part => isChannel ? part.broker === detail.name : isCategory ? part.category === detail.name : true);
            const totals = isChannel ? stock.by_channel[detail.name] : isCategory ? stock.by_category[detail.name] : stock;
            if (!parts.length || !totals) return null;
            const prices = new Set(parts.map(part => part.price));
            const open = expanded === stock.ticker;
            return <Fragment key={stock.ticker}>
              <tr>{ranked && <td>{index + 1}</td>}
                <td><button aria-expanded={open} onClick={() => setExpanded(open ? null : stock.ticker)}>{stock.ticker}</button></td>
                <td>{stock.category || "—"}</td><td>{fmt(totals.quantity)}</td>
                {isChannel && <td>{fmt(prices.size === 1 ? parts[0].price : null)}</td>}
                <td>{fmt(totals.market_value)}</td><td>{fmt(share(totals.market_value, row.market_value), "ratio")}</td>
                <td>{[...new Set(parts.map(part => part.broker))].join("、")}</td>
              </tr>
              {open && <tr><td colSpan={6 + Number(ranked) + Number(isChannel)}>
                <TrendTable headers={["渠道", "账户 ID", "股数", "月末价格", "月末市值"]} rows={parts.map(part =>
                  [part.broker, part.account == null ? "—" : String(part.account), fmt(part.quantity), fmt(part.price), fmt(part.market_value)])} />
              </td></tr>}
            </Fragment>;
          })}</tbody>
        </table></div>
      </>}
    </aside>
  </div>;
}

function TrendTable({ headers, rows }: { headers: string[]; rows: string[][] }) {
  return <div className="pt-table-scroll"><table><thead><tr>{headers.map(label => <th key={label}>{label}</th>)}</tr></thead>
    <tbody>{rows.map((row, i) => <tr key={i}>{row.map((value, j) => <td key={j}>{value}</td>)}</tr>)}</tbody></table></div>;
}
