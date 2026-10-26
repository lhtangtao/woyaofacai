import { SystemBootstrap, TradeItem, WatchlistItem, ReportDetailData } from "../types";

function getCsrfToken(): string {
  const match = document.cookie.match(new RegExp("(^| )money_csrf_token=([^;]+)"));
  return match ? match[2] : "";
}

export async function requestApi<T = any>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");

  // Inject CSRF token on mutating requests
  const method = (options.method || "GET").toUpperCase();
  if (["POST", "PUT", "DELETE", "PATCH"].includes(method)) {
    const token = getCsrfToken();
    if (token) {
      headers.set("X-CSRF-Token", token);
    }
  }

  const response = await fetch(endpoint, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    // Remote unauthenticated
    const data = await response.json().catch(() => ({}));
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("money_auth_required"));
    }
    const err: any = new Error(data.message || "需要登录");
    err.status = 401;
    err.loginRequired = true;
    throw err;
  }

  if (response.status === 403) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.message || "无权执行此操作");
  }

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.message || `请求失败 (${response.status})`);
  }

  return response.json();
}

export const api = {
  // Bootstrap & Profile
  bootstrap: () => requestApi<SystemBootstrap>("/api/v1/bootstrap"),

  // Portfolio Overview & KPIs
  overview: (personId?: number | null, currency = "USD") => {
    const query = new URLSearchParams();
    if (personId != null) query.set("person_id", String(personId));
    if (currency) query.set("currency", currency);
    return requestApi(`/api/v1/portfolio/overview?${query.toString()}`);
  },

  // Holdings & Today Intraday PnL
  holdingsToday: (personId?: number | null) => {
    const query = new URLSearchParams();
    if (personId != null) query.set("person_id", String(personId));
    return requestApi(`/api/v1/holdings/today?${query.toString()}`);
  },

  // Trades & Provenance
  trades: (params: {
    page?: number;
    size?: number;
    person_id?: number | null;
    symbol?: string;
    broker?: string;
    side?: string;
    search?: string;
  }) => {
    const query = new URLSearchParams();
    if (params.page) query.set("page", String(params.page));
    if (params.size) query.set("size", String(params.size));
    if (params.person_id != null) query.set("person_id", String(params.person_id));
    if (params.symbol) query.set("symbol", params.symbol);
    if (params.broker) query.set("broker", params.broker);
    if (params.side) query.set("side", params.side);
    if (params.search) query.set("search", params.search);
    return requestApi<{
      status: string;
      trades: TradeItem[];
      items?: TradeItem[];
      columns: string[];
      total?: number;
      pagination: { page: number; size: number; total: number; total_pages: number };
    }>(`/api/v1/trades?${query.toString()}`);
  },

  tradeDetail: (tradeId: number) =>
    requestApi(`/api/v1/trades/detail?trade_id=${tradeId}`),

  // Reports
  reportsList: () => requestApi("/api/v1/reports"),
  reportDetail: (
    reportName: string,
    personId?: number | null,
    search?: string,
    page?: number,
    pageSize?: number,
    sortCol?: string,
    sortDir?: string
  ) => {
    const query = new URLSearchParams();
    if (personId != null) query.set("person_id", String(personId));
    if (search) query.set("search", search);
    if (page != null) query.set("page", String(page));
    if (pageSize != null) query.set("page_size", String(pageSize));
    if (sortCol) query.set("sort_col", sortCol);
    if (sortDir) query.set("sort_dir", sortDir);
    return requestApi<ReportDetailData>(`/api/v1/reports/${encodeURIComponent(reportName)}?${query.toString()}`);
  },

  // Entities Breakdown
  people: () => requestApi("/api/v1/people"),
  brokers: () => requestApi("/api/v1/brokers"),
  ownership: () => requestApi("/api/v1/ownership"),
  earningsCalendar: (year?: number, month?: number) => {
    const query = new URLSearchParams();
    if (year != null) query.set("year", String(year));
    if (month != null) query.set("month", String(month));
    return requestApi(`/api/v1/earnings-calendar?${query.toString()}`);
  },

  // Charts
  charts: (personId?: number | null) => {
    const query = new URLSearchParams();
    if (personId != null) query.set("person_id", String(personId));
    return requestApi(`/api/v1/charts?${query.toString()}`);
  },
  topDays: (personId?: number | null, topN = 20) => {
    const query = new URLSearchParams();
    if (personId != null) query.set("person_id", String(personId));
    query.set("top_n", String(topN));
    return requestApi(`/api/v1/charts/top_days?${query.toString()}`);
  },
  topMonths: (personId?: number | null, topN = 20) => {
    const query = new URLSearchParams();
    if (personId != null) query.set("person_id", String(personId));
    query.set("top_n", String(topN));
    return requestApi(`/api/v1/charts/top_months?${query.toString()}`);
  },
  ranking: (personId?: number | null) => {
    const query = new URLSearchParams();
    if (personId != null) query.set("person_id", String(personId));
    return requestApi(`/api/v1/charts/ranking?${query.toString()}`);
  },

  // Market Monitoring & Alert Rules
  monitoringWatchlist: () =>
    requestApi<{ status: string; items: WatchlistItem[]; count: number }>("/api/v1/monitoring/watchlist"),
  addWatchlist: (symbol: string) =>
    requestApi("/api/v1/monitoring/watchlist/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol }),
    }),
  deleteWatchlist: (symbol: string) =>
    requestApi("/api/v1/monitoring/watchlist/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol }),
    }),
  monitoringRules: () => requestApi("/api/v1/monitoring/rules"),
  toggleRule: (ruleId: string, enabled: boolean) =>
    requestApi(`/api/v1/monitoring/rules/${ruleId}/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }),
  monitoringAlerts: () => requestApi("/api/v1/monitoring/alerts"),
  monitoringKline: (symbol: string) =>
    requestApi(`/api/v1/monitoring/kline?symbol=${encodeURIComponent(symbol)}`),

  // AI Assistant
  aiChatHistory: () => requestApi("/api/v1/ai/history"),
  aiChat: (message: string, personId?: number | null) =>
    requestApi("/api/v1/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: message, message, person_id: personId }),
    }),
  aiClear: () => requestApi("/api/v1/ai/clear", { method: "POST" }),
  aiStockHistory: (symbol?: string) =>
    requestApi(symbol ? `/api/v1/ai/stock/history?symbol=${encodeURIComponent(symbol)}` : "/api/v1/ai/stock/history"),
  aiStockChat: (symbol: string, message: string) =>
    requestApi("/api/v1/ai/stock/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol, prompt: message, message }),
    }),

  // System Diagnostics
  systemDbCheck: () => requestApi("/api/v1/system/database"),
  systemProgress: () => requestApi("/api/v1/system/progress"),
  systemMetrics: () => requestApi("/api/v1/system/metrics"),
  systemReconcile: () => requestApi("/api/v1/system/reconcile", { method: "POST" }),

  // Auth
  login: (username: string, password: string) =>
    requestApi("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    }),
  logout: () => requestApi("/api/auth/logout", { method: "POST" }),
  getUsers: () => requestApi("/api/auth/users"),
  createUser: (data: any) =>
    requestApi("/api/auth/users/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  updatePassword: (data: any) =>
    requestApi("/api/auth/users/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  barkConfig: () => requestApi("/api/bark/config"),
  saveBarkConfig: (url: string) =>
    requestApi("/api/bark/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }),
  testBark: (url?: string) =>
    requestApi("/api/bark/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }),
};
