import React, { useEffect, useState, useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { requestApi } from "../api/client";
import "./today-pnl.css";

type Cell = string | number | null;

interface PeopleSnapshot {
  status: string;
  columns: string[];
  rows: Cell[][];
  broker?: Cell[];
  detail_columns?: string[];
  detail_rows?: Cell[][];
  note: string;
  quote_status: string;
  as_of?: string;
}

const todayDate = () =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());

const amount = (v: Cell | undefined, percent = false): string => {
  if (v == null || v === "" || v === "—") return "—";
  const num = typeof v === "number" ? v : Number(v);
  if (isNaN(num)) return String(v);
  return num.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: "exceptZero",
    ...(percent ? { style: "percent" as const } : {}),
  });
};

const formatRate = (rate: Cell | undefined): string => {
  if (rate == null || rate === "" || rate === "—") return "—";
  const num = typeof rate === "number" ? rate : Number(rate);
  if (isNaN(num)) return "—";
  return num.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: "exceptZero",
    style: "percent",
  });
};

const formatPlain = (v: Cell | undefined, decimals = 2): string => {
  if (v == null || v === "" || v === "—") return "—";
  const num = typeof v === "number" ? v : Number(v);
  if (isNaN(num)) return String(v);
  return num.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
};

type TableSort = { column: number; descending: boolean } | null;
const sortRows = (rows: Cell[][], sort: TableSort) => {
  if (!sort) return rows;
  return [...rows].sort((a, b) => {
    const left = a[sort.column], right = b[sort.column];
    if (left == null || right == null) return left == null ? (right == null ? 0 : 1) : -1;
    const result = String(left).trim() !== "" && String(right).trim() !== "" &&
      Number.isFinite(Number(left)) && Number.isFinite(Number(right)) ? Number(left) - Number(right) :
      String(left).localeCompare(String(right), "zh-CN");
    return sort.descending ? -result : result;
  });
};
const nextSort = (current: TableSort, column: number): TableSort => ({ column,
  descending: current?.column === column ? !current.descending : true });

