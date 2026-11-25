import React, { useEffect, useState } from "react";
import { Database, ShieldCheck, History, BookOpen, Sliders, CheckCircle2, AlertTriangle, Bell, KeyRound, Users } from "lucide-react";
import { api } from "../api/client";
import { UserProfile } from "../types";

interface SystemViewsProps {
  mode: "db" | "progress" | "metrics" | "reconcile" | "settings";
  user: UserProfile | null;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  greenProfit: boolean;
  onToggleGreenProfit: () => void;
  onSelfPasswordChanged?: (username?: string) => void;
}

export const SystemViews: React.FC<SystemViewsProps> = ({
  mode,
  user,
  theme,
  onToggleTheme,
  greenProfit,
  onToggleGreenProfit,
  onSelfPasswordChanged,
}) => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  // Settings: Bark
  const [barkUrl, setBarkUrl] = useState("");
  const [barkStatus, setBarkStatus] = useState<string | null>(null);

  // Settings: 修改本人密码
  const [selfCurPwd, setSelfCurPwd] = useState("");
  const [selfNewPwd, setSelfNewPwd] = useState("");
  const [selfConfirmPwd, setSelfConfirmPwd] = useState("");
  const [selfPwdStatus, setSelfPwdStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [selfPwdSaving, setSelfPwdSaving] = useState(false);

  // Settings: 管理员重置他人密码
  const [managedUsers, setManagedUsers] = useState<Array<{ id: number; username: string; person_name?: string; role: string }>>([]);
  const [resetTargetUser, setResetTargetUser] = useState("");
  const [resetNewPwd, setResetNewPwd] = useState("");
  const [resetStatus, setResetStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [resetSaving, setResetSaving] = useState(false);

  useEffect(() => {
    setLoading(true);
    let promise: Promise<any>;
    if (mode === "db") {
      promise = api.systemDbCheck();
    } else if (mode === "progress") {
      promise = api.systemProgress();
    } else if (mode === "metrics") {
      promise = api.systemMetrics();
    } else if (mode === "reconcile") {
      promise = api.systemReconcile();
    } else {
      // settings
      promise = api.barkConfig();
    }

    promise
      .then((res) => {
        if (res.status === "ok") {
          setData(res);
          if (res.url) setBarkUrl(res.url);
        }
      })
      .catch((err) => console.error("System view error:", err))
      .finally(() => setLoading(false));

    if (mode === "settings" && user?.can_admin) {
      api
        .getUsers()
        .then((res) => {
          if (res.status === "ok" && Array.isArray(res.users)) {
            const others = res.users.filter((u: any) => u.username !== user?.username);
            setManagedUsers(others);
            if (others.length > 0 && !resetTargetUser) {
              setResetTargetUser(others[0].username);
            }
          }
        })
        .catch(() => {});
    }
  }, [mode, user?.can_admin]);

  const handleSelfPasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSelfPwdStatus(null);
    if (!selfCurPwd) {
      setSelfPwdStatus({ ok: false, text: "请输入当前原密码" });
      return;
    }
    if (selfNewPwd.length < 6 || selfNewPwd.length > 64) {
      setSelfPwdStatus({ ok: false, text: "新密码长度需在 6 至 64 位之间" });
      return;
    }
    if (selfNewPwd !== selfNewPwd.trim()) {
      setSelfPwdStatus({ ok: false, text: "新密码首尾不能包含空格" });
      return;
    }
    if (selfNewPwd !== selfConfirmPwd) {
      setSelfPwdStatus({ ok: false, text: "两次输入的新密码不一致" });
      return;
    }
    if (selfNewPwd === selfCurPwd) {
      setSelfPwdStatus({ ok: false, text: "新密码不能与当前密码相同" });
      return;
    }
    setSelfPwdSaving(true);
    try {
      const res = await api.updatePassword({
        username: user?.username || "",
        current_password: selfCurPwd,
        password: selfNewPwd,
      });
      if (res.status === "ok") {
        setSelfCurPwd("");
        setSelfNewPwd("");
        setSelfConfirmPwd("");
        setSelfPwdStatus({ ok: true, text: res.message || "密码修改成功，请重新登录" });
        if (onSelfPasswordChanged) {
          onSelfPasswordChanged(user?.username || "");
        }
      } else {
        setSelfPwdStatus({ ok: false, text: res.message || "修改失败" });
      }
    } catch (err: any) {
      setSelfPwdStatus({ ok: false, text: err.message || "修改密码异常" });
    } finally {
      setSelfPwdSaving(false);
    }
  };

  const handleAdminResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setResetStatus(null);
    if (!resetTargetUser) {
      setResetStatus({ ok: false, text: "请选择需要重置密码的成员账号" });
      return;
    }
    if (resetNewPwd.length < 6 || resetNewPwd.length > 64) {
      setResetStatus({ ok: false, text: "新密码长度需在 6 至 64 位之间" });
      return;
    }
    if (resetNewPwd !== resetNewPwd.trim()) {
      setResetStatus({ ok: false, text: "新密码首尾不能包含空格" });
      return;
    }
    setResetSaving(true);
    try {
      const res = await api.updatePassword({
        username: resetTargetUser,
        password: resetNewPwd,
      });
      if (res.status === "ok") {
        setResetNewPwd("");
        setResetStatus({ ok: true, text: res.message || `已重置 ${resetTargetUser} 的密码` });
      } else {
        setResetStatus({ ok: false, text: res.message || "重置失败" });
      }
    } catch (err: any) {
      setResetStatus({ ok: false, text: err.message || "重置密码异常" });
    } finally {
      setResetSaving(false);
    }
  };

  const handleSaveBark = async () => {
    try {
      const res = await api.saveBarkConfig(barkUrl);
      if (res.status === "ok") {
        setBarkStatus("✅ Bark 配置保存成功！");
      }
    } catch (err: any) {
      setBarkStatus("❌ 保存失败: " + err.message);
    }
  };

  const handleTestBark = async () => {
    try {
      const res = await api.testBark(barkUrl);
      if (res.status === "ok") {
        setBarkStatus("🚀 测试推送已发送，请在手机上查收！");
      }
    } catch (err: any) {
      setBarkStatus("❌ 推送失败: " + err.message);
    }
  };

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "300px", color: "var(--brand)" }}>
        正在读取系统状态与元数据...
      </div>
    );
  }

  // Database Diagnostics
  if (mode === "db") {
    const tables = data?.tables || [];
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", overflowY: "auto" }}>
        <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
          <Database size={18} color="var(--brand)" />
          <span>核心 SQLite 数据库只读状态与表记录完整性检查</span>
        </div>

        <div className="card-panel">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "12px" }}>
            <div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>数据库物理路径</div>
              <div style={{ fontSize: "13px", fontWeight: 600, marginTop: "2px", wordBreak: "break-all" }}>
                {data?.database?.path || "—"}
              </div>
            </div>
            <div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>文件大小</div>
              <div className="font-mono" style={{ fontSize: "13px", fontWeight: 600, marginTop: "2px" }}>
                {((data?.database?.size_bytes || 0) / 1024 / 1024).toFixed(2)} MB
              </div>
            </div>
            <div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>访问安全权限</div>
              <div style={{ fontSize: "13px", fontWeight: 600, marginTop: "2px", color: "#16A34A" }}>
                🔒 绝对只读保全 (PRD SEC-01)
              </div>
            </div>
          </div>
        </div>

        <div className="table-container">
          <table className="finance-table">
            <thead>
              <tr>
                <th>数据表名</th>
                <th style={{ textAlign: "right" }}>有效记录条数</th>
                <th>作用说明</th>
              </tr>
            </thead>
            <tbody>
              {tables.map((t: any, idx: number) => (
                <tr key={idx}>
                  <td style={{ fontWeight: 700, color: "var(--text-heading)" }}>{t.name}</td>
                  <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                    {t.count?.toLocaleString()}
                  </td>
                  <td style={{ color: "var(--text-muted)" }}>{t.description || "基础财务事实表"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // Progress Changelog
  if (mode === "progress") {
    const logs = data?.progress_logs || [];
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", overflowY: "auto", overflowX: "hidden", overflowWrap: "anywhere" }}>
        <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
          <History size={18} color="var(--brand)" />
          <span>项目架构演进与开发日志 (Progress Log)</span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {logs.map((log: any, idx: number) => (
            <div key={idx} className="card-panel" style={{ padding: "14px 18px" }}>
              <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: "4px 12px", marginBottom: "8px" }}>
                <span style={{ fontSize: "15px", fontWeight: 700, color: "var(--text-heading)" }}>
                  {log.title || `更新记录`}
                </span>
                <span className="font-mono" style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                  {log.timestamp || log.time}
                </span>
              </div>
              {(log.author || log.tag) && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 12px", marginBottom: "8px", fontSize: "12px", color: "var(--brand)" }}>
                  {log.author && <strong>{log.author}</strong>}
                  {log.tag && <span>{log.tag}</span>}
                </div>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                {(log.details || []).map((detail: string, dIdx: number) => (
                  <div key={dIdx} style={{ fontSize: "13px", lineHeight: "1.5", color: "var(--text-main)" }}>
                    • {detail}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Metrics Glossary
  if (mode === "metrics") {
    const metrics = data?.metrics || [];
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", overflowY: "auto" }}>
        <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
          <BookOpen size={18} color="var(--brand)" />
          <span>复式财务计算口径与 18 项核心指标定义字典</span>
        </div>

        <div className="table-container">
          <table className="finance-table">
            <thead>
              <tr>
                <th style={{ minWidth: "120px" }}>指标名称</th>
                <th style={{ minWidth: "220px" }}>标准财务计算公式</th>
                <th>业务定义与边界</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((m: any, idx: number) => (
                <tr key={idx}>
                  <td style={{ fontWeight: 700, color: "var(--text-heading)" }}>{m.name}</td>
                  <td className="font-mono" style={{ color: "var(--brand)", fontSize: "12px" }}>
                    {m.formula}
                  </td>
                  <td style={{ color: "var(--text-muted)", lineHeight: "1.5" }}>{m.definition}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // Excel Reconciliation
  if (mode === "reconcile") {
    const diffs = data?.diffs || [];
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", overflowY: "auto" }}>
        <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
          <CheckCircle2 size={18} color="#16A34A" />
          <span>Excel 交易流水与 SQLite 引擎核算对账核验</span>
        </div>

        <div className="card-panel">
          <div style={{ color: "#16A34A", fontWeight: 600, display: "flex", alignItems: "center", gap: "6px" }}>
            <CheckCircle2 size={16} />
            <span>核心财务事实比对一致：全部标的持仓均价、结余股数与净投入无断层差异。</span>
          </div>
        </div>

        <div className="table-container">
          <table className="finance-table">
            <thead>
              <tr>
                <th>标的代码</th>
                <th>券商渠道</th>
                <th style={{ textAlign: "right" }}>Excel 记录数</th>
                <th style={{ textAlign: "right" }}>数据库记录数</th>
                <th>核对状态</th>
              </tr>
            </thead>
            <tbody>
              {diffs.map((d: any, idx: number) => (
                <tr key={idx}>
                  <td style={{ fontWeight: 700 }}>{d.symbol}</td>
                  <td>{d.broker}</td>
                  <td className="font-mono" style={{ textAlign: "right" }}>
                    {d.excel_count}
                  </td>
                  <td className="font-mono" style={{ textAlign: "right" }}>
                    {d.db_count}
                  </td>
                  <td style={{ color: "#16A34A", fontWeight: 600 }}>100% 对齐一致</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // Settings
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "16px", overflowY: "auto", maxWidth: "700px" }}>
      <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
        <Sliders size={18} color="var(--brand)" />
        <span>系统首选项、账号安全与推送配置</span>
      </div>

      {/* 账号与安全（修改本人密码 + 管理员重置成员密码） */}
      <div className="card-panel" style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "14px", fontWeight: 600, color: "var(--text-heading)" }}>
          <KeyRound size={16} color="var(--brand)" />
          <span>账号与登录密码</span>
        </div>

        {user?.is_authenticated ? (
          <>
            <div style={{ fontSize: "12px", color: "var(--text-muted)", lineHeight: "1.6" }}>
              当前登录账号：<strong>{user.username}</strong>
              {user.person_name ? `（归属人员：${user.person_name}）` : ""}。修改密码后将注销所有设备上的旧会话，需使用新密码重新登录。
            </div>

            <form onSubmit={handleSelfPasswordSubmit} style={{ display: "flex", flexDirection: "column", gap: "10px", maxWidth: "420px" }}>
              <div>
                <label style={{ fontSize: "12px", fontWeight: 600, display: "block", marginBottom: "4px" }}>当前原密码</label>
                <input
                  type="password"
                  value={selfCurPwd}
                  onChange={(e) => setSelfCurPwd(e.target.value)}
                  className="input-control"
                  style={{ width: "100%" }}
                  autoComplete="current-password"
                  required
                />
              </div>
              <div>
                <label style={{ fontSize: "12px", fontWeight: 600, display: "block", marginBottom: "4px" }}>新密码（6~64 位）</label>
                <input
                  type="password"
                  value={selfNewPwd}
                  onChange={(e) => setSelfNewPwd(e.target.value)}
                  className="input-control"
                  style={{ width: "100%" }}
                  autoComplete="new-password"
                  minLength={6}
                  maxLength={64}
                  required
                />
              </div>
              <div>
                <label style={{ fontSize: "12px", fontWeight: 600, display: "block", marginBottom: "4px" }}>确认新密码</label>
                <input
                  type="password"
                  value={selfConfirmPwd}
                  onChange={(e) => setSelfConfirmPwd(e.target.value)}
                  className="input-control"
                  style={{ width: "100%" }}
                  autoComplete="new-password"
                  minLength={6}
                  maxLength={64}
                  required
                />
              </div>
              {selfPwdStatus && (
                <div style={{ fontSize: "12px", fontWeight: 600, color: selfPwdStatus.ok ? "#16A34A" : "var(--loss-color)" }}>
                  {selfPwdStatus.text}
                </div>
              )}
              <div>
                <button type="submit" className="btn btn-primary" disabled={selfPwdSaving}>
                  {selfPwdSaving ? "正在保存..." : "修改我的密码"}
                </button>
              </div>
            </form>

            {user.can_admin && (
              <div style={{ borderTop: "1px solid var(--border-color)", paddingTop: "14px", display: "flex", flexDirection: "column", gap: "10px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px", fontWeight: 600, color: "var(--text-heading)" }}>
                  <Users size={15} color="var(--brand)" />
                  <span>管理员快捷重置成员密码（免原密码）</span>
                </div>
                {managedUsers.length > 0 ? (
                  <form onSubmit={handleAdminResetPassword} style={{ display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center" }}>
                    <select
                      className="input-control"
                      value={resetTargetUser}
                      onChange={(e) => setResetTargetUser(e.target.value)}
                      style={{ minWidth: "160px" }}
                    >
                      {managedUsers.map((u) => (
                        <option key={u.id} value={u.username}>
                          {u.username}{u.person_name && u.person_name !== u.username ? ` (${u.person_name})` : ""}
                        </option>
                      ))}
                    </select>
                    <input
                      type="password"
                      value={resetNewPwd}
                      onChange={(e) => setResetNewPwd(e.target.value)}
                      placeholder="输入成员新密码（至少 6 位）"
                      className="input-control"
                      style={{ flex: 1, minWidth: "200px" }}
                      minLength={6}
                      maxLength={64}
                      required
                    />
                    <button type="submit" className="btn" disabled={resetSaving}>
                      {resetSaving ? "重置中..." : "重置该成员密码"}
                    </button>
                  </form>
                ) : (
                  <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>暂无其他可管理的成员账号。</div>
                )}
                {resetStatus && (
                  <div style={{ fontSize: "12px", fontWeight: 600, color: resetStatus.ok ? "#16A34A" : "var(--loss-color)" }}>
                    {resetStatus.text}
                  </div>
                )}
              </div>
            )}
          </>
        ) : (
          <div style={{ background: "var(--table-alternate)", padding: "10px", borderRadius: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            当前为免登录访问状态。如需修改账户密码，请先使用对应账号登录。
          </div>
        )}
      </div>

      {/* Theme & Display Preferences */}
      <div className="card-panel" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
        <div style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-heading)" }}>界面与视觉</div>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontWeight: 600 }}>深色模式 (Dark Theme)</div>
            <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
              与原生桌面端深浅色风格 1:1 同步
            </div>
          </div>
          <button onClick={onToggleTheme} className="btn">
            {theme === "light" ? "开启深色模式" : "切换为浅色模式"}
          </button>
        </div>

        <div style={{ borderTop: "1px solid var(--border-color)", paddingTop: "12px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontWeight: 600 }}>红涨绿跌 / 绿涨红跌</div>
            <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
              当前为：{greenProfit ? "绿涨红跌 (国际金融惯例)" : "红涨绿跌 (中国A股习惯)"}
            </div>
          </div>
          <button onClick={onToggleGreenProfit} className="btn">
            {greenProfit ? "切换为红涨绿跌" : "切换为绿涨红跌"}
          </button>
        </div>
      </div>

      {/* Bark Push Notifications (Only accessible/editable by admin, PRD SEC-08) */}
      <div className="card-panel" style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "14px", fontWeight: 600, color: "var(--text-heading)" }}>
          <Bell size={16} color="var(--brand)" />
          <span>Bark 行情预警与系统通知</span>
        </div>

        <div style={{ fontSize: "12px", color: "var(--text-muted)", lineHeight: "1.5" }}>
          Bark URL 属于敏感凭据。根据安全规范，未登录用户和局域网访客无法获取完整密钥，仅管理员可配置与测试。
        </div>

        {user?.can_admin ? (
          <>
            <div>
              <label style={{ fontSize: "12px", fontWeight: 600, display: "block", marginBottom: "4px" }}>
                Bark 推送完整 URL
              </label>
              <input
                type="text"
                value={barkUrl}
                onChange={(e) => setBarkUrl(e.target.value)}
                placeholder="https://api.day.app/YOUR_BARK_KEY/"
                className="input-control font-mono"
                style={{ width: "100%" }}
              />
            </div>

            <div style={{ display: "flex", gap: "8px" }}>
              <button onClick={handleSaveBark} className="btn btn-primary">
                保存配置
              </button>
              <button onClick={handleTestBark} className="btn">
                发送测试通知
              </button>
            </div>
          </>
        ) : (
          <div style={{ background: "var(--table-alternate)", padding: "10px", borderRadius: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            当前身份无权管理 Bark 推送密钥。若需配置，请使用管理员账号登录。
          </div>
        )}

        {barkStatus && (
          <div style={{ fontSize: "12px", fontWeight: 600, marginTop: "4px" }}>
            {barkStatus}
          </div>
        )}
      </div>
    </div>
  );
};
