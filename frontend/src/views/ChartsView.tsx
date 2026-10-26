import React, { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";
import { BarChart3, TrendingUp, TrendingDown, Award } from "lucide-react";
import { api } from "../api/client";

interface ChartsViewProps {
  mode: "gallery" | "top_days" | "top_months" | "ranking";
  personId: number | null;
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
    const bestItems = (data?.best || data?.top_gainers || []).slice(0, 20);
    const worstItems = (data?.worst || data?.top_losers || []).slice(0, 20);

    return (
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
                  {bestItems.map((item: any, idx: number) => (
                    <tr key={idx}>
                      <td style={{ fontWeight: 600 }}>#{idx + 1}</td>
                      <td>{item.date || item.day || item.month}</td>
                      <td className="font-mono profit-text" style={{ textAlign: "right" }}>
                        +${Number(item.profit || item.value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                    </tr>
                  ))}
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
                  {worstItems.map((item: any, idx: number) => (
                    <tr key={idx}>
                      <td style={{ fontWeight: 600 }}>#{idx + 1}</td>
                      <td>{item.date || item.day || item.month}</td>
                      <td className="font-mono loss-text" style={{ textAlign: "right" }}>
                        -${Math.abs(Number(item.profit || item.value || 0)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Ranking Leaderboard View
  if (mode === "ranking") {
    const items = data?.items || data?.ranking || [];
    return (
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
              {items.map((r: any, idx: number) => {
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
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // Gallery of 27 ECharts
  const charts = data?.charts || [];
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "16px", overflowY: "auto" }}>
      <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-heading)" }}>
        27 维全景量化分析与走势图集
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(480px, 1fr))", gap: "16px" }}>
        {charts.length === 0 ? (
          <div className="card-panel" style={{ height: "400px", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <span>暂无图表配置</span>
          </div>
        ) : (
          charts.map((c: any, idx: number) => (
            <div key={idx} className="card-panel" style={{ height: "360px", display: "flex", flexDirection: "column" }}>
              <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-heading)", marginBottom: "8px" }}>
                {c.title || `图表 ${idx + 1}`}
              </div>
              <div style={{ flex: 1 }}>
                <ReactECharts option={c.option || c} style={{ height: "100%", width: "100%" }} />
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
