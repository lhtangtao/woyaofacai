import React, { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";
import { TrendingUp, TrendingDown, Award, BarChart3, PieChart, LineChart } from "lucide-react";
import { api } from "../api/client";
import { ErrorBoundary } from "../components/ErrorBoundary";

interface ChartsViewProps {
  mode: "asset" | "monthly" | "distribution" | "gallery" | "top_days" | "top_months" | "ranking";
  personId: number | null;
}

function convertToEchartsOption(chart: any) {
  if (chart.option) return chart.option;

  if (chart.kind === "pie") {
    const pieData = (chart.series?.[0]?.points || []).map(([k, v]: any) => ({
      name: String(k),
      value: v != null ? Number(v) : 0,
    }));
    return {
      title: {
        text: chart.title,
        left: "center",
        top: 10,
        textStyle: { fontSize: 13, fontWeight: 600 },
      },
      tooltip: {
        trigger: "item",
        formatter: "{b}: {c} ({d}%)",
      },
      legend: {
        bottom: 8,
        type: "scroll",
        textStyle: { fontSize: 11 },
      },
      series: [
        {
          name: chart.title,
          type: "pie",
          radius: ["35%", "65%"],
          center: ["50%", "48%"],
          avoidLabelOverlap: true,
          itemStyle: { borderRadius: 4 },
          label: { show: false },
          emphasis: { label: { show: true, fontSize: 12, fontWeight: "bold" } },
          data: pieData,
        },
      ],
    };
  }

  if (chart.kind === "capital_assets") {
    const seriesList = chart.series || [];
    const xCategories: string[] = [];
    seriesList.forEach((s: any) => {
      (s.points || []).forEach(([k]: any) => {
        const kStr = String(k);
        if (!xCategories.includes(kStr)) xCategories.push(kStr);
      });
    });

    const pt0 = new Map((seriesList[0]?.points || []).map(([k, v]: any) => [String(k), v]));
    const pt1 = new Map((seriesList[1]?.points || []).map(([k, v]: any) => [String(k), v]));
    const pt2 = new Map((seriesList[2]?.points || []).map(([k, v]: any) => [String(k), v]));
    const pt3 = new Map((seriesList[3]?.points || []).map(([k, v]: any) => [String(k), v]));

    return {
      title: {
        text: "累计净买入与股票市值变化",
        left: "left",
        top: 10,
        textStyle: { fontSize: 15, fontWeight: 700, color: "#1F2937" },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { type: "dashed", color: "#9CA3AF" } },
        formatter: (params: any) => {
          if (!params || !params.length) return "";
          const xName = params[0].name;
          const origKey = xCategories.find((k) => (k.length === 7 ? k.slice(2) : k) === xName) || xName;
          const m = Number(pt3.get(origKey) ?? 0);
          const v0 = Number(pt0.get(origKey) ?? 0);
          const v2 = Number(pt2.get(origKey) ?? 0);
          const c = v2 > 0 ? v0 + v2 : v0;
          const diff = m - c;
          const isProf = diff >= 0;
          return `
            <div style="font-weight:700;margin-bottom:6px;border-bottom:1px solid #F1F5F9;padding-bottom:4px;">${origKey.replace("-", "年")}月</div>
            <div style="display:flex;justify-content:space-between;gap:16px;color:#1E293B;"><span>● 股票市值:</span><span style="font-weight:700;">$${Number(m).toLocaleString(undefined, {minimumFractionDigits: 2})}</span></div>
            <div style="display:flex;justify-content:space-between;gap:16px;color:#475569;"><span>● 累计净买入:</span><span style="font-weight:700;">$${Number(c).toLocaleString(undefined, {minimumFractionDigits: 2})}</span></div>
            <div style="display:flex;justify-content:space-between;gap:16px;color:${isProf ? '#16A34A' : '#DC2626'};"><span>市值差额:</span><span style="font-weight:700;">${isProf ? '+' : ''}$${Number(diff).toLocaleString(undefined, {minimumFractionDigits: 2})}</span></div>
          `;
        },
      },
      legend: {
        top: 12,
        left: "center",
        data: ["累计净买入基柱", "市值高于净买入", "市值低于净买入", "股票市值"],
        textStyle: { fontSize: 12, color: "#334155" },
      },
      grid: { left: "4%", right: "4%", top: 55, bottom: 25, containLabel: true },
      xAxis: {
        type: "category",
        data: xCategories.map((x) => (x.length === 7 ? x.slice(2) : x)),
        axisLabel: { fontSize: 11, color: "#64748B" },
      },
      yAxis: {
        type: "value",
        name: "金额 (USD)",
        splitLine: { lineStyle: { color: "#E2E8F0" } },
        axisLabel: {
          fontSize: 11,
          color: "#64748B",
          formatter: (v: number) => v.toLocaleString(),
        },
      },
      series: [
        {
          name: "累计净买入基柱",
          type: "bar",
          stack: "total",
          data: xCategories.map((x) => pt0.get(x) ?? null),
          itemStyle: { color: "#B8CBD7" },
          barMaxWidth: 36,
        },
        {
          name: "市值高于净买入",
          type: "bar",
          stack: "total",
          data: xCategories.map((x) => pt1.get(x) ?? null),
          itemStyle: { color: "#70AD47" },
          barMaxWidth: 36,
        },
        {
          name: "市值低于净买入",
          type: "bar",
          stack: "total",
          data: xCategories.map((x) => pt2.get(x) ?? null),
          itemStyle: { color: "#C00000" },
          barMaxWidth: 36,
        },
        {
          name: "股票市值",
          type: "line",
          data: xCategories.map((x) => pt3.get(x) ?? null),
          itemStyle: { color: "#4472C4" },
          lineStyle: { width: 2.5 },
          symbol: "circle",
          symbolSize: 6,
        },
      ],
    };
  }

  // Line or Bar chart
  const seriesList = chart.series || [];
  const xCategories: string[] = [];
  seriesList.forEach((s: any) => {
    (s.points || []).forEach(([k]: any) => {
      const kStr = String(k);
      if (!xCategories.includes(kStr)) xCategories.push(kStr);
    });
  });

  const colors = ["#2563eb", "#0d9488", "#f59e0b", "#8b5cf6", "#ec4899", "#06b6d4"];
  const eSeries = seriesList.map((s: any, idx: number) => {
    const ptMap = new Map((s.points || []).map(([k, v]: any) => [String(k), v]));
    const dataVals = xCategories.map((x) => ptMap.get(x) ?? null);
    const isLine = chart.kind === "line";
    return {
      name: s.name || chart.title,
      type: isLine ? "line" : "bar",
      data: dataVals,
      smooth: true,
      showSymbol: xCategories.length <= 40,
      itemStyle: {
        color: colors[idx % colors.length],
      },
    };
  });

  return {
    title: {
      text: chart.title,
      left: "center",
      top: 10,
      textStyle: { fontSize: 13, fontWeight: 600 },
    },
    tooltip: {
      trigger: "axis",
    },
    legend: {
      top: 32,
      textStyle: { fontSize: 11 },
    },
    grid: {
      left: "6%",
      right: "6%",
      top: 65,
      bottom: 25,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      data: xCategories,
      axisLabel: { fontSize: 11 },
    },
    yAxis: {
      type: "value",
      axisLabel: {
        fontSize: 11,
        formatter: chart.ratio ? "{value}%" : "{value}",
      },
      splitLine: { lineStyle: { opacity: 0.15 } },
    },
    series: eSeries,
  };
}

