import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Calendar,
  Check,
  Copy,
  FileText,
  RefreshCw,
  Send,
  Sparkles,
  Trash2,
  User,
} from "lucide-react";
import { api } from "../api/client";
import { MarkdownBlock } from "./AiViews";

interface ReportSummary {
  id: string;
  trade_date: string;
  person_key: string;
  person_id?: number | null;
  person_name: string;
  generated_at: string;
  trigger: string;
  provider: string;
  model: string;
  sync_status?: { ran?: boolean; ok?: boolean; message?: string; finished_at?: string };
  day_pnl?: number | null;
  day_pnl_pct?: number | null;
  leverage_pct?: number | null;
  total_market_value?: number | null;
  trade_count: number;
  holding_count: number;
  regime?: { label?: string; tone?: string };
  tldr?: string;
}

interface PersonOption {
  key: string;
  label: string;
  id?: number | null;
}

interface AgyModelOption {
  id: string;
  label: string;
}

const DEFAULT_AFTER_HOURS_MODEL = "gemini-4-neon-high";

const DEFAULT_MODELS: AgyModelOption[] = [
  { id: "gemini-4-neon-high", label: "Gemini 4 Neon (High)" },
  { id: "gemini-4-neon-medium", label: "Gemini 4 Neon (Medium)" },
  { id: "gemini-4-hydrogen-high", label: "Gemini 4 Hydrogen (High)" },
  { id: "gemini-4-hydrogen-medium", label: "Gemini 4 Hydrogen (Medium)" },
  { id: "gemini-3.8-flash-medium", label: "Gemini 3.8 Flash (Medium)" },
];

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function getWeekday(dateStr?: string): string {
  if (!dateStr) return "";
  const d = new Date(`${dateStr}T12:00:00`);
  return Number.isNaN(d.getTime()) ? "" : WEEKDAYS[d.getDay()];
}

