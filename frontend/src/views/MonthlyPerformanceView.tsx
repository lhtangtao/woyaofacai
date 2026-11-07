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
  BarChart2,
  PieChart,
} from "lucide-react";
import { api } from "../api/client";
import { PersonItem, ReportDetailData } from "../types";
import "./daily-performance.css";

interface MonthlyPerformanceViewProps {
  personId: number | null;
  onPersonChange?: (id: number | null) => void;
  broker?: string;
  onBrokerChange?: (broker: string) => void;
  persons: PersonItem[];
}

interface RawMonthlyRow {
  month: string;
  channel: string;
  category: string;
  symbol: string;
  status: string;
  initQty: number;
  buyQty: number;
  sellQty: number;
  endQty: number;
  costPrice: number;
  closePrice: number;
  marketValue: number;
  marketWeight: number;
  profitRate: number;
  breakeven: number;
  initValue: number;
  buyAmount: number;
  sellAmount: number;
  microProfit: number;
  tradesCount: number;
  monthlyProfit: number;
  cumBuy: number;
  cumSell: number;
  cumNet: number;
}

interface MonthSummary {
  month: string;
  initValue: number;
  marketValue: number;
  buyAmount: number;
  sellAmount: number;
  netAmount: number;
  monthlyProfit: number;
  profitRate: number;
  cumProfit: number;
  cumProfitRate: number;
  upStocks: number;
  downStocks: number;
}

interface ChannelSummary {
  channel: string;
  cumProfit: number;
  profitRate: number;
  contributionRatio: number;
}

const fmtMoney = (val: number | null | undefined, digits = 2): string => {
  if (val == null || isNaN(val)) return "—";
  return Number(val).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
};

const fmtSignedMoney = (val: number | null | undefined, digits = 2): string => {
  if (val == null || isNaN(val)) return "—";
  const num = Number(val);
  const sign = num > 0 ? "+" : num < 0 ? "-" : "";
  return `${sign}${fmtMoney(Math.abs(num), digits)}`;
};

const fmtPercent = (val: number | null | undefined, digits = 2): string => {
  if (val == null || isNaN(val)) return "—";
  const num = Number(val) * 100;
  const sign = num > 0 ? "+" : num < 0 ? "-" : "";
  return `${sign}${Math.abs(num).toFixed(digits)}%`;
};

const BROKER_CN_MAP: Record<string, string> = {
  LONGBRIDGE: "长桥",
  "LONG BRIDGE": "长桥",
  FIRSTRADE: "第一证券",
  SCHWAB: "嘉信",
  "CHARLES SCHWAB": "嘉信",
  BBAE: "BBAE",
  IBKR: "盈透证券",
  "INTERACTIVE BROKERS": "盈透证券",
  FUTU: "富途证券",
  TIGER: "老虎证券",
};

const fmtBrokerCn = (broker: string): string => {
  if (!broker) return "—";
  const upper = broker.toUpperCase();
  return BROKER_CN_MAP[upper] || broker;
};