export const ChartsView: React.FC<ChartsViewProps> = ({ mode, personId }) => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [bestDateSort, setBestDateSort] = useState<"desc" | "asc" | null>(null);
  const [worstDateSort, setWorstDateSort] = useState<"desc" | "asc" | null>(null);

  useEffect(() => {
    setBestDateSort(null);
    setWorstDateSort(null);
    setLoading(true);
    let promise: Promise<any>;
    if (mode === "top_days") {
      promise = api.topDays(personId);
    } else if (mode === "top_months") {
      promise = api.topMonths(personId);
    } else if (mode === "ranking") {
      promise = api.ranking(personId);
    } else {
      promise = api.charts(personId);
    }

    promise
      .then((res) => {
        if (res.status === "ok") {
          setData(res);
        }
      })
      .catch((err) => console.error("Failed to load chart data:", err))
      .finally(() => setLoading(false));
  }, [mode, personId]);

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "300px", color: "var(--brand)" }}>
        正在生成高精度财务图表...
      </div>
    );
  }

  // Top Days or Top Months Side-by-Side View
  if (mode === "top_days" || mode === "top_months") {
    const isDay = mode === "top_days";
    const rawBest = (data?.best || data?.best_days || data?.best_months || data?.top_gainers || []).slice(0, 20);
    const rawWorst = (data?.worst || data?.worst_days || data?.worst_months || data?.top_losers || []).slice(0, 20);

    const getDateStr = (item: any) => String(item.date || item.day || item.month || "");

    const bestItems = bestDateSort
      ? [...rawBest].sort((a, b) =>
          bestDateSort === "desc"
            ? getDateStr(b).localeCompare(getDateStr(a))
            : getDateStr(a).localeCompare(getDateStr(b))
        )
      : rawBest;

    const worstItems = worstDateSort
      ? [...rawWorst].sort((a, b) =>
          worstDateSort === "desc"
            ? getDateStr(b).localeCompare(getDateStr(a))
            : getDateStr(a).localeCompare(getDateStr(b))
        )
      : rawWorst;

    return (
      <ErrorBoundary fallbackTitle={isDay ? "Top日汇总渲染异常" : "Top月汇总渲染异常"}>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", overflowY: "auto" }}>
          <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-heading)" }}>
            {isDay ? "全时期单日最大盈利与最大亏损日汇总 (Side-by-Side)" : "全时期单月最大盈利与最大亏损月汇总"}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", minHeight: "500px" }}>
            {/* Best / Gainers Column */}
            <div className="card-panel" style={{ display: "flex", flexDirection: "column", padding: "16px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "var(--profit-color)", fontWeight: 700, marginBottom: "12px" }}>
                <TrendingUp size={18} />
                <span>{isDay ? "最佳单日 Top 20 盈利日" : "最佳月份 Top 20 盈利月"}</span>
              </div>

              <div className="table-container" style={{ border: "none" }}>
                <table className="finance-table">
                  <thead>
                    <tr>
                      <th>排名</th>
                      <th
                        onClick={() => setBestDateSort((prev) => (prev === "desc" ? "asc" : "desc"))}
                        style={{ cursor: "pointer", userSelect: "none" }}
                        title="点击切换日期排序"
                      >
                        <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                          <span>{isDay ? "交易日期" : "月份"}</span>
                          {bestDateSort && (
                            <span style={{ fontSize: "12px", color: "var(--brand, #2563eb)", fontWeight: 700 }}>
                              {bestDateSort === "desc" ? "↓" : "↑"}
                            </span>
                          )}
                        </span>
                      </th>
                      <th
                        onClick={() => setBestDateSort(null)}
                        style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }}
                        title="点击恢复按盈利金额排序"
                      >
                        当日盈利 (USD)
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {bestItems.length === 0 ? (
                      <tr>
                        <td colSpan={3} style={{ textAlign: "center", padding: "20px", color: "var(--text-muted)" }}>
                          暂无数据
                        </td>
                      </tr>
                    ) : (
                      bestItems.map((item: any, idx: number) => (
                        <tr key={idx}>
                          <td style={{ fontWeight: 600 }}>#{idx + 1}</td>
                          <td>{item.date || item.day || item.month}</td>
                          <td className="font-mono profit-text" style={{ textAlign: "right" }}>
                            +${Number(item.profit || item.value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Worst / Losers Column */}
            <div className="card-panel" style={{ display: "flex", flexDirection: "column", padding: "16px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "var(--loss-color)", fontWeight: 700, marginBottom: "12px" }}>
                <TrendingDown size={18} />
                <span>{isDay ? "最大回撤单日 Top 20 亏损日" : "最差月份 Top 20 亏损月"}</span>
              </div>

              <div className="table-container" style={{ border: "none" }}>
                <table className="finance-table">
                  <thead>
                    <tr>
                      <th>排名</th>
                      <th
                        onClick={() => setWorstDateSort((prev) => (prev === "desc" ? "asc" : "desc"))}
                        style={{ cursor: "pointer", userSelect: "none" }}
                        title="点击切换日期排序"
                      >
                        <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                          <span>{isDay ? "交易日期" : "月份"}</span>
                          {worstDateSort && (
                            <span style={{ fontSize: "12px", color: "var(--brand, #2563eb)", fontWeight: 700 }}>
                              {worstDateSort === "desc" ? "↓" : "↑"}
                            </span>
                          )}
                        </span>
                      </th>
                      <th
                        onClick={() => setWorstDateSort(null)}
                        style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }}
                        title="点击恢复按亏损金额排序"
                      >
                        当日亏损 (USD)
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {worstItems.length === 0 ? (
                      <tr>
                        <td colSpan={3} style={{ textAlign: "center", padding: "20px", color: "var(--text-muted)" }}>
                          暂无数据
                        </td>
                      </tr>
                    ) : (
                      worstItems.map((item: any, idx: number) => (
                        <tr key={idx}>
                          <td style={{ fontWeight: 600 }}>#{idx + 1}</td>
                          <td>{item.date || item.day || item.month}</td>
                          <td className="font-mono loss-text" style={{ textAlign: "right" }}>
                            -${Math.abs(Number(item.profit || item.value || 0)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </ErrorBoundary>
    );
  }

  // Ranking Leaderboard View
  if (mode === "ranking") {
    const items = data?.items || data?.ranking || [];
    return (
      <ErrorBoundary fallbackTitle="收益排行榜渲染异常">
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", overflowY: "auto" }}>
          <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "6px" }}>
            <Award size={18} color="var(--brand)" />
            <span>全时期已实现与持仓标的收益排行榜 (Leaderboard)</span>
          </div>

          <div className="table-container">
            <table className="finance-table">
              <thead>
                <tr>
                  <th>标的代码</th>
                  <th>标的名称</th>
                  <th style={{ textAlign: "right" }}>累计收益额 (USD)</th>
                  <th style={{ textAlign: "right" }}>收益率贡献</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={4} style={{ textAlign: "center", padding: "40px", color: "var(--text-muted)" }}>
                      暂无收益排行数据
                    </td>
                  </tr>
                ) : (
                  items.map((r: any, idx: number) => {
                    const profit = Number(r.profit || r.value || 0);
                    const isProf = profit >= 0;
                    return (
                      <tr key={idx}>
                        <td style={{ fontWeight: 700, color: "var(--text-heading)" }}>{r.symbol || r.name}</td>
                        <td>{r.title || r.symbol}</td>
                        <td className={`font-mono ${isProf ? "profit-text" : "loss-text"}`} style={{ textAlign: "right" }}>
                          {isProf ? "+" : ""}${profit.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right" }}>
                          {r.percentage ? `${r.percentage}%` : "—"}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </ErrorBoundary>
    );
  }

  // Visual Charts (filtered by mode)
  const allCharts = data?.charts || [];
  let displayedCharts = allCharts;
  let pageTitle = "27 维全景量化分析与走势图集";
  let pageIcon = <BarChart3 size={18} color="var(--brand)" />;

  if (mode === "asset") {
    pageTitle = "资产与净值增长曲线";
    pageIcon = <LineChart size={18} color="var(--brand)" />;
    displayedCharts = allCharts.filter((c: any) =>
      c.title.includes("资产") || c.title.includes("净值") || c.title.includes("累计") || c.title.includes("本金")
    );
    if (displayedCharts.length === 0) displayedCharts = allCharts.slice(6, 12);
  } else if (mode === "monthly") {
    pageTitle = "月度走势与收益详情";
    pageIcon = <BarChart3 size={18} color="var(--brand)" />;
    displayedCharts = allCharts.filter((c: any) =>
      c.title.includes("月") || c.title.includes("微操")
    );
    if (displayedCharts.length === 0) displayedCharts = allCharts.slice(6, 11);
  } else if (mode === "distribution") {
    pageTitle = "持仓与资产结构分布";
    pageIcon = <PieChart size={18} color="var(--brand)" />;
    displayedCharts = allCharts.filter((c: any) => c.kind === "pie");
    if (displayedCharts.length === 0) displayedCharts = allCharts.slice(0, 6);
  }

  const chart18 = allCharts.find((c: any) => c.kind === "capital_assets" || c.id === 18);
  let kpiData: { marketVal: number; netBuy: number; diff: number; ratio: number } | null = null;
  if (chart18 && chart18.series && chart18.series.length >= 4) {
    const s0 = chart18.series[0]?.points || [];
    const s2 = chart18.series[2]?.points || [];
    const s3 = chart18.series[3]?.points || [];
    if (s3.length > 0) {
      const lastIdx = s3.length - 1;
      const m = Number(s3[lastIdx]?.[1] ?? 0);
      const v0 = Number(s0[lastIdx]?.[1] ?? 0);
      const v2 = Number(s2[lastIdx]?.[1] ?? 0);
      const c = v2 > 0 ? v0 + v2 : v0;
      const diff = m - c;
      const ratio = c > 0 ? (diff / c) * 100 : 0;
      kpiData = { marketVal: m, netBuy: c, diff, ratio };
    }
  }

  return (
    <ErrorBoundary fallbackTitle="图表集渲染异常">
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "16px", overflowY: "auto" }}>
        <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-heading)", display: "flex", alignItems: "center", gap: "8px" }}>
          {pageIcon}
          <span>{pageTitle}</span>
        </div>

        {mode === "asset" && kpiData && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "16px" }}>
            <div className="card-panel" style={{ padding: "14px 18px", display: "flex", flexDirection: "column", gap: "6px" }}>
              <div style={{ fontSize: "13px", color: "var(--text-muted)", fontWeight: 500 }}>股票市值</div>
              <div className="font-mono" style={{ fontSize: "22px", fontWeight: 700, color: "var(--text-heading)" }}>
                ${kpiData.marketVal.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </div>
            </div>
            <div className="card-panel" style={{ padding: "14px 18px", display: "flex", flexDirection: "column", gap: "6px" }}>
              <div style={{ fontSize: "13px", color: "var(--text-muted)", fontWeight: 500 }}>累计净买入</div>
              <div className="font-mono" style={{ fontSize: "22px", fontWeight: 700, color: "var(--text-heading)" }}>
                ${kpiData.netBuy.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </div>
            </div>
            <div className="card-panel" style={{ padding: "14px 18px", display: "flex", flexDirection: "column", gap: "6px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "13px", color: "var(--text-muted)", fontWeight: 500 }}>市值差额</span>
                <span style={{
                  fontSize: "11px",
                  fontWeight: 600,
                  padding: "1px 6px",
                  borderRadius: "4px",
                  backgroundColor: kpiData.diff >= 0 ? "var(--profit-bg, #DCFCE7)" : "var(--loss-bg, #FEE2E2)",
                  color: kpiData.diff >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                }}>
                  {kpiData.diff >= 0 ? `↗ +${kpiData.ratio.toFixed(1)}%` : `↘ ${kpiData.ratio.toFixed(1)}%`}
                </span>
              </div>
              <div className="font-mono" style={{
                fontSize: "22px",
                fontWeight: 700,
                color: kpiData.diff >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
              }}>
                {kpiData.diff >= 0 ? "+" : ""}${kpiData.diff.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </div>
            </div>
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(460px, 1fr))", gap: "16px" }}>
          {displayedCharts.length === 0 ? (
            <div className="card-panel" style={{ height: "400px", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)" }}>
              <span>暂无对应图表数据</span>
            </div>
          ) : (
            displayedCharts.map((c: any, idx: number) => {
              const opt = convertToEchartsOption(c);
              return (
                <div key={idx} className="card-panel" style={{ height: "360px", display: "flex", flexDirection: "column", padding: "12px" }}>
                  <div style={{ flex: 1 }}>
                    <ReactECharts option={opt} style={{ height: "100%", width: "100%" }} notMerge={true} />
                  </div>
                  {c.note && (
                    <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px", textAlign: "right" }}>
                      {c.note}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>
    </ErrorBoundary>
  );
};
