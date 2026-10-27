import React, { useEffect, useState } from "react";
import { Search, RotateCcw, ChevronLeft, ChevronRight, Download } from "lucide-react";
import { api } from "../api/client";
import { TradeDrawer } from "../components/TradeDrawer";
import { PersonItem, TradeItem } from "../types";

interface TradesViewProps {
  personId: number | null;
  persons: PersonItem[];
  accounts: string[];
}

export const TradesView: React.FC<TradesViewProps> = ({
  personId,
  persons,
  accounts,
}) => {
  const [trades, setTrades] = useState<TradeItem[]>([]);
  const [columns, setColumns] = useState<string[]>([
    "时间", "券商", "分类", "标的", "方向", "当前价", "价格", "数量", "金额",
    "标记", "持仓", "净投入", "成本", "操作类型", "对手方", "订单号", "来源"
  ]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [loading, setLoading] = useState(false);
  const [selectedTrade, setSelectedTrade] = useState<TradeItem | null>(null);

  // Sorting
  const [sortCol, setSortCol] = useState<string>("时间");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  // Filters
  const [filterBroker, setFilterBroker] = useState("");
  const [filterSide, setFilterSide] = useState("");
  const [filterSymbol, setFilterSymbol] = useState("");
  const [filterSearch, setFilterSearch] = useState("");

  const fetchTrades = (
    targetPage = page,
    targetSortCol = sortCol,
    targetSortDir = sortDir
  ) => {
    setLoading(true);
    api
      .trades({
        page: targetPage,
        size: pageSize,
        person_id: personId,
        broker: filterBroker || undefined,
        side: filterSide || undefined,
        symbol: filterSymbol || undefined,
        search: filterSearch || undefined,
        sort_col: targetSortCol,
        sort_dir: targetSortDir,
      })
      .then((res) => {
        if (res.status === "ok") {
          setTrades(res.trades || res.items || []);
          if (res.columns) setColumns(res.columns);
          setTotal(res.pagination?.total || res.total || 0);
          setPage(res.pagination?.page || targetPage);
          if (res.sort_col) setSortCol(res.sort_col);
          if (res.sort_dir) setSortDir(res.sort_dir as "asc" | "desc");
        }
      })
      .catch((err) => {
        console.error("Failed to load trades:", err);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchTrades(1, sortCol, sortDir);
  }, [personId, filterBroker, filterSide, filterSymbol]);

  const handleResetFilters = () => {
    setFilterBroker("");
    setFilterSide("");
    setFilterSymbol("");
    setFilterSearch("");
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      fetchTrades(1, sortCol, sortDir);
    }
  };

  const handleHeaderClick = (col: string) => {
    const sortableCols = [
      "时间", "券商", "分类", "标的", "方向", "数量", "价格", "金额",
      "操作类型", "对手方", "订单号"
    ];
    if (!sortableCols.includes(col)) return;

    let newDir: "asc" | "desc" = "asc";
    if (sortCol === col) {
      newDir = sortDir === "desc" ? "asc" : "desc";
    } else {
      newDir = col === "时间" ? "desc" : "asc";
    }

    setSortCol(col);
    setSortDir(newDir);
    setPage(1);
    fetchTrades(1, col, newDir);
  };

  const handleExportTradesCsv = () => {
    const params = new URLSearchParams();
    if (personId != null) params.set("person_id", String(personId));
    if (filterBroker) params.set("broker", filterBroker);
    if (filterSide) params.set("side", filterSide);
    if (filterSymbol) params.set("symbol", filterSymbol);
    if (filterSearch) params.set("search", filterSearch);
    if (sortCol) params.set("sort_col", sortCol);
    if (sortDir) params.set("sort_dir", sortDir);
    window.open(`/api/v1/export/trades?${params.toString()}`, "_blank");
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "10px", overflow: "hidden" }}>
      {/* Filter Card (Matching native filterCard) */}
      <div
        className="card-panel"
        style={{
          padding: "10px 14px",
          display: "flex",
          alignItems: "center",
          gap: "8px",
          flexWrap: "wrap",
        }}
      >
        {/* Broker Filter */}
        <select
          value={filterBroker}
          onChange={(e) => setFilterBroker(e.target.value)}
          className="input-control"
          style={{ width: "124px" }}
        >
          <option value="">全部券商</option>
          {accounts.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>

        {/* Side Filter */}
        <select
          value={filterSide}
          onChange={(e) => setFilterSide(e.target.value)}
          className="input-control"
          style={{ width: "90px" }}
        >
          <option value="">全部方向</option>
          <option value="买入">买入</option>
          <option value="卖出">卖出</option>
          <option value="转入">转入</option>
          <option value="转出">转出</option>
        </select>

        {/* Ticker Symbol Input */}
        <input
          type="text"
          placeholder="标的代码 (如 AAPL)"
          value={filterSymbol}
          onChange={(e) => setFilterSymbol(e.target.value.toUpperCase())}
          className="input-control"
          style={{ width: "130px" }}
        />

        {/* Full-text search */}
        <div style={{ position: "relative", flex: 1, minWidth: "150px" }}>
          <input
            type="text"
            placeholder="搜索代码、订单、来源... (按回车)"
            value={filterSearch}
            onChange={(e) => setFilterSearch(e.target.value)}
            onKeyDown={handleSearchKeyDown}
            className="input-control"
            style={{ width: "100%", paddingRight: "28px" }}
          />
          <Search
            size={14}
            color="var(--text-muted)"
            style={{ position: "absolute", right: "8px", top: "50%", transform: "translateY(-50%)" }}
          />
        </div>

        {/* Query button */}
        <button onClick={() => fetchTrades(1, sortCol, sortDir)} className="btn btn-primary" style={{ padding: "6px 14px" }}>
          查询
        </button>

        {/* Reset button */}
        <button onClick={handleResetFilters} className="btn" title="重置全部筛选">
          <RotateCcw size={14} />
          <span>重置</span>
        </button>

        {/* Export CSV button */}
        <button onClick={handleExportTradesCsv} className="btn" title="导出当前筛选和排序的交易明细 CSV">
          <Download size={14} />
          <span>导出 CSV</span>
        </button>
      </div>

      {/* Main Content Area: Splitter between Table and Drawer */}
      <div style={{ flex: 1, display: "flex", gap: "10px", overflow: "hidden", position: "relative" }}>
        {/* Table Area */}
        <div className="table-container">
          <table className="finance-table">
            <thead>
              <tr>
                {columns.map((col) => {
                  const isSortable = [
                    "时间", "券商", "分类", "标的", "方向", "数量", "价格", "金额",
                    "操作类型", "对手方", "订单号"
                  ].includes(col);
                  const isSorted = sortCol === col;
                  return (
                    <th
                      key={col}
                      onClick={() => isSortable && handleHeaderClick(col)}
                      style={{
                        cursor: isSortable ? "pointer" : "default",
                        userSelect: "none",
                      }}
                      title={isSortable ? `点击切换${col}排序` : undefined}
                    >
                      <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                        <span>{col}</span>
                        {isSorted && (
                          <span style={{ fontSize: "12px", color: "var(--brand, #2563eb)", fontWeight: 700 }}>
                            {sortDir === "desc" ? "↓" : "↑"}
                          </span>
                        )}
                      </span>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {trades.length === 0 ? (
                <tr>
                  <td colSpan={columns.length} style={{ textAlign: "center", padding: "40px 0", color: "var(--text-muted)" }}>
                    {loading ? "正在加载交易流水..." : "没有符合条件的交易记录"}
                  </td>
                </tr>
              ) : (
                trades.map((t) => {
                  const isSelected = (selectedTrade?.record_id || selectedTrade?.id) === (t.record_id || t.id);
                  const isBuy = t.side?.includes("买");
                  const isSell = t.side?.includes("卖");

                  return (
                    <tr
                      key={t.record_id || t.id}
                      onClick={() => setSelectedTrade(t)}
                      className={isSelected ? "selected" : ""}
                      style={{ cursor: "pointer" }}
                    >
                      {/* 时间 */}
                      <td style={{ fontSize: "12px", color: "var(--text-muted)" }}>{t.trade_date}</td>
                      {/* 券商 */}
                      <td style={{ fontWeight: 600 }}>{t.broker}</td>
                      {/* 分类 */}
                      <td>
                        <span
                          style={{
                            fontSize: "11px",
                            padding: "2px 6px",
                            borderRadius: "4px",
                            background: "var(--border-color)",
                          }}
                        >
                          {t.category || "—"}
                        </span>
                      </td>
                      {/* 标的 */}
                      <td style={{ fontWeight: 700, color: "var(--text-heading)" }}>{t.symbol}</td>
                      {/* 方向 */}
                      <td>
                        <span className={`badge ${isBuy ? "badge-buy" : isSell ? "badge-sell" : ""}`}>
                          {t.side}
                        </span>
                      </td>
                      {/* 当前价 */}
                      <td
                        className="font-mono"
                        style={{
                          textAlign: "right",
                          fontWeight: 600,
                          color:
                            t.current_price != null && t.price != null
                              ? isBuy
                                ? t.current_price > t.price
                                  ? "var(--profit-color, #16a34a)"
                                  : t.current_price < t.price
                                  ? "var(--loss-color, #dc2626)"
                                  : "inherit"
                                : t.current_price < t.price
                                ? "var(--profit-color, #16a34a)"
                                : t.current_price > t.price
                                ? "var(--loss-color, #dc2626)"
                                : "inherit"
                              : "inherit",
                        }}
                        title={
                          t.current_price != null && t.price != null
                            ? `当前价: $${t.current_price.toFixed(2)} | 成交均价: $${t.price.toFixed(4)} | 决策评估: ${
                                isBuy
                                  ? t.current_price > t.price
                                    ? "买入后上涨（做对）"
                                    : t.current_price < t.price
                                    ? "买入后下跌（做错）"
                                    : "持平"
                                  : t.current_price < t.price
                                  ? "卖出后下跌（卖得好）"
                                  : t.current_price > t.price
                                  ? "卖出后继续上涨（卖早了）"
                                  : "持平"
                              }`
                            : undefined
                        }
                      >
                        {t.current_price != null ? `$${t.current_price.toFixed(2)}` : "—"}
                      </td>
                      {/* 价格 */}
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        ${t.price?.toFixed(4)}
                      </td>
                      {/* 数量 */}
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {t.quantity?.toLocaleString()}
                      </td>
                      {/* 金额 */}
                      <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                        ${t.amount?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                      {/* 标记 */}
                      <td>{t.tag || "—"}</td>
                      {/* 持仓 */}
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {t.post_quantity != null ? t.post_quantity.toLocaleString() : "—"}
                      </td>
                      {/* 净投入 */}
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {t.post_net != null ? `$${t.post_net.toLocaleString()}` : "—"}
                      </td>
                      {/* 成本 */}
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {t.post_cost != null ? `$${t.post_cost.toFixed(4)}` : "—"}
                      </td>
                      {/* 操作类型 */}
                      <td>{t.kind || "市场交易"}</td>
                      {/* 对手方 */}
                      <td>{t.person_name || "—"}</td>
                      {/* 订单号 */}
                      <td className="font-mono" style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                        {t.order_id || "—"}
                      </td>
                      {/* 来源 */}
                      <td style={{ fontSize: "11px", color: "var(--text-subtle)", maxWidth: "160px", overflow: "hidden", textOverflow: "ellipsis" }} title={t.source}>
                        {t.source || "—"}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Slide-in Detail Drawer */}
        {selectedTrade && (
          <TradeDrawer trade={selectedTrade} onClose={() => setSelectedTrade(null)} />
        )}
      </div>

      {/* Pagination Bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "4px 8px",
          fontSize: "12px",
          color: "var(--text-muted)",
        }}
      >
        <div>
          共 <span style={{ fontWeight: 600, color: "var(--text-heading)" }}>{total.toLocaleString()}</span> 笔交易记录 · 第 {page} / {totalPages} 页
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <button
            onClick={() => fetchTrades(page - 1)}
            disabled={page <= 1 || loading}
            className="btn"
            style={{ padding: "4px 8px", height: "28px" }}
          >
            <ChevronLeft size={14} />
            <span>上一页</span>
          </button>
          <button
            onClick={() => fetchTrades(page + 1)}
            disabled={page >= totalPages || loading}
            className="btn"
            style={{ padding: "4px 8px", height: "28px" }}
          >
            <span>下一页</span>
            <ChevronRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
};
