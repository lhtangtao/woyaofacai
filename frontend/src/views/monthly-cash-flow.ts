import { api } from "../api/client";

export const CASH_COLUMNS = [
  "月份", "券商渠道", "股票分类", "Ticker", "月初持仓数量", "本月买入数量", "本月卖出数量",
  "每月净变化", "月末持仓数量", "本月买入金额（USD）", "本月卖出金额（USD）", "本月净投入（USD）", "月末累计净投入（USD）",
];

export const NET_QUANTITY_COLUMN = 7;

export type ReportEntry = { values: any[]; meta: Record<string, any> };

export async function loadMonthlyReport(name: string, person: number | null, signal: AbortSignal): Promise<ReportEntry[]> {
  const entries: ReportEntry[] = [];
  for (let page = 1; ; page++) {
    const result = await api.reportDetail(name, person, undefined, page, 1000, undefined, undefined, signal);
    if (result.status !== "ok") throw new Error(result.message || "月度报表读取失败");
    entries.push(...result.rows.map((values, i) => ({ values, meta: result.items?.[i]?._meta || {} })));
    if (page >= (result.total_pages || 1)) return entries;
  }
}

function positionMonth({ values, meta }: ReportEntry) {
  if (meta.account_id == null || meta.instrument_id == null) return null;
  return JSON.stringify([values[0], meta.account_id, meta.instrument_id]);
}

export function cashFlowRows(net: ReportEntry[], monthly: ReportEntry[]): ReportEntry[] {
  const quantities = new Map<string, any[] | null>();
  for (const row of monthly) {
    const key = positionMonth(row);
    if (key) quantities.set(key, quantities.has(key) ? null : row.values.slice(6, 8));
  }
  return net.map(row => {
    const key = positionMonth(row);
    const [buy, sell] = (key && quantities.get(key)) || [null, null];
    const change = buy == null || sell == null ? null : Number(scaled(buy) - scaled(sell)) / 100000000;
    return { ...row, values: [...row.values.slice(0, 5), buy, sell, change, ...row.values.slice(5)] };
  });
}

function scaled(value: number | string) {
  return BigInt(Number(value).toFixed(8).replace(".", ""));
}

export function sumReportColumn(rows: ReportEntry[], column: number): number | null {
  let total = 0n;
  if (!rows.length) return null;
  for (const { values } of rows) {
    const value = values[column];
    if (value == null || !Number.isFinite(Number(value))) return null;
    total += scaled(value);
  }
  return Number(total) / 100000000;
}

export function exportMonthlyCsv(name: string, columns: string[], rows: any[][]) {
  const csvCell = (value: any) => '"' + String(value ?? "").replace(/"/g, '""') + '"';
  const csv = [columns, ...rows].map(row => row.map(csvCell).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name + ".csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
