import React, { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import {
  Calendar,
  Download,
  Info,
  TrendingDown,
  TrendingUp,
  X,
  ChevronLeft,
  ChevronRight,
  ArrowUpDown,
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

export const MonthlyScalpingView: React.FC<MonthlyScalpingViewProps> = ({
  personId,
  onPersonChange,
  broker: externalBroker,
  onBrokerChange,
  persons,
  refreshKey = 0,
}) => {
  const [entries, setEntries] = useState<ReportEntry[]>([]);
  const [unrealizedMap, setUnrealizedMap] = useState<Record<string, number>>({});
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
  const [activeRankingTicker, setActiveRankingTicker] = useState<string>("");

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

  // 加载微操收益数据与持仓数据 (提取浮动盈亏)
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    Promise.all([
      loadMonthlyReport("每月微操收益", personId, controller.signal),
      loadMonthlyReport("当前持仓", personId, controller.signal).catch(() => []),
    ])
      .then(([scalpingData, holdingsData]) => {
        setEntries(scalpingData);

        // 提取持仓浮动盈亏映射 (Ticker -> 持仓盈亏金额)
        const uMap: Record<string, number> = {};
        for (const h of holdingsData) {
          if (h.values && h.values.length > 10 && h.values[2]) {
            const sym = String(h.values[2]).toUpperCase();
            const pnl = Number(h.values[10]);
            if (!isNaN(pnl)) {
              uMap[sym] = (uMap[sym] || 0) + pnl;
            }
          }
        }
        setUnrealizedMap(uMap);

        const months = Array.from(new Set(scalpingData.map((e) => String(e.values[0] || "")).filter(Boolean))).sort();
        if (months.length > 0) {
          if (!appliedStartMonth) {
            setUiStartMonth(months[0]);
            setAppliedStartMonth(months[0]);
          }
          if (!appliedEndMonth) {
            setUiEndMonth(months[months.length - 1]);
            setAppliedEndMonth(months[months.length - 1]);
          }
          if (!activeMonth) {
            setActiveMonth(months[months.length - 1]); // 默认选中最新月份
          }
        }
        setLoading(false);
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          setError(err.message || "加载微操数据失败");
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [personId, refreshKey]);

  // 全量原始数据列表
  const rawRows = useMemo(() => entries.map((e) => e.values), [entries]);

  // 可选券商、分类与月份集合
  const availableBrokers = useMemo(() => {
    const set = new Set<string>();
    rawRows.forEach((r) => {
      const b = fmtBrokerCn(r[1]);
      if (b && b !== "—") set.add(b);
    });
    return Array.from(set).sort();
  }, [rawRows]);

  const availableCategories = useMemo(() => {
    const set = new Set<string>();
    rawRows.forEach((r) => {
      if (r[2]) set.add(String(r[2]));
    });
    return Array.from(set).sort();
  }, [rawRows]);

  const allMonths = useMemo(() => {
    const set = new Set<string>();
    rawRows.forEach((r) => {
      if (r[0]) set.add(String(r[0]));
    });
    return Array.from(set).sort();
  }, [rawRows]);

  // 1. 全局筛选范围下的数据 (globalScopeRows，不被点击月份截断)
  const globalScopeRows = useMemo(() => {
    return rawRows.filter((r) => {
      const m = String(r[0] || "");
      const b = fmtBrokerCn(r[1]);
      const c = String(r[2] || "");
      const sym = String(r[3] || "").toUpperCase();

      if (appliedBroker && b !== appliedBroker) return false;
      if (appliedCategory && c !== appliedCategory) return false;
      if (appliedTicker && !sym.includes(appliedTicker)) return false;
      if (appliedStartMonth && m < appliedStartMonth) return false;
      if (appliedEndMonth && m > appliedEndMonth) return false;
      return true;
    });
  }, [rawRows, appliedBroker, appliedCategory, appliedTicker, appliedStartMonth, appliedEndMonth]);

  // 2. 统计月度微操趋势数据 (各月份独立计算，无配对月份明确区分)
  const trendData = useMemo(() => {
    const monthMap = new Map<string, any[]>();
    for (const r of globalScopeRows) {
      const m = String(r[0] || "");
      if (!monthMap.has(m)) monthMap.set(m, []);
      monthMap.get(m)!.push(r);
    }

    // 展开生效期间内的所有自然月
    const fullMonths: string[] = [];
    if (appliedStartMonth && appliedEndMonth) {
      const [sy, sm] = appliedStartMonth.split("-").map(Number);
      const [ey, em] = appliedEndMonth.split("-").map(Number);
      let curY = sy;
      let curM = sm;
      while (curY < ey || (curY === ey && curM <= em)) {
        fullMonths.push(`${curY}-${String(curM).padStart(2, "0")}`);
        curM++;
        if (curM > 12) {
          curY++;
          curM = 1;
        }
      }
    } else {
      fullMonths.push(...allMonths);
    }

    return fullMonths.map((m) => {
      const rows = monthMap.get(m) || [];
      if (rows.length > 0) {
        const pnl = rows.reduce((acc, r) => acc + Number(r[11] || 0), 0);
        const trades = rows.reduce((acc, r) => acc + Number(r[4] || 0), 0);
        const qty = rows.reduce((acc, r) => acc + Number(r[5] || 0), 0);
        const profitCnt = rows.filter((r) => Number(r[11] || 0) > 0).length;
        const lossCnt = rows.filter((r) => Number(r[11] || 0) < 0).length;
        return { month: m, pnl, trades, qty, profitCnt, lossCnt, hasPair: true };
      }
      return { month: m, pnl: 0, trades: 0, qty: 0, profitCnt: 0, lossCnt: 0, hasPair: false };
    });
  }, [globalScopeRows, appliedStartMonth, appliedEndMonth, allMonths]);

  // 3. 选中月份数据摘要 (右侧摘要卡片)
  const selectedMonthSummary = useMemo(() => {
    if (!activeMonth) {
      return { pnl: null, trades: 0, qty: 0, hasPair: false };
    }
    const found = trendData.find((d) => d.month === activeMonth);
    if (found && found.hasPair) {
      return { pnl: found.pnl, trades: found.trades, qty: found.qty, hasPair: true };
    }
    return { pnl: null, trades: 0, qty: 0, hasPair: false };
  }, [trendData, activeMonth]);

  // 4. 统计月度股票 TOP 5 (严格取当前选中月份)
  const monthScopeRows = useMemo(() => {
    if (!activeMonth) return globalScopeRows;
    return globalScopeRows.filter((r) => String(r[0] || "") === activeMonth);
  }, [globalScopeRows, activeMonth]);

  const { profitTop5, lossTop5 } = useMemo(() => {
    const stockMap = new Map<string, { pnl: number; trades: number; qty: number }>();
    for (const r of monthScopeRows) {
      const sym = String(r[3] || "").toUpperCase();
      const existing = stockMap.get(sym) || { pnl: 0, trades: 0, qty: 0 };
      existing.pnl += Number(r[11] || 0);
      existing.trades += Number(r[4] || 0);
      existing.qty += Number(r[5] || 0);
      stockMap.set(sym, existing);
    }

    const allStocks = Array.from(stockMap.entries()).map(([ticker, data]) => ({
      ticker,
      ...data,
      unrealized: unrealizedMap[ticker] ?? null,
    }));

    const profits = allStocks
      .filter((s) => s.pnl >= 0)
      .sort((a, b) => b.pnl - a.pnl)
      .slice(0, 5);

    const losses = allStocks
      .filter((s) => s.pnl < 0)
      .sort((a, b) => a.pnl - b.pnl)
      .slice(0, 5);

    return { profitTop5: profits, lossTop5: losses };
  }, [monthScopeRows, unrealizedMap]);

  // 5. 过滤用于明细表格展示的数据 (displayRows)
  const displayRows = useMemo(() => {
    return globalScopeRows.filter((r) => {
      const m = String(r[0] || "");
      const sym = String(r[3] || "").toUpperCase();

      if (activeMonth && m !== activeMonth) return false;
      if (activeRankingTicker && sym !== activeRankingTicker) return false;
      return true;
    });
  }, [globalScopeRows, activeMonth, activeRankingTicker]);

  // 6. 16 列明细表格数据转换与排序
  const sortedTableRows = useMemo(() => {
    const rows = displayRows.map((r) => {
      const sym = String(r[3] || "").toUpperCase();
      const unrealized = unrealizedMap[sym] ?? null;
      return [
        String(r[0] || ""),             // 0: 月份
        fmtBrokerCn(r[1]),             // 1: 渠道
        String(r[2] || ""),             // 2: 分类
        sym,                            // 3: Ticker
        Number(r[4] || 0),              // 4: 微操次数
        Number(r[5] || 0),              // 5: 微操数量
        Number(r[6] || 0),              // 6: 微操均买价
        Number(r[7] || 0),              // 7: 微操均卖价
        Number(r[8] || 0),              // 8: 每股价差
        Number(r[9] || 0),              // 9: 买入金额
        Number(r[10] || 0),             // 10: 卖出金额
        Number(r[11] || 0),             // 11: 月内微操收益
        String(r[12] || "—"),           // 12: 首笔微操时间
        String(r[13] || "—"),           // 13: 最近微操时间
        unrealized,                     // 14: 当前浮动盈亏
        "仅月内配对",                    // 15: 说明
      ];
    });

    rows.sort((a, b) => {
      if (sortCol === 0 && !sortAsc) {
        // 三级稳定排序：月份降序 -> 收益降序 -> Ticker 升序
        if (a[0] !== b[0]) return String(b[0] || "").localeCompare(String(a[0] || ""));
        if (a[11] !== b[11]) return Number(b[11] || 0) - Number(a[11] || 0);
        return String(a[3] || "").localeCompare(String(b[3] || ""));
      }
      const valA = a[sortCol];
      const valB = b[sortCol];
      if (typeof valA === "number" && typeof valB === "number") {
        return sortAsc ? valA - valB : valB - valA;
      }
      return sortAsc
        ? String(valA || "").localeCompare(String(valB || ""))
        : String(valB || "").localeCompare(String(valA || ""));
    });

    return rows;
  }, [displayRows, sortCol, sortAsc, unrealizedMap]);

  // 分页数据
  const paginatedRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return sortedTableRows.slice(start, start + pageSize);
  }, [sortedTableRows, page, pageSize]);

  const totalPages = Math.max(1, Math.ceil(sortedTableRows.length / pageSize));

  // 点击查询
  const handleQuery = () => {
    let s = uiStartMonth;
    let e = uiEndMonth;
    if (s && e && s > e) {
      const temp = s;
      s = e;
      e = temp;
      setUiStartMonth(s);
      setUiEndMonth(e);
    }
    if (onPersonChange && uiPerson !== personId) onPersonChange(uiPerson);
    if (onBrokerChange && uiBroker !== externalBroker) onBrokerChange(uiBroker);

    setAppliedBroker(uiBroker);
    setAppliedCategory(uiCategory);
    setAppliedTicker(uiTicker.trim().toUpperCase());
    setAppliedStartMonth(s);
    setAppliedEndMonth(e);

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

    setActiveMonth(e);
    setActiveRankingTicker("");
    setPage(1);
  };

  // 导出 CSV
  const handleExport = () => {
    const cols = [
      "月份",
      "渠道",
      "分类",
      "Ticker",
      "微操次数",
      "微操数量",
      "微操均买价($)",
      "微操均卖价($)",
      "每股价差($)",
      "买入金额($)",
      "卖出金额($)",
      "月内微操收益($)",
      "首笔微操时间",
      "最近微操时间",
      "当前浮动盈亏($)",
      "说明",
    ];
    exportMonthlyCsv("每月微操收益明细", cols, sortedTableRows);
  };

  // 表头排序点击
  const handleHeaderSort = (colIndex: number) => {
    if (sortCol === colIndex) {
      setSortAsc(!sortAsc);
    } else {
      setSortCol(colIndex);
      setSortAsc(![0, 4, 5, 6, 7, 8, 9, 10, 11, 14].includes(colIndex));
    }
    setPage(1);
  };

  // ECharts 月度微操收益趋势配置
  const chartOption = useMemo(() => {
    const xData = trendData.map((d) => d.month);
    const yData = trendData.map((d) => {
      const isSelected = activeMonth && d.month === activeMonth;
      if (!d.hasPair) {
        return {
          value: 0,
          itemStyle: {
            color: "#CBD5E1",
            borderColor: isSelected ? "#2563EB" : "transparent",
            borderWidth: isSelected ? 2 : 0,
          },
        };
      }
      return {
        value: d.pnl,
        itemStyle: {
          color: d.pnl >= 0 ? "#10B981" : "#EF4444",
          borderColor: isSelected ? "#2563EB" : "transparent",
          borderWidth: isSelected ? 2 : 0,
          borderRadius: d.pnl >= 0 ? [3, 3, 0, 0] : [0, 0, 3, 3],
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
          const found = trendData.find((d) => d.month === m);
          if (!found || !found.hasPair) {
            return `
              <div style="font-weight: 600; margin-bottom: 4px;">${m}</div>
              <div style="color:#94A3B8; font-size:12px;">本月无完成的月内买卖配对</div>
            `;
          }
          return `
            <div style="font-weight: 600; margin-bottom: 4px;">${m}</div>
            <div style="display:flex; justify-content:space-between; gap:16px;">
              <span>月内微操收益:</span>
              <span style="font-weight:bold; color:${found.pnl >= 0 ? "#10B981" : "#EF4444"}">
                ${fmtSignedMoney(found.pnl, 2)}
              </span>
            </div>
            <div style="display:flex; justify-content:space-between; gap:16px; font-size:12px; color:#64748B;">
              <span>微操次数:</span>
              <span>${found.trades} 次</span>
            </div>
            <div style="display:flex; justify-content:space-between; gap:16px; font-size:12px; color:#64748B;">
              <span>微操数量:</span>
              <span>${fmtMoney(found.qty, 2)} 股</span>
            </div>
            <div style="display:flex; justify-content:space-between; gap:16px; font-size:11px; color:#94A3B8; margin-top:4px;">
              <span>盈利/亏损股票:</span>
              <span>${found.profitCnt} 只 / ${found.lossCnt} 只</span>
            </div>
          `;
        },
      },
      grid: {
        top: 30,
        left: 65,
        right: 20,
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
          name: "月内微操收益",
          type: "bar",
          data: yData,
          barMaxWidth: 22,
          label: {
            show: true,
            position: "top",
            formatter: (params: any) => {
              const found = trendData[params.dataIndex];
              if (!found || !found.hasPair) return "";
              if (found.pnl >= 0) return `+$${fmtMoney(found.pnl, 0)}`;
              return `-$${fmtMoney(Math.abs(found.pnl), 0)}`;
            },
            fontSize: 9.5,
            fontWeight: "bold",
            color: (params: any) => {
              const found = trendData[params.dataIndex];
              return (found?.pnl ?? 0) >= 0 ? "#10B981" : "#EF4444";
            },
          },
        },
      ],
    };
  }, [trendData, activeMonth]);

  return (
    <div className="daily-perf-container" style={{ gap: 12 }}>
      {/* 顶部标题与操作栏 */}
      <div className="daily-perf-header" style={{ marginBottom: 0 }}>
        <div className="title-area">
          <h2>每月微操收益</h2>
          <p>跟踪每月通过短线微操（同月买卖配对）获得的收益，评估你的短线交易表现。</p>
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

      {/* 统计口径说明卡片 (浅蓝色背景) */}
      <div
        style={{
          backgroundColor: "#EFF6FF",
          border: "1px solid #BFDBFE",
          borderRadius: 6,
          padding: "8px 14px",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <Info size={16} style={{ color: "#2563EB", flexShrink: 0 }} />
        <span style={{ color: "#1E40AF", fontSize: 12 }}>
          统计口径：仅统计同一自然月内完成买卖配对的交易；跨月持仓盈亏不计入微操收益，请在持仓管理或已实现盈亏中查看。
        </span>
      </div>

      {/* 1. 全局筛选卡片 */}
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
        <span style={{ fontSize: 12, color: "#64748B", fontWeight: 500 }}>人员</span>
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

        <span style={{ fontSize: 12, color: "#64748B", fontWeight: 500, marginLeft: 4 }}>渠道</span>
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

        <span style={{ fontSize: 12, color: "#64748B", fontWeight: 500, marginLeft: 4 }}>分类</span>
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

        <span style={{ fontSize: 12, color: "#64748B", fontWeight: 500, marginLeft: 4 }}>Ticker</span>
        <div style={{ position: "relative", width: 110 }}>
          <input
            type="text"
            placeholder="输入 Ticker"
            value={uiTicker}
            onChange={(e) => setUiTicker(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleQuery()}
            style={{ width: "100%", height: 32, padding: "0 8px", borderRadius: 6, border: "1px solid #CBD5E1", fontSize: 12.5, boxSizing: "border-box" }}
          />
        </div>

        <span style={{ fontSize: 12, color: "#64748B", fontWeight: 500, marginLeft: 4 }}>开始月份</span>
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

        <span style={{ fontSize: 12, color: "#64748B", fontWeight: 500, marginLeft: 4 }}>结束月份</span>
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

      {/* 2. 中部：月度微操趋势图 + 选中月份摘要 (高度约 260px) */}
      <div className="charts-split-section" style={{ display: "grid", gridTemplateColumns: "68% calc(32% - 10px)", gap: 10 }}>
        {/* 左侧：月度微操收益趋势 */}
        <div className="chart-card" style={{ background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0", padding: "12px 16px", height: 260, display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 2 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, color: "#0F172A" }}>月度微操收益趋势</h3>
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "#64748B" }}>
                <span style={{ display: "inline-block", width: 8, height: 8, backgroundColor: "#10B981", borderRadius: 1 }}></span>
                <span>月内微操盈利</span>
                <span style={{ display: "inline-block", width: 8, height: 8, backgroundColor: "#EF4444", borderRadius: 1, marginLeft: 6 }}></span>
                <span>月内微操亏损</span>
                <span style={{ display: "inline-block", width: 8, height: 8, backgroundColor: "#CBD5E1", borderRadius: 1, marginLeft: 6 }}></span>
                <span>无月内配对</span>
              </div>
            </div>
          </div>

          <div style={{ flex: 1, minHeight: 0 }}>
            <ReactECharts
              option={chartOption}
              style={{ height: "100%", width: "100%" }}
              onEvents={{
                click: (params: any) => {
                  if (params.name && activeMonth !== params.name) {
                    setActiveMonth(params.name);
                  }
                },
              }}
            />
          </div>

          <div style={{ fontSize: 11, color: "#94A3B8", marginTop: 2 }}>
            ⓘ 说明：若某月只有买入或只有卖出，未形成月内配对，则该月不计入微操收益。
          </div>
        </div>

        {/* 右侧：选中月份摘要卡片 */}
        <div
          style={{
            background: "#FFF",
            borderRadius: 6,
            border: "1px solid #E2E8F0",
            padding: "16px 18px",
            height: 260,
            display: "flex",
            flexDirection: "column",
            boxSizing: "border-box",
            justifyContent: "space-between",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Calendar size={15} style={{ color: "#2563EB" }} />
            <span style={{ fontSize: 13, fontWeight: 700, color: "#0F172A" }}>
              已选月份：{activeMonth || "全周期汇总"}
            </span>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginTop: 12 }}>
            <div>
              <div style={{ fontSize: 11, color: "#64748B", fontWeight: 500 }}>月内微操收益</div>
              <div
                style={{
                  fontSize: 20,
                  fontWeight: 700,
                  marginTop: 4,
                  fontFamily: "monospace",
                  color:
                    selectedMonthSummary.pnl == null
                      ? "#94A3B8"
                      : selectedMonthSummary.pnl > 0
                      ? "#10B981"
                      : selectedMonthSummary.pnl < 0
                      ? "#EF4444"
                      : "#0F172A",
                }}
              >
                {selectedMonthSummary.hasPair ? fmtSignedMoney(selectedMonthSummary.pnl, 2) : "—"}
              </div>
            </div>

            <div>
              <div style={{ fontSize: 11, color: "#64748B", fontWeight: 500 }}>微操次数</div>
              <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, fontFamily: "monospace", color: "#0F172A" }}>
                {selectedMonthSummary.trades}
              </div>
            </div>

            <div>
              <div style={{ fontSize: 11, color: "#64748B", fontWeight: 500 }}>微操数量</div>
              <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, fontFamily: "monospace", color: "#0F172A" }}>
                {fmtMoney(selectedMonthSummary.qty, 2)}
              </div>
            </div>
          </div>

          <div style={{ fontSize: 11, color: "#94A3B8", borderTop: "1px dashed #E2E8F0", paddingTop: 8 }}>
            点击左侧趋势柱体可实时切换已选月份，同步联动下方排行榜与明细。
          </div>
        </div>
      </div>

      {/* 3. 股票微操收益排行榜 (月内微操收益 TOP 5 & 月内微操亏损 TOP 5) */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {/* 左侧：月内微操收益 TOP 5 */}
        <div style={{ background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0", padding: "12px 16px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: "#0F172A" }}>月内微操收益 TOP 5</span>
              <span style={{ background: "#ECFDF5", color: "#10B981", fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 4 }}>
                仅看已选月份
              </span>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "28px 70px 1fr 65px 80px 95px", fontSize: 11, fontWeight: 600, color: "#94A3B8", padding: "4px 0", borderBottom: "1px solid #F1F5F9" }}>
            <span>#</span>
            <span>Ticker</span>
            <span style={{ textAlign: "right" }}>月内微操收益</span>
            <span style={{ textAlign: "right" }}>微操次数</span>
            <span style={{ textAlign: "right" }}>微操数量</span>
            <span style={{ textAlign: "right" }}>当前浮动盈亏</span>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 4 }}>
            {profitTop5.length === 0 ? (
              <div style={{ textAlign: "center", color: "#94A3B8", fontSize: 11, padding: "16px 0" }}>当前月份暂无微操盈利股票</div>
            ) : (
              profitTop5.map((s, idx) => (
                <div
                  key={s.ticker}
                  onClick={() => setActiveRankingTicker((prev) => (prev === s.ticker ? "" : s.ticker))}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "28px 70px 1fr 65px 80px 95px",
                    alignItems: "center",
                    padding: "3px 4px",
                    borderRadius: 4,
                    background: activeRankingTicker === s.ticker ? "#EFF6FF" : "transparent",
                    cursor: "pointer",
                    fontSize: 12,
                  }}
                >
                  <span style={{ width: 18, height: 18, borderRadius: 9, background: "#10B981", color: "#FFF", fontSize: 10, fontWeight: "bold", display: "flex", alignItems: "center", justifyContent: "center" }}>
                    {idx + 1}
                  </span>
                  <span style={{ fontWeight: "bold", color: "#2563EB", fontFamily: "monospace" }}>{s.ticker}</span>
                  <span style={{ textAlign: "right", fontWeight: "bold", color: "#10B981", fontFamily: "monospace" }}>
                    {fmtSignedMoney(s.pnl, 2)}
                  </span>
                  <span style={{ textAlign: "right", color: "#475569", fontFamily: "monospace" }}>{s.trades}</span>
                  <span style={{ textAlign: "right", color: "#475569", fontFamily: "monospace" }}>{fmtMoney(s.qty, 2)}</span>
                  <span
                    style={{
                      textAlign: "right",
                      fontWeight: "bold",
                      fontFamily: "monospace",
                      color: s.unrealized == null ? "#94A3B8" : s.unrealized >= 0 ? "#10B981" : "#EF4444",
                    }}
                  >
                    {s.unrealized == null ? "—" : fmtSignedMoney(s.unrealized, 2)}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* 右侧：月内微操亏损 TOP 5 */}
        <div style={{ background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0", padding: "12px 16px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: "#0F172A" }}>月内微操亏损 TOP 5</span>
              <span style={{ background: "#FEF2F2", color: "#EF4444", fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 4 }}>
                仅看已选月份
              </span>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "28px 70px 1fr 65px 80px 95px", fontSize: 11, fontWeight: 600, color: "#94A3B8", padding: "4px 0", borderBottom: "1px solid #F1F5F9" }}>
            <span>#</span>
            <span>Ticker</span>
            <span style={{ textAlign: "right" }}>月内微操亏损</span>
            <span style={{ textAlign: "right" }}>微操次数</span>
            <span style={{ textAlign: "right" }}>微操数量</span>
            <span style={{ textAlign: "right" }}>当前浮动盈亏</span>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 4 }}>
            {lossTop5.length === 0 ? (
              <div style={{ textAlign: "center", color: "#94A3B8", fontSize: 11, padding: "16px 0" }}>当前月份暂无微操亏损股票</div>
            ) : (
              lossTop5.map((s, idx) => (
                <div
                  key={s.ticker}
                  onClick={() => setActiveRankingTicker((prev) => (prev === s.ticker ? "" : s.ticker))}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "28px 70px 1fr 65px 80px 95px",
                    alignItems: "center",
                    padding: "3px 4px",
                    borderRadius: 4,
                    background: activeRankingTicker === s.ticker ? "#EFF6FF" : "transparent",
                    cursor: "pointer",
                    fontSize: 12,
                  }}
                >
                  <span style={{ width: 18, height: 18, borderRadius: 9, background: "#EF4444", color: "#FFF", fontSize: 10, fontWeight: "bold", display: "flex", alignItems: "center", justifyContent: "center" }}>
                    {idx + 1}
                  </span>
                  <span style={{ fontWeight: "bold", color: "#2563EB", fontFamily: "monospace" }}>{s.ticker}</span>
                  <span style={{ textAlign: "right", fontWeight: "bold", color: "#EF4444", fontFamily: "monospace" }}>
                    {fmtSignedMoney(s.pnl, 2)}
                  </span>
                  <span style={{ textAlign: "right", color: "#475569", fontFamily: "monospace" }}>{s.trades}</span>
                  <span style={{ textAlign: "right", color: "#475569", fontFamily: "monospace" }}>{fmtMoney(s.qty, 2)}</span>
                  <span
                    style={{
                      textAlign: "right",
                      fontWeight: "bold",
                      fontFamily: "monospace",
                      color: s.unrealized == null ? "#94A3B8" : s.unrealized >= 0 ? "#10B981" : "#EF4444",
                    }}
                  >
                    {s.unrealized == null ? "—" : fmtSignedMoney(s.unrealized, 2)}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* 4. 微操收益明细表格卡片 (直接全部展示，无内部滚动条) */}
      <div className="table-card" style={{ background: "#FFF", borderRadius: 6, border: "1px solid #E2E8F0", padding: "12px 16px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, color: "#0F172A" }}>微操收益明细</h3>
            <span style={{ fontSize: 11, color: "#64748B" }}>
              共 {sortedTableRows.length} 条记录 {activeMonth ? `(已选月份 ${activeMonth})` : ""} · 点击行查看详情，双击行进入交易回放
            </span>
          </div>
          <button
            className="primary-btn"
            onClick={handleExport}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              height: 28,
              padding: "0 12px",
              backgroundColor: "#2563EB",
              color: "#FFF",
              borderRadius: 6,
              border: "none",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            <Download size={13} />
            <span>导出 CSV</span>
          </button>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table className="finance-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "#F8FAFC", borderBottom: "1px solid #E2E8F0", color: "#475569" }}>
                {[
                  "月份",
                  "渠道",
                  "分类",
                  "Ticker",
                  "微操次数",
                  "微操数量",
                  "微操均买价($)",
                  "微操均卖价($)",
                  "每股价差($)",
                  "买入金额($)",
                  "卖出金额($)",
                  "月内微操收益($)",
                  "首笔微操时间",
                  "最近微操时间",
                  "当前浮动盈亏($)",
                  "说明",
                ].map((col, idx) => (
                  <th
                    key={col}
                    onClick={() => handleHeaderSort(idx)}
                    style={{
                      padding: "8px 6px",
                      cursor: "pointer",
                      textAlign: [0, 1, 2].includes(idx) ? "left" : [3, 12, 13, 15].includes(idx) ? "center" : "right",
                      whiteSpace: "nowrap",
                    }}
                  >
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
                      {col}
                      {sortCol === idx && <ArrowUpDown size={11} style={{ color: "#2563EB" }} />}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortedTableRows.length === 0 ? (
                <tr>
                  <td colSpan={16} style={{ textAlign: "center", padding: "24px", color: "#94A3B8" }}>
                    当前筛选条件下暂无微操收益明细
                  </td>
                </tr>
              ) : (
                sortedTableRows.map((r, i) => (
                  <tr
                    key={i}
                    onClick={() => setActiveDrawerRow(r)}
                    onDoubleClick={() => setReplayStock(String(r[3]))}
                    style={{
                      cursor: "pointer",
                      borderBottom: "1px solid #F1F5F9",
                      background: (activeMonth && r[0] === activeMonth) || (activeRankingTicker && r[3] === activeRankingTicker) ? "#EFF6FF" : i % 2 === 1 ? "#F8FAFC" : "#FFF",
                    }}
                  >
                    <td style={{ padding: "7px 6px", fontFamily: "monospace" }}>{r[0]}</td>
                    <td style={{ padding: "7px 6px" }}>{r[1]}</td>
                    <td style={{ padding: "7px 6px" }}>{r[2]}</td>
                    <td style={{ padding: "7px 6px", textAlign: "center", fontWeight: "bold", color: "#2563EB", fontFamily: "monospace" }}>
                      {r[3]}
                    </td>
                    <td style={{ padding: "7px 6px", textAlign: "right", fontFamily: "monospace" }}>{r[4]}</td>
                    <td style={{ padding: "7px 6px", textAlign: "right", fontFamily: "monospace" }}>{fmtMoney(r[5], 2)}</td>
                    <td style={{ padding: "7px 6px", textAlign: "right", fontFamily: "monospace" }}>${fmtMoney(r[6], 2)}</td>
                    <td style={{ padding: "7px 6px", textAlign: "right", fontFamily: "monospace" }}>${fmtMoney(r[7], 2)}</td>
                    <td
                      style={{
                        padding: "7px 6px",
                        textAlign: "right",
                        fontFamily: "monospace",
                        color: Number(r[8]) > 0 ? "#10B981" : Number(r[8]) < 0 ? "#EF4444" : "#64748B",
                      }}
                    >
                      {fmtSignedMoney(r[8], 2)}
                    </td>
                    <td style={{ padding: "7px 6px", textAlign: "right", fontFamily: "monospace" }}>${fmtMoney(r[9], 2)}</td>
                    <td style={{ padding: "7px 6px", textAlign: "right", fontFamily: "monospace" }}>${fmtMoney(r[10], 2)}</td>
                    <td
                      style={{
                        padding: "7px 6px",
                        textAlign: "right",
                        fontWeight: "bold",
                        fontFamily: "monospace",
                        color: Number(r[11]) > 0 ? "#10B981" : Number(r[11]) < 0 ? "#EF4444" : "#64748B",
                      }}
                    >
                      {fmtSignedMoney(r[11], 2)}
                    </td>
                    <td style={{ padding: "7px 6px", textAlign: "center", fontSize: 11, color: "#64748B" }}>{r[12]}</td>
                    <td style={{ padding: "7px 6px", textAlign: "center", fontSize: 11, color: "#64748B" }}>{r[13]}</td>
                    <td
                      style={{
                        padding: "7px 6px",
                        textAlign: "right",
                        fontWeight: "bold",
                        fontFamily: "monospace",
                        color: r[14] == null ? "#94A3B8" : Number(r[14]) >= 0 ? "#10B981" : "#EF4444",
                      }}
                    >
                      {r[14] == null ? "—" : fmtSignedMoney(r[14], 2)}
                    </td>
                    <td style={{ padding: "7px 6px", textAlign: "center" }}>
                      <span style={{ background: "#ECFDF5", color: "#059669", fontSize: 10, padding: "2px 6px", borderRadius: 4, fontWeight: 500 }}>
                        {r[15]}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 5. 详情抽屉 (点击行查看) */}
      {activeDrawerRow && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(15, 23, 42, 0.25)",
            zIndex: 100,
            display: "flex",
            justifyContent: "flex-end",
          }}
          onClick={() => setActiveDrawerRow(null)}
        >
          <div
            style={{
              width: 420,
              height: "100%",
              background: "#FFF",
              boxShadow: "-4px 0 16px rgba(0,0,0,0.1)",
              padding: "20px 24px",
              display: "flex",
              flexDirection: "column",
              gap: 16,
              boxSizing: "border-box",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "#0F172A" }}>
                {activeDrawerRow[3]} 微操详情
              </h3>
              <X size={18} style={{ cursor: "pointer", color: "#64748B" }} onClick={() => setActiveDrawerRow(null)} />
            </div>

            <div style={{ fontSize: 12, color: "#64748B" }}>
              {activeDrawerRow[0]} · {activeDrawerRow[1]} · {activeDrawerRow[2]}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8 }}>
              {[
                ["月份", activeDrawerRow[0]],
                ["渠道", activeDrawerRow[1]],
                ["分类", activeDrawerRow[2]],
                ["股票代码", activeDrawerRow[3]],
                ["微操次数", `${activeDrawerRow[4]} 次`],
                ["微操数量", `${fmtMoney(activeDrawerRow[5], 2)} 股`],
                ["微操均买价", `$${fmtMoney(activeDrawerRow[6], 2)}`],
                ["微操均卖价", `$${fmtMoney(activeDrawerRow[7], 2)}`],
                ["每股价差", fmtSignedMoney(activeDrawerRow[8], 2)],
                ["买入金额", `$${fmtMoney(activeDrawerRow[9], 2)}`],
                ["卖出金额", `$${fmtMoney(activeDrawerRow[10], 2)}`],
                ["月内微操收益", fmtSignedMoney(activeDrawerRow[11], 2)],
                ["首笔微操时间", activeDrawerRow[12]],
                ["最近微操时间", activeDrawerRow[13]],
                ["当前浮动盈亏", activeDrawerRow[14] == null ? "—" : fmtSignedMoney(activeDrawerRow[14], 2)],
                ["统计说明", activeDrawerRow[15]],
              ].map(([lbl, val]) => (
                <div key={lbl} style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
                  <span style={{ color: "#64748B" }}>{lbl}</span>
                  <span
                    style={{
                      fontWeight: ["月内微操收益", "当前浮动盈亏"].includes(lbl) ? 700 : 500,
                      fontFamily: "monospace",
                      color:
                        lbl === "月内微操收益"
                          ? activeDrawerRow[11] >= 0
                            ? "#10B981"
                            : "#EF4444"
                          : lbl === "当前浮动盈亏" && activeDrawerRow[14] != null
                          ? activeDrawerRow[14] >= 0
                            ? "#10B981"
                            : "#EF4444"
                          : "#0F172A",
                    }}
                  >
                    {val}
                  </span>
                </div>
              ))}
            </div>

            <button
              onClick={() => {
                setReplayStock(activeDrawerRow[3]);
                setActiveDrawerRow(null);
              }}
              style={{
                marginTop: "auto",
                height: 36,
                backgroundColor: "#2563EB",
                color: "#FFF",
                border: "none",
                borderRadius: 6,
                fontWeight: 600,
                fontSize: 13,
                cursor: "pointer",
              }}
            >
              查看该标的历史交易回放 →
            </button>
          </div>
        </div>
      )}

      {/* 6. 股票抽屉/交易回放 */}
      {replayStock && (
        <StockDrawer
          ticker={replayStock}
          personId={personId}
          onClose={() => setReplayStock(null)}
        />
      )}
    </div>
  );
};
