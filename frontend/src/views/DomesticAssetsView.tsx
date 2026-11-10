import { useEffect, useState } from "react";
import { requestApi } from "../api/client";

const VIEWS = [
  ["positions", "基金持仓"],
  ["transactions", "基金交易明细"],
  ["monthly", "月度资产变化"],
  ["imports", "数据导入与校验"],
] as const;
type View = typeof VIEWS[number][0];
type LedgerRow = Record<string, string | number | null>;
interface Result {
  columns: [string, string][];
  rows: LedgerRow[];
  page: number;
  size: number;
  total: number;
  raw_path: string;
  calc_path: string;
  as_of: string;
  stale: boolean;
  run: LedgerRow | null;
  checks: LedgerRow[];
  issues: LedgerRow[];
}
const ROOT = "/api/v1/domestic";
const TYPES = ["定投买入", "用户买入", "用户卖出", "用户跨TA转换", "机构分红", "现金分红", "红利再投资"];

export function DomesticAssetsView({ refreshKey }: { refreshKey: number }) {
  const [view, setView] = useState<View>("positions");
  const [filters, setFilters] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [pdf, setPdf] = useState<File | null>(null);
  const [sourceType, setSourceType] = useState("opening_asset");
  const [snapshot, setSnapshot] = useState("2021-11-11");
  const [preview, setPreview] = useState<{ digest: string; summary: Record<string, unknown>; issues: LedgerRow[] } | null>(null);
  const [source, setSource] = useState<LedgerRow | null>(null);
  const [operationMessage, setOperationMessage] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const params = new URLSearchParams(filters);
    params.set("page", String(page));
    requestApi<Result>(`${ROOT}/${view}?${params}`)
      .then(result => { if (active) setData(result); })
      .catch(error => { if (active) { setData(null); setError(error.message); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [view, filters, page, refreshKey, revision]);

  async function importPdf(commit: boolean) {
    if (!pdf || busy || (commit && !preview)) return;
    const body = new FormData();
    body.set("pdf", pdf);
    body.set("source_type", sourceType);
    body.set("snapshot", snapshot);
    if (preview && commit) body.set("digest", preview.digest);
    setBusy(true);
    setError("");
    setOperationMessage("");
    try {
      const result = await requestApi(`${ROOT}/import/${commit ? "commit" : "preview"}`, { method: "POST", body });
      if (commit) {
        setPreview(null);
        setOperationMessage(result.imports?.[0]?.status === "already_imported" ? "该文件已导入，无重复入账。" : "原始批次已入库。");
        setRevision(value => value + 1);
      } else {
        setPreview(result);
      }
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function rebuild() {
    setBusy(true);
    setError("");
    setOperationMessage("");
    try {
      await requestApi(`${ROOT}/rebuild`, { method: "POST" });
      setOperationMessage("全量重算完成。");
      setRevision(value => value + 1);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function openSource(id: string) {
    requestApi<{row: LedgerRow}>(`${ROOT}/transactions/${id}`)
      .then(value => setSource(value.row))
      .catch(error => setError(error.message));
  }

  return <section style={{ overflowY: "auto", padding: 12 }}>
    <nav aria-label="国内资产页面" style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
      {VIEWS.map(([key, label]) => <button key={key} className={view === key ? "btn btn-primary" : "btn"}
        onClick={() => { setView(key); setData(null); setPage(1); setFilters(""); setSource(null); }}>{label}</button>)}
    </nav>
    {view !== "imports" && <form key={view} className="card-panel" style={{ display: "flex", flexWrap: "wrap", gap: 8, padding: 12 }}
      onSubmit={event => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        setFilters(new URLSearchParams(Array.from(form.entries(), ([key, value]) => [key, String(value)])).toString());
        setPage(1);
      }}>
      {view === "positions" && <label>持仓日期 <input className="input-control" name="as_of" type="date" /></label>}
      {view === "transactions" && <>
        <label>确认起日 <input className="input-control" name="start" type="date" /></label>
        <label>确认止日 <input className="input-control" name="end" type="date" /></label>
        <select className="input-control" aria-label="原始交易类型" name="kind"><option value="">全部类型</option>
          {TYPES.map(kind => <option key={kind}>{kind}</option>)}</select>
        <select className="input-control" aria-label="交易时间排序" name="sort">
          <option value="desc">交易时间从新到旧</option><option value="asc">交易时间从旧到新</option>
        </select>
      </>}
      <input className="input-control" name="fund" aria-label="基金代码" placeholder="基金代码" />
      <button className="btn btn-primary">查询</button>
    </form>}
    {view === "imports" && <div className="card-panel" style={{ padding: 12, marginBottom: 12 }}>
      <fieldset disabled={busy} style={{ display: "flex", flexWrap: "wrap", gap: 8, border: 0, alignItems: "center" }}>
        <select className="input-control" aria-label="PDF 类型" value={sourceType} onChange={event => { setSourceType(event.target.value); setPreview(null); }}>
          <option value="opening_asset">期初资产证明</option><option value="transaction">支付宝交易明细</option>
        </select>
        {sourceType === "opening_asset" && <label>证明截止日 <input className="input-control" type="date" value={snapshot}
          onChange={event => { setSnapshot(event.target.value); setPreview(null); }} /></label>}
        <input type="file" accept="application/pdf,.pdf" aria-label="选择 PDF"
          onChange={event => { setPdf(event.target.files?.[0] ?? null); setPreview(null); }} />
        <button className="btn" disabled={!pdf} onClick={() => importPdf(false)}>预检 PDF</button>
        <button className="btn btn-primary" disabled={!preview} onClick={() => importPdf(true)}>确认导入</button>
        <button className="btn" onClick={rebuild}>全量重算</button>
      </fieldset>
      {preview && <div>
        <p>预检结果 · {String(preview.summary.pages)} 页 · {String(preview.summary.records)} 条 · {String(preview.summary.issues)} 个异常 · {String(preview.summary.duplicate_rows)} 条重复</p>
        {preview.summary.opening_total != null && <p>期初合计 {String(preview.summary.opening_total)} CNY</p>}
        {Object.entries((preview.summary.types ?? {}) as Record<string, number>).map(([kind, count]) => <p key={kind}>{kind} {count} 条</p>)}
        {preview.issues.map((issue, index) => <p key={index}>第 {issue.source_page} 页 · {issue.reason}</p>)}
      </div>}
    </div>}
    {busy && <p role="status">正在处理国内资产…</p>}
    {operationMessage && <p role="status">{operationMessage}</p>}
    {loading && <p role="status">正在读取…</p>}
    {error && <p role="alert">{error}</p>}
    {data && <>
      <p style={{ overflowWrap: "anywhere" }}>原始库 {data.raw_path}<br />计算库 {data.calc_path}</p>
      {data.as_of && <p>持仓日期 {data.as_of}</p>}
      {data.stale && <p>原始数据已更新，当前计算结果尚未发布。</p>}
      <div className="table-container" style={{ overflowX: "auto" }}><table className="finance-table">
        <thead><tr>{data.columns.map(([key, label]) => <th key={key}>{label}</th>)}
          {view === "transactions" && <th>来源</th>}</tr></thead>
        <tbody>{data.rows.map((row, index) => <tr key={String(row.id ?? index)}>
          {data.columns.map(([key]) => <td key={key}>{row[key] ?? "—"}</td>)}
          {view === "transactions" && <td><button className="btn" onClick={() => openSource(String(row.id))}>查看原始记录</button></td>}
        </tr>)}</tbody>
      </table></div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
        <button className="btn" disabled={page <= 1 || loading} onClick={() => setPage(page - 1)}>上一页</button>
        <span>第 {page} 页 · 共 {data.total} 条</span>
        <button className="btn" disabled={page * data.size >= data.total || loading} onClick={() => setPage(page + 1)}>下一页</button>
      </div>
      {data.run && <p>最近计算 {data.run.completed_at} UTC · {data.run.status}</p>}
      {data.checks.length > 0 && <ul>{data.checks.map((row, index) => <li key={index}>{row.check_code} {row.fund_code} {row.detail}</li>)}</ul>}
      {data.issues.length > 0 && <ul>{data.issues.map((row, index) => <li key={index}>{row.source_filename} · 第 {row.source_page} 页 · {row.reason}</li>)}</ul>}
    </>}
    {source && <div className="card-panel" style={{ padding: 12, marginTop: 12 }}>
      <button className="btn" onClick={() => setSource(null)}>关闭原始记录</button>
      <p>{source.source_filename} · 第 {source.source_page} 页 · 表格第 {source.source_row_no} 行</p>
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(JSON.parse(String(source.raw_data)), null, 2)}</pre>
    </div>}
  </section>;
}
