import React, { useEffect, useState } from "react";
import { Calendar, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { ErrorBoundary } from "../components/ErrorBoundary";

export const EarningsView: React.FC = () => {
  const today = new Date();
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const fetchCalendar = (y: number, m: number, dir = sortDir) => {
    setLoading(true);
    api
      .earningsCalendar(y, m, dir)
      .then((res: any) => {
        if (res.status === "ok") {
          setData(res);
          if (res.sort_dir) setSortDir(res.sort_dir as "asc" | "desc");
        }
      })
      .catch((err) => console.error("Failed to load earnings calendar:", err))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchCalendar(year, month, sortDir);
  }, [year, month]);

  const handleToggleSort = () => {
    const newDir = sortDir === "desc" ? "asc" : "desc";
    setSortDir(newDir);
    fetchCalendar(year, month, newDir);
  };

  const handlePrevMonth = () => {
    if (month === 1) {
      setYear(year - 1);
      setMonth(12);
    } else {
      setMonth(month - 1);
    }
  };

  const handleNextMonth = () => {
    if (month === 12) {
      setYear(year + 1);
      setMonth(1);
    } else {
      setMonth(month + 1);
    }
  };

  const handleCurrentMonth = () => {
    setYear(today.getFullYear());
    setMonth(today.getMonth() + 1);
  };

  const items = data?.events || data?.calendar || data?.items || [];

  return (
    <ErrorBoundary fallbackTitle="持仓财报日历渲染异常">
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", overflowY: "auto" }}>
        {/* Header & Month Navigation */}
        <div
          className="card-panel"
          style={{
            padding: "10px 14px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: "10px",
          }}
        >
          <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
            <Calendar size={18} color="var(--brand)" />
            <span>持仓标的财报披露日历 ({year} 年 {month} 月)</span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <button onClick={handlePrevMonth} className="btn" title="上个月">
              <ChevronLeft size={14} />
              <span>上月</span>
            </button>
            <button onClick={handleCurrentMonth} className="btn" title="返回本月">
              本月
            </button>
            <button onClick={handleNextMonth} className="btn" title="下个月">
              <span>下月</span>
              <ChevronRight size={14} />
            </button>
            <button onClick={() => fetchCalendar(year, month)} className="btn btn-quiet" title="刷新">
              <RefreshCw size={14} />
            </button>
          </div>
        </div>

        {/* Calendar / Events Table */}
        <div className="table-container">
          {loading ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "300px", color: "var(--brand)" }}>
              正在查询 {year} 年 {month} 月持仓标的财报披露记录...
            </div>
          ) : (
            <table className="finance-table">
              <thead>
                <tr>
                  <th>标的代码</th>
                  <th>公司名称</th>
                  <th
                    onClick={handleToggleSort}
                    style={{ cursor: "pointer", userSelect: "none" }}
                    title="点击切换披露日期排序"
                  >
                    <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                      <span>披露日期</span>
                      <span style={{ fontSize: "12px", color: "var(--brand, #2563eb)", fontWeight: 700 }}>
                        {sortDir === "desc" ? "↓" : "↑"}
                      </span>
                    </span>
                  </th>
                  <th>发布时段</th>
                  <th style={{ textAlign: "right" }}>EPS 预期值</th>
                  <th style={{ textAlign: "right" }}>EPS 实际值</th>
                  <th>披露状态</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={7} style={{ textAlign: "center", padding: "50px", color: "var(--text-muted)" }}>
                      {year} 年 {month} 月暂无持仓标的财报披露记录（真实行情缓存数据）
                    </td>
                  </tr>
                ) : (
                  items.map((r: any, idx: number) => {
                    return (
                      <tr key={idx}>
                        <td style={{ fontWeight: 700, color: "var(--text-heading)" }}>{r.symbol}</td>
                        <td>{r.company_name || r.name || "—"}</td>
                        <td>{r.date || "—"}</td>
                        <td>
                          <span style={{ fontSize: "11px", padding: "2px 6px", borderRadius: "4px", background: "var(--hover-bg)" }}>
                            {r.market_session || r.time || "盘后"}
                          </span>
                        </td>
                        <td className="font-mono" style={{ textAlign: "right" }}>
                          {r.estimated_eps != null ? `$${Number(r.estimated_eps).toFixed(2)}` : "—"}
                        </td>
                        <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                          {r.actual_eps != null ? `$${Number(r.actual_eps).toFixed(2)}` : "—"}
                        </td>
                        <td>
                          <span
                            style={{
                              fontSize: "11px",
                              fontWeight: 600,
                              padding: "2px 6px",
                              borderRadius: "4px",
                              background: r.status === "已公布" ? "var(--hover-bg)" : "var(--table-alternate)",
                              color: r.status === "已公布" ? "var(--profit-color)" : "var(--text-muted)",
                            }}
                          >
                            {r.status || "待发布"}
                          </span>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </ErrorBoundary>
  );
};
