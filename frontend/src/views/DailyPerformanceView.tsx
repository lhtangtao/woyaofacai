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
  DollarSign,
  BarChart2,
  PieChart,
} from "lucide-react";
import { api } from "../api/client";
import { PersonItem, ReportDetailData } from "../types";
import "./daily-performance.css";

interface DailyPerformanceViewProps {
  personId: number | null;
  onPersonChange?: (id: number | null) => void;
  broker?: string;
  onBrokerChange?: (broker: string) => void;
  persons: PersonItem[];
}

interface RawDailyRow {
  date: string;
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
  tradesCount: number;
  dailyProfit: number;
  cumBuy: number;
  cumSell: number;
  cumNet: number;
}

interface DateSummary {
  date: string;
  initValue: number;
  marketValue: number;
  buyAmount: number;
  sellAmount: number;
  netAmount: number;
  dailyProfit: number;
  profitRate: number;
  cumProfit: number;
  cumProfitRate: number;
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
  return `${sign}$${fmtMoney(Math.abs(num), digits)}`;
};

const fmtPercent = (val: number | null | undefined, digits = 2): string => {
  if (val == null || isNaN(val)) return "—";
  const num = Number(val) * 100;
  const sign = num > 0 ? "+" : num < 0 ? "-" : "";
  return `${sign}${Math.abs(num).toFixed(digits)}%`;
};

