import React from "react";
import {
  LayoutGrid,
  TrendingUp,
  Wallet,
  Table,
  Users,
  Building2,
  PieChart,
  Calendar,
  ArrowLeftRight,
  Bot,
  Activity,
  BarChart3,
  Database,
  RefreshCw,
  Sliders,
  ChevronLeft,
  ChevronRight,
  ShieldCheck,
} from "lucide-react";

export interface NavEntry {
  id: string;
  label: string;
  icon: React.ReactNode;
  badge?: string;
}

export interface NavGroup {
  title: string;
  entries: NavEntry[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    title: "资产管理",
    entries: [
      { id: "overview", label: "资产总览", icon: <LayoutGrid size={16} /> },
      { id: "reports:今日盈亏", label: "今日盈亏", icon: <TrendingUp size={16} /> },
      { id: "holdings", label: "持仓管理", icon: <Wallet size={16} /> },
      { id: "ownership", label: "股票归属", icon: <PieChart size={16} /> },
      { id: "earnings", label: "持仓财报日历", icon: <Calendar size={16} /> },
    ],
  },
  {
    title: "交易与收益",
    entries: [
      { id: "trades", label: "交易明细", icon: <ArrowLeftRight size={16} /> },
      { id: "reports:每日持仓收益", label: "每日持仓收益", icon: <Calendar size={16} /> },
      { id: "reports:每月持仓收益", label: "每月持仓收益", icon: <TrendingUp size={16} /> },
      { id: "reports:每月微操收益", label: "每月微操收益", icon: <ArrowLeftRight size={16} /> },
      { id: "reports:清仓收益", label: "清仓收益", icon: <Wallet size={16} /> },
      { id: "reports:月度净投入明细", label: "月度净投入", icon: <TrendingUp size={16} /> },
    ],
  },
  {
    title: "AI 助手",
    entries: [
      { id: "ai_chat", label: "AI 投资问答", icon: <Bot size={16} /> },
      { id: "monitor", label: "行情监控中心", icon: <Activity size={16} /> },
      { id: "ai_stock", label: "AI 股票分析", icon: <TrendingUp size={16} /> },
    ],
  },
  {
    title: "图表分析",
    entries: [
      { id: "charts", label: "资产走势图", icon: <BarChart3 size={16} /> },
      { id: "charts:monthly", label: "月度走势详情", icon: <BarChart3 size={16} /> },
      { id: "charts:distribution", label: "持仓分布", icon: <LayoutGrid size={16} /> },
      { id: "charts:gallery", label: "走势图 (27图)", icon: <BarChart3 size={16} /> },
      { id: "top_days", label: "Top日汇总", icon: <BarChart3 size={16} /> },
      { id: "top_months", label: "Top月汇总", icon: <BarChart3 size={16} /> },
      { id: "ranking", label: "收益排行榜", icon: <BarChart3 size={16} /> },
      { id: "reports:交易回放", label: "历史资产回放", icon: <Calendar size={16} /> },
      { id: "reports:分类汇总", label: "证券详情", icon: <BarChart3 size={16} /> },
    ],
  },
  {
    title: "系统管理",
    entries: [
      { id: "system:db", label: "数据源检查", icon: <Database size={16} /> },
      { id: "reports:list", label: "数据中心 (28报表)", icon: <Database size={16} /> },
      { id: "system:reconcile", label: "Excel 对账", icon: <Table size={16} /> },
      { id: "system:metrics", label: "指标说明", icon: <Table size={16} /> },
      { id: "system:sync", label: "数据同步 · 未开放", icon: <RefreshCw size={16} />, badge: "只读" },
      { id: "system:progress", label: "开发进度", icon: <Table size={16} /> },
      { id: "settings", label: "设置", icon: <Sliders size={16} /> },
    ],
  },
];

