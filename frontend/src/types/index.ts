export interface UserProfile {
  can_view_ownership?: boolean;
  access_mode: string;
  principal_type: string;
  user_id: number | null;
  username: string | null;
  role: "admin" | "user" | "lan_viewer" | "unauthenticated";
  person_id: number | null;
  person_name: string | null;
  is_authenticated: boolean;
  is_lan: boolean;
  login_required: boolean;
  can_admin: boolean;
  can_write: boolean;
  can_chat: boolean;
  capabilities: string[];
}

export interface PersonItem {
  id: number;
  name: string;
}

export interface ReportMeta {
  name: string;
  group: string;
  description: string;
}

export interface SystemBootstrap {
  status: string;
  user: UserProfile;
  access_mode: string;
  persons: PersonItem[];
  people: PersonItem[];
  accounts: string[];
  reports: ReportMeta[];
  database: {
    name: string;
    path: string;
    exists: boolean;
    size_bytes: number;
    record_count: number;
    as_of: string;
  };
  system: {
    version: string;
    platform: string;
    server_time: string;
    default_currency: string;
    metric_mode: string;
  };
  preferences: {
    theme: string;
    green_profit: boolean;
    bark_configured: boolean;
  };
  design_tokens: {
    row_height: number;
    header_height: number;
    card_radius: number;
    control_radius: number;
  };
}

export interface TradeItem {
  id: number;
  record_id: number;
  trade_date: string;
  broker: string;
  category: string;
  symbol: string;
  side: string;
  current_price?: number | null;
  quantity: number;
  price: number;
  amount: number;
  tag?: string | null;
  post_quantity?: number | null;
  post_net?: number | null;
  post_cost?: number | null;
  kind?: string;
  person_name?: string;
  order_id?: string;
  source?: string;
}

export interface TradeProvenance {
  trade_id: number;
  trade_date: string;
  symbol: string;
  side: string;
  current_price?: number | null;
  quantity: number;
  price: number;
  amount: number;
  broker: string;
  order_id: string;
  source_file: string;
  source_sheet: string;
  source_row: number;
  source_batch_id: string;
  tag: string;
  post_quantity: number;
  post_cost: number;
  post_net: number;
  person_splits: Array<{ person: string; ratio: number; amount: number }>;
}

export interface WatchlistItem {
  symbol: string;
  full_symbol: string;
  price: string;
  change: string;
  rsi: string;
  trend_short: string;
  trend_medium: string;
  trend_long: string;
  cross: string;
  ma5: string | null;
  ma10: string | null;
  ma20: string | null;
  ma50: string | null;
  ma200: string | null;
  day: string;
}

export interface ReportDetailData {
  status: string;
  name?: string;
  report_name?: string;
  message?: string;
  columns: string[];
  rows: any[];
  items?: Record<string, any>[];
  summary_row?: any[] | null;
  totals?: Record<string, any>;
  types?: string[];
  total_rows?: number;
  total?: number;
  page?: number;
  page_size?: number;
  total_pages?: number;
  note?: string;
}