function fmtMoney(v: number | null | undefined, signed = false): string {
  if (v == null || Number.isNaN(v)) return "—";
  const prefix = signed ? (v > 0 ? "+$" : v < 0 ? "-$" : "$") : v < 0 ? "-$" : "$";
  return `${prefix}${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function fmtPct(v: number | null | undefined, alreadyPercent = false): string {
  if (v == null || Number.isNaN(v)) return "—";
  const pct = alreadyPercent ? v : v * 100;
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

function toneColor(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v === 0) return "var(--text-muted)";
  return v > 0 ? "var(--profit-color)" : "var(--loss-color)";
}

function regimeStyle(tone?: string): { bg: string; fg: string } {
  if (tone === "success") return { bg: "rgba(22, 163, 74, 0.14)", fg: "#16A34A" };
  if (tone === "error") return { bg: "rgba(220, 38, 38, 0.14)", fg: "#DC2626" };
  return { bg: "rgba(217, 119, 6, 0.15)", fg: "#D97706" };
}

function sentimentStyle(s?: string): { bg: string; fg: string } {
  if (s === "利好") return { bg: "rgba(22, 163, 74, 0.14)", fg: "#16A34A" };
  if (s === "利空") return { bg: "rgba(220, 38, 38, 0.14)", fg: "#DC2626" };
  return { bg: "var(--hover-bg)", fg: "var(--text-muted)" };
}

interface AfterHoursReportViewProps {
  personId?: number | null;
  onPersonChange?: (pid: number | null) => void;
}

/** 美股盘后复盘日报 2.0 页面 */
export const AfterHoursReportView: React.FC<AfterHoursReportViewProps> = ({
  personId,
  onPersonChange,
}) => {
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [personOptions, setPersonOptions] = useState<PersonOption[]>([
    { key: "个人", label: "个人" },
    { key: "ALL", label: "全部人员" },
  ]);
  const [isIsolated, setIsIsolated] = useState(false);
  const [canSync, setCanSync] = useState(false);
  const [marketSession, setMarketSession] = useState<any>(null);

  const [filterPerson, setFilterPerson] = useState<string>("");
  const [genPerson, setGenPerson] = useState<string>("个人");
  const [genDate, setGenDate] = useState<string>("");
  const [runSync, setRunSync] = useState<boolean>(true);
  const [models, setModels] = useState<AgyModelOption[]>(DEFAULT_MODELS);
  const [selectedModel, setSelectedModel] = useState<string>(DEFAULT_AFTER_HOURS_MODEL);

  const [selectedId, setSelectedId] = useState<string>("");
  const [detail, setDetail] = useState<any>(null);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [errorMsg, setErrorMsg] = useState("");
  const [copied, setCopied] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // 2.0 交互状态
  const [activeTab, setActiveTab] = useState<"overview" | "trades" | "news" | "markdown">("overview");
  const [moverFilter, setMoverFilter] = useState<"all" | "up" | "down" | "after" | "news">("all");
  const [moverSort, setMoverSort] = useState<"day_pnl" | "weight" | "change">("day_pnl");
  const [expandedSyms, setExpandedSyms] = useState<string[]>([]);
  const [checkedIds, setCheckedIds] = useState<string[]>([]);
  const [followupInput, setFollowupInput] = useState("");
  const [followupLoading, setFollowupLoading] = useState(false);

  const loadList = (personFilter = filterPerson, preferId?: string) => {
    setLoadingList(true);
    api
      .aiAfterHoursReports(personFilter || undefined)
      .then((res) => {
        if (res.status === "ok") {
          const opts: PersonOption[] = res.person_options || [];
          if (opts.length > 0) {
            setPersonOptions(opts);
            if (!opts.some((o) => o.key === genPerson)) {
              setGenPerson(opts[0].key);
            }
          }
          setIsIsolated(Boolean(res.is_isolated));
          setCanSync(Boolean(res.can_sync));
          if (res.market_session) {
            setMarketSession(res.market_session);
          }
          if (!genDate && res.default_trade_date) {
            setGenDate(res.default_trade_date);
          }
          const list: ReportSummary[] = res.reports || [];
          setReports(list);
          const targetId =
            preferId && list.some((r) => r.id === preferId)
              ? preferId
              : selectedId && list.some((r) => r.id === selectedId)
                ? selectedId
                : list[0]?.id || "";
          setSelectedId(targetId);
          if (!targetId) {
            setDetail(null);
          }
        }
      })
      .catch((err) => setErrorMsg(err.message || "加载日报列表失败"))
      .finally(() => setLoadingList(false));
  };

  useEffect(() => {
    if (isIsolated || personId === undefined || personOptions.length === 0) return;
    const matched =
      personId == null
        ? personOptions.find((o) => o.key === "ALL")
        : personOptions.find((o) => o.id === personId);
    if (matched && matched.key !== genPerson) {
      setGenPerson(matched.key);
      const hit = reports.find((r) => r.person_key === matched.key);
      if (hit?.id && hit.id !== selectedId) {
        setSelectedId(hit.id);
      }
    }
  }, [personId, personOptions, isIsolated]);

  useEffect(() => {
    loadList("");
    api
      .aiAgyModels(false)
      .then((res) => {
        if (res.status === "ok" && Array.isArray(res.models) && res.models.length > 0) {
          setModels(res.models);
          const hasDefault = res.models.some((m: AgyModelOption) => m.id === DEFAULT_AFTER_HOURS_MODEL);
          if (hasDefault) {
            setSelectedModel(DEFAULT_AFTER_HOURS_MODEL);
          } else if (res.current_model) {
            setSelectedModel(res.current_model);
          }
        }
      })
      .catch(() => {});
  }, []);

  const defaultExpandedForHoldings = (list: any[]): string[] => {
    const ranked = [...(list || [])]
      .filter((h) => h?.symbol)
      .sort((a, b) => Number(b.weight_pct || 0) - Number(a.weight_pct || 0));
    let core = ranked.filter((h) => Number(h.weight_pct || 0) >= 0.05);
    if (core.length < 3) core = ranked.slice(0, 3);
    else if (core.length > 5) core = core.slice(0, 5);
    return core.map((h) => String(h.symbol));
  };

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setExpandedSyms([]);
      return;
    }
    setLoadingDetail(true);
    api
      .aiAfterHoursDetail(selectedId)
      .then((res) => {
        if (res.status === "ok" && res.report) {
          setDetail(res.report);
          setCheckedIds(Array.isArray(res.report.checked_ids) ? res.report.checked_ids : []);
          const hs = res.report?.snapshot?.holdings || [];
          setExpandedSyms(defaultExpandedForHoldings(hs));
        }
      })
      .catch((err) => setErrorMsg(err.message || "加载日报详情失败"))
      .finally(() => setLoadingDetail(false));
  }, [selectedId]);

  const pollTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimerRef.current !== null) {
        window.clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!generating) {
      setElapsedSec(0);
      return;
    }
    const timer = window.setInterval(() => setElapsedSec((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [generating]);

  const applyGeneratedReport = (report: any) => {
    setDetail(report);
    setCheckedIds(Array.isArray(report.checked_ids) ? report.checked_ids : []);
    setSelectedId(report.id);
    loadList(filterPerson, report.id);
  };

  const pollTaskUntilDone = (taskId: string, consecutiveFailures = 0) => {
    pollTimerRef.current = window.setTimeout(async () => {
      try {
        const st = await api.aiAfterHoursTaskStatus(taskId);
        const task = st?.task;
        if (!task) {
          setErrorMsg("任务状态响应为空");
          setGenerating(false);
          return;
        }
        if (task.status === "done" && task.report) {
          applyGeneratedReport(task.report);
          setGenerating(false);
          return;
        }
        if (task.status === "error") {
          setErrorMsg(task.error || "生成日报失败");
          setGenerating(false);
          return;
        }
        pollTaskUntilDone(taskId, 0);
      } catch (err: any) {
        if (consecutiveFailures + 1 >= 5) {
          setErrorMsg(err?.message || "轮询生成结果超时，请点击右上角刷新查看");
          setGenerating(false);
          return;
        }
        pollTaskUntilDone(taskId, consecutiveFailures + 1);
      }
    }, 3000);
  };

  const handleGenerate = async () => {
    if (generating) return;
    if (pollTimerRef.current !== null) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    setErrorMsg("");
    setGenerating(true);
    try {
      const res = await api.aiAfterHoursGenerate({
        trade_date: genDate || undefined,
        person: genPerson || "个人",
        run_sync: canSync ? runSync : false,
        provider: "gemini",
        model: selectedModel,
        async: true,
      });
      if (res.status === "accepted" && res.task_id) {
        pollTaskUntilDone(res.task_id, 0);
        return;
      }
      if (res.status === "ok" && res.report) {
        applyGeneratedReport(res.report);
        setGenerating(false);
      } else {
        setErrorMsg(res.message || "生成日报失败");
        setGenerating(false);
      }
    } catch (err: any) {
      setErrorMsg(err.message || "生成日报请求异常");
      setGenerating(false);
    }
  };

  const handleCopy = () => {
    if (!detail?.markdown) return;
    navigator.clipboard?.writeText(detail.markdown).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  const handleDeleteReport = async () => {
    if (!detail?.id || deleting) return;
    const ok = window.confirm(
      `确认删除「${detail.trade_date} · ${detail.person_name}」的盘后复盘日报存档吗？此操作不可撤销。`
    );
    if (!ok) return;
    setDeleting(true);
    setErrorMsg("");
    try {
      await api.aiAfterHoursDelete(detail.id);
      setDetail(null);
      setSelectedId("");
      await loadList(filterPerson);
    } catch (err: any) {
      setErrorMsg(err.message || "删除日报失败");
    } finally {
      setDeleting(false);
    }
  };

  const handleToggleChecklist = (cid: string) => {
    if (!detail?.id) return;
    const next = checkedIds.includes(cid)
      ? checkedIds.filter((x) => x !== cid)
      : [...checkedIds, cid];
    setCheckedIds(next);
    api.aiAfterHoursSaveChecklist(detail.id, next).catch(() => {});
  };

  const handleSendFollowup = async (customQ?: string) => {
    const q = (customQ ?? followupInput).trim();
    if (!q || !detail?.id || followupLoading) return;
    if (customQ === undefined) setFollowupInput("");
    setFollowupLoading(true);
    setErrorMsg("");
    try {
      const res = await api.aiAfterHoursFollowup(detail.id, q, selectedModel, "gemini");
      if (res.status === "ok") {
        setDetail((prev: any) =>
          prev ? { ...prev, followups_history: res.followups_history || [] } : prev
        );
      } else {
        setErrorMsg(res.message || "追问请求未成功");
      }
    } catch (err: any) {
      setErrorMsg(err.message || "追问请求异常");
    } finally {
      setFollowupLoading(false);
    }
  };

  const snap = detail?.snapshot || {};
  const summary = snap?.summary || {};
  const benchmarks = snap?.market_benchmarks || {};
  const structured = detail?.structured || {};
  const holdings: any[] = snap?.holdings || [];
  const trades: any[] = snap?.trades || [];
  const newsMap: Record<string, any[]> = snap?.news || {};
  const moverReasons: Record<string, string> = structured?.mover_reasons || {};
  const newsSentiments: Record<string, string> = structured?.news_sentiments || {};
  const newsDigest: any[] = structured?.news_digest || [];
  const newsZhMap: Record<string, any> = structured?.news_zh_map || {};
  const risks: any[] = structured?.risks || [];
  const checklist: any[] = structured?.checklist || [];
  const followups: string[] = structured?.followups || [];
  const followupsHistory: any[] = detail?.followups_history || [];

  const getNewsForHolding = (h: any) => {
    const syms = new Set<string>([String(h.symbol || "").toUpperCase()]);
    if (h.underlying) syms.add(String(h.underlying).toUpperCase());

    if (newsDigest.length > 0) {
      const matched = newsDigest.filter((d) => syms.has(String(d.symbol || "").toUpperCase()));
      if (matched.length > 0) {
        return matched.map((d) => ({
          title: d.original_title || d.zh_title,
          zh_title: d.zh_title || d.original_title,
          summary: d.summary || "",
          sentiment: d.sentiment || newsSentiments[d.original_title] || "中性",
          publisher: d.publisher || "",
          published_bj: d.published_bj || "",
          url: d.url || d.link || "",
        }));
      }
    }

    const own = newsMap[h.symbol] || [];
    const under = h.underlying ? newsMap[h.underlying] || [] : [];
    const merged = [...own, ...under];
    const seen = new Set<string>();
    return merged
      .filter((n) => {
        const k = String(n.title || "");
        if (!k || seen.has(k)) return false;
        seen.add(k);
        return true;
      })
      .map((n) => {
        const zh = newsZhMap[n.title];
        return {
          ...n,
          zh_title: zh?.zh_title || n.title,
          summary: zh?.summary || "",
          sentiment: zh?.sentiment || newsSentiments[n.title] || "中性",
          url: n.link || n.url || "",
        };
      });
  };

  const filteredHoldings = useMemo(() => {
    const base = holdings.filter((h) => {
      if (moverFilter === "up") return (h.regular_change_pct || 0) > 0;
      if (moverFilter === "down") return (h.regular_change_pct || 0) < 0;
      if (moverFilter === "after") {
        return h.after_hours_change_pct != null && Math.abs(h.after_hours_change_pct) >= 0.005;
      }
      if (moverFilter === "news") return getNewsForHolding(h).length > 0;
      return true;
    });
    return [...base].sort((a, b) => {
      if (moverSort === "weight") return Number(b.weight_pct || 0) - Number(a.weight_pct || 0);
      if (moverSort === "change") {
        return Math.abs(Number(b.regular_change_pct || 0)) - Math.abs(Number(a.regular_change_pct || 0));
      }
      return Math.abs(Number(b.day_pnl || 0)) - Math.abs(Number(a.day_pnl || 0));
    });
  }, [holdings, moverFilter, moverSort, newsMap, newsDigest]);

  const allNewsFeed = useMemo(() => {
    if (newsDigest.length > 0) {
      return newsDigest.map((d) => ({
        symbol: d.symbol || "组合",
        title: d.original_title || d.zh_title,
        zh_title: d.zh_title || d.original_title,
        summary: d.summary || "",
        sentiment: d.sentiment || newsSentiments[d.original_title] || "中性",
        publisher: d.publisher || "",
        published_bj: d.published_bj || "",
        url: d.url || d.link || "",
      }));
    }
    const out: any[] = [];
    Object.entries(newsMap).forEach(([sym, items]) => {
      (items || []).forEach((it: any) => {
        const zh = newsZhMap[it.title];
        out.push({
          ...it,
          symbol: sym,
          zh_title: zh?.zh_title || it.title,
          summary: zh?.summary || "",
          sentiment: zh?.sentiment || newsSentiments[it.title] || "中性",
          url: it.link || it.url || "",
        });
      });
    });
    return out;
  }, [newsMap, newsSentiments, newsDigest, newsZhMap]);

  const levPctNum = summary.leverage_pct != null ? Number(summary.leverage_pct) * 100 : 0;
  const regStyle = regimeStyle(structured?.regime?.tone);
  const doneChecklistCount = checklist.filter((c) => checkedIds.includes(c.id)).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "12px", minHeight: 0 }}>
      {/* 顶部全局操作栏 */}
      <div
        className="card-panel"
        style={{
          padding: "10px 16px",
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "10px",
        }}
      >
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "10px" }}>
          <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <Calendar size={14} />
            <span>交易日</span>
            <input
              type="date"
              className="input-control"
              value={genDate}
              onChange={(e) => setGenDate(e.target.value)}
              disabled={generating}
              style={{ fontSize: "12px", padding: "4px 8px" }}
            />
          </label>

          <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <User size={14} />
            <span>范围</span>
            <select
              className="input-control"
              value={genPerson}
              onChange={(e) => {
                const nextKey = e.target.value;
                setGenPerson(nextKey);
                if (!isIsolated && onPersonChange) {
                  const opt = personOptions.find((o) => o.key === nextKey);
                  onPersonChange(nextKey === "ALL" ? null : opt?.id ?? null);
                }
              }}
              disabled={generating || isIsolated}
              style={{ fontSize: "12px", padding: "4px 8px", minWidth: "108px" }}
            >
              {personOptions.map((opt) => (
                <option key={opt.key} value={opt.key}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>

          <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>模型</span>
            <select
              className="input-control"
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              disabled={generating}
              style={{ fontSize: "12px", padding: "4px 8px", minWidth: "185px" }}
            >
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>

          {canSync && (
            <label style={{ display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "12px", color: "var(--text-main)", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={runSync}
                onChange={(e) => setRunSync(e.target.checked)}
                disabled={generating}
              />
              <span>生成前同步流水</span>
            </label>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <button
            className="btn"
            onClick={() => loadList(filterPerson)}
            disabled={loadingList || generating}
            style={{ fontSize: "12px", padding: "5px 10px", display: "inline-flex", alignItems: "center", gap: "4px" }}
          >
            <RefreshCw size={13} />
            刷新
          </button>
          <button
            className="btn btn-primary"
            onClick={handleGenerate}
            disabled={generating}
            style={{ fontSize: "12.5px", padding: "6px 14px", display: "inline-flex", alignItems: "center", gap: "6px", fontWeight: 600 }}
          >
            <Sparkles size={14} />
            {generating ? `正在同步并生成 (${elapsedSec}s)...` : "生成 / 重算日报"}
          </button>
        </div>
      </div>

      {marketSession && (!marketSession.is_trading_day || marketSession.is_early_close) && (
        <div
          style={{
            padding: "7px 12px",
            borderRadius: "8px",
            background: !marketSession.is_trading_day ? "rgba(217,119,6,0.12)" : "rgba(37,99,235,0.1)",
            color: !marketSession.is_trading_day ? "#B45309" : "#1D4ED8",
            fontSize: "12px",
            fontWeight: 500,
          }}
        >
          {!marketSession.is_trading_day ? "🗓️ " : "⏱️ "}
          {marketSession.hint || (marketSession.is_early_close ? "今日美股半日市（13:00 ET 收盘 / 17:00 ET 盘后结束）" : "今日美股休市")}
        </div>
      )}

      {errorMsg && (
        <div style={{ padding: "8px 12px", borderRadius: "8px", background: "#FEE2E2", color: "#991B1B", fontSize: "12.5px" }}>
          ⚠️ {errorMsg}
        </div>
      )}

      {/* 主体左右布局 */}
      <div style={{ flex: 1, display: "flex", gap: "12px", minHeight: 0 }}>
        {/* 左侧历史复盘时间轴 */}
        <div
          className="card-panel"
          style={{
            width: "268px",
            minWidth: "235px",
            padding: "12px",
            display: "flex",
            flexDirection: "column",
            gap: "10px",
            minHeight: 0,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span style={{ fontSize: "12.5px", fontWeight: 700, color: "var(--text-heading)" }}>
              历史复盘存档
            </span>
            <span className="font-mono" style={{ fontSize: "11.5px", color: "var(--text-muted)" }}>
              {reports.length} 份
            </span>
          </div>

          {!isIsolated && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
              {[{ key: "", label: "全部" }, ...personOptions.slice(0, 4)].map((opt) => {
                const active = filterPerson === opt.key;
                return (
                  <button
                    key={opt.key || "ALL_FILTER"}
                    type="button"
                    onClick={() => {
                      setFilterPerson(opt.key);
                      loadList(opt.key);
                    }}
                    style={{
                      fontSize: "11.5px",
                      padding: "3px 9px",
                      borderRadius: "999px",
                      border: active ? "1px solid var(--brand)" : "1px solid var(--border-color)",
                      background: active ? "var(--brand-selected)" : "var(--bg-app)",
                      color: active ? "var(--brand)" : "var(--text-muted)",
                      fontWeight: active ? 600 : 500,
                      cursor: "pointer",
                    }}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          )}

          <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "8px" }}>
            {loadingList ? (
              <div style={{ fontSize: "12px", color: "var(--text-muted)", textAlign: "center", padding: "18px" }}>
                加载中...
              </div>
            ) : reports.length === 0 ? (
              <div style={{ fontSize: "12px", color: "var(--text-muted)", textAlign: "center", padding: "20px 8px", lineHeight: 1.6 }}>
                暂无历史盘后日报。点击右上角「生成 / 重算日报」即可生成。
              </div>
            ) : (
              reports.map((rep) => {
                const active = rep.id === selectedId;
                const pnl = rep.day_pnl;
                const pct = rep.day_pnl_pct;
                const barWidth = pct != null ? Math.min(100, Math.max(12, Math.abs(pct * 100) * 14)) : 0;
                const barCol = pnl == null ? "var(--border-color)" : pnl >= 0 ? "#16A34A" : "#DC2626";
                return (
                  <button
                    key={rep.id}
                    type="button"
                    onClick={() => setSelectedId(rep.id)}
                    style={{
                      textAlign: "left",
                      padding: "10px",
                      borderRadius: "10px",
                      border: active ? "1px solid var(--brand)" : "1px solid var(--border-color)",
                      background: active ? "var(--brand-selected)" : "var(--bg-app)",
                      cursor: "pointer",
                      display: "flex",
                      flexDirection: "column",
                      gap: "5px",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "6px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "5px" }}>
                        <span className="font-mono" style={{ fontWeight: 700, fontSize: "12.5px", color: "var(--text-heading)" }}>
                          {rep.trade_date}
                        </span>
                        <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>{getWeekday(rep.trade_date)}</span>
                      </div>
                      <span
                        style={{
                          fontSize: "10.5px",
                          padding: "1px 6px",
                          borderRadius: "6px",
                          background: "var(--bg-panel)",
                          border: "1px solid var(--border-color)",
                          color: "var(--text-main)",
                          fontWeight: 600,
                        }}
                      >
                        {rep.person_name}
                      </span>
                    </div>

                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                      <span className="font-mono" style={{ fontSize: "14px", fontWeight: 700, color: toneColor(pnl) }}>
                        {fmtMoney(pnl, true)}
                      </span>
                      <span className="font-mono" style={{ fontSize: "11.5px", fontWeight: 600, color: toneColor(pct) }}>
                        {fmtPct(pct)}
                      </span>
                    </div>

                    {pct != null && (
                      <div style={{ height: "4px", width: "100%", borderRadius: "999px", background: "var(--border-color)", overflow: "hidden" }}>
                        <div style={{ height: "100%", width: `${barWidth}%`, background: barCol, borderRadius: "999px" }} />
                      </div>
                    )}

                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", color: "var(--text-muted)" }}>
                      <span>
                        {rep.trade_count} 笔成交
                        {rep.leverage_pct != null ? ` · 杠杆 ${(rep.leverage_pct * 100).toFixed(0)}%` : ""}
                      </span>
                      <span>{rep.trigger === "auto" ? "自动" : "手动"}</span>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* 右侧多维复盘工作台 */}
        <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "12px", minHeight: 0, paddingRight: "2px" }}>
          {loadingDetail ? (
            <div className="card-panel" style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: "13px" }}>
              正在加载复盘驾驶舱...
            </div>
          ) : !detail ? (
            <div className="card-panel" style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: "13px", lineHeight: 1.8, padding: "24px" }}>
              <FileText size={32} style={{ opacity: 0.4, marginBottom: "8px" }} />
              <div>选择左侧历史交易日查看结构化盘后复盘，或点击右上角「生成 / 重算日报」立即生成。</div>
            </div>
          ) : (
            <>
              {/* 1. 顶部驾驶舱 Hero 卡片 */}
              <div className="card-panel" style={{ padding: "16px", display: "flex", flexDirection: "column", gap: "14px" }}>
                <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-start", gap: "10px" }}>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                      <span style={{ fontSize: "17px", fontWeight: 700, color: "var(--text-heading)" }}>
                        {detail.trade_date} ({getWeekday(detail.trade_date)}) 美股盘后复盘 · {detail.person_name}
                      </span>
                      {structured?.regime?.label && (
                        <span style={{ fontSize: "11.5px", fontWeight: 600, padding: "2px 9px", borderRadius: "999px", background: regStyle.bg, color: regStyle.fg }}>
                          {structured.regime.label}
                        </span>
                      )}
                      {snap?.market_session?.is_early_close && (
                        <span
                          style={{
                            fontSize: "11.5px",
                            fontWeight: 600,
                            padding: "2px 9px",
                            borderRadius: "999px",
                            background: "rgba(37,99,235,0.12)",
                            color: "#1D4ED8",
                          }}
                        >
                          半日市 · 13:00 ET 收盘
                        </span>
                      )}
                      <span
                        style={{
                          fontSize: "11.5px",
                          fontWeight: 600,
                          padding: "2px 9px",
                          borderRadius: "999px",
                          background: detail.sync_status?.ok === false ? "rgba(220,38,38,0.12)" : "rgba(22,163,74,0.12)",
                          color: detail.sync_status?.ok === false ? "#DC2626" : "#16A34A",
                        }}
                      >
                        {detail.sync_status?.ran ? (detail.sync_status?.ok ? "✓ 已自动同步" : "⚠ 同步异常") : "未执行同步"}
                      </span>
                    </div>
                    <div style={{ fontSize: "11.5px", color: "var(--text-muted)", marginTop: "4px" }}>
                      生成时间：{(detail.generated_at || "").replace("T", " ")} · 引擎：{detail.provider}
                      {detail.model ? ` (${detail.model})` : ""} · 盈亏口径：
                      {summary.day_pnl_basis === "ledger_plus_after_hours"
                        ? "账本精确 + 盘后"
                        : summary.day_pnl_basis === "quote_after_hours"
                          ? "收盘 + 盘后估算"
                          : summary.day_pnl_basis === "quote_regular" || summary.day_pnl_basis === "quote_estimated"
                            ? "收盘行情估算"
                            : "账本流水"}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    <button
                      className="btn"
                      onClick={handleCopy}
                      style={{ fontSize: "12px", padding: "5px 10px", display: "inline-flex", alignItems: "center", gap: "5px" }}
                    >
                      {copied ? <Check size={13} color="#16A34A" /> : <Copy size={13} />}
                      {copied ? "已复制" : "复制 Markdown"}
                    </button>
                    <button
                      className="btn"
                      onClick={handleDeleteReport}
                      disabled={deleting || generating}
                      style={{
                        fontSize: "12px",
                        padding: "5px 10px",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "5px",
                        color: "#DC2626",
                        borderColor: "rgba(220,38,38,0.35)",
                      }}
                    >
                      <Trash2 size={13} />
                      {deleting ? "删除中..." : "删除存档"}
                    </button>
                  </div>
                </div>

                {/* AI 一句话核心定性横幅 (TL;DR) */}
                {structured?.tldr && (
                  <div
                    style={{
                      borderRadius: "10px",
                      border: "1px solid rgba(37, 99, 235, 0.28)",
                      background: "var(--brand-selected)",
                      padding: "11px 14px",
                      display: "flex",
                      alignItems: "flex-start",
                      gap: "10px",
                    }}
                  >
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 700,
                        padding: "2px 7px",
                        borderRadius: "6px",
                        background: "var(--brand)",
                        color: "#FFFFFF",
                        flexShrink: 0,
                        marginTop: "2px",
                      }}
                    >
                      核心定性
                    </span>
                    <div style={{ fontSize: "13px", lineHeight: 1.65, color: "var(--text-heading)" }}>
                      {structured.tldr}
                    </div>
                  </div>
                )}

                {/* 4 张 KPI 指标卡 */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(185px, 1fr))", gap: "10px" }}>
                  <div style={{ borderRadius: "10px", border: "1px solid var(--border-color)", background: "var(--bg-app)", padding: "11px 12px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "6px", fontSize: "11.5px", color: "var(--text-muted)" }}>
                      <span>
                        当日真实盈亏
                        <span
                          style={{
                            marginLeft: "5px",
                            fontSize: "10px",
                            padding: "0px 5px",
                            borderRadius: "4px",
                            background: "var(--hover-bg)",
                            color: "var(--text-muted)",
                          }}
                        >
                          {summary.day_pnl_basis === "ledger_plus_after_hours"
                            ? "账本+盘后"
                            : summary.day_pnl_basis === "quote_after_hours"
                              ? "收盘+盘后"
                              : summary.day_pnl_basis === "quote_regular" || summary.day_pnl_basis === "quote_estimated"
                                ? "收盘行情"
                                : "账本流水"}
                        </span>
                      </span>
                      {summary.alpha_vs_qqq != null && (
                        <span className="font-mono" style={{ fontWeight: 600, color: toneColor(summary.alpha_vs_qqq) }}>
                          vs QQQ {fmtPct(summary.alpha_vs_qqq)}
                        </span>
                      )}
                    </div>
                    <div className="font-mono" style={{ fontSize: "19px", fontWeight: 700, marginTop: "3px", color: toneColor(summary.day_pnl) }}>
                      {fmtMoney(summary.day_pnl, true)}
                    </div>
                    <div
                      className="font-mono"
                      style={{ fontSize: "11.5px", color: "var(--text-muted)", marginTop: "2px", lineHeight: 1.45 }}
                      title={
                        summary.day_pnl_denominator != null
                          ? `持仓日收益率分母：${summary.day_pnl_denominator_label || "前日持仓市值"} ${fmtMoney(summary.day_pnl_denominator)}${
                              summary.day_pnl_pct_total_assets != null && summary.cash_balance != null
                                ? ` | 总资产日收益率分母：前日总资产 ${fmtMoney(Number(summary.day_pnl_denominator) + Number(summary.cash_balance))}`
                                : ""
                            }`
                          : "日收益率分母：前日持仓市值"
                      }
                    >
                      <span>
                        持仓日收益 {fmtPct(summary.day_pnl_pct)}
                        {summary.day_pnl_pct_total_assets != null && (
                          <span style={{ marginLeft: "6px", color: "var(--text-muted)" }}>
                            (总资产 {fmtPct(summary.day_pnl_pct_total_assets)})
                          </span>
                        )}
                      </span>
                      {summary.after_hours_day_pnl != null && (
                        <span style={{ display: "block", fontSize: "11px" }}>
                          常规 {fmtMoney(summary.regular_day_pnl, true)} · 盘后 {fmtMoney(summary.after_hours_day_pnl, true)}
                        </span>
                      )}
                    </div>
                  </div>

                  {(() => {
                    const totalAssetsVal =
                      summary.total_assets != null
                        ? Number(summary.total_assets)
                        : summary.cash_balance != null && summary.total_market_value != null
                          ? Number(summary.total_market_value) + Number(summary.cash_balance)
                          : null;
                    const anomalySyms: string[] = Array.isArray(summary.cost_anomaly_symbols)
                      ? summary.cost_anomaly_symbols
                      : [];
                    return (
                      <div style={{ borderRadius: "10px", border: "1px solid var(--border-color)", background: "var(--bg-app)", padding: "11px 12px" }}>
                        <div style={{ fontSize: "11.5px", color: "var(--text-muted)" }}>
                          {totalAssetsVal != null ? "总资产（净值）/ 持仓与现金" : "总持仓市值 / 可用现金"}
                        </div>
                        <div className="font-mono" style={{ fontSize: "19px", fontWeight: 700, marginTop: "3px", color: "var(--text-heading)" }}>
                          {fmtMoney(totalAssetsVal != null ? totalAssetsVal : summary.total_market_value)}
                        </div>
                        <div
                          className="font-mono"
                          style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "2px" }}
                          title={
                            Array.isArray(summary.cash_breakdown) && summary.cash_breakdown.length > 0
                              ? summary.cash_breakdown.map((c: any) => `${c.channel}: ${fmtMoney(c.amount)}`).join(" | ")
                              : undefined
                          }
                        >
                          {totalAssetsVal != null
                            ? `持仓 ${fmtMoney(summary.total_market_value)} · 现金 ${fmtMoney(summary.cash_balance)}${
                                summary.cash_pct != null ? ` (${(Number(summary.cash_pct) * 100).toFixed(1)}%)` : ""
                              }`
                            : "可用现金 —（无独立现金账本）"}
                        </div>
                        <div
                          className="font-mono"
                          style={{ fontSize: "11.5px", color: toneColor(summary.total_unrealized_pnl), marginTop: "2px" }}
                          title={
                            anomalySyms.length > 0 && summary.unrealized_pnl_ex_anomaly != null
                              ? `含成本异常标的 (${anomalySyms.join(", ")})；剔除异常标的后参考累计浮盈：${fmtMoney(summary.unrealized_pnl_ex_anomaly, true)}`
                              : undefined
                          }
                        >
                          {Number(summary.total_unrealized_pnl) < 0 ? "累计浮亏" : "累计浮盈"}{" "}
                          {fmtMoney(summary.total_unrealized_pnl, true)} ({fmtPct(summary.unrealized_pct)})
                          {anomalySyms.length > 0 && (
                            <span style={{ marginLeft: "5px", fontSize: "10.5px", color: "#D97706", fontWeight: 600 }}>
                              ⚠ 含异常成本({anomalySyms.join(",")})
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })()}

                  <div style={{ borderRadius: "10px", border: "1px solid var(--border-color)", background: "var(--bg-app)", padding: "11px 12px" }}>
                    <div style={{ fontSize: "11.5px", color: "var(--text-muted)" }}>大盘基准（常规 / 盘后）</div>
                    <div className="font-mono" style={{ fontSize: "12px", marginTop: "6px", display: "flex", flexDirection: "column", gap: "4px" }}>
                      {["SPY", "QQQ"].map((k) => {
                        const b = benchmarks[k];
                        if (!b) return <div key={k} style={{ color: "var(--text-muted)" }}>{k} 暂无行情</div>;
                        return (
                          <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: "6px" }}>
                            <strong>{k}</strong>
                            <span style={{ color: toneColor(b.regular_change_pct) }}>收 {fmtPct(b.regular_change_pct)}</span>
                            <span style={{ color: toneColor(b.after_hours_change_pct) }}>盘后 {fmtPct(b.after_hours_change_pct)}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  <div style={{ borderRadius: "10px", border: "1px solid var(--border-color)", background: "var(--bg-app)", padding: "11px 12px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "11.5px", color: "var(--text-muted)" }}>
                      <span>杠杆仓位暴露</span>
                      {summary.effective_leverage != null && (
                        <span
                          className="font-mono"
                          style={{
                            fontSize: "10.5px",
                            fontWeight: 600,
                            padding: "1px 7px",
                            borderRadius: "999px",
                            background: "var(--bg-panel)",
                            border: "1px solid var(--border-color)",
                            color: "var(--text-heading)",
                          }}
                          title={
                            summary.effective_leverage_total_assets != null
                              ? `持仓净名义敞口 ${Number(summary.effective_leverage).toFixed(2)}x | 含现金总资产净敞口 ${Number(summary.effective_leverage_total_assets).toFixed(2)}x`
                              : "持仓加权净名义敞口倍数"
                          }
                        >
                          等效 {Number(summary.effective_leverage).toFixed(2)}x
                        </span>
                      )}
                    </div>
                    <div style={{ display: "flex", alignItems: "baseline", gap: "8px", marginTop: "3px", flexWrap: "wrap" }}>
                      <span className="font-mono" style={{ fontSize: "19px", fontWeight: 700, color: "var(--text-heading)" }}>
                        {levPctNum.toFixed(1)}%
                      </span>
                      <span
                        style={{
                          fontSize: "11.5px",
                          fontWeight: 600,
                          color: levPctNum >= 50 ? "#DC2626" : levPctNum >= 35 ? "#D97706" : "#16A34A",
                        }}
                      >
                        {levPctNum >= 50 ? "高杠杆警戒" : levPctNum >= 35 ? "中高暴露" : "稳健可控"}
                      </span>
                    </div>
                    <div style={{ height: "6px", borderRadius: "999px", background: "var(--border-color)", overflow: "hidden", marginTop: "6px" }}>
                      <div
                        style={{
                          height: "100%",
                          width: `${Math.min(100, levPctNum)}%`,
                          borderRadius: "999px",
                          background: levPctNum >= 50 ? "#DC2626" : levPctNum >= 35 ? "#D97706" : "#16A34A",
                        }}
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* 2. 多视角 Tab 容器 */}
              <div className="card-panel" style={{ display: "flex", flexDirection: "column" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px", borderBottom: "1px solid var(--border-color)", padding: "6px 14px 0" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "4px", flexWrap: "wrap" }}>
                    {[
                      { id: "overview", label: "结构化复盘总览" },
                      { id: "trades", label: `今日操作复盘 (${trades.length})` },
                      { id: "news", label: `突发新闻情报 (${allNewsFeed.length})` },
                      { id: "markdown", label: "完整长文 (Markdown)" },
                    ].map((t) => {
                      const active = activeTab === t.id;
                      return (
                        <button
                          key={t.id}
                          type="button"
                          onClick={() => setActiveTab(t.id as any)}
                          style={{
                            border: "none",
                            borderBottom: active ? "2px solid var(--brand)" : "2px solid transparent",
                            background: "transparent",
                            color: active ? "var(--brand)" : "var(--text-muted)",
                            fontWeight: active ? 700 : 500,
                            fontSize: "12.5px",
                            padding: "8px 12px",
                            cursor: "pointer",
                          }}
                        >
                          {t.label}
                        </button>
                      );
                    })}
                  </div>
                  <span style={{ fontSize: "11.5px", color: "var(--text-muted)", paddingBottom: "6px" }}>
                    点击标的可展开明细 · 清单勾选自动保存
                  </span>
                </div>

                <div style={{ padding: "16px" }}>
                  {/* TAB 1: 结构化复盘总览 */}
                  {activeTab === "overview" && (
                    <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
                      {/* 模块 B + C: 风险雷达 & 可勾选次日关注清单（首屏置顶） */}
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: "12px" }}>
                        {/* 风险雷达 */}
                        <div style={{ borderRadius: "10px", border: "1px solid var(--border-color)", background: "var(--bg-app)", padding: "14px", display: "flex", flexDirection: "column", gap: "10px" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <span style={{ fontSize: "13.5px", fontWeight: 700, color: "var(--text-heading)" }}>仓位结构与风险雷达</span>
                            <span
                              style={{
                                fontSize: "11px",
                                fontWeight: 600,
                                padding: "2px 8px",
                                borderRadius: "999px",
                                background: risks.some((r) => r.level === "高危") ? "rgba(220,38,38,0.14)" : "rgba(22,163,74,0.14)",
                                color: risks.some((r) => r.level === "高危") ? "#DC2626" : "#16A34A",
                              }}
                            >
                              {risks.filter((r) => r.level === "高危").length > 0
                                ? `${risks.filter((r) => r.level === "高危").length} 项高危提醒`
                                : "风险整体可控"}
                            </span>
                          </div>
                          {risks.length === 0 ? (
                            <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>暂无额外结构性风险提醒。</div>
                          ) : (
                            risks.map((r, rIdx) => {
                              const lvBg = r.level === "高危" ? "rgba(220,38,38,0.14)" : r.level === "中危" ? "rgba(217,119,6,0.14)" : "var(--brand-selected)";
                              const lvFg = r.level === "高危" ? "#DC2626" : r.level === "中危" ? "#D97706" : "var(--brand)";
                              return (
                                <div key={rIdx} style={{ borderRadius: "8px", border: "1px solid var(--border-color)", background: "var(--bg-panel)", padding: "10px" }}>
                                  <div style={{ display: "flex", alignItems: "center", gap: "7px", marginBottom: "4px" }}>
                                    <span style={{ fontSize: "11px", fontWeight: 700, padding: "1px 7px", borderRadius: "6px", background: lvBg, color: lvFg }}>
                                      {r.level}
                                    </span>
                                    <span style={{ fontSize: "12.5px", fontWeight: 700, color: "var(--text-heading)" }}>{r.title}</span>
                                  </div>
                                  <div style={{ fontSize: "12px", color: "var(--text-muted)", lineHeight: 1.55 }}>{r.detail}</div>
                                </div>
                              );
                            })
                          )}
                        </div>

                        {/* 次日盘前关注清单 */}
                        <div style={{ borderRadius: "10px", border: "1px solid var(--border-color)", background: "var(--bg-app)", padding: "14px", display: "flex", flexDirection: "column", gap: "10px" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <span style={{ fontSize: "13.5px", fontWeight: 700, color: "var(--text-heading)" }}>次日盘前关注清单</span>
                            <span className="font-mono" style={{ fontSize: "11.5px", fontWeight: 600, color: "var(--brand)" }}>
                              {doneChecklistCount} / {checklist.length} 已核查
                            </span>
                          </div>
                          {checklist.length === 0 ? (
                            <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>暂无次日盘前待办事项。</div>
                          ) : (
                            checklist.map((c) => {
                              const checked = checkedIds.includes(c.id);
                              return (
                                <label
                                  key={c.id}
                                  style={{
                                    display: "flex",
                                    alignItems: "flex-start",
                                    gap: "10px",
                                    borderRadius: "8px",
                                    border: "1px solid var(--border-color)",
                                    background: "var(--bg-panel)",
                                    padding: "10px",
                                    cursor: "pointer",
                                    opacity: checked ? 0.65 : 1,
                                  }}
                                >
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() => handleToggleChecklist(c.id)}
                                    style={{ marginTop: "3px", cursor: "pointer" }}
                                  />
                                  <div style={{ flex: 1 }}>
                                    <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap", marginBottom: "3px" }}>
                                      <span className="font-mono" style={{ fontSize: "11px", fontWeight: 700, padding: "1px 6px", borderRadius: "6px", background: "var(--brand-selected)", color: "var(--brand)" }}>
                                        {c.ticker}
                                      </span>
                                      <span className="font-mono" style={{ fontSize: "11.5px", fontWeight: 600, color: "#D97706" }}>
                                        {c.level}
                                      </span>
                                    </div>
                                    <div style={{ fontSize: "12px", lineHeight: 1.55, textDecoration: checked ? "line-through" : "none", color: checked ? "var(--text-muted)" : "var(--text-main)" }}>
                                      {c.text}
                                    </div>
                                  </div>
                                </label>
                              );
                            })
                          )}
                        </div>
                      </div>

                      {/* 模块 A: 重点持仓异动与归因 */}
                      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px" }}>
                          <div>
                            <span style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-heading)" }}>重点持仓异动与归因</span>
                            <span style={{ fontSize: "12px", color: "var(--text-muted)", marginLeft: "8px" }}>
                              杠杆 ETF 自动关联正股新闻
                            </span>
                          </div>
                          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
                            {[
                              { id: "all", label: "全部" },
                              { id: "up", label: "领涨" },
                              { id: "down", label: "领跌" },
                              { id: "after", label: "盘后波动" },
                              { id: "news", label: "有突发新闻" },
                            ].map((f) => {
                              const active = moverFilter === f.id;
                              return (
                                <button
                                  key={f.id}
                                  type="button"
                                  onClick={() => setMoverFilter(f.id as any)}
                                  style={{
                                    fontSize: "12px",
                                    padding: "4px 10px",
                                    borderRadius: "8px",
                                    border: active ? "1px solid var(--brand)" : "1px solid var(--border-color)",
                                    background: active ? "var(--brand-selected)" : "var(--bg-app)",
                                    color: active ? "var(--brand)" : "var(--text-muted)",
                                    fontWeight: active ? 600 : 500,
                                    cursor: "pointer",
                                  }}
                                >
                                  {f.label}
                                </button>
                              );
                            })}
                            <select
                              className="input-control"
                              value={moverSort}
                              onChange={(e) => setMoverSort(e.target.value as any)}
                              style={{ fontSize: "12px", padding: "4px 8px" }}
                            >
                              <option value="day_pnl">按当日盈亏贡献</option>
                              <option value="weight">按仓位权重</option>
                              <option value="change">按涨跌幅</option>
                            </select>
                            <button
                              type="button"
                              className="btn"
                              onClick={() => {
                                const allSyms = holdings.map((x) => String(x.symbol || "")).filter(Boolean);
                                const isAll = allSyms.length > 0 && allSyms.every((s) => expandedSyms.includes(s));
                                setExpandedSyms(isAll ? defaultExpandedForHoldings(holdings) : allSyms);
                              }}
                              style={{ fontSize: "12px", padding: "4px 10px" }}
                            >
                              {filteredHoldings.length > 0 && filteredHoldings.every((x) => expandedSyms.includes(String(x.symbol || "")))
                                ? "收起小仓位"
                                : "全部展开"}
                            </button>
                          </div>
                        </div>

                        {filteredHoldings.length === 0 ? (
                          <div style={{ padding: "20px", textAlign: "center", fontSize: "12.5px", color: "var(--text-muted)", border: "1px dashed var(--border-color)", borderRadius: "10px" }}>
                            当前筛选条件下无匹配持仓标的
                          </div>
                        ) : (
                          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                            {filteredHoldings.map((h) => {
                              const hNews = getNewsForHolding(h);
                              const reason = moverReasons[h.symbol];
                              const pnlNum = h.day_pnl ?? h.unrealized_pnl ?? 0;
                              const symKey = String(h.symbol || "");
                              const isExpanded = expandedSyms.includes(symKey);
                              return (
                                <div
                                  key={h.symbol}
                                  style={{
                                    borderRadius: "10px",
                                    border: "1px solid var(--border-color)",
                                    background: "var(--bg-app)",
                                    overflow: "hidden",
                                  }}
                                >
                                  <div
                                    onClick={() =>
                                      setExpandedSyms((prev) =>
                                        prev.includes(symKey) ? prev.filter((x) => x !== symKey) : [...prev, symKey]
                                      )
                                    }
                                    style={{
                                      padding: "11px 14px",
                                      display: "flex",
                                      flexWrap: "wrap",
                                      alignItems: "center",
                                      justifyContent: "space-between",
                                      gap: "10px",
                                      cursor: "pointer",
                                    }}
                                  >
                                    <div style={{ display: "flex", alignItems: "center", gap: "10px", minWidth: "200px" }}>
                                      <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>{isExpanded ? "▾" : "▸"}</span>
                                      <div
                                        style={{
                                          width: "4px",
                                          height: "36px",
                                          borderRadius: "999px",
                                          background: pnlNum >= 0 ? "#16A34A" : "#DC2626",
                                          flexShrink: 0,
                                        }}
                                      />
                                      <div>
                                        <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
                                          <span className="font-mono" style={{ fontWeight: 700, fontSize: "14px", color: "var(--text-heading)" }}>
                                            {h.symbol}
                                          </span>
                                          {h.underlying && (
                                            <span className="font-mono" style={{ fontSize: "11px", padding: "1px 6px", borderRadius: "6px", background: "var(--brand-selected)", color: "var(--brand)", fontWeight: 600 }}>
                                              → {h.underlying}
                                            </span>
                                          )}
                                          {h.is_leveraged && (
                                            <span style={{ fontSize: "11px", padding: "1px 6px", borderRadius: "6px", background: "rgba(217,119,6,0.14)", color: "#D97706", fontWeight: 600 }}>
                                              {h.leverage_factor != null ? `${Number(h.leverage_factor)}x 杠杆` : "杠杆"}
                                            </span>
                                          )}
                                          {h.cost_anomaly && (
                                            <span
                                              style={{ fontSize: "11px", padding: "1px 6px", borderRadius: "6px", background: "#FEF3C7", color: "#B45309", border: "1px solid #F59E0B", fontWeight: 600 }}
                                              title={h.cost_anomaly_reason || "现价与摊薄均价偏离过大，请核对拆股或历史成交录入价"}
                                            >
                                              ⚠ 成本偏离核对
                                            </span>
                                          )}
                                          {hNews.length > 0 && (
                                            <span style={{ fontSize: "11px", padding: "1px 6px", borderRadius: "6px", background: "var(--bg-panel)", border: "1px solid var(--border-color)", color: "var(--text-muted)" }}>
                                              新闻 {hNews.length}
                                            </span>
                                          )}
                                        </div>
                                        <div style={{ fontSize: "11.5px", color: "var(--text-muted)", marginTop: "2px" }}>
                                          {(h.channels || []).join(" / ")} · 仓位 {h.weight_pct != null ? `${(h.weight_pct * 100).toFixed(1)}%` : "—"} · 摊薄均价 {fmtMoney(h.avg_cost)}
                                          {(h.is_zero_cost || (h.avg_cost != null && Number(h.avg_cost) <= 0)) && (
                                            <span style={{ color: "#16A34A", fontWeight: 600 }}>（已零成本/利润仓）</span>
                                          )}
                                          {h.need_breakeven_pct != null && !(h.is_zero_cost || (h.avg_cost != null && Number(h.avg_cost) <= 0)) && (
                                            <span style={{ color: "#D97706" }}>
                                              {" "}· 回本需 +{(h.need_breakeven_pct * 100).toFixed(1)}%{h.cost_anomaly ? " ⚠" : ""}
                                            </span>
                                          )}
                                        </div>
                                      </div>
                                    </div>

                                    <div className="font-mono" style={{ display: "flex", alignItems: "center", gap: "18px", fontSize: "12px", flexWrap: "wrap" }}>
                                      <div style={{ textAlign: "right" }}>
                                        <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>常规收盘</div>
                                        <div>
                                          <strong>{fmtMoney(h.regular_close)}</strong>{" "}
                                          <span style={{ color: toneColor(h.regular_change_pct) }}>{fmtPct(h.regular_change_pct)}</span>
                                        </div>
                                      </div>
                                      <div style={{ textAlign: "right" }}>
                                        <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>盘后最新</div>
                                        <div>
                                          <strong>{fmtMoney(h.after_hours_price)}</strong>{" "}
                                          <span style={{ color: toneColor(h.after_hours_change_pct) }}>{fmtPct(h.after_hours_change_pct)}</span>
                                        </div>
                                      </div>
                                      <div style={{ textAlign: "right", minWidth: "84px" }}>
                                        <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>当日盈亏</div>
                                        <div style={{ fontSize: "13.5px", fontWeight: 700, color: toneColor(h.day_pnl) }}>
                                          {fmtMoney(h.day_pnl, true)}
                                        </div>
                                      </div>
                                    </div>
                                  </div>

                                  {isExpanded && (
                                  <div style={{ padding: "10px 14px 12px", borderTop: "1px solid var(--border-color)", display: "flex", flexDirection: "column", gap: "8px", background: "var(--bg-panel)" }}>
                                      <div style={{ fontSize: "12.5px", lineHeight: 1.6 }}>
                                        <strong style={{ color: "var(--brand)" }}>AI 异动归因：</strong>
                                        {reason || `当前持仓 ${h.quantity} 股，${Number(h.unrealized_pnl) < 0 ? "浮亏" : "浮盈"} ${fmtMoney(h.unrealized_pnl, true)}；可切换至「完整长文」查看全文点评。`}
                                      </div>
                                      {hNews.length > 0 ? (
                                        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                                          {hNews.map((n, nIdx) => {
                                            const sent = n.sentiment || newsSentiments[n.title] || "中性";
                                            const st = sentimentStyle(sent);
                                            const displayTitle = n.zh_title || n.title;
                                            const hasLink = typeof n.url === "string" && /^https?:\/\//i.test(n.url);
                                            return (
                                              <div
                                                key={nIdx}
                                                style={{
                                                  display: "flex",
                                                  flexDirection: "column",
                                                  gap: "4px",
                                                  padding: "8px 10px",
                                                  borderRadius: "8px",
                                                  border: "1px solid var(--border-color)",
                                                  background: "var(--bg-app)",
                                                  fontSize: "12px",
                                                }}
                                                title={n.title && n.title !== displayTitle ? `原文：${n.title}` : undefined}
                                              >
                                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "10px" }}>
                                                  <div style={{ display: "flex", alignItems: "flex-start", gap: "7px", minWidth: 0, flex: 1 }}>
                                                    <span style={{ fontSize: "11px", fontWeight: 600, padding: "1px 6px", borderRadius: "6px", background: st.bg, color: st.fg, flexShrink: 0, marginTop: "1px" }}>
                                                      {sent}
                                                    </span>
                                                    {hasLink ? (
                                                      <a
                                                        href={n.url}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        style={{ fontWeight: 600, color: "var(--brand)", lineHeight: 1.45, textDecoration: "none" }}
                                                      >
                                                        {displayTitle} ↗
                                                      </a>
                                                    ) : (
                                                      <span style={{ fontWeight: 600, color: "var(--text-heading)", lineHeight: 1.45 }}>{displayTitle}</span>
                                                    )}
                                                  </div>
                                                  <span className="font-mono" style={{ fontSize: "11px", color: "var(--text-muted)", flexShrink: 0 }}>
                                                    {n.publisher} · {n.published_bj}
                                                  </span>
                                                </div>
                                                {n.summary && (
                                                  <div style={{ fontSize: "11.5px", color: "var(--text-muted)", lineHeight: 1.5, paddingLeft: "4px" }}>
                                                    摘要：{n.summary}
                                                  </div>
                                                )}
                                              </div>
                                            );
                                          })}
                                        </div>
                                      ) : (
                                        <div style={{ fontSize: "11.5px", color: "var(--text-muted)" }}>近 36 小时无显著突发新闻。</div>
                                      )}
                                  </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* TAB 2: 今日操作复盘 */}
                  {activeTab === "trades" && (
                    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                      <div style={{ borderRadius: "8px", border: "1px solid var(--border-color)", background: "var(--bg-app)", padding: "10px 12px", fontSize: "12.5px", lineHeight: 1.6 }}>
                        <strong>今日操作总评：</strong>
                        {structured?.trades_summary || "暂无操作点评。"}
                      </div>
                      {trades.length === 0 ? (
                        <div style={{ padding: "24px", textAlign: "center", fontSize: "12.5px", color: "var(--text-muted)", border: "1px dashed var(--border-color)", borderRadius: "10px" }}>
                          该交易日无真实成交记录
                        </div>
                      ) : (
                        trades.map((t, idx) => {
                          const isBuy = String(t.side || "").toUpperCase() === "BUY" || t.side === "买入";
                          const review = (structured?.trade_reviews || []).find(
                            (r: any) => r.symbol === t.symbol && (!r.side || r.side === String(t.side || "").toUpperCase())
                          );
                          const diffPct = t.vs_close_pct;
                          return (
                            <div key={idx} style={{ borderRadius: "10px", border: "1px solid var(--border-color)", background: "var(--bg-app)", padding: "12px 14px", display: "flex", flexDirection: "column", gap: "6px" }}>
                              <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
                                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                                  <span style={{ fontSize: "11.5px", fontWeight: 700, padding: "2px 8px", borderRadius: "6px", background: isBuy ? "rgba(22,163,74,0.14)" : "rgba(220,38,38,0.14)", color: isBuy ? "#16A34A" : "#DC2626" }}>
                                    {isBuy ? "买入" : "卖出"}
                                  </span>
                                  <span className="font-mono" style={{ fontSize: "14px", fontWeight: 700 }}>{t.symbol}</span>
                                  <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>{t.channel} · {t.date}</span>
                                </div>
                                <div className="font-mono" style={{ display: "flex", alignItems: "center", gap: "12px", fontSize: "12px", flexWrap: "wrap" }}>
                                  <span>{t.quantity} 股 @ <strong>{fmtMoney(t.price)}</strong></span>
                                  <span style={{ color: "var(--text-muted)" }}>收盘 {fmtMoney(t.close_price)}</span>
                                  {diffPct != null && (
                                    <span style={{ fontSize: "11.5px", fontWeight: 600, padding: "2px 7px", borderRadius: "6px", background: diffPct >= 0 ? "rgba(22,163,74,0.14)" : "rgba(217,119,6,0.14)", color: diffPct >= 0 ? "#16A34A" : "#D97706" }}>
                                      {review?.verdict || (diffPct >= 0 ? "节奏占优" : "偏早/偏追")} ({fmtPct(diffPct)})
                                    </span>
                                  )}
                                </div>
                              </div>
                              {review?.comment && (
                                <div style={{ fontSize: "12.5px", color: "var(--text-muted)", lineHeight: 1.6 }}>
                                  <strong style={{ color: "var(--text-main)" }}>AI 复盘点评：</strong>
                                  {review.comment}
                                </div>
                              )}
                            </div>
                          );
                        })
                      )}
                    </div>
                  )}

                  {/* TAB 3: 突发新闻情报 */}
                  {activeTab === "news" && (
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                      {allNewsFeed.length === 0 ? (
                        <div style={{ padding: "24px", textAlign: "center", fontSize: "12.5px", color: "var(--text-muted)", border: "1px dashed var(--border-color)", borderRadius: "10px" }}>
                          近 36 小时未抓取到持仓/交易标的的显著突发新闻
                        </div>
                      ) : (
                        allNewsFeed.map((n, idx) => {
                          const st = sentimentStyle(n.sentiment);
                          const displayTitle = n.zh_title || n.title;
                          return (
                            <div
                              key={idx}
                              style={{ borderRadius: "10px", border: "1px solid var(--border-color)", background: "var(--bg-app)", padding: "11px 14px", display: "flex", flexDirection: "column", gap: "5px" }}
                              title={n.title && n.title !== displayTitle ? `原文：${n.title}` : undefined}
                            >
                              <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                                <span className="font-mono" style={{ fontSize: "11.5px", fontWeight: 700, padding: "1px 7px", borderRadius: "6px", background: "var(--brand-selected)", color: "var(--brand)" }}>
                                  {n.symbol}
                                </span>
                                <span style={{ fontSize: "11px", fontWeight: 600, padding: "1px 7px", borderRadius: "6px", background: st.bg, color: st.fg }}>
                                  {n.sentiment}
                                </span>
                                <span className="font-mono" style={{ fontSize: "11.5px", color: "var(--text-muted)" }}>
                                  {n.publisher} · {n.published_bj}
                                </span>
                              </div>
                              <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-heading)", lineHeight: 1.5 }}>
                                {typeof n.url === "string" && /^https?:\/\//i.test(n.url) ? (
                                  <a
                                    href={n.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    style={{ color: "var(--brand)", textDecoration: "none" }}
                                  >
                                    {displayTitle} ↗
                                  </a>
                                ) : (
                                  displayTitle
                                )}
                              </div>
                              {n.summary && (
                                <div style={{ fontSize: "12px", color: "var(--text-muted)", lineHeight: 1.55 }}>
                                  核心影响：{n.summary}
                                </div>
                              )}
                            </div>
                          );
                        })
                      )}
                    </div>
                  )}

                  {/* TAB 4: 完整长文 Markdown */}
                  {activeTab === "markdown" && (
                    <div style={{ fontSize: "13.5px", lineHeight: 1.75, color: "var(--text-main)" }}>
                      <MarkdownBlock text={detail.markdown || ""} />
                    </div>
                  )}
                </div>
              </div>

              {/* 3. 底部带上下文追问条 + 历史追问回答 */}
              <div className="card-panel" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: "10px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px" }}>
                  <span style={{ fontSize: "12px", fontWeight: 700, color: "var(--text-muted)" }}>
                    基于这份盘后日报继续追问 Gemini
                  </span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                    {followups.map((q, idx) => (
                      <button
                        key={idx}
                        type="button"
                        disabled={followupLoading}
                        onClick={() => handleSendFollowup(q)}
                        style={{
                          fontSize: "11.5px",
                          padding: "4px 10px",
                          borderRadius: "999px",
                          border: "1px solid var(--border-color)",
                          background: "var(--bg-app)",
                          color: "var(--text-main)",
                          cursor: followupLoading ? "not-allowed" : "pointer",
                        }}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </div>

                <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                  <input
                    type="text"
                    className="input-control"
                    value={followupInput}
                    onChange={(e) => setFollowupInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSendFollowup();
                    }}
                    disabled={followupLoading}
                    placeholder="输入想基于这份盘后日报继续追问的问题（自动携带今日快照上下文）..."
                    style={{ flex: 1, fontSize: "12.5px", padding: "7px 11px" }}
                  />
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={followupLoading || !followupInput.trim()}
                    onClick={() => handleSendFollowup()}
                    style={{ padding: "7px 14px", fontSize: "12.5px", display: "inline-flex", alignItems: "center", gap: "5px", fontWeight: 600 }}
                  >
                    <Send size={13} />
                    {followupLoading ? "思考中..." : "追问 Gemini"}
                  </button>
                </div>

                {followupsHistory.length > 0 && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginTop: "4px", borderTop: "1px solid var(--border-color)", paddingTop: "10px" }}>
                    {followupsHistory.map((item, idx) => (
                      <div key={idx} style={{ borderRadius: "8px", border: "1px solid var(--border-color)", background: "var(--bg-app)", padding: "10px 12px", display: "flex", flexDirection: "column", gap: "6px" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "12px", fontWeight: 700, color: "var(--brand)" }}>
                          <span>问：{item.question}</span>
                          <span className="font-mono" style={{ fontSize: "11px", color: "var(--text-muted)", fontWeight: 400 }}>{item.time_str || ""}</span>
                        </div>
                        <div style={{ fontSize: "13px", lineHeight: 1.65 }}>
                          <MarkdownBlock text={item.answer || ""} />
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
