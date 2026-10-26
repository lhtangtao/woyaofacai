import React, { useEffect, useState } from "react";
import { Download, Search, Table, AlertCircle, ChevronLeft, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react";
import { api } from "../api/client";
import { ReportDetailData, ReportMeta } from "../types";
import { ErrorBoundary } from "../components/ErrorBoundary";

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

  // Pagination & Sort state
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [sortCol, setSortCol] = useState<string | undefined>(undefined);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  useEffect(() => {
    if (initialReportName) {
      setCurrentReport(initialReportName);
      setPage(1);
    }
  }, [initialReportName]);

  const loadReport = (targetPage = page) => {
    setLoading(true);
    setError(null);
    api
      .reportDetail(currentReport, personId, search, targetPage, pageSize, sortCol, sortDir)
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
    loadReport(1);
  }, [currentReport, personId, pageSize, sortCol, sortDir]);

  const handleSearchSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setPage(1);
    loadReport(1);
  };

  const handleSort = (colName: string) => {
    if (sortCol === colName) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortCol(colName);
      setSortDir("asc");
    }
    setPage(1);
  };

  const handlePageChange = (newPage: number) => {
    setPage(newPage);
    loadReport(newPage);
  };

  const handleExportCsv = () => {
    const params = new URLSearchParams();
    if (personId != null) params.set("person_id", String(personId));
    if (search.trim()) params.set("search", search.trim());
    window.open(`/api/v1/export/report/${encodeURIComponent(currentReport)}?${params.toString()}`, "_blank");
  };

  // Combine reports list ensuring 今日盈亏 is present
  const allReports = [...reportsList];
  if (!allReports.find((r) => r.name === "今日盈亏")) {
    allReports.unshift({
      name: "今日盈亏",
      group: "核心报表",
      description: "按持仓查看当日盈亏与累计盈亏",
    });
  }

  const rows = reportData?.rows || [];
  const columns = reportData?.columns || [];
  const totalRows = reportData?.total_rows ?? reportData?.total ?? rows.length;
  const totalPages = reportData?.total_pages || Math.max(1, Math.ceil(totalRows / pageSize));

  return (
    <ErrorBoundary fallbackTitle={`报表【${currentReport}】渲染异常`}>
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
              onChange={(e) => {
                setCurrentReport(e.target.value);
                setPage(1);
              }}
              className="input-control"
              style={{ fontWeight: 600, minWidth: "160px" }}
            >
              {allReports.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name}
                </option>
              ))}
            </select>
          </div>

          {/* Search, PageSize & Export */}
          <form onSubmit={handleSearchSubmit} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div style={{ position: "relative" }}>
              <input
                type="text"
                placeholder="在当前报表中筛选..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="input-control"
                style={{ width: "200px", paddingRight: "28px" }}
              />
              <Search
                size={14}
                color="var(--text-muted)"
                style={{ position: "absolute", right: "8px", top: "50%", transform: "translateY(-50%)" }}
              />
            </div>

            <button type="submit" className="btn">
              筛选
            </button>

            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setPage(1);
              }}
              className="input-control"
              style={{ width: "85px" }}
              title="每页条数"
            >
              <option value={25}>25 条/页</option>
              <option value={50}>50 条/页</option>
              <option value={100}>100 条/页</option>
              <option value={500}>500 条/页</option>
            </select>

            <button type="button" onClick={handleExportCsv} className="btn btn-primary" title="导出此报表 CSV">
              <Download size={14} />
              <span>导出 CSV</span>
            </button>
          </form>
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
          ) : reportData && columns.length > 0 ? (
            <table className="finance-table">
              <thead>
                <tr>
                  {columns.map((c, i) => {
                    const isSorted = sortCol === c;
                    return (
                      <th
                        key={i}
                        onClick={() => handleSort(c)}
                        style={{ cursor: "pointer", userSelect: "none" }}
                        title={`点击按 ${c} 排序`}
                      >
                        <div style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                          <span>{c}</span>
                          {isSorted ? (
                            sortDir === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />
                          ) : (
                            <ArrowUpDown size={11} style={{ opacity: 0.3 }} />
                          )}
                        </div>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={columns.length} style={{ textAlign: "center", padding: "40px", color: "var(--text-muted)" }}>
                      无匹配数据
                    </td>
                  </tr>
                ) : (
                  rows.map((row, rowIdx) => (
                    <tr key={rowIdx}>
                      {columns.map((colName, colIdx) => {
                        const cell = Array.isArray(row) ? row[colIdx] : (row as any)?.[colName];
                        const isNumeric = typeof cell === "number";
                        const strCell = cell == null ? "—" : String(cell);
                        const isProfit = isNumeric && cell > 0;
                        const isLoss = isNumeric && cell < 0;

                        // Check if column name suggests profit/loss
                        const isPnLCol =
                          colName.includes("盈亏") ||
                          colName.includes("收益") ||
                          colName.includes("涨跌") ||
                          colName.includes("回本");

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
                            {isNumeric
                              ? cell.toLocaleString(undefined, {
                                  minimumFractionDigits: colName.includes("率") || colName.includes("幅") || colName.includes("比") ? 2 : 2,
                                  maximumFractionDigits: 4,
                                })
                              : strCell}
                          </td>
                        );
                      })}
                    </tr>
                  ))
                )}
                {/* Summary Row */}
                {reportData.summary_row && (
                  <tr style={{ background: "var(--table-alternate)", fontWeight: 700, borderTop: "2px solid var(--border-color)" }}>
                    {columns.map((colName, i) => {
                      const sumCell = Array.isArray(reportData.summary_row)
                        ? reportData.summary_row[i]
                        : reportData.totals
                        ? reportData.totals[colName]
                        : null;
                      const isNumeric = typeof sumCell === "number";
                      return (
                        <td
                          key={i}
                          className="font-mono"
                          style={{
                            textAlign: isNumeric ? "right" : "left",
                            color: isNumeric && sumCell > 0 ? "var(--profit-color)" : isNumeric && sumCell < 0 ? "var(--loss-color)" : "inherit",
                          }}
                        >
                          {sumCell == null ? "" : isNumeric ? sumCell.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : String(sumCell)}
                        </td>
                      );
                    })}
                  </tr>
                )}
              </tbody>
            </table>
          ) : null}
        </div>

        {/* Footer with Row count & Pagination */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            fontSize: "12px",
            color: "var(--text-muted)",
            padding: "2px 4px",
          }}
        >
          <div>
            共 {totalRows} 行记录 · 口径：EXCEL_COMPAT {reportData?.note ? ` · ${reportData.note}` : ""}
          </div>

          {totalPages > 1 && (
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <button
                onClick={() => handlePageChange(Math.max(1, page - 1))}
                disabled={page <= 1}
                className="btn btn-quiet"
                style={{ padding: "4px 8px" }}
              >
                <ChevronLeft size={14} />
              </button>
              <span>
                第 {page} / {totalPages} 页
              </span>
              <button
                onClick={() => handlePageChange(Math.min(totalPages, page + 1))}
                disabled={page >= totalPages}
                className="btn btn-quiet"
                style={{ padding: "4px 8px" }}
              >
                <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>
      </div>
    </ErrorBoundary>
  );
};
