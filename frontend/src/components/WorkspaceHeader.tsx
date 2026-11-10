import React from "react";
import {
  RefreshCw,
  RotateCcw,
  Download,
  User,
  Sun,
  Moon,
} from "lucide-react";
import { PersonItem, UserProfile } from "../types";

interface WorkspaceHeaderProps {
  title: string;
  subtitle?: string;
  onRefresh: () => void;
  onResetFilters?: () => void;
  onExport?: () => void;
  user: UserProfile | null;
  persons: PersonItem[];
  currentPersonId: number | null;
  hidePersonSelector?: boolean;
  onPersonChange: (personId: number | null) => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  extraActions?: React.ReactNode;
}

export const WorkspaceHeader: React.FC<WorkspaceHeaderProps> = ({
  title,
  subtitle,
  onRefresh,
  onResetFilters,
  onExport,
  user,
  hidePersonSelector = false,
  persons,
  currentPersonId,
  onPersonChange,
  theme,
  onToggleTheme,
  extraActions,
}) => {
  // Determine if person selector is disabled (e.g. regular isolated user)
  const isIsolatedUser = user?.role === "user" && user?.person_id != null;

  return (
    <header
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        paddingBottom: "8px",
        borderBottom: "1px solid var(--border-color)",
      }}
    >
      {/* Top Bar: Title & Primary Actions */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "16px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <div>
            <h1
              style={{
                fontSize: "22px",
                fontWeight: 700,
                color: "var(--text-heading)",
                lineHeight: "1.2",
              }}
            >
              {title}
            </h1>
            {subtitle && (
              <p
                style={{
                  fontSize: "12px",
                  color: "var(--text-muted)",
                  marginTop: "2px",
                }}
              >
                {subtitle}
              </p>
            )}
          </div>
        </div>

        {/* Actions Right */}
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          {extraActions}
          {/* Person Selector */}
          {!hidePersonSelector && <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
            <User size={14} color="var(--text-muted)" />
            <select
              value={currentPersonId == null ? "all" : String(currentPersonId)}
              onChange={(e) => {
                const val = e.target.value;
                onPersonChange(val === "all" ? null : Number(val));
              }}
              disabled={isIsolatedUser}
              className="input-control"
              style={{
                fontSize: "12px",
                padding: "4px 8px",
                cursor: isIsolatedUser ? "not-allowed" : "pointer",
                background: isIsolatedUser ? "var(--hover-bg)" : "var(--bg-panel)",
              }}
            >
              {!isIsolatedUser && <option value="all">全量账本</option>}
              {persons.map((p) => {
                // If isolated, only show allowed person
                if (isIsolatedUser && p.id !== user?.person_id) return null;
                return (
                  <option key={p.id} value={String(p.id)}>
                    {p.name}
                  </option>
                );
              })}
            </select>
          </div>}

          {onResetFilters && (
            <button onClick={onResetFilters} className="btn"
              title={hidePersonSelector ? "仅重置本页面筛选" : "恢复个人 · 全部渠道 · 全部股票及默认日期"} style={{ height: "32px" }}>
              <RotateCcw size={14} />
              <span>重置筛选</span>
            </button>
          )}

          {/* Theme Switcher */}
          <button
            onClick={onToggleTheme}
            className="btn btn-quiet"
            style={{ width: "32px", height: "32px", padding: 0 }}
            title={theme === "light" ? "切换深色模式" : "切换浅色模式"}
          >
            {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
          </button>

          {/* Refresh */}
          <button
            onClick={onRefresh}
            className="btn"
            title="刷新当前数据 (⌘R)"
            style={{ height: "32px" }}
          >
            <RefreshCw size={14} />
            <span>刷新</span>
          </button>

          {/* Export */}
          {onExport && (
            <button
              onClick={onExport}
              className="btn btn-primary"
              style={{ height: "32px" }}
              title="导出当前表格数据"
            >
              <Download size={14} />
              <span>导出</span>
            </button>
          )}
        </div>
      </div>

      {/* Account identity and access scope */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: "11px",
          color: "var(--text-muted)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span>当前身份: {user?.person_name || user?.username || "访客"}</span>
          {isIsolatedUser && (
            <span
              style={{
                background: "var(--brand-selected)",
                color: "var(--brand)",
                padding: "1px 6px",
                borderRadius: "4px",
                fontWeight: 600,
              }}
            >
              单人隔离: {user?.person_name}
            </span>
          )}
        </div>
      </div>
    </header>
  );
};
