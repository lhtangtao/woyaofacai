import React, { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import {
  Calendar,
  ChevronRight,
  Download,
  Filter,
  Info,
  TrendingDown,
  TrendingUp,
  X,
  ChevronLeft,
  Search,
  RotateCcw,
  ArrowUpDown,
  ArrowUpRight,
  History,
} from "lucide-react";
import { loadMonthlyReport, exportMonthlyCsv, type ReportEntry } from "./monthly-cash-flow";
import { PersonItem } from "../types";
import { StockDrawer } from "../components/StockDrawer";
import "./daily-performance.css";

interface MonthlyScalpingViewProps {
  personId: number | null;
  onPersonChange?: (id: number | null) => void;
  broker?: string;
  onBrokerChange?: (broker: string) => void;
  persons: PersonItem[];
  refreshKey?: number;
}

// 标准券商中文名称映射
function fmtBrokerCn(val: any): string {
  if (!val) return "—";
  const s = String(val).trim();
  const up = s.toUpperCase();
  if (up === "LONGBRIDGE" || up === "LONG BRIDGE" || up === "长桥证券") return "长桥";
  if (up === "FUTU" || up === "富途证券" || up === "富途") return "富途";
  if (up === "TIGER" || up === "老虎证券" || up === "老虎") return "老虎";
  if (up === "FIRSTTRADE" || up === "FIRST TRADE" || up === "第一证券" || up === "第一") return "第一证券";
  if (up === "IBKR" || up === "IB" || up === "INTERACTIVE BROKERS" || up === "盈透证券" || up === "盈透") return "盈透";
  if (up === "SCHWAB" || up === "CHARLES SCHWAB" || up === "嘉信理财" || up === "嘉信") return "嘉信";
  if (up === "BBAE") return "BBAE";
  return s;
}

function fmtMoney(val: any, digits: number = 2): string {
  if (val == null || val === "") return "—";
  const num = Number(val);
  if (isNaN(num)) return String(val);
  return num.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtSignedMoney(val: any, digits: number = 2): string {
  if (val == null || val === "") return "—";
  const num = Number(val);
  if (isNaN(num)) return String(val);
  if (Math.abs(num) < 1e-9) return `0.${"0".repeat(digits)}`;
  const sign = num > 0 ? "+" : "";
  return `${sign}${num.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export const MonthlyScalpingView: React.FC<MonthlyScalpingViewProps> = ({
  personId,
  onPersonChange,
  broker: externalBroker,
  onBrokerChange,
  persons,
  refreshKey = 0,
}) => {
  const [entries, setEntries] = useState<ReportEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 筛选器状态
  const [selectedBroker, setSelectedBroker] = useState<string>(externalBroker || "");
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  const [tickerQuery, setTickerQuery] = useState<string>("");
  const [startMonth, setStartMonth] = useState<string>("");
  const [endMonth, setEndMonth] = useState<string>("");

  // 交互高亮与联动状态
  const [activeMonth, setActiveMonth] = useState<string>("");
  const [trendMode, setTrendMode] = useState<"monthly" | "cum">("monthly");

  // 抽屉状态
  const [activeDrawerRow, setActiveDrawerRow] = useState<any[] | null>(null);
  const [replayStock, setReplayStock] = useState<string | null>(null);

  // 表格分页与排序
  const [page, setPage] = useState<number>(1);
  const [pageSize, setPageSize] = useState<number>(50);
  const [sortCol, setSortCol] = useState<number>(0); // 0: 月份
  const [sortAsc, setSortAsc] = useState<boolean>(false); // 默认月份降序

  // 外部券商变化同步
  useEffect(() => {
    if (externalBroker !== undefined) {
      setSelectedBroker(externalBroker);
    }
  }, [externalBroker]);

  // 加载数据
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    loadMonthlyReport("每月微操收益", personId, controller.signal)
      .then((data) => {
        setEntries(data);
        // 初始化默认月份范围
        const months = Array.from(new Set(data.map((e) => String(e.values[0] || "")).filter(Boolean))).sort();
        if (months.length > 0) {
          if (!startMonth) setStartMonth(months[0]);
          if (!endMonth) setEndMonth(months[months.length - 1]);
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          setError(err.message || "加载每月微操收益失败");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [personId, refreshKey]);

  // 全部可用券商与分类选项
  const availableBrokers = useMemo(() => {
    const set = new Set<string>();
    entries.forEach((e) => {
      const b = fmtBrokerCn(e.values[1]);
      if (b && b !== "—") set.add(b);
    });
    return Array.from(set).sort();
  }, [entries]);

  const availableCategories = useMemo(() => {
    const set = new Set<string>();
    entries.forEach((e) => {
      const c = String(e.values[2] || "").trim();
      if (c) set.add(c);
    });
    return Array.from(set).sort();
  }, [entries]);

  // 根据当前条件筛选原始数据
  const filteredRows = useMemo(() => {
    return entries
      .map((e) => e.values)
      .filter((r) => {
        const m = String(r[0] || "");
        const b = fmtBrokerCn(r[1]);
        const cat = String(r[2] || "");
        const sym = String(r[3] || "").toUpperCase();

        if (activeMonth && m !== activeMonth) return false;
        if (selectedBroker && b !== selectedBroker) return false;
        if (selectedCategory && cat !== selectedCategory) return false;
        if (tickerQuery && !sym.includes(tickerQuery.trim().toUpperCase())) return false;
        if (startMonth && m < startMonth) return false;
        if (endMonth && m > endMonth) return false;

        return true;
      });
  }, [entries, activeMonth, selectedBroker, selectedCategory, tickerQuery, startMonth, endMonth]);

  // 1. 核心指标卡片统计
  const kpis = useMemo(() => {
    let totPnl = 0;
    let totTrades = 0;
    let totBuy = 0;
    let totSell = 0;
    let sumSpread = 0;
    const tickerSet = new Set<string>();

    filteredRows.forEach((r) => {
      totPnl += Number(r[11] || 0);
      totTrades += Number(r[4] || 0);
      totBuy += Number(r[9] || 0);
      totSell += Number(r[10] || 0);
      sumSpread += Number(r[8] || 0);
      if (r[3]) tickerSet.add(String(r[3]));
    });

    const avgSpread = filteredRows.length > 0 ? sumSpread / filteredRows.length : 0;
    return {
      totPnl,
      totTrades,
      totBuy,
      totSell,
      avgSpread,
      stockCount: tickerSet.size,
    };
  }, [filteredRows]);

  // 2. 月度收益趋势数据 (支持月度收益 / 累计收益)
  const trendData = useMemo(() => {
    // 趋势图根据全局筛选（除了 activeMonth 本身）聚合各月微操收益
    const monthMap: Record<string, number> = {};
    entries
      .map((e) => e.values)
      .filter((r) => {
        const m = String(r[0] || "");
        const b = fmtBrokerCn(r[1]);
        const cat = String(r[2] || "");
        const sym = String(r[3] || "").toUpperCase();

        if (selectedBroker && b !== selectedBroker) return false;
        if (selectedCategory && cat !== selectedCategory) return false;
        if (tickerQuery && !sym.includes(tickerQuery.trim().toUpperCase())) return false;
        if (startMonth && m < startMonth) return false;
        if (endMonth && m > endMonth) return false;
        return true;
      })
      .forEach((r) => {
        const m = String(r[0] || "");
        monthMap[m] = (monthMap[m] || 0) + Number(r[11] || 0);
      });

    const sortedMonths = Object.keys(monthMap).sort();
    let cum = 0;
    const list = sortedMonths.map((m) => {
      const pnl = monthMap[m];
      cum += pnl;
      return { month: m, pnl, cum };
    });

    return list;
  }, [entries, selectedBroker, selectedCategory, tickerQuery, startMonth, endMonth]);

  // 3. 券商渠道收益排行
  const brokerRankings = useMemo(() => {
    const map: Record<string, number> = {};
    filteredRows.forEach((r) => {
      const b = fmtBrokerCn(r[1]);
      map[b] = (map[b] || 0) + Number(r[11] || 0);
    });

    const sorted = Object.entries(map).sort((a, b) => b[1] - a[1]);
    const maxAbs = Math.max(...sorted.map(([, val]) => Math.abs(val)), 1);
    const absTotal = Math.abs(kpis.totPnl) > 0.001 ? Math.abs(kpis.totPnl) : 1;

    return sorted.map(([name, pnl]) => ({
      name,
      pnl,
      ratio: (Math.abs(pnl) / maxAbs) * 100,
      share: (Math.abs(pnl) / absTotal) * 100,
    }));
  }, [filteredRows, kpis.totPnl]);

  // 4. 股票微操收益 TOP 5 (盈利 TOP 5 & 亏损 TOP 5)
  const { profitTop5, lossTop5 } = useMemo(() => {
    const map: Record<string, { pnl: number; trades: number; buy: number; sell: number }> = {};
    filteredRows.forEach((r) => {
      const sym = String(r[3] || "");
      if (!map[sym]) map[sym] = { pnl: 0, trades: 0, buy: 0, sell: 0 };
      map[sym].pnl += Number(r[11] || 0);
      map[sym].trades += Number(r[4] || 0);
      map[sym].buy += Number(r[9] || 0);
      map[sym].sell += Number(r[10] || 0);
    });

    const allStocks = Object.entries(map).map(([sym, stats]) => ({
      sym,
      ...stats,
    }));

    const profits = allStocks.filter((s) => s.pnl > 0).sort((a, b) => b.pnl - a.pnl).slice(0, 5);
    const losses = allStocks.filter((s) => s.pnl < 0).sort((a, b) => a.pnl - b.pnl).slice(0, 5);

    return { profitTop5: profits, lossTop5: losses };
  }, [filteredRows]);

  // 5. 明细表格排序与分页
  const sortedTableRows = useMemo(() => {
    const list = [...filteredRows];
    list.sort((a, b) => {
      // 默认排序规范：月份降序 -> 收益降序 -> Ticker 升序
      if (sortCol === 0) {
        const ma = String(a[0] || "");
        const mb = String(b[0] || "");
        if (ma !== mb) return sortAsc ? ma.localeCompare(mb) : mb.localeCompare(ma);
        const pa = Number(a[11] || 0);
        const pb = Number(b[11] || 0);
        if (pa !== pb) return pb - pa;
        return String(a[3] || "").localeCompare(String(b[3] || ""));
      }
      const va = a[sortCol];
      const vb = b[sortCol];
      const na = Number(va);
      const nb = Number(vb);
      if (!isNaN(na) && !isNaN(nb)) {
        return sortAsc ? na - nb : nb - na;
      }
      return sortAsc ? String(va || "").localeCompare(String(vb || "")) : String(vb || "").localeCompare(String(va || ""));
    });
    return list;
  }, [filteredRows, sortCol, sortAsc]);

  const totalPages = Math.ceil(sortedTableRows.length / pageSize) || 1;
  const pagedRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return sortedTableRows.slice(start, start + pageSize);
  }, [sortedTableRows, page, pageSize]);

  // 重置筛选
  const handleReset = () => {
    setSelectedBroker("");
    if (onBrokerChange) onBrokerChange("");
    setSelectedCategory("");
    setTickerQuery("");
    setActiveMonth("");
    const months = Array.from(new Set(entries.map((e) => String(e.values[0] || "")).filter(Boolean))).sort();
    if (months.length > 0) {
      setStartMonth(months[0]);
      setEndMonth(months[months.length - 1]);
    }
    setPage(1);
  };

  // 导出 CSV
  const handleExport = () => {
    const cols = [
      "月份",
      "券商渠道",
      "分类",
      "Ticker",
      "微操次数",
      "微操数量",
      "微操均买价($)",
      "微操均卖价($)",
      "每股价差($)",
      "买入金额($)",
      "卖出金额($)",
      "微操收益($)",
      "首笔微操时间",
      "最近微操时间",
    ];
    exportMonthlyCsv("每月微操收益明细", cols, sortedTableRows);
  };

  // ECharts 月度微操收益趋势配置
  const chartOption = useMemo(() => {
    const xData = trendData.map((d) => d.month);
    const yData = trendData.map((d) => {
      const val = trendMode === "monthly" ? d.pnl : d.cum;
      const isSelected = activeMonth && d.month === activeMonth;
      return {
        value: val,
        itemStyle: {
          color: val >= 0 ? "#10B981" : "#EF4444",
          borderColor: isSelected ? "#2563EB" : "transparent",
          borderWidth: isSelected ? 2 : 0,
          borderRadius: [2, 2, 0, 0],
        },
      };
    });

    return {
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params: any[]) => {
          if (!params || !params[0]) return "";
          const item = params[0];
          const m = item.name;
          const val = item.value;
          return `
            <div style="font-weight: 600; margin-bottom: 4px;">${m}</div>
            <div style="display:flex; justify-content:space-between; gap:16px;">
              <span>${trendMode === "monthly" ? "月度收益" : "累计收益"}:</span>
              <span style="font-weight:bold; color:${val >= 0 ? "#10B981" : "#EF4444"}">
                ${val >= 0 ? "+" : ""}$${fmtMoney(val, 2)}
              </span>
            </div>
            <div style="font-size:11px; color:#94A3B8; margin-top:4px;">点击柱体联动筛选对应月份</div>
          `;
        },
      },
      grid: {
        top: 25,
        left: 55,
        right: 20,
        bottom: 30,
      },
      xAxis: {
        type: "category",
        data: xData,
        axisLine: { lineStyle: { color: "#CBD5E1" } },
        axisLabel: { color: "#64748B", fontSize: 11, interval: 0, rotate: xData.length > 15 ? 40 : 0 },
      },
      yAxis: {
        type: "value",
        axisLine: { show: false },
        splitLine: { lineStyle: { color: "#F1F5F9", type: "dashed" } },
        axisLabel: {
          color: "#94A3B8",
          fontSize: 11,
          formatter: (v: number) => `$${fmtMoney(v, 0)}`,
        },
      },
      series: [
        {
          name: trendMode === "monthly" ? "月度收益" : "累计收益",
          type: "bar",
          data: yData,
          barMaxWidth: 22,
        },
      ],
    };
  }, [trendData, trendMode, activeMonth]);

  return (
    <div className="daily-perf-container" style={{ gap: 14 }}>
      {/* 顶部标题与操作栏 */}
      <div className="daily-perf-header" style={{ marginBottom: 0 }}>
        <div className="title-area">
          <h2>每月微操收益</h2>
          <p>分析主动交易的操作效果，比较不同月份、券商和股票的微操收益表现。</p>
        </div>
        <div className="header-actions" style={{ display: "flex", gap: 10 }}>
          <button className="primary-btn" onClick={handleExport} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, height: 32, padding: "0 14px", backgroundColor: "#2563EB", color: "#FFF", borderRadius: 6, border: "none", cursor: "pointer", fontWeight: 600 }}>
            <Download size={14} />
            <span>导出</span>
          </button>
        </div>
      </div>

      {/* 1. 顶部全局筛选卡片 */}
      <div className="filter-card" style={{ padding: "10px 14px", display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, background: "#FFF", borderRadius: 8, border: "1px solid #E2E8F0" }}>
        {/* 归属人员 */}
        <select
          value={personId ?? ""}
          onChange={(e) => {
            const val = e.target.value === "" ? null : Number(e.target.value);
            if (onPersonChange) onPersonChange(val);
          }}
          style={{ height: 32, padding: "0 10px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 13, background: "#FFF", cursor: "pointer" }}
        >
          <option value="">全部人员</option>
          {persons.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>

        {/* 券商渠道 */}
        <select
          value={selectedBroker}
          onChange={(e) => {
            const b = e.target.value;
            setSelectedBroker(b);
            if (onBrokerChange) onBrokerChange(b);
          }}
          style={{ height: 32, padding: "0 10px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 13, background: "#FFF", cursor: "pointer" }}
        >
          <option value="">全部渠道</option>
          {availableBrokers.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>

        {/* 股票分类 */}
        <select
          value={selectedCategory}
          onChange={(e) => setSelectedCategory(e.target.value)}
          style={{ height: 32, padding: "0 10px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 13, background: "#FFF", cursor: "pointer" }}
        >
          <option value="">全部分类</option>
          {availableCategories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>

        {/* 输入 Ticker */}
        <div style={{ position: "relative", width: 140 }}>
          <input
            type="text"
            placeholder="输入 Ticker"
            value={tickerQuery}
            onChange={(e) => setTickerQuery(e.target.value)}
            style={{ width: "100%", height: 32, padding: "0 8px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 13, boxSizing: "border-box" }}
          />
        </div>

        {/* 月份范围 */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="text"
            placeholder="起始月份 2025-01"
            value={startMonth}
            onChange={(e) => setStartMonth(e.target.value)}
            style={{ width: 110, height: 32, padding: "0 8px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 12 }}
          />
          <span style={{ color: "#64748B", fontWeight: "bold" }}>~</span>
          <input
            type="text"
            placeholder="结束月份 2026-11"
            value={endMonth}
            onChange={(e) => setEndMonth(e.target.value)}
            style={{ width: 110, height: 32, padding: "0 8px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 12 }}
          />
        </div>

        <button
          onClick={() => setPage(1)}
          style={{ height: 32, padding: "0 16px", borderRadius: 6, background: "#2563EB", color: "#FFF", fontWeight: 600, fontSize: 13, border: "none", cursor: "pointer" }}
        >
          查询
        </button>

        <button
          onClick={handleReset}
          style={{ height: 32, padding: "0 14px", borderRadius: 6, background: "#F1F5F9", color: "#475569", fontWeight: 600, fontSize: 13, border: "1px solid #CBD5E1", cursor: "pointer" }}
        >
          重置
        </button>

        {activeMonth && (
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6, background: "#EFF6FF", padding: "4px 10px", borderRadius: 6, border: "1px solid #BFDBFE" }}>
            <span style={{ fontSize: 12, color: "#1E40AF" }}>当前锁定月份: <strong>{activeMonth}</strong></span>
            <X size={14} style={{ cursor: "pointer", color: "#1E40AF" }} onClick={() => setActiveMonth("")} />
          </div>
        )}
      </div>

      {/* 2. 核心指标卡片 (5张横向) */}
      <div className="stat-cards-grid" style={{ gridTemplateColumns: "repeat(5, 1fr)", gap: 12 }}>
        {/* 微操收益合计 */}
        <div className="stat-card" style={{ height: 86, padding: "12px 14px", display: "flex", alignItems: "center", gap: 12, background: "#FFF", borderRadius: 8, border: "1px solid #E2E8F0" }}>
          <div style={{ width: 38, height: 38, borderRadius: 19, background: "#F1F5F9", color: "#2563EB", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16 }}>
            🪙
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 12, color: "#64748B", fontWeight: 500 }}>微操收益合计</span>
            <span style={{ fontSize: 19, fontWeight: 700, color: kpis.totPnl >= 0 ? "#10B981" : "#EF4444" }}>
              {kpis.totPnl >= 0 ? "+" : ""}${fmtMoney(kpis.totPnl, 2)}
            </span>
            <span style={{ fontSize: 11, color: "#94A3B8" }}>当前筛选期间</span>
          </div>
        </div>

        {/* 微操次数 */}
        <div className="stat-card" style={{ height: 86, padding: "12px 14px", display: "flex", alignItems: "center", gap: 12, background: "#FFF", borderRadius: 8, border: "1px solid #E2E8F0" }}>
          <div style={{ width: 38, height: 38, borderRadius: 19, background: "#F1F5F9", color: "#2563EB", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16 }}>
            ⇄
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 12, color: "#64748B", fontWeight: 500 }}>微操次数</span>
            <span style={{ fontSize: 19, fontWeight: 700, color: "#0F172A" }}>
              {kpis.totTrades}
            </span>
            <span style={{ fontSize: 11, color: "#94A3B8" }}>涉及 {kpis.stockCount} 只股票</span>
          </div>
        </div>

        {/* 买入金额合计 */}
        <div className="stat-card" style={{ height: 86, padding: "12px 14px", display: "flex", alignItems: "center", gap: 12, background: "#FFF", borderRadius: 8, border: "1px solid #E2E8F0" }}>
          <div style={{ width: 38, height: 38, borderRadius: 19, background: "#F1F5F9", color: "#10B981", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16 }}>
            ↗
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 12, color: "#64748B", fontWeight: 500 }}>买入金额合计</span>
            <span style={{ fontSize: 19, fontWeight: 700, color: "#0F172A" }}>
              ${fmtMoney(kpis.totBuy, 2)}
            </span>
            <span style={{ fontSize: 11, color: "transparent" }}>—</span>
          </div>
        </div>

        {/* 卖出金额合计 */}
        <div className="stat-card" style={{ height: 86, padding: "12px 14px", display: "flex", alignItems: "center", gap: 12, background: "#FFF", borderRadius: 8, border: "1px solid #E2E8F0" }}>
          <div style={{ width: 38, height: 38, borderRadius: 19, background: "#F1F5F9", color: "#EF4444", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16 }}>
            ↘
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 12, color: "#64748B", fontWeight: 500 }}>卖出金额合计</span>
            <span style={{ fontSize: 19, fontWeight: 700, color: "#0F172A" }}>
              ${fmtMoney(kpis.totSell, 2)}
            </span>
            <span style={{ fontSize: 11, color: "transparent" }}>—</span>
          </div>
        </div>

        {/* 平均每股价差 */}
        <div className="stat-card" style={{ height: 86, padding: "12px 14px", display: "flex", alignItems: "center", gap: 12, background: "#FFF", borderRadius: 8, border: "1px solid #E2E8F0" }}>
          <div style={{ width: 38, height: 38, borderRadius: 19, background: "#F1F5F9", color: "#2563EB", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16 }}>
            %
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 12, color: "#64748B", fontWeight: 500 }}>平均每股价差</span>
            <span style={{ fontSize: 19, fontWeight: 700, color: kpis.avgSpread >= 0 ? "#10B981" : "#EF4444" }}>
              {kpis.avgSpread >= 0 ? "+" : ""}${fmtMoney(kpis.avgSpread, 2)}
            </span>
            <span style={{ fontSize: 11, color: "#94A3B8" }}>已完成的微操交易</span>
          </div>
        </div>
      </div>

      {/* 3. 中部：月度微操趋势图 + 券商渠道收益排行 */}
      <div className="charts-split-section" style={{ display: "grid", gridTemplateColumns: "58% calc(42% - 12px)", gap: 12 }}>
        {/* 左侧：月度微操收益趋势 */}
        <div className="chart-card" style={{ background: "#FFF", borderRadius: 8, border: "1px solid #E2E8F0", padding: "12px 16px", height: 260, display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "#0F172A" }}>月度微操收益趋势</h3>
            <div style={{ display: "flex", background: "#F1F5F9", borderRadius: 6, padding: 2 }}>
              <button
                onClick={() => setTrendMode("monthly")}
                style={{
                  border: "none",
                  padding: "3px 10px",
                  borderRadius: 4,
                  fontSize: 12,
                  cursor: "pointer",
                  fontWeight: 600,
                  background: trendMode === "monthly" ? "#2563EB" : "transparent",
                  color: trendMode === "monthly" ? "#FFF" : "#64748B",
                }}
              >
                月度收益
              </button>
              <button
                onClick={() => setTrendMode("cum")}
                style={{
                  border: "none",
                  padding: "3px 10px",
                  borderRadius: 4,
                  fontSize: 12,
                  cursor: "pointer",
                  fontWeight: 600,
                  background: trendMode === "cum" ? "#2563EB" : "transparent",
                  color: trendMode === "cum" ? "#FFF" : "#64748B",
                }}
              >
                累计收益
              </button>
            </div>
          </div>
          <div style={{ flex: 1, minHeight: 0 }}>
            <ReactECharts
              option={chartOption}
              style={{ width: "100%", height: "100%" }}
              onEvents={{
                click: (params: any) => {
                  if (params && params.name) {
                    setActiveMonth((cur) => (cur === params.name ? "" : params.name));
                  }
                },
              }}
            />
          </div>
        </div>

        {/* 右侧：券商渠道微操收益排行 */}
        <div className="rankings-card" style={{ background: "#FFF", borderRadius: 8, border: "1px solid #E2E8F0", padding: "12px 16px", height: 260, display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "#0F172A" }}>券商渠道微操收益排行</h3>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "80px 80px 1fr 50px", fontSize: 11, color: "#94A3B8", fontWeight: 600, borderBottom: "1px solid #F1F5F9", paddingBottom: 4 }}>
            <span>券商渠道</span>
            <span style={{ textAlign: "right" }}>微操收益</span>
            <span style={{ paddingLeft: 12 }}>微操收益</span>
            <span style={{ textAlign: "right" }}>占比</span>
          </div>
          <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 6, paddingTop: 6 }}>
            {brokerRankings.map((b) => (
              <div
                key={b.name}
                onClick={() => {
                  const next = selectedBroker === b.name ? "" : b.name;
                  setSelectedBroker(next);
                  if (onBrokerChange) onBrokerChange(next);
                }}
                style={{
                  display: "grid",
                  gridTemplateColumns: "80px 80px 1fr 50px",
                  alignItems: "center",
                  padding: "4px 6px",
                  borderRadius: 4,
                  cursor: "pointer",
                  background: selectedBroker === b.name ? "#EFF6FF" : "#FFF",
                  border: selectedBroker === b.name ? "1px solid #BFDBFE" : "1px solid #F8FAFC",
                }}
              >
                <span style={{ fontSize: 12, fontWeight: 600, color: "#1E293B" }}>{b.name}</span>
                <span style={{ fontSize: 12, fontWeight: 700, textAlign: "right", color: b.pnl >= 0 ? "#10B981" : "#EF4444" }}>
                  {fmtSignedMoney(b.pnl, 2)}
                </span>
                <div style={{ padding: "0 12px" }}>
                  <div style={{ height: 8, borderRadius: 4, background: b.pnl >= 0 ? "#DCFCE7" : "#FEE2E2", overflow: "hidden" }}>
                    <div style={{ height: "100%", width: `${Math.max(5, Math.min(100, b.ratio))}%`, background: b.pnl >= 0 ? "#10B981" : "#EF4444", borderRadius: 4 }} />
                  </div>
                </div>
                <span style={{ fontSize: 11, color: "#64748B", textAlign: "right" }}>{b.share.toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 4. 股票微操收益排行榜：盈利 TOP 5 & 亏损 TOP 5 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {/* 盈利股票 TOP 5 */}
        <div style={{ background: "#FFF", borderRadius: 8, border: "1px solid #E2E8F0", padding: "12px 16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
            <span style={{ fontSize: 15 }}>🏆</span>
            <h3 style={{ margin: 0, fontSize: 13.5, fontWeight: 700, color: "#10B981" }}>盈利股票 TOP 5</h3>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "24px 75px 85px 60px 85px 85px", fontSize: 11, color: "#94A3B8", fontWeight: 600, borderBottom: "1px solid #F1F5F9", paddingBottom: 4 }}>
            <span>#</span>
            <span>Ticker</span>
            <span style={{ textAlign: "right" }}>微操收益</span>
            <span style={{ textAlign: "right" }}>微操次数</span>
            <span style={{ textAlign: "right" }}>买入金额</span>
            <span style={{ textAlign: "right" }}>卖出金额</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, paddingTop: 4 }}>
            {profitTop5.map((s, idx) => (
              <div
                key={s.sym}
                onClick={() => setTickerQuery((cur) => (cur === s.sym ? "" : s.sym))}
                style={{
                  display: "grid",
                  gridTemplateColumns: "24px 75px 85px 60px 85px 85px",
                  alignItems: "center",
                  padding: "4px 4px",
                  borderRadius: 4,
                  cursor: "pointer",
                  background: tickerQuery === s.sym ? "#EFF6FF" : "#FFF",
                  border: tickerQuery === s.sym ? "1px solid #BFDBFE" : "1px solid transparent",
                }}
              >
                <span style={{ fontSize: 11, fontWeight: 700, color: idx < 3 ? "#D97706" : "#94A3B8" }}>{idx + 1}</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: "#2563EB" }}>{s.sym}</span>
                <span style={{ fontSize: 12, fontWeight: 700, textAlign: "right", color: "#10B981" }}>+{fmtMoney(s.pnl, 2)}</span>
                <span style={{ fontSize: 11, color: "#64748B", textAlign: "right" }}>{s.trades}</span>
                <span style={{ fontSize: 11, color: "#475569", textAlign: "right" }}>${fmtMoney(s.buy, 2)}</span>
                <span style={{ fontSize: 11, color: "#475569", textAlign: "right" }}>${fmtMoney(s.sell, 2)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 亏损股票 TOP 5 */}
        <div style={{ background: "#FFF", borderRadius: 8, border: "1px solid #E2E8F0", padding: "12px 16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
            <span style={{ fontSize: 15 }}>🎯</span>
            <h3 style={{ margin: 0, fontSize: 13.5, fontWeight: 700, color: "#EF4444" }}>亏损股票 TOP 5</h3>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "24px 75px 85px 60px 85px 85px", fontSize: 11, color: "#94A3B8", fontWeight: 600, borderBottom: "1px solid #F1F5F9", paddingBottom: 4 }}>
            <span>#</span>
            <span>Ticker</span>
            <span style={{ textAlign: "right" }}>微操收益</span>
            <span style={{ textAlign: "right" }}>微操次数</span>
            <span style={{ textAlign: "right" }}>买入金额</span>
            <span style={{ textAlign: "right" }}>卖出金额</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, paddingTop: 4 }}>
            {lossTop5.map((s, idx) => (
              <div
                key={s.sym}
                onClick={() => setTickerQuery((cur) => (cur === s.sym ? "" : s.sym))}
                style={{
                  display: "grid",
                  gridTemplateColumns: "24px 75px 85px 60px 85px 85px",
                  alignItems: "center",
                  padding: "4px 4px",
                  borderRadius: 4,
                  cursor: "pointer",
                  background: tickerQuery === s.sym ? "#FEF2F2" : "#FFF",
                  border: tickerQuery === s.sym ? "1px solid #FECACA" : "1px solid transparent",
                }}
              >
                <span style={{ fontSize: 11, fontWeight: 700, color: "#94A3B8" }}>{idx + 1}</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: "#2563EB" }}>{s.sym}</span>
                <span style={{ fontSize: 12, fontWeight: 700, textAlign: "right", color: "#EF4444" }}>{fmtMoney(s.pnl, 2)}</span>
                <span style={{ fontSize: 11, color: "#64748B", textAlign: "right" }}>{s.trades}</span>
                <span style={{ fontSize: 11, color: "#475569", textAlign: "right" }}>${fmtMoney(s.buy, 2)}</span>
                <span style={{ fontSize: 11, color: "#475569", textAlign: "right" }}>${fmtMoney(s.sell, 2)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 5. 微操收益明细表格 */}
      <div className="table-card" style={{ background: "#FFF", borderRadius: 8, border: "1px solid #E2E8F0", padding: "14px 16px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#0F172A" }}>微操收益明细</h3>
            <span style={{ fontSize: 12, color: "#64748B" }}>共 {sortedTableRows.length} 条记录</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 12, color: "#64748B" }}>每页 {pageSize} 条</span>
            <div style={{ display: "flex", gap: 4 }}>
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                style={{ width: 28, height: 28, borderRadius: 4, border: "1px solid #CBD5E1", background: "#FFF", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}
              >
                <ChevronLeft size={14} />
              </button>
              <span style={{ fontSize: 12, padding: "4px 8px", color: "#1E293B", fontWeight: 600 }}>
                {page} / {totalPages}
              </span>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                style={{ width: 28, height: 28, borderRadius: 4, border: "1px solid #CBD5E1", background: "#FFF", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}
              >
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table className="data-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ background: "#F8FAFC", borderBottom: "1px solid #E2E8F0", color: "#64748B", height: 34 }}>
                <th style={{ padding: "6px 8px", textAlign: "left", cursor: "pointer" }} onClick={() => { setSortCol(0); setSortAsc(!sortAsc); }}>
                  月份 {sortCol === 0 ? (sortAsc ? "↑" : "↓") : ""}
                </th>
                <th style={{ padding: "6px 8px", textAlign: "left" }}>券商渠道</th>
                <th style={{ padding: "6px 8px", textAlign: "left" }}>分类</th>
                <th style={{ padding: "6px 8px", textAlign: "left" }}>Ticker</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>微操次数</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>微操数量</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>微操均买价($)</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>微操均卖价($)</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>每股价差($)</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>买入金额($)</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>卖出金额($)</th>
                <th style={{ padding: "6px 8px", textAlign: "right", cursor: "pointer" }} onClick={() => { setSortCol(11); setSortAsc(!sortAsc); }}>
                  微操收益($) {sortCol === 11 ? (sortAsc ? "↑" : "↓") : ""}
                </th>
                <th style={{ padding: "6px 8px", textAlign: "left" }}>首笔微操时间</th>
                <th style={{ padding: "6px 8px", textAlign: "left" }}>最近微操时间</th>
              </tr>
            </thead>
            <tbody>
              {pagedRows.map((r, i) => {
                const pnl = Number(r[11] || 0);
                const spread = Number(r[8] || 0);
                const isSelected = activeDrawerRow && activeDrawerRow === r;

                return (
                  <tr
                    key={i}
                    onClick={() => setActiveDrawerRow(r)}
                    onDoubleClick={() => setReplayStock(String(r[3] || ""))}
                    style={{
                      borderBottom: "1px solid #F1F5F9",
                      height: 38,
                      cursor: "pointer",
                      background: isSelected ? "#F0FDF4" : undefined,
                    }}
                  >
                    <td style={{ padding: "6px 8px", fontWeight: 600 }}>{r[0]}</td>
                    <td style={{ padding: "6px 8px" }}>{fmtBrokerCn(r[1])}</td>
                    <td style={{ padding: "6px 8px" }}>{r[2]}</td>
                    <td style={{ padding: "6px 8px", fontWeight: 700, color: "#2563EB" }}>{r[3]}</td>
                    <td style={{ padding: "6px 8px", textAlign: "right" }}>{r[4]}</td>
                    <td style={{ padding: "6px 8px", textAlign: "right" }}>{fmtMoney(r[5], 2)}</td>
                    <td style={{ padding: "6px 8px", textAlign: "right" }}>${fmtMoney(r[6], 2)}</td>
                    <td style={{ padding: "6px 8px", textAlign: "right" }}>${fmtMoney(r[7], 2)}</td>
                    <td style={{ padding: "6px 8px", textAlign: "right", fontWeight: 600, color: spread >= 0 ? "#10B981" : "#EF4444" }}>
                      {fmtSignedMoney(spread, 2)}
                    </td>
                    <td style={{ padding: "6px 8px", textAlign: "right" }}>${fmtMoney(r[9], 2)}</td>
                    <td style={{ padding: "6px 8px", textAlign: "right" }}>${fmtMoney(r[10], 2)}</td>
                    <td style={{ padding: "6px 8px", textAlign: "right", fontWeight: 700, color: pnl >= 0 ? "#10B981" : "#EF4444" }}>
                      {fmtSignedMoney(pnl, 2)}
                    </td>
                    <td style={{ padding: "6px 8px", color: "#64748B", fontSize: 11 }}>{r[12] || "—"}</td>
                    <td style={{ padding: "6px 8px", color: "#64748B", fontSize: 11 }}>{r[13] || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* 6. 右侧详情抽屉 (点击空白遮罩关闭) */}
      {activeDrawerRow && (
        <div
          className="detail-drawer-overlay"
          onClick={() => setActiveDrawerRow(null)}
        >
          <div
            className="detail-drawer"
            style={{ width: 440 }}
            onClick={(e) => e.stopPropagation()} // 阻止内部点击冒泡
          >
            <div className="drawer-header">
              <div>
                <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "#0F172A" }}>
                  微操详情 · {activeDrawerRow[3]}
                </h3>
                <p style={{ margin: "2px 0 0", fontSize: 12, color: "#64748B" }}>
                  {activeDrawerRow[0]} · {fmtBrokerCn(activeDrawerRow[1])} · {activeDrawerRow[2]}
                </p>
              </div>
              <button className="drawer-close-btn" onClick={() => setActiveDrawerRow(null)}>
                <X size={16} />
              </button>
            </div>

            <div className="drawer-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {/* 微操核心结果卡片 */}
              <div style={{ background: "#F8FAFC", borderRadius: 8, padding: 14, border: "1px solid #E2E8F0" }}>
                <span style={{ fontSize: 12, color: "#64748B" }}>微操收益</span>
                <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4, color: Number(activeDrawerRow[11] || 0) >= 0 ? "#10B981" : "#EF4444" }}>
                  {fmtSignedMoney(activeDrawerRow[11], 2)} USD
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 12 }}>
                  <span style={{ color: "#64748B" }}>每股价差:</span>
                  <span style={{ fontWeight: 600, color: Number(activeDrawerRow[8] || 0) >= 0 ? "#10B981" : "#EF4444" }}>
                    {fmtSignedMoney(activeDrawerRow[8], 2)} USD
                  </span>
                </div>
              </div>

              {/* 交易规模明细 */}
              <div style={{ background: "#FFF", borderRadius: 8, padding: 14, border: "1px solid #E2E8F0", display: "flex", flexDirection: "column", gap: 10 }}>
                <h4 style={{ margin: 0, fontSize: 13, fontWeight: 700, color: "#1E293B" }}>操作规模</h4>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ color: "#64748B" }}>微操次数</span>
                  <span style={{ fontWeight: 600 }}>{activeDrawerRow[4]} 次</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ color: "#64748B" }}>微操总股数</span>
                  <span style={{ fontWeight: 600 }}>{fmtMoney(activeDrawerRow[5], 2)} 股</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ color: "#64748B" }}>微操均买价</span>
                  <span style={{ fontWeight: 600 }}>${fmtMoney(activeDrawerRow[6], 2)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ color: "#64748B" }}>微操均卖价</span>
                  <span style={{ fontWeight: 600 }}>${fmtMoney(activeDrawerRow[7], 2)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ color: "#64748B" }}>买入金额</span>
                  <span style={{ fontWeight: 600 }}>${fmtMoney(activeDrawerRow[9], 2)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ color: "#64748B" }}>卖出金额</span>
                  <span style={{ fontWeight: 600 }}>${fmtMoney(activeDrawerRow[10], 2)}</span>
                </div>
              </div>

              {/* 时间记录 */}
              <div style={{ background: "#FFF", borderRadius: 8, padding: 14, border: "1px solid #E2E8F0", display: "flex", flexDirection: "column", gap: 10 }}>
                <h4 style={{ margin: 0, fontSize: 13, fontWeight: 700, color: "#1E293B" }}>时间追溯</h4>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ color: "#64748B" }}>首笔微操时间</span>
                  <span style={{ color: "#475569" }}>{activeDrawerRow[12] || "—"}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ color: "#64748B" }}>最近微操时间</span>
                  <span style={{ color: "#475569" }}>{activeDrawerRow[13] || "—"}</span>
                </div>
              </div>

              {/* 关联交易回放入口 */}
              <button
                onClick={() => setReplayStock(String(activeDrawerRow[3]))}
                style={{
                  marginTop: "auto",
                  padding: "10px",
                  borderRadius: 6,
                  border: "1px solid #2563EB",
                  background: "#EFF6FF",
                  color: "#2563EB",
                  fontWeight: 600,
                  fontSize: 13,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 6,
                }}
              >
                <History size={16} />
                <span>查看 {activeDrawerRow[3]} 原始交易回放与明细</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 标的交易回放抽屉 */}
      {replayStock && (
        <StockDrawer
          ticker={replayStock}
          personId={personId}
          broker={selectedBroker}
          onClose={() => setReplayStock(null)}
        />
      )}
    </div>
  );
};
