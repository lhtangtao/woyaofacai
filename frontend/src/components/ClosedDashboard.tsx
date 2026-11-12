import React from "react";
import ReactECharts from "echarts-for-react";

interface ClosedDashboardProps {
  data: any;
  activeBroker: string;
  activePnlStatus: string;
  trendWindow: string;
  onBrokerClick: (broker: string) => void;
  onStructureClick: (status: string) => void;
  onTrendWindowChange: (win: string) => void;
}

const fmtMoney = (val: any, signed = false) => {
  if (val == null || isNaN(Number(val))) return "—";
  const n = Number(val);
  if (Math.abs(n) < 0.005) return "0.00";
  const absStr = Math.abs(n).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (n < 0) return `-${absStr}`;
  return signed && n > 0 ? `+${absStr}` : absStr;
};

const fmtRate = (val: any) => {
  if (val == null || isNaN(Number(val))) return "—";
  const pct = Number(val) * 100;
  if (Math.abs(pct) < 0.005) return "0.00%";
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
};

const pnlColor = (val: any) => {
  if (val == null || Math.abs(Number(val)) < 0.005) return "var(--text-muted, #64748b)";
  return Number(val) > 0 ? "var(--profit-color, #16a34a)" : "var(--loss-color, #dc2626)";
};

export const ClosedDashboard: React.FC<ClosedDashboardProps> = ({
  data,
  activeBroker,
  activePnlStatus,
  trendWindow,
  onBrokerClick,
  onStructureClick,
  onTrendWindowChange,
}) => {
  if (!data) return null;

  const cards = data.cards || {};
  const brokers: any[] = data.broker_summary || [];
  const brokerTotal = data.broker_total;
  const structure = data.structure || {};
  const monthly: any[] = data.monthly_trend || [];

  const kpiItems = [
    {
      label: "已清仓股票数",
      value: String(cards.closed_count ?? 0),
      color: "var(--text-heading, #0f172a)",
      tip: "同一股票在不同券商账户下分别统计，避免跨账户合并",
    },
    {
      label: "买入总额 (USD)",
      value: fmtMoney(cards.total_buy_amount),
      color: "var(--text-heading, #0f172a)",
    },
    {
      label: "卖出总额 (USD)",
      value: fmtMoney(cards.total_sell_amount),
      color: "var(--text-heading, #0f172a)",
    },
    {
      label: "已实现盈利",
      value: fmtMoney(cards.realized_profit, true),
      color: (cards.realized_profit || 0) > 0 ? "var(--profit-color, #16a34a)" : "var(--text-muted, #64748b)",
    },
    {
      label: "已实现亏损",
      value: fmtMoney(cards.realized_loss, true),
      color: (cards.realized_loss || 0) < 0 ? "var(--loss-color, #dc2626)" : "var(--text-muted, #64748b)",
    },
    {
      label: "净已实现盈亏",
      value: fmtMoney(cards.net_realized_pnl, true),
      color: pnlColor(cards.net_realized_pnl),
    },
    {
      label: "已实现收益率",
      value: fmtRate(cards.realized_pnl_rate),
      color: pnlColor(cards.realized_pnl_rate),
      tip: cards.rate_formula_note || "净已实现盈亏 ÷ 买入总额 × 100%",
    },
  ];

  const structItems = [
    {
      key: "win",
      label: "盈利股票",
      count: structure.win_count ?? 0,
      pct: structure.win_pct ?? 0,
      color: "#16a34a",
    },
    {
      key: "flat",
      label: "持平股票",
      count: structure.flat_count ?? 0,
      pct: structure.flat_pct ?? 0,
      color: "#94a3b8",
    },
    {
      key: "loss",
      label: "亏损股票",
      count: structure.loss_count ?? 0,
      pct: structure.loss_pct ?? 0,
      color: "#dc2626",
    },
  ];

  const donutOption = {
    animation: false,
    tooltip: {
      trigger: "item",
      formatter: (p: any) => `${p.name}：${p.value} 只 (${p.percent}%)<br/>点击筛选下方明细`,
    },
    series: [
      {
        name: "盈亏结构",
        type: "pie",
        radius: ["56%", "78%"],
        center: ["50%", "50%"],
        avoidLabelOverlap: false,
        label: { show: false },
        itemStyle: { borderColor: "#ffffff", borderWidth: 2 },
        data: structItems.map((it) => ({
          name: it.label,
          value: it.count,
          statusKey: it.key,
          itemStyle: {
            color: it.color,
            opacity: activePnlStatus && activePnlStatus !== it.key ? 0.35 : 1,
          },
        })),
      },
    ],
    graphic: [
      {
        type: "text",
        left: "center",
        top: "40%",
        style: {
          text: String(structure.total ?? 0),
          fontSize: 20,
          fontWeight: "bold",
          fill: "#0f172a",
          textAlign: "center",
        },
      },
      {
        type: "text",
        left: "center",
        top: "56%",
        style: {
          text: "清仓股票数",
          fontSize: 10,
          fill: "#64748b",
          textAlign: "center",
        },
      },
    ],
  };

  const barOption = {
    animation: false,
    grid: { left: 56, right: 12, top: 28, bottom: 26 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: any[]) => {
        const item = params?.[0];
        if (!item) return "";
        const raw = monthly[item.dataIndex];
        return `${raw.month}<br/>已实现盈亏：<b>${fmtMoney(raw.pnl, true)} USD</b><br/>卖出笔数：${raw.sell_count ?? 0} 笔`;
      },
    },
    xAxis: {
      type: "category",
      data: monthly.map((m) => m.month),
      axisLine: { lineStyle: { color: "#cbd5e1" } },
      axisTick: { show: false },
      axisLabel: { fontSize: 10, color: "#475569" },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "#f1f5f9" } },
      axisLabel: {
        fontSize: 10,
        color: "#64748b",
        formatter: (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 0 }),
      },
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 28,
        data: monthly.map((m) => {
          const val = Number(m.pnl || 0);
          return {
            value: val,
            itemStyle: {
              color: val >= 0 ? "#16a34a" : "#dc2626",
              borderRadius: val >= 0 ? [3, 3, 0, 0] : [0, 0, 3, 3],
            },
            label: {
              show: monthly.length <= 14,
              position: val >= 0 ? "top" : "bottom",
              fontSize: 9,
              fontWeight: 600,
              color: val >= 0 ? "#15803d" : "#b91c1c",
              formatter: () => `${val >= 0 ? "+" : ""}${Math.round(val).toLocaleString("en-US")}`,
            },
          };
        }),
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: "#94a3b8", width: 1.2, type: "solid" },
          label: { show: false },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      {/* 第一行：7 张核心统计卡片 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: "10px",
        }}
      >
        {kpiItems.map((card) => (
          <div
            key={card.label}
            title={card.tip}
            style={{
              background: "#ffffff",
              border: "1px solid var(--border-color, #e2e8f0)",
              borderRadius: "8px",
              padding: "12px 14px",
              display: "flex",
              flexDirection: "column",
              gap: "6px",
            }}
          >
            <span style={{ fontSize: "11px", color: "var(--text-muted, #64748b)", fontWeight: 500 }}>
              {card.label}
            </span>
            <strong
              className="font-mono"
              style={{ fontSize: "18px", fontWeight: 700, color: card.color }}
            >
              {card.value}
            </strong>
          </div>
        ))}
      </div>

      {/* 第二行：按渠道汇总 | 盈利亏损结构 | 每月已实现盈亏趋势 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
          gap: "12px",
          alignItems: "stretch",
        }}
      >
        {/* 左：按渠道汇总 */}
        <div
          style={{
            background: "#ffffff",
            border: "1px solid var(--border-color, #e2e8f0)",
            borderRadius: "8px",
            padding: "12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: "8px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "13px", fontWeight: 700, color: "#0f172a" }}>按渠道汇总</span>
            <span style={{ fontSize: "10px", color: "#94a3b8" }}>点击行筛选，点击合计恢复</span>
          </div>
          <div className="table-container" style={{ overflowX: "auto" }}>
            <table className="finance-table" style={{ fontSize: "11px", width: "100%" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>渠道</th>
                  <th style={{ textAlign: "right" }}>股票数</th>
                  <th style={{ textAlign: "right" }}>买入总额</th>
                  <th style={{ textAlign: "right" }}>卖出总额</th>
                  <th style={{ textAlign: "right" }}>已实现盈利</th>
                  <th style={{ textAlign: "right" }}>已实现亏损</th>
                  <th style={{ textAlign: "right" }}>净已实现盈亏</th>
                  <th style={{ textAlign: "right" }}>收益率</th>
                </tr>
              </thead>
              <tbody>
                {brokers.map((b) => {
                  const selected = activeBroker === b.channel;
                  return (
                    <tr
                      key={b.channel}
                      onClick={() => onBrokerClick(selected ? "" : b.channel)}
                      style={{
                        cursor: "pointer",
                        background: selected ? "#eff6ff" : undefined,
                      }}
                    >
                      <td style={{ textAlign: "left", fontWeight: 600, color: selected ? "#2563eb" : "#0f172a" }}>
                        {b.channel}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right" }}>{b.stock_count}</td>
                      <td className="font-mono" style={{ textAlign: "right" }}>{fmtMoney(b.buy_amount)}</td>
                      <td className="font-mono" style={{ textAlign: "right" }}>{fmtMoney(b.sell_amount)}</td>
                      <td className="font-mono" style={{ textAlign: "right", color: (b.realized_profit || 0) > 0 ? "#16a34a" : "#64748b" }}>
                        {fmtMoney(b.realized_profit, true)}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right", color: (b.realized_loss || 0) < 0 ? "#dc2626" : "#64748b" }}>
                        {fmtMoney(b.realized_loss, true)}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right", fontWeight: 700, color: pnlColor(b.net_pnl) }}>
                        {fmtMoney(b.net_pnl, true)}
                      </td>
                      <td className="font-mono" style={{ textAlign: "right", fontWeight: 600, color: pnlColor(b.pnl_rate) }}>
                        {fmtRate(b.pnl_rate)}
                      </td>
                    </tr>
                  );
                })}
                {brokers.length > 0 && brokerTotal && (
                  <tr
                    onClick={() => onBrokerClick("")}
                    style={{ background: "#f8fafc", fontWeight: 700, cursor: "pointer" }}
                  >
                    <td style={{ textAlign: "left" }}>合计</td>
                    <td className="font-mono" style={{ textAlign: "right" }}>{brokerTotal.stock_count}</td>
                    <td className="font-mono" style={{ textAlign: "right" }}>{fmtMoney(brokerTotal.buy_amount)}</td>
                    <td className="font-mono" style={{ textAlign: "right" }}>{fmtMoney(brokerTotal.sell_amount)}</td>
                    <td className="font-mono" style={{ textAlign: "right", color: (brokerTotal.realized_profit || 0) > 0 ? "#16a34a" : "#64748b" }}>
                      {fmtMoney(brokerTotal.realized_profit, true)}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right", color: (brokerTotal.realized_loss || 0) < 0 ? "#dc2626" : "#64748b" }}>
                      {fmtMoney(brokerTotal.realized_loss, true)}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right", color: pnlColor(brokerTotal.net_pnl) }}>
                      {fmtMoney(brokerTotal.net_pnl, true)}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right", color: pnlColor(brokerTotal.pnl_rate) }}>
                      {fmtRate(brokerTotal.pnl_rate)}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* 中：盈利 / 亏损结构 */}
        <div
          style={{
            background: "#ffffff",
            border: "1px solid var(--border-color, #e2e8f0)",
            borderRadius: "8px",
            padding: "12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: "6px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "13px", fontWeight: 700, color: "#0f172a" }}>盈利 / 亏损结构</span>
            <span style={{ fontSize: "10px", color: "#94a3b8" }}>点击扇区或图例联动明细</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", flex: 1, minHeight: "190px" }}>
            <div style={{ width: "48%", height: "190px" }}>
              <ReactECharts
                option={donutOption}
                style={{ width: "100%", height: "100%" }}
                onEvents={{
                  click: (params: any) => {
                    const statusKey = params?.data?.statusKey;
                    if (statusKey) onStructureClick(activePnlStatus === statusKey ? "" : statusKey);
                  },
                }}
              />
            </div>
            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "6px" }}>
              {structItems.map((it) => {
                const isSelected = activePnlStatus === it.key;
                return (
                  <button
                    key={it.key}
                    type="button"
                    onClick={() => onStructureClick(isSelected ? "" : it.key)}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "6px 8px",
                      borderRadius: "6px",
                      border: isSelected ? "1px solid #bfdbfe" : "1px solid transparent",
                      background: isSelected ? "#eff6ff" : "transparent",
                      cursor: "pointer",
                      fontSize: "12px",
                      opacity: activePnlStatus && !isSelected ? 0.5 : 1,
                    }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: "6px", color: "#0f172a" }}>
                      <span style={{ width: 8, height: 8, borderRadius: "50%", background: it.color }} />
                      {it.label}
                    </span>
                    <span className="font-mono" style={{ fontWeight: 600, color: "#0f172a" }}>
                      {it.count} ({Number(it.pct).toFixed(2)}%)
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* 右：每月已实现盈亏趋势 */}
        <div
          style={{
            background: "#ffffff",
            border: "1px solid var(--border-color, #e2e8f0)",
            borderRadius: "8px",
            padding: "12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: "6px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
            <span style={{ fontSize: "13px", fontWeight: 700, color: "#0f172a" }} title={data.attribution_note}>
              已实现盈亏趋势 ⓘ
            </span>
            <div style={{ display: "inline-flex", background: "#f1f5f9", padding: "2px", borderRadius: "6px", gap: "2px" }}>
              {([
                ["6m", "近6月"],
                ["12m", "近12月"],
                ["24m", "近24月"],
                ["all", "全部"],
              ] as const).map(([key, label]) => {
                const active = trendWindow === key;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => onTrendWindowChange(key)}
                    style={{
                      border: "none",
                      borderRadius: "4px",
                      padding: "2px 7px",
                      fontSize: "10px",
                      fontWeight: 600,
                      cursor: "pointer",
                      background: active ? "#2563eb" : "transparent",
                      color: active ? "#ffffff" : "#64748b",
                    }}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
          <div style={{ flex: 1, minHeight: "190px" }}>
            {monthly.length > 0 ? (
              <ReactECharts option={barOption} notMerge style={{ width: "100%", height: "195px" }} />
            ) : (
              <div style={{ height: "195px", display: "flex", alignItems: "center", justifyContent: "center", color: "#94a3b8", fontSize: "12px" }}>
                暂无月度已实现盈亏记录
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
