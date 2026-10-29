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
  persons?: { id: number; name: string; enabled?: boolean }[];
  onPersonChange?: (id: number | null) => void;
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
        page_size: 200,
      })
      .then((res) => {
        if (res.status === "ok") {
          setDetailRows(res.rows || []);
        }
      })
      .catch((err) => console.error("Holdings detail load error:", err))
      .finally(() => setDetailLoading(false));
  };

  useEffect(() => {
    if (viewMode === "dashboard") {
      loadDashboard();
    } else {
      loadDetail();
    }
  }, [viewMode, dimension, selectedPersonId, selectedBroker, selectedTicker, statusFilter, searchKeyword]);

  // Formatters
  const formatNumber = (val: any, decimals = 2) => {
    if (val == null || isNaN(Number(val))) return "0.00";
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
          fill: "var(--text-heading, #1e293b)",
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
          fill: "var(--text-muted, #64748b)",
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

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "14px", overflowY: "auto", position: "relative" }}>
      {/* Top Header Bar: Person Tabs on Left, Mode Switcher on Right */}
      <div
        className="card-panel"
        style={{
          padding: "10px 16px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "10px",
        }}
      >
        {/* Left: Person Filter Tabs (Horizontal Pills) */}
        <div style={{ display: "flex", alignItems: "center", gap: "6px", overflowX: "auto", flex: 1, minWidth: "280px" }}>
          <button
            onClick={() => {
              setSelectedPersonId(null);
              if (onPersonChange) onPersonChange(null);
            }}
            style={{
              padding: "5px 14px",
              borderRadius: "16px",
              border: "none",
              background: selectedPersonId == null ? "var(--brand, #2563eb)" : "var(--bg-app, #f1f5f9)",
              color: selectedPersonId == null ? "#fff" : "var(--text-muted, #64748b)",
              fontWeight: selectedPersonId == null ? 700 : 500,
              fontSize: "12px",
              cursor: "pointer",
              whiteSpace: "nowrap",
              transition: "all 0.15s ease",
            }}
          >
            全部启用人员
          </button>
          {enabledPersons.map((p) => {
            const isSelected = selectedPersonId === p.id;
            return (
              <button
                key={p.id}
                onClick={() => {
                  setSelectedPersonId(p.id);
                  if (onPersonChange) onPersonChange(p.id);
                }}
                style={{
                  padding: "5px 14px",
                  borderRadius: "16px",
                  border: "none",
                  background: isSelected ? "var(--brand, #2563eb)" : "var(--bg-app, #f1f5f9)",
                  color: isSelected ? "#fff" : "var(--text-muted, #64748b)",
                  fontWeight: isSelected ? 700 : 500,
                  fontSize: "12px",
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                  transition: "all 0.15s ease",
                }}
              >
                {p.name}
              </button>
            );
          })}
        </div>

        {/* Right: View Mode Toggle (仪表板总览 vs 持仓明细页) */}
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
              padding: "5px 14px",
              borderRadius: "6px",
              border: "none",
              background: viewMode === "dashboard" ? "var(--brand, #2563eb)" : "transparent",
              color: viewMode === "dashboard" ? "#fff" : "var(--text-muted, #64748b)",
              fontWeight: viewMode === "dashboard" ? 700 : 500,
              fontSize: "12px",
              cursor: "pointer",
            }}
          >
            仪表板总览
          </button>
          <button
            onClick={() => setViewMode("detail")}
            style={{
              padding: "5px 14px",
              borderRadius: "6px",
              border: "none",
              background: viewMode === "detail" ? "var(--brand, #2563eb)" : "transparent",
              color: viewMode === "detail" ? "#fff" : "var(--text-muted, #64748b)",
              fontWeight: viewMode === "detail" ? 700 : 500,
              fontSize: "12px",
              cursor: "pointer",
            }}
          >
            持仓明细页
          </button>
        </div>
      </div>

      {/* =========================================================================
          VIEW 1: 主页仪表板 (Dashboard)
          ========================================================================= */}
      {viewMode === "dashboard" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {/* Top 4 Summary Cards (Matching Mockup) */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "14px" }}>
            {/* Card 1: 持仓总市值 */}
            <div className="card-panel" style={{ padding: "16px 20px" }}>
              <div style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", fontWeight: 500 }}>持仓总市值 (USD)</div>
              <div style={{ fontSize: "24px", fontWeight: 800, marginTop: "6px", fontFamily: "var(--font-mono)", color: "var(--text-heading, #0f172a)" }}>
                ${formatNumber(cards.total_market_value, 2)}
              </div>
              <div style={{ marginTop: "6px", fontSize: "12px", display: "flex", alignItems: "center", gap: "6px" }}>
                <span
                  style={{
                    color: (cards.floating_pnl || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                    fontWeight: 700,
                  }}
                >
                  {(cards.floating_pnl || 0) >= 0 ? "+" : ""}${formatNumber(cards.floating_pnl, 2)}
                </span>
                <span
                  style={{
                    color: (cards.floating_pnl_rate || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                    fontWeight: 600,
                  }}
                >
                  {formatPercent(cards.floating_pnl_rate)}
                </span>
              </div>
            </div>

            {/* Card 2: 持仓股票 */}
            <div className="card-panel" style={{ padding: "16px 20px" }}>
              <div style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", fontWeight: 500 }}>持仓股票</div>
              <div style={{ fontSize: "24px", fontWeight: 800, marginTop: "6px", fontFamily: "var(--font-mono)", color: "var(--text-heading, #0f172a)" }}>
                {cards.total_stock_count ?? "0"}
              </div>
              <div style={{ marginTop: "6px", fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
                当前持仓 <strong style={{ color: "var(--text-heading)" }}>{cards.open_stock_count ?? 0}</strong> | 已清仓{" "}
                <strong style={{ color: "var(--text-heading)" }}>{cards.closed_stock_count ?? 0}</strong>
              </div>
            </div>

            {/* Card 3: 累计盈亏 (含已清仓) */}
            <div className="card-panel" style={{ padding: "16px 20px" }}>
              <div style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", fontWeight: 500 }}>累计盈亏 (含已清仓)</div>
              <div
                style={{
                  fontSize: "24px",
                  fontWeight: 800,
                  marginTop: "6px",
                  fontFamily: "var(--font-mono)",
                  color: (cards.cumulative_pnl || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                }}
              >
                {(cards.cumulative_pnl || 0) >= 0 ? "+" : ""}${formatNumber(cards.cumulative_pnl, 2)}
              </div>
              <div style={{ marginTop: "6px", fontSize: "12px", fontWeight: 600 }}>
                <span
                  style={{
                    color: (cards.cumulative_pnl_rate || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                  }}
                >
                  收益率 {formatPercent(cards.cumulative_pnl_rate)}
                </span>
              </div>
            </div>

            {/* Card 4: 总投入 */}
            <div className="card-panel" style={{ padding: "16px 20px" }}>
              <div style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", fontWeight: 500 }}>总投入 (USD)</div>
              <div style={{ fontSize: "24px", fontWeight: 800, marginTop: "6px", fontFamily: "var(--font-mono)", color: "var(--text-heading, #0f172a)" }}>
                ${formatNumber(cards.total_invested, 2)}
              </div>
              <div style={{ marginTop: "6px", fontSize: "11px", color: "var(--text-muted, #64748b)" }}>
                已实现 +${formatNumber(cards.realized_pnl, 2)} | 浮动 ${(cards.floating_pnl || 0) >= 0 ? "+" : ""}${formatNumber(cards.floating_pnl, 2)}
              </div>
            </div>
          </div>

          {/* 上下两层结构：上层人员持仓明细，下层[券商持仓汇总 | 股票持仓汇总 | 资产分布] 等高三列并排 */}
          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            {/* 上层：人员持仓明细 (全宽) */}
            <div className="card-panel" style={{ padding: "16px 20px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
                <div style={{ fontSize: "15px", fontWeight: 700, display: "flex", alignItems: "center", gap: "6px" }}>
                  <Users size={16} color="var(--brand, #2563EB)" />
                  人员持仓明细
                </div>
                <span style={{ fontSize: "11px", color: "var(--text-muted, #64748B)" }}>点击表头可正倒序排列</span>
              </div>

              <div className="table-container">
                <table className="finance-table" style={{ fontSize: "12px" }}>
                  <thead>
                    <tr>
                      <th onClick={() => togglePSort("name")} style={{ cursor: "pointer", userSelect: "none" }}>
                        人员 {pSortField === "name" ? (pSortAsc ? "▲" : "▼") : ""}
                      </th>
                      <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => togglePSort("market_value")}>
                        持仓市值 (USD) {pSortField === "market_value" ? (pSortAsc ? "▲" : "▼") : ""}
                      </th>
                      <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => togglePSort("total_pnl")}>
                        累计盈亏 (USD) {pSortField === "total_pnl" ? (pSortAsc ? "▲" : "▼") : ""}
                      </th>
                      <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => togglePSort("pnl_rate")}>
                        收益率 {pSortField === "pnl_rate" ? (pSortAsc ? "▲" : "▼") : ""}
                      </th>
                      <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => togglePSort("total_stocks")}>
                        持仓股票 {pSortField === "total_stocks" ? (pSortAsc ? "▲" : "▼") : ""}
                      </th>
                      <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => togglePSort("open_stocks")}>
                        当前持仓 {pSortField === "open_stocks" ? (pSortAsc ? "▲" : "▼") : ""}
                      </th>
                      <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => togglePSort("closed_stocks")}>
                        已清仓 {pSortField === "closed_stocks" ? (pSortAsc ? "▲" : "▼") : ""}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedPersonSummary.map((p, idx) => (
                      <tr key={idx}>
                        <td style={{ fontWeight: 700, color: "var(--brand, #2563EB)" }}>{p.name}</td>
                        <td className="font-mono" style={{ textAlign: "right" }}>
                          ${formatNumber(p.market_value, 2)}
                        </td>
                        <td
                          className="font-mono"
                          style={{
                            textAlign: "right",
                            fontWeight: 700,
                            color: (p.total_pnl || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                          }}
                        >
                          {(p.total_pnl || 0) >= 0 ? "+" : ""}${formatNumber(p.total_pnl, 2)}
                        </td>
                        <td
                          className="font-mono"
                          style={{
                            textAlign: "right",
                            fontWeight: 600,
                            color: (p.pnl_rate || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                          }}
                        >
                          {formatPercent(p.pnl_rate)}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right" }}>{Math.round(p.total_stocks || 0)}</td>
                        <td className="font-mono" style={{ textAlign: "right" }}>{Math.round(p.open_stocks || 0)}</td>
                        <td className="font-mono" style={{ textAlign: "right" }}>{Math.round(p.closed_stocks || 0)}</td>
                      </tr>
                    ))}
                    {/* 合计行 */}
                    {personSummary.length > 0 && (
                      <tr style={{ background: "var(--bg-app, #F8FAFC)", fontWeight: "bold" }}>
                        <td>合计</td>
                        <td className="font-mono" style={{ textAlign: "right" }}>${formatNumber(cards.total_market_value, 2)}</td>
                        <td
                          className="font-mono"
                          style={{
                            textAlign: "right",
                            color: (cards.cumulative_pnl || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                          }}
                        >
                          {(cards.cumulative_pnl || 0) >= 0 ? "+" : ""}${formatNumber(cards.cumulative_pnl, 2)}
                        </td>
                        <td
                          className="font-mono"
                          style={{
                            textAlign: "right",
                            color: (cards.cumulative_pnl_rate || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                          }}
                        >
                          {formatPercent(cards.cumulative_pnl_rate)}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right" }}>{Math.round(cards.total_stock_count ?? 0)}</td>
                        <td className="font-mono" style={{ textAlign: "right" }}>{Math.round(cards.open_stock_count ?? 0)}</td>
                        <td className="font-mono" style={{ textAlign: "right" }}>{Math.round(cards.closed_stock_count ?? 0)}</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* 下层：券商持仓汇总 + 股票持仓汇总 + 资产分布 (三列并列等高，表头线水平对齐) */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1.15fr", gap: "16px", alignItems: "stretch" }}>
              {/* 1. 券商持仓汇总 */}
              <div className="card-panel" style={{ padding: "16px 18px", display: "flex", flexDirection: "column" }}>
                <div style={{ fontSize: "14px", fontWeight: 700, marginBottom: "10px", display: "flex", alignItems: "center", gap: "6px", height: "24px" }}>
                  <Building2 size={15} color="var(--brand, #2563EB)" />
                  券商持仓汇总
                </div>
                <div className="table-container" style={{ flex: 1 }}>
                  <table className="finance-table" style={{ fontSize: "11px" }}>
                    <thead>
                      <tr>
                        <th onClick={() => toggleBSort("channel")} style={{ cursor: "pointer", userSelect: "none" }}>
                          券商 {bSortField === "channel" ? (bSortAsc ? "▲" : "▼") : ""}
                        </th>
                        <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => toggleBSort("market_value")}>
                          持仓市值 {bSortField === "market_value" ? (bSortAsc ? "▲" : "▼") : ""}
                        </th>
                        <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => toggleBSort("total_pnl")}>
                          累计盈亏 {bSortField === "total_pnl" ? (bSortAsc ? "▲" : "▼") : ""}
                        </th>
                        <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => toggleBSort("pnl_rate")}>
                          收益率 {bSortField === "pnl_rate" ? (bSortAsc ? "▲" : "▼") : ""}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedBrokerSummary.map((b, idx) => (
                        <tr key={idx} style={{ cursor: "pointer" }} onClick={() => drillToBroker(b.channel)}>
                          <td style={{ fontWeight: 600 }}>{b.channel}</td>
                          <td className="font-mono" style={{ textAlign: "right" }}>${formatNumber(b.market_value, 2)}</td>
                          <td
                            className="font-mono"
                            style={{
                              textAlign: "right",
                              fontWeight: 700,
                              color: (b.total_pnl || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                            }}
                          >
                            {(b.total_pnl || 0) >= 0 ? "+" : ""}${formatNumber(b.total_pnl, 2)}
                          </td>
                          <td
                            className="font-mono"
                            style={{
                              textAlign: "right",
                              color: (b.pnl_rate || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                            }}
                          >
                            {formatPercent(b.pnl_rate)}
                          </td>
                        </tr>
                      ))}
                      {brokerSummary.length > 0 && (
                        <tr style={{ background: "var(--bg-app, #F8FAFC)", fontWeight: "bold" }}>
                          <td>合计</td>
                          <td className="font-mono" style={{ textAlign: "right" }}>${formatNumber(cards.total_market_value, 2)}</td>
                          <td
                            className="font-mono"
                            style={{
                              textAlign: "right",
                              color: (cards.cumulative_pnl || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                            }}
                          >
                            {(cards.cumulative_pnl || 0) >= 0 ? "+" : ""}${formatNumber(cards.cumulative_pnl, 2)}
                          </td>
                          <td
                            className="font-mono"
                            style={{
                              textAlign: "right",
                              color: (cards.cumulative_pnl_rate || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                            }}
                          >
                            {formatPercent(cards.cumulative_pnl_rate)}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* 2. 股票持仓汇总 */}
              <div className="card-panel" style={{ padding: "16px 18px", display: "flex", flexDirection: "column" }}>
                <div style={{ display: "flex", alignItems: "center", marginBottom: "10px", height: "24px" }}>
                  <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "6px" }}>
                    <Layers size={15} color="var(--brand, #2563EB)" />
                    股票持仓汇总
                  </div>
                </div>
                <div className="table-container" style={{ flex: 1 }}>
                  <table className="finance-table" style={{ fontSize: "11px" }}>
                    <thead>
                      <tr>
                        <th onClick={() => toggleSSort("ticker")} style={{ cursor: "pointer", userSelect: "none" }}>
                          Ticker {sSortField === "ticker" ? (sSortAsc ? "▲" : "▼") : ""}
                        </th>
                        <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => toggleSSort("market_value")}>
                          持仓市值 {sSortField === "market_value" ? (sSortAsc ? "▲" : "▼") : ""}
                        </th>
                        <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => toggleSSort("total_pnl")}>
                          累计盈亏 {sSortField === "total_pnl" ? (sSortAsc ? "▲" : "▼") : ""}
                        </th>
                        <th style={{ textAlign: "right", cursor: "pointer", userSelect: "none" }} onClick={() => toggleSSort("pnl_rate")}>
                          收益率 {sSortField === "pnl_rate" ? (sSortAsc ? "▲" : "▼") : ""}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedStockSummary.slice(0, 7).map((s, idx) => (
                        <tr
                          key={idx}
                          style={{ cursor: "pointer" }}
                          onClick={() => {
                            setDrawerTicker(s.ticker);
                          }}
                        >
                          <td style={{ fontWeight: 700, color: "var(--brand, #2563EB)" }}>{s.ticker}</td>
                          <td className="font-mono" style={{ textAlign: "right" }}>${formatNumber(s.market_value, 2)}</td>
                          <td
                            className="font-mono"
                            style={{
                              textAlign: "right",
                              fontWeight: 700,
                              color: (s.total_pnl || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
                            }}
                          >
                            {(s.total_pnl || 0) >= 0 ? "+" : ""}${formatNumber(s.total_pnl, 2)}
                          </td>
                          <td
                            className="font-mono"
                            style={{
                              textAlign: "right",
                              color: (s.pnl_rate || 0) >= 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)",
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

              {/* 3. 资产分布图 (Donut Ring Chart) */}
              <div className="card-panel" style={{ padding: "16px 18px", display: "flex", flexDirection: "column" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                  <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "6px" }}>
                    <PieIcon size={15} color="var(--brand, #2563EB)" />
                    资产分布
                  </div>

                  {/* Donut Tabs */}
                  <div
                    style={{
                      display: "flex",
                      background: "var(--bg-app, #f1f5f9)",
                      padding: "2px",
                      borderRadius: "6px",
                      gap: "2px",
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
                          padding: "3px 8px",
                          borderRadius: "4px",
                          border: "none",
                          fontSize: "11px",
                          background: distTab === t.key ? "var(--brand, #2563eb)" : "transparent",
                          color: distTab === t.key ? "#fff" : "var(--text-muted, #64748B)",
                          fontWeight: distTab === t.key ? 700 : 500,
                          cursor: "pointer",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {t.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div style={{ fontSize: "11px", color: "var(--text-muted, #64748B)", marginBottom: "8px" }}>持仓市值分布占比</div>

                {/* Donut Chart Ring & Clean Legend */}
                <div style={{ display: "flex", alignItems: "center", gap: "10px", flex: 1, minHeight: "220px" }}>
                  <div style={{ flex: 1.1, height: "200px" }}>
                    <ReactECharts option={donutOption} style={{ height: "100%", width: "100%" }} />
                  </div>

                  {/* Legend Table on the Right */}
                  <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "6px", overflowY: "auto", maxHeight: "200px" }}>
                    {donutSlices.map((d: any, idx: number) => (
                      <div key={idx} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "11px", gap: "4px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "5px" }}>
                          <span
                            style={{
                              width: "8px",
                              height: "8px",
                              borderRadius: "50%",
                              background: donutColors[idx % donutColors.length],
                              flexShrink: 0,
                            }}
                          />
                          <span style={{ fontWeight: 600, color: "var(--text-heading, #0f172a)", maxWidth: "60px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {d.name}
                          </span>
                        </div>
                        <div style={{ display: "flex", gap: "6px", fontFamily: "var(--font-mono)", fontSize: "11px" }}>
                          <span>${formatNumber(d.value, 0)}</span>
                          <span style={{ color: "var(--text-muted, #64748B)", width: "32px", textAlign: "right" }}>{d.percent}%</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
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
          {/* Detail Filters Toolbar (Exclusive to Detail View!) */}
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "10px",
              paddingBottom: "8px",
              borderBottom: "1px solid var(--border-color, #e2e8f0)",
            }}
          >
            {/* Status, Broker, Search, Export */}
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <select
                value={statusFilter}
                onChange={(e) => {
                  const nextStatus = e.target.value;
                  setStatusFilter(nextStatus);
                  setDetailPage(1);
                  if (nextStatus === "已清仓") {
                    setSortField("last_close_date");
                    setSortAsc(false);
                  } else {
                    setSortField("market_value");
                    setSortAsc(false);
                  }
                }}
                className="input-control"
                style={{ fontSize: "12px", padding: "5px 10px" }}
              >
                <option value="持有中">当前持仓</option>
                <option value="已清仓">已清仓</option>
              </select>

              <select
                value={selectedBroker}
                onChange={(e) => {
                  setSelectedBroker(e.target.value);
                  setDetailPage(1);
                }}
                className="input-control"
                style={{ fontSize: "12px", padding: "5px 10px" }}
              >
                <option value="">全部渠道</option>
                <option value="长桥">长桥</option>
                <option value="BBAE">BBAE</option>
                <option value="第一证券">第一证券</option>
                <option value="嘉信">嘉信</option>
              </select>

              <div style={{ position: "relative" }}>
                <Search size={14} style={{ position: "absolute", left: "8px", top: "8px", color: "var(--text-muted)" }} />
                <input
                  type="text"
                  placeholder="搜索人员、券商或股票代码..."
                  value={searchKeyword}
                  onChange={(e) => {
                    setSearchKeyword(e.target.value);
                    setDetailPage(1);
                  }}
                  className="input-control"
                  style={{ paddingLeft: "28px", fontSize: "12px", width: "190px" }}
                />
              </div>

              <button onClick={exportDetailCsv} className="btn btn-quiet" style={{ fontSize: "12px", padding: "5px 10px" }}>
                <Download size={14} style={{ marginRight: "4px" }} />
                导出明细
              </button>
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
              <Layers size={16} color="var(--brand, #2563EB)" />
              <span>
                {statusFilter === "已清仓" ? "已清仓明细" : "当前持仓明细"} ·{" "}
                {dimension === "person" ? "人员维度" : dimension === "broker" ? "券商维度" : "股票维度"}
              </span>
              <span style={{ fontSize: "12px", color: "var(--text-muted)", fontWeight: 400 }}>
                （共 {totalDetailCount} 条记录）
              </span>
            </div>
            <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>点击任意股票行右侧滑出深度分析抽屉</span>
          </div>

          {/* Unified Detail Table */}
          <div className="table-container" style={{ border: "1px solid var(--border-color, #e2e8f0)", borderRadius: "8px" }}>
            <table className="finance-table" style={{ fontSize: "12px" }}>
              <thead>
                {statusFilter === "已清仓" ? (
                  <tr>
                    <th onClick={() => toggleDetailSort("person_name")} style={{ cursor: "pointer" }}>
                      归属人员 {sortField === "person_name" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th onClick={() => toggleDetailSort("channel")} style={{ cursor: "pointer" }}>
                      券商渠道 {sortField === "channel" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th onClick={() => toggleDetailSort("ticker")} style={{ cursor: "pointer" }}>
                      Ticker {sortField === "ticker" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th onClick={() => toggleDetailSort("first_buy_date")} style={{ cursor: "pointer" }}>
                      首次买入日期 {sortField === "first_buy_date" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th onClick={() => toggleDetailSort("last_close_date")} style={{ cursor: "pointer" }}>
                      最后清仓日期 {sortField === "last_close_date" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("closed_quantity")}>
                      清仓数量 {sortField === "closed_quantity" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("avg_buy_price")}>
                      买入均价 {sortField === "avg_buy_price" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("avg_sell_price")}>
                      卖出均价 {sortField === "avg_sell_price" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("close_price")}>
                      清仓价 {sortField === "close_price" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("current_price")}>
                      当前价 {sortField === "current_price" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("post_close_change")}>
                      清仓后涨跌幅 {sortField === "post_close_change" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("total_buy_amount")}>
                      买入总额 {sortField === "total_buy_amount" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("total_sell_amount")}>
                      卖出总额 {sortField === "total_sell_amount" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("realized_pnl")}>
                      已实现盈亏 {sortField === "realized_pnl" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("realized_pnl_rate")}>
                      已实现收益率 {sortField === "realized_pnl_rate" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("buy_count")}>
                      买入次数 {sortField === "buy_count" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("sell_count")}>
                      卖出次数 {sortField === "sell_count" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                  </tr>
                ) : (
                  <tr>
                    <th onClick={() => toggleDetailSort("person_name")} style={{ cursor: "pointer" }}>
                      归属人员 {sortField === "person_name" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th onClick={() => toggleDetailSort("channel")} style={{ cursor: "pointer" }}>
                      券商渠道 {sortField === "channel" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th onClick={() => toggleDetailSort("ticker")} style={{ cursor: "pointer" }}>
                      Ticker {sortField === "ticker" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("quantity")}>
                      数量 {sortField === "quantity" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("cost_price")}>
                      成本价 {sortField === "cost_price" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("cost_amount")}>
                      成本总额 {sortField === "cost_amount" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("current_price")}>
                      当前最新价 {sortField === "current_price" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("market_value")}>
                      市值 {sortField === "market_value" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("pnl_rate")}>
                      收益率 {sortField === "pnl_rate" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("total_pnl")}>
                      累计盈亏 {sortField === "total_pnl" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("buy_count")}>
                      买入次数 {sortField === "buy_count" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("sell_count")}>
                      卖出次数 {sortField === "sell_count" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                    <th style={{ textAlign: "right", cursor: "pointer" }} onClick={() => toggleDetailSort("weight")}>
                      持仓占比 {sortField === "weight" ? (sortAsc ? "▲" : "▼") : ""}
                    </th>
                  </tr>
                )}
              </thead>
              <tbody>
                {detailLoading ? (
                  <tr>
                    <td colSpan={statusFilter === "已清仓" ? 17 : 13} style={{ textAlign: "center", padding: "30px", color: "var(--brand)" }}>
                      正在加载持仓明细...
                    </td>
                  </tr>
                ) : statusFilter === "已清仓" ? (
                  currentDetailRows.map((r, idx) => {
                    const postChange = r.post_close_change != null ? Number(r.post_close_change) : null;
                    const realizedPnl = r.realized_pnl != null ? Number(r.realized_pnl) : null;
                    const realizedRate = r.realized_pnl_rate != null ? Number(r.realized_pnl_rate) : null;
                    const closedQty = r.closed_quantity != null ? Number(r.closed_quantity) : null;
                    return (
                      <tr
                        key={idx}
                        style={{ cursor: "pointer" }}
                        onClick={() => setDrawerTicker(r.ticker)}
                        title="点击从右侧打开该标的深度交易与收益抽屉"
                      >
                        <td style={{ fontWeight: 600 }}>{r.person_name || "—"}</td>
                        <td>{r.channel || "—"}</td>
                        <td style={{ fontWeight: 700, color: "var(--brand)" }}>{r.ticker || "—"}</td>
                        <td className="font-mono">{r.first_buy_date || "—"}</td>
                        <td className="font-mono">{r.last_close_date || "—"}</td>
                        <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                          {closedQty != null ? formatNumber(closedQty, closedQty % 1 === 0 ? 0 : 2) : "—"}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right" }}>
                          {r.avg_buy_price != null ? `$${formatNumber(r.avg_buy_price, 2)}` : "—"}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right" }}>
                          {r.avg_sell_price != null ? `$${formatNumber(r.avg_sell_price, 2)}` : "—"}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                          {r.close_price != null ? `$${formatNumber(r.close_price, 2)}` : "—"}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right", color: "var(--brand)" }}>
                          {r.current_price != null ? `$${formatNumber(r.current_price, 2)}` : "—"}
                        </td>
                        <td
                          className="font-mono"
                          style={{
                            textAlign: "right",
                            fontWeight: 600,
                            color: postChange != null && postChange > 0 ? "var(--profit-color, #16A34A)" : postChange != null && postChange < 0 ? "var(--loss-color, #DC2626)" : "var(--text-muted)",
                          }}
                        >
                          {postChange != null ? `${postChange > 0 ? "+" : ""}${(postChange * 100).toFixed(2)}%` : "—"}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right" }}>
                          {r.total_buy_amount != null ? `$${formatNumber(r.total_buy_amount, 2)}` : "—"}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right" }}>
                          {r.total_sell_amount != null ? `$${formatNumber(r.total_sell_amount, 2)}` : "—"}
                        </td>
                        <td
                          className="font-mono"
                          style={{
                            textAlign: "right",
                            fontWeight: 700,
                            color: realizedPnl != null && realizedPnl > 0 ? "var(--profit-color, #16A34A)" : realizedPnl != null && realizedPnl < 0 ? "var(--loss-color, #DC2626)" : "var(--text-muted)",
                          }}
                        >
                          {realizedPnl != null ? `${realizedPnl > 0 ? "+" : ""}$${formatNumber(realizedPnl, 2)}` : "—"}
                        </td>
                        <td
                          className="font-mono"
                          style={{
                            textAlign: "right",
                            fontWeight: 600,
                            color: realizedRate != null && realizedRate > 0 ? "var(--profit-color, #16A34A)" : realizedRate != null && realizedRate < 0 ? "var(--loss-color, #DC2626)" : "var(--text-muted)",
                          }}
                        >
                          {realizedRate != null ? `${realizedRate > 0 ? "+" : ""}${(realizedRate * 100).toFixed(2)}%` : "—"}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right" }}>
                          {r.buy_count != null ? r.buy_count : "—"}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right" }}>
                          {r.sell_count != null ? r.sell_count : "—"}
                        </td>
                      </tr>
                    );
                  })
                ) : currentDetailRows.map((r, idx) => {
                  const totalPnl = r.total_pnl != null ? Number(r.total_pnl) : (r.pnl != null ? Number(r.pnl) : null);
                  const pnlRate = r.pnl_rate != null ? Number(r.pnl_rate) : null;
                  const qty = r.quantity != null ? Number(r.quantity) : null;
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
                      <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                        {qty != null ? formatNumber(qty, qty % 1 === 0 ? 0 : 2) : "—"}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {r.cost_price != null ? `$${formatNumber(r.cost_price, 2)}` : "—"}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {r.cost_amount != null ? `$${formatNumber(r.cost_amount, 2)}` : "—"}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right", color: "var(--brand)" }}>
                        {r.current_price != null ? `$${formatNumber(r.current_price, 2)}` : "—"}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right", fontWeight: 700 }}>
                        {r.market_value != null ? `$${formatNumber(r.market_value, 2)}` : "—"}
                      </td>
                      <td
                        className="font-mono"
                        style={{
                          textAlign: "right",
                          fontWeight: 600,
                          color: pnlRate != null && pnlRate > 0 ? "var(--profit-color, #16A34A)" : pnlRate != null && pnlRate < 0 ? "var(--loss-color, #DC2626)" : "var(--text-muted)",
                        }}
                      >
                        {pnlRate != null ? `${pnlRate > 0 ? "+" : ""}${(pnlRate * 100).toFixed(2)}%` : "—"}
                      </td>
                      <td
                        className="font-mono"
                        style={{
                          textAlign: "right",
                          fontWeight: 700,
                          color: totalPnl != null && totalPnl > 0 ? "var(--profit-color, #16A34A)" : totalPnl != null && totalPnl < 0 ? "var(--loss-color, #DC2626)" : "var(--text-muted)",
                        }}
                      >
                        {totalPnl != null ? `${totalPnl > 0 ? "+" : ""}$${formatNumber(totalPnl, 2)}` : "—"}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {r.buy_count != null ? r.buy_count : "—"}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {r.sell_count != null ? r.sell_count : "—"}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right", color: "var(--text-muted)" }}>
                        {r.weight != null ? `${(Number(r.weight) * 100).toFixed(2)}%` : "0.00%"}
                      </td>
                    </tr>
                  );
                })}
                {!detailLoading && !currentDetailRows.length && (
                  <tr>
                    <td colSpan={statusFilter === "已清仓" ? 17 : 13} style={{ textAlign: "center", padding: "30px", color: "var(--text-muted)" }}>
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
