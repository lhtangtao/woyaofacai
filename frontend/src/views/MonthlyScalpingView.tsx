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
  ArrowDownRight,
  ArrowLeftRight,
  Scale,
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
  if (Math.abs(num) < 1e-9) return `$0.${"0".repeat(digits)}`;
  const sign = num > 0 ? "+" : "-";
  return `${sign}$${Math.abs(num).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

// 加权平均每股价差 = Σ(每股价差 × 微操数量) / Σ(微操数量)
function calcWeightedSpread(rows: any[][]): { val: number | null; str: string } {
  let totalSpreadQty = 0;
  let totalQty = 0;
  for (const r of rows) {
    const qty = Number(r[5] || 0);
    const spread = Number(r[8] || 0);
    if (qty > 0 && !isNaN(spread) && !isNaN(qty)) {
      totalSpreadQty += spread * qty;
      totalQty += qty;
    }
  }
  if (totalQty > 0) {
    const val = totalSpreadQty / totalQty;
    return { val, str: fmtSignedMoney(val, 2) };
  }
  return { val: null, str: "—" };
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

  // 待查询输入条件 (UI Controls)
  const [uiPerson, setUiPerson] = useState<number | null>(personId);
  const [uiBroker, setUiBroker] = useState<string>(externalBroker || "");
  const [uiCategory, setUiCategory] = useState<string>("");
  const [uiTicker, setUiTicker] = useState<string>("");
  const [uiStartMonth, setUiStartMonth] = useState<string>("");
  const [uiEndMonth, setUiEndMonth] = useState<string>("");

  // 已生效全局筛选条件 (Applied Scope)
  const [appliedBroker, setAppliedBroker] = useState<string>(externalBroker || "");
  const [appliedCategory, setAppliedCategory] = useState<string>("");
  const [appliedTicker, setAppliedTicker] = useState<string>("");
  const [appliedStartMonth, setAppliedStartMonth] = useState<string>("");
  const [appliedEndMonth, setAppliedEndMonth] = useState<string>("");

  // 交互下钻反选状态 (不缩短全局日期轴)
  const [activeMonth, setActiveMonth] = useState<string>("");
  const [activeRankingBroker, setActiveRankingBroker] = useState<string>("");
  const [activeRankingTicker, setActiveRankingTicker] = useState<string>("");

  const [trendMode, setTrendMode] = useState<"monthly" | "cum">("monthly");

  // 抽屉与回放状态
  const [activeDrawerRow, setActiveDrawerRow] = useState<any[] | null>(null);
  const [replayStock, setReplayStock] = useState<string | null>(null);

  // 表格分页与排序
  const [page, setPage] = useState<number>(1);
  const [pageSize, setPageSize] = useState<number>(50);
  const [sortCol, setSortCol] = useState<number>(0); // 0: 月份
  const [sortAsc, setSortAsc] = useState<boolean>(false); // 默认降序

  // 外部人员/券商变化同步
  useEffect(() => {
    setUiPerson(personId);
  }, [personId]);

  useEffect(() => {
    if (externalBroker !== undefined) {
      setUiBroker(externalBroker);
      setAppliedBroker(externalBroker);
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
        const months = Array.from(new Set(data.map((e) => String(e.values[0] || "")).filter(Boolean))).sort();
        if (months.length > 0) {
          if (!appliedStartMonth) {
            setUiStartMonth(months[0]);
            setAppliedStartMonth(months[0]);
          }
          if (!appliedEndMonth) {
            setUiEndMonth(months[months.length - 1]);
            setAppliedEndMonth(months[months.length - 1]);
          }
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

  // 全部可用券商、分类与月份选项
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

  const allMonths = useMemo(() => {
    const set = new Set<string>();
    entries.forEach((e) => {
      const m = String(e.values[0] || "").trim();
      if (m) set.add(m);
    });
    return Array.from(set).sort();
  }, [entries]);

  // 1. 全局筛选生效集合 (globalRows) - 趋势图完全基于该集合绘制，绝不因点击月份而缩短日期范围！
  const globalRows = useMemo(() => {
    return entries
      .map((e) => e.values)
      .filter((r) => {
        const m = String(r[0] || "");
        const b = fmtBrokerCn(r[1]);
        const cat = String(r[2] || "");
        const sym = String(r[3] || "").toUpperCase();

        if (appliedBroker && b !== appliedBroker) return false;
        if (appliedCategory && cat !== appliedCategory) return false;
        if (appliedTicker && !sym.includes(appliedTicker)) return false;
        if (appliedStartMonth && m < appliedStartMonth) return false;
        if (appliedEndMonth && m > appliedEndMonth) return false;

        return true;
      });
  }, [entries, appliedBroker, appliedCategory, appliedTicker, appliedStartMonth, appliedEndMonth]);

  // 2. 趋势图数据序列 (完整保留全局月份范围)
  const trendData = useMemo(() => {
    const monthMap: Record<string, number> = {};
    // 初始化区间内的全部月份
    allMonths
      .filter((m) => (!appliedStartMonth || m >= appliedStartMonth) && (!appliedEndMonth || m <= appliedEndMonth))
      .forEach((m) => {
        monthMap[m] = 0;
      });

    globalRows.forEach((r) => {
      const m = String(r[0] || "");
      if (monthMap[m] !== undefined) {
        monthMap[m] += Number(r[11] || 0);
      }
    });

    const sortedMonths = Object.keys(monthMap).sort();
    let cum = 0;
    return sortedMonths.map((m) => {
      const pnl = monthMap[m];
      cum += pnl;
      return { month: m, pnl, cum };
    });
  }, [allMonths, globalRows, appliedStartMonth, appliedEndMonth]);

  // 3. 当前展示集合 (displayRows) - 受到趋势图点击月份、排行榜点击的下钻反选
  const displayRows = useMemo(() => {
    return globalRows.filter((r) => {
      const m = String(r[0] || "");
      const b = fmtBrokerCn(r[1]);
      const sym = String(r[3] || "").toUpperCase();

      if (activeMonth && m !== activeMonth) return false;
      if (activeRankingBroker && b !== activeRankingBroker) return false;
      if (activeRankingTicker && sym !== activeRankingTicker) return false;

      return true;
    });
  }, [globalRows, activeMonth, activeRankingBroker, activeRankingTicker]);

  // 4. 核心指标卡片统计 (基于 displayRows)
  const kpis = useMemo(() => {
    let totPnl = 0;
    let totTrades = 0;
    let totBuy = 0;
    let totSell = 0;
    const tickerSet = new Set<string>();

    displayRows.forEach((r) => {
      totPnl += Number(r[11] || 0);
      totTrades += Number(r[4] || 0);
      totBuy += Number(r[9] || 0);
      totSell += Number(r[10] || 0);
      if (r[3]) tickerSet.add(String(r[3]));
    });

    const weighted = calcWeightedSpread(displayRows);
    return {
      totPnl,
      totTrades,
      totBuy,
      totSell,
      weightedSpreadVal: weighted.val,
      weightedSpreadStr: weighted.str,
      stockCount: tickerSet.size,
    };
  }, [displayRows]);

  // 5. 券商渠道微操收益排行 (取消占比列，按最大绝对收益归一化)
  const brokerRankings = useMemo(() => {
    const map: Record<string, number> = {};
    displayRows.forEach((r) => {
      const b = fmtBrokerCn(r[1]);
      map[b] = (map[b] || 0) + Number(r[11] || 0);
    });

    const sorted = Object.entries(map).sort((a, b) => b[1] - a[1]);
    const maxAbs = Math.max(...sorted.map(([, val]) => Math.abs(val)), 1);

    return sorted.map(([name, pnl]) => ({
      name,
      pnl,
      ratio: (Math.abs(pnl) / maxAbs) * 100,
    }));
  }, [displayRows]);

  // 6. 股票微操收益 TOP 5 (列头：# | Ticker | 微操收益 | 微操次数 | 微操数量)
  const { profitTop5, lossTop5 } = useMemo(() => {
    const map: Record<string, { pnl: number; trades: number; qty: number }> = {};
    displayRows.forEach((r) => {
      const sym = String(r[3] || "");
      if (!map[sym]) map[sym] = { pnl: 0, trades: 0, qty: 0 };
      map[sym].pnl += Number(r[11] || 0);
      map[sym].trades += Number(r[4] || 0);
      map[sym].qty += Number(r[5] || 0);
    });

    const allStocks = Object.entries(map).map(([sym, stats]) => ({
      sym,
      ...stats,
    }));

    // 盈利 TOP 5 (收益降序，只展示 > 0)
    const profits = allStocks.filter((s) => s.pnl > 0).sort((a, b) => b.pnl - a.pnl).slice(0, 5);
    // 亏损 TOP 5 (收益升序，最亏损排在第一位，只展示 < 0)
    const losses = allStocks.filter((s) => s.pnl < 0).sort((a, b) => a.pnl - b.pnl).slice(0, 5);

    return { profitTop5: profits, lossTop5: losses };
  }, [displayRows]);

  // 7. 明细表格全字段排序与分页 (默认三级稳定排序：月份降序->收益降序->Ticker升序)
  const sortedTableRows = useMemo(() => {
    const list = [...displayRows];
    list.sort((a, b) => {
      // 默认排序
      if (sortCol === 0 && !sortAsc) {
        const ma = String(a[0] || "");
        const mb = String(b[0] || "");
        if (ma !== mb) return mb.localeCompare(ma);
        const pa = Number(a[11] || 0);
        const pb = Number(b[11] || 0);
        if (pa !== pb) return pb - pa;
        return String(a[3] || "").localeCompare(String(b[3] || ""));
      }

      // 针对指定列排序
      const va = a[sortCol];
      const vb = b[sortCol];

      // 数值列：4, 5, 6, 7, 8, 9, 10, 11
      if ([4, 5, 6, 7, 8, 9, 10, 11].includes(sortCol)) {
        const na = Number(va || 0);
        const nb = Number(vb || 0);
        return sortAsc ? na - nb : nb - na;
      }

      // 日期时间列：0, 12, 13
      if ([0, 12, 13].includes(sortCol)) {
        return sortAsc
          ? String(va || "").localeCompare(String(vb || ""))
          : String(vb || "").localeCompare(String(va || ""));
      }

      // 文本列：1, 2, 3
      return sortAsc
        ? String(va || "").localeCompare(String(vb || ""))
        : String(vb || "").localeCompare(String(va || ""));
    });
    return list;
  }, [displayRows, sortCol, sortAsc]);

  const totalPages = Math.ceil(sortedTableRows.length / pageSize) || 1;
  const pagedRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return sortedTableRows.slice(start, start + pageSize);
  }, [sortedTableRows, page, pageSize]);

  // 点击查询按钮正式应用筛选
  const handleQuery = () => {
    let s = uiStartMonth;
    let e = uiEndMonth;
    if (s && e && s > e) {
      const tmp = s;
      s = e;
      e = tmp;
      setUiStartMonth(s);
      setUiEndMonth(e);
    }

    if (onPersonChange && uiPerson !== personId) {
      onPersonChange(uiPerson);
    }
    setAppliedBroker(uiBroker);
    if (onBrokerChange) onBrokerChange(uiBroker);
    setAppliedCategory(uiCategory);
    setAppliedTicker(uiTicker.trim().toUpperCase());
    setAppliedStartMonth(s);
    setAppliedEndMonth(e);

    // 清除下钻状态
    setActiveMonth("");
    setActiveRankingBroker("");
    setActiveRankingTicker("");
    setPage(1);
  };

  // 点击重置恢复默认
  const handleReset = () => {
    setUiPerson(null);
    if (onPersonChange) onPersonChange(null);
    setUiBroker("");
    if (onBrokerChange) onBrokerChange("");
    setUiCategory("");
    setUiTicker("");

    const s = allMonths.length > 0 ? allMonths[0] : "";
    const e = allMonths.length > 0 ? allMonths[allMonths.length - 1] : "";
    setUiStartMonth(s);
    setUiEndMonth(e);

    setAppliedBroker("");
    setAppliedCategory("");
    setAppliedTicker("");
    setAppliedStartMonth(s);
    setAppliedEndMonth(e);

    setActiveMonth("");
    setActiveRankingBroker("");
    setActiveRankingTicker("");
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

  // 表头排序点击
  const handleHeaderSort = (colIndex: number) => {
    if (sortCol === colIndex) {
      setSortAsc(!sortAsc);
    } else {
      setSortCol(colIndex);
      // 数值及月份默认降序，其他默认升序
      setSortAsc(![0, 4, 5, 6, 7, 8, 9, 10, 11].includes(colIndex));
    }
    setPage(1);
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
            <div style="font-size:11px; color:#94A3B8; margin-top:4px;">点击柱体筛选该月份明细 (不会缩短日期范围)</div>
          `;
        },
      },
      grid: {
        top: 25,
        left: 55,
        right: 15,
        bottom: 25,
      },
      xAxis: {
        type: "category",
        data: xData,
        axisLine: { lineStyle: { color: "#CBD5E1" } },
        axisLabel: {
          color: (val: string) => (val === activeMonth ? "#2563EB" : "#64748B"),
          fontWeight: (val: string) => (val === activeMonth ? "bold" : "normal"),
          fontSize: 10.5,
          interval: 0,
          rotate: xData.length > 15 ? 35 : 0,
        },
      },
      yAxis: {
        type: "value",
        axisLine: { show: false },
        splitLine: { lineStyle: { color: "#F1F5F9", type: "dashed" } },
        axisLabel: {
          color: "#94A3B8",
          fontSize: 10.5,
          formatter: (v: number) => `$${fmtMoney(v, 0)}`,
        },
      },
      series: [
        {
          name: trendMode === "monthly" ? "月度收益" : "累计收益",
          type: "bar",
          data: yData,
          barMaxWidth: 20,
        },
      ],
    };
  }, [trendData, trendMode, activeMonth]);

  return (
    <div className="daily-perf-container" style={{ gap: 12 }}>
      {/* 顶部标题与操作栏 */}
      <div className="daily-perf-header" style={{ marginBottom: 0 }}>
        <div className="title-area">
          <h2>每月微操收益</h2>
          <p>分析主动交易的操作效果，比较不同月份、券商和股票的微操收益表现。</p>
        </div>
        <div className="header-actions" style={{ display: "flex", gap: 10 }}>
          <button
            className="primary-btn"
            onClick={handleExport}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 13,
              height: 32,
              padding: "0 14px",
              backgroundColor: "#2563EB",
              color: "#FFF",
              borderRadius: 6,
              border: "none",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            <Download size={14} />
            <span>导出 CSV</span>
          </button>
        </div>
      </div>

      {/* 1. 顶部全局筛选卡片 (待查询与已生效分离) */}
      <div
        className="filter-card"
        style={{
          padding: "8px 12px",
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 8,
          background: "#FFF",
          borderRadius: 6,
          border: "1px solid #E2E8F0",
        }}
      >
        {/* 归属人员 */}
        <select
          value={uiPerson ?? ""}
          onChange={(e) => setUiPerson(e.target.value === "" ? null : Number(e.target.value))}
          style={{ height: 32, padding: "0 8px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 12.5, background: "#FFF", cursor: "pointer" }}
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
          value={uiBroker}
          onChange={(e) => setUiBroker(e.target.value)}
          style={{ height: 32, padding: "0 8px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 12.5, background: "#FFF", cursor: "pointer" }}
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
          value={uiCategory}
          onChange={(e) => setUiCategory(e.target.value)}
          style={{ height: 32, padding: "0 8px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 12.5, background: "#FFF", cursor: "pointer" }}
        >
          <option value="">全部分类</option>
          {availableCategories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>

        {/* 输入 Ticker */}
        <div style={{ position: "relative", width: 120 }}>
          <input
            type="text"
            placeholder="输入股票代码"
            value={uiTicker}
            onChange={(e) => setUiTicker(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleQuery()}
            style={{ width: "100%", height: 32, padding: "0 8px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 12.5, boxSizing: "border-box" }}
          />
        </div>

        {/* 开始月份 */}
        <select
          value={uiStartMonth}
          onChange={(e) => setUiStartMonth(e.target.value)}
          style={{ height: 32, padding: "0 8px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 12, background: "#FFF" }}
        >
          {allMonths.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>

        <span style={{ color: "#64748B", fontSize: 12 }}>至</span>

        {/* 结束月份 */}
        <select
          value={uiEndMonth}
          onChange={(e) => setUiEndMonth(e.target.value)}
          style={{ height: 32, padding: "0 8px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 12, background: "#FFF" }}
        >
          {allMonths.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>

        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button
            onClick={handleReset}
            style={{ height: 32, padding: "0 14px", borderRadius: 6, background: "#F1F5F9", color: "#475569", fontWeight: 600, fontSize: 12.5, border: "1px solid #CBD5E1", cursor: "pointer" }}
          >
            重置
          </button>
          <button
            onClick={handleQuery}
            style={{ height: 32, padding: "0 18px", borderRadius: 6, background: "#2563EB", color: "#FFF", fontWeight: 600, fontSize: 12.5, border: "none", cursor: "pointer" }}
          >
            查询
          </button>
        </div>
      </div>

      {/* 2.5 筛选状态信息条 (Breadcrumb Status Bar V2.0) */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 4px", minHeight: 28, fontSize: 12, color: "#64748B" }}>
        <span>
          当前人员: <strong>{persons.find((p) => p.id === personId)?.name || "全部人员"}</strong>
        </span>
        <span>|</span>
        <span>
          渠道: <strong>{appliedBroker || "全部渠道"}</strong>
        </span>
        <span>|</span>
        <span>
          日期: <strong>{appliedStartMonth && appliedEndMonth ? `${appliedStartMonth} 至 ${appliedEndMonth}` : "全周期"}</strong>
        </span>

        {/* 月份下钻胶囊 */}
        {activeMonth && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, background: "#EFF6FF", padding: "2px 8px", borderRadius: 12, border: "1px solid #BFDBFE" }}>
            <span style={{ fontSize: 11.5, color: "#1D4ED8", fontWeight: 600 }}>月份: {activeMonth}</span>
            <X size={13} style={{ cursor: "pointer", color: "#2563EB" }} onClick={() => setActiveMonth("")} />
          </div>
        )}

        {/* 券商下钻胶囊 */}
        {activeRankingBroker && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, background: "#EFF6FF", padding: "2px 8px", borderRadius: 12, border: "1px solid #BFDBFE" }}>
            <span style={{ fontSize: 11.5, color: "#1D4ED8", fontWeight: 600 }}>券商: {activeRankingBroker}</span>
            <X size={13} style={{ cursor: "pointer", color: "#2563EB" }} onClick={() => setActiveRankingBroker("")} />
          </div>
        )}

        {/* 股票下钻胶囊 */}
        {activeRankingTicker && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, background: "#EFF6FF", padding: "2px 8px", borderRadius: 12, border: "1px solid #BFDBFE" }}>
            <span style={{ fontSize: 11.5, color: "#1D4ED8", fontWeight: 600 }}>股票: {activeRankingTicker}</span>
            <X size={13} style={{ cursor: "pointer", color: "#2563EB" }} onClick={() => setActiveRankingTicker("")} />
          </div>
        )}

        {activeMonth && (
          <button
            onClick={() => setActiveMonth("")}
            style={{ border: "none", background: "transparent", color: "#64748B", fontSize: 11.5, textDecoration: "underline", cursor: "pointer", padding: 0 }}
          >
            取消月份筛选
          </button>
        )}
      </div>

      {/* 2. 核心指标卡片 (5张横向并排，高度约 82px，现代线性图标无 emoji) */}
      <div className="stat-cards-grid" style={{ gridTemplateColumns: "repeat(5, 1fr)", gap: 10 }}>
        {/* 微操收益合计 */}
        <div className="stat-card" style={{ height: 82, padding: "10px 12px", display: "flex", alignItems: "center", gap: 10, background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0" }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, background: "#ECFDF5", color: "#10B981", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <TrendingUp size={18} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <span style={{ fontSize: 11, color: "#64748B", fontWeight: 500 }}>微操收益合计</span>
            <span style={{ fontSize: 18, fontWeight: 700, color: kpis.totPnl >= 0 ? "#10B981" : "#EF4444" }}>
              {fmtSignedMoney(kpis.totPnl, 2)}
            </span>
            <span style={{ fontSize: 10.5, color: "#94A3B8" }}>当前筛选范围 · 已实现收益</span>
          </div>
        </div>

        {/* 微操次数 */}
        <div className="stat-card" style={{ height: 82, padding: "10px 12px", display: "flex", alignItems: "center", gap: 10, background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0" }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, background: "#EFF6FF", color: "#2563EB", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <ArrowLeftRight size={18} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <span style={{ fontSize: 11, color: "#64748B", fontWeight: 500 }}>微操次数</span>
            <span style={{ fontSize: 18, fontWeight: 700, color: "#0F172A" }}>
              {kpis.totTrades}
            </span>
            <span style={{ fontSize: 10.5, color: "#94A3B8" }}>涉及 {kpis.stockCount} 只股票</span>
          </div>
        </div>

        {/* 买入金额合计 */}
        <div className="stat-card" style={{ height: 82, padding: "10px 12px", display: "flex", alignItems: "center", gap: 10, background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0" }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, background: "#EFF6FF", color: "#2563EB", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <ArrowUpRight size={18} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <span style={{ fontSize: 11, color: "#64748B", fontWeight: 500 }}>买入金额合计</span>
            <span style={{ fontSize: 18, fontWeight: 700, color: "#0F172A" }}>
              ${fmtMoney(kpis.totBuy, 2)}
            </span>
            <span style={{ fontSize: 10.5, color: "#94A3B8" }}>累计微操买入</span>
          </div>
        </div>

        {/* 卖出金额合计 */}
        <div className="stat-card" style={{ height: 82, padding: "10px 12px", display: "flex", alignItems: "center", gap: 10, background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0" }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, background: "#EFF6FF", color: "#2563EB", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <ArrowDownRight size={18} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <span style={{ fontSize: 11, color: "#64748B", fontWeight: 500 }}>卖出金额合计</span>
            <span style={{ fontSize: 18, fontWeight: 700, color: "#0F172A" }}>
              ${fmtMoney(kpis.totSell, 2)}
            </span>
            <span style={{ fontSize: 10.5, color: "#94A3B8" }}>累计微操卖出</span>
          </div>
        </div>

        {/* 加权平均每股价差 */}
        <div className="stat-card" style={{ height: 82, padding: "10px 12px", display: "flex", alignItems: "center", gap: 10, background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0" }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, background: "#EFF6FF", color: "#2563EB", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Scale size={18} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <span style={{ fontSize: 11, color: "#64748B", fontWeight: 500 }}>加权平均每股价差</span>
            <span style={{ fontSize: 18, fontWeight: 700, color: (kpis.weightedSpreadVal ?? 0) >= 0 ? "#10B981" : "#EF4444" }}>
              {kpis.weightedSpreadStr}
            </span>
            <span style={{ fontSize: 10.5, color: "#94A3B8" }}>按微操数量加权</span>
          </div>
        </div>
      </div>

      {/* 3. 中部：月度微操趋势图 + 券商渠道收益排行 (高度约 230px) */}
      <div className="charts-split-section" style={{ display: "grid", gridTemplateColumns: "58% calc(42% - 10px)", gap: 10 }}>
        {/* 左侧：月度微操收益趋势 */}
        <div className="chart-card" style={{ background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0", padding: "10px 14px", height: 230, display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, color: "#0F172A" }}>月度微操收益趋势</h3>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "#64748B" }}>
                <span style={{ display: "inline-block", width: 8, height: 8, backgroundColor: "#10B981", borderRadius: 1 }}></span>
                <span>盈利</span>
                <span style={{ display: "inline-block", width: 8, height: 8, backgroundColor: "#EF4444", borderRadius: 1, marginLeft: 4 }}></span>
                <span>亏损</span>
                <span style={{ color: "#94A3B8", fontSize: 10.5, marginLeft: 6 }}>点击月份柱体筛选明细（不会缩短日期范围）</span>
              </div>
            </div>
            <div style={{ display: "flex", background: "#F1F5F9", borderRadius: 4, padding: 2 }}>
              <button
                onClick={() => setTrendMode("monthly")}
                style={{
                  border: "none",
                  padding: "3px 8px",
                  borderRadius: 3,
                  fontSize: 11,
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
                  padding: "3px 8px",
                  borderRadius: 3,
                  fontSize: 11,
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

        {/* 右侧：券商渠道微操收益排行 (取消占比列) */}
        <div className="rankings-card" style={{ background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0", padding: "10px 14px", height: 230, display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, color: "#0F172A" }}>券商渠道微操收益排行</h3>
            <span style={{ fontSize: 10.5, color: "#94A3B8" }}>按收益从高到低；条形长度相对最大单渠道绝对收益。</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "75px 85px 1fr", fontSize: 11, color: "#94A3B8", fontWeight: 600, borderBottom: "1px solid #F1F5F9", paddingBottom: 4 }}>
            <span>券商渠道</span>
            <span style={{ textAlign: "right" }}>微操收益</span>
            <span style={{ paddingLeft: 12 }}>微操收益</span>
          </div>
          <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4, paddingTop: 4 }}>
            {brokerRankings.map((b) => (
              <div
                key={b.name}
                onClick={() => setActiveRankingBroker((cur) => (cur === b.name ? "" : b.name))}
                style={{
                  display: "grid",
                  gridTemplateColumns: "75px 85px 1fr",
                  alignItems: "center",
                  padding: "4px 6px",
                  borderRadius: 4,
                  cursor: "pointer",
                  background: activeRankingBroker === b.name ? "#EFF6FF" : "#FFF",
                  border: activeRankingBroker === b.name ? "1px solid #BFDBFE" : "1px solid #F8FAFC",
                }}
              >
                <span style={{ fontSize: 12, fontWeight: 600, color: "#1E293B" }}>{b.name}</span>
                <span style={{ fontSize: 11.5, fontWeight: 700, textAlign: "right", color: b.pnl >= 0 ? "#10B981" : "#EF4444" }}>
                  {fmtSignedMoney(b.pnl, 2)}
                </span>
                <div style={{ padding: "0 12px" }}>
                  <div style={{ height: 8, borderRadius: 4, background: b.pnl >= 0 ? "#DCFCE7" : "#FEE2E2", overflow: "hidden" }}>
                    <div style={{ height: "100%", width: `${Math.max(6, Math.min(100, b.ratio))}%`, background: b.pnl >= 0 ? "#10B981" : "#EF4444", borderRadius: 4 }} />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 4. 股票微操收益排行榜：盈利 TOP 5 & 亏损 TOP 5 (列头：# | Ticker | 微操收益 | 微操次数 | 微操数量) */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        {/* 盈利股票 TOP 5 */}
        <div style={{ background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0", padding: "10px 14px", height: 195, display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
            <h3 style={{ margin: 0, fontSize: 12.5, fontWeight: 700, color: "#10B981" }}>盈利股票 TOP 5</h3>
            <span style={{ background: "#ECFDF5", color: "#10B981", fontSize: 10, fontWeight: 600, padding: "1px 6px", borderRadius: 4 }}>收益最高</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "24px 75px 85px 70px 1fr", fontSize: 11, color: "#94A3B8", fontWeight: 600, borderBottom: "1px solid #F1F5F9", paddingBottom: 4 }}>
            <span>#</span>
            <span>Ticker</span>
            <span style={{ textAlign: "right" }}>微操收益</span>
            <span style={{ textAlign: "right" }}>微操次数</span>
            <span style={{ textAlign: "right" }}>微操数量</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3, paddingTop: 4, flex: 1, overflowY: "auto" }}>
            {profitTop5.map((s, idx) => (
              <div
                key={s.sym}
                onClick={() => setActiveRankingTicker((cur) => (cur === s.sym ? "" : s.sym))}
                style={{
                  display: "grid",
                  gridTemplateColumns: "24px 75px 85px 70px 1fr",
                  alignItems: "center",
                  padding: "3px 4px",
                  borderRadius: 4,
                  cursor: "pointer",
                  background: activeRankingTicker === s.sym ? "#EFF6FF" : "#FFF",
                  border: activeRankingTicker === s.sym ? "1px solid #BFDBFE" : "1px solid transparent",
                }}
              >
                <span style={{ fontSize: 10.5, fontWeight: 700, color: idx < 3 ? "#10B981" : "#94A3B8" }}>{idx + 1}</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: "#2563EB" }}>{s.sym}</span>
                <span style={{ fontSize: 11.5, fontWeight: 700, textAlign: "right", color: "#10B981" }}>{fmtSignedMoney(s.pnl, 2)}</span>
                <span style={{ fontSize: 11, color: "#64748B", textAlign: "right" }}>{s.trades}</span>
                <span style={{ fontSize: 11, color: "#475569", textAlign: "right" }}>{fmtMoney(s.qty, 2)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 亏损股票 TOP 5 */}
        <div style={{ background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0", padding: "10px 14px", height: 195, display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
            <h3 style={{ margin: 0, fontSize: 12.5, fontWeight: 700, color: "#EF4444" }}>亏损股票 TOP 5</h3>
            <span style={{ background: "#FEF2F2", color: "#EF4444", fontSize: 10, fontWeight: 600, padding: "1px 6px", borderRadius: 4 }}>亏损最高</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "24px 75px 85px 70px 1fr", fontSize: 11, color: "#94A3B8", fontWeight: 600, borderBottom: "1px solid #F1F5F9", paddingBottom: 4 }}>
            <span>#</span>
            <span>Ticker</span>
            <span style={{ textAlign: "right" }}>微操收益</span>
            <span style={{ textAlign: "right" }}>微操次数</span>
            <span style={{ textAlign: "right" }}>微操数量</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3, paddingTop: 4, flex: 1, overflowY: "auto" }}>
            {lossTop5.map((s, idx) => (
              <div
                key={s.sym}
                onClick={() => setActiveRankingTicker((cur) => (cur === s.sym ? "" : s.sym))}
                style={{
                  display: "grid",
                  gridTemplateColumns: "24px 75px 85px 70px 1fr",
                  alignItems: "center",
                  padding: "3px 4px",
                  borderRadius: 4,
                  cursor: "pointer",
                  background: activeRankingTicker === s.sym ? "#FEF2F2" : "#FFF",
                  border: activeRankingTicker === s.sym ? "1px solid #FECACA" : "1px solid transparent",
                }}
              >
                <span style={{ fontSize: 10.5, fontWeight: 700, color: idx < 3 ? "#EF4444" : "#94A3B8" }}>{idx + 1}</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: "#2563EB" }}>{s.sym}</span>
                <span style={{ fontSize: 11.5, fontWeight: 700, textAlign: "right", color: "#EF4444" }}>{fmtSignedMoney(s.pnl, 2)}</span>
                <span style={{ fontSize: 11, color: "#64748B", textAlign: "right" }}>{s.trades}</span>
                <span style={{ fontSize: 11, color: "#475569", textAlign: "right" }}>{fmtMoney(s.qty, 2)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 5. 微操收益明细表格 (全列支持点击表头排序) */}
      <div className="table-card" style={{ background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0", padding: "12px 14px", minHeight: 360 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, color: "#0F172A" }}>微操收益明细</h3>
            <span style={{ fontSize: 11, color: "#64748B" }}>
              共 {sortedTableRows.length} 条记录 · 点击行查看详情，双击行进入交易回放
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11, color: "#64748B" }}>每页 {pageSize} 条</span>
            <div style={{ display: "flex", gap: 4 }}>
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                style={{ width: 26, height: 26, borderRadius: 4, border: "1px solid #CBD5E1", background: "#FFF", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}
              >
                <ChevronLeft size={13} />
              </button>
              <span style={{ fontSize: 11, padding: "3px 6px", color: "#1E293B", fontWeight: 600 }}>
                {page} / {totalPages}
              </span>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                style={{ width: 26, height: 26, borderRadius: 4, border: "1px solid #CBD5E1", background: "#FFF", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}
              >
                <ChevronRight size={13} />
              </button>
            </div>
          </div>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table className="data-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
            <thead>
              <tr style={{ background: "#F8FAFC", borderBottom: "1px solid #E2E8F0", color: "#64748B", height: 32 }}>
                {[
                  { name: "月份", align: "left", idx: 0 },
                  { name: "券商渠道", align: "left", idx: 1 },
                  { name: "分类", align: "left", idx: 2 },
                  { name: "Ticker", align: "left", idx: 3 },
                  { name: "微操次数", align: "right", idx: 4 },
                  { name: "微操数量", align: "right", idx: 5 },
                  { name: "微操均买价($)", align: "right", idx: 6 },
                  { name: "微操均卖价($)", align: "right", idx: 7 },
                  { name: "每股价差($)", align: "right", idx: 8 },
                  { name: "买入金额($)", align: "right", idx: 9 },
                  { name: "卖出金额($)", align: "right", idx: 10 },
                  { name: "微操收益($)", align: "right", idx: 11 },
                  { name: "首笔微操时间", align: "left", idx: 12 },
                  { name: "最近微操时间", align: "left", idx: 13 },
                ].map((th) => (
                  <th
                    key={th.name}
                    onClick={() => handleHeaderSort(th.idx)}
                    style={{
                      padding: "4px 8px",
                      textAlign: th.align as any,
                      cursor: "pointer",
                      userSelect: "none",
                      whiteSpace: "nowrap",
                    }}
                  >
                    <span>{th.name}</span>
                    {sortCol === th.idx && (
                      <span style={{ marginLeft: 3, color: "#2563EB" }}>
                        {sortAsc ? "▲" : "▼"}
                      </span>
                    )}
                  </th>
                ))}
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
                      height: 34,
                      cursor: "pointer",
                      background: isSelected ? "#EFF6FF" : undefined,
                    }}
                  >
                    <td style={{ padding: "4px 8px", fontWeight: 600 }}>{r[0]}</td>
                    <td style={{ padding: "4px 8px" }}>{fmtBrokerCn(r[1])}</td>
                    <td style={{ padding: "4px 8px" }}>{r[2]}</td>
                    <td style={{ padding: "4px 8px", fontWeight: 700, color: "#2563EB" }}>{r[3]}</td>
                    <td style={{ padding: "4px 8px", textAlign: "right" }}>{r[4]}</td>
                    <td style={{ padding: "4px 8px", textAlign: "right" }}>{fmtMoney(r[5], 2)}</td>
                    <td style={{ padding: "4px 8px", textAlign: "right" }}>${fmtMoney(r[6], 2)}</td>
                    <td style={{ padding: "4px 8px", textAlign: "right" }}>${fmtMoney(r[7], 2)}</td>
                    <td style={{ padding: "4px 8px", textAlign: "right", fontWeight: 600, color: spread >= 0 ? "#10B981" : "#EF4444" }}>
                      {fmtSignedMoney(spread, 2)}
                    </td>
                    <td style={{ padding: "4px 8px", textAlign: "right" }}>${fmtMoney(r[9], 2)}</td>
                    <td style={{ padding: "4px 8px", textAlign: "right" }}>${fmtMoney(r[10], 2)}</td>
                    <td style={{ padding: "4px 8px", textAlign: "right", fontWeight: 700, color: pnl >= 0 ? "#10B981" : "#EF4444" }}>
                      {fmtSignedMoney(pnl, 2)}
                    </td>
                    <td style={{ padding: "4px 8px", color: "#64748B", fontSize: 10.5 }}>{r[12] || "—"}</td>
                    <td style={{ padding: "4px 8px", color: "#64748B", fontSize: 10.5 }}>{r[13] || "—"}</td>
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
            style={{ width: 420 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="drawer-header">
              <div>
                <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#0F172A" }}>
                  微操详情 · {activeDrawerRow[3]}
                </h3>
                <p style={{ margin: "2px 0 0", fontSize: 11.5, color: "#64748B" }}>
                  {activeDrawerRow[0]} · {fmtBrokerCn(activeDrawerRow[1])} · {activeDrawerRow[2]}
                </p>
              </div>
              <button className="drawer-close-btn" onClick={() => setActiveDrawerRow(null)}>
                <X size={15} />
              </button>
            </div>

            <div className="drawer-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {/* 微操核心结果卡片 */}
              <div style={{ background: "#F8FAFC", borderRadius: 6, padding: 12, border: "1px solid #E2E8F0" }}>
                <span style={{ fontSize: 11.5, color: "#64748B" }}>微操收益</span>
                <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2, color: Number(activeDrawerRow[11] || 0) >= 0 ? "#10B981" : "#EF4444" }}>
                  {fmtSignedMoney(activeDrawerRow[11], 2)} USD
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6, fontSize: 11.5 }}>
                  <span style={{ color: "#64748B" }}>每股价差:</span>
                  <span style={{ fontWeight: 600, color: Number(activeDrawerRow[8] || 0) >= 0 ? "#10B981" : "#EF4444" }}>
                    {fmtSignedMoney(activeDrawerRow[8], 2)} USD
                  </span>
                </div>
              </div>

              {/* 交易规模明细 */}
              <div style={{ background: "#FFF", borderRadius: 6, padding: 12, border: "1px solid #E2E8F0", display: "flex", flexDirection: "column", gap: 8 }}>
                <h4 style={{ margin: 0, fontSize: 12.5, fontWeight: 700, color: "#1E293B" }}>操作规模</h4>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
                  <span style={{ color: "#64748B" }}>微操次数</span>
                  <span style={{ fontWeight: 600 }}>{activeDrawerRow[4]} 次</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
                  <span style={{ color: "#64748B" }}>微操总股数</span>
                  <span style={{ fontWeight: 600 }}>{fmtMoney(activeDrawerRow[5], 2)} 股</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
                  <span style={{ color: "#64748B" }}>微操均买价</span>
                  <span style={{ fontWeight: 600 }}>${fmtMoney(activeDrawerRow[6], 2)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
                  <span style={{ color: "#64748B" }}>微操均卖价</span>
                  <span style={{ fontWeight: 600 }}>${fmtMoney(activeDrawerRow[7], 2)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
                  <span style={{ color: "#64748B" }}>买入金额</span>
                  <span style={{ fontWeight: 600 }}>${fmtMoney(activeDrawerRow[9], 2)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
                  <span style={{ color: "#64748B" }}>卖出金额</span>
                  <span style={{ fontWeight: 600 }}>${fmtMoney(activeDrawerRow[10], 2)}</span>
                </div>
              </div>

              {/* 时间记录 */}
              <div style={{ background: "#FFF", borderRadius: 6, padding: 12, border: "1px solid #E2E8F0", display: "flex", flexDirection: "column", gap: 8 }}>
                <h4 style={{ margin: 0, fontSize: 12.5, fontWeight: 700, color: "#1E293B" }}>时间追溯</h4>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
                  <span style={{ color: "#64748B" }}>首笔微操时间</span>
                  <span style={{ color: "#475569" }}>{activeDrawerRow[12] || "—"}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
                  <span style={{ color: "#64748B" }}>最近微操时间</span>
                  <span style={{ color: "#475569" }}>{activeDrawerRow[13] || "—"}</span>
                </div>
              </div>

              {/* 关联交易回放入口 */}
              <button
                onClick={() => setReplayStock(String(activeDrawerRow[3]))}
                style={{
                  marginTop: "auto",
                  padding: "9px",
                  borderRadius: 6,
                  border: "1px solid #2563EB",
                  background: "#EFF6FF",
                  color: "#2563EB",
                  fontWeight: 600,
                  fontSize: 12.5,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 6,
                }}
              >
                <History size={15} />
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
          broker={appliedBroker}
          onClose={() => setReplayStock(null)}
        />
      )}
    </div>
  );
};
