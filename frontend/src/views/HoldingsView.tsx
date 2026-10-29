import React, { useEffect, useState } from "react";
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
import { StockDrawer } from "../components/StockDrawer";

interface HoldingsViewProps {
  personId?: number | null;
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
  initialDimension = "person",
  initialStatus = "全部",
  initialMode = "dashboard",
  initialFilter = {},
}) => {
  // Mode: "dashboard" (仪表板总览) or "detail" (统一持仓明细页)
  const [viewMode, setViewMode] = useState<"dashboard" | "detail">(initialMode);
  const [dimension, setDimension] = useState<"person" | "broker" | "stock">(initialDimension);

  // Filters
  const [statusFilter, setStatusFilter] = useState<string>(initialStatus);
  const [selectedPersonId, setSelectedPersonId] = useState<number | null>(
    personId !== undefined ? personId : initialFilter.person_id ?? null
  );
  const [selectedBroker, setSelectedBroker] = useState<string>(initialFilter.broker || "");
  const [selectedTicker, setSelectedTicker] = useState<string>(initialFilter.ticker || "");
  const [searchKeyword, setSearchKeyword] = useState<string>("");

  // Dashboard Data State
  const [dashboardData, setDashboardData] = useState<any>(null);
  const [dashLoading, setDashLoading] = useState(false);
  const [distTab, setDistTab] = useState<"by_person" | "by_broker" | "by_stock">("by_person");

  // Detail Data State
  const [detailRows, setDetailRows] = useState<any[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailPage, setDetailPage] = useState(1);
  const [detailPageSize] = useState(25);
  const [sortField, setSortField] = useState<string>("market_value");
  const [sortAsc, setSortAsc] = useState<boolean>(false);

  // Stock Drawer State (Right slide-out)
  const [drawerTicker, setDrawerTicker] = useState<string | null>(null);

  // Sync external personId changes
  useEffect(() => {
    if (personId !== undefined) {
      setSelectedPersonId(personId);
    }
  }, [personId]);

  // Load Dashboard Data
  const loadDashboard = () => {
    setDashLoading(true);
    api
      .holdingsDashboard(selectedPersonId)
      .then((res) => {
        if (res.status === "ok") {
          setDashboardData(res);
        }
      })
      .catch((err) => console.error("Holdings dashboard load error:", err))
      .finally(() => setDashLoading(false));
  };

  // Load Detail Rows
  const loadDetail = () => {
    setDetailLoading(true);
    api
      .holdingsDetail({
        dimension,
        person_id: selectedPersonId,
        broker: selectedBroker || undefined,
        ticker: selectedTicker || undefined,
        status: statusFilter !== "全部" ? statusFilter : undefined,
        search: searchKeyword || undefined,
        page: 1,
        page_size: 500, // Load all matching for client-side sorting/pagination
      })
      .then((res) => {
        if (res.status === "ok") {
          setDetailRows(res.rows || []);
          setDetailPage(1);
        }
      })
      .catch((err) => console.error("Holdings detail load error:", err))
      .finally(() => setDetailLoading(false));
  };

  useEffect(() => {
    loadDashboard();
    loadDetail();
  }, [selectedPersonId, dimension, statusFilter, selectedBroker, selectedTicker, searchKeyword]);

  const cards = dashboardData?.cards || {};
  const personSummary: any[] = dashboardData?.person_summary || [];
  const brokerSummary: any[] = dashboardData?.broker_summary || [];
  const stockSummary: any[] = dashboardData?.stock_summary || [];
  const distData = dashboardData?.distribution?.[distTab] || [];

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

  // Navigate from dashboard to detail with preset
  const drillToPerson = (pId: number) => {
    setSelectedPersonId(pId);
    setDimension("person");
    setViewMode("detail");
  };

  const drillToBroker = (brokerName: string) => {
    setSelectedBroker(brokerName);
    setDimension("broker");
    setViewMode("detail");
  };

  const drillToStock = (tk: string) => {
    setSelectedTicker(tk);
    setDimension("stock");
    setViewMode("detail");
  };

  // Sorted and paged detail rows
  const sortedDetailRows = [...detailRows].sort((a, b) => {
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

  const totalDetailCount = sortedDetailRows.length;
  const totalDetailPages = Math.max(1, Math.ceil(totalDetailCount / detailPageSize));
  const currentDetailRows = sortedDetailRows.slice((detailPage - 1) * detailPageSize, detailPage * detailPageSize);

  // Donut ECharts Options
  const donutOption = {
    tooltip: { trigger: "item", formatter: "{b}: ${c} ({d}%)" },
    legend: { show: false },
    series: [
      {
        name: "资产分布",
        type: "pie",
        radius: ["55%", "75%"],
        center: ["50%", "50%"],
        avoidLabelOverlap: false,
        label: { show: false },
        emphasis: {
          label: {
            show: true,
            fontSize: 13,
            fontWeight: "bold",
          },
        },
        data: distData.map((d: any) => ({
          name: d.name,
          value: d.value,
        })),
      },
    ],
  };

  // Export detail rows to CSV
  const exportDetailCsv = () => {
    if (!sortedDetailRows.length) return;
    const headers = [
      "归属人员",
      "券商渠道",
      "Ticker",
      "持仓状态",
      "持仓数量",
      "持仓成本价",
      "当前最新价",
      "持仓市值",
      "浮动盈亏",
      "累计盈亏",
      "收益率",
      "持仓占比",
    ];
    const csvRows = sortedDetailRows.map((r) => [
      `"${r.person_name || ""}"`,
      `"${r.channel || ""}"`,
      `"${r.ticker || ""}"`,
      `"${r.status || ""}"`,
      r.quantity ?? "",
      r.cost_price ?? "",
      r.current_price ?? "",
      r.market_value ?? "",
      r.pnl ?? "",
      r.pnl ?? "",
      r.pnl_rate ?? "",
      r.weight ?? "",
    ]);
    const csvContent = "\uFEFF" + [headers.join(","), ...csvRows.map((r) => r.join(","))].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute("download", `持仓明细_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "16px", overflowY: "auto", position: "relative" }}>
      {/* Top Controls Bar */}
      <div
        className="card-panel"
        style={{
          padding: "12px 18px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "12px",
        }}
      >
        {/* Left: View Mode Toggle & Dimension Tabs */}
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          {/* Main Mode Toggle */}
          <div
            style={{
              display: "flex",
              background: "var(--bg-app, #f1f5f9)",
              padding: "3px",
              borderRadius: "8px",
              border: "1px solid var(--border-color, #e2e8f0)",
            }}
          >
            <button
              onClick={() => setViewMode("dashboard")}
              style={{
                padding: "6px 14px",
                borderRadius: "6px",
                border: "none",
                background: viewMode === "dashboard" ? "var(--brand, #2563eb)" : "transparent",
                color: viewMode === "dashboard" ? "#fff" : "var(--text-muted, #64748b)",
                fontWeight: viewMode === "dashboard" ? 700 : 500,
                fontSize: "13px",
                cursor: "pointer",
              }}
            >
              仪表板总览
            </button>
            <button
              onClick={() => setViewMode("detail")}
              style={{
                padding: "6px 14px",
                borderRadius: "6px",
                border: "none",
                background: viewMode === "detail" ? "var(--brand, #2563eb)" : "transparent",
                color: viewMode === "detail" ? "#fff" : "var(--text-muted, #64748b)",
                fontWeight: viewMode === "detail" ? 700 : 500,
                fontSize: "13px",
                cursor: "pointer",
              }}
            >
              持仓明细页
            </button>
          </div>

          <div style={{ width: "1px", height: "20px", background: "var(--border-color, #e2e8f0)" }} />

          {/* Three Dimension Buttons */}
          <div style={{ display: "flex", gap: "6px" }}>
            {[
              { key: "person", label: "按人员查看", icon: <Users size={14} /> },
              { key: "broker", label: "按券商查看", icon: <Building2 size={14} /> },
              { key: "stock", label: "按股票查看", icon: <Layers size={14} /> },
            ].map((d) => (
              <button
                key={d.key}
                onClick={() => {
                  setDimension(d.key as any);
                  if (viewMode === "dashboard") setViewMode("detail");
                }}
                className={`btn ${dimension === d.key ? "btn-primary" : "btn-quiet"}`}
                style={{
                  fontSize: "12px",
                  padding: "5px 12px",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  borderRadius: "6px",
                }}
              >
                {d.icon}
                {d.label}
              </button>
            ))}
          </div>
        </div>

        {/* Right: Search, Filter, Export */}
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          {/* Status Dropdown */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="input-control"
            style={{ fontSize: "12px", padding: "5px 10px" }}
          >
            <option value="全部">全部状态</option>
            <option value="持有中">当前持仓 (持有中)</option>
            <option value="已清仓">已清仓记录</option>
          </select>

          {/* Broker Filter */}
          <select
            value={selectedBroker}
            onChange={(e) => setSelectedBroker(e.target.value)}
            className="input-control"
            style={{ fontSize: "12px", padding: "5px 10px" }}
          >
            <option value="">全部渠道</option>
            <option value="长桥">长桥</option>
            <option value="BBAE">BBAE</option>
            <option value="第一证券">第一证券</option>
            <option value="嘉信">嘉信</option>
          </select>

          {/* Search Input */}
          <div style={{ position: "relative" }}>
            <Search size={14} style={{ position: "absolute", left: "8px", top: "8px", color: "var(--text-muted)" }} />
            <input
              type="text"
              placeholder="搜索人员、券商或股票代码..."
              value={searchKeyword}
              onChange={(e) => setSearchKeyword(e.target.value)}
              className="input-control"
              style={{ paddingLeft: "28px", fontSize: "12px", width: "190px" }}
            />
          </div>

          {viewMode === "detail" && (
            <button onClick={exportDetailCsv} className="btn btn-quiet" style={{ fontSize: "12px", padding: "5px 10px" }}>
              <Download size={14} style={{ marginRight: "4px" }} />
              导出
            </button>
          )}
        </div>
      </div>

      {/* =========================================================================
          VIEW 1: 主页仪表板 (Dashboard)
          ========================================================================= */}
      {viewMode === "dashboard" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {/* Top 4 Summary Cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "14px" }}>
            {/* Card 1: 持仓总市值 */}
            <div className="card-panel" style={{ padding: "18px 20px" }}>
              <div style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", fontWeight: 500 }}>持仓总市值 (USD)</div>
              <div style={{ fontSize: "24px", fontWeight: 800, marginTop: "6px", fontFamily: "var(--font-mono)" }}>
                ${formatNumber(cards.total_market_value, 2)}
              </div>
              <div style={{ marginTop: "6px", fontSize: "12px", display: "flex", alignItems: "center", gap: "6px" }}>
                <span
                  style={{
                    color: (cards.floating_pnl || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                    fontWeight: 700,
                  }}
                >
                  {(cards.floating_pnl || 0) >= 0 ? "+" : ""}${formatNumber(cards.floating_pnl, 2)}
                </span>
                <span
                  style={{
                    color: (cards.floating_pnl_rate || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                    fontWeight: 600,
                  }}
                >
                  {formatPercent(cards.floating_pnl_rate)}
                </span>
              </div>
            </div>

            {/* Card 2: 持仓股票 */}
            <div className="card-panel" style={{ padding: "18px 20px" }}>
              <div style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", fontWeight: 500 }}>持仓股票</div>
              <div style={{ fontSize: "24px", fontWeight: 800, marginTop: "6px", fontFamily: "var(--font-mono)" }}>
                {cards.total_stock_count ?? "—"}
              </div>
              <div style={{ marginTop: "6px", fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
                当前持仓 <strong style={{ color: "var(--text-heading)" }}>{cards.open_stock_count ?? 0}</strong> | 已清仓{" "}
                <strong style={{ color: "var(--text-heading)" }}>{cards.closed_stock_count ?? 0}</strong>
              </div>
            </div>

            {/* Card 3: 累计盈亏 (含已清仓) */}
            <div className="card-panel" style={{ padding: "18px 20px" }}>
              <div style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", fontWeight: 500 }}>累计盈亏 (含已清仓)</div>
              <div
                style={{
                  fontSize: "24px",
                  fontWeight: 800,
                  marginTop: "6px",
                  fontFamily: "var(--font-mono)",
                  color: (cards.cumulative_pnl || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                }}
              >
                {(cards.cumulative_pnl || 0) >= 0 ? "+" : ""}${formatNumber(cards.cumulative_pnl, 2)}
              </div>
              <div style={{ marginTop: "6px", fontSize: "12px", fontWeight: 600 }}>
                <span
                  style={{
                    color: (cards.cumulative_pnl_rate || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                  }}
                >
                  {formatPercent(cards.cumulative_pnl_rate)}
                </span>
              </div>
            </div>

            {/* Card 4: 总投入 */}
            <div className="card-panel" style={{ padding: "18px 20px" }}>
              <div style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", fontWeight: 500 }}>总投入 (USD)</div>
              <div style={{ fontSize: "24px", fontWeight: 800, marginTop: "6px", fontFamily: "var(--font-mono)" }}>
                ${formatNumber(cards.total_invested, 2)}
              </div>
              <div style={{ marginTop: "6px", fontSize: "11px", color: "var(--text-muted, #64748b)" }}>
                已实现 ${(formatNumber(cards.realized_pnl, 2))} | 浮动 ${(formatNumber(cards.floating_pnl, 2))}
              </div>
            </div>
          </div>

          {/* Bottom Grid: Tables on Left, Donut on Right */}
          <div style={{ display: "grid", gridTemplateColumns: "1.65fr 1fr", gap: "16px" }}>
            {/* Left Column: Summary Tables */}
            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              {/* 人员持仓汇总 */}
              <div className="card-panel" style={{ padding: "16px 20px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
                  <div style={{ fontSize: "15px", fontWeight: 700, display: "flex", alignItems: "center", gap: "6px" }}>
                    <Users size={16} color="var(--brand)" />
                    人员持仓明细
                  </div>
                  <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>点击人员直达专属持仓明细</span>
                </div>

                <div className="table-container">
                  <table className="finance-table" style={{ fontSize: "12px" }}>
                    <thead>
                      <tr>
                        <th>人员</th>
                        <th style={{ textAlign: "right" }}>持仓市值 (USD)</th>
                        <th style={{ textAlign: "right" }}>累计盈亏 (USD)</th>
                        <th style={{ textAlign: "right" }}>收益率</th>
                        <th style={{ textAlign: "right" }}>持仓股票</th>
                        <th style={{ textAlign: "right" }}>当前持仓</th>
                        <th style={{ textAlign: "right" }}>已清仓</th>
                        <th style={{ textAlign: "center" }}>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {personSummary.map((p, idx) => (
                        <tr key={idx} style={{ cursor: "pointer" }} onClick={() => drillToPerson(p.person_id)}>
                          <td style={{ fontWeight: 700, color: "var(--brand)" }}>{p.name}</td>
                          <td className="font-mono" style={{ textAlign: "right" }}>
                            ${formatNumber(p.market_value, 2)}
                          </td>
                          <td
                            className="font-mono"
                            style={{
                              textAlign: "right",
                              fontWeight: 700,
                              color: (p.total_pnl || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                            }}
                          >
                            {(p.total_pnl || 0) >= 0 ? "+" : ""}${formatNumber(p.total_pnl, 2)}
                          </td>
                          <td
                            className="font-mono"
                            style={{
                              textAlign: "right",
                              fontWeight: 600,
                              color: (p.pnl_rate || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                            }}
                          >
                            {formatPercent(p.pnl_rate)}
                          </td>
                          <td className="font-mono" style={{ textAlign: "right" }}>{p.total_stocks}</td>
                          <td className="font-mono" style={{ textAlign: "right" }}>{p.open_stocks}</td>
                          <td className="font-mono" style={{ textAlign: "right" }}>{p.closed_stocks}</td>
                          <td style={{ textAlign: "center" }}>
                            <span style={{ fontSize: "11px", color: "var(--brand)", display: "flex", alignItems: "center", justifyContent: "center", gap: "2px" }}>
                              查看明细 <ArrowRight size={11} />
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* 券商持仓汇总 & 股票持仓汇总 (Side-by-side) */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
                {/* 券商持仓汇总 */}
                <div className="card-panel" style={{ padding: "16px 18px" }}>
                  <div style={{ fontSize: "14px", fontWeight: 700, marginBottom: "10px", display: "flex", alignItems: "center", gap: "6px" }}>
                    <Building2 size={15} color="var(--brand)" />
                    券商持仓汇总
                  </div>
                  <div className="table-container">
                    <table className="finance-table" style={{ fontSize: "11px" }}>
                      <thead>
                        <tr>
                          <th>券商</th>
                          <th style={{ textAlign: "right" }}>持仓市值</th>
                          <th style={{ textAlign: "right" }}>累计盈亏</th>
                          <th style={{ textAlign: "right" }}>收益率</th>
                        </tr>
                      </thead>
                      <tbody>
                        {brokerSummary.map((b, idx) => (
                          <tr key={idx} style={{ cursor: "pointer" }} onClick={() => drillToBroker(b.channel)}>
                            <td style={{ fontWeight: 600 }}>{b.channel}</td>
                            <td className="font-mono" style={{ textAlign: "right" }}>${formatNumber(b.market_value, 2)}</td>
                            <td
                              className="font-mono"
                              style={{
                                textAlign: "right",
                                fontWeight: 700,
                                color: (b.total_pnl || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                              }}
                            >
                              {(b.total_pnl || 0) >= 0 ? "+" : ""}${formatNumber(b.total_pnl, 2)}
                            </td>
                            <td
                              className="font-mono"
                              style={{
                                textAlign: "right",
                                color: (b.pnl_rate || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                              }}
                            >
                              {formatPercent(b.pnl_rate)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* 股票持仓汇总 */}
                <div className="card-panel" style={{ padding: "16px 18px" }}>
                  <div style={{ fontSize: "14px", fontWeight: 700, marginBottom: "10px", display: "flex", alignItems: "center", gap: "6px" }}>
                    <Layers size={15} color="var(--brand)" />
                    股票持仓汇总
                  </div>
                  <div className="table-container">
                    <table className="finance-table" style={{ fontSize: "11px" }}>
                      <thead>
                        <tr>
                          <th>Ticker</th>
                          <th style={{ textAlign: "right" }}>持仓市值</th>
                          <th style={{ textAlign: "right" }}>累计盈亏</th>
                          <th style={{ textAlign: "right" }}>收益率</th>
                        </tr>
                      </thead>
                      <tbody>
                        {stockSummary.slice(0, 7).map((s, idx) => (
                          <tr
                            key={idx}
                            style={{ cursor: "pointer" }}
                            onClick={() => {
                              setDrawerTicker(s.ticker);
                            }}
                          >
                            <td style={{ fontWeight: 700, color: "var(--brand)" }}>{s.ticker}</td>
                            <td className="font-mono" style={{ textAlign: "right" }}>${formatNumber(s.market_value, 2)}</td>
                            <td
                              className="font-mono"
                              style={{
                                textAlign: "right",
                                fontWeight: 700,
                                color: (s.total_pnl || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                              }}
                            >
                              {(s.total_pnl || 0) >= 0 ? "+" : ""}${formatNumber(s.total_pnl, 2)}
                            </td>
                            <td
                              className="font-mono"
                              style={{
                                textAlign: "right",
                                color: (s.pnl_rate || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                              }}
                            >
                              {formatPercent(s.pnl_rate)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>

            {/* Right Column: 资产分布图 (Donut) */}
            <div className="card-panel" style={{ padding: "18px 20px", display: "flex", flexDirection: "column" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px" }}>
                <div style={{ fontSize: "15px", fontWeight: 700, display: "flex", alignItems: "center", gap: "6px" }}>
                  <PieIcon size={16} color="var(--brand)" />
                  资产分布
                </div>

                {/* Donut Tabs */}
                <div
                  style={{
                    display: "flex",
                    background: "var(--bg-app, #f1f5f9)",
                    padding: "2px",
                    borderRadius: "6px",
                  }}
                >
                  {[
                    { key: "by_person", label: "按人员" },
                    { key: "by_broker", label: "按券商" },
                    { key: "by_stock", label: "按股票" },
                  ].map((t) => (
                    <button
                      key={t.key}
                      onClick={() => setDistTab(t.key as any)}
                      style={{
                        padding: "3px 10px",
                        borderRadius: "4px",
                        border: "none",
                        fontSize: "11px",
                        background: distTab === t.key ? "var(--brand, #2563eb)" : "transparent",
                        color: distTab === t.key ? "#fff" : "var(--text-muted)",
                        fontWeight: distTab === t.key ? 700 : 500,
                        cursor: "pointer",
                      }}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Donut Chart */}
              <div style={{ height: "230px", position: "relative" }}>
                <ReactECharts option={donutOption} style={{ height: "100%", width: "100%" }} />
                {/* Center text */}
                <div
                  style={{
                    position: "absolute",
                    top: "50%",
                    left: "50%",
                    transform: "translate(-50%, -50%)",
                    textAlign: "center",
                    pointerEvents: "none",
                  }}
                >
                  <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>总持仓市值</div>
                  <div style={{ fontSize: "16px", fontWeight: 800, fontFamily: "var(--font-mono)" }}>
                    ${formatNumber(cards.total_market_value, 0)}
                  </div>
                </div>
              </div>

              {/* Donut Legend Items */}
              <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "12px", overflowY: "auto", maxHeight: "260px" }}>
                {distData.map((d: any, idx: number) => (
                  <div key={idx} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "12px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span
                        style={{
                          width: "8px",
                          height: "8px",
                          borderRadius: "50%",
                          background: ["#3B82F6", "#10B981", "#F59E0B", "#8B5CF6", "#EC4899", "#6366F1"][idx % 6],
                        }}
                      />
                      <span style={{ fontWeight: 600 }}>{d.name}</span>
                    </div>
                    <div style={{ display: "flex", gap: "10px", fontFamily: "var(--font-mono)" }}>
                      <span>${formatNumber(d.value, 2)}</span>
                      <span style={{ color: "var(--text-muted)", width: "42px", textAlign: "right" }}>{d.percent}%</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* =========================================================================
          VIEW 2: 统一持仓明细页 (Unified Detail Page)
          ========================================================================= */}
      {viewMode === "detail" && (
        <div className="card-panel" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: "12px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontSize: "15px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
              <Layers size={16} color="var(--brand)" />
              <span>
                统一持仓明细 ·{" "}
                {dimension === "person" ? "人员维度" : dimension === "broker" ? "券商维度" : "股票维度"}
              </span>
              <span style={{ fontSize: "12px", color: "var(--text-muted)", fontWeight: 400 }}>
                （共 {totalDetailCount} 条记录）
              </span>
            </div>
            <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>点击任意股票行右侧滑出深度分析抽屉</span>
          </div>

          {/* Unified Detail Table */}
          <div className="table-container" style={{ border: "1px solid var(--border-color, #e2e8f0)", borderRadius: "8px" }}>
            <table className="finance-table" style={{ fontSize: "12px" }}>
              <thead>
                <tr>
                  <th onClick={() => { setSortField("person_name"); setSortAsc(!sortAsc); }} style={{ cursor: "pointer" }}>
                    归属人员 {sortField === "person_name" ? (sortAsc ? "▲" : "▼") : ""}
                  </th>
                  <th onClick={() => { setSortField("channel"); setSortAsc(!sortAsc); }} style={{ cursor: "pointer" }}>
                    券商渠道 {sortField === "channel" ? (sortAsc ? "▲" : "▼") : ""}
                  </th>
                  <th onClick={() => { setSortField("ticker"); setSortAsc(!sortAsc); }} style={{ cursor: "pointer" }}>
                    Ticker {sortField === "ticker" ? (sortAsc ? "▲" : "▼") : ""}
                  </th>
                  <th>持仓状态</th>
                  <th style={{ textAlign: "right" }} onClick={() => { setSortField("quantity"); setSortAsc(!sortAsc); }}>
                    持仓数量 {sortField === "quantity" ? (sortAsc ? "▲" : "▼") : ""}
                  </th>
                  <th style={{ textAlign: "right" }}>持仓成本价</th>
                  <th style={{ textAlign: "right" }}>当前最新价</th>
                  <th style={{ textAlign: "right" }} onClick={() => { setSortField("market_value"); setSortAsc(!sortAsc); }}>
                    持仓市值 (USD) {sortField === "market_value" ? (sortAsc ? "▲" : "▼") : ""}
                  </th>
                  <th style={{ textAlign: "right" }} onClick={() => { setSortField("pnl"); setSortAsc(!sortAsc); }}>
                    浮动盈亏 {sortField === "pnl" ? (sortAsc ? "▲" : "▼") : ""}
                  </th>
                  <th style={{ textAlign: "right" }}>收益率</th>
                  <th style={{ textAlign: "right" }}>持仓占比</th>
                  <th style={{ textAlign: "center" }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {detailLoading ? (
                  <tr>
                    <td colSpan={12} style={{ textAlign: "center", padding: "30px", color: "var(--brand)" }}>
                      正在加载持仓明细...
                    </td>
                  </tr>
                ) : currentDetailRows.map((r, idx) => {
                  const isOpen = r.status === "持有中";
                  const pnl = Number(r.pnl || 0);
                  return (
                    <tr
                      key={idx}
                      style={{ cursor: "pointer" }}
                      onClick={() => setDrawerTicker(r.ticker)}
                      title="点击从右侧打开该标的深度交易与收益抽屉"
                    >
                      <td style={{ fontWeight: 600 }}>{r.person_name}</td>
                      <td>{r.channel}</td>
                      <td style={{ fontWeight: 700, color: "var(--brand)" }}>{r.ticker}</td>
                      <td>
                        <span
                          className={`badge ${isOpen ? "badge-buy" : "badge-sell"}`}
                          style={{ fontSize: "11px", padding: "2px 6px" }}
                        >
                          {r.status}
                        </span>
                      </td>
                      <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                        {formatNumber(r.quantity, 0)}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        ${formatNumber(r.cost_price, 2)}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right", color: "var(--brand)" }}>
                        {r.current_price != null ? `$${formatNumber(r.current_price, 2)}` : "—"}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right", fontWeight: 700 }}>
                        ${formatNumber(r.market_value, 2)}
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
                          color: (r.pnl_rate || 0) >= 0 ? "var(--profit-color, #10B981)" : "var(--loss-color, #DC2626)",
                        }}
                      >
                        {formatPercent(r.pnl_rate)}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right", color: "var(--text-muted)" }}>
                        {formatPercent(r.weight)}
                      </td>
                      <td style={{ textAlign: "center" }}>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setDrawerTicker(r.ticker);
                          }}
                          className="btn btn-quiet"
                          style={{ fontSize: "11px", padding: "2px 8px" }}
                        >
                          <Eye size={12} style={{ marginRight: "3px" }} />
                          详情
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {!detailLoading && !currentDetailRows.length && (
                  <tr>
                    <td colSpan={12} style={{ textAlign: "center", padding: "30px", color: "var(--text-muted)" }}>
                      没有符合当前筛选条件的持仓明细记录
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Detail Pagination */}
          {totalDetailPages > 1 && (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "8px" }}>
              <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                共 {totalDetailCount} 条记录，第 {detailPage} / {totalDetailPages} 页
              </span>
              <div style={{ display: "flex", gap: "6px" }}>
                <button
                  className="btn"
                  disabled={detailPage <= 1}
                  onClick={() => setDetailPage(detailPage - 1)}
                  style={{ fontSize: "12px", padding: "4px 10px" }}
                >
                  上一页
                </button>
                <button
                  className="btn"
                  disabled={detailPage >= totalDetailPages}
                  onClick={() => setDetailPage(detailPage + 1)}
                  style={{ fontSize: "12px", padding: "4px 10px" }}
                >
                  下一页
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* =========================================================================
          RIGHT SLIDE-OUT DRAWER (股票右侧抽屉)
          ========================================================================= */}
      <StockDrawer
        ticker={drawerTicker}
        personId={selectedPersonId}
        broker={selectedBroker}
        onClose={() => setDrawerTicker(null)}
      />
    </div>
  );
};
