import React from "react";
import ReactECharts from "echarts-for-react";

interface OpenHoldingsDashboardProps {
  data: any;
  activeBroker: string;
  activeIndustry: string;
  selectedStockType: string;
  selectedCurrency: string;
  searchKeyword: string;
  trendWindow: string;
  showPersonSummary?: boolean;
  onBrokerChange: (broker: string) => void;
  onIndustryChange: (industry: string) => void;
  onStockTypeChange: (stockType: string) => void;
  onCurrencyChange: (currency: string) => void;
  onSearchChange: (search: string) => void;
  onResetFilters: () => void;
  onTrendWindowChange: (win: string) => void;
}

const DONUT_COLORS = ["#2563EB", "#8B5CF6", "#10B981", "#F59E0B", "#06B6D4", "#94A3B8"];

const fmtMoney = (val: any): string => {
  const num = Number(val || 0);
  return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const fmtSignedMoney = (val: any): string => {
  const num = Number(val || 0);
  return `${num > 0 ? "+" : ""}${fmtMoney(num)}`;
};

const fmtPct = (val: any, signed = false): string => {
  if (val == null || isNaN(Number(val))) return "0.00%";
  const num = Number(val) * 100;
  return `${signed && num > 0 ? "+" : ""}${num.toFixed(2)}%`;
};

export const OpenHoldingsDashboard: React.FC<OpenHoldingsDashboardProps> = ({
  data,
  activeBroker,
  activeIndustry,
  selectedCurrency,
  searchKeyword,
  trendWindow,
  showPersonSummary = false,
  onBrokerChange,
  onIndustryChange,
  onCurrencyChange,
  onSearchChange,
  onResetFilters,
  onTrendWindowChange,
}) => {
  if (!data) return null;

  const cards = data.cards || {};
  const brokerDist: any[] = data.broker_distribution || [];
  const industryDist: any[] = data.industry_distribution || [];
  const personSummary: any[] = data.person_summary || [];
  const mvTrend = data.mv_trend || { series: [] };
  const avail = data.available_filters || {};

  const mv = Number(cards.total_market_value || 0);
  const cost = Number(cards.total_cost_amount || 0);
  const fpnl = Number(cards.floating_pnl || 0);
  const rate = Number(cards.return_rate || 0);
  const count = Number(cards.stock_count || 0);
  const top5 = Number(cards.top5_concentration || 0);

  const pnlColor = fpnl > 0 ? "#16A34A" : fpnl < 0 ? "#DC2626" : "#475569";
  const rateColor = rate > 0 ? "#16A34A" : rate < 0 ? "#DC2626" : "#475569";

  const makeDonutOption = (items: any[], centerValue: number, subtitle: string) => ({
    color: DONUT_COLORS,
    tooltip: {
      trigger: "item",
      formatter: (p: any) => `${p.name}: ${fmtMoney(p.value)} (${p.percent}%)`,
    },
    series: [
      {
        type: "pie",
        radius: ["60%", "82%"],
        center: ["50%", "50%"],
        avoidLabelOverlap: false,
        label: { show: false },
        data: items.map((it) => ({
          name: it.name,
          value: Number(it.market_value || 0),
        })),
      },
    ],
    graphic: [
      {
        type: "text",
        left: "center",
        top: subtitle ? "42%" : "46%",
        style: {
          text: fmtMoney(centerValue),
          textAlign: "center",
          fontSize: 13,
          fontWeight: "bold",
          fill: "#0F172A",
        },
      },
      ...(subtitle
        ? [
            {
              type: "text",
              left: "center",
              top: "55%",
              style: {
                text: subtitle,
                textAlign: "center",
                fontSize: 10,
                fill: "#64748B",
              },
            },
          ]
        : []),
    ],
  });

  const trendSeries: any[] = mvTrend.series || [];
  const trendOption = {
    grid: { left: 52, right: 16, top: 18, bottom: 26 },
    tooltip: {
      trigger: "axis",
      formatter: (params: any) => {
        const pt = Array.isArray(params) ? params[0] : params;
        if (!pt) return "";
        return `<div style="font-size:12px;"><b>${pt.axisValue}</b><br/>持仓市值: <b>${fmtMoney(pt.data)}</b></div>`;
      },
    },
    xAxis: {
      type: "category",
      data: trendSeries.map((s) => s.date),
      axisLabel: { fontSize: 10, color: "#64748B" },
      axisLine: { lineStyle: { color: "#E2E8F0" } },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: {
        fontSize: 10,
        color: "#64748B",
        formatter: (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(0)}K` : `${v}`),
      },
      splitLine: { lineStyle: { color: "#F1F5F9" } },
    },
    series: [
      {
        name: "持仓市值",
        type: "line",
        smooth: true,
        symbol: "circle",
        symbolSize: 4,
        lineStyle: { color: "#2563EB", width: 2 },
        itemStyle: { color: "#2563EB" },
        areaStyle: {
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(37, 99, 235, 0.22)" },
              { offset: 1, color: "rgba(37, 99, 235, 0.01)" },
            ],
          },
        },
        data: trendSeries.map((s) => Number(s.market_value || 0)),
      },
    ],
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      {/* 一、顶部六张统计卡片 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(175px, 1fr))",
          gap: "10px",
        }}
      >
        {[
          { title: "持仓总市值 (USD)", value: fmtMoney(mv), color: "#0F172A", badge: "Σ", badgeBg: "#EFF6FF", badgeFg: "#2563EB" },
          { title: "持仓总成本 (USD)", value: fmtMoney(cost), color: "#0F172A", badge: "↗", badgeBg: "#EFF6FF", badgeFg: "#2563EB" },
          { title: "浮动盈亏 (USD)", value: fmtSignedMoney(fpnl), color: pnlColor, badge: "±", badgeBg: fpnl >= 0 ? "#F0FDF4" : "#FEF2F2", badgeFg: pnlColor },
          { title: "持仓收益率", value: fmtPct(rate, true), color: rateColor, badge: "%", badgeBg: rate >= 0 ? "#F0FDF4" : "#FEF2F2", badgeFg: rateColor },
          { title: "持仓股票数量", value: `${count} 只`, color: "#0F172A", badge: "#", badgeBg: "#EFF6FF", badgeFg: "#2563EB" },
          { title: "前五大持仓集中度", value: fmtPct(top5, false), color: "#0F172A", badge: "◔", badgeBg: "#EFF6FF", badgeFg: "#2563EB" },
        ].map((c, idx) => (
          <div
            key={idx}
            style={{
              background: "#FFFFFF",
              border: "1px solid #E2E8F0",
              borderRadius: "10px",
              padding: "12px 14px",
              display: "flex",
              alignItems: "center",
              gap: "10px",
            }}
          >
            <div
              style={{
                width: "32px",
                height: "32px",
                borderRadius: "8px",
                background: c.badgeBg,
                color: c.badgeFg,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontWeight: 700,
                fontSize: "14px",
                flexShrink: 0,
              }}
            >
              {c.badge}
            </div>
            <div>
              <div style={{ fontSize: "12px", color: "#64748B", fontWeight: 600 }}>{c.title}</div>
              <div className="font-mono" style={{ fontSize: "18px", fontWeight: 800, color: c.color, marginTop: "2px" }}>
                {c.value}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* 二、筛选区域（不重复增加人员筛选框，统一读取全局人员筛选） */}
      <div
        style={{
          background: "#FFFFFF",
          border: "1px solid #E2E8F0",
          borderRadius: "10px",
          padding: "10px 14px",
          display: "flex",
          alignItems: "center",
          gap: "12px",
          flexWrap: "wrap",
        }}
      >
        <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "#475569", fontWeight: 600 }}>
          <span>券商渠道</span>
          <select
            aria-label="持有中券商筛选"
            value={activeBroker}
            onChange={(e) => onBrokerChange(e.target.value)}
            className="input-control"
            style={{ fontSize: "12px", padding: "5px 10px", minWidth: "105px" }}
          >
            <option value="">全部</option>
            {(avail.brokers || []).map((b: string) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>

        <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "#475569", fontWeight: 600 }}>
          <span>类型</span>
          <select
            aria-label="持有中类型筛选"
            value={activeIndustry}
            onChange={(e) => onIndustryChange(e.target.value)}
            className="input-control"
            style={{ fontSize: "12px", padding: "5px 10px", minWidth: "105px" }}
          >
            <option value="">全部</option>
            {(avail.industries || []).map((ind: string) => (
              <option key={ind} value={ind}>
                {ind}
              </option>
            ))}
          </select>
        </label>

        <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "#475569", fontWeight: 600 }}>
          <span>币种</span>
          <select
            aria-label="持有中币种筛选"
            value={selectedCurrency}
            onChange={(e) => onCurrencyChange(e.target.value)}
            className="input-control"
            style={{ fontSize: "12px", padding: "5px 10px", minWidth: "85px" }}
          >
            <option value="">全部</option>
            {(avail.currencies || ["USD"]).map((cur: string) => (
              <option key={cur} value={cur}>
                {cur}
              </option>
            ))}
          </select>
        </label>

        <input
          aria-label="持有中股票搜索"
          type="search"
          className="input-control"
          placeholder="搜索股票名称 / Ticker"
          value={searchKeyword}
          onChange={(e) => onSearchChange(e.target.value)}
          style={{ fontSize: "12px", padding: "5px 12px", flex: 1, minWidth: "180px" }}
        />

        <button
          type="button"
          onClick={onResetFilters}
          className="btn btn-quiet"
          style={{ fontSize: "12px", padding: "5px 12px" }}
        >
          重置筛选
        </button>
      </div>

      {/* 三、券商持仓分布 + 行业持仓分布 + 持仓市值趋势 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(310px, 1fr))",
          gap: "12px",
        }}
      >
        {/* 1. 券商持仓分布 */}
        <div
          style={{
            background: "#FFFFFF",
            border: "1px solid #E2E8F0",
            borderRadius: "10px",
            padding: "14px 16px",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
            <span style={{ fontSize: "14px", fontWeight: 700, color: "#0F172A" }}>持仓分布 (按券商渠道)</span>
            <span style={{ fontSize: "11px", color: "#94A3B8" }}>点击联动筛选</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", minHeight: "195px" }}>
            <div style={{ width: "165px", height: "185px", flexShrink: 0 }}>
              <ReactECharts
                option={makeDonutOption(brokerDist, mv, "持仓市值")}
                style={{ width: "100%", height: "100%" }}
                onEvents={{
                  click: (params: any) => {
                    if (params?.name) {
                      onBrokerChange(activeBroker === params.name ? "" : params.name);
                    }
                  },
                }}
              />
            </div>
            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "6px" }}>
              {brokerDist.map((item, idx) => {
                const isSelected = activeBroker && activeBroker === item.name;
                return (
                  <div
                    key={item.name}
                    onClick={() => onBrokerChange(isSelected ? "" : item.name)}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      fontSize: "12px",
                      padding: "4px 6px",
                      borderRadius: "6px",
                      cursor: "pointer",
                      background: isSelected ? "#EFF6FF" : "transparent",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <span
                        style={{
                          width: "8px",
                          height: "8px",
                          borderRadius: "50%",
                          background: DONUT_COLORS[idx % DONUT_COLORS.length],
                        }}
                      />
                      <span style={{ fontWeight: isSelected ? 700 : 600, color: isSelected ? "#2563EB" : "#1E293B" }}>
                        {item.name}
                      </span>
                    </div>
                    <div className="font-mono" style={{ display: "flex", gap: "8px", fontSize: "11px", color: "#334155" }}>
                      <span>{fmtPct(item.weight)}</span>
                      <span style={{ fontWeight: 600 }}>{fmtMoney(item.market_value)}</span>
                      <span style={{ color: "#64748B" }}>{item.stock_count} 只</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* 2. 类型持仓分布 */}
        <div
          style={{
            background: "#FFFFFF",
            border: "1px solid #E2E8F0",
            borderRadius: "10px",
            padding: "14px 16px",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
            <span style={{ fontSize: "14px", fontWeight: 700, color: "#0F172A" }}>持仓分布 (按类型)</span>
            <span style={{ fontSize: "11px", color: "#94A3B8" }}>点击联动筛选</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", minHeight: "195px" }}>
            <div style={{ width: "165px", height: "185px", flexShrink: 0 }}>
              <ReactECharts
                option={makeDonutOption(industryDist, mv, "")}
                style={{ width: "100%", height: "100%" }}
                onEvents={{
                  click: (params: any) => {
                    if (params?.name) {
                      onIndustryChange(activeIndustry === params.name ? "" : params.name);
                    }
                  },
                }}
              />
            </div>
            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "6px" }}>
              {industryDist.map((item, idx) => {
                const isSelected = activeIndustry && activeIndustry === item.name;
                return (
                  <div
                    key={item.name}
                    onClick={() => onIndustryChange(isSelected ? "" : item.name)}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      fontSize: "12px",
                      padding: "4px 6px",
                      borderRadius: "6px",
                      cursor: "pointer",
                      background: isSelected ? "#EFF6FF" : "transparent",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <span
                        style={{
                          width: "8px",
                          height: "8px",
                          borderRadius: "50%",
                          background: DONUT_COLORS[idx % DONUT_COLORS.length],
                        }}
                      />
                      <span style={{ fontWeight: isSelected ? 700 : 600, color: isSelected ? "#2563EB" : "#1E293B" }}>
                        {item.name}
                      </span>
                    </div>
                    <div className="font-mono" style={{ display: "flex", gap: "8px", fontSize: "11px", color: "#334155" }}>
                      <span>{fmtPct(item.weight)}</span>
                      <span style={{ fontWeight: 600 }}>{fmtMoney(item.market_value)}</span>
                      <span style={{ color: "#64748B" }}>{item.stock_count} 只</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* 3. 持仓市值趋势 */}
        <div
          style={{
            background: "#FFFFFF",
            border: "1px solid #E2E8F0",
            borderRadius: "10px",
            padding: "14px 16px",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
            <span style={{ fontSize: "14px", fontWeight: 700, color: "#0F172A" }}>持仓市值趋势</span>
            <div style={{ display: "flex", gap: "4px" }}>
              {[
                { key: "1m", label: "近1月" },
                { key: "3m", label: "近3月" },
                { key: "6m", label: "近6月" },
                { key: "ytd", label: "今年" },
                { key: "all", label: "全部" },
              ].map((w) => (
                <button
                  key={w.key}
                  type="button"
                  onClick={() => onTrendWindowChange(w.key)}
                  style={{
                    border: trendWindow === w.key ? "1px solid #2563EB" : "1px solid #E2E8F0",
                    background: trendWindow === w.key ? "#2563EB" : "#F8FAFC",
                    color: trendWindow === w.key ? "#FFFFFF" : "#475569",
                    borderRadius: "5px",
                    padding: "2px 7px",
                    fontSize: "11px",
                    fontWeight: trendWindow === w.key ? 700 : 600,
                    cursor: "pointer",
                  }}
                >
                  {w.label}
                </button>
              ))}
            </div>
          </div>
          {mvTrend.has_incomplete_data && mvTrend.incomplete_note && (
            <div
              style={{
                background: "#FFFBEB",
                color: "#B45309",
                border: "1px solid #FDE68A",
                borderRadius: "6px",
                padding: "3px 8px",
                fontSize: "11px",
                marginBottom: "4px",
              }}
            >
              {mvTrend.incomplete_note}
            </div>
          )}
          <div style={{ flex: 1, minHeight: "175px" }}>
            {trendSeries.length > 0 ? (
              <ReactECharts option={trendOption} style={{ width: "100%", height: "185px" }} />
            ) : (
              <div style={{ height: "185px", display: "flex", alignItems: "center", justifyContent: "center", color: "#94A3B8", fontSize: "12px" }}>
                当前筛选范围暂无历史市值快照数据
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 四、各人员持仓情况（当全局人员选择「全部」或「他人」时展示） */}
      {showPersonSummary && personSummary.length > 0 && (
        <div
          style={{
            background: "#FFFFFF",
            border: "1px solid #E2E8F0",
            borderRadius: "10px",
            padding: "12px 16px",
            display: "flex",
            flexDirection: "column",
            gap: "8px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "14px", fontWeight: 700, color: "#0F172A" }}>各人员持仓情况</span>
            <span style={{ fontSize: "11px", color: "#64748B" }}>当前全局人员为多人员范围</span>
          </div>
          <div className="table-container" style={{ border: "1px solid #E2E8F0", borderRadius: "8px" }}>
            <table className="finance-table" style={{ fontSize: "12px" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "center" }}>归属人员</th>
                  <th style={{ textAlign: "center" }}>持仓市值</th>
                  <th style={{ textAlign: "center" }}>持仓成本</th>
                  <th style={{ textAlign: "center" }}>浮动盈亏</th>
                  <th style={{ textAlign: "center" }}>持仓收益率</th>
                  <th style={{ textAlign: "center" }}>持仓占比</th>
                  <th style={{ textAlign: "center" }}>持仓股票数</th>
                  <th style={{ textAlign: "center" }}>盈利股票数</th>
                  <th style={{ textAlign: "center" }}>亏损股票数</th>
                </tr>
              </thead>
              <tbody>
                {personSummary.map((pr, idx) => {
                  const pPnl = Number(pr.floating_pnl || 0);
                  const pRate = pr.return_rate != null ? Number(pr.return_rate) : null;
                  const pColor = pPnl > 0 ? "#16A34A" : pPnl < 0 ? "#DC2626" : "#475569";
                  return (
                    <tr key={pr.person_id ?? idx}>
                      <td style={{ textAlign: "center", fontWeight: 600 }}>{pr.person_name || pr.name}</td>
                      <td className="font-mono" style={{ textAlign: "center", fontWeight: 700 }}>{fmtMoney(pr.market_value)}</td>
                      <td className="font-mono" style={{ textAlign: "center" }}>{fmtMoney(pr.cost_amount)}</td>
                      <td className="font-mono" style={{ textAlign: "center", fontWeight: 700, color: pColor }}>{fmtSignedMoney(pPnl)}</td>
                      <td className="font-mono" style={{ textAlign: "center", fontWeight: 600, color: pColor }}>{pRate != null ? fmtPct(pRate, true) : "—"}</td>
                      <td className="font-mono" style={{ textAlign: "center" }}>{fmtPct(pr.weight)}</td>
                      <td className="font-mono" style={{ textAlign: "center" }}>{pr.stock_count ?? 0}</td>
                      <td className="font-mono" style={{ textAlign: "center", color: "#16A34A", fontWeight: 600 }}>{pr.profit_count ?? 0}</td>
                      <td className="font-mono" style={{ textAlign: "center", color: "#DC2626", fontWeight: 600 }}>{pr.loss_count ?? 0}</td>
                    </tr>
                  );
                })}
                <tr style={{ background: "#F8FAFC", fontWeight: 700 }}>
                  <td style={{ textAlign: "center" }}>合计</td>
                  <td className="font-mono" style={{ textAlign: "center" }}>{fmtMoney(mv)}</td>
                  <td className="font-mono" style={{ textAlign: "center" }}>{fmtMoney(cost)}</td>
                  <td className="font-mono" style={{ textAlign: "center", color: pnlColor }}>{fmtSignedMoney(fpnl)}</td>
                  <td className="font-mono" style={{ textAlign: "center", color: rateColor }}>{fmtPct(rate, true)}</td>
                  <td className="font-mono" style={{ textAlign: "center" }}>100.00%</td>
                  <td className="font-mono" style={{ textAlign: "center" }}>{count}</td>
                  <td className="font-mono" style={{ textAlign: "center", color: "#16A34A" }}>
                    {personSummary.reduce((acc, p) => acc + Number(p.profit_count || 0), 0)}
                  </td>
                  <td className="font-mono" style={{ textAlign: "center", color: "#DC2626" }}>
                    {personSummary.reduce((acc, p) => acc + Number(p.loss_count || 0), 0)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