export const MonthlyPerformanceView: React.FC<MonthlyPerformanceViewProps> = ({
  personId,
  onPersonChange,
  broker,
  onBrokerChange,
  persons,
}) => {
  // Loading & Data State
  const [loading, setLoading] = useState<boolean>(true);
  const [rawData, setRawData] = useState<RawMonthlyRow[]>([]);

  // Filter State
  const [filterPersonId, setFilterPersonId] = useState<number | null>(personId);
  const [filterBroker, setFilterBroker] = useState<string>(broker || "");
  const [filterTicker, setFilterTicker] = useState<string>("");
  const [filterCategory, setFilterCategory] = useState<string>("");
  const [filterStartMonth, setFilterStartMonth] = useState<string>("2025-01");
  const [filterEndMonth, setFilterEndMonth] = useState<string>("2026-11");

  // Applied Filters State
  const [appliedFilters, setAppliedFilters] = useState({
    personId: personId,
    broker: broker || "",
    ticker: "",
    category: "",
    startMonth: "2025-01",
    endMonth: "2026-11",
  });

  // Selected Month & Trend Mode
  const [selectedMonth, setSelectedMonth] = useState<string>("");
  const [trendMode, setTrendMode] = useState<"monthly" | "cum">("monthly");
  const [kpiFilter, setKpiFilter] = useState<"up" | "down" | null>(null);

  // Table Tabs & Pagination
  const [activeTab, setActiveTab] = useState<"month" | "channel" | "stock">("month");
  const [currentPage, setCurrentPage] = useState<number>(1);
  const pageSize = 20;

  // Sorting
  const [sortField, setSortField] = useState<string>("");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");

  // Stock Tab PnL Filter
  const [stockPnlFilter, setStockPnlFilter] = useState<"all" | "profit" | "loss">("all");
  const [stockFilterMonth, setStockFilterMonth] = useState<string>("");

  // Drawer State
  const [drawerOpen, setDrawerOpen] = useState<boolean>(false);
  const [drawerType, setDrawerType] = useState<"month" | "channel" | "stock">("month");
  const [drawerTitle, setDrawerTitle] = useState<string>("");
  const [drawerMonth, setDrawerMonth] = useState<string>("");
  const [drawerSymbol, setDrawerSymbol] = useState<string>("");
  const [drawerBroker, setDrawerBroker] = useState<string>("");

  // Sync external person/broker changes
  useEffect(() => {
    setFilterPersonId(personId);
    setAppliedFilters((prev) => ({ ...prev, personId }));
  }, [personId]);

  useEffect(() => {
    setFilterBroker(broker || "");
    setAppliedFilters((prev) => ({ ...prev, broker: broker || "" }));
  }, [broker]);

  // Fetch report data
  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await api.reportDetail(
        "每月持仓收益",
        appliedFilters.personId,
        undefined,
        1,
        50000
      );

      if (res && res.rows) {
        const rows: RawMonthlyRow[] = res.rows.map((r: any[]) => ({
          month: String(r[0] || ""),
          channel: String(r[1] || ""),
          category: String(r[2] || ""),
          symbol: String(r[3] || ""),
          status: String(r[4] || ""),
          initQty: Number(r[5] || 0),
          buyQty: Number(r[6] || 0),
          sellQty: Number(r[7] || 0),
          endQty: Number(r[8] || 0),
          costPrice: Number(r[9] || 0),
          closePrice: Number(r[10] || 0),
          marketValue: Number(r[11] || 0),
          marketWeight: Number(r[12] || 0),
          profitRate: Number(r[13] || 0),
          breakeven: Number(r[14] || 0),
          initValue: Number(r[15] || 0),
          buyAmount: Number(r[16] || 0),
          sellAmount: Number(r[17] || 0),
          microProfit: Number(r[18] || 0),
          tradesCount: Number(r[19] || 0),
          monthlyProfit: Number(r[20] || 0),
          cumBuy: Number(r[21] || 0),
          cumSell: Number(r[22] || 0),
          cumNet: Number(r[23] || 0),
        }));

        setRawData(rows);

        // Calculate max month
        let maxM = "";
        rows.forEach((r) => {
          if (r.month > maxM) maxM = r.month;
        });
        if (maxM) {
          setSelectedMonth((prev) => prev || maxM);
        }
      }
    } catch (err) {
      console.error("Failed to load monthly performance data:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [appliedFilters.personId]);

  // Available dropdown options
  const channelOptions = useMemo(() => {
    const s = new Set<string>();
    rawData.forEach((r) => {
      if (r.channel) s.add(r.channel);
    });
    return Array.from(s).sort();
  }, [rawData]);

  const categoryOptions = useMemo(() => {
    const s = new Set<string>();
    rawData.forEach((r) => {
      if (r.category) s.add(r.category);
    });
    return Array.from(s).sort();
  }, [rawData]);

  // Filtered rows
  const filteredRows = useMemo(() => {
    return rawData.filter((r) => {
      if (appliedFilters.broker && r.channel !== appliedFilters.broker && fmtBrokerCn(r.channel) !== appliedFilters.broker) {
        return false;
      }
      if (appliedFilters.ticker && !r.symbol.toUpperCase().includes(appliedFilters.ticker.toUpperCase())) {
        return false;
      }
      if (appliedFilters.category && r.category !== appliedFilters.category) {
        return false;
      }
      if (appliedFilters.startMonth && r.month < appliedFilters.startMonth) {
        return false;
      }
      if (appliedFilters.endMonth && r.month > appliedFilters.endMonth) {
        return false;
      }
      return true;
    });
  }, [rawData, appliedFilters]);

  // Months ascending list
  const availableMonths = useMemo(() => {
    const s = new Set<string>();
    filteredRows.forEach((r) => {
      if (r.month) s.add(r.month);
    });
    return Array.from(s).sort();
  }, [filteredRows]);

  // Group by Month Summaries
  const monthSummaries = useMemo(() => {
    const map = new Map<string, {
      month: string;
      initValue: number;
      marketValue: number;
      buyAmount: number;
      sellAmount: number;
      netAmount: number;
      monthlyProfit: number;
      upStocks: number;
      downStocks: number;
    }>();

    filteredRows.forEach((r) => {
      const m = r.month;
      if (!map.has(m)) {
        map.set(m, {
          month: m,
          initValue: 0,
          marketValue: 0,
          buyAmount: 0,
          sellAmount: 0,
          netAmount: 0,
          monthlyProfit: 0,
          upStocks: 0,
          downStocks: 0,
        });
      }
      const item = map.get(m)!;
      item.initValue += r.initValue;
      item.marketValue += r.marketValue;
      item.buyAmount += r.buyAmount;
      item.sellAmount += r.sellAmount;
      item.netAmount += (r.buyAmount - r.sellAmount);
      item.monthlyProfit += r.monthlyProfit;
      if (r.monthlyProfit > 0) item.upStocks += 1;
      else if (r.monthlyProfit < 0) item.downStocks += 1;
    });

    const monthsAsc = Array.from(map.keys()).sort();
    let cumPnl = 0;
    const summaries: MonthSummary[] = [];

    monthsAsc.forEach((m) => {
      const data = map.get(m)!;
      cumPnl += data.monthlyProfit;
      const profitRate = data.initValue > 0 ? data.monthlyProfit / data.initValue : 0;
      summaries.push({
        ...data,
        profitRate,
        cumProfit: cumPnl,
        cumProfitRate: 0,
      });
    });

    return summaries;
  }, [filteredRows]);

  // 7 KPI Metrics
  const kpiMetrics = useMemo(() => {
    let totalPnl = 0;
    let upMonths = 0;
    let downMonths = 0;
    let upPnlSum = 0;
    let downPnlSum = 0;

    monthSummaries.forEach((d) => {
      totalPnl += d.monthlyProfit;
      if (d.monthlyProfit > 0) {
        upMonths += 1;
        upPnlSum += d.monthlyProfit;
      } else if (d.monthlyProfit < 0) {
        downMonths += 1;
        downPnlSum += d.monthlyProfit;
      }
    });

    const totalMonths = monthSummaries.length;
    const avgPnl = totalMonths > 0 ? totalPnl / totalMonths : 0;
    const avgUpPnl = upMonths > 0 ? upPnlSum / upMonths : 0;
    const avgDownPnl = downMonths > 0 ? downPnlSum / downMonths : 0;

    return {
      totalPnl,
      totalMonths,
      avgPnl,
      upMonths,
      downMonths,
      avgUpPnl,
      avgDownPnl,
      dateRangeText: totalMonths > 0 ? `${monthSummaries[0].month} 至 ${monthSummaries[totalMonths - 1].month}` : "—",
      upRatioText: totalMonths > 0 ? `占比 ${(upMonths / totalMonths * 100).toFixed(1)}%` : "—",
      downRatioText: totalMonths > 0 ? `占比 ${(downMonths / totalMonths * 100).toFixed(1)}%` : "—",
    };
  }, [monthSummaries]);

  // Channel Cumulative Overview
  const channelSummaries = useMemo(() => {
    const map = new Map<string, number>();
    rawData.forEach((r) => {
      if (r.channel) {
        map.set(r.channel, 0);
      }
    });

    filteredRows.forEach((r) => {
      if (r.channel) {
        map.set(r.channel, (map.get(r.channel) || 0) + r.monthlyProfit);
      }
    });

    const totAbs = Array.from(map.values()).reduce((acc, v) => acc + Math.abs(v), 0) || 1;
    const list: ChannelSummary[] = [];

    map.forEach((pnl, ch) => {
      list.push({
        channel: ch,
        cumProfit: pnl,
        profitRate: 0,
        contributionRatio: (Math.abs(pnl) / totAbs) * 100,
      });
    });

    list.sort((a, b) => b.cumProfit - a.cumProfit);
    return list;
  }, [rawData, filteredRows]);

  // Monthly Attribution Data
  const targetMonth = selectedMonth || (availableMonths.length > 0 ? availableMonths[availableMonths.length - 1] : "");
  const selectedMonthRows = useMemo(() => {
    return filteredRows.filter((r) => r.month === targetMonth);
  }, [filteredRows, targetMonth]);

  const attributionStructure = useMemo(() => {
    let pCnt = 0;
    let pAmt = 0;
    let lCnt = 0;
    let lAmt = 0;
    let totPnl = 0;

    selectedMonthRows.forEach((r) => {
      totPnl += r.monthlyProfit;
      if (r.monthlyProfit > 0) {
        pCnt += 1;
        pAmt += r.monthlyProfit;
      } else if (r.monthlyProfit < 0) {
        lCnt += 1;
        lAmt += r.monthlyProfit;
      }
    });

    const lossCandidates = selectedMonthRows.filter((r) => r.monthlyProfit < 0);
    lossCandidates.sort((a, b) => a.monthlyProfit - b.monthlyProfit); // Most negative first
    const topLoss = lossCandidates.slice(0, 5);

    const profitCandidates = selectedMonthRows.filter((r) => r.monthlyProfit > 0);
    profitCandidates.sort((a, b) => b.monthlyProfit - a.monthlyProfit); // Most positive first
    const topProfit = profitCandidates.slice(0, 5);

    return {
      pCnt,
      pAmt,
      lCnt,
      lAmt,
      totPnl,
      topLoss,
      topProfit,
    };
  }, [selectedMonthRows]);

  // Month navigation helpers
  const curMonthIndex = availableMonths.indexOf(targetMonth);
  const canPrevMonth = curMonthIndex > 0;
  const canNextMonth = curMonthIndex >= 0 && curMonthIndex < availableMonths.length - 1;

  const handlePrevMonth = () => {
    if (canPrevMonth) {
      setSelectedMonth(availableMonths[curMonthIndex - 1]);
    }
  };

  const handleNextMonth = () => {
    if (canNextMonth) {
      setSelectedMonth(availableMonths[curMonthIndex + 1]);
    }
  };

  // ECharts Trend Option
  const trendOption = useMemo(() => {
    const months = monthSummaries.map((d) => d.month);
    const data = monthSummaries.map((d) => (trendMode === "monthly" ? d.monthlyProfit : d.cumProfit));

    if (trendMode === "monthly") {
      return {
        tooltip: {
          trigger: "axis",
          backgroundColor: "rgba(15, 23, 42, 0.9)",
          borderColor: "transparent",
          textStyle: { color: "#FFFFFF", fontSize: 12 },
          formatter: (params: any) => {
            const p = params[0];
            const item = monthSummaries[p.dataIndex];
            return `
              <div style="font-weight:600;margin-bottom:4px;">${p.name}</div>
              <div>本月收益: <b>${fmtSignedMoney(item?.monthlyProfit)}</b></div>
              <div>累计收益: <b>${fmtSignedMoney(item?.cumProfit)}</b></div>
            `;
          },
        },
        grid: {
          left: "6%",
          right: "4%",
          top: "14%",
          bottom: "12%",
          containLabel: true,
        },
        xAxis: {
          type: "category",
          data: months,
          axisLine: { lineStyle: { color: "#E2E8F0" } },
          axisLabel: { color: "#64748B", fontSize: 11 },
          axisTick: { show: false },
        },
        yAxis: {
          type: "value",
          splitLine: { lineStyle: { color: "#F1F5F9", type: "dashed" } },
          axisLabel: {
            color: "#64748B",
            fontSize: 11,
            formatter: (v: number) => fmtSignedMoney(v, 0),
          },
        },
        series: [
          {
            name: "月度收益",
            type: "bar",
            barMaxWidth: 24,
            data: data.map((val, idx) => ({
              value: val,
              itemStyle: {
                color:
                  months[idx] === targetMonth
                    ? val >= 0
                      ? "#059669"
                      : "#DC2626"
                    : val >= 0
                    ? "#10B981"
                    : "#EF4444",
                borderRadius: [2, 2, 0, 0],
              },
            })),
          },
        ],
      };
    } else {
      return {
        tooltip: {
          trigger: "axis",
          backgroundColor: "rgba(15, 23, 42, 0.9)",
          borderColor: "transparent",
          textStyle: { color: "#FFFFFF", fontSize: 12 },
          formatter: (params: any) => {
            const p = params[0];
            const item = monthSummaries[p.dataIndex];
            return `
              <div style="font-weight:600;margin-bottom:4px;">${p.name}</div>
              <div>累计收益: <b>${fmtSignedMoney(item?.cumProfit)}</b></div>
              <div>本月收益: <b>${fmtSignedMoney(item?.monthlyProfit)}</b></div>
            `;
          },
        },
        grid: {
          left: "6%",
          right: "4%",
          top: "14%",
          bottom: "12%",
          containLabel: true,
        },
        xAxis: {
          type: "category",
          data: months,
          axisLine: { lineStyle: { color: "#E2E8F0" } },
          axisLabel: { color: "#64748B", fontSize: 11 },
          axisTick: { show: false },
        },
        yAxis: {
          type: "value",
          splitLine: { lineStyle: { color: "#F1F5F9", type: "dashed" } },
          axisLabel: {
            color: "#64748B",
            fontSize: 11,
            formatter: (v: number) => fmtSignedMoney(v, 0),
          },
        },
        series: [
          {
            name: "累计收益",
            type: "line",
            smooth: true,
            showSymbol: false,
            lineStyle: { width: 2.5, color: "#2563EB" },
            areaStyle: {
              color: {
                type: "linear",
                x: 0,
                y: 0,
                x2: 0,
                y2: 1,
                colorStops: [
                  { offset: 0, color: "rgba(37, 99, 235, 0.2)" },
                  { offset: 1, color: "rgba(37, 99, 235, 0.0)" },
                ],
              },
            },
            data,
          },
        ],
      };
    }
  }, [monthSummaries, trendMode, targetMonth]);

  // Donut Option for Selected Month Structure
  const donutOption = useMemo(() => {
    const pAmt = attributionStructure.pAmt;
    const lAmt = Math.abs(attributionStructure.lAmt);
    const tot = pAmt + lAmt;

    return {
      tooltip: {
        trigger: "item",
        formatter: "{b}: {c} ({d}%)",
      },
      series: [
        {
          name: "当月盈亏结构",
          type: "pie",
          radius: ["60%", "85%"],
          center: ["50%", "50%"],
          avoidLabelOverlap: false,
          label: {
            show: true,
            position: "center",
            formatter: () => `{pnl|${fmtSignedMoney(attributionStructure.totPnl, 0)}}\n{title|当月收益}`,
            rich: {
              pnl: {
                fontSize: 14,
                fontWeight: "bold",
                color: attributionStructure.totPnl >= 0 ? "#10B981" : "#EF4444",
                lineHeight: 20,
              },
              title: {
                fontSize: 10,
                color: "#94A3B8",
              },
            },
          },
          data: tot === 0 ? [{ value: 1, name: "无数据", itemStyle: { color: "#E2E8F0" } }] : [
            { value: pAmt, name: "盈利股票", itemStyle: { color: "#10B981" } },
            { value: lAmt, name: "亏损股票", itemStyle: { color: "#EF4444" } },
          ],
        },
      ],
    };
  }, [attributionStructure]);

  // Table Data Handlers
  const filteredMonthSummariesDesc = useMemo(() => {
    let list = [...monthSummaries].reverse();
    if (kpiFilter === "up") {
      list = list.filter((d) => d.monthlyProfit > 0);
    } else if (kpiFilter === "down") {
      list = list.filter((d) => d.monthlyProfit < 0);
    }
    return list;
  }, [monthSummaries, kpiFilter]);

  // Channel Daily List
  const channelMonthlyList = useMemo(() => {
    const map = new Map<string, {
      month: string;
      channel: string;
      initValue: number;
      marketValue: number;
      buyAmount: number;
      sellAmount: number;
      netAmount: number;
      monthlyProfit: number;
      upStocks: number;
      downStocks: number;
    }>();

    filteredRows.forEach((r) => {
      const key = `${r.month}__${r.channel}`;
      if (!map.has(key)) {
        map.set(key, {
          month: r.month,
          channel: r.channel,
          initValue: 0,
          marketValue: 0,
          buyAmount: 0,
          sellAmount: 0,
          netAmount: 0,
          monthlyProfit: 0,
          upStocks: 0,
          downStocks: 0,
        });
      }
      const item = map.get(key)!;
      item.initValue += r.initValue;
      item.marketValue += r.marketValue;
      item.buyAmount += r.buyAmount;
      item.sellAmount += r.sellAmount;
      item.netAmount += (r.buyAmount - r.sellAmount);
      item.monthlyProfit += r.monthlyProfit;
      if (r.monthlyProfit > 0) item.upStocks += 1;
      else if (r.monthlyProfit < 0) item.downStocks += 1;
    });

    const brokerHistory = new Map<string, Array<{ month: string; data: any }>>();
    map.forEach((item) => {
      if (!brokerHistory.has(item.channel)) {
        brokerHistory.set(item.channel, []);
      }
      brokerHistory.get(item.channel)!.push({ month: item.month, data: item });
    });

    const result: Array<{
      month: string;
      channel: string;
      initValue: number;
      marketValue: number;
      buyAmount: number;
      sellAmount: number;
      netAmount: number;
      monthlyProfit: number;
      profitRate: number;
      cumProfit: number;
      upStocks: number;
      downStocks: number;
    }> = [];

    brokerHistory.forEach((entries) => {
      entries.sort((a, b) => (a.month > b.month ? 1 : -1));
      let cum = 0;
      entries.forEach(({ month, data }) => {
        cum += data.monthlyProfit;
        const rate = data.initValue > 0 ? data.monthlyProfit / data.initValue : 0;
        result.push({
          ...data,
          profitRate: rate,
          cumProfit: cum,
        });
      });
    });

    result.sort((a, b) => {
      if (a.month === b.month) return a.channel.localeCompare(b.channel);
      return b.month.localeCompare(a.month);
    });

    return result;
  }, [filteredRows]);

  // Stock Cumulative Map
  const stockCumMap = useMemo(() => {
    const map = new Map<string, number>();
    const sorted = [...rawData].sort((a, b) => a.month.localeCompare(b.month));
    const tracker = new Map<string, number>();

    sorted.forEach((r) => {
      const k = `${r.channel}__${r.symbol}`;
      const prev = tracker.get(k) || 0;
      const next = prev + r.monthlyProfit;
      tracker.set(k, next);
      map.set(`${r.month}__${r.channel}__${r.symbol}`, next);
    });

    return map;
  }, [rawData]);

  const stockTableRows = useMemo(() => {
    let list = [...filteredRows];
    if (stockFilterMonth) {
      list = list.filter((r) => r.month === stockFilterMonth);
    }
    if (stockPnlFilter === "profit") {
      list = list.filter((r) => r.monthlyProfit > 0);
    } else if (stockPnlFilter === "loss") {
      list = list.filter((r) => r.monthlyProfit < 0);
    }

    list.sort((a, b) => b.month.localeCompare(a.month));
    return list;
  }, [filteredRows, stockFilterMonth, stockPnlFilter]);

  // Action Click Handlers
  const openMonthDetail = (m: string) => {
    setSelectedMonth(m);
    setDrawerMonth(m);
    setDrawerType("month");
    setDrawerTitle(`📅 月度详情 · ${m}`);
    setDrawerOpen(true);
  };

  const openChannelDetail = (ch: string, m: string) => {
    setDrawerBroker(ch);
    setDrawerMonth(m);
    setDrawerType("channel");
    setDrawerTitle(`🏛️ 渠道月度明细 · ${fmtBrokerCn(ch)}`);
    setDrawerOpen(true);
  };

  const openStockDetail = (sym: string, m: string, ch: string) => {
    setDrawerSymbol(sym);
    setDrawerMonth(m);
    setDrawerBroker(ch);
    setDrawerType("stock");
    setDrawerTitle(`📈 股票收益详情 · ${sym}`);
    setDrawerOpen(true);
  };

  const handleApplyFilter = () => {
    setAppliedFilters({
      personId: filterPersonId,
      broker: filterBroker,
      ticker: filterTicker,
      category: filterCategory,
      startMonth: filterStartMonth,
      endMonth: filterEndMonth,
    });
    setCurrentPage(1);
  };

  const handleResetFilter = () => {
    setFilterPersonId(null);
    setFilterBroker("");
    setFilterTicker("");
    setFilterCategory("");
    setFilterStartMonth("2025-01");
    setFilterEndMonth("2026-11");
    setAppliedFilters({
      personId: null,
      broker: "",
      ticker: "",
      category: "",
      startMonth: "2025-01",
      endMonth: "2026-11",
    });
    setKpiFilter(null);
    setStockFilterMonth("");
    setStockPnlFilter("all");
    setCurrentPage(1);
  };

  const handleKpiCardClick = (type: "up" | "down" | "total") => {
    if (type === "up") {
      setKpiFilter((prev) => (prev === "up" ? null : "up"));
      setActiveTab("month");
    } else if (type === "down") {
      setKpiFilter((prev) => (prev === "down" ? null : "down"));
      setActiveTab("month");
    } else {
      setKpiFilter(null);
      setActiveTab("month");
    }
    setCurrentPage(1);
  };

  const handleStockFilterClick = (mode: "profit" | "loss") => {
    setStockFilterMonth(targetMonth);
    setStockPnlFilter(mode);
    setActiveTab("stock");
    setCurrentPage(1);
  };

  return (
    <div className="daily-perf-container">
      {/* 1. Header */}
      <div className="daily-perf-header">
        <div>
          <h1 className="daily-perf-title">每月持仓收益</h1>
          <p className="daily-perf-desc">
            查看历史每月的持仓市值变化与收益表现，支持按人员、券商渠道、股票等维度下钻分析。
          </p>
          <div className="db-badge">已连接数据库 · 只读</div>
        </div>
        <button className="btn-refresh" onClick={fetchData}>
          刷新
        </button>
      </div>

      {/* 2. Filter Bar */}
      <div className="filter-card">
        <div className="filter-row">
          <select
            className="filter-select"
            value={filterPersonId ?? ""}
            onChange={(e) => {
              const val = e.target.value === "" ? null : Number(e.target.value);
              setFilterPersonId(val);
              if (onPersonChange) onPersonChange(val);
            }}
          >
            <option value="">全部人员</option>
            {persons.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>

          <select
            className="filter-select"
            value={filterBroker}
            onChange={(e) => {
              setFilterBroker(e.target.value);
              if (onBrokerChange) onBrokerChange(e.target.value);
            }}
          >
            <option value="">全部渠道</option>
            {channelOptions.map((b) => (
              <option key={b} value={b}>
                {fmtBrokerCn(b)}
              </option>
            ))}
          </select>

          <input
            type="text"
            className="filter-input"
            placeholder="输入代码或名称，如 AAPL"
            value={filterTicker}
            onChange={(e) => setFilterTicker(e.target.value)}
          />

          <div className="date-range-box">
            <input
              type="text"
              className="filter-input month-input"
              value={filterStartMonth}
              onChange={(e) => setFilterStartMonth(e.target.value)}
            />
            <span className="date-separator">—</span>
            <input
              type="text"
              className="filter-input month-input"
              value={filterEndMonth}
              onChange={(e) => setFilterEndMonth(e.target.value)}
            />
          </div>

          <select
            className="filter-select"
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
          >
            <option value="">全部分类</option>
            {categoryOptions.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>

          <button className="btn-primary" onClick={handleApplyFilter}>
            查询
          </button>
          <button className="btn-secondary" onClick={handleResetFilter}>
            重置
          </button>

          <div className="as-of-tag">
            数据截至：{availableMonths.length > 0 ? availableMonths[availableMonths.length - 1] : "—"} (月末)
          </div>
        </div>
      </div>

      {/* 3. 7 KPI Metric Cards */}
      <div className="kpi-grid">
        <div
          className={`kpi-card ${kpiFilter === null ? "selected" : ""}`}
          onClick={() => handleKpiCardClick("total")}
        >
          <div className="kpi-head">
            <span className="kpi-title">期间总收益</span>
            <span className="kpi-arrow">›</span>
          </div>
          <div
            className={`kpi-value ${
              kpiMetrics.totalPnl >= 0 ? "color-profit" : "color-loss"
            }`}
          >
            {fmtSignedMoney(kpiMetrics.totalPnl)}
          </div>
          <div className="kpi-sub">累计收益</div>
        </div>

        <div className="kpi-card">
          <div className="kpi-head">
            <span className="kpi-title">交易月数</span>
            <span className="kpi-arrow">›</span>
          </div>
          <div className="kpi-value">{kpiMetrics.totalMonths} 个月</div>
          <div className="kpi-sub">{kpiMetrics.dateRangeText}</div>
        </div>

        <div className="kpi-card">
          <div className="kpi-head">
            <span className="kpi-title">月均收益</span>
            <span className="kpi-arrow">›</span>
          </div>
          <div
            className={`kpi-value ${
              kpiMetrics.avgPnl >= 0 ? "color-profit" : "color-loss"
            }`}
          >
            {fmtSignedMoney(kpiMetrics.avgPnl)}
          </div>
          <div className="kpi-sub">月均净现金额</div>
        </div>

        <div
          className={`kpi-card ${kpiFilter === "up" ? "selected" : ""}`}
          onClick={() => handleKpiCardClick("up")}
        >
          <div className="kpi-head">
            <span className="kpi-title">上涨月数</span>
            <span className="kpi-arrow">›</span>
          </div>
          <div className="kpi-value color-profit">{kpiMetrics.upMonths} 个月</div>
          <div className="kpi-sub">{kpiMetrics.upRatioText}</div>
        </div>

        <div
          className={`kpi-card ${kpiFilter === "down" ? "selected" : ""}`}
          onClick={() => handleKpiCardClick("down")}
        >
          <div className="kpi-head">
            <span className="kpi-title">下跌月数</span>
            <span className="kpi-arrow">›</span>
          </div>
          <div className="kpi-value color-loss">{kpiMetrics.downMonths} 个月</div>
          <div className="kpi-sub">{kpiMetrics.downRatioText}</div>
        </div>

        <div className="kpi-card">
          <div className="kpi-head">
            <span className="kpi-title">平均盈利月</span>
            <span className="kpi-arrow">›</span>
          </div>
          <div className="kpi-value color-profit">
            {fmtSignedMoney(kpiMetrics.avgUpPnl)}
          </div>
          <div className="kpi-sub">盈利月均值 ({kpiMetrics.upMonths}月)</div>
        </div>

        <div className="kpi-card">
          <div className="kpi-head">
            <span className="kpi-title">平均亏损月</span>
            <span className="kpi-arrow">›</span>
          </div>
          <div className="kpi-value color-loss">
            {fmtSignedMoney(kpiMetrics.avgDownPnl)}
          </div>
          <div className="kpi-sub">亏损月均值 ({kpiMetrics.downMonths}月)</div>
        </div>
      </div>

      {/* 4. Middle Section: Trend Chart (Left) + Broker Overview (Right) */}
      <div className="mid-grid">
        <div className="chart-panel">
          <div className="panel-head">
            <span className="panel-title">月度收益走势</span>
            <div className="toggle-group">
              <button
                className={`toggle-btn ${trendMode === "monthly" ? "active" : ""}`}
                onClick={() => setTrendMode("monthly")}
              >
                月度收益
              </button>
              <button
                className={`toggle-btn ${trendMode === "cum" ? "active" : ""}`}
                onClick={() => setTrendMode("cum")}
              >
                累计收益
              </button>
            </div>
            <button
              className="link-detail"
              onClick={() => setActiveTab("month")}
            >
              查看详情 ›
            </button>
          </div>
          <div className="chart-container">
            <ReactECharts
              option={trendOption}
              style={{ height: 260, width: "100%" }}
              onEvents={{
                click: (params: any) => {
                  if (params.name) {
                    setSelectedMonth(params.name);
                  }
                },
              }}
            />
          </div>
        </div>

        <div className="broker-panel">
          <div className="panel-head">
            <span className="panel-title">渠道收益概览 (期间累计)</span>
            <button
              className="link-detail"
              onClick={() => setActiveTab("channel")}
            >
              查看详情 ›
            </button>
          </div>
          <div className="broker-list">
            {channelSummaries.map((b) => {
              const isSelected =
                appliedFilters.broker === b.channel ||
                fmtBrokerCn(b.channel) === appliedFilters.broker;
              return (
                <div
                  key={b.channel}
                  className={`broker-row ${isSelected ? "selected" : ""}`}
                  onClick={() => {
                    setActiveTab("channel");
                    setFilterBroker(b.channel);
                    setAppliedFilters((prev) => ({ ...prev, broker: b.channel }));
                  }}
                >
                  <span className="broker-name">{fmtBrokerCn(b.channel)}</span>
                  <div className="broker-progress-wrap">
                    <div
                      className={`broker-progress-bar ${
                        isSelected
                          ? "bar-selected"
                          : b.cumProfit >= 0
                          ? "bar-profit"
                          : "bar-loss"
                      }`}
                      style={{ width: `${Math.min(100, b.contributionRatio)}%` }}
                    />
                  </div>
                  <span className="broker-ratio">
                    {b.contributionRatio.toFixed(1)}%
                  </span>
                  <span
                    className={`broker-pnl ${
                      b.cumProfit >= 0 ? "color-profit" : "color-loss"
                    }`}
                  >
                    {fmtSignedMoney(b.cumProfit)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* 5. Full-width Monthly Attribution Module */}
      <div className="attr-card">
        <div className="attr-header">
          <div className="attr-header-left">
            <span className="attr-title">单月收益归因 ({targetMonth || "—"})</span>
            <span className="attr-badge">点击联动</span>
            <span className="attr-subtitle">
              深度拆解单月收益构成：亏损/盈利贡献排行、盈亏结构与股票明细。
            </span>
          </div>
          <div className="attr-nav-buttons">
            <button
              className="btn-nav"
              disabled={!canPrevMonth}
              onClick={handlePrevMonth}
            >
              ◀ 上一月
            </button>
            <button
              className="btn-nav"
              disabled={!canNextMonth}
              onClick={handleNextMonth}
            >
              下一月 ▶
            </button>
          </div>
        </div>

        <div className="attr-grid">
          {/* 5.1 Left: Monthly PnL Structure */}
          <div className="attr-col pnl-struct-col">
            <div className="col-title">当月盈亏结构 ⓘ</div>
            <div className="struct-content">
              <div className="donut-wrap">
                <ReactECharts
                  option={donutOption}
                  style={{ height: 130, width: 130 }}
                />
              </div>
              <div className="struct-btns">
                <button
                  className="btn-struct-p"
                  onClick={() => handleStockFilterClick("profit")}
                >
                  ● 盈利股票 {attributionStructure.pCnt} 只 {fmtSignedMoney(attributionStructure.pAmt)}
                </button>
                <button
                  className="btn-struct-l"
                  onClick={() => handleStockFilterClick("loss")}
                >
                  ● 亏损股票 {attributionStructure.lCnt} 只 {fmtSignedMoney(attributionStructure.lAmt)}
                </button>
              </div>
            </div>
          </div>

          {/* 5.2 Middle: Loss Contribution Top 5 */}
          <div className="attr-col top-loss-col">
            <div className="col-title color-loss">↓ 亏损贡献排行 Top 5</div>
            <div className="top-table-header">
              <span style={{ width: "22px" }}>排名</span>
              <span style={{ flex: 1 }}>股票(Ticker)</span>
              <span style={{ width: "70px" }}>券商渠道</span>
              <span style={{ width: "85px", textAlign: "right" }}>当月收益</span>
              <span style={{ width: "80px", textAlign: "right" }}>贡献度</span>
            </div>
            <div className="top-table-body">
              {attributionStructure.topLoss.length === 0 ? (
                <div className="top-empty">当月无亏损标的</div>
              ) : (
                attributionStructure.topLoss.map((r, idx) => {
                  const baseLossTot = Math.abs(attributionStructure.lAmt) || 1;
                  const contrib = (Math.abs(r.monthlyProfit) / baseLossTot) * 100;
                  return (
                    <div
                      key={`${r.symbol}-${r.channel}-${idx}`}
                      className="top-row row-loss"
                      onClick={() => openStockDetail(r.symbol, targetMonth, r.channel)}
                    >
                      <span className="rank-badge loss-badge">{idx + 1}</span>
                      <span className="stock-ticker">{r.symbol}</span>
                      <span className="stock-channel">{fmtBrokerCn(r.channel)}</span>
                      <span className="stock-pnl color-loss">
                        {fmtSignedMoney(r.monthlyProfit)}
                      </span>
                      <div className="contrib-bar-wrap">
                        <div
                          className="contrib-bar-loss"
                          style={{ width: `${Math.min(100, contrib)}%` }}
                        />
                        <span className="contrib-text">{contrib.toFixed(1)}%</span>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* 5.3 Right: Profit Contribution Top 5 */}
          <div className="attr-col top-profit-col">
            <div className="col-title color-profit">↑ 盈利贡献排行 Top 5</div>
            <div className="top-table-header">
              <span style={{ width: "22px" }}>排名</span>
              <span style={{ flex: 1 }}>股票(Ticker)</span>
              <span style={{ width: "70px" }}>券商渠道</span>
              <span style={{ width: "85px", textAlign: "right" }}>当月收益</span>
              <span style={{ width: "80px", textAlign: "right" }}>贡献度</span>
            </div>
            <div className="top-table-body">
              {attributionStructure.topProfit.length === 0 ? (
                <div className="top-empty">当月无盈利标的</div>
              ) : (
                attributionStructure.topProfit.map((r, idx) => {
                  const baseProfitTot = attributionStructure.pAmt || 1;
                  const contrib = (r.monthlyProfit / baseProfitTot) * 100;
                  return (
                    <div
                      key={`${r.symbol}-${r.channel}-${idx}`}
                      className="top-row row-profit"
                      onClick={() => openStockDetail(r.symbol, targetMonth, r.channel)}
                    >
                      <span className="rank-badge profit-badge">{idx + 1}</span>
                      <span className="stock-ticker">{r.symbol}</span>
                      <span className="stock-channel">{fmtBrokerCn(r.channel)}</span>
                      <span className="stock-pnl color-profit">
                        {fmtSignedMoney(r.monthlyProfit)}
                      </span>
                      <div className="contrib-bar-wrap">
                        <div
                          className="contrib-bar-profit"
                          style={{ width: `${Math.min(100, contrib)}%` }}
                        />
                        <span className="contrib-text">{contrib.toFixed(1)}%</span>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
        <div className="attr-footer-hint">
          ⓘ 点击走势图月份、渠道概览或股票排行可联动下方明细与右侧抽屉。
        </div>
      </div>

      {/* 6. Multi-tab Data Table Card */}
      <div className="table-card">
        <div className="table-tab-header">
          <div className="tab-buttons">
            <button
              className={`tab-btn ${activeTab === "month" ? "active" : ""}`}
              onClick={() => {
                setActiveTab("month");
                setCurrentPage(1);
              }}
            >
              📅 按月份汇总
            </button>
            <button
              className={`tab-btn ${activeTab === "channel" ? "active" : ""}`}
              onClick={() => {
                setActiveTab("channel");
                setCurrentPage(1);
              }}
            >
              🏛️ 按渠道明细
            </button>
            <button
              className={`tab-btn ${activeTab === "stock" ? "active" : ""}`}
              onClick={() => {
                setActiveTab("stock");
                setCurrentPage(1);
              }}
            >
              📈 按股票明细
            </button>
          </div>
          <span className="table-record-count">
            共 {activeTab === "month" ? filteredMonthSummariesDesc.length : activeTab === "channel" ? channelMonthlyList.length : stockTableRows.length} 条记录
          </span>
        </div>

        {/* Table Container - NO OPERATION COLUMN */}
        <div className="modern-table-wrap">
          {activeTab === "month" && (
            <table className="modern-table">
              <thead>
                <tr>
                  <th style={{ width: "11%" }}>月份 (收盘)</th>
                  <th className="col-right" style={{ width: "9%" }}>期初市值</th>
                  <th className="col-right" style={{ width: "9%" }}>期末市值</th>
                  <th className="col-right" style={{ width: "8.5%" }}>本月买入</th>
                  <th className="col-right" style={{ width: "8.5%" }}>本月卖出</th>
                  <th className="col-right" style={{ width: "9%" }}>本月净投入</th>
                  <th className="col-right" style={{ width: "9.5%" }}>本月收益</th>
                  <th className="col-right" style={{ width: "8.5%" }}>本月收益率</th>
                  <th className="col-right" style={{ width: "9.5%" }}>累计收益</th>
                  <th className="col-center" style={{ width: "8.5%" }}>上涨股票数</th>
                  <th className="col-center" style={{ width: "9%" }}>下跌股票数</th>
                </tr>
              </thead>
              <tbody>
                {filteredMonthSummariesDesc
                  .slice((currentPage - 1) * pageSize, currentPage * pageSize)
                  .map((d) => (
                    <tr
                      key={d.month}
                      className={`clickable-row ${selectedMonth === d.month ? "selected-row" : ""}`}
                      onClick={() => openMonthDetail(d.month)}
                    >
                      <td style={{ fontWeight: 600 }}>{d.month}</td>
                      <td className="col-right">{fmtMoney(d.initValue, 2)}</td>
                      <td className="col-right">{fmtMoney(d.marketValue, 2)}</td>
                      <td className="col-right">{fmtMoney(d.buyAmount, 2)}</td>
                      <td className="col-right">{fmtMoney(d.sellAmount, 2)}</td>
                      <td
                        className={`col-right ${
                          d.netAmount > 0 ? "color-profit" : d.netAmount < 0 ? "color-loss" : ""
                        }`}
                      >
                        {fmtSignedMoney(d.netAmount, 2)}
                      </td>
                      <td
                        className={`col-right ${
                          d.monthlyProfit >= 0 ? "color-profit" : "color-loss"
                        }`}
                        style={{ fontWeight: 600 }}
                      >
                        {fmtSignedMoney(d.monthlyProfit, 2)}
                      </td>
                      <td
                        className={`col-right ${
                          d.profitRate >= 0 ? "color-profit" : "color-loss"
                        }`}
                      >
                        {fmtPercent(d.profitRate, 2)}
                      </td>
                      <td
                        className={`col-right ${
                          d.cumProfit >= 0 ? "color-profit" : "color-loss"
                        }`}
                        style={{ fontWeight: 600 }}
                      >
                        {fmtSignedMoney(d.cumProfit, 2)}
                      </td>
                      <td
                        className={`col-center ${d.upStocks > 0 ? "color-profit" : ""}`}
                        style={{ fontWeight: 600 }}
                      >
                        {d.upStocks} 只
                      </td>
                      <td
                        className={`col-center ${d.downStocks > 0 ? "color-loss" : ""}`}
                        style={{ fontWeight: 600 }}
                      >
                        {d.downStocks} 只
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}

          {activeTab === "channel" && (
            <table className="modern-table">
              <thead>
                <tr>
                  <th style={{ width: "10%" }}>月份</th>
                  <th style={{ width: "8.5%" }}>券商渠道</th>
                  <th className="col-right" style={{ width: "8.5%" }}>期初市值</th>
                  <th className="col-right" style={{ width: "8.5%" }}>期末市值</th>
                  <th className="col-right" style={{ width: "8%" }}>本月买入</th>
                  <th className="col-right" style={{ width: "8%" }}>本月卖出</th>
                  <th className="col-right" style={{ width: "8%" }}>本月净投入</th>
                  <th className="col-right" style={{ width: "8.5%" }}>本月收益</th>
                  <th className="col-right" style={{ width: "8%" }}>本月收益率</th>
                  <th className="col-right" style={{ width: "8.5%" }}>累计收益</th>
                  <th className="col-center" style={{ width: "7.5%" }}>上涨股票数</th>
                  <th className="col-center" style={{ width: "7.5%" }}>下跌股票数</th>
                </tr>
              </thead>
              <tbody>
                {channelMonthlyList
                  .slice((currentPage - 1) * pageSize, currentPage * pageSize)
                  .map((cd, idx) => (
                    <tr
                      key={`${cd.month}-${cd.channel}-${idx}`}
                      className="clickable-row"
                      onClick={() => openChannelDetail(cd.channel, cd.month)}
                    >
                      <td style={{ fontWeight: 600 }}>{cd.month}</td>
                      <td>{fmtBrokerCn(cd.channel)}</td>
                      <td className="col-right">{fmtMoney(cd.initValue, 2)}</td>
                      <td className="col-right">{fmtMoney(cd.marketValue, 2)}</td>
                      <td className="col-right">{fmtMoney(cd.buyAmount, 2)}</td>
                      <td className="col-right">{fmtMoney(cd.sellAmount, 2)}</td>
                      <td
                        className={`col-right ${
                          cd.netAmount > 0 ? "color-profit" : cd.netAmount < 0 ? "color-loss" : ""
                        }`}
                      >
                        {fmtSignedMoney(cd.netAmount, 2)}
                      </td>
                      <td
                        className={`col-right ${
                          cd.monthlyProfit >= 0 ? "color-profit" : "color-loss"
                        }`}
                        style={{ fontWeight: 600 }}
                      >
                        {fmtSignedMoney(cd.monthlyProfit, 2)}
                      </td>
                      <td
                        className={`col-right ${
                          cd.profitRate >= 0 ? "color-profit" : "color-loss"
                        }`}
                      >
                        {fmtPercent(cd.profitRate, 2)}
                      </td>
                      <td
                        className={`col-right ${
                          cd.cumProfit >= 0 ? "color-profit" : "color-loss"
                        }`}
                        style={{ fontWeight: 600 }}
                      >
                        {fmtSignedMoney(cd.cumProfit, 2)}
                      </td>
                      <td
                        className={`col-center ${cd.upStocks > 0 ? "color-profit" : ""}`}
                        style={{ fontWeight: 600 }}
                      >
                        {cd.upStocks} 只
                      </td>
                      <td
                        className={`col-center ${cd.downStocks > 0 ? "color-loss" : ""}`}
                        style={{ fontWeight: 600 }}
                      >
                        {cd.downStocks} 只
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}

          {activeTab === "stock" && (
            <table className="modern-table">
              <thead>
                <tr>
                  <th style={{ width: "9.5%" }}>月份</th>
                  <th style={{ width: "8.5%" }}>券商渠道</th>
                  <th style={{ width: "7%" }}>Ticker</th>
                  <th style={{ width: "7%" }}>股票分类</th>
                  <th className="col-right" style={{ width: "8%" }}>持仓数量</th>
                  <th className="col-right" style={{ width: "8%" }}>成本价</th>
                  <th className="col-right" style={{ width: "8%" }}>收盘价</th>
                  <th className="col-right" style={{ width: "9%" }}>本月收益</th>
                  <th className="col-right" style={{ width: "8%" }}>本月收益率</th>
                  <th className="col-right" style={{ width: "9%" }}>累计收益</th>
                  <th className="col-right" style={{ width: "8%" }}>持仓盈亏率</th>
                  <th className="col-right" style={{ width: "9%" }}>月末市值</th>
                </tr>
              </thead>
              <tbody>
                {stockTableRows
                  .slice((currentPage - 1) * pageSize, currentPage * pageSize)
                  .map((s, idx) => {
                    const cumPnl = stockCumMap.get(`${s.month}__${s.channel}__${s.symbol}`) ?? s.monthlyProfit;
                    const baseV = s.initValue > 0 ? s.initValue : s.buyAmount;
                    const monthlyRate = baseV > 0 ? s.monthlyProfit / baseV : (s.profitRate || 0);

                    return (
                      <tr
                        key={`${s.month}-${s.channel}-${s.symbol}-${idx}`}
                        className="clickable-row"
                        onClick={() => openStockDetail(s.symbol, s.month, s.channel)}
                      >
                        <td style={{ fontWeight: 600 }}>{s.month}</td>
                        <td>{fmtBrokerCn(s.channel)}</td>
                        <td style={{ fontWeight: 700, color: "#2563EB" }}>{s.symbol}</td>
                        <td>{s.category}</td>
                        <td className="col-right">{fmtMoney(s.endQty, 2)}</td>
                        <td className="col-right">{fmtMoney(s.costPrice, 2)}</td>
                        <td className="col-right">{fmtMoney(s.closePrice, 2)}</td>
                        <td
                          className={`col-right ${
                            s.monthlyProfit >= 0 ? "color-profit" : "color-loss"
                          }`}
                          style={{ fontWeight: 600 }}
                        >
                          {fmtSignedMoney(s.monthlyProfit, 2)}
                        </td>
                        <td
                          className={`col-right ${
                            monthlyRate >= 0 ? "color-profit" : "color-loss"
                          }`}
                        >
                          {fmtPercent(monthlyRate, 2)}
                        </td>
                        <td
                          className={`col-right ${
                            cumPnl >= 0 ? "color-profit" : "color-loss"
                          }`}
                          style={{ fontWeight: 600 }}
                        >
                          {fmtSignedMoney(cumPnl, 2)}
                        </td>
                        <td
                          className={`col-right ${
                            s.profitRate >= 0 ? "color-profit" : "color-loss"
                          }`}
                        >
                          {fmtPercent(s.profitRate, 2)}
                        </td>
                        <td className="col-right">{fmtMoney(s.marketValue, 2)}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          )}
        </div>

        {/* Pagination Bar */}
        <div className="table-pagination">
          <span className="page-size-info">每页 20 条</span>
          <div className="page-controls">
            <button
              className="page-btn"
              disabled={currentPage <= 1}
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
            >
              上一页
            </button>
            <span className="page-current">
              第 {currentPage} /{" "}
              {Math.max(
                1,
                Math.ceil(
                  (activeTab === "month"
                    ? filteredMonthSummariesDesc.length
                    : activeTab === "channel"
                    ? channelMonthlyList.length
                    : stockTableRows.length) / pageSize
                )
              )}{" "}
              页
            </span>
            <button
              className="page-btn"
              disabled={
                currentPage >=
                Math.ceil(
                  (activeTab === "month"
                    ? filteredMonthSummariesDesc.length
                    : activeTab === "channel"
                    ? channelMonthlyList.length
                    : stockTableRows.length) / pageSize
                )
              }
              onClick={() => setCurrentPage((p) => p + 1)}
            >
              下一页
            </button>
          </div>
        </div>
      </div>

      {/* 7. Slide-out Right Drawer */}
      {drawerOpen && (
        <div className="drawer-overlay" onClick={() => setDrawerOpen(false)}>
          <div className="drawer-panel" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-head">
              <span className="drawer-title">{drawerTitle}</span>
              <button
                className="drawer-close-btn"
                onClick={() => setDrawerOpen(false)}
              >
                ✕
              </button>
            </div>
            <div className="drawer-body">
              {drawerType === "month" && (
                <div>
                  <h4 style={{ margin: "0 0 10px 0", color: "#1E293B" }}>资金与收益概览</h4>
                  <p>当月月份：<b>{drawerMonth}</b></p>
                  <p>
                    当月总收益：
                    <b
                      className={
                        attributionStructure.totPnl >= 0
                          ? "color-profit"
                          : "color-loss"
                      }
                    >
                      {fmtSignedMoney(attributionStructure.totPnl)}
                    </b>
                  </p>
                  <p>盈利股票总计：<b className="color-profit">{fmtSignedMoney(attributionStructure.pAmt)}</b> ({attributionStructure.pCnt} 只)</p>
                  <p>亏损股票总计：<b className="color-loss">{fmtSignedMoney(attributionStructure.lAmt)}</b> ({attributionStructure.lCnt} 只)</p>
                  <hr style={{ margin: "16px 0", border: "none", borderTop: "1px solid #E2E8F0" }} />
                  <h4 style={{ margin: "0 0 10px 0", color: "#1E293B" }}>持仓标的清单</h4>
                  {selectedMonthRows.map((r, i) => (
                    <div
                      key={i}
                      style={{
                        padding: "8px 0",
                        borderBottom: "1px solid #F1F5F9",
                        display: "flex",
                        justifyContent: "space-between",
                      }}
                    >
                      <span>
                        <b>{r.symbol}</b> ({fmtBrokerCn(r.channel)})
                      </span>
                      <span className={r.monthlyProfit >= 0 ? "color-profit" : "color-loss"}>
                        {fmtSignedMoney(r.monthlyProfit)}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {drawerType === "channel" && (
                <div>
                  <h4 style={{ margin: "0 0 10px 0", color: "#1E293B" }}>渠道概览</h4>
                  <p>券商渠道：<b>{fmtBrokerCn(drawerBroker)}</b></p>
                  <p>当前月份：<b>{drawerMonth}</b></p>
                  <hr style={{ margin: "16px 0", border: "none", borderTop: "1px solid #E2E8F0" }} />
                  <h4 style={{ margin: "0 0 10px 0", color: "#1E293B" }}>该渠道持仓标的</h4>
                  {rawData
                    .filter((r) => r.month === drawerMonth && r.channel === drawerBroker)
                    .map((r, i) => (
                      <div
                        key={i}
                        style={{
                          padding: "8px 0",
                          borderBottom: "1px solid #F1F5F9",
                          display: "flex",
                          justifyContent: "space-between",
                        }}
                      >
                        <span>
                          <b>{r.symbol}</b> ({r.category})
                        </span>
                        <span className={r.monthlyProfit >= 0 ? "color-profit" : "color-loss"}>
                          {fmtSignedMoney(r.monthlyProfit)}
                        </span>
                      </div>
                    ))}
                </div>
              )}

              {drawerType === "stock" && (
                <div>
                  <h4 style={{ margin: "0 0 10px 0", color: "#1E293B" }}>标的历史月度明细</h4>
                  <p>股票代码：<b>{drawerSymbol}</b></p>
                  <p>券商渠道：<b>{fmtBrokerCn(drawerBroker)}</b></p>
                  <hr style={{ margin: "16px 0", border: "none", borderTop: "1px solid #E2E8F0" }} />
                  {rawData
                    .filter(
                      (r) =>
                        r.symbol === drawerSymbol &&
                        (!drawerBroker || r.channel === drawerBroker)
                    )
                    .sort((a, b) => b.month.localeCompare(a.month))
                    .map((r, i) => (
                      <div
                        key={i}
                        style={{
                          padding: "8px 0",
                          borderBottom: "1px solid #F1F5F9",
                          display: "flex",
                          justifyContent: "space-between",
                        }}
                      >
                        <span>{r.month}</span>
                        <span className={r.monthlyProfit >= 0 ? "color-profit" : "color-loss"}>
                          {fmtSignedMoney(r.monthlyProfit)} (持仓 {r.endQty} 股)
                        </span>
                      </div>
                    ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
