import React, { useEffect, useState } from "react";
import { Calendar, AlertCircle } from "lucide-react";
import { api } from "../api/client";

export const EarningsView: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .earningsCalendar()
      .then((res) => {
        if (res.status === "ok") {
          setData(res);
        }
      })
      .catch((err) => console.error("Failed to load earnings calendar:", err))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "300px", color: "var(--brand)" }}>
        正在同步持仓标的财报日历与 EPS 预期...
      </div>
    );
  }

  const items = data?.calendar || data?.items || [];

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", overflowY: "auto" }}>
      <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
        <Calendar size={18} color="var(--brand)" />
        <span>当前持仓标的财报披露日历与 EPS 业绩追踪</span>
      </div>

      <div className="table-container">
        <table className="finance-table">
          <thead>
            <tr>
              <th>标的代码</th>
              <th>财报公布日期</th>
              <th>发布时段</th>
              <th style={{ textAlign: "right" }}>当前持有股数</th>
              <th style={{ textAlign: "right" }}>持仓市值 (USD)</th>
              <th style={{ textAlign: "right" }}>EPS 预期值</th>
              <th style={{ textAlign: "right" }}>EPS 实际值</th>
              <th style={{ textAlign: "right" }}>业绩意外幅度</th>
              <th>倒计时 / 状态</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td colSpan={9} style={{ textAlign: "center", padding: "40px", color: "var(--text-muted)" }}>
                  近 90 天内暂无财报公布计划或数据已全部归档
                </td>
              </tr>
            ) : (
              items.map((r: any, idx: number) => {
                const surprise = r.surprise ? Number(r.surprise) : null;
                const isSurprisePositive = surprise != null && surprise > 0;
                return (
                  <tr key={idx}>
                    <td style={{ fontWeight: 700, color: "var(--text-heading)" }}>{r.symbol}</td>
                    <td>{r.date || r.earnings_date}</td>
                    <td>
                      <span style={{ fontSize: "11px", padding: "2px 6px", borderRadius: "4px", background: "var(--hover-bg)" }}>
                        {r.time === "bmo" ? "开盘前 (BMO)" : r.time === "amc" ? "盘后 (AMC)" : r.time || "盘后"}
                      </span>
                    </td>
                    <td className="font-mono" style={{ textAlign: "right" }}>
                      {Number(r.shares || r.quantity || 0).toLocaleString()}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                      ${Number(r.market_value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right" }}>
                      {r.eps_estimate != null ? `$${r.eps_estimate}` : "—"}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                      {r.eps_actual != null ? `$${r.eps_actual}` : "—"}
                    </td>
                    <td
                      className={`font-mono ${isSurprisePositive ? "profit-text" : surprise != null ? "loss-text" : ""}`}
                      style={{ textAlign: "right" }}
                    >
                      {surprise != null ? `${surprise > 0 ? "+" : ""}${surprise.toFixed(2)}%` : "—"}
                    </td>
                    <td>
                      <span
                        style={{
                          fontSize: "11px",
                          fontWeight: 600,
                          padding: "2px 6px",
                          borderRadius: "4px",
                          background: r.days_until <= 3 ? "var(--error-bg)" : "var(--table-alternate)",
                          color: r.days_until <= 3 ? "var(--loss-color)" : "var(--text-muted)",
                        }}
                      >
                        {r.status || (r.days_until != null ? `${r.days_until} 天后` : "已公布")}
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