interface SidebarProps {
  currentRoute: string;
  onRouteChange: (route: string) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentRoute,
  onRouteChange,
  collapsed,
  onToggleCollapse,
}) => {
  return (
    <aside
      style={{
        width: collapsed ? "64px" : "212px",
        minWidth: collapsed ? "64px" : "212px",
        transition: "width 0.2s ease, min-width 0.2s ease",
        background: "var(--bg-sidebar)",
        borderRight: "1px solid var(--border-color)",
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        padding: "20px 0 16px",
        userSelect: "none",
        zIndex: 50,
      }}
    >
      {/* Brand & Subtitle */}
      <div
        style={{
          padding: collapsed ? "0 14px 16px" : "0 20px 16px",
          textAlign: collapsed ? "center" : "left",
          borderBottom: "1px solid var(--border-color)",
          marginBottom: "8px",
        }}
      >
        <div
          style={{
            fontSize: collapsed ? "14px" : "18px",
            fontWeight: 700,
            color: "var(--text-heading)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            letterSpacing: "-0.3px",
          }}
        >
          {collapsed ? "发" : "我要发财"}
        </div>
        {!collapsed && (
          <div
            style={{
              fontSize: "12px",
              color: "var(--text-muted)",
              marginTop: "2px",
            }}
          >
            投资资产管理
          </div>
        )}
      </div>

      {/* Nav List */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          overflowX: "hidden",
          padding: "0 6px",
        }}
      >
        {NAV_GROUPS.map((group) => (
          <div key={group.title} style={{ marginBottom: "12px" }}>
            {!collapsed && (
              <div
                style={{
                  fontSize: "11px",
                  fontWeight: 600,
                  color: "var(--text-subtle)",
                  padding: "8px 12px 4px",
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                }}
              >
                {group.title}
              </div>
            )}
            {group.entries.map((entry) => {
              const active = currentRoute === entry.id;
              return (
                <button
                  key={entry.id}
                  onClick={() => onRouteChange(entry.id)}
                  title={entry.label}
                  style={{
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    gap: "10px",
                    padding: collapsed ? "8px 0" : "7px 12px",
                    justifyContent: collapsed ? "center" : "flex-start",
                    borderRadius: "8px",
                    border: "none",
                    background: active ? "var(--brand-selected)" : "transparent",
                    color: active ? "var(--brand)" : "var(--text-main)",
                    fontWeight: active ? 600 : 500,
                    fontSize: "13px",
                    cursor: "pointer",
                    margin: "1px 0",
                    transition: "all 0.15s ease",
                    textAlign: "left",
                  }}
                  onMouseEnter={(e) => {
                    if (!active) e.currentTarget.style.background = "var(--hover-bg)";
                  }}
                  onMouseLeave={(e) => {
                    if (!active) e.currentTarget.style.background = "transparent";
                  }}
                >
                  <span style={{ display: "inline-flex", flexShrink: 0 }}>
                    {entry.icon}
                  </span>
                  {!collapsed && (
                    <span
                      style={{
                        flex: 1,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {entry.label}
                    </span>
                  )}
                  {!collapsed && entry.badge && (
                    <span
                      style={{
                        fontSize: "10px",
                        padding: "1px 5px",
                        borderRadius: "4px",
                        background: "var(--border-color)",
                        color: "var(--text-muted)",
                      }}
                    >
                      {entry.badge}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {/* Collapse Toggle & Footer */}
      <div
        style={{
          padding: collapsed ? "8px 10px" : "8px 16px",
          borderTop: "1px solid var(--border-color)",
          display: "flex",
          flexDirection: "column",
          gap: "8px",
        }}
      >
        <button
          onClick={onToggleCollapse}
          className="btn btn-quiet"
          style={{
            width: "100%",
            justifyContent: collapsed ? "center" : "flex-start",
            padding: "6px 8px",
            fontSize: "12px",
          }}
          title={collapsed ? "展开导航" : "收起导航"}
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          {!collapsed && <span>收起导航</span>}
        </button>

        {!collapsed && (
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              display: "flex",
              alignItems: "center",
              gap: "6px",
              paddingLeft: "4px",
            }}
          >
            <ShieldCheck size={14} color="#16A34A" />
            <span>本地工作区 · 源库只读</span>
          </div>
        )}
      </div>
    </aside>
  );
};
