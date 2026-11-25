import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  Check,
  Copy,
  Edit3,
  MessageSquare,
  PenTool,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Sparkles,
  Trash2,
  User,
  X,
} from "lucide-react";
import { api } from "../api/client";
import { MarkdownBlock } from "./AiViews";

interface LakeWriterViewProps {
  isAdmin?: boolean;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  time?: string;
  time_str?: string;
  model?: string;
  failedPrompt?: string;
}

interface TabSummary {
  id: string;
  title: string;
  message_count: number;
  created_at?: string;
  updated_at?: string;
}

interface ArchiveBatch {
  id: string;
  cleared_at: string;
  reason?: string;
  messages: Message[];
}

interface ArchiveRecord {
  id: string;
  owner: string;
  title: string;
  status: "active" | "deleted" | string;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
  active_message_count: number;
  archived_batch_count: number;
  archived_message_count: number;
  total_message_count: number;
  messages: Message[];
  archived_batches: ArchiveBatch[];
}

interface AgyModelOption {
  id: string;
  label: string;
}

const DEFAULT_GEMINI_MODELS: AgyModelOption[] = [
  { id: "gemini-4-hydrogen-high", label: "Gemini 4 Hydrogen (High)" },
  { id: "gemini-4-hydrogen-medium", label: "Gemini 4 Hydrogen (Medium)" },
  { id: "gemini-4-hydrogen-xhigh", label: "Gemini 4 Hydrogen (XHigh)" },
  { id: "gemini-4-neon-medium", label: "Gemini 4 Neon (Medium)" },
  { id: "gemini-4-neon-high", label: "Gemini 4 Neon (High)" },
  { id: "gemini-4-neon-xhigh", label: "Gemini 4 Neon (XHigh)" },
  { id: "gemini-4-argon-medium", label: "Gemini 4 Argon (Medium)" },
  { id: "gemini-4-argon-high", label: "Gemini 4 Argon (High)" },
  { id: "gemini-4-carbon-medium", label: "Gemini 4 Carbon (Medium)" },
  { id: "gemini-3.8-flash-medium", label: "Gemini 3.8 Flash (Medium)" },
];

const QUICK_PROMPTS = [
  "帮我拟 5 个更有吸引力的文章标题，主题是：",
  "帮我写一篇 1500 字左右、观点鲜明且适合公众号阅读的深度文章，主题是：",
  "请帮我润色下面这段文字，让表达更流畅、有节奏感且不生硬：\n",
  "请根据以下核心观点，帮我先列出文章大纲和段落结构：\n",
];