export const BrokerPeoplePnl: React.FC = () => {
  const [day, setDay] = useState(todayDate);
  const [data, setData] = useState<PeopleSnapshot | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState<"daily" | "lifetime">("daily");
  const [order, setOrder] = useState<"profit" | "loss" | "abs">("profit");
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null);
  const [labelMode, setLabelMode] = useState<"both" | "amount" | "rate">("both");
  const [summarySort, setSummarySort] = useState<TableSort>(null);
  const [detailSort, setDetailSort] = useState<TableSort>(null);

  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    setLoading(true);
    setError("");

    const load = async () => {
      if (busy) return;
      busy = true;
      try {
        const result = await requestApi<PeopleSnapshot>(
          `/api/v1/brokers/people?as_of=${day}`,
          { signal: controller.signal }
        );
        if (!controller.signal.aborted) {
          setData(result);
          setError("");
        }
      } catch (e: any) {
        if (!controller.signal.aborted) {
          setError(e.message || "人员盈亏读取失败");
        }
      } finally {
        busy = false;
        if (!controller.signal.aborted) setLoading(false);
      }
    };

    void load();
    const timer = window.setInterval(() => {
      if (!document.hidden) void load();
    }, 10000);

    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [day]);

  const colIdx = mode === "daily" ? 1 : 5;

  const getRowRate = (row: Cell[]): number | null => {
    if (mode === "daily") {
      const r = row[2];
      return r != null && r !== "" ? Number(r) : null;
    } else {
      const pnl = row[5] != null ? Number(row[5]) : null;
      const net = row[8] != null ? Number(row[8]) : null;
      return net && net > 0 && pnl != null ? pnl / net : null;
    }
  };

  const getBarLabel = (row: Cell[]): string => {
    const val = row[colIdx];
    if (val == null || val === "" || val === "—") return "— 数据不完整";
    const amtStr = amount(val);
    const rate = getRowRate(row);
    const rateStr = formatRate(rate);
    return labelMode === "amount" ? amtStr : labelMode === "rate" ? rateStr : `${amtStr}（${rateStr}）`;
  };

  // Process rows for bar chart
  const processedRows = useMemo(() => {
    if (!data?.rows) return [];
    // Filter visible rows: non-zero
    const filtered = data.rows.filter((r) => {
      const v = r[colIdx];
      if (v == null) return true;
      const num = Number(v);
      return !isNaN(num) && Math.abs(num) >= 0.005;
    });

    // Split known and null
    const known = filtered.filter((r) => r[colIdx] != null && !isNaN(Number(r[colIdx])));
    const unknown = filtered.filter((r) => r[colIdx] == null || isNaN(Number(r[colIdx])));

    known.sort((a, b) => {
      const va = Number(a[colIdx]);
      const vb = Number(b[colIdx]);
      if (order === "profit") return vb - va;
      if (order === "loss") return va - vb;
      return Math.abs(vb) - Math.abs(va);
    });

    return [...known, ...unknown];
  }, [data?.rows, colIdx, order]);

  // Metric cards computation matching desktop BrokerPeoplePage
  const cards = useMemo(() => {
    const broker = data?.broker;
    const rows = data?.rows || [];
    const validRows = rows.filter((r) => String(r[0]) !== "未分配／归属差异");

    if (mode === "daily") {
      const posCount = validRows.filter((r) => r[1] != null && Number(r[1]) > 0).length;
      const negCount = validRows.filter((r) => r[1] != null && Number(r[1]) < 0).length;
      return [
        {
          title: "渠道当日盈亏",
          value: amount(broker ? broker[1] : undefined),
          raw: broker ? broker[1] : null,
        },
        {
          title: "当日收益率",
          value: formatRate(broker ? broker[2] : undefined),
          raw: broker ? broker[2] : null,
        },
        {
          title: "盈利人数 / 亏损人数",
          value: `${posCount} / ${negCount}`,
          raw: null,
        },
        {
          title: "持仓市值（不含现金）",
          value: formatPlain(broker ? broker[3] : undefined),
          raw: null,
        },
      ];
    } else {
      return [
        {
          title: "渠道累计盈亏",
          value: amount(broker ? broker[5] : undefined),
          raw: broker ? broker[5] : null,
        },
        {
          title: "已清仓收益",
          value: amount(broker ? broker[6] : undefined),
          raw: broker ? broker[6] : null,
        },
        {
          title: "持仓浮动盈亏",
          value: amount(broker ? broker[7] : undefined),
          raw: broker ? broker[7] : null,
        },
        {
          title: "持仓市值（不含现金）",
          value: formatPlain(broker ? broker[3] : undefined),
          raw: null,
        },
      ];
    }
  }, [data?.broker, data?.rows, mode]);

  const handleBarClick = (personName: string) => {
    setSelectedPerson((prev) => (prev === personName ? null : personName));
  };

  const chartOptions = useMemo(() => {
    if (!processedRows.length) return null;
    const names = processedRows.map((r) => String(r[0]));
    const barData = processedRows.map((r) => {
      const val = r[colIdx];
      const num = val != null ? Number(val) : 0;
      const personName = String(r[0]);
      const isSelected = selectedPerson === personName;
      const isNegative = num < 0;

      let color = isNegative ? "#DC2626" : "#16A34A";
      if (selectedPerson && !isSelected) {
        color = "#9CA3AF";
      }

      return {
        value: val == null ? 0 : num,
        name: personName,
        itemStyle: { color, borderRadius: 2 },
        label: {
          show: true,
          position: isNegative ? "left" : "right",
          formatter: () => getBarLabel(r),
          color: "inherit",
          fontSize: 12,
        },
      };
    });

    return {
      animation: false,
      grid: { left: 110, right: 260, top: 25, bottom: 25 },
      xAxis: {
        type: "value",
        axisLine: { show: true },
        splitLine: { lineStyle: { type: "dashed", color: "var(--border-color, #e5e7eb)" } },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: names,
        axisTick: { show: false },
        axisLabel: {
          color: "var(--text-main, #374151)",
          fontSize: 13,
          fontWeight: 500,
        },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params: any[]) => {
          const idx = params[0]?.dataIndex;
          const row = processedRows[idx];
          if (!row) return "";
          const pName = row[0];
          const label = getBarLabel(row);
          return `<div style="font-weight:600;margin-bottom:4px;">${pName}</div><div>${mode === "daily" ? "当日盈亏" : "累计盈亏"}：${label}</div>`;
        },
      },
      series: [
        {
          type: "bar",
          barMaxWidth: 22,
          data: barData,
        },
      ],
    };
  }, [processedRows, colIdx, mode, selectedPerson, labelMode]);

  // Filtered details
  const filteredDetails = useMemo(() => {
    if (!data?.detail_rows) return [];
    const rows = selectedPerson ? data.detail_rows.filter((r) => String(r[0]) === selectedPerson) : data.detail_rows;
    return sortRows(rows, detailSort);
  }, [data?.detail_rows, selectedPerson, detailSort]);
  const summaryRows = sortRows(data?.rows || [], summarySort);

  const getColor = (raw: Cell | null) => {
    if (raw == null) return "inherit";
    const n = Number(raw);
    if (isNaN(n) || n === 0) return "inherit";
    return n > 0 ? "var(--profit-color, #16A34A)" : "var(--loss-color, #DC2626)";
  };

  return (
    <div className="today-pnl" style={{ gap: 12, paddingBottom: 24 }}>
      {/* Top Controls */}
      <div className="card-panel today-toolbar" style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontWeight: 700, fontSize: "15px" }}>全部人员 · 权益账本</span>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "13px" }}>
            截止日期
            <input
              aria-label="人员盈亏截止日期"
              className="input-control"
              type="date"
              value={day}
              max={todayDate()}
              onChange={(e) => {
                if (e.target.value) setDay(e.target.value);
              }}
            />
          </label>
          <select
            aria-label="盈亏模式"
            className="input-control"
            value={mode}
            onChange={(e) => setMode(e.target.value as "daily" | "lifetime")}
          >
            <option value="daily">当日盈亏</option>
            <option value="lifetime">累计盈亏</option>
          </select>
          <select
            aria-label="排序规则"
            className="input-control"
            value={order}
            onChange={(e) => setOrder(e.target.value as "profit" | "loss" | "abs")}
          >
            <option value="profit">盈利最多</option>
            <option value="loss">亏损最多</option>
            <option value="abs">盈亏幅度最大</option>
          </select>
          <select className="input-control" aria-label="柱图标签显示" value={labelMode}
            title="切换柱体旁的标签；柱长与排序仍按盈亏金额"
            onChange={e => setLabelMode(e.target.value as "both" | "amount" | "rate")}>
            <option value="both">金额和收益率</option><option value="amount">仅金额</option><option value="rate">仅收益率</option>
          </select>
        </div>
        <div>
          <button
            className="btn"
            onClick={() => {
              const url = `/api/v1/export/report/${encodeURIComponent("人员盈亏")}?as_of=${day}`;
              window.open(url, "_blank");
            }}
          >
            导出人员汇总…
          </button>
        </div>
      </div>

      {/* Quote Status */}
      <div className="card-panel today-quote" style={{ padding: "8px 12px", fontSize: "12px", color: "var(--text-muted)" }}>
        <span>{data?.quote_status || (loading ? "正在读取人员盈亏…" : "等待数据")}</span>
      </div>

      {/* Error Banner */}
      {error && (
        <div role="alert" className="today-warning" style={{ color: "var(--loss-color)" }}>
          {error}
        </div>
      )}

      {/* 4 Metric Cards */}
      <div className="today-cards">
        {cards.map((c, idx) => (
          <div className="card-panel today-card" key={idx}>
            <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>{c.title}</span>
            <strong style={{ fontSize: "19px", color: getColor(c.raw) }}>{c.value}</strong>
          </div>
        ))}
      </div>

      {/* Note Description */}
      {data?.note && (
        <p style={{ fontSize: "12px", color: "var(--text-muted)", lineHeight: 1.6, margin: "2px 0" }}>
          {data.note}
        </p>
      )}

      {/* Horizontal Bar Chart */}
      <section className="card-panel" style={{ padding: 16 }}>
        <h3 style={{ fontSize: "14px", fontWeight: 600, marginBottom: 12 }}>
          {mode === "daily" ? "人员当日盈亏分布" : "人员累计盈亏分布"}
          {selectedPerson && (
            <span style={{ fontSize: "12px", fontWeight: "normal", color: "var(--brand)", marginLeft: 8 }}>
              （已选中: {selectedPerson}，点击可取消）
            </span>
          )}
        </h3>
        {chartOptions ? (
          <ReactECharts
            notMerge
            style={{ height: Math.max(220, 52 + processedRows.length * 44) }}
            option={chartOptions}
            onEvents={{
              click: (params: any) => {
                if (params?.name) handleBarClick(params.name);
              },
            }}
          />
        ) : (
          <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
            暂无人员盈亏数据
          </div>
        )}
      </section>

      {/* Summary Table */}
      {data?.columns && data.rows && (
        <section className="card-panel" style={{ padding: 16 }}>
          <h3 style={{ fontSize: "14px", fontWeight: 600, marginBottom: 8 }}>人员盈亏汇总表</h3>
          <div className="table-container" style={{ overflowX: "auto" }}>
            <table className="finance-table">
              <thead>
                <tr>
                  {data.columns.map((c, i) => (
                    <th key={i} style={{ textAlign: i === 0 ? "left" : "right", whiteSpace: "nowrap", cursor: "pointer" }}
                      onClick={() => setSummarySort(current => nextSort(current, i))}>
                      {c}{summarySort?.column === i ? summarySort.descending ? " ↓" : " ↑" : " ↕"}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {summaryRows.map((row, rIdx) => {
                  const personName = String(row[0]);
                  const isSelected = selectedPerson === personName;
                  return (
                    <tr
                      key={rIdx}
                      style={{
                        cursor: "pointer",
                        background: isSelected ? "var(--brand-selected, rgba(59, 130, 246, 0.1))" : undefined,
                      }}
                      onClick={() => handleBarClick(personName)}
                    >
                      {row.map((val, cIdx) => {
                        let text = String(val ?? "—");
                        let color = "inherit";
                        if (cIdx === 0) {
                          text = String(val ?? "");
                        } else if (cIdx === 2) {
                          text = formatRate(val);
                          color = getColor(val);
                        } else {
                          text = formatPlain(val);
                          if (cIdx === 1 || cIdx === 4 || cIdx === 5 || cIdx === 6 || cIdx === 7) {
                            color = getColor(val);
                          }
                        }
                        return (
                          <td
                            key={cIdx}
                            className={cIdx > 0 ? "font-mono" : undefined}
                            style={{
                              textAlign: cIdx === 0 ? "left" : "right",
                              color,
                              fontWeight: cIdx === 0 ? 600 : undefined,
                              whiteSpace: "nowrap",
                            }}
                          >
                            {text}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Detail Table */}
      {data?.detail_columns && filteredDetails.length > 0 && (
        <section className="card-panel" style={{ padding: 16 }}>
          <h3 style={{ fontSize: "14px", fontWeight: 600, marginBottom: 8 }}>
            {selectedPerson || "全部人员"} · 股票盈亏明细
            <span style={{ fontSize: "12px", fontWeight: "normal", color: "var(--text-muted)", marginLeft: 8 }}>
              （点击上方人员柱或汇总行切换，再次点击恢复全部）
            </span>
          </h3>
          <div className="table-container" style={{ overflowX: "auto" }}>
            <table className="finance-table">
              <thead>
                <tr>
                  {data.detail_columns.map((c, i) => (
                    <th key={i} style={{ textAlign: i < 4 ? "left" : "right", whiteSpace: "nowrap", cursor: "pointer" }}
                      onClick={() => setDetailSort(current => nextSort(current, i))}>
                      {c}{detailSort?.column === i ? detailSort.descending ? " ↓" : " ↑" : " ↕"}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredDetails.map((row, rIdx) => (
                  <tr key={rIdx}>
                    {row.map((val, cIdx) => {
                      let text = String(val ?? "—");
                      let color = "inherit";
                      if (cIdx < 4) {
                        text = String(val ?? "");
                      } else if (cIdx === 8) {
                        text = formatRate(val);
                        color = getColor(val);
                      } else {
                        text = formatPlain(val);
                        if (cIdx === 7 || cIdx === 9) {
                          color = getColor(val);
                        }
                      }
                      return (
                        <td
                          key={cIdx}
                          className={cIdx >= 4 ? "font-mono" : undefined}
                          style={{
                            textAlign: cIdx < 4 ? "left" : "right",
                            color,
                            fontWeight: cIdx === 0 ? 600 : undefined,
                            whiteSpace: "nowrap",
                          }}
                        >
                          {text}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
};
