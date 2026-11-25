import React, { useEffect, useState } from "react";
import {
  Calendar,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  FileText,
  RefreshCw,
  Sparkles,
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
  total_market_value?: number | null;
  trade_count: number;
  holding_count: number;
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

const DEFAULT_MODELS: AgyModelOption[] = [
  { id: "gemini-4-hydrogen-high", label: "Gemini 4 Hydrogen (High)" },
  { id: "gemini-4-hydrogen-medium", label: "Gemini 4 Hydrogen (Medium)" },
  { id: "gemini-4-neon-medium", label: "Gemini 4 Neon (Medium)" },
  { id: "gemini-4-neon-high", label: "Gemini 4 Neon (High)" },
  { id: "gemini-3.8-flash-medium", label: "Gemini 3.8 Flash (Medium)" },
];

function fmtMoney(v: number | null | undefined, signed = false): string {
  if (v == null || Number.isNaN(v)) return "—";
  const prefix = signed && v > 0 ? "+$" : v < 0 ? "-$" : "$";
  return `${prefix}${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const pct = v * 100;
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

/** 美股盘后复盘日报页面（支持按人员/日期生成、自动同步流水、历史存档回看与事实快照核对） */
export const AfterHoursReportView: React.FC = () => {
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [personOptions, setPersonOptions] = useState<PersonOption[]>([
    { key: "个人", label: "个人" },
    { key: "ALL", label: "全部人员" },
  ]);
  const [isIsolated, setIsIsolated] = useState(false);
  const [canSync, setCanSync] = useState(false);

  const [filterPerson, setFilterPerson] = useState<string>("");
  const [genPerson, setGenPerson] = useState<string>("个人");
  const [genDate, setGenDate] = useState<string>("");
  const [runSync, setRunSync] = useState<boolean>(true);
  const [models, setModels] = useState<AgyModelOption[]>(DEFAULT_MODELS);
  const [selectedModel, setSelectedModel] = useState<string>("gemini-4-hydrogen-high");

  const [selectedId, setSelectedId] = useState<string>("");
  const [detail, setDetail] = useState<any>(null);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [errorMsg, setErrorMsg] = useState("");
  const [copied, setCopied] = useState(false);
  const [showSnapshot, setShowSnapshot] = useState(false);

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
    loadList("");
    api
      .aiAgyModels(false)
      .then((res) => {
        if (res.status === "ok" && Array.isArray(res.models) && res.models.length > 0) {
          setModels(res.models);
          if (res.current_model) setSelectedModel(res.current_model);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    setLoadingDetail(true);
    api
      .aiAfterHoursDetail(selectedId)
      .then((res) => {
        if (res.status === "ok") {
          setDetail(res.report);
        }
      })
      .catch((err) => setErrorMsg(err.message || "加载日报详情失败"))
      .finally(() => setLoadingDetail(false));
  }, [selectedId]);

  useEffect(() => {
    if (!generating) {
      setElapsedSec(0);
      return;
    }
    const timer = window.setInterval(() => setElapsedSec((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [generating]);

  const handleGenerate = async () => {
    if (generating) return;
    setErrorMsg("");
    setGenerating(true);
    try {
      const res = await api.aiAfterHoursGenerate({
        trade_date: genDate || undefined,
        person: genPerson || "个人",
        run_sync: canSync ? runSync : false,
        provider: "gemini",
        model: selectedModel,
      });
      if (res.status === "ok" && res.report) {
        setDetail(res.report);
        setSelectedId(res.report.id);
        loadList(filterPerson, res.report.id);
      } else {
        setErrorMsg(res.message || "生成日报失败");
      }
    } catch (err: any) {
      setErrorMsg(err.message || "生成日报请求异常");
    } finally {
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

  const snap = detail?.snapshot;
  const summary = snap?.summary || {};
  const benchmarks = snap?.market_benchmarks || {};

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "12px", minHeight: 0 }}>
      {/* 顶部控制栏：日期、人员范围、模型、同步开关与生成按钮 */}
      <div
        className="card-panel"
        style={{
          padding: "12px 16px",
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
              onChange={(e) => setGenPerson(e.target.value)}
              disabled={generating || isIsolated}
              style={{ fontSize: "12px", padding: "4px 8px", minWidth: "110px" }}
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
              style={{ fontSize: "12px", padding: "4px 8px", minWidth: "190px" }}
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
              <span>生成前同步券商流水</span>
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
            刷新列表
          </button>
          <button
            className="btn btn-primary"
            onClick={handleGenerate}
            disabled={generating}
            style={{ fontSize: "12.5px", padding: "6px 14px", display: "inline-flex", alignItems: "center", gap: "6px", fontWeight: 600 }}
          >
            <Sparkles size={14} />
            {generating ? `正在同步并生成 (${elapsedSec}s)...` : "生成 / 重算复盘日报"}
          </button>
        </div>
      </div>

      {errorMsg && (
        <div
          style={{
            padding: "8px 12px",
            borderRadius: "8px",
            background: "#FEE2E2",
            color: "#991B1B",
            fontSize: "12.5px",
          }}
        >
          ⚠️ {errorMsg}
        </div>
      )}

      {/* 主体左右双栏 */}
      <div style={{ flex: 1, display: "flex", gap: "12px", minHeight: 0 }}>
        {/* 左侧：按交易日倒序的历史日报列表 */}
        <div
          className="card-panel"
          style={{
            width: "270px",
            minWidth: "240px",
            padding: "12px",
            display: "flex",
            flexDirection: "column",
            gap: "10px",
            minHeight: 0,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
            <span style={{ fontSize: "13px", fontWeight: 700, color: "var(--text-heading)" }}>
              历史复盘 ({reports.length})
            </span>
            {!isIsolated && (
              <select
                className="input-control"
                value={filterPerson}
                onChange={(e) => {
                  const val = e.target.value;
                  setFilterPerson(val);
                  loadList(val);
                }}
                style={{ fontSize: "11.5px", padding: "2px 6px" }}
              >
                <option value="">全部范围</option>
                {personOptions.map((opt) => (
                  <option key={opt.key} value={opt.key}>
                    {opt.label}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "6px" }}>
            {loadingList ? (
              <div style={{ fontSize: "12px", color: "var(--text-muted)", textAlign: "center", padding: "16px" }}>
                加载中...
              </div>
            ) : reports.length === 0 ? (
              <div style={{ fontSize: "12px", color: "var(--text-muted)", textAlign: "center", padding: "20px 8px", lineHeight: 1.6 }}>
                暂无历史盘后日报。
                <br />
                点击右上角「生成 / 重算复盘日报」立即生成，或等待美股盘后（美东 20:10）自动生成。
              </div>
            ) : (
              reports.map((rep) => {
                const active = rep.id === selectedId;
                const pnl = rep.day_pnl;
                const pnlColor = pnl == null ? "var(--text-muted)" : pnl > 0 ? "var(--profit-color)" : pnl < 0 ? "var(--loss-color)" : "var(--text-main)";
                return (
                  <button
                    key={rep.id}
                    type="button"
                    onClick={() => setSelectedId(rep.id)}
                    style={{
                      textAlign: "left",
                      padding: "10px",
                      borderRadius: "8px",
                      border: active ? "1px solid var(--brand)" : "1px solid var(--border-color)",
                      background: active ? "var(--brand-selected)" : "var(--bg-app)",
                      cursor: "pointer",
                      display: "flex",
                      flexDirection: "column",
                      gap: "4px",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <span className="font-mono" style={{ fontWeight: 700, fontSize: "13px", color: "var(--text-heading)" }}>
                        {rep.trade_date}
                      </span>
                      <span
                        style={{
                          fontSize: "10.5px",
                          padding: "1px 6px",
                          borderRadius: "10px",
                          background: "var(--hover-bg)",
                          color: "var(--brand)",
                          fontWeight: 600,
                        }}
                      >
                        {rep.person_name}
                      </span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11.5px" }}>
                      <span style={{ color: "var(--text-muted)" }}>
                        成交 {rep.trade_count} 笔 · 持仓 {rep.holding_count}
                      </span>
                      <span className="font-mono" style={{ fontWeight: 600, color: pnlColor }}>
                        {fmtMoney(pnl, true)}
                      </span>
                    </div>
                    <div style={{ fontSize: "10.5px", color: "var(--text-subtle)" }}>
                      {rep.trigger === "auto" ? "定时自动" : "手动生成"} · {(rep.generated_at || "").slice(5, 16).replace("T", " ")}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* 右侧：完整复盘正文与事实快照 */}
        <div
          className="card-panel"
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "20px",
            display: "flex",
            flexDirection: "column",
            gap: "14px",
            minHeight: 0,
          }}
        >
          {loadingDetail ? (
            <div style={{ margin: "auto", color: "var(--text-muted)", fontSize: "13px" }}>
              正在读取复盘日报详情...
            </div>
          ) : !detail ? (
            <div style={{ margin: "auto", textAlign: "center", color: "var(--text-muted)", fontSize: "13px", lineHeight: 1.8 }}>
              <FileText size={32} style={{ opacity: 0.4, marginBottom: "8px" }} />
              <div>选择一个历史交易日查看盘后总结，或点击上方按钮立即生成。</div>
              <div style={{ fontSize: "12px", opacity: 0.8 }}>
                每个美股交易日盘后（美东 20:10）会自动先同步券商流水，再结合盘后收盘价、涨跌幅与新闻生成「个人」盘后复盘日报。
              </div>
            </div>
          ) : (
            <>
              {/* 报告头部与核心指标 */}
              <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-start", gap: "10px", borderBottom: "1px solid var(--border-color)", paddingBottom: "12px" }}>
                <div>
                  <div style={{ fontSize: "17px", fontWeight: 700, color: "var(--text-heading)" }}>
                    {detail.trade_date} 美股盘后复盘日报 · {detail.person_name}
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "4px" }}>
                    生成时间：{(detail.generated_at || "").replace("T", " ")} · 引擎：{detail.provider}
                    {detail.model ? ` (${detail.model})` : ""} · 同步状态：
                    <span style={{ color: detail.sync_status?.ok === false ? "var(--loss-color)" : "#16A34A", fontWeight: 600 }}>
                      {detail.sync_status?.ran ? (detail.sync_status?.ok ? "已自动同步" : "同步异常") : "未执行同步"}
                    </span>
                  </div>
                </div>
                <button
                  className="btn"
                  onClick={handleCopy}
                  style={{ fontSize: "12px", padding: "5px 10px", display: "inline-flex", alignItems: "center", gap: "5px" }}
                >
                  {copied ? <Check size={13} color="#16A34A" /> : <Copy size={13} />}
                  {copied ? "已复制 Markdown" : "复制全文"}
                </button>
              </div>

              {/* 关键指标卡片 */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "10px" }}>
                <div style={{ padding: "10px 12px", borderRadius: "8px", background: "var(--bg-app)", border: "1px solid var(--border-color)" }}>
                  <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>当日盈亏</div>
                  <div className="font-mono" style={{ fontSize: "16px", fontWeight: 700, marginTop: "2px" }}>
                    {fmtMoney(summary.day_pnl, true)}
                  </div>
                </div>
                <div style={{ padding: "10px 12px", borderRadius: "8px", background: "var(--bg-app)", border: "1px solid var(--border-color)" }}>
                  <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>总持仓市值</div>
                  <div className="font-mono" style={{ fontSize: "16px", fontWeight: 700, marginTop: "2px" }}>
                    {fmtMoney(summary.total_market_value)}
                  </div>
                </div>
                <div style={{ padding: "10px 12px", borderRadius: "8px", background: "var(--bg-app)", border: "1px solid var(--border-color)" }}>
                  <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>当日成交 / 持仓数</div>
                  <div className="font-mono" style={{ fontSize: "16px", fontWeight: 700, marginTop: "2px" }}>
                    {summary.trade_count || 0} 笔 / {summary.holding_count || 0} 只
                  </div>
                </div>
                {(benchmarks.SPY || benchmarks.QQQ) && (
                  <div style={{ padding: "10px 12px", borderRadius: "8px", background: "var(--bg-app)", border: "1px solid var(--border-color)" }}>
                    <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>大盘 SPY / QQQ</div>
                    <div className="font-mono" style={{ fontSize: "12.5px", fontWeight: 600, marginTop: "4px", lineHeight: 1.5 }}>
                      {benchmarks.SPY && <div>SPY {fmtPct(benchmarks.SPY.regular_change_pct)} (盘后 {fmtPct(benchmarks.SPY.after_hours_change_pct)})</div>}
                      {benchmarks.QQQ && <div>QQQ {fmtPct(benchmarks.QQQ.regular_change_pct)} (盘后 {fmtPct(benchmarks.QQQ.after_hours_change_pct)})</div>}
                    </div>
                  </div>
                )}
              </div>

              {/* Gemini 完整 Markdown 总结 */}
              <div style={{ fontSize: "13.5px", lineHeight: 1.75, color: "var(--text-main)" }}>
                <MarkdownBlock text={detail.markdown || ""} />
              </div>

              {/* 底部可折叠：原始事实快照 */}
              {snap && (
                <div style={{ borderTop: "1px solid var(--border-color)", paddingTop: "12px", marginTop: "8px" }}>
                  <button
                    type="button"
                    onClick={() => setShowSnapshot(!showSnapshot)}
                    style={{
                      border: "none",
                      background: "transparent",
                      color: "var(--brand)",
                      fontSize: "12.5px",
                      fontWeight: 600,
                      cursor: "pointer",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "4px",
                    }}
                  >
                    {showSnapshot ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    查看原始事实快照（当日成交 {snap.trades?.length || 0} 笔 · 持仓盘后行情 {snap.holdings?.length || 0} 只 · 新闻 {Object.keys(snap.news || {}).length} 标的）
                  </button>

                  {showSnapshot && (
                    <div style={{ marginTop: "10px", display: "flex", flexDirection: "column", gap: "12px", fontSize: "12px" }}>
                      {snap.holdings?.length > 0 && (
                        <div className="table-container">
                          <table className="finance-table">
                            <thead>
                              <tr>
                                <th>标的</th>
                                <th style={{ textAlign: "right" }}>股数</th>
                                <th style={{ textAlign: "right" }}>常规收盘</th>
                                <th style={{ textAlign: "right" }}>常规涨跌</th>
                                <th style={{ textAlign: "right" }}>盘后价</th>
                                <th style={{ textAlign: "right" }}>盘后涨跌</th>
                                <th style={{ textAlign: "right" }}>当日盈亏</th>
                              </tr>
                            </thead>
                            <tbody>
                              {snap.holdings.map((h: any) => (
                                <tr key={h.symbol}>
                                  <td style={{ fontWeight: 700 }}>{h.symbol}{h.underlying ? ` (${h.underlying})` : ""}</td>
                                  <td className="font-mono" style={{ textAlign: "right" }}>{h.quantity}</td>
                                  <td className="font-mono" style={{ textAlign: "right" }}>{fmtMoney(h.regular_close)}</td>
                                  <td className="font-mono" style={{ textAlign: "right" }}>{fmtPct(h.regular_change_pct)}</td>
                                  <td className="font-mono" style={{ textAlign: "right" }}>{fmtMoney(h.after_hours_price)}</td>
                                  <td className="font-mono" style={{ textAlign: "right" }}>{fmtPct(h.after_hours_change_pct)}</td>
                                  <td className="font-mono" style={{ textAlign: "right" }}>{fmtMoney(h.day_pnl, true)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}

                      {snap.news && Object.keys(snap.news).length > 0 && (
                        <div style={{ padding: "10px 12px", borderRadius: "8px", background: "var(--bg-app)", border: "1px solid var(--border-color)" }}>
                          <div style={{ fontWeight: 700, marginBottom: "6px" }}>抓取到的近 36 小时相关新闻</div>
                          {Object.entries(snap.news).map(([sym, items]: [string, any]) => (
                            <div key={sym} style={{ marginBottom: "6px" }}>
                              <strong>{sym}</strong>：
                              {(items || []).map((n: any, idx: number) => (
                                <div key={idx} style={{ paddingLeft: "10px", color: "var(--text-muted)", lineHeight: 1.5 }}>
                                  • [{n.published_bj} · {n.publisher}] {n.title}
                                </div>
                              ))}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
