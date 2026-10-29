import React, { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";
import { X, Download, TrendingUp, TrendingDown, ArrowUpRight, ArrowDownRight, Layers } from "lucide-react";
import { api } from "../api/client";

interface StockDrawerProps {
  ticker: string | null;
  personId?: number | null;
  broker?: string | null;
  onClose: () => void;
}

export const StockDrawer: React.FC<StockDrawerProps> = ({ ticker, personId, broker, onClose }) => {
  const [activeTab, setActiveTab] = useState<"trades" | "holdings" | "pnl" | "monthly">("trades");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Trade table sorting & pagination
  const [sortAsc, setSortAsc] = useState(false);
  const [page, setPage] = useState(1);
  const pageSize = 15;

  useEffect(() => {
    if (!ticker) {
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    setPage(1);
    api
      .stockDrawer(ticker, personId, broker || undefined)
      .then((res) => {
        if (res.status === "ok") {
          setData(res);
        } else {
          setError(res.message || "加载股票详情失败");
        }
      })
      .catch((err) => {
        setError(err.message || "请求异常");
      })
      .finally(() => setLoading(false));
  }, [ticker, personId, broker]);

  if (!ticker) return null;

  const summary = data?.summary || {};
  const trades: any[] = data?.trades || [];
  const holdingAnalysis = data?.holding_analysis || { dates: [], quantities: [], costs: [] };
  const pnlAnalysis = data?.pnl_analysis || {};
  const monthly = data?.monthly_performance || [];

  // Sort trades by trade_time
  const sortedTrades = [...trades].sort((a, b) => {
    const tA = String(a.trade_time || "");
    const tB = String(b.trade_time || "");
    return sortAsc ? tA.localeCompare(tB) : tB.localeCompare(tA);
  });

  const totalTrades = sortedTrades.length;
  const totalPages = Math.max(1, Math.ceil(totalTrades / pageSize));
  const currentTrades = sortedTrades.slice((page - 1) * pageSize, page * pageSize);

  const formatNumber = (num: number | null | undefined, digits = 2) => {
    if (num == null) return "—";
    return Number(num).toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  };

  const formatPercent = (num: number | null | undefined) => {
    if (num == null) return "—";
    const val = Number(num) * 100;
    const sign = val > 0 ? "+" : "";
    return `${sign}${val.toFixed(2)}%`;
  };

  const exportTradesCsv = () => {
    if (!sortedTrades.length) return;
    const headers = [
      "交易时间",
      "归属人员",
      "券商渠道",
      "交易方向",
      "成交数量",
      "当前价格",
      "成交价格",
      "成交金额",
      "手续费",
      "成交后持仓",
      "成本变化",
    ];
    const rows = sortedTrades.map((t) => [
      `"${t.trade_time || ""}"`,
      `"${t.person_name || ""}"`,
      `"${t.channel || ""}"`,
      `"${t.side || ""}"`,
      t.quantity ?? "",
      t.current_price ?? "",
      t.price ?? "",
      t.amount ?? "",
      t.fee ?? "",
      t.post_quantity ?? "",
      `"${t.cost_change || ""}"`,
    ]);
    const csvContent = "\uFEFF" + [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute("download", `${ticker}_交易记录_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  // ECharts: Holding Analysis (Quantity + Cost)
  const holdingChartOption = {
    tooltip: { trigger: "axis" },
    legend: { data: ["持仓数量", "成本价 (USD)"], bottom: 0 },
    grid: { left: "4%", right: "4%", top: "12%", bottom: "16%", containLabel: true },
    xAxis: {
      type: "category",
      data: holdingAnalysis.dates || [],
      axisLabel: { fontSize: 11 },
    },
    yAxis: [
      { type: "value", name: "股数", splitLine: { lineStyle: { type: "dashed" } } },
      { type: "value", name: "USD", splitLine: { show: false } },
    ],
    series: [
      {
        name: "持仓数量",
        type: "bar",
        data: holdingAnalysis.quantities || [],
        itemStyle: { color: "#3B82F6" },
        barMaxWidth: 24,
      },
      {
        name: "成本价 (USD)",
        type: "line",
        yAxisIndex: 1,
        data: holdingAnalysis.costs || [],
        itemStyle: { color: "#F59E0B" },
        lineStyle: { width: 2 },
        smooth: true,
      },
    ],
  };

  // ECharts: PnL Rate Analysis
  const pnlChartOption = {
    tooltip: { trigger: "axis" },
    legend: { data: ["累计收益率", "浮动收益率"], bottom: 0 },
    grid: { left: "4%", right: "4%", top: "12%", bottom: "16%", containLabel: true },
    xAxis: {
      type: "category",
      data: (pnlAnalysis.markers || []).map((m: any) => m.date),
      axisLabel: { fontSize: 11 },
    },
    yAxis: {
      type: "value",
      axisLabel: { formatter: "{value}%" },
      splitLine: { lineStyle: { type: "dashed" } },
    },
    series: [
      {
        name: "成交买卖点",
        type: "scatter",
        data: (pnlAnalysis.markers || []).map((m: any) => [m.date, Number(m.price || 0)]),
        itemStyle: {
          color: (param: any) => {
            const item = (pnlAnalysis.markers || [])[param.dataIndex];
            return item?.side?.includes("买") ? "var(--loss-color, #DC2626)" : "var(--profit-color, #10B981)";
          },
        },
      },
    ],
  };

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: "min(720px, 94vw)",
        background: "var(--bg-panel, #ffffff)",
        boxShadow: "-8px 0 24px rgba(0, 0, 0, 0.12)",
        zIndex: 999,
        display: "flex",
        flexDirection: "column",
        borderLeft: "1px solid var(--border-color, #e2e8f0)",
        animation: "drawerSlideIn 0.22s ease-out",
      }}
    >
      <style>{`
        @keyframes drawerSlideIn {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>

      {/* 1. Header */}
      <div
        style={{
          padding: "16px 20px",
          borderBottom: "1px solid var(--border-color, #e2e8f0)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "var(--table-alternate, #f8fafc)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <div
            style={{
              width: "36px",
              height: "36px",
              borderRadius: "8px",
              background: "#1E293B",
              color: "#fff",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 800,
              fontSize: "15px",
            }}
          >
            {ticker.slice(0, 2)}
          </div>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span style={{ fontSize: "18px", fontWeight: 800, color: "var(--text-heading, #0f172a)" }}>
                {ticker}
              </span>
              <span style={{ fontSize: "13px", color: "var(--text-muted, #64748b)" }}>
                {summary.company_name || `${ticker} Corporation`}
              </span>
            </div>
            <div style={{ display: "flex", gap: "6px", marginTop: "3px" }}>
              {(summary.tags || ["美股", "标的"]).map((tag: string, idx: number) => (
                <span
                  key={idx}
                  style={{
                    fontSize: "10px",
                    padding: "1px 6px",
                    borderRadius: "4px",
                    background: "rgba(59, 130, 246, 0.1)",
                    color: "var(--brand, #2563eb)",
                    fontWeight: 600,
                  }}
                >
                  {tag}
                </span>
              ))}
            </div>
          </div>
        </div>

        <button
          onClick={onClose}
          className="btn btn-quiet"
          style={{ width: "32px", height: "32px", borderRadius: "50%", padding: 0 }}
          title="关闭抽屉"
        >
          <X size={18} />
        </button>
      </div>

      {/* 2. Top Summary Metrics (6 Items) */}
      <div
        style={{
          padding: "14px 20px",
          borderBottom: "1px solid var(--border-color, #e2e8f0)",
          background: "#fff",
          display: "grid",
          gridTemplateColumns: "repeat(6, 1fr)",
          gap: "12px",
        }}
      >
        <div>
          <div style={{ fontSize: "11px", color: "var(--text-muted, #64748b)", marginBottom: "3px" }}>当前持仓</div>
          <div style={{ fontSize: "15px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>
            {formatNumber(summary.current_quantity, 0)} <span style={{ fontSize: "11px", fontWeight: 400 }}>股</span>
          </div>
        </div>
        <div>
          <div style={{ fontSize: "11px", color: "var(--text-muted, #64748b)", marginBottom: "3px" }}>持仓成本</div>
          <div style={{ fontSize: "15px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>
            ${formatNumber(summary.cost_price, 2)}
          </div>
        </div>
        <div>
          <div style={{ fontSize: "11px", color: "var(--text-muted, #64748b)", marginBottom: "3px" }}>最新价</div>
          <div style={{ fontSize: "15px", fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--brand, #2563eb)" }}>
            ${formatNumber(summary.current_price, 2)}
          </div>
        </div>
        <div>
          <div style={{ fontSize: "11px", color: "var(--text-muted, #64748b)", marginBottom: "3px" }}>持仓市值</div>
          <div style={{ fontSize: "15px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>
            ${formatNumber(summary.market_value, 2)}
          </div>
        </div>
        <div>
          <div style={{ fontSize: "11px", color: "var(--text-muted, #64748b)", marginBottom: "3px" }}>浮动盈亏</div>
          <div
            style={{
              fontSize: "14px",
              fontWeight: 700,
              fontFamily: "var(--font-mono)",
              color: (summary.floating_pnl || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
            }}
          >
            {(summary.floating_pnl || 0) >= 0 ? "+" : ""}${formatNumber(summary.floating_pnl, 2)}
            <span style={{ fontSize: "11px", marginLeft: "4px" }}>({formatPercent(summary.floating_pnl_rate)})</span>
          </div>
        </div>
        <div>
          <div style={{ fontSize: "11px", color: "var(--text-muted, #64748b)", marginBottom: "3px" }}>累计盈亏</div>
          <div
            style={{
              fontSize: "14px",
              fontWeight: 700,
              fontFamily: "var(--font-mono)",
              color: (summary.cumulative_pnl || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
            }}
          >
            {(summary.cumulative_pnl || 0) >= 0 ? "+" : ""}${formatNumber(summary.cumulative_pnl, 2)}
            <span style={{ fontSize: "11px", marginLeft: "4px" }}>({formatPercent(summary.cumulative_pnl_rate)})</span>
          </div>
        </div>
      </div>

      {/* 3. Navigation Tabs */}
      <div
        style={{
          display: "flex",
          borderBottom: "1px solid var(--border-color, #e2e8f0)",
          background: "var(--table-alternate, #f8fafc)",
          padding: "0 20px",
        }}
      >
        {[
          { key: "trades", label: "交易记录" },
          { key: "holdings", label: "持仓分析" },
          { key: "pnl", label: "收益分析" },
          { key: "monthly", label: "月度表现" },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key as any)}
            style={{
              padding: "10px 16px",
              border: "none",
              background: "transparent",
              fontSize: "13px",
              fontWeight: activeTab === tab.key ? 700 : 500,
              color: activeTab === tab.key ? "var(--brand, #2563eb)" : "var(--text-muted, #64748b)",
              borderBottom: activeTab === tab.key ? "2px solid var(--brand, #2563eb)" : "2px solid transparent",
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* 4. Tab Body */}
      <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px" }}>
        {loading && (
          <div style={{ textAlign: "center", padding: "40px", color: "var(--brand, #2563eb)", fontSize: "14px" }}>
            正在加载标的交易与收益数据...
          </div>
        )}

        {error && (
          <div style={{ padding: "16px", background: "#fee2e2", color: "#dc2626", borderRadius: "8px", fontSize: "13px" }}>
            {error}
          </div>
        )}

        {!loading && !error && (
          <>
            {/* Tab 1: 交易记录 (11 Columns) */}
            {activeTab === "trades" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
                    共找到 {totalTrades} 笔成交记录
                  </span>
                  <button onClick={exportTradesCsv} className="btn btn-quiet" style={{ fontSize: "12px", padding: "4px 10px" }}>
                    <Download size={13} style={{ marginRight: "4px" }} />
                    导出 CSV
                  </button>
                </div>

                <div style={{ overflowX: "auto", border: "1px solid var(--border-color, #e2e8f0)", borderRadius: "8px" }}>
                  <table className="finance-table" style={{ fontSize: "12px", minWidth: "960px", width: "100%" }}>
                    <thead>
                      <tr>
                        <th
                          style={{ cursor: "pointer", userSelect: "none" }}
                          onClick={() => {
                            setSortAsc(!sortAsc);
                            setPage(1);
                          }}
                        >
                          交易时间 {sortAsc ? "▲" : "▼"}
                        </th>
                        <th>归属人员</th>
                        <th>券商渠道</th>
                        <th>交易方向</th>
                        <th style={{ textAlign: "right" }}>成交数量</th>
                        <th style={{ textAlign: "right" }}>当前价格</th>
                        <th style={{ textAlign: "right" }}>成交价格</th>
                        <th style={{ textAlign: "right" }}>成交金额</th>
                        <th style={{ textAlign: "right" }}>手续费</th>
                        <th style={{ textAlign: "right" }}>成交后持仓</th>
                        <th>成本变化</th>
                      </tr>
                    </thead>
                    <tbody>
                      {currentTrades.map((t, idx) => {
                        const isBuy = t.side?.includes("买");
                        return (
                          <tr key={idx}>
                            <td className="font-mono">{t.trade_time}</td>
                            <td style={{ fontWeight: 600 }}>{t.person_name}</td>
                            <td>{t.channel}</td>
                            <td>
                              <span
                                className={`badge ${isBuy ? "badge-buy" : "badge-sell"}`}
                                style={{ fontSize: "11px", padding: "2px 6px" }}
                              >
                                {t.side}
                              </span>
                            </td>
                            <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                              {formatNumber(t.quantity, 0)}
                            </td>
                            <td className="font-mono" style={{ textAlign: "right", color: "var(--brand, #2563eb)" }}>
                              {t.current_price != null ? `$${formatNumber(t.current_price, 2)}` : "—"}
                            </td>
                            <td className="font-mono" style={{ textAlign: "right" }}>
                              ${formatNumber(t.price, 2)}
                            </td>
                            <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                              ${formatNumber(t.amount, 2)}
                            </td>
                            <td className="font-mono" style={{ textAlign: "right", color: "var(--text-muted)" }}>
                              ${formatNumber(t.fee, 2)}
                            </td>
                            <td className="font-mono" style={{ textAlign: "right", fontWeight: 700 }}>
                              {formatNumber(t.post_quantity, 0)}
                            </td>
                            <td>
                              <span
                                style={{
                                  fontSize: "11px",
                                  fontWeight: 600,
                                  color:
                                    t.cost_change?.includes("降低")
                                      ? "var(--profit-color, #10B981)"
                                      : t.cost_change?.includes("提高")
                                      ? "var(--loss-color, #DC2626)"
                                      : "var(--text-muted)",
                                }}
                              >
                                {t.cost_change}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                      {!currentTrades.length && (
                        <tr>
                          <td colSpan={11} style={{ textAlign: "center", padding: "24px", color: "var(--text-muted)" }}>
                            暂无符合条件的交易记录
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>

                {/* Pagination */}
                {totalPages > 1 && (
                  <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: "8px", marginTop: "8px" }}>
                    <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                      共 {totalTrades} 条，第 {page} / {totalPages} 页
                    </span>
                    <button
                      className="btn"
                      disabled={page <= 1}
                      onClick={() => setPage(page - 1)}
                      style={{ padding: "4px 8px", fontSize: "12px" }}
                    >
                      上一页
                    </button>
                    <button
                      className="btn"
                      disabled={page >= totalPages}
                      onClick={() => setPage(page + 1)}
                      style={{ padding: "4px 8px", fontSize: "12px" }}
                    >
                      下一页
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Tab 2: 持仓分析 */}
            {activeTab === "holdings" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                <div style={{ fontSize: "14px", fontWeight: 700 }}>持仓数量与成本价变化</div>
                <div style={{ height: "360px", background: "#fff", borderRadius: "8px", border: "1px solid var(--border-color)" }}>
                  <ReactECharts option={holdingChartOption} style={{ height: "100%", width: "100%" }} />
                </div>
              </div>
            )}

            {/* Tab 3: 收益分析 */}
            {activeTab === "pnl" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px" }}>
                  <div className="card-panel" style={{ padding: "12px" }}>
                    <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>浮动盈亏 (USD)</div>
                    <div style={{ fontSize: "16px", fontWeight: 700, marginTop: "4px" }}>
                      ${formatNumber(pnlAnalysis.floating_pnl, 2)}
                    </div>
                  </div>
                  <div className="card-panel" style={{ padding: "12px" }}>
                    <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>已实现盈亏 (USD)</div>
                    <div style={{ fontSize: "16px", fontWeight: 700, marginTop: "4px" }}>
                      ${formatNumber(pnlAnalysis.realized_pnl, 2)}
                    </div>
                  </div>
                  <div className="card-panel" style={{ padding: "12px" }}>
                    <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>累计盈亏 (USD)</div>
                    <div style={{ fontSize: "16px", fontWeight: 700, marginTop: "4px" }}>
                      ${formatNumber(pnlAnalysis.cumulative_pnl, 2)}
                    </div>
                  </div>
                </div>

                <div style={{ fontSize: "14px", fontWeight: 700 }}>交易买卖点与收益走势</div>
                <div style={{ height: "320px", background: "#fff", borderRadius: "8px", border: "1px solid var(--border-color)" }}>
                  <ReactECharts option={pnlChartOption} style={{ height: "100%", width: "100%" }} />
                </div>
              </div>
            )}

            {/* Tab 4: 月度表现 */}
            {activeTab === "monthly" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                <div style={{ fontSize: "14px", fontWeight: 700 }}>按月展示持仓市值、盈亏和收益率</div>
                <div style={{ border: "1px solid var(--border-color, #e2e8f0)", borderRadius: "8px", overflow: "hidden" }}>
                  <table className="finance-table" style={{ fontSize: "13px", width: "100%" }}>
                    <thead>
                      <tr>
                        <th>月份</th>
                        <th style={{ textAlign: "right" }}>月末市值 (USD)</th>
                        <th style={{ textAlign: "right" }}>月度盈亏 (USD)</th>
                        <th style={{ textAlign: "right" }}>收益率</th>
                      </tr>
                    </thead>
                    <tbody>
                      {monthly.map((m: any, idx: number) => {
                        const pnl = Number(m.monthly_pnl || 0);
                        return (
                          <tr key={idx}>
                            <td className="font-mono" style={{ fontWeight: 600 }}>{m.month}</td>
                            <td className="font-mono" style={{ textAlign: "right" }}>
                              ${formatNumber(m.market_value, 2)}
                            </td>
                            <td
                              className="font-mono"
                              style={{
                                textAlign: "right",
                                fontWeight: 700,
                                color: pnl >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                              }}
                            >
                              {pnl >= 0 ? "+" : ""}${formatNumber(pnl, 2)}
                            </td>
                            <td
                              className="font-mono"
                              style={{
                                textAlign: "right",
                                fontWeight: 600,
                                color: (m.return_rate || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                              }}
                            >
                              {formatPercent(m.return_rate)}
                            </td>
                          </tr>
                        );
                      })}
                      {!monthly.length && (
                        <tr>
                          <td colSpan={4} style={{ textAlign: "center", padding: "24px", color: "var(--text-muted)" }}>
                            暂无月度历史收益记录
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
