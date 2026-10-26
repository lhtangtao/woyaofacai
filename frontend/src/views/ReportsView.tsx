import React, { useEffect, useState } from "react";
import { Download, Search, Table, AlertCircle } from "lucide-react";
import { api } from "../api/client";
import { ReportDetailData, ReportMeta } from "../types";

interface ReportsViewProps {
  initialReportName?: string;
  personId: number | null;
  reportsList: ReportMeta[];
}

export const ReportsView: React.FC<ReportsViewProps> = ({
  initialReportName = "当前持仓",
  personId,
  reportsList,
}) => {
  const [currentReport, setCurrentReport] = useState<string>(initialReportName);
  const [reportData, setReportData] = useState<ReportDetailData | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialReportName) {
      setCurrentReport(initialReportName);
    }
  }, [initialReportName]);

  const loadReport = () => {
    setLoading(true);
    setError(null);
    api
      .reportDetail(currentReport, personId, search)
      .then((res) => {
        if (res.status === "ok") {
          setReportData(res);
        } else {
          setError(res.message || "未能加载报表数据");
        }
      })
      .catch((err) => {
        setError(err.message || "请求报表失败");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadReport();
  }, [currentReport, personId]);

  const handleExportCsv = () => {
    window.open(`/api/v1/export/report/${encodeURIComponent(currentReport)}`, "_blank");
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "10px", overflow: "hidden" }}>
      {/* Report Switcher & Filter Toolbar */}
      <div
        className="card-panel"
        style={{
          padding: "10px 14px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "12px",
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <Table size={16} color="var(--brand)" />
          <span style={{ fontSize: "13px", fontWeight: 600 }}>选择分析报表:</span>
          <select
            value={currentReport}
            onChange={(e) => setCurrentReport(e.target.value)}
            className="input-control"
            style={{ fontWeight: 600, minWidth: "160px" }}
          >
            {reportsList.map((r) => (
              <option key={r.name} value={r.name}>
                {r.name}
              </option>
            ))}
          </select>
        </div>

        {/* Search & Export */}
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <div style={{ position: "relative" }}>
            <input
              type="text"
              placeholder="在当前报表中筛选..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && loadReport()}
              className="input-control"
              style={{ width: "200px", paddingRight: "28px" }}
            />
            <Search
              size={14}
              color="var(--text-muted)"
              style={{ position: "absolute", right: "8px", top: "50%", transform: "translateY(-50%)" }}
            />
          </div>

          <button onClick={loadReport} className="btn">
            筛选
          </button>

          <button onClick={handleExportCsv} className="btn btn-primary" title="导出此报表 CSV">
            <Download size={14} />
            <span>导出 CSV</span>
          </button>
        </div>
      </div>

      {/* Table Surface */}
      <div className="table-container">
        {loading ? (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "300px", color: "var(--brand)" }}>
            正在计算报表数据...
          </div>
        ) : error ? (
          <div style={{ padding: "40px 20px", textAlign: "center", color: "var(--loss-color)" }}>
            <AlertCircle size={28} style={{ marginBottom: "8px" }} />
            <div>{error}</div>
          </div>
        ) : reportData && reportData.columns ? (
          <table className="finance-table">
            <thead>
              <tr>
                {reportData.columns.map((c, i) => (
                  <th key={i}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {reportData.rows.length === 0 ? (
                <tr>
                  <td colSpan={reportData.columns.length} style={{ textAlign: "center", padding: "40px", color: "var(--text-muted)" }}>
                    无数据
                  </td>
                </tr>
              ) : (
                reportData.rows.map((row, rowIdx) => (
                  <tr key={rowIdx}>
                    {row.map((cell, colIdx) => {
                      const isNumeric = typeof cell === "number";
                      const strCell = cell == null ? "—" : String(cell);
                      const isProfit = isNumeric && cell > 0;
                      const isLoss = isNumeric && cell < 0;

                      // Check if column name suggests profit/loss
                      const colName = reportData.columns[colIdx] || "";
                      const isPnLCol = colName.includes("盈亏") || colName.includes("收益") || colName.includes("涨跌");

                      return (
                        <td
                          key={colIdx}
                          className={isNumeric ? "font-mono" : ""}
                          style={{
                            textAlign: isNumeric ? "right" : "left",
                            color: isPnLCol && isProfit ? "var(--profit-color)" : isPnLCol && isLoss ? "var(--loss-color)" : "inherit",
                            fontWeight: isPnLCol && (isProfit || isLoss) ? 600 : "normal",
                          }}
                        >
                          {isNumeric ? cell.toLocaleString(undefined, { minimumFractionDigits: colName.includes("率") ? 2 : 2, maximumFractionDigits: 4 }) : strCell}
                        </td>
                      );
                    })}
                  </tr>
                ))
              )}
              {/* Optional Summary Row */}
              {reportData.summary_row && (
                <tr style={{ background: "var(--table-alternate)", fontWeight: 700, borderTop: "2px solid var(--border-color)" }}>
                  {reportData.summary_row.map((sumCell, i) => (
                    <td key={i} className="font-mono" style={{ textAlign: typeof sumCell === "number" ? "right" : "left" }}>
                      {sumCell == null ? "" : String(sumCell)}
                    </td>
                  ))}
                </tr>
              )}
            </tbody>
          </table>
        ) : null}
      </div>

      {/* Row count footer */}
      {reportData && (
        <div style={{ fontSize: "12px", color: "var(--text-muted)", padding: "0 4px" }}>
          共 {reportData.rows.length} 行记录 · 口径：EXCEL_COMPAT · 财务缺失值保留为 —
        </div>
      )}
    </div>
  );
};
