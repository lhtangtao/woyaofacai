import React, { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";
import { TrendingUp, TrendingDown, DollarSign, PieChart, ShieldCheck } from "lucide-react";
import { api } from "../api/client";

interface DashboardViewProps {
  personId: number | null;
}

export const DashboardView: React.FC<DashboardViewProps> = ({ personId }) => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = () => {
    setLoading(true);
    setError(null);
    api
      .overview(personId)
      .then((res) => {
        if (res.status === "ok") {
          setData(res);
        } else {
          setError(res.message || "获取资产概况失败");
        }
      })
      .catch((err) => {
        setError(err.message || "请求概况出错");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchData();
  }, [personId]);

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "300px" }}>
        <span style={{ color: "var(--brand)" }}>正在加载全时期财务计算结果...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: "20px", color: "var(--loss-color)", background: "var(--error-bg)", borderRadius: "8px" }}>
        {error}
      </div>
    );
  }

  const kpis = data?.kpis || {};
  const metrics = data?.metrics || {};
  const totalAsset = metrics["当前总资产"] || kpis.total_asset || 0;
  const totalProfit = metrics["全时期总盈亏"] || kpis.total_profit || 0;
  const totalProfitRate = metrics["全时期收益率"] || kpis.total_profit_rate || 0;
  const todayProfit = metrics["今日盈亏"] || kpis.today_profit || 0;
  const todayProfitRate = metrics["当日收益率"] || kpis.today_profit_rate || 0;
  const netInvested = metrics["回本缺口"] || kpis.net_invested || 0;
  const holdingCost = metrics["当前持仓总成本"];

  const isProfit = totalProfit >= 0;
  const isTodayProfit = todayProfit >= 0;

  // Pie chart option for category
  const categoryOption = {
    tooltip: { trigger: "item", formatter: "{b}: ${c} ({d}%)" },
    series: [
      {
        type: "pie",
        radius: ["40%", "70%"],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 },
        label: { show: true, formatter: "{b}: {d}%" },
        data: data?.category_distribution || [],
      },
    ],
  };

  // Pie chart option for brokers
  const channelOption = {
    tooltip: { trigger: "item", formatter: "{b}: ${c} ({d}%)" },
    series: [
      {
        type: "pie",
        radius: ["40%", "70%"],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 },
        label: { show: true, formatter: "{b}: {d}%" },
        data: data?.channel_distribution || [],
      },
    ],
  };

  return (
    <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "16px" }}>
      {/* KPI Cards Row (Matching native MetricCard) */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: "12px",
        }}
      >
        {/* Total Assets */}
        <div className="card-panel">
          <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "4px" }}>
            当前总资产
          </div>
          <div className="font-mono" style={{ fontSize: "28px", fontWeight: 700, color: "var(--text-heading)" }}>
            ${Number(totalAsset).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-subtle)", marginTop: "4px" }}>
            基准货币 USD · EXCEL_COMPAT
          </div>
        </div>

        {/* All-time PnL */}
        <div className="card-panel">
          <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "4px" }}>
            全时期总盈亏
          </div>
          <div
            className={`font-mono ${isProfit ? "profit-text" : "loss-text"}`}
            style={{ fontSize: "28px", fontWeight: 700 }}
          >
            {isProfit ? "+" : ""}${Number(totalProfit).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div style={{ fontSize: "11px", color: isProfit ? "var(--profit-color)" : "var(--loss-color)", marginTop: "4px" }}>
            累计收益率: {Number(totalProfitRate) > 0 ? "+" : ""}{(Number(totalProfitRate) * 100).toFixed(2)}%
          </div>
        </div>

        {/* Today PnL */}
        <div className="card-panel">
          <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "4px" }}>
            今日盈亏 (实盘行情)
          </div>
          <div
            className={`font-mono ${isTodayProfit ? "profit-text" : "loss-text"}`}
            style={{ fontSize: "28px", fontWeight: 700 }}
          >
            {isTodayProfit ? "+" : ""}${Number(todayProfit).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div style={{ fontSize: "11px", color: isTodayProfit ? "var(--profit-color)" : "var(--loss-color)", marginTop: "4px" }}>
            当日涨跌幅: {Number(todayProfitRate) > 0 ? "+" : ""}{(Number(todayProfitRate) * 100).toFixed(2)}%
          </div>
        </div>

        {/* Gap to break-even */}
        <div className="card-panel">
          <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "4px" }}>
            回本缺口 / 累计净投入
          </div>
          <div className="font-mono" style={{ fontSize: "28px", fontWeight: 700, color: "var(--text-heading)" }}>
            ${Number(netInvested).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-subtle)", marginTop: "4px" }}>
            有效出入金与净本金
          </div>
        </div>

      <div className="card-panel" title="当前持仓累计买入金额减累计卖出金额之和，沿用原账本历史基准及费用口径；不含已清仓分组。">
        <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "4px" }}>当前持仓总成本</div>
        <div className="font-mono" style={{ fontSize: "28px", fontWeight: 700, color: "var(--text-heading)" }}>
          {holdingCost == null ? "— 数据不完整" : `$${Number(holdingCost).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
        </div>
        <div style={{ fontSize: "11px", color: "var(--text-subtle)", marginTop: "4px" }}>当前持仓净投入，卖出回款已抵扣</div>
      </div>
      </div>
      {/* Distribution Charts Row */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
        <div className="card-panel" style={{ height: "320px", display: "flex", flexDirection: "column" }}>
          <div style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-heading)", marginBottom: "8px" }}>
            标的分类持仓分布
          </div>
          <div style={{ flex: 1 }}>
            <ReactECharts option={categoryOption} style={{ height: "100%", width: "100%" }} />
          </div>
        </div>

        <div className="card-panel" style={{ height: "320px", display: "flex", flexDirection: "column" }}>
          <div style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-heading)", marginBottom: "8px" }}>
            券商真实渠道分布
          </div>
          <div style={{ flex: 1 }}>
            <ReactECharts option={channelOption} style={{ height: "100%", width: "100%" }} />
          </div>
        </div>
      </div>

      {/* Baseline Verification Notice */}
      <div
        style={{
          background: "var(--table-alternate)",
          border: "1px solid var(--border-color)",
          borderRadius: "8px",
          padding: "10px 14px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: "12px",
          color: "var(--text-muted)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <ShieldCheck size={16} color="#16A34A" />
          <span>核心财务数据源自 money.sqlite3，与原 Mac 原生桌面端使用相同计算引擎，两端数据完全一致。</span>
        </div>
        <span>基准估值日期: {data?.data_asof || data?.as_of || "最新交易日"}</span>
      </div>
    </div>
  );
};
