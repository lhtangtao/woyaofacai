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

  useEffect(() => {
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
    const bestItems = (data?.best || data?.best_days || data?.best_months || data?.top_gainers || []).slice(0, 20);
    const worstItems = (data?.worst || data?.worst_days || data?.worst_months || data?.top_losers || []).slice(0, 20);

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
                      <th>{isDay ? "交易日期" : "月份"}</th>
                      <th style={{ textAlign: "right" }}>当日盈利 (USD)</th>
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
                      <th>{isDay ? "交易日期" : "月份"}</th>
                      <th style={{ textAlign: "right" }}>当日亏损 (USD)</th>
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

  return (
    <ErrorBoundary fallbackTitle="图表集渲染异常">
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "16px", overflowY: "auto" }}>
        <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-heading)", display: "flex", alignItems: "center", gap: "8px" }}>
          {pageIcon}
          <span>{pageTitle}</span>
        </div>

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