/** 拉科专属 Gemini 写作空间（多对话框标签 + 软清空归档 + 管理员存档只读浏览） */
export const LakeWriterView: React.FC<LakeWriterViewProps> = ({ isAdmin = false }) => {
  const [viewMode, setViewMode] = useState<"writer" | "archives">("writer");

  // 多标签写作状态
  const [tabs, setTabs] = useState<TabSummary[]>([]);
  const [activeTabId, setActiveTabId] = useState<string>("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [geminiModels, setGeminiModels] = useState<AgyModelOption[]>(DEFAULT_GEMINI_MODELS);
  const [selectedModel, setSelectedModel] = useState("gemini-4-hydrogen-high");
  const [geminiStatus, setGeminiStatus] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 管理员存档视图状态
  const [archiveOwners, setArchiveOwners] = useState<string[]>([]);
  const [archiveOwnerFilter, setArchiveOwnerFilter] = useState<string>("");
  const [archiveSearch, setArchiveSearch] = useState<string>("");
  const [archiveRecords, setArchiveRecords] = useState<ArchiveRecord[]>([]);
  const [selectedArchiveId, setSelectedArchiveId] = useState<string>("");
  const [archiveLoading, setArchiveLoading] = useState(false);

  const refreshStatus = (force = false) => {
    api
      .aiStatus("gemini", force)
      .then((res) => {
        if (res.status === "ok") {
          const st = res.providers?.gemini || res.ai_status;
          if (st) setGeminiStatus(st);
          if (res.agy_model && String(res.agy_model).toLowerCase().includes("gemini")) {
            setSelectedModel(res.agy_model);
          }
        }
      })
      .catch(() => {});
  };

  const fetchModels = (force = false) => {
    api
      .aiAgyModels(force)
      .then((res) => {
        if (res.status === "ok" && Array.isArray(res.models)) {
          const onlyGemini = res.models.filter((m: AgyModelOption) =>
            String(m.id || "").toLowerCase().includes("gemini")
          );
          const list = onlyGemini.length > 0 ? onlyGemini : DEFAULT_GEMINI_MODELS;
          setGeminiModels(list);
          if (res.current_model && list.some((m: AgyModelOption) => m.id === res.current_model)) {
            setSelectedModel(res.current_model);
          } else if (!list.some((m: AgyModelOption) => m.id === selectedModel)) {
            setSelectedModel(list[0].id);
          }
        }
      })
      .catch(() => {});
  };

  const fetchTabHistory = (tabId?: string) => {
    api
      .aiLakeWriterHistory(tabId)
      .then((res) => {
        if (res.status === "ok") {
          setTabs(res.tabs || []);
          setActiveTabId(res.active_tab_id || "");
          setMessages(res.messages || []);
        }
      })
      .catch((err) => console.error("加载拉科专属写作历史失败:", err));
  };

  const fetchAdminArchives = (owner = archiveOwnerFilter, q = archiveSearch) => {
    if (!isAdmin) return;
    setArchiveLoading(true);
    api
      .aiLakeWriterAdminArchives(owner || undefined, q || undefined)
      .then((res) => {
        if (res.status === "ok") {
          setArchiveOwners(res.owners || []);
          const list: ArchiveRecord[] = res.records || [];
          setArchiveRecords(list);
          if (list.length > 0 && !list.some((r) => r.id === selectedArchiveId)) {
            setSelectedArchiveId(list[0].id);
          } else if (list.length === 0) {
            setSelectedArchiveId("");
          }
        }
      })
      .catch((err) => console.error("加载管理员写作存档失败:", err))
      .finally(() => setArchiveLoading(false));
  };

  useEffect(() => {
    fetchTabHistory();
    refreshStatus(false);
    fetchModels(false);
  }, []);

  useEffect(() => {
    if (viewMode === "archives" && isAdmin) {
      fetchAdminArchives(archiveOwnerFilter, archiveSearch);
    }
  }, [viewMode, archiveOwnerFilter]);

  useEffect(() => {
    if (!loading) {
      setElapsedSec(0);
      return;
    }
    const timer = window.setInterval(() => setElapsedSec((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [loading]);

  useEffect(() => {
    if (scrollRef.current && viewMode === "writer") {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, loading, viewMode]);

  const canSend = useMemo(() => {
    if (loading) return false;
    if (!geminiStatus) return true;
    return Boolean(geminiStatus.logged_in);
  }, [loading, geminiStatus]);

  const handleModelChange = (nextModel: string) => {
    setSelectedModel(nextModel);
    api.aiSaveAgyModel(nextModel).catch(() => {});
  };

  const handleCreateTab = async () => {
    if (loading) return;
    try {
      const res = await api.aiLakeWriterCreateTab();
      if (res.status === "ok") {
        setTabs(res.tabs || []);
        setActiveTabId(res.active_tab_id || "");
        setMessages([]);
        textareaRef.current?.focus();
      }
    } catch (err: any) {
      alert("新建对话标签失败: " + err.message);
    }
  };

  const handleSwitchTab = (tabId: string) => {
    if (loading || tabId === activeTabId) return;
    fetchTabHistory(tabId);
  };

  const handleRenameTab = async (tab: TabSummary, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    const nextTitle = window.prompt("请输入新的对话标签名称：", tab.title);
    if (nextTitle === null) return;
    const clean = nextTitle.trim();
    if (!clean || clean === tab.title) return;
    try {
      const res = await api.aiLakeWriterRenameTab(tab.id, clean);
      if (res.status === "ok") {
        setTabs(res.tabs || []);
      }
    } catch (err: any) {
      alert("重命名失败: " + err.message);
    }
  };

  const handleCloseTab = async (tab: TabSummary, e: React.MouseEvent) => {
    e.stopPropagation();
    if (loading) return;
    if (!confirm(`确定关闭标签「${tab.title}」吗？`)) return;
    try {
      const res = await api.aiLakeWriterDeleteTab(tab.id);
      if (res.status === "ok") {
        setTabs(res.tabs || []);
        setActiveTabId(res.active_tab_id || "");
        setMessages(res.messages || []);
      }
    } catch (err: any) {
      alert("关闭标签失败: " + err.message);
    }
  };

  const sendPrompt = async (customText?: string) => {
    const userMsg = (customText ?? input).trim();
    if (!userMsg || loading) return;
    if (!canSend) {
      alert("当前 Gemini 引擎未就绪，请点击右上角刷新检查状态。");
      return;
    }
    if (customText === undefined) {
      setInput("");
    }
    const nowTime = new Date().toLocaleTimeString();
    setMessages((prev) => [...prev, { role: "user", content: userMsg, time_str: nowTime }]);
    setLoading(true);

    try {
      const res = await api.aiLakeWriterChat(userMsg, selectedModel, "gemini", activeTabId || undefined);
      if (res.status === "ok") {
        if (Array.isArray(res.tabs)) setTabs(res.tabs);
        if (res.active_tab_id) setActiveTabId(res.active_tab_id);
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: res.answer || res.reply || res.content || res.response || "",
            time_str: new Date().toLocaleTimeString(),
            model: selectedModel,
          },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: `⚠️ **调用 Gemini 遇到问题**：\n\n${res.message || "请求未成功"}`,
            time_str: new Date().toLocaleTimeString(),
            failedPrompt: userMsg,
          },
        ]);
      }
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `⚠️ **调用 Gemini 异常**：\n\n${err.message || "网络或服务异常"}`,
          time_str: new Date().toLocaleTimeString(),
          failedPrompt: userMsg,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleClear = async () => {
    if (!confirm("确定要清空当前标签页的对话记录并开启新上下文吗？")) return;
    try {
      const res = await api.aiLakeWriterClear(activeTabId || undefined);
      if (res.status === "ok") {
        if (Array.isArray(res.tabs)) setTabs(res.tabs);
        setMessages([]);
      }
    } catch (err: any) {
      alert("清空失败: " + err.message);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      sendPrompt();
    }
  };

  const copyContent = (text: string, key: string) => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey((cur) => (cur === key ? null : cur)), 1500);
    });
  };

  const applyQuickPrompt = (tpl: string) => {
    setInput(tpl);
    textareaRef.current?.focus();
  };

  const selectedArchive = useMemo(
    () => archiveRecords.find((r) => r.id === selectedArchiveId) || archiveRecords[0] || null,
    [archiveRecords, selectedArchiveId]
  );

  const renderStatusBadge = () => {
    if (!geminiStatus) {
      return (
        <span style={{ fontSize: "11px", padding: "2px 9px", borderRadius: "10px", background: "#F1F5F9", color: "#64748B" }}>
          正在检测 Gemini 状态...
        </span>
      );
    }
    const ok = Boolean(geminiStatus.logged_in);
    const inst = Boolean(geminiStatus.installed);
    const label = ok
      ? `🟢 已连接 Gemini (${geminiStatus.account_type || "Antigravity"})`
      : inst
        ? "🟡 Gemini 未登录"
        : "🔴 未安装 Gemini CLI";
    return (
      <span
        style={{
          fontSize: "11px",
          padding: "2px 9px",
          borderRadius: "10px",
          background: ok ? "#DCFCE7" : inst ? "#FEF9C3" : "#FEE2E2",
          color: ok ? "#166534" : inst ? "#854D0E" : "#991B1B",
          fontWeight: 600,
        }}
      >
        {label}
      </span>
    );
  };

  const renderMessageBubble = (msg: Message, keyId: string, allowRetry = false) => {
    const isUser = msg.role === "user";
    return (
      <div
        key={keyId}
        style={{
          display: "flex",
          flexDirection: isUser ? "row-reverse" : "row",
          alignItems: "flex-start",
          gap: "10px",
        }}
      >
        <div
          style={{
            width: "30px",
            height: "30px",
            borderRadius: "50%",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: isUser ? "var(--brand)" : "var(--brand-selected)",
            color: isUser ? "#FFFFFF" : "var(--brand)",
          }}
        >
          {isUser ? <User size={15} /> : <PenTool size={15} />}
        </div>

        <div
          style={{
            maxWidth: isUser ? "75%" : "86%",
            background: isUser ? "var(--brand)" : "var(--bg-app)",
            color: isUser ? "#FFFFFF" : "var(--text-main)",
            border: isUser ? "none" : "1px solid var(--border-color)",
            borderRadius: "12px",
            padding: "12px 15px",
            fontSize: "13.5px",
            lineHeight: 1.7,
            wordBreak: "break-word",
          }}
        >
          {isUser ? (
            <div style={{ whiteSpace: "pre-wrap" }}>{msg.content}</div>
          ) : (
            <MarkdownBlock text={msg.content} />
          )}

          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "10px",
              marginTop: "8px",
              fontSize: "11px",
              opacity: 0.78,
            }}
          >
            <span>
              {msg.time_str || msg.time || ""}
              {!isUser && msg.model ? ` · ${msg.model}` : ""}
            </span>
            {!isUser && (
              <div style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                {allowRetry && msg.failedPrompt && (
                  <button
                    type="button"
                    onClick={() => sendPrompt(msg.failedPrompt)}
                    disabled={loading}
                    style={{
                      border: "1px solid var(--border-color)",
                      background: "var(--bg-card)",
                      color: "var(--brand)",
                      borderRadius: "6px",
                      padding: "2px 8px",
                      fontSize: "11px",
                      cursor: "pointer",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "4px",
                    }}
                  >
                    <RotateCcw size={11} />
                    重试
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => copyContent(msg.content, keyId)}
                  style={{
                    border: "1px solid var(--border-color)",
                    background: "var(--bg-card)",
                    color: "var(--text-main)",
                    borderRadius: "6px",
                    padding: "2px 8px",
                    fontSize: "11px",
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "4px",
                  }}
                >
                  {copiedKey === keyId ? <Check size={11} color="#16A34A" /> : <Copy size={11} />}
                  {copiedKey === keyId ? "已复制" : "复制文章"}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "10px", minHeight: 0 }}>
      {/* 顶部工具栏 */}
      <div
        className="card-panel"
        style={{
          padding: "10px 16px",
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "12px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 700, fontSize: "14px", color: "var(--text-heading)" }}>
            <PenTool size={17} color="var(--brand)" />
            <span>Gemini 写作助手</span>
          </div>
          {renderStatusBadge()}
          <button
            className="btn"
            onClick={() => {
              refreshStatus(true);
              fetchModels(true);
              if (viewMode === "archives" && isAdmin) {
                fetchAdminArchives(archiveOwnerFilter, archiveSearch);
              }
            }}
            title="刷新 Gemini 状态与模型列表"
            style={{ padding: "4px 8px", fontSize: "12px", display: "inline-flex", alignItems: "center", gap: "4px" }}
          >
            <RefreshCw size={13} />
            刷新
          </button>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          {viewMode === "writer" && (
            <>
              <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
                <span>Gemini 模型</span>
                <select
                  className="input-control"
                  value={selectedModel}
                  onChange={(e) => handleModelChange(e.target.value)}
                  disabled={loading}
                  style={{ minWidth: "205px", fontSize: "12px", padding: "5px 8px" }}
                >
                  {geminiModels.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </label>

              <button
                className="btn"
                onClick={handleClear}
                disabled={loading || messages.length === 0}
                style={{
                  padding: "5px 10px",
                  fontSize: "12px",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "5px",
                  color: messages.length > 0 ? "var(--loss-color)" : "var(--text-muted)",
                }}
              >
                <Trash2 size={13} />
                清空对话
              </button>
            </>
          )}

          {isAdmin && (
            <button
              className={viewMode === "archives" ? "btn btn-primary" : "btn"}
              onClick={() => setViewMode(viewMode === "writer" ? "archives" : "writer")}
              style={{
                padding: "5px 10px",
                fontSize: "12px",
                display: "inline-flex",
                alignItems: "center",
                gap: "5px",
              }}
              title="管理员专用：查看包括已清空、已关闭在内的全部历史对话记录"
            >
              {viewMode === "archives" ? <MessageSquare size={13} /> : <Archive size={13} />}
              {viewMode === "archives" ? "返回写作" : "全部对话存档"}
            </button>
          )}
        </div>
      </div>

      {viewMode === "writer" ? (
        <>
          {/* 多对话框标签栏（每个标签上下文独立，节约 Token） */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              overflowX: "auto",
              padding: "2px 2px 4px",
              flexShrink: 0,
            }}
          >
            {tabs.map((tab) => {
              const isActive = tab.id === activeTabId;
              return (
                <div
                  key={tab.id}
                  onClick={() => handleSwitchTab(tab.id)}
                  onDoubleClick={() => handleRenameTab(tab)}
                  title={`${tab.title}（共 ${tab.message_count} 条消息 · 双击重命名）`}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "6px",
                    padding: "6px 10px",
                    borderRadius: "8px",
                    border: isActive ? "1px solid var(--brand)" : "1px solid var(--border-color)",
                    background: isActive ? "var(--brand-selected)" : "var(--bg-panel)",
                    color: isActive ? "var(--brand)" : "var(--text-main)",
                    fontWeight: isActive ? 600 : 500,
                    fontSize: "12.5px",
                    cursor: loading ? "not-allowed" : "pointer",
                    whiteSpace: "nowrap",
                    flexShrink: 0,
                  }}
                >
                  <MessageSquare size={13} />
                  <span style={{ maxWidth: "150px", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {tab.title}
                  </span>
                  {tab.message_count > 0 && (
                    <span
                      style={{
                        fontSize: "10px",
                        padding: "0 5px",
                        borderRadius: "10px",
                        background: isActive ? "rgba(37, 99, 235, 0.14)" : "var(--hover-bg)",
                        color: isActive ? "var(--brand)" : "var(--text-muted)",
                      }}
                    >
                      {tab.message_count}
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={(e) => handleRenameTab(tab, e)}
                    title="重命名标签"
                    style={{
                      border: "none",
                      background: "transparent",
                      color: "var(--text-muted)",
                      cursor: "pointer",
                      padding: "1px",
                      display: "inline-flex",
                    }}
                  >
                    <Edit3 size={11} />
                  </button>
                  <button
                    type="button"
                    onClick={(e) => handleCloseTab(tab, e)}
                    title="关闭标签"
                    style={{
                      border: "none",
                      background: "transparent",
                      color: "var(--text-muted)",
                      cursor: "pointer",
                      padding: "1px",
                      display: "inline-flex",
                    }}
                  >
                    <X size={12} />
                  </button>
                </div>
              );
            })}

            <button
              type="button"
              onClick={handleCreateTab}
              disabled={loading}
              className="btn"
              style={{
                padding: "5px 10px",
                fontSize: "12px",
                display: "inline-flex",
                alignItems: "center",
                gap: "4px",
                flexShrink: 0,
                borderStyle: "dashed",
              }}
              title="新建一个空白对话标签，与当前上下文完全隔离"
            >
              <Plus size={13} />
              新建对话
            </button>
          </div>

          {/* 主对话内容区 */}
          <div
            ref={scrollRef}
            className="card-panel"
            style={{
              flex: 1,
              overflowY: "auto",
              padding: "20px",
              display: "flex",
              flexDirection: "column",
              gap: "16px",
              minHeight: 0,
            }}
          >
            {messages.length === 0 ? (
              <div
                style={{
                  margin: "auto",
                  maxWidth: "620px",
                  textAlign: "center",
                  padding: "24px 16px",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: "14px",
                }}
              >
                <div
                  style={{
                    width: "48px",
                    height: "48px",
                    borderRadius: "14px",
                    background: "var(--brand-selected)",
                    color: "var(--brand)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Sparkles size={24} />
                </div>
                <div>
                  <div style={{ fontSize: "17px", fontWeight: 700, color: "var(--text-heading)", marginBottom: "6px" }}>
                    拉科专属 · Gemini 写作空间
                  </div>
                  <div style={{ fontSize: "13px", color: "var(--text-muted)", lineHeight: 1.6 }}>
                    不同文章可点上方「新建对话」分开创作，互不干扰上下文；当前标签内支持持续改稿、润色与起标题。
                  </div>
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                    gap: "8px",
                    width: "100%",
                    marginTop: "6px",
                  }}
                >
                  {QUICK_PROMPTS.map((tpl, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => applyQuickPrompt(tpl)}
                      style={{
                        textAlign: "left",
                        padding: "10px 12px",
                        borderRadius: "10px",
                        border: "1px solid var(--border-color)",
                        background: "var(--bg-app)",
                        color: "var(--text-main)",
                        fontSize: "12px",
                        lineHeight: 1.5,
                        cursor: "pointer",
                      }}
                    >
                      {tpl.trim()}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((msg, idx) => renderMessageBubble(msg, `w-${activeTabId}-${idx}`, true))
            )}

            {loading && (
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                <div
                  style={{
                    width: "30px",
                    height: "30px",
                    borderRadius: "50%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: "var(--brand-selected)",
                    color: "var(--brand)",
                  }}
                >
                  <PenTool size={15} />
                </div>
                <div
                  style={{
                    padding: "10px 14px",
                    borderRadius: "12px",
                    border: "1px solid var(--border-color)",
                    background: "var(--bg-app)",
                    fontSize: "13px",
                    color: "var(--text-muted)",
                  }}
                >
                  Gemini 正在创作中... {elapsedSec > 0 ? `(${elapsedSec}s)` : ""}
                </div>
              </div>
            )}
          </div>

          {/* 底部输入栏 */}
          <div
            className="card-panel"
            style={{
              padding: "12px 14px",
              display: "flex",
              gap: "10px",
              alignItems: "flex-end",
            }}
          >
            <textarea
              ref={textareaRef}
              className="input-control"
              rows={3}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading}
              placeholder="输入文章主题、提纲、素材或改稿意见...（⌘+Enter 或 Ctrl+Enter 发送）"
              style={{
                flex: 1,
                resize: "vertical",
                minHeight: "68px",
                maxHeight: "220px",
                fontSize: "13.5px",
                lineHeight: 1.6,
                padding: "8px 12px",
              }}
            />
            <button
              className="btn btn-primary"
              onClick={() => sendPrompt()}
              disabled={loading || !input.trim() || !canSend}
              style={{
                height: "40px",
                padding: "0 18px",
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
                fontWeight: 600,
              }}
            >
              <Send size={14} />
              {loading ? "创作中" : "发送"}
            </button>
          </div>
        </>
      ) : (
        /* 管理员专用：全量写作存档视图（含已清空批次和已关闭标签） */
        <div style={{ flex: 1, display: "flex", gap: "12px", minHeight: 0 }}>
          {/* 左侧过滤与标签存档列表 */}
          <div
            className="card-panel"
            style={{
              width: "300px",
              minWidth: "260px",
              display: "flex",
              flexDirection: "column",
              gap: "10px",
              padding: "12px",
              minHeight: 0,
            }}
          >
            <div style={{ fontSize: "13px", fontWeight: 700, color: "var(--text-heading)" }}>
              全部写作存档（共 {archiveRecords.length} 个标签）
            </div>
            <div style={{ display: "flex", gap: "6px" }}>
              <select
                className="input-control"
                value={archiveOwnerFilter}
                onChange={(e) => setArchiveOwnerFilter(e.target.value)}
                style={{ flex: 1, fontSize: "12px" }}
              >
                <option value="">全部账号</option>
                {archiveOwners.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                fetchAdminArchives(archiveOwnerFilter, archiveSearch);
              }}
              style={{ display: "flex", gap: "6px" }}
            >
              <input
                type="text"
                className="input-control"
                placeholder="搜索标题或对话关键词..."
                value={archiveSearch}
                onChange={(e) => setArchiveSearch(e.target.value)}
                style={{ flex: 1, fontSize: "12px" }}
              />
              <button type="submit" className="btn" style={{ padding: "4px 8px" }} title="搜索">
                <Search size={13} />
              </button>
            </form>

            <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "6px" }}>
              {archiveLoading ? (
                <div style={{ fontSize: "12px", color: "var(--text-muted)", padding: "12px", textAlign: "center" }}>
                  正在加载存档...
                </div>
              ) : archiveRecords.length === 0 ? (
                <div style={{ fontSize: "12px", color: "var(--text-muted)", padding: "12px", textAlign: "center" }}>
                  暂无匹配的对话记录
                </div>
              ) : (
                archiveRecords.map((rec) => {
                  const active = selectedArchive?.id === rec.id;
                  const isDeleted = rec.status === "deleted";
                  return (
                    <button
                      key={rec.id}
                      type="button"
                      onClick={() => setSelectedArchiveId(rec.id)}
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
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "6px" }}>
                        <span style={{ fontWeight: 600, fontSize: "12.5px", color: "var(--text-heading)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {rec.title}
                        </span>
                        <span
                          style={{
                            fontSize: "10px",
                            padding: "1px 6px",
                            borderRadius: "10px",
                            background: isDeleted ? "#FEE2E2" : "#DCFCE7",
                            color: isDeleted ? "#991B1B" : "#166534",
                            flexShrink: 0,
                          }}
                        >
                          {isDeleted ? "已关闭" : "活跃中"}
                        </span>
                      </div>
                      <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                        账号：{rec.owner} · 共 {rec.total_message_count} 条
                        {rec.archived_batch_count > 0 ? `（含 ${rec.archived_batch_count} 次清空存档）` : ""}
                      </div>
                      <div style={{ fontSize: "10.5px", color: "var(--text-subtle)" }}>
                        更新：{rec.updated_at.replace("T", " ")}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </div>

          {/* 右侧完整对话与清空存档内容 */}
          <div
            className="card-panel"
            style={{
              flex: 1,
              overflowY: "auto",
              padding: "18px",
              display: "flex",
              flexDirection: "column",
              gap: "16px",
              minHeight: 0,
            }}
          >
            {!selectedArchive ? (
              <div style={{ margin: "auto", color: "var(--text-muted)", fontSize: "13px" }}>
                请在左侧选择一个对话标签查看完整记录（含用户已清空的历史批次）。
              </div>
            ) : (
              <>
                <div style={{ borderBottom: "1px solid var(--border-color)", paddingBottom: "10px" }}>
                  <div style={{ fontSize: "15px", fontWeight: 700, color: "var(--text-heading)" }}>
                    {selectedArchive.title}
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "4px" }}>
                    所属账号：{selectedArchive.owner} · 状态：{selectedArchive.status === "deleted" ? `已关闭 (${(selectedArchive.deleted_at || "").replace("T", " ")})` : "活跃中"} ·
                    当前对话 {selectedArchive.active_message_count} 条 · 已清空归档 {selectedArchive.archived_batch_count} 批次（{selectedArchive.archived_message_count} 条）
                  </div>
                </div>

                {/* 当前未清空对话 */}
                <div>
                  <div style={{ fontSize: "12.5px", fontWeight: 700, color: "var(--brand)", marginBottom: "10px" }}>
                    当前标签内对话（{selectedArchive.messages.length} 条）
                  </div>
                  {selectedArchive.messages.length === 0 ? (
                    <div style={{ fontSize: "12px", color: "var(--text-muted)", padding: "8px 12px", background: "var(--bg-app)", borderRadius: "8px" }}>
                      当前窗口为空（可能已被清空归档至下方历史批次）。
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                      {selectedArchive.messages.map((m, idx) =>
                        renderMessageBubble(m, `arch-cur-${selectedArchive.id}-${idx}`, false)
                      )}
                    </div>
                  )}
                </div>

                {/* 已清空归档批次 */}
                {selectedArchive.archived_batches.length > 0 && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "16px", marginTop: "8px" }}>
                    {[...selectedArchive.archived_batches].reverse().map((batch, bIdx) => (
                      <div
                        key={batch.id}
                        style={{
                          border: "1px dashed var(--border-color)",
                          borderRadius: "10px",
                          padding: "12px 14px",
                          background: "var(--table-alternate)",
                          display: "flex",
                          flexDirection: "column",
                          gap: "12px",
                        }}
                      >
                        <div style={{ fontSize: "12px", fontWeight: 700, color: "#B45309" }}>
                          已清空存档 #{selectedArchive.archived_batches.length - bIdx} · 清空时间：{(batch.cleared_at || "").replace("T", " ")} · 共 {batch.messages.length} 条
                        </div>
                        {batch.messages.map((m, mIdx) =>
                          renderMessageBubble(m, `arch-b-${batch.id}-${mIdx}`, false)
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
