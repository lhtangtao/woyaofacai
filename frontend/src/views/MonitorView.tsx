import React, { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";
import {
  Activity,
  Plus,
  Trash2,
  X,
  TrendingUp,
  TrendingDown,
  Bell,
  LineChart,
} from "lucide-react";
import { api } from "../api/client";
import { WatchlistItem } from "../types";

export const MonitorView: React.FC<{ canWrite: boolean }> = ({ canWrite }) => {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [newSymbol, setNewSymbol] = useState("");
  const [adding, setAdding] = useState(false);
  const [watchlistError, setWatchlistError] = useState("");

  // Tab state
  const [tab, setTab] = useState<"watchlist" | "alerts">("watchlist");

  // Alert History state
  const [alerts, setAlerts] = useState<any[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(false);
  const [alertSortDir, setAlertSortDir] = useState<"asc" | "desc">("desc");

  // K-line modal state
  const [klineSymbol, setKlineSymbol] = useState<string | null>(null);
  const [klineData, setKlineData] = useState<any>(null);
  const [klineLoading, setKlineLoading] = useState(false);

  // Rule drawer state
  const [rulesOpen, setRulesOpen] = useState(false);
  const [rules, setRules] = useState<any[]>([]);

  // Watchlist 3-state sorting: asc -> desc -> clear
  const [watchSortKey, setWatchSortKey] = useState<string>("");
  const [watchSortDir, setWatchSortDir] = useState<"" | "asc" | "desc">("");
  const [kindFilter, setKindFilter] = useState<"STARRED" | "ALL">("STARRED");

  const toggleWatchSort = (key: string) => {
    if (watchSortKey !== key) {
      setWatchSortKey(key);
      setWatchSortDir("asc");
    } else if (watchSortDir === "asc") {
      setWatchSortDir("desc");
    } else {
      setWatchSortKey("");
      setWatchSortDir("");
    }
  };

  const renderSortHeader = (label: string, key: string, align: "left" | "right" | "center" = "left", minWidth = "85px") => {
    const active = watchSortKey === key;
    const arrow = active ? (watchSortDir === "asc" ? " ↑" : " ↓") : "";
    return (
      <th
        onClick={() => toggleWatchSort(key)}
        style={{ minWidth, textAlign: align, cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
        title="点击切换：正序 → 倒序 → 清除排序"
      >
        {label}
        {arrow && <span style={{ color: "var(--brand, #2563eb)", fontWeight: 700 }}>{arrow}</span>}
      </th>
    );
  };

  const sortedItems = React.useMemo(() => {
    const filtered = kindFilter === "STARRED" ? items.filter((it) => Boolean(it.starred)) : items;
    if (!watchSortKey || !watchSortDir) return filtered;
    const getSortVal = (r: WatchlistItem): number | string | null => {
      switch (watchSortKey) {
        case "symbol": return r.symbol || "";
        case "price": return r.price && r.price !== "—" ? Number(r.price) : null;
        case "change": return r.change && r.change !== "—" ? Number(r.change.replace("%", "")) : null;
        case "ath_high": return r.ath_high != null ? Number(r.ath_high) : null;
        case "drawdown_pct": return r.drawdown_pct != null ? Number(r.drawdown_pct) : null;
        case "ath_date": return r.ath_date || null;
        case "rsi": return r.rsi ? Number(r.rsi) : null;
        case "ma5": return r.ma5 ? Number(r.ma5) : null;
        case "ma10": return r.ma10 ? Number(r.ma10) : null;
        case "ma20": return r.ma20 ? Number(r.ma20) : null;
        case "ma50": return r.ma50 ? Number(r.ma50) : null;
        case "ma200": return r.ma200 ? Number(r.ma200) : null;
        case "ma250": return r.ma250 ? Number(r.ma250) : null;
        case "dev20": return r.dev20 != null ? Number(r.dev20) : null;
        case "dev50": return r.dev50 != null ? Number(r.dev50) : null;
        case "dev200": return r.dev200 != null ? Number(r.dev200) : null;
        case "dev250": return r.dev250 != null ? Number(r.dev250) : null;
        default: return (r as any)[watchSortKey] || null;
      }
    };
    const valid: WatchlistItem[] = [];
    const missing: WatchlistItem[] = [];
    filtered.forEach((it) => {
      const v = getSortVal(it);
      if (v === null || v === undefined || (typeof v === "number" && Number.isNaN(v))) {
        missing.push(it);
      } else {
        valid.push(it);
      }
    });
    const dir = watchSortDir === "desc" ? -1 : 1;
    valid.sort((a, b) => {
      const va = getSortVal(a)!;
      const vb = getSortVal(b)!;
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });
    return [...valid, ...missing];
  }, [items, kindFilter, watchSortKey, watchSortDir]);

  const fetchWatchlist = () => {
    setLoading(true);
    setWatchlistError("");
    api
      .monitoringWatchlist()
      .then((res) => {
        if (res.status === "ok") {
          setItems(res.items || []);
        }
      })
      .catch(() => setWatchlistError("观察列表读取失败，请刷新后重试。"))
      .finally(() => setLoading(false));
  };

  const fetchAlerts = (dir = alertSortDir) => {
    setAlertsLoading(true);
    api
      .monitoringAlerts({ sort_col: "触发时间", sort_dir: dir })
      .then((res: any) => {
        if (res.status === "ok") {
          setAlerts(res.items || []);
          if (res.sort_dir) setAlertSortDir(res.sort_dir as "asc" | "desc");
        }
      })
      .catch((err) => console.error("Failed to load alerts:", err))
      .finally(() => setAlertsLoading(false));
  };

  useEffect(() => {
    fetchWatchlist();
    fetchAlerts();
  }, []);

  const handleToggleAlertSort = () => {
    const newDir = alertSortDir === "desc" ? "asc" : "desc";
    setAlertSortDir(newDir);
    fetchAlerts(newDir);
  };

  const handleMarkAllRead = async () => {
    try {
      await api.monitoringAlertsRead();
      fetchAlerts();
    } catch (err: any) {
      alert(err.message || "标为已读失败");
    }
  };

  const handleClearAlerts = async () => {
    if (!confirm("确定要清空全部提醒历史记录吗？")) return;
    try {
      await api.monitoringAlertsClear();
      fetchAlerts();
    } catch (err: any) {
      alert(err.message || "清空历史记录失败");
    }
  };

  const handleAddSymbol = async () => {
    if (!canWrite || adding || !newSymbol.trim()) return;
    setAdding(true);
    try {
      await api.addWatchlist(newSymbol.trim().toUpperCase());
      setNewSymbol("");
      fetchWatchlist();
    } catch (err: any) {
      alert(err.message || "添加标的失败");
    } finally {
      setAdding(false);
    }
  };

  const handleDeleteSymbol = async (symbol: string) => {
    if (!confirm(`确定要从监控列表中移除 ${symbol} 吗？`)) return;
    try {
      await api.deleteWatchlist(symbol);
      fetchWatchlist();
    } catch (err: any) {
      alert(err.message || "删除标的失败");
    }
  };

  const handleToggleStar = async (row: WatchlistItem) => {
    if (!canWrite) return;
    const nextStarred = !row.starred;
    setItems((prev) =>
      prev.map((it) => (it.full_symbol === row.full_symbol ? { ...it, starred: nextStarred } : it))
    );
    try {
      await api.toggleWatchlistStar(row.full_symbol, nextStarred);
    } catch (err: any) {
      setItems((prev) =>
        prev.map((it) => (it.full_symbol === row.full_symbol ? { ...it, starred: row.starred } : it))
      );
      alert(err.message || "更新星标失败");
    }
  };

  const openKline = (symbol: string) => {
    setKlineSymbol(symbol);
    setKlineLoading(true);
    api
      .monitoringKline(symbol)
      .then((res) => {
        if (res.status === "ok") {
          setKlineData(res);
        }
      })
      .catch((err) => console.error("Failed to load kline:", err))
      .finally(() => setKlineLoading(false));
  };

  const openRules = () => {
    setRulesOpen(true);
    api.monitoringRules().then((res) => {
      if (res.status === "ok") {
        setRules(res.rules || res.items || []);
      }
    });
  };

  // Build ECharts option for K-line
  const buildKlineOption = () => {
    if (!klineData || !klineData.dates) return {};
    return {
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
      },
      legend: {
        data: [klineSymbol, "MA5", "MA10", "MA20", "MA50"],
        top: 0,
      },
      grid: [
        { left: "10%", right: "8%", height: "55%" },
        { left: "10%", right: "8%", top: "72%", height: "18%" },
      ],
      xAxis: [
        {
          type: "category",
          data: klineData.dates,
          scale: true,
          boundaryGap: false,
          axisLine: { onZero: false },
        },
        {
          type: "category",
          gridIndex: 1,
          data: klineData.dates,
          axisLabel: { show: false },
        },
      ],
      yAxis: [
        { scale: true, splitArea: { show: true } },
        { scale: true, gridIndex: 1, splitNumber: 2 },
      ],
      dataZoom: [{ type: "inside", start: 50, end: 100 }],
      series: [
        {
          name: klineSymbol,
          type: "candlestick",
          data: klineData.candles, // [open, close, low, high]
          itemStyle: {
            color: "#16A34A",
            color0: "#DC2626",
            borderColor: "#16A34A",
            borderColor0: "#DC2626",
          },
        },
        {
          name: "MA5",
          type: "line",
          data: klineData.ma5,
          smooth: true,
          lineStyle: { opacity: 0.8, width: 1.5 },
        },
        {
          name: "MA10",
          type: "line",
          data: klineData.ma10,
          smooth: true,
          lineStyle: { opacity: 0.8, width: 1.5 },
        },
        {
          name: "MA20",
          type: "line",
          data: klineData.ma20,
          smooth: true,
          lineStyle: { opacity: 0.8, width: 1.5 },
        },
        {
          name: "成交量",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: klineData.volumes,
        },
      ],
    };
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "10px", overflow: "hidden" }}>
      {/* Top Toolbar */}
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
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <Activity size={16} color="var(--brand)" />
            <span style={{ fontSize: "14px", fontWeight: 700 }}>行情监控中心</span>
          </div>

          <div style={{ display: "flex", gap: "4px", background: "var(--border-color)", padding: "2px", borderRadius: "6px" }}>
            <button
              onClick={() => setTab("watchlist")}
              className={`btn ${tab === "watchlist" ? "btn-primary" : "btn-quiet"}`}
              style={{ padding: "4px 10px", fontSize: "12px" }}
            >
              自选监控 ({items.length})
            </button>
            <button
              onClick={() => {
                setTab("alerts");
                fetchAlerts();
              }}
              className={`btn ${tab === "alerts" ? "btn-primary" : "btn-quiet"}`}
              style={{ padding: "4px 10px", fontSize: "12px" }}
            >
              提醒历史 ({alerts.length})
            </button>
          </div>
        </div>

        {tab === "watchlist" ? (
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            {canWrite && <><input
              type="text"
              placeholder="如 NVDA, AAPL, TSLA"
              aria-label="自选股票代码，可用逗号或空格分隔"
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && handleAddSymbol()}
              className="input-control"
              style={{ width: "200px" }}
            />
            <button
              onClick={handleAddSymbol}
              disabled={adding || !newSymbol.trim()}
              className="btn btn-primary"
              style={{ height: "32px" }}
            >
              <Plus size={14} />
              <span>添加自选股票</span>
            </button></>}

            <button onClick={openRules} className="btn" style={{ height: "32px" }}>
              <Bell size={14} />
              <span>预警规则</span>
            </button>

            <select
              aria-label="观察列表筛选类型"
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value as "STARRED" | "ALL")}
              className="input-control"
              style={{ width: "112px", height: "32px" }}
            >
              <option value="STARRED">星标股票</option>
              <option value="ALL">全部类型</option>
            </select>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <button onClick={handleMarkAllRead} className="btn" style={{ height: "32px" }}>
              全部标为已读
            </button>
            <button onClick={handleClearAlerts} className="btn" style={{ height: "32px", color: "var(--loss-color)" }}>
              清空提醒记录
            </button>
            <button onClick={() => fetchAlerts()} className="btn btn-quiet" style={{ height: "32px" }}>
              刷新
            </button>
          </div>
        )}
      </div>

      {tab === "watchlist" ? (
        <>
        {watchlistError && <p role="status">{watchlistError}</p>}
        {/* Main Watchlist Table */}
        <div className="table-container">
          <table className="finance-table">
            <thead>
              <tr>
                {renderSortHeader("标的", "symbol", "left", "90px")}
                {renderSortHeader("最新价", "price", "right", "90px")}
                {renderSortHeader("涨跌幅", "change", "right", "85px")}
                {renderSortHeader("短期", "trend_short", "left", "70px")}
                {renderSortHeader("中期", "trend_medium", "left", "70px")}
                {renderSortHeader("长期", "trend_long", "left", "70px")}
                {renderSortHeader("形态交叉", "cross", "left", "75px")}
                {renderSortHeader("RSI14", "rsi", "right", "75px")}
                {renderSortHeader("MA5", "ma5", "right", "90px")}
                {renderSortHeader("MA10", "ma10", "right", "90px")}
                {renderSortHeader("历史最高 / 回撤", "drawdown_pct", "right", "105px")}
                {renderSortHeader("高点日期 / 距今", "ath_date", "center", "115px")}
                {renderSortHeader("MA20 / 偏离", "dev20", "right", "95px")}
                {renderSortHeader("MA50 / 偏离", "dev50", "right", "95px")}
                {renderSortHeader("MA200 / 偏离", "dev200", "right", "95px")}
                {renderSortHeader("MA250 / 偏离", "dev250", "right", "95px")}
                <th style={{ minWidth: "110px", textAlign: "center" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {sortedItems.length === 0 ? (
                <tr>
                  <td colSpan={17} style={{ textAlign: "center", padding: "40px", color: "var(--text-muted)" }}>
                    {loading
                      ? "正在读取观察标的…"
                      : watchlistError
                      ? "暂无可展示的观察标的"
                      : kindFilter === "STARRED"
                      ? "暂无星标股票，请先在右上方选择「全部类型」并为重点关注股票点击 ☆ 加星标"
                      : "暂无观察标的"}
                  </td>
                </tr>
              ) : (
                sortedItems.map((row) => {
                  const isUp = row.change?.startsWith("+");
                  const isDown = row.change?.startsWith("-");

                  return (
                    <tr key={row.full_symbol}>
                      {/* 标的 + 星标 */}
                      <td style={{ fontWeight: 700, color: "var(--text-heading)" }}>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                          <span>{row.symbol}</span>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleToggleStar(row);
                            }}
                            disabled={!canWrite}
                            title={row.starred ? "已设为星标重点关注（点击取消）" : "点击加星标为重点关注股票"}
                            style={{
                              border: "none",
                              background: "transparent",
                              padding: "0 2px",
                              cursor: canWrite ? "pointer" : "default",
                              fontSize: "14px",
                              lineHeight: 1,
                              color: row.starred ? "#F59E0B" : "#94A3B8",
                            }}
                          >
                            {row.starred ? "★" : "☆"}
                          </button>
                          <span style={{ fontSize: "11px", color: "var(--text-subtle)", fontWeight: 400 }}>
                            {row.full_symbol}
                          </span>
                        </span>
                      </td>
                      {/* 最新价 */}
                      <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                        ${row.price || "—"}
                      </td>
                      {/* 涨跌幅 */}
                      <td
                        className="font-mono"
                        style={{
                          textAlign: "right",
                          color: isUp ? "var(--profit-color)" : isDown ? "var(--loss-color)" : "inherit",
                          fontWeight: 600,
                        }}
                      >
                        {row.change || "—"}
                      </td>
                      {/* 短期 */}
                      <td>
                        <span style={{ fontSize: "11px", padding: "2px 6px", borderRadius: "4px", background: "var(--hover-bg)" }}>
                          {row.trend_short || "—"}
                        </span>
                      </td>
                      {/* 中期 */}
                      <td>
                        <span style={{ fontSize: "11px", padding: "2px 6px", borderRadius: "4px", background: "var(--hover-bg)" }}>
                          {row.trend_medium || "—"}
                        </span>
                      </td>
                      {/* 长期 */}
                      <td>
                        <span style={{ fontSize: "11px", padding: "2px 6px", borderRadius: "4px", background: "var(--hover-bg)" }}>
                          {row.trend_long || "—"}
                        </span>
                      </td>
                      {/* 交叉 */}
                      <td style={{ fontSize: "12px" }}>{row.cross || "—"}</td>
                      {/* RSI14 */}
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {row.rsi ? Number(row.rsi).toFixed(1) : "—"}
                      </td>
                      {/* MA5 (100% UN-TRUNCATED) */}
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {row.ma5 ? Number(row.ma5).toFixed(3) : "—"}
                      </td>
                      {/* MA10 */}
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {row.ma10 ? Number(row.ma10).toFixed(3) : "—"}
                      </td>
                      {/* 历史最高 / 回撤 */}
                      <td
                        className="font-mono"
                        style={{ textAlign: "right", lineHeight: "1.25" }}
                        title={row.ath_high != null ? `历史最高: $${Number(row.ath_high).toFixed(2)}` : undefined}
                      >
                        <div style={{ fontWeight: 600 }}>
                          {row.ath_high != null ? `$${Number(row.ath_high).toFixed(2)}` : "—"}
                        </div>
                        <div
                          style={{
                            fontSize: "11px",
                            fontWeight: 600,
                            color: row.drawdown_pct != null ? (Number(row.drawdown_pct) >= 0 ? "var(--profit-color)" : "var(--loss-color)") : "var(--text-muted)",
                          }}
                        >
                          {row.drawdown_pct != null ? `${Number(row.drawdown_pct).toFixed(2)}%` : "—"}
                        </div>
                      </td>
                      {/* 高点日期 / 距今 */}
                      <td className="font-mono" style={{ textAlign: "center", fontSize: "12px", lineHeight: "1.25" }}>
                        {row.ath_date ? (
                          <div>
                            <div>{row.ath_date}</div>
                            <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                              {row.ath_days === 0 ? "今日新高" : row.ath_days != null ? `已过 ${row.ath_days} 天` : "—"}
                            </div>
                          </div>
                        ) : "—"}
                      </td>
                      {/* MA20 / 50 / 200 / 250 双行（均价 + 偏离度） */}
                      {([
                        [row.ma20, row.dev20],
                        [row.ma50, row.dev50],
                        [row.ma200, row.dev200],
                        [row.ma250, row.dev250],
                      ] as const).map(([maVal, devVal], idx) => (
                        <td key={idx} className="font-mono" style={{ textAlign: "right", lineHeight: "1.25" }}>
                          <div>{maVal ? Number(maVal).toFixed(2) : "—"}</div>
                          <div
                            style={{
                              fontSize: "11px",
                              fontWeight: 600,
                              color: devVal != null ? (Number(devVal) >= 0 ? "var(--profit-color)" : "var(--loss-color)") : "var(--text-muted)",
                            }}
                          >
                            {devVal != null ? `${Number(devVal) >= 0 ? "+" : ""}${Number(devVal).toFixed(2)}%` : "—"}
                          </div>
                        </td>
                      ))}
                      {/* 操作 */}
                      <td style={{ textAlign: "center" }}>
                        <div style={{ display: "inline-flex", gap: "4px" }}>
                          <button
                            onClick={() => openKline(row.full_symbol)}
                            className="btn btn-quiet"
                            style={{ padding: "3px 6px", fontSize: "11px" }}
                            title="查看专业 K 线图"
                          >
                            <LineChart size={13} />
                            <span>K线</span>
                          </button>
                          {canWrite && row.manual && <button
                            onClick={() => handleDeleteSymbol(row.full_symbol)}
                            className="btn btn-quiet"
                            style={{ padding: "3px 6px", color: "var(--loss-color)" }}
                            title="移除手动自选"
                          >
                            <Trash2 size={13} />
                            <span>移除</span>
                          </button>}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        </>
      ) : (
        /* Alert History Table */
        <div className="table-container">
          <table className="finance-table">
            <thead>
              <tr>
                <th
                  onClick={handleToggleAlertSort}
                  style={{ cursor: "pointer", userSelect: "none", minWidth: "140px" }}
                  title="点击切换触发时间排序"
                >
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                    <span>触发时间</span>
                    <span style={{ fontSize: "12px", color: "var(--brand, #2563eb)", fontWeight: 700 }}>
                      {alertSortDir === "desc" ? "↓" : "↑"}
                    </span>
                  </span>
                </th>
                <th style={{ minWidth: "90px" }}>标的代码</th>
                <th style={{ minWidth: "120px" }}>规则名称</th>
                <th style={{ minWidth: "180px" }}>触发详情</th>
                <th style={{ minWidth: "110px", textAlign: "right" }}>现价 / 阈值</th>
                <th style={{ minWidth: "80px" }}>交易时段</th>
                <th style={{ minWidth: "80px" }}>通知渠道</th>
                <th style={{ minWidth: "70px" }}>状态</th>
              </tr>
            </thead>
            <tbody>
              {alerts.length === 0 ? (
                <tr>
                  <td colSpan={8} style={{ textAlign: "center", padding: "40px", color: "var(--text-muted)" }}>
                    {alertsLoading ? "正在加载提醒历史..." : "暂无触发的预警提醒历史"}
                  </td>
                </tr>
              ) : (
                alerts.map((evt, idx) => {
                  const isUnread = evt.status === "unread";
                  return (
                    <tr key={evt.event_id || idx}>
                      <td style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                        {evt.detected_at || evt.created_at || "—"}
                      </td>
                      <td style={{ fontWeight: 700, color: "var(--text-heading)" }}>{evt.symbol}</td>
                      <td style={{ fontWeight: 600 }}>{evt.rule_name || "—"}</td>
                      <td style={{ fontSize: "12px" }}>{evt.message || "—"}</td>
                      <td className="font-mono" style={{ textAlign: "right" }}>
                        {evt.trigger_price != null ? `$${evt.trigger_price}` : "—"}
                      </td>
                      <td>
                        <span style={{ fontSize: "11px", padding: "2px 6px", borderRadius: "4px", background: "var(--hover-bg)" }}>
                          盘中
                        </span>
                      </td>
                      <td>{evt.channel === "bark" ? "Bark 推送" : "站内通知"}</td>
                      <td>
                        <span
                          style={{
                            fontSize: "11px",
                            fontWeight: 600,
                            padding: "2px 6px",
                            borderRadius: "4px",
                            background: isUnread ? "rgba(37, 99, 235, 0.1)" : "var(--table-alternate)",
                            color: isUnread ? "var(--brand)" : "var(--text-muted)",
                          }}
                        >
                          {isUnread ? "未读" : "已读"}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* K-line Modal Dialog */}
      {klineSymbol && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0, 0, 0, 0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 100,
          }}
        >
          <div
            className="card-panel"
            style={{
              width: "850px",
              height: "560px",
              display: "flex",
              flexDirection: "column",
              boxShadow: "0 10px 30px rgba(0, 0, 0, 0.15)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
              <div style={{ fontSize: "16px", fontWeight: 700 }}>
                {klineSymbol} 专业日 K 线与均线系统
              </div>
              <button onClick={() => setKlineSymbol(null)} className="btn btn-quiet">
                <X size={18} />
              </button>
            </div>

            <div style={{ flex: 1, minHeight: 0 }}>
              {klineLoading ? (
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%" }}>
                  正在加载 {klineSymbol} 历史 K 线...
                </div>
              ) : !klineData || !klineData.dates || klineData.dates.length === 0 ? (
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-muted)" }}>
                  暂无 {klineSymbol} 历史行情数据（非交易时段或未收录）
                </div>
              ) : (
                <ReactECharts option={buildKlineOption()} style={{ height: "100%", width: "100%" }} />
              )}
            </div>
          </div>
        </div>
      )}

      {/* Rules Slide-in Drawer */}
      {rulesOpen && (
        <div
          style={{
            position: "fixed",
            top: 0,
            right: 0,
            width: "380px",
            height: "100vh",
            background: "var(--bg-panel)",
            borderLeft: "1px solid var(--border-color)",
            boxShadow: "-4px 0 20px rgba(0,0,0,0.1)",
            zIndex: 90,
            padding: "20px",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontSize: "16px", fontWeight: 700 }}>预警规则中心</div>
            <button onClick={() => setRulesOpen(false)} className="btn btn-quiet">
              <X size={18} />
            </button>
          </div>

          <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "8px" }}>
            {rules.map((r) => (
              <div
                key={r.id}
                style={{
                  background: "var(--table-alternate)",
                  padding: "12px",
                  borderRadius: "8px",
                  border: "1px solid var(--border-color)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600 }}>
                  <span>{r.name || r.id}</span>
                  <span style={{ color: r.enabled ? "var(--profit-color)" : "var(--text-muted)" }}>
                    {r.enabled ? "已启用" : "已暂停"}
                  </span>
                </div>
                <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "4px" }}>
                  {r.description || `当条件触发时推送 Bark 通知`}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