export const DailyPerformanceView: React.FC<DailyPerformanceViewProps> = ({
  personId,
  onPersonChange,
  broker = "",
  onBrokerChange,
  persons,
}) => {
  const [loading, setLoading] = useState<boolean>(true);
  const [rawData, setRawData] = useState<RawDailyRow[]>([]);
  const [dataAsOf, setDataAsOf] = useState<string>("");

  // Filters
  const [selectedPerson, setSelectedPerson] = useState<number | null>(personId);
  const [selectedBroker, setSelectedBroker] = useState<string>(broker);
  const [searchTicker, setSearchTicker] = useState<string>("");
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  const [startDate, setStartDate] = useState<string>("2025-01-01");
  const [endDate, setEndDate] = useState<string>("");

  // Filter Applied State
  const [appliedFilters, setAppliedFilters] = useState({
    personId: personId,
    broker: broker,
    ticker: "",
    category: "",
    startDate: "2025-01-01",
    endDate: "",
  });

  // KPI Active Filter
  const [activeKpiFilter, setActiveKpiFilter] = useState<string | null>(null);

  // View States
  const [trendView, setTrendView] = useState<"daily" | "cum">("daily");
  const [activeTab, setActiveTab] = useState<"date" | "channel" | "stock">("date");
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [stockPnlFilter, setStockPnlFilter] = useState<"all" | "profit" | "loss" | "flat">("all");

  // Pagination
  const [currentPage, setCurrentPage] = useState<number>(1);
  const pageSize = 20;

  // Drawer
  const [drawerOpen, setDrawerOpen] = useState<boolean>(false);
  const [drawerMode, setDrawerMode] = useState<"date" | "channel" | "stock">("date");
  const [drawerTargetDate, setDrawerTargetDate] = useState<string>("");
  const [drawerTargetChannel, setDrawerTargetChannel] = useState<string>("");
  const [drawerTargetStock, setDrawerTargetStock] = useState<string>("");
  const [drawerTab, setDrawerTab] = useState<"channel" | "stock">("stock");

  // Fetch report data
  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await api.reportDetail(
        "每日持仓收益",
        appliedFilters.personId,
        undefined,
        1,
        50000
      );

      if (res && res.rows) {
        const rows: RawDailyRow[] = res.rows.map((r: any[]) => ({
          date: String(r[0] || ""),
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
          tradesCount: Number(r[18] || 0),
          dailyProfit: Number(r[19] || 0),
          cumBuy: Number(r[20] || 0),
          cumSell: Number(r[21] || 0),
          cumNet: Number(r[22] || 0),
        }));

        setRawData(rows);

        // Calculate max date as of
        let maxD = "";
        rows.forEach((r) => {
          if (r.date > maxD) maxD = r.date;
        });
        if (maxD) {
          setDataAsOf(maxD);
          if (!selectedDate) setSelectedDate(maxD);
          if (!endDate) setEndDate(maxD);
        }
      }
    } catch (e) {
      console.error("Failed to load daily performance:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [appliedFilters.personId]);

  // Handle Search & Reset
  const handleQuery = () => {
    setAppliedFilters({
      personId: selectedPerson,
      broker: selectedBroker,
      ticker: searchTicker.trim(),
      category: selectedCategory,
      startDate,
      endDate,
    });
    setActiveKpiFilter(null);
    setCurrentPage(1);
    if (onPersonChange && selectedPerson !== personId) onPersonChange(selectedPerson);
    if (onBrokerChange && selectedBroker !== broker) onBrokerChange(selectedBroker);
  };

  const handleReset = () => {
    setSelectedPerson(null);
    setSelectedBroker("");
    setSearchTicker("");
    setSelectedCategory("");
    setStartDate("2025-01-01");
    setEndDate(dataAsOf);
    setActiveKpiFilter(null);
    setStockPnlFilter("all");
    setAppliedFilters({
      personId: null,
      broker: "",
      ticker: "",
      category: "",
      startDate: "2025-01-01",
      endDate: dataAsOf,
    });
    setCurrentPage(1);
    if (onPersonChange) onPersonChange(null);
    if (onBrokerChange) onBrokerChange("");
  };

  // Channels and Categories options from full rawData
  const channelList = useMemo(() => {
    const s = new Set<string>();
    rawData.forEach((r) => {
      if (r.channel) s.add(r.channel);
    });
    return Array.from(s).sort();
  }, [rawData]);

  const categoryList = useMemo(() => {
    const s = new Set<string>();
    rawData.forEach((r) => {
      if (r.category) s.add(r.category);
    });
    return Array.from(s).sort();
  }, [rawData]);

  // Filtered dataset based on query form
  const filteredRawData = useMemo(() => {
    return rawData.filter((r) => {
      if (appliedFilters.broker && r.channel !== appliedFilters.broker) return false;
      if (appliedFilters.ticker && !r.symbol.toLowerCase().includes(appliedFilters.ticker.toLowerCase())) return false;
      if (appliedFilters.category && r.category !== appliedFilters.category) return false;
      if (appliedFilters.startDate && r.date < appliedFilters.startDate) return false;
      if (appliedFilters.endDate && r.date > appliedFilters.endDate) return false;
      return true;
    });
  }, [rawData, appliedFilters]);

  // Aggregate by Date
  const dateSummariesAsc: DateSummary[] = useMemo(() => {
    const dateMap = new Map<
      string,
      {
        initVal: number;
        marketVal: number;
        profit: number;
        buyAmount: number;
        sellAmount: number;
      }
    >();

    filteredRawData.forEach((r) => {
      if (!dateMap.has(r.date)) {
        dateMap.set(r.date, {
          initVal: 0,
          marketVal: 0,
          profit: 0,
          buyAmount: 0,
          sellAmount: 0,
        });
      }
      const item = dateMap.get(r.date)!;
      item.initVal += r.initValue || 0;
      item.marketVal += r.marketValue || 0;
      item.profit += r.dailyProfit || 0;
      item.buyAmount += r.buyAmount || 0;
      item.sellAmount += r.sellAmount || 0;
    });

    const dates = Array.from(dateMap.keys()).sort();
    let runningCum = 0;
    let initialBaseline = 0;
    for (const d of dates) {
      const item = dateMap.get(d)!;
      if (item.initVal > 0) {
        initialBaseline = item.initVal;
        break;
      }
      if (item.marketVal > 0) {
        initialBaseline = item.marketVal;
        break;
      }
    }

    return dates.map((d) => {
      const data = dateMap.get(d)!;
      runningCum += data.profit;
      const profitRate = data.initVal > 0 ? data.profit / data.initVal : (data.buyAmount > 0 ? data.profit / data.buyAmount : 0);
      const cumProfitRate = initialBaseline > 0 ? runningCum / initialBaseline : 0;
      const netAmount = data.buyAmount - data.sellAmount;

      return {
        date: d,
        initValue: data.initVal,
        marketValue: data.marketVal,
        buyAmount: data.buyAmount,
        sellAmount: data.sellAmount,
        netAmount,
        dailyProfit: data.profit,
        profitRate,
        cumProfit: runningCum,
        cumProfitRate,
      };
    });
  }, [rawData]);

  // Date summaries desc for table
  const dateSummariesDesc = useMemo(() => {
    return [...dateSummariesAsc].reverse();
  }, [dateSummariesAsc]);

  // 7 KPI Calculations
  const kpiStats = useMemo(() => {
    const totalTradingDays = dateSummariesAsc.length;
    let totalProfit = 0;
    let upDays = 0;
    let downDays = 0;
    let maxProfit = -Infinity;
    let maxProfitDate = "—";
    let maxLoss = Infinity;
    let maxLossDate = "—";

    dateSummariesAsc.forEach((ds) => {
      totalProfit += ds.dailyProfit;
      if (ds.dailyProfit > 0) upDays++;
      else if (ds.dailyProfit < 0) downDays++;

      if (ds.dailyProfit > maxProfit) {
        maxProfit = ds.dailyProfit;
        maxProfitDate = ds.date;
      }
      if (ds.dailyProfit < maxLoss) {
        maxLoss = ds.dailyProfit;
        maxLossDate = ds.date;
      }
    });

    const avgDailyProfit = totalTradingDays > 0 ? totalProfit / totalTradingDays : 0;
    let initialBase = 0;
    for (const ds of dateSummariesAsc) {
      if (ds.initValue > 0) {
        initialBase = ds.initValue;
        break;
      }
      if (ds.marketValue > 0) {
        initialBase = ds.marketValue;
        break;
      }
    }
    const totalProfitRate = initialBase > 0 ? totalProfit / initialBase : 0;
    const upRatio = totalTradingDays > 0 ? upDays / totalTradingDays : 0;
    const downRatio = totalTradingDays > 0 ? downDays / totalTradingDays : 0;

    return {
      totalProfit,
      totalProfitRate,
      totalTradingDays,
      firstDate: dateSummariesAsc[0]?.date || "—",
      lastDate: dateSummariesAsc[dateSummariesAsc.length - 1]?.date || "—",
      avgDailyProfit,
      upDays,
      upRatio,
      downDays,
      downRatio,
      maxProfit: maxProfit === -Infinity ? 0 : maxProfit,
      maxProfitDate,
      maxLoss: maxLoss === Infinity ? 0 : maxLoss,
      maxLossDate,
    };
  }, [dateSummariesAsc]);

  // Channel Summaries
  const channelSummaries: ChannelSummary[] = useMemo(() => {
    const map = new Map<string, { profit: number; initVal: number }>();
    filteredRawData.forEach((r) => {
      if (!map.has(r.channel)) {
        map.set(r.channel, { profit: 0, initVal: 0 });
      }
      const c = map.get(r.channel)!;
      c.profit += r.dailyProfit;
      c.initVal += r.initValue;
    });

    let totalAbsProfit = 0;
    map.forEach((v) => {
      totalAbsProfit += Math.abs(v.profit);
    });

    const list: ChannelSummary[] = [];
    map.forEach((v, k) => {
      list.push({
        channel: k,
        cumProfit: v.profit,
        profitRate: v.initVal > 0 ? v.profit / v.initVal : 0,
        contributionRatio: totalAbsProfit > 0 ? Math.abs(v.profit) / totalAbsProfit : 0,
      });
    });

    return list.sort((a, b) => b.cumProfit - a.cumProfit);
  }, [filteredRawData]);

  // Aggregate by Channel & Date for Tab 2 (Mirrors Date Summary Structure)
  const channelDailyList = useMemo(() => {
    const map = new Map<
      string,
      {
        date: string;
        channel: string;
        initValue: number;
        marketValue: number;
        buyAmount: number;
        sellAmount: number;
        netAmount: number;
        dailyProfit: number;
      }
    >();

    filteredRawData.forEach((r) => {
      const key = `${r.date}__${r.channel}`;
      if (!map.has(key)) {
        map.set(key, {
          date: r.date,
          channel: r.channel,
          initValue: 0,
          marketValue: 0,
          buyAmount: 0,
          sellAmount: 0,
          netAmount: 0,
          dailyProfit: 0,
        });
      }
      const item = map.get(key)!;
      item.initValue += r.initValue || 0;
      item.marketValue += r.marketValue || 0;
      const bAmt = r.buyAmount || 0;
      const sAmt = r.sellAmount || 0;
      item.buyAmount += bAmt;
      item.sellAmount += sAmt;
      item.netAmount += (bAmt - sAmt);
      item.dailyProfit += r.dailyProfit || 0;
    });

    // Group entries per channel to calculate historical cumulative returns
    const brokerMap = new Map<string, Array<{
      date: string;
      channel: string;
      initValue: number;
      marketValue: number;
      buyAmount: number;
      sellAmount: number;
      netAmount: number;
      dailyProfit: number;
    }>>();

    map.forEach((item) => {
      if (!brokerMap.has(item.channel)) {
        brokerMap.set(item.channel, []);
      }
      brokerMap.get(item.channel)!.push(item);
    });

    const resultList: Array<{
      date: string;
      channel: string;
      initValue: number;
      marketValue: number;
      buyAmount: number;
      sellAmount: number;
      netAmount: number;
      dailyProfit: number;
      profitRate: number;
      cumProfit: number;
      cumProfitRate: number | null;
    }> = [];

    brokerMap.forEach((entries) => {
      entries.sort((a, b) => a.date.localeCompare(b.date));
      let brokerBaseline = 0;
      for (const e of entries) {
        if (e.initValue > 0) {
          brokerBaseline = e.initValue;
          break;
        } else if (e.marketValue > 0) {
          brokerBaseline = e.marketValue;
          break;
        }
      }

      let brokerCum = 0;
      for (const e of entries) {
        brokerCum += e.dailyProfit;
        const rate = e.initValue > 0 ? e.dailyProfit / e.initValue : (e.buyAmount > 0 ? e.dailyProfit / e.buyAmount : 0);
        const cumRate = brokerBaseline > 0 ? brokerCum / brokerBaseline : null;
        resultList.push({
          ...e,
          profitRate: rate,
          cumProfit: brokerCum,
          cumProfitRate: cumRate,
        });
      }
    });

    return resultList.sort((a, b) => {
      if (b.date !== a.date) return b.date.localeCompare(a.date);
      return a.channel.localeCompare(b.channel);
    });
  }, [filteredRawData]);

  // Selected date stock list & distribution
  const selectedDateRows = useMemo(() => {
    const curDate = selectedDate || dataAsOf;
    return filteredRawData.filter((r) => r.date === curDate);
  }, [filteredRawData, selectedDate, dataAsOf]);

  const sortedSelectedDateRows = useMemo(() => {
    return [...selectedDateRows].sort((a, b) => b.dailyProfit - a.dailyProfit);
  }, [selectedDateRows]);

  const selectedDateDistribution = useMemo(() => {
    let profitCount = 0;
    let profitAmount = 0;
    let lossCount = 0;
    let lossAmount = 0;
    let flatCount = 0;
    let flatAmount = 0;
    let totalProfit = 0;

    selectedDateRows.forEach((r) => {
      totalProfit += r.dailyProfit;
      if (r.dailyProfit > 0) {
        profitCount++;
        profitAmount += r.dailyProfit;
      } else if (r.dailyProfit < 0) {
        lossCount++;
        lossAmount += r.dailyProfit;
      } else {
        flatCount++;
        flatAmount += r.dailyProfit;
      }
    });

    return {
      date: selectedDate || dataAsOf,
      totalProfit,
      profitCount,
      profitAmount,
      lossCount,
      lossAmount,
      flatCount,
      flatAmount,
    };
  }, [selectedDateRows, selectedDate, dataAsOf]);

  // Tab 3 Stock list with filters
  const stockTableRows = useMemo(() => {
    let list = filteredRawData;
    if (activeKpiFilter === "up") {
      list = list.filter((r) => r.dailyProfit > 0);
    } else if (activeKpiFilter === "down") {
      list = list.filter((r) => r.dailyProfit < 0);
    }

    if (stockPnlFilter === "profit") {
      list = list.filter((r) => r.dailyProfit > 0);
    } else if (stockPnlFilter === "loss") {
      list = list.filter((r) => r.dailyProfit < 0);
    } else if (stockPnlFilter === "flat") {
      list = list.filter((r) => r.dailyProfit === 0);
    }

    return [...list].sort((a, b) => {
      if (b.date !== a.date) return b.date.localeCompare(a.date);
      return b.dailyProfit - a.dailyProfit;
    });
  }, [filteredRawData, activeKpiFilter, stockPnlFilter]);

  // Tab 1 Date list with KPI filters
  const filteredDateSummariesDesc = useMemo(() => {
    if (activeKpiFilter === "up") {
      return dateSummariesDesc.filter((d) => d.dailyProfit > 0);
    }
    if (activeKpiFilter === "down") {
      return dateSummariesDesc.filter((d) => d.dailyProfit < 0);
    }
    return dateSummariesDesc;
  }, [dateSummariesDesc, activeKpiFilter]);

  // ECharts Trend Option
  const trendChartOption = useMemo(() => {
    const dates = dateSummariesAsc.map((d) => d.date);

    if (trendView === "daily") {
      const profits = dateSummariesAsc.map((d) => d.dailyProfit);
      return {
        tooltip: {
          trigger: "axis",
          formatter: (params: any[]) => {
            const p = params[0];
            if (!p) return "";
            const d = dateSummariesAsc[p.dataIndex];
            const colorCls = d.dailyProfit >= 0 ? "#10B981" : "#EF4444";
            return `
              <div style="font-size:12px; font-weight:600; margin-bottom:4px;">${d.date}</div>
              <div style="font-size:12px; color:${colorCls};">当日收益: ${fmtSignedMoney(d.dailyProfit)} (${fmtPercent(d.profitRate)})</div>
              <div style="font-size:12px; color:#2563EB;">累计收益: ${fmtSignedMoney(d.cumProfit)}</div>
            `;
          },
        },
        grid: { left: "3%", right: "3%", bottom: "8%", top: "8%", containLabel: true },
        xAxis: {
          type: "category",
          data: dates,
          axisLine: { lineStyle: { color: "#CBD5E1" } },
          axisLabel: { color: "#64748B", fontSize: 11 },
        },
        yAxis: {
          type: "value",
          axisLabel: {
            color: "#64748B",
            fontSize: 11,
            formatter: (v: number) => `$${v.toLocaleString()}`,
          },
          splitLine: { lineStyle: { color: "#F1F5F9" } },
        },
        series: [
          {
            name: "当日收益",
            type: "bar",
            data: profits.map((val, idx) => ({
              value: val,
              itemStyle: {
                color: val >= 0 ? "#10B981" : "#EF4444",
                borderRadius: val >= 0 ? [3, 3, 0, 0] : [0, 0, 3, 3],
              },
            })),
          },
        ],
      };
    } else {
      const cumProfits = dateSummariesAsc.map((d) => d.cumProfit);
      return {
        tooltip: {
          trigger: "axis",
          formatter: (params: any[]) => {
            const p = params[0];
            if (!p) return "";
            const d = dateSummariesAsc[p.dataIndex];
            return `
              <div style="font-size:12px; font-weight:600; margin-bottom:4px;">${d.date}</div>
              <div style="font-size:12px; color:#2563EB;">累计收益: ${fmtSignedMoney(d.cumProfit)} (${fmtPercent(d.cumProfitRate)})</div>
              <div style="font-size:12px; color:#64748B;">期初市值: $${fmtMoney(d.initValue)}</div>
            `;
          },
        },
        grid: { left: "3%", right: "3%", bottom: "8%", top: "8%", containLabel: true },
        xAxis: {
          type: "category",
          data: dates,
          axisLine: { lineStyle: { color: "#CBD5E1" } },
          axisLabel: { color: "#64748B", fontSize: 11 },
        },
        yAxis: {
          type: "value",
          axisLabel: {
            color: "#64748B",
            fontSize: 11,
            formatter: (v: number) => `$${v.toLocaleString()}`,
          },
          splitLine: { lineStyle: { color: "#F1F5F9" } },
        },
        series: [
          {
            name: "累计收益",
            type: "line",
            smooth: true,
            showSymbol: false,
            data: cumProfits,
            lineStyle: { color: "#2563EB", width: 2.5 },
            areaStyle: {
              color: {
                type: "linear",
                x: 0,
                y: 0,
                x2: 0,
                y2: 1,
                colorStops: [
                  { offset: 0, color: "rgba(37, 99, 235, 0.25)" },
                  { offset: 1, color: "rgba(37, 99, 235, 0.01)" },
                ],
              },
            },
          },
        ],
      };
    }
  }, [dateSummariesAsc, trendView]);

  // ECharts Donut Distribution Option
  const donutChartOption = useMemo(() => {
    const { profitAmount, lossAmount, totalProfit } = selectedDateDistribution;
    const absProfit = Math.max(0, profitAmount);
    const absLoss = Math.abs(Math.min(0, lossAmount));

    return {
      tooltip: {
        trigger: "item",
        formatter: (p: any) => `${p.name}: $${Math.abs(p.value).toLocaleString()} (${p.percent}%)`,
      },
      legend: { show: false },
      series: [
        {
          name: "当日分布",
          type: "pie",
          radius: ["65%", "85%"],
          center: ["50%", "50%"],
          avoidLabelOverlap: false,
          label: {
            show: true,
            position: "center",
            formatter: () =>
              `{val|${fmtSignedMoney(totalProfit, 0)}}\n{sub|当日总收益}`,
            rich: {
              val: {
                fontSize: 16,
                fontWeight: "bold",
                color: totalProfit >= 0 ? "#10B981" : "#EF4444",
                lineHeight: 22,
              },
              sub: {
                fontSize: 11,
                color: "#94A3B8",
                lineHeight: 16,
              },
            },
          },
          labelLine: { show: false },
          data: [
            {
              value: absProfit,
              name: "盈利股票",
              itemStyle: { color: "#10B981" },
            },
            {
              value: absLoss,
              name: "亏损股票",
              itemStyle: { color: "#EF4444" },
            },
          ],
        },
      ],
    };
  }, [selectedDateDistribution]);

  // Handle Chart Click Linkage
  const onChartClick = (params: any) => {
    if (params && params.name) {
      setSelectedDate(params.name);
    }
  };

  // KPI Card Clicks
  const handleKpiClick = (type: string) => {
    if (type === "total") {
      setActiveTab("date");
      setActiveKpiFilter(null);
    } else if (type === "days") {
      setActiveTab("date");
      setActiveKpiFilter(null);
    } else if (type === "avg") {
      const el = document.getElementById("trend-chart-card");
      if (el) el.scrollIntoView({ behavior: "smooth" });
    } else if (type === "up") {
      setActiveTab("date");
      setActiveKpiFilter(activeKpiFilter === "up" ? null : "up");
    } else if (type === "down") {
      setActiveTab("date");
      setActiveKpiFilter(activeKpiFilter === "down" ? null : "down");
    } else if (type === "max_profit") {
      if (kpiStats.maxProfitDate && kpiStats.maxProfitDate !== "—") {
        setSelectedDate(kpiStats.maxProfitDate);
        setDrawerTargetDate(kpiStats.maxProfitDate);
        setDrawerMode("date");
        setDrawerOpen(true);
      }
    } else if (type === "max_loss") {
      if (kpiStats.maxLossDate && kpiStats.maxLossDate !== "—") {
        setSelectedDate(kpiStats.maxLossDate);
        setDrawerTargetDate(kpiStats.maxLossDate);
        setDrawerMode("date");
        setDrawerOpen(true);
      }
    }
  };

  // Row Click for Details Drawer
  const openDateDetail = (date: string) => {
    setSelectedDate(date);
    setDrawerTargetDate(date);
    setDrawerMode("date");
    setDrawerOpen(true);
  };

  const openChannelDetail = (channel: string, date?: string) => {
    setDrawerTargetChannel(channel);
    if (date) setSelectedDate(date);
    setDrawerMode("channel");
    setDrawerOpen(true);
  };

  const openStockDetail = (symbol: string, date?: string) => {
    setDrawerTargetStock(symbol);
    if (date) setSelectedDate(date);
    setDrawerMode("stock");
    setDrawerOpen(true);
  };

  // Export CSV
  const handleExport = () => {
    if (filteredRawData.length === 0) return;
    const headers = [
      "日期",
      "渠道",
      "分类",
      "Ticker",
      "持仓数量",
      "成本均价",
      "收盘价",
      "日末市值",
      "当日收益",
      "持仓盈亏比例",
    ];
    const csvRows = [headers.join(",")];
    filteredRawData.forEach((r) => {
      csvRows.push(
        [
          r.date,
          r.channel,
          r.category,
          r.symbol,
          r.endQty,
          r.costPrice,
          r.closePrice,
          r.marketValue,
          r.dailyProfit,
          r.profitRate,
        ].join(",")
      );
    });

    const blob = new Blob(["\uFEFF" + csvRows.join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute("download", `每日持仓收益_${dataAsOf || "export"}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  // Drawer Next / Prev Navigation
  const handleDrawerNav = (direction: -1 | 1) => {
    if (drawerMode === "date") {
      const idx = dateSummariesDesc.findIndex((d) => d.date === drawerTargetDate);
      if (idx !== -1) {
        const nextIdx = idx + direction;
        if (nextIdx >= 0 && nextIdx < dateSummariesDesc.length) {
          const nextDate = dateSummariesDesc[nextIdx].date;
          setDrawerTargetDate(nextDate);
          setSelectedDate(nextDate);
        }
      }
    }
  };

  return (
    <div className="daily-perf-container">
      {/* 1. Header & Actions */}
      <div className="daily-perf-header">
        <div className="title-area">
          <h2>每日持仓收益</h2>
          <p>查看历史每日的持仓市值变化与收益表现，支持按人员、券商渠道、股票等维度下钻分析。</p>
        </div>
        <div className="actions-area">
          <div className="as-of-badge">
            <Info size={14} />
            <span>数据截至：{dataAsOf || "—"}（收盘）</span>
          </div>
          <button className="export-btn" onClick={handleExport}>
            <Download size={14} />
            <span>导出</span>
          </button>
        </div>
      </div>

      {/* 2. Compact Query & Filter Card */}
      <div className="daily-perf-card">
        <div className="filter-row">
          <div className="filter-item">
            <label>归属人员</label>
            <select
              value={selectedPerson ?? ""}
              onChange={(e) => setSelectedPerson(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">全部人员</option>
              {persons.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          <div className="filter-item">
            <label>券商渠道</label>
            <select
              value={selectedBroker}
              onChange={(e) => setSelectedBroker(e.target.value)}
            >
              <option value="">全部渠道</option>
              {channelList.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>

          <div className="filter-item">
            <label>Ticker</label>
            <input
              type="text"
              placeholder="输入代码或名称"
              value={searchTicker}
              onChange={(e) => setSearchTicker(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleQuery()}
            />
          </div>

          <div className="filter-item">
            <label>日期范围</label>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
              <span style={{ color: "#94A3B8" }}>~</span>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </div>
          </div>

          <div className="filter-item">
            <label>股票分类</label>
            <select
              value={selectedCategory}
              onChange={(e) => setSelectedCategory(e.target.value)}
            >
              <option value="">全部分类</option>
              {categoryList.map((cat) => (
                <option key={cat} value={cat}>
                  {cat}
                </option>
              ))}
            </select>
          </div>

          <div className="filter-btn-group">
            <button className="query-btn" onClick={handleQuery}>
              <Filter size={14} />
              <span>查询</span>
            </button>
            <button className="reset-btn" onClick={handleReset}>
              重置
            </button>
          </div>
        </div>

        {/* Active Chips */}
        {(appliedFilters.personId ||
          appliedFilters.broker ||
          appliedFilters.ticker ||
          appliedFilters.category ||
          activeKpiFilter) && (
          <div className="chips-row">
            {appliedFilters.personId && (
              <div className="filter-chip">
                <span>
                  人员: {persons.find((p) => p.id === appliedFilters.personId)?.name || appliedFilters.personId}
                </span>
                <span
                  className="chip-close"
                  onClick={() => {
                    setSelectedPerson(null);
                    setAppliedFilters((f) => ({ ...f, personId: null }));
                  }}
                >
                  ×
                </span>
              </div>
            )}
            {appliedFilters.broker && (
              <div className="filter-chip">
                <span>渠道: {appliedFilters.broker}</span>
                <span
                  className="chip-close"
                  onClick={() => {
                    setSelectedBroker("");
                    setAppliedFilters((f) => ({ ...f, broker: "" }));
                  }}
                >
                  ×
                </span>
              </div>
            )}
            {appliedFilters.ticker && (
              <div className="filter-chip">
                <span>标的: {appliedFilters.ticker}</span>
                <span
                  className="chip-close"
                  onClick={() => {
                    setSearchTicker("");
                    setAppliedFilters((f) => ({ ...f, ticker: "" }));
                  }}
                >
                  ×
                </span>
              </div>
            )}
            {appliedFilters.category && (
              <div className="filter-chip">
                <span>分类: {appliedFilters.category}</span>
                <span
                  className="chip-close"
                  onClick={() => {
                    setSelectedCategory("");
                    setAppliedFilters((f) => ({ ...f, category: "" }));
                  }}
                >
                  ×
                </span>
              </div>
            )}
            {activeKpiFilter && (
              <div className="filter-chip">
                <span>{activeKpiFilter === "up" ? "仅看上涨日期" : "仅看下跌日期"}</span>
                <span className="chip-close" onClick={() => setActiveKpiFilter(null)}>
                  ×
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 3. Seven KPI Stat Cards */}
      <div className="kpi-cards-grid">
        {/* Card 1: 期间总收益 */}
        <div
          className={`kpi-card ${activeTab === "date" && !activeKpiFilter ? "selected" : ""}`}
          onClick={() => handleKpiClick("total")}
        >
          <div className="kpi-card-header">
            <div className="kpi-title-wrap">
              <DollarSign size={15} color="#2563EB" />
              <span>期间总收益</span>
            </div>
            <span className="kpi-arrow">&gt;</span>
          </div>
          <div
            className={`kpi-value ${
              kpiStats.totalProfit > 0
                ? "color-profit"
                : kpiStats.totalProfit < 0
                ? "color-loss"
                : ""
            }`}
          >
            {fmtSignedMoney(kpiStats.totalProfit)}
          </div>
          <div className="kpi-sub">
            <span>收益率 {fmtPercent(kpiStats.totalProfitRate)}</span>
          </div>
        </div>

        {/* Card 2: 交易日数 */}
        <div className="kpi-card" onClick={() => handleKpiClick("days")}>
          <div className="kpi-card-header">
            <div className="kpi-title-wrap">
              <Calendar size={15} color="#2563EB" />
              <span>交易日数</span>
            </div>
            <span className="kpi-arrow">&gt;</span>
          </div>
          <div className="kpi-value">{kpiStats.totalTradingDays} 天</div>
          <div className="kpi-sub">
            <span>
              {kpiStats.firstDate} 至 {kpiStats.lastDate}
            </span>
          </div>
        </div>

        {/* Card 3: 日均收益 */}
        <div className="kpi-card" onClick={() => handleKpiClick("avg")}>
          <div className="kpi-card-header">
            <div className="kpi-title-wrap">
              <TrendingUp size={15} color="#10B981" />
              <span>日均收益</span>
            </div>
            <span className="kpi-arrow">&gt;</span>
          </div>
          <div
            className={`kpi-value ${
              kpiStats.avgDailyProfit > 0
                ? "color-profit"
                : kpiStats.avgDailyProfit < 0
                ? "color-loss"
                : ""
            }`}
          >
            {fmtSignedMoney(kpiStats.avgDailyProfit)}
          </div>
          <div className="kpi-sub">
            <span>日均净现金额</span>
          </div>
        </div>

        {/* Card 4: 上涨天数 */}
        <div
          className={`kpi-card ${activeKpiFilter === "up" ? "selected" : ""}`}
          onClick={() => handleKpiClick("up")}
        >
          <div className="kpi-card-header">
            <div className="kpi-title-wrap">
              <TrendingUp size={15} color="#10B981" />
              <span>上涨天数</span>
            </div>
            <span className="kpi-arrow">&gt;</span>
          </div>
          <div className="kpi-value color-profit">{kpiStats.upDays} 天</div>
          <div className="kpi-sub">
            <span>占比 {(kpiStats.upRatio * 100).toFixed(1)}%</span>
          </div>
        </div>

        {/* Card 5: 下跌天数 */}
        <div
          className={`kpi-card ${activeKpiFilter === "down" ? "selected" : ""}`}
          onClick={() => handleKpiClick("down")}
        >
          <div className="kpi-card-header">
            <div className="kpi-title-wrap">
              <TrendingDown size={15} color="#EF4444" />
              <span>下跌天数</span>
            </div>
            <span className="kpi-arrow">&gt;</span>
          </div>
          <div className="kpi-value color-loss">{kpiStats.downDays} 天</div>
          <div className="kpi-sub">
            <span>占比 {(kpiStats.downRatio * 100).toFixed(1)}%</span>
          </div>
        </div>

        {/* Card 6: 单日最大收益 */}
        <div className="kpi-card" onClick={() => handleKpiClick("max_profit")}>
          <div className="kpi-card-header">
            <div className="kpi-title-wrap">
              <BarChart2 size={15} color="#2563EB" />
              <span>单日最大收益</span>
            </div>
            <span className="kpi-arrow">&gt;</span>
          </div>
          <div className="kpi-value color-profit">{fmtSignedMoney(kpiStats.maxProfit)}</div>
          <div className="kpi-sub">
            <span>{kpiStats.maxProfitDate}</span>
          </div>
        </div>

        {/* Card 7: 单日最大亏损 */}
        <div className="kpi-card" onClick={() => handleKpiClick("max_loss")}>
          <div className="kpi-card-header">
            <div className="kpi-title-wrap">
              <BarChart2 size={15} color="#EF4444" />
              <span>单日最大亏损</span>
            </div>
            <span className="kpi-arrow">&gt;</span>
          </div>
          <div className="kpi-value color-loss">{fmtSignedMoney(kpiStats.maxLoss)}</div>
          <div className="kpi-sub">
            <span>{kpiStats.maxLossDate}</span>
          </div>
        </div>
      </div>

      {/* 4. Trend Chart & Broker Overview */}
      <div className="charts-split-section">
        {/* Left Trend Chart */}
        <div className="daily-perf-card" id="trend-chart-card">
          <div className="trend-card-header">
            <div className="trend-tabs">
              <button
                className={`trend-tab ${trendView === "daily" ? "active" : ""}`}
                onClick={() => setTrendView("daily")}
              >
                每日收益走势
              </button>
              <button
                className={`trend-tab ${trendView === "cum" ? "active" : ""}`}
                onClick={() => setTrendView("cum")}
              >
                累计收益走势
              </button>
            </div>
            <div className="trend-legend">
              {trendView === "daily" ? (
                <>
                  <div className="legend-item">
                    <span className="legend-dot" style={{ background: "#10B981" }} />
                    <span>正收益</span>
                  </div>
                  <div className="legend-item">
                    <span className="legend-dot" style={{ background: "#EF4444" }} />
                    <span>负收益</span>
                  </div>
                </>
              ) : (
                <div className="legend-item">
                  <span className="legend-dot" style={{ background: "#2563EB" }} />
                  <span>累计收益曲线</span>
                </div>
              )}
            </div>
          </div>
          <div style={{ height: 280 }}>
            <ReactECharts
              option={trendChartOption}
              style={{ height: "100%", width: "100%" }}
              onEvents={{ click: onChartClick }}
              notMerge={true}
            />
          </div>
        </div>

        {/* Right Broker Overview */}
        <div className="daily-perf-card">
          <div className="trend-card-header">
            <div style={{ fontSize: 14, fontWeight: 700, color: "#1E293B" }}>
              渠道收益概览 <span style={{ fontSize: 12, color: "#64748B", fontWeight: 400 }}>(期间累计)</span>
            </div>
            <div
              style={{ fontSize: 12, color: "#2563EB", cursor: "pointer", fontWeight: 500 }}
              onClick={() => setActiveTab("channel")}
            >
              查看详情 →
            </div>
          </div>

          <div className="channel-overview-list">
            {channelSummaries.map((c) => (
              <div
                key={c.channel}
                className="channel-row"
                onClick={() => {
                  setSelectedBroker(c.channel);
                  setAppliedFilters((f) => ({ ...f, broker: c.channel }));
                  setActiveTab("channel");
                }}
              >
                <div className="channel-info">
                  <span className="channel-name">{c.channel}</span>
                  <span
                    className={`channel-rate ${
                      c.profitRate >= 0 ? "color-profit" : "color-loss"
                    }`}
                  >
                    {fmtPercent(c.profitRate)}
                  </span>
                </div>
                <div className="channel-bar-wrap">
                  <div className="channel-bar-bg">
                    <div
                      className="channel-bar-fill"
                      style={{ width: `${Math.min(100, Math.round(c.contributionRatio * 100))}%` }}
                    />
                  </div>
                  <span className="channel-bar-text">
                    {(c.contributionRatio * 100).toFixed(1)}% 贡献
                  </span>
                </div>
                <div
                  className={`channel-profit ${
                    c.cumProfit >= 0 ? "color-profit" : "color-loss"
                  }`}
                >
                  {fmtSignedMoney(c.cumProfit)}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 5. Main Multi-dimensional Table & Selected Date Distribution */}
      <div className="details-split-section">
        {/* Left Multi-dimensional Table */}
        <div className="daily-perf-card">
          <div className="table-tabs-header">
            <div className="table-tab-btns">
              <button
                className={`table-tab-btn ${activeTab === "date" ? "active" : ""}`}
                onClick={() => {
                  setActiveTab("date");
                  setCurrentPage(1);
                }}
              >
                <Calendar size={14} />
                <span>按日期汇总</span>
              </button>
              <button
                className={`table-tab-btn ${activeTab === "channel" ? "active" : ""}`}
                onClick={() => {
                  setActiveTab("channel");
                  setCurrentPage(1);
                }}
              >
                <BarChart2 size={14} />
                <span>按渠道明细</span>
              </button>
              <button
                className={`table-tab-btn ${activeTab === "stock" ? "active" : ""}`}
                onClick={() => {
                  setActiveTab("stock");
                  setCurrentPage(1);
                }}
              >
                <TrendingUp size={14} />
                <span>按股票明细</span>
              </button>
            </div>
            <div style={{ fontSize: 12, color: "#64748B" }}>
              {activeTab === "date" && `共 ${filteredDateSummariesDesc.length} 条记录`}
              {activeTab === "channel" && `共 ${channelDailyList.length} 条记录`}
              {activeTab === "stock" && `共 ${stockTableRows.length} 条记录`}
            </div>
          </div>

          {/* Table Container - NO OPERATION COLUMN */}
          <div className="modern-table-wrap">
            {activeTab === "date" && (
              <table className="modern-table">
                <thead>
                  <tr>
                    <th>日期 (收盘)</th>
                    <th className="col-right">期初市值</th>
                    <th className="col-right">期末市值</th>
                    <th className="col-right">当日买入</th>
                    <th className="col-right">当日卖出</th>
                    <th className="col-right">当日净投入</th>
                    <th className="col-right">当日收益</th>
                    <th className="col-right">当日收益率</th>
                    <th className="col-right">累计收益</th>
                    <th className="col-right">累计收益率</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredDateSummariesDesc
                    .slice((currentPage - 1) * pageSize, currentPage * pageSize)
                    .map((d) => (
                      <tr
                        key={d.date}
                        className={`clickable-row ${selectedDate === d.date ? "selected-row" : ""}`}
                        onClick={() => openDateDetail(d.date)}
                      >
                        <td style={{ fontWeight: 600 }}>{d.date}</td>
                        <td className="col-right">${fmtMoney(d.initValue, 2)}</td>
                        <td className="col-right">${fmtMoney(d.marketValue, 2)}</td>
                        <td className="col-right">${fmtMoney(d.buyAmount, 2)}</td>
                        <td className="col-right">${fmtMoney(d.sellAmount, 2)}</td>
                        <td
                          className={`col-right ${
                            d.netAmount > 0 ? "color-profit" : d.netAmount < 0 ? "color-loss" : ""
                          }`}
                        >
                          {fmtSignedMoney(d.netAmount, 2)}
                        </td>
                        <td
                          className={`col-right ${
                            d.dailyProfit >= 0 ? "color-profit" : "color-loss"
                          }`}
                          style={{ fontWeight: 600 }}
                        >
                          {fmtSignedMoney(d.dailyProfit, 2)}
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
                          className={`col-right ${
                            d.cumProfitRate >= 0 ? "color-profit" : "color-loss"
                          }`}
                        >
                          {fmtPercent(d.cumProfitRate, 2)}
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
                    <th>日期</th>
                    <th>券商渠道</th>
                    <th className="col-right">期初市值</th>
                    <th className="col-right">期末市值</th>
                    <th className="col-right">当日买入</th>
                    <th className="col-right">当日卖出</th>
                    <th className="col-right">当日净投入</th>
                    <th className="col-right">当日收益</th>
                    <th className="col-right">当日收益率</th>
                    <th className="col-right">累计收益</th>
                    <th className="col-right">累计收益率</th>
                  </tr>
                </thead>
                <tbody>
                  {channelDailyList
                    .slice((currentPage - 1) * pageSize, currentPage * pageSize)
                    .map((cd, idx) => (
                      <tr
                        key={`${cd.date}-${cd.channel}-${idx}`}
                        className="clickable-row"
                        onClick={() => openChannelDetail(cd.channel, cd.date)}
                      >
                        <td style={{ fontWeight: 600 }}>{cd.date}</td>
                        <td>{cd.channel}</td>
                        <td className="col-right">${fmtMoney(cd.initValue, 2)}</td>
                        <td className="col-right">${fmtMoney(cd.marketValue, 2)}</td>
                        <td className="col-right">${fmtMoney(cd.buyAmount, 2)}</td>
                        <td className="col-right">${fmtMoney(cd.sellAmount, 2)}</td>
                        <td
                          className={`col-right ${
                            cd.netAmount > 0 ? "color-profit" : cd.netAmount < 0 ? "color-loss" : ""
                          }`}
                        >
                          {fmtSignedMoney(cd.netAmount, 2)}
                        </td>
                        <td
                          className={`col-right ${
                            cd.dailyProfit >= 0 ? "color-profit" : "color-loss"
                          }`}
                          style={{ fontWeight: 600 }}
                        >
                          {fmtSignedMoney(cd.dailyProfit, 2)}
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
                          className={`col-right ${
                            (cd.cumProfitRate ?? 0) >= 0 ? "color-profit" : "color-loss"
                          }`}
                        >
                          {cd.cumProfitRate !== null ? fmtPercent(cd.cumProfitRate, 2) : "—"}
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
                    <th>日期</th>
                    <th>券商渠道</th>
                    <th>Ticker</th>
                    <th>股票分类</th>
                    <th className="col-right">持仓数量</th>
                    <th className="col-right">成本价</th>
                    <th className="col-right">收盘价</th>
                    <th className="col-right">当日收益</th>
                    <th className="col-right">收益率</th>
                    <th className="col-right">日末市值</th>
                  </tr>
                </thead>
                <tbody>
                  {stockTableRows
                    .slice((currentPage - 1) * pageSize, currentPage * pageSize)
                    .map((s, idx) => (
                      <tr
                        key={`${s.date}-${s.channel}-${s.symbol}-${idx}`}
                        className="clickable-row"
                        onClick={() => openStockDetail(s.symbol, s.date)}
                      >
                        <td style={{ fontWeight: 600 }}>{s.date}</td>
                        <td>{s.channel}</td>
                        <td style={{ fontWeight: 700, color: "#2563EB" }}>{s.symbol}</td>
                        <td>{s.category}</td>
                        <td className="col-right">{fmtMoney(s.endQty, 2)}</td>
                        <td className="col-right">${fmtMoney(s.costPrice)}</td>
                        <td className="col-right">${fmtMoney(s.closePrice)}</td>
                        <td
                          className={`col-right ${
                            s.dailyProfit >= 0 ? "color-profit" : "color-loss"
                          }`}
                          style={{ fontWeight: 600 }}
                        >
                          {fmtSignedMoney(s.dailyProfit)}
                        </td>
                        <td
                          className={`col-right ${
                            s.profitRate >= 0 ? "color-profit" : "color-loss"
                          }`}
                        >
                          {fmtPercent(s.profitRate)}
                        </td>
                        <td className="col-right">${fmtMoney(s.marketValue)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Pagination */}
          <div className="table-pagination">
            <span>每页 {pageSize} 条</span>
            <div className="page-controls">
              <button
                className="page-btn"
                disabled={currentPage === 1}
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              >
                上一页
              </button>
              <span style={{ margin: "0 6px", fontWeight: 600 }}>
                第 {currentPage} 页
              </span>
              <button
                className="page-btn"
                disabled={
                  activeTab === "date"
                    ? currentPage * pageSize >= filteredDateSummariesDesc.length
                    : activeTab === "channel"
                    ? currentPage * pageSize >= channelDailyList.length
                    : currentPage * pageSize >= stockTableRows.length
                }
                onClick={() => setCurrentPage((p) => p + 1)}
              >
                下一页
              </button>
            </div>
          </div>
        </div>

        {/* Right Selected Date Distribution Card */}
        <div className="daily-perf-card">
          <div className="trend-card-header">
            <div style={{ fontSize: 14, fontWeight: 700, color: "#1E293B" }}>
              当日收益分布{" "}
              <span style={{ fontSize: 12, color: "#2563EB" }}>({selectedDate || dataAsOf})</span>
            </div>
          </div>

          <div style={{ height: 180 }}>
            <ReactECharts
              option={donutChartOption}
              style={{ height: "100%", width: "100%" }}
              notMerge={true}
            />
          </div>

          <div className="donut-stat-row">
            <div
              className="donut-stat-item"
              onClick={() => {
                setActiveTab("stock");
                setStockPnlFilter("profit");
              }}
            >
              <div className="donut-stat-left">
                <span className="donut-badge-dot" style={{ background: "#10B981" }} />
                <span>盈利股票 {selectedDateDistribution.profitCount} 只</span>
              </div>
              <div className="donut-stat-right color-profit">
                {fmtSignedMoney(selectedDateDistribution.profitAmount)}
              </div>
            </div>

            <div
              className="donut-stat-item"
              onClick={() => {
                setActiveTab("stock");
                setStockPnlFilter("loss");
              }}
            >
              <div className="donut-stat-left">
                <span className="donut-badge-dot" style={{ background: "#EF4444" }} />
                <span>亏损股票 {selectedDateDistribution.lossCount} 只</span>
              </div>
              <div className="donut-stat-right color-loss">
                {fmtSignedMoney(selectedDateDistribution.lossAmount)}
              </div>
            </div>

            <div
              className="donut-stat-item"
              onClick={() => {
                setActiveTab("stock");
                setStockPnlFilter("flat");
              }}
            >
              <div className="donut-stat-left">
                <span className="donut-badge-dot" style={{ background: "#94A3B8" }} />
                <span>持平股票 {selectedDateDistribution.flatCount} 只</span>
              </div>
              <div className="donut-stat-right" style={{ color: "#64748B" }}>
                ${fmtMoney(selectedDateDistribution.flatAmount)}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 6. Right Side Detail Drawer */}
      {drawerOpen && (
        <div className="detail-drawer-overlay" onClick={() => setDrawerOpen(false)}>
          <div className="detail-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                {drawerMode === "date" && (
                  <div className="drawer-nav">
                    <button
                      className="drawer-nav-btn"
                      onClick={() => handleDrawerNav(1)}
                      title="前一交易日"
                    >
                      <ChevronLeft size={16} />
                    </button>
                    <button
                      className="drawer-nav-btn"
                      onClick={() => handleDrawerNav(-1)}
                      title="后一交易日"
                    >
                      <ChevronRight size={16} />
                    </button>
                  </div>
                )}
                <div style={{ fontSize: 16, fontWeight: 700, color: "#1E293B" }}>
                  {drawerMode === "date" && `单日收益详情 · ${drawerTargetDate}`}
                  {drawerMode === "channel" && `渠道收益详情 · ${drawerTargetChannel}`}
                  {drawerMode === "stock" && `标的历史收益 · ${drawerTargetStock}`}
                </div>
              </div>
              <button className="drawer-close-btn" onClick={() => setDrawerOpen(false)}>
                <X size={16} />
              </button>
            </div>

            <div className="drawer-body">
              {drawerMode === "date" && (
                <>
                  {/* Date Summary Header in Drawer */}
                  {(() => {
                    const ds = dateSummariesDesc.find((d) => d.date === drawerTargetDate);
                    return ds ? (
                      <div
                        style={{
                          background: "#F8FAFC",
                          padding: "14px 16px",
                          borderRadius: 8,
                          border: "1px solid #E2E8F0",
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                        }}
                      >
                        <div>
                          <div style={{ fontSize: 12, color: "#64748B" }}>当日总收益</div>
                          <div
                            style={{
                              fontSize: 20,
                              fontWeight: 700,
                              color: ds.dailyProfit >= 0 ? "#10B981" : "#EF4444",
                            }}
                          >
                            {fmtSignedMoney(ds.dailyProfit)}
                          </div>
                        </div>
                        <div style={{ textAlign: "right" }}>
                          <div style={{ fontSize: 12, color: "#64748B" }}>当日收益率</div>
                          <div
                            style={{
                              fontSize: 15,
                              fontWeight: 600,
                              color: ds.profitRate >= 0 ? "#10B981" : "#EF4444",
                            }}
                          >
                            {fmtPercent(ds.profitRate)}
                          </div>
                        </div>
                      </div>
                    ) : null;
                  })()}

                  {/* Sub-tabs in Date Drawer: By Stock / By Channel */}
                  <div style={{ display: "flex", gap: 8, borderBottom: "1px solid #E2E8F0", paddingBottom: 6 }}>
                    <button
                      className={`table-tab-btn ${drawerTab === "stock" ? "active" : ""}`}
                      onClick={() => setDrawerTab("stock")}
                    >
                      按股票 ({selectedDateRows.length})
                    </button>
                    <button
                      className={`table-tab-btn ${drawerTab === "channel" ? "active" : ""}`}
                      onClick={() => setDrawerTab("channel")}
                    >
                      按渠道
                    </button>
                  </div>

                  {drawerTab === "stock" ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {sortedSelectedDateRows.map((r, i) => (
                        <div
                          key={i}
                          style={{
                            padding: "10px 12px",
                            borderRadius: 8,
                            background: "#F8FAFC",
                            border: "1px solid #E2E8F0",
                            cursor: "pointer",
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                          }}
                          onClick={() => {
                            setDrawerTargetStock(r.symbol);
                            setDrawerMode("stock");
                          }}
                        >
                          <div>
                            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                              <span style={{ fontWeight: 700, color: "#2563EB", fontSize: 13 }}>
                                {r.symbol}
                              </span>
                              <span style={{ fontSize: 11, color: "#64748B" }}>({r.channel})</span>
                            </div>
                            <div style={{ fontSize: 11, color: "#94A3B8", marginTop: 2 }}>
                              持仓: {fmtMoney(r.endQty)} 股 · 收盘价: ${fmtMoney(r.closePrice)}
                            </div>
                          </div>
                          <div style={{ textAlign: "right" }}>
                            <div
                              style={{
                                fontSize: 13,
                                fontWeight: 600,
                                color: r.dailyProfit >= 0 ? "#10B981" : "#EF4444",
                              }}
                            >
                              {fmtSignedMoney(r.dailyProfit)}
                            </div>
                            <div
                              style={{
                                fontSize: 11,
                                fontWeight: 500,
                                color: r.profitRate >= 0 ? "#10B981" : "#EF4444",
                              }}
                            >
                              {fmtPercent(r.profitRate)}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {channelSummaries.map((c) => {
                        const cRows = selectedDateRows.filter((r) => r.channel === c.channel);
                        const cProfit = cRows.reduce((acc, cur) => acc + cur.dailyProfit, 0);
                        return (
                          <div
                            key={c.channel}
                            style={{
                              padding: "10px 12px",
                              borderRadius: 8,
                              background: "#F8FAFC",
                              border: "1px solid #E2E8F0",
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                            }}
                          >
                            <span style={{ fontWeight: 600, fontSize: 13 }}>{c.channel}</span>
                            <span
                              style={{
                                fontWeight: 600,
                                fontSize: 13,
                                color: cProfit >= 0 ? "#10B981" : "#EF4444",
                              }}
                            >
                              {fmtSignedMoney(cProfit)}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              )}

              {drawerMode === "channel" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div
                    style={{
                      background: "#F8FAFC",
                      padding: "14px 16px",
                      borderRadius: 8,
                      border: "1px solid #E2E8F0",
                    }}
                  >
                    <div style={{ fontSize: 13, color: "#64748B" }}>渠道累计收益</div>
                    <div
                      style={{
                        fontSize: 22,
                        fontWeight: 700,
                        marginTop: 4,
                        color:
                          (channelSummaries.find((c) => c.channel === drawerTargetChannel)?.cumProfit || 0) >= 0
                            ? "#10B981"
                            : "#EF4444",
                      }}
                    >
                      {fmtSignedMoney(
                        channelSummaries.find((c) => c.channel === drawerTargetChannel)?.cumProfit || 0
                      )}
                    </div>
                  </div>

                  <div style={{ fontSize: 13, fontWeight: 600, color: "#1E293B" }}>近期每日表现</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {channelDailyList
                      .filter((cd) => cd.channel === drawerTargetChannel)
                      .slice(0, 15)
                      .map((cd, i) => (
                        <div
                          key={i}
                          style={{
                            padding: "8px 10px",
                            borderRadius: 6,
                            background: "#F8FAFC",
                            display: "flex",
                            justifyContent: "space-between",
                            fontSize: 12,
                          }}
                        >
                          <span style={{ color: "#475569" }}>{cd.date}</span>
                          <span
                            style={{
                              fontWeight: 600,
                              color: cd.dailyProfit >= 0 ? "#10B981" : "#EF4444",
                            }}
                          >
                            {fmtSignedMoney(cd.dailyProfit)} ({fmtPercent(cd.profitRate)})
                          </span>
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {drawerMode === "stock" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {(() => {
                    const stockRows = rawData
                      .filter((r) => r.symbol === drawerTargetStock)
                      .sort((a, b) => b.date.localeCompare(a.date));
                    const latest = stockRows[0];
                    const stockCumProfit = stockRows.reduce((acc, cur) => acc + cur.dailyProfit, 0);

                    return (
                      <>
                        <div
                          style={{
                            background: "#F8FAFC",
                            padding: "14px 16px",
                            borderRadius: 8,
                            border: "1px solid #E2E8F0",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between" }}>
                            <span style={{ fontSize: 18, fontWeight: 700, color: "#2563EB" }}>
                              {drawerTargetStock}
                            </span>
                            <span style={{ fontSize: 12, color: "#64748B" }}>
                              分类: {latest?.category || "—"}
                            </span>
                          </div>
                          <div style={{ marginTop: 8, fontSize: 12, color: "#475569" }}>
                            期间累计贡献:{" "}
                            <strong
                              style={{
                                color: stockCumProfit >= 0 ? "#10B981" : "#EF4444",
                              }}
                            >
                              {fmtSignedMoney(stockCumProfit)}
                            </strong>
                          </div>
                        </div>

                        <div style={{ fontSize: 13, fontWeight: 600, color: "#1E293B" }}>
                          历史每日收益记录
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                          {stockRows.slice(0, 20).map((r, i) => (
                            <div
                              key={i}
                              style={{
                                padding: "8px 10px",
                                borderRadius: 6,
                                background: "#F8FAFC",
                                display: "flex",
                                justifyContent: "space-between",
                                fontSize: 12,
                              }}
                            >
                              <div style={{ display: "flex", gap: 8 }}>
                                <span style={{ color: "#475569" }}>{r.date}</span>
                                <span style={{ color: "#94A3B8" }}>{r.channel}</span>
                              </div>
                              <span
                                style={{
                                  fontWeight: 600,
                                  color: r.dailyProfit >= 0 ? "#10B981" : "#EF4444",
                                }}
                              >
                                {fmtSignedMoney(r.dailyProfit)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </>
                    );
                  })()}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
