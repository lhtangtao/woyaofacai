import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  Copy,
  PenTool,
  RefreshCw,
  RotateCcw,
  Send,
  Sparkles,
  Trash2,
  User,
} from "lucide-react";
import { api } from "../api/client";
import { MarkdownBlock } from "./AiViews";

interface Message {
  role: "user" | "assistant";
  content: string;
  time?: string;
  time_str?: string;
  model?: string;
  failedPrompt?: string;
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

/** 拉科专属 Gemini 写作空间（纯写作多轮对话，不关联任何账本数据） */
export const LakeWriterView: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [geminiModels, setGeminiModels] = useState<AgyModelOption[]>(DEFAULT_GEMINI_MODELS);
  const [selectedModel, setSelectedModel] = useState("gemini-4-hydrogen-high");
  const [geminiStatus, setGeminiStatus] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
          if (
            res.current_model &&
            list.some((m: AgyModelOption) => m.id === res.current_model)
          ) {
            setSelectedModel(res.current_model);
          } else if (!list.some((m: AgyModelOption) => m.id === selectedModel)) {
            setSelectedModel(list[0].id);
          }
        }
      })
      .catch(() => {});
  };

  const fetchHistory = () => {
    api
      .aiLakeWriterHistory()
      .then((res) => {
        if (res.status === "ok") {
          setMessages(res.messages || []);
        }
      })
      .catch((err) => console.error("加载拉科专属写作历史失败:", err));
  };

  useEffect(() => {
    fetchHistory();
    refreshStatus(false);
    fetchModels(false);
  }, []);

  useEffect(() => {
    if (!loading) {
      setElapsedSec(0);
      return;
    }
    const timer = window.setInterval(() => setElapsedSec((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [loading]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const canSend = useMemo(() => {
    if (loading) return false;
    if (!geminiStatus) return true;
    return Boolean(geminiStatus.logged_in);
  }, [loading, geminiStatus]);

  const handleModelChange = (nextModel: string) => {
    setSelectedModel(nextModel);
    api.aiSaveAgyModel(nextModel).catch(() => {});
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
      const res = await api.aiLakeWriterChat(userMsg, selectedModel, "gemini");
      if (res.status === "ok") {
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
    if (!confirm("确定要清空拉科专属的写作对话历史吗？")) return;
    try {
      await api.aiLakeWriterClear();
      setMessages([]);
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

  const copyContent = (text: string, idx: number) => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopiedIdx(idx);
      window.setTimeout(() => setCopiedIdx((cur) => (cur === idx ? null : cur)), 1500);
    });
  };

  const applyQuickPrompt = (tpl: string) => {
    setInput(tpl);
    textareaRef.current?.focus();
  };

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

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "12px", minHeight: 0 }}>
      {/* 顶部工具栏：仅保留 Gemini 模型选择、状态检测与清空 */}
      <div
        className="card-panel"
        style={{
          padding: "12px 16px",
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
            }}
            title="刷新 Gemini 状态与模型列表"
            style={{ padding: "4px 8px", fontSize: "12px", display: "inline-flex", alignItems: "center", gap: "4px" }}
          >
            <RefreshCw size={13} />
            刷新
          </button>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>Gemini 模型</span>
            <select
              className="input-control"
              value={selectedModel}
              onChange={(e) => handleModelChange(e.target.value)}
              disabled={loading}
              style={{ minWidth: "210px", fontSize: "12px", padding: "5px 8px" }}
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
        </div>
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
                直接告诉 Gemini 你想写的主题、风格或字数要求；生成后可以继续多轮对话改稿、扩写、润色或起标题。
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
          messages.map((msg, idx) => {
            const isUser = msg.role === "user";
            return (
              <div
                key={idx}
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
                        {msg.failedPrompt && (
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
                          onClick={() => copyContent(msg.content, idx)}
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
                          {copiedIdx === idx ? <Check size={11} color="#16A34A" /> : <Copy size={11} />}
                          {copiedIdx === idx ? "已复制" : "复制文章"}
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })
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
    </div>
  );
};
