import React, { useEffect, useState } from "react";
import { Sidebar, NAV_GROUPS } from "./components/Sidebar";
import { WorkspaceHeader } from "./components/WorkspaceHeader";
import { DashboardView } from "./views/DashboardView";
import { TradesView } from "./views/TradesView";
import { ReportsView } from "./views/ReportsView";
import { MonitorView } from "./views/MonitorView";
import { HistoricalTrendView } from "./views/HistoricalTrendView";
import { ChartsView } from "./views/ChartsView";
import { EntitiesView } from "./views/EntitiesView";
import { EarningsView } from "./views/EarningsView";
import { AiViews } from "./views/AiViews";
import { SystemViews } from "./views/SystemViews";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { api } from "./api/client";
import { SystemBootstrap, UserProfile, PersonItem, ReportMeta } from "./types";

export const App: React.FC = () => {
  const [bootstrap, setBootstrap] = useState<SystemBootstrap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Navigation & History State
  const [history, setHistory] = useState<string[]>(["overview"]);
  const [historyIndex, setHistoryIndex] = useState(0);
  const currentRoute = history[historyIndex] || "overview";

  // Person Filter State
  const [selectedPersonId, setSelectedPersonId] = useState<number | null>(null);
  const [assetResetKey, setAssetResetKey] = useState(0);

  // Layout & Theme State
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [greenProfit, setGreenProfit] = useState(true);

  // Refresh trigger counter
  const [refreshKey, setRefreshKey] = useState(0);

  // Login Modal State
  const [showLogin, setShowLogin] = useState(false);
  const [loginUsername, setLoginUsername] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState("");

  const loadBootstrap = () => {
    setLoading(true);
    api
      .bootstrap()
      .then((data) => {
        if (data.status === "ok") {
          setBootstrap(data);
          setError(null);
          // If user has isolated person_id, initialize with it
          if (data.user?.person_id != null) {
            setSelectedPersonId(data.user.person_id);
          } else {
            // 打开的时候默认展示「个人」
            const geren = (data.persons || []).find((p) => p.name === "个人");
            if (geren) {
              setSelectedPersonId(geren.id);
            }
          }
        } else {
          setError("初始化服务失败");
        }
      })
      .catch((err: any) => {
        if (err.status === 401 || err.loginRequired || err.message?.includes("登录")) {
          setShowLogin(true);
        } else {
          setError(err.message || "请求后端 API 异常");
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadBootstrap();
  }, [refreshKey]);

  useEffect(() => {
    const onAuthRequired = () => setShowLogin(true);
    window.addEventListener("money_auth_required", onAuthRequired);
    return () => window.removeEventListener("money_auth_required", onAuthRequired);
  }, []);

  // Apply theme to HTML root
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  // Apply green/red profit rule to HTML root
  useEffect(() => {
    document.documentElement.setAttribute("data-profit-color", greenProfit ? "intl" : "cn");
  }, [greenProfit]);

  // Navigation handlers
  const navigate = (newRoute: string) => {
    if (newRoute === currentRoute) return;
    const newHistory = history.slice(0, historyIndex + 1);
    newHistory.push(newRoute);
    setHistory(newHistory);
    setHistoryIndex(newHistory.length - 1);
  };

  const goBack = () => {
    if (historyIndex > 0) {
      setHistoryIndex(historyIndex - 1);
    }
  };

  const goForward = () => {
    if (historyIndex < history.length - 1) {
      setHistoryIndex(historyIndex + 1);
    }
  };

  const handleRefresh = () => {
    setRefreshKey((k) => k + 1);
  };

  const assetRoute = NAV_GROUPS[0].entries.some((entry) => entry.id === currentRoute);
  const resetAssetFilters = () => {
    const isolatedId = bootstrap?.user?.role === "user" ? bootstrap.user.person_id : null;
    const personalId = bootstrap?.persons?.find((person) => person.name === "个人")?.id ?? null;
    setSelectedPersonId(isolatedId ?? personalId);
    setAssetResetKey((key) => key + 1);
  };

  // Keyboard Shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // ⌘R or Ctrl+R -> Refresh
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "r") {
        e.preventDefault();
        handleRefresh();
      }
      // ⌥← -> Back
      if (e.altKey && e.key === "ArrowLeft") {
        e.preventDefault();
        goBack();
      }
      // ⌥→ -> Forward
      if (e.altKey && e.key === "ArrowRight") {
        e.preventDefault();
        goForward();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [historyIndex, history]);

  const handleLoginSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoginError("");
    try {
      const res = await api.login(loginUsername, loginPassword);
      if (res.status === "ok") {
        setShowLogin(false);
        handleRefresh();
      } else {
        setLoginError(res.message || "登录失败");
      }
    } catch (err: any) {
      setLoginError(err.message || "登录请求异常");
    }
  };

  if (loading && !bootstrap) {
    return (
      <div
        style={{
          display: "flex",
          height: "100vh",
          width: "100vw",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--bg-app)",
          color: "var(--brand)",
          fontSize: "14px",
          fontWeight: 600,
        }}
      >
        <span>正在启动 Money Portfolio 安全工作区...</span>
      </div>
    );
  }

  // Get Page Title & Subtitle based on Route
  const getHeaderMeta = () => {
    if (currentRoute === "overview") return { title: "资产总览", subtitle: "全时期投资组合资产、净投入与全周期盈亏看板" };
    if (currentRoute === "trades") return { title: "交易明细", subtitle: "查看每笔交易，追溯原始记录与人员归属" };
    if (currentRoute.startsWith("reports:")) {
      const repName = currentRoute.replace("reports:", "");
      return { title: repName, subtitle: `专业量化分析报表 · ${repName}` };
    }
    if (currentRoute === "people") return { title: "人员资产", subtitle: "各出资人权益账本穿透与份额占比" };
    if (currentRoute === "brokers") return { title: "券商资产", subtitle: "券商渠道真实持仓与现金分布汇总" };
    if (currentRoute === "ownership") return { title: "股票归属", subtitle: "底层持仓标的股权归属与穿透" };
    if (currentRoute === "earnings") return { title: "持仓财报日历", subtitle: "追踪持仓标的财报披露时间与 EPS 预期" };
    if (currentRoute === "monitor") return { title: "行情监控中心", subtitle: "实时行情、多周期均线系统与 Bark 预警规则" };
    if (currentRoute === "ai_chat") return { title: "AI 投资问答", subtitle: "基于账本真实财务数据的智能分析顾问" };
    if (currentRoute === "ai_stock") return { title: "AI 股票分析", subtitle: "单标的深度技术面与基本面研报" };
    if (currentRoute === "charts") return { title: "资产走势图", subtitle: "全时期资产净值增长曲线" };
    if (currentRoute === "charts:monthly") return { title: "月度走势详情", subtitle: "月度真实盈亏与现金流动" };
    if (currentRoute === "charts:distribution") return { title: "持仓分布", subtitle: "标的分类与资产占比" };
    if (currentRoute === "charts:gallery") return { title: "走势图 (27图)", subtitle: "27 维全景观定量化走势图库" };
    if (currentRoute === "top_days") return { title: "Top日汇总", subtitle: "全时期最佳盈利日与最大亏损日左右对比" };
    if (currentRoute === "top_months") return { title: "Top月汇总", subtitle: "全时期最佳与最差月份汇总" };
    if (currentRoute === "ranking") return { title: "收益排行榜", subtitle: "持仓与清仓标的累计实现收益榜单" };
    if (currentRoute === "system:db") return { title: "数据源检查", subtitle: "本地 SQLite 数据库物理状态只读检查" };
    if (currentRoute === "system:reconcile") return { title: "Excel 对账", subtitle: "核对 Excel 交易流水与数据库引擎对账状态" };
    if (currentRoute === "system:metrics") return { title: "指标说明", subtitle: "18 项财务指标的标准计算公式与定义字典" };
    if (currentRoute === "system:sync") return { title: "数据同步 · 未开放", subtitle: "云端同步与多端备份功能预览" };
    if (currentRoute === "system:progress") return { title: "开发进度", subtitle: "系统演进时间线与工程提交记录" };
    if (currentRoute === "settings") return { title: "设置", subtitle: "首选项、红绿涨跌配置与 Bark 推送管理" };
    return { title: "工作区", subtitle: "" };
  };

  const headerMeta = getHeaderMeta();

  return (
    <div className="app-container">
      {/* Native-identical Sidebar */}
      <Sidebar
        currentRoute={currentRoute}
        onRouteChange={navigate}
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
      />

      {/* Main Workspace Frame */}
      <main className="workspace-container">
        {/* Workspace Header */}
        <WorkspaceHeader
          title={headerMeta.title}
          subtitle={headerMeta.subtitle}
          canGoBack={historyIndex > 0}
          canGoForward={historyIndex < history.length - 1}
          onGoBack={goBack}
          onGoForward={goForward}
          onRefresh={handleRefresh}
          onResetFilters={assetRoute ? resetAssetFilters : undefined}
          dbName={bootstrap?.database?.name || "money.sqlite3"}
          user={bootstrap?.user || null}
          persons={bootstrap?.persons || []}
          currentPersonId={selectedPersonId}
          onPersonChange={setSelectedPersonId}
          theme={theme}
          onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
        />

        {/* Dynamic Route Body */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
          <ErrorBoundary fallbackTitle="页面视图渲染异常">
            {currentRoute === "overview" && <DashboardView key={assetResetKey} personId={selectedPersonId} />}

            {currentRoute === "trades" && (
              <TradesView
                personId={selectedPersonId}
                persons={bootstrap?.persons || []}
                accounts={bootstrap?.accounts || []}
              />
            )}

            {currentRoute.startsWith("reports:") && (
              <ReportsView
                key={`${currentRoute}:${assetResetKey}`}
                initialReportName={currentRoute.replace("reports:", "")}
                personId={selectedPersonId}
                reportsList={bootstrap?.reports || []}
              />
            )}

            {currentRoute === "monitor" && <MonitorView />}

            {currentRoute === "people" && <EntitiesView key={`people:${assetResetKey}`} type="people" personId={selectedPersonId} />}
            {currentRoute === "brokers" && <EntitiesView key={`brokers:${assetResetKey}`} type="brokers" personId={selectedPersonId} />}
            {currentRoute === "ownership" && <EntitiesView key={`ownership:${assetResetKey}`} type="ownership" personId={selectedPersonId} />}

            {currentRoute === "earnings" && <EarningsView key={assetResetKey} />}

            {currentRoute === "ai_chat" && <AiViews mode="chat" personId={selectedPersonId} />}
            {currentRoute === "ai_stock" && <AiViews mode="stock" personId={selectedPersonId} />}

            {currentRoute === "charts" && <ChartsView mode="asset" personId={selectedPersonId} />}
            {currentRoute === "charts:monthly" && <HistoricalTrendView personId={selectedPersonId} />}
            {currentRoute === "charts:distribution" && <ChartsView mode="distribution" personId={selectedPersonId} />}
            {currentRoute === "charts:gallery" && <ChartsView mode="gallery" personId={selectedPersonId} />}
            {currentRoute === "top_days" && <ChartsView mode="top_days" personId={selectedPersonId} />}
            {currentRoute === "top_months" && <ChartsView mode="top_months" personId={selectedPersonId} />}
            {currentRoute === "ranking" && <ChartsView mode="ranking" personId={selectedPersonId} />}

            {currentRoute === "system:db" && (
              <SystemViews
                mode="db"
                user={bootstrap?.user || null}
                theme={theme}
                onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
                greenProfit={greenProfit}
                onToggleGreenProfit={() => setGreenProfit(!greenProfit)}
              />
            )}
            {currentRoute === "system:reconcile" && (
              <SystemViews
                mode="reconcile"
                user={bootstrap?.user || null}
                theme={theme}
                onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
                greenProfit={greenProfit}
                onToggleGreenProfit={() => setGreenProfit(!greenProfit)}
              />
            )}
            {currentRoute === "system:metrics" && (
              <SystemViews
                mode="metrics"
                user={bootstrap?.user || null}
                theme={theme}
                onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
                greenProfit={greenProfit}
                onToggleGreenProfit={() => setGreenProfit(!greenProfit)}
              />
            )}
            {currentRoute === "system:progress" && (
              <SystemViews
                mode="progress"
                user={bootstrap?.user || null}
                theme={theme}
                onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
                greenProfit={greenProfit}
                onToggleGreenProfit={() => setGreenProfit(!greenProfit)}
              />
            )}
            {currentRoute === "settings" && (
              <SystemViews
                mode="settings"
                user={bootstrap?.user || null}
                theme={theme}
                onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
                greenProfit={greenProfit}
                onToggleGreenProfit={() => setGreenProfit(!greenProfit)}
              />
            )}
          </ErrorBoundary>
        </div>
      </main>

      {/* Login Modal for Remote Access */}
      {showLogin && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
        >
          <div className="card-panel" style={{ width: "360px", padding: "24px" }}>
            <h2 style={{ fontSize: "18px", fontWeight: 700, marginBottom: "8px" }}>账号登录</h2>
            <p style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "16px" }}>
              外出或公网访问时，请输入账户密码进行身份鉴权。
            </p>

            <form onSubmit={handleLoginSubmit} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <div>
                <label style={{ fontSize: "12px", fontWeight: 600, display: "block", marginBottom: "4px" }}>用户名</label>
                <input
                  type="text"
                  value={loginUsername}
                  onChange={(e) => setLoginUsername(e.target.value)}
                  className="input-control"
                  style={{ width: "100%" }}
                  required
                />
              </div>

              <div>
                <label style={{ fontSize: "12px", fontWeight: 600, display: "block", marginBottom: "4px" }}>密码</label>
                <input
                  type="password"
                  value={loginPassword}
                  onChange={(e) => setLoginPassword(e.target.value)}
                  className="input-control"
                  style={{ width: "100%" }}
                  required
                />
              </div>

              {loginError && (
                <div style={{ color: "var(--loss-color)", fontSize: "12px" }}>{loginError}</div>
              )}

              <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
                <button type="submit" className="btn btn-primary" style={{ flex: 1 }}>
                  登录
                </button>
                <button type="button" onClick={() => setShowLogin(false)} className="btn">
                  取消
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
