import React, { useEffect, useState, useRef, useMemo } from "react";
import {
  Bot,
  Send,
  Trash2,
  TrendingUp,
  Sparkles,
  User,
  RefreshCw,
  Settings2,
  Copy,
  Check,
  RotateCcw,
  X,
} from "lucide-react";
import { api } from "../api/client";

type AiProvider = "codex" | "gemini" | "google_api";

interface AiViewsProps {
  mode: "chat" | "stock";
  personId: number | null;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  time?: string;
  time_str?: string;
}

interface AgyModelOption {
  id: string;
  label: string;
}

const DEFAULT_AGY_MODELS: AgyModelOption[] = [
  { id: "gemini-4-hydrogen-high", label: "Gemini 4 Hydrogen (High)" },
  { id: "gemini-4-hydrogen-medium", label: "Gemini 4 Hydrogen (Medium)" },
  { id: "gemini-4-hydrogen-xhigh", label: "Gemini 4 Hydrogen (XHigh)" },
  { id: "gemini-4-argon-medium", label: "Gemini 4 Argon (Medium)" },
  { id: "gemini-4-argon-high", label: "Gemini 4 Argon (High)" },
  { id: "gemini-4-neon-medium", label: "Gemini 4 Neon (Medium)" },
  { id: "gemini-4-neon-high", label: "Gemini 4 Neon (High)" },
  { id: "gemini-4-neon-xhigh", label: "Gemini 4 Neon (XHigh)" },
  { id: "gemini-4-carbon-medium", label: "Gemini 4 Carbon (Medium)" },
  { id: "gemini-3.8-flash-medium", label: "Gemini 3.8 Flash (Medium)" },
  { id: "claude-opus-5-5-medium", label: "Claude Opus 5.5 (Medium)" },
  { id: "claude-opus-5-5-high", label: "Claude Opus 5.5 (High)" },
  { id: "claude-sonnet-5-5-medium", label: "Claude Sonnet 5.5 (Medium)" },
  { id: "claude-sonnet-5-5-high", label: "Claude Sonnet 5.5 (High)" },
];

const DEFAULT_GOOGLE_MODELS = [
  "gemini-3.8-flash",
  "gemini-4-hydrogen",
  "gemini-4-neon",
  "gemini-4-argon",
  "gemini-3.7-flash",
  "gemini-3.1-pro-preview",
  "gemini-2.5-flash",
];

function providerLabel(p: AiProvider): string {
  if (p === "gemini") return "Gemini";
  if (p === "google_api") return "Google API";
  return "Codex";
}

/** 轻量安全 Markdown 渲染（支持标题、粗体、行内代码、代码块与列表） */
const MarkdownBlock: React.FC<{ text: string }> = ({ text }) => {
  const renderInline = (line: string, keyPrefix: string): React.ReactNode[] => {
    const parts: React.ReactNode[] = [];
    const regex = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;
    let last = 0;
    let match: RegExpExecArray | null;
    let idx = 0;
    while ((match = regex.exec(line)) !== null) {
      if (match.index > last) {
        parts.push(line.slice(last, match.index));
      }
      const token = match[0];
      if (token.startsWith("`") && token.endsWith("`")) {
        parts.push(
          <code
            key={`${keyPrefix}-c-${idx++}`}
            style={{
              background: "rgba(15, 23, 42, 0.07)",
              padding: "1px 5px",
              borderRadius: "4px",
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              fontSize: "12px",
            }}
          >
            {token.slice(1, -1)}
          </code>
        );
      } else if (token.startsWith("**") && token.endsWith("**")) {
        parts.push(
          <strong key={`${keyPrefix}-b-${idx++}`} style={{ fontWeight: 700 }}>
            {token.slice(2, -2)}
          </strong>
        );
      } else if (token.startsWith("*") && token.endsWith("*")) {
        parts.push(<em key={`${keyPrefix}-i-${idx++}`}>{token.slice(1, -1)}</em>);
      }
      last = regex.lastIndex;
    }
    if (last < line.length) {
      parts.push(line.slice(last));
    }
    return parts;
  };

  const lines = (text || "").split("\n");
  const blocks: React.ReactNode[] = [];
  let inCode = false;
  let codeLines: string[] = [];

  lines.forEach((rawLine, i) => {
    const trimmed = rawLine.trim();
    if (trimmed.startsWith("```")) {
      if (inCode) {
        blocks.push(
          <pre
            key={`code-${i}`}
            style={{
              background: "#0F172A",
              color: "#E2E8F0",
              padding: "10px 12px",
              borderRadius: "8px",
              overflowX: "auto",
              fontSize: "12px",
              lineHeight: 1.5,
              margin: "8px 0",
            }}
          >
            <code>{codeLines.join("\n")}</code>
          </pre>
        );
        codeLines = [];
        inCode = false;
      } else {
        inCode = true;
      }
      return;
    }
    if (inCode) {
      codeLines.push(rawLine);
      return;
    }

    if (!trimmed) {
      blocks.push(<div key={`sp-${i}`} style={{ height: "6px" }} />);
      return;
    }
    if (/^---+$/.test(trimmed)) {
      blocks.push(<hr key={`hr-${i}`} style={{ border: "none", borderTop: "1px solid var(--border-color)", margin: "8px 0" }} />);
      return;
    }
    const hMatch = trimmed.match(/^(#{1,4})\s+(.*)$/);
    if (hMatch) {
      const level = hMatch[1].length;
      const fontSize = level === 1 ? "16px" : level === 2 ? "15px" : "13.5px";
      blocks.push(
        <div key={`h-${i}`} style={{ fontSize, fontWeight: 700, color: "var(--text-heading)", margin: "8px 0 4px" }}>
          {renderInline(hMatch[2], `h-${i}`)}
        </div>
      );
      return;
    }
    const ulMatch = rawLine.match(/^\s*[-*•]\s+(.*)$/);
    if (ulMatch) {
      blocks.push(
        <div key={`ul-${i}`} style={{ display: "flex", gap: "6px", paddingLeft: "6px", margin: "2px 0" }}>
          <span style={{ color: "var(--brand)", fontWeight: 700 }}>•</span>
          <div style={{ flex: 1 }}>{renderInline(ulMatch[1], `ul-${i}`)}</div>
        </div>
      );
      return;
    }
    const olMatch = rawLine.match(/^\s*(\d+\.)\s+(.*)$/);
    if (olMatch) {
      blocks.push(
        <div key={`ol-${i}`} style={{ display: "flex", gap: "6px", paddingLeft: "6px", margin: "2px 0" }}>
          <span style={{ color: "var(--brand)", fontWeight: 600 }}>{olMatch[1]}</span>
          <div style={{ flex: 1 }}>{renderInline(olMatch[2], `ol-${i}`)}</div>
        </div>
      );
      return;
    }
    blocks.push(
      <div key={`p-${i}`} style={{ margin: "2px 0" }}>
        {renderInline(rawLine, `p-${i}`)}
      </div>
    );
  });

  if (inCode && codeLines.length > 0) {
    blocks.push(
      <pre
        key="code-tail"
        style={{
          background: "#0F172A",
          color: "#E2E8F0",
          padding: "10px 12px",
          borderRadius: "8px",
          overflowX: "auto",
          fontSize: "12px",
          margin: "8px 0",
        }}
      >
        <code>{codeLines.join("\n")}</code>
      </pre>
    );
  }

  return <div>{blocks}</div>;
};

export const AiViews: React.FC<AiViewsProps> = ({ mode, personId }) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [symbol, setSymbol] = useState("NVDA");
  const [chatChannel, setChatChannel] = useState("全部渠道");
  const [chatSymbol, setChatSymbol] = useState("全部标的");
  const [includeContext, setIncludeContext] = useState(true);
  const [availableChannels, setAvailableChannels] = useState<string[]>([]);
  const [availableTickers, setAvailableTickers] = useState<string[]>([]);

  const [provider, setProvider] = useState<AiProvider>("gemini");
  const [providersStatus, setProvidersStatus] = useState<Record<string, any>>({});
  const [agyModels, setAgyModels] = useState<AgyModelOption[]>(DEFAULT_AGY_MODELS);
  const [agyModel, setAgyModel] = useState("gemini-4-hydrogen-high");
  const [agyModelsLoaded, setAgyModelsLoaded] = useState(false);

  const [loading, setLoading] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Google API 配置弹窗状态
  const [googleModalOpen, setGoogleModalOpen] = useState(false);
  const [gHasKey, setGHasKey] = useState(false);
  const [gMaskedKey, setGMaskedKey] = useState("");
  const [gKeyInput, setGKeyInput] = useState("");
  const [gShowKey, setGShowKey] = useState(false);
  const [gModels, setGModels] = useState<string[]>(DEFAULT_GOOGLE_MODELS);
  const [gModel, setGModel] = useState("gemini-3.8-flash");
  const [gBudget, setGBudget] = useState("8.50");
  const [gMaxTokens, setGMaxTokens] = useState(4096);
  const [gUsage, setGUsage] = useState<any>(null);
  const [gFetchingModels, setGFetchingModels] = useState(false);
  const [gFetchHint, setGFetchHint] = useState("");
  const [gSaving, setGSaving] = useState(false);

  const refreshStatus = (targetProvider: AiProvider = provider, force = false) => {
    api
      .aiStatus(targetProvider, force)
      .then((res) => {
        if (res.status === "ok" && res.providers) {
          setProvidersStatus(res.providers);
        } else if (res.status === "ok" && res.ai_status) {
          setProvidersStatus((prev) => ({ ...prev, [targetProvider]: res.ai_status }));
        }
        if (res.agy_model) {
          setAgyModel(res.agy_model);
        }
      })
      .catch(() => {});
  };

  const fetchAgyModels = (force = false) => {
    if (agyModelsLoaded && !force) return;
    api
      .aiAgyModels(force)
      .then((res) => {
        if (res.status === "ok") {
          setAgyModelsLoaded(true);
          if (Array.isArray(res.models) && res.models.length > 0) {
            setAgyModels(res.models);
          }
          if (res.current_model) {
            setAgyModel(res.current_model);
          }
        }
      })
      .catch(() => {});
  };

  const fetchHistory = () => {
    const promise =
      mode === "chat"
        ? api.aiChatHistory(chatChannel, chatSymbol)
        : api.aiStockHistory(symbol);
    promise
      .then((res) => {
        if (res.status === "ok") {
          setMessages(res.messages || res.history || []);
        }
      })
      .catch((err) => console.error("Failed to load AI history:", err));
  };

  useEffect(() => {
    api
      .aiScopeOptions()
      .then((res) => {
        if (res.status === "ok") {
          setAvailableChannels(res.channels || []);
          setAvailableTickers(res.tickers || []);
        }
      })
      .catch(() => {});
  }, [personId]);

  useEffect(() => {
    fetchHistory();
  }, [mode, symbol, chatChannel, chatSymbol]);

  useEffect(() => {
    refreshStatus(provider, false);
    if (provider === "gemini") {
      fetchAgyModels(false);
    }
  }, [provider]);

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

  const currentStatus = providersStatus[provider];
  const canSend = useMemo(() => {
    if (loading) return false;
    if (!currentStatus) return true;
    if (provider === "google_api" && currentStatus.budget_exceeded) return false;
    return Boolean(currentStatus.logged_in);
  }, [loading, currentStatus, provider]);

  const handleAgyModelChange = (nextModel: string) => {
    setAgyModel(nextModel);
    api.aiSaveAgyModel(nextModel).catch(() => {});
  };

  const openGoogleModal = () => {
    setGoogleModalOpen(true);
    setGFetchHint("");
    setGKeyInput("");
    setGShowKey(false);
    api
      .aiGoogleApiConfig()
      .then((res) => {
        if (res.status === "ok") {
          setGHasKey(Boolean(res.has_key));
          setGMaskedKey(res.masked_key || "");
          const savedModel = res.model || "gemini-3.8-flash";
          setGModel(savedModel);
          const presets: string[] = Array.isArray(res.preset_models) && res.preset_models.length > 0
            ? res.preset_models
            : DEFAULT_GOOGLE_MODELS;
          setGModels(presets.includes(savedModel) ? presets : [savedModel, ...presets]);
          setGBudget(String(res.monthly_budget_usd || "8.50"));
          setGMaxTokens(Number(res.max_output_tokens || 4096));
          setGUsage(res.usage || null);
        }
      })
      .catch(() => {});
  };

  const handleFetchGoogleModels = async () => {
    if (!gKeyInput.trim() && !gHasKey) {
      alert("请先输入 Google API Key。");
      return;
    }
    setGFetchingModels(true);
    setGFetchHint("");
    try {
      const res = await api.aiGoogleApiFetchModels(gKeyInput.trim() || undefined);
      if (res.status === "ok" && Array.isArray(res.models) && res.models.length > 0) {
        setGModels(res.models);
        if (!res.models.includes(gModel)) {
          setGModel(res.models[0]);
        }
        setGFetchHint(`✅ 已拉取 ${res.models.length} 个可用模型，请在上方下拉框选择`);
      } else {
        alert(res.message || "未查询到可用模型");
      }
    } catch (err: any) {
      alert("拉取模型失败: " + err.message);
    } finally {
      setGFetchingModels(false);
    }
  };

  const handleCalibrateUsage = async () => {
    const currentSpent = Number(gUsage?.estimated_cost_usd || 0).toFixed(4);
    const raw = window.prompt(
      "输入对照 Google Cloud 账单后的本月已用金额 (USD，输入 0 可清零重置):",
      currentSpent
    );
    if (raw === null) return;
    const num = Number(raw);
    if (Number.isNaN(num) || num < 0) {
      alert("请输入有效的非负数字");
      return;
    }
    try {
      const res = await api.aiGoogleApiCalibrate(num.toFixed(4));
      if (res.status === "ok") {
        setGUsage(res.usage);
        if (res.ai_status) {
          setProvidersStatus((prev) => ({ ...prev, google_api: res.ai_status }));
        }
      }
    } catch (err: any) {
      alert("校准失败: " + err.message);
    }
  };

  const handleSaveGoogleConfig = async () => {
    if (!gKeyInput.trim() && !gHasKey) {
      alert("请输入有效的 Google API Key");
      return;
    }
    setGSaving(true);
    try {
      const res = await api.aiSaveGoogleApiConfig({
        api_key: gKeyInput.trim() || undefined,
        model: gModel,
        monthly_budget_usd: gBudget,
        max_output_tokens: gMaxTokens,
      });
      if (res.status === "ok") {
        if (res.ai_status) {
          setProvidersStatus((prev) => ({ ...prev, google_api: res.ai_status }));
        }
        setProvider("google_api");
        setGoogleModalOpen(false);
        refreshStatus("google_api", true);
      } else {
        alert(res.message || "保存失败");
      }
    } catch (err: any) {
      alert("保存失败: " + err.message);
    } finally {
      setGSaving(false);
    }
  };

  const sendPrompt = async (customText?: string) => {
    const userMsg = (customText ?? input).trim();
    if (!userMsg || loading) return;
    if (!canSend) {
      alert("当前 AI 引擎未就绪或已达到预算上限，请先检查配置。");
      return;
    }
    if (customText === undefined) {
      setInput("");
    }
    setMessages((prev) => [
      ...prev,
      { role: "user", content: userMsg, time: new Date().toLocaleTimeString() },
    ]);
    setLoading(true);

    try {
      const selectedModel = provider === "gemini" ? agyModel : undefined;
      let res: any;
      if (mode === "chat") {
        res = await api.aiChat(userMsg, personId, provider, {
          model: selectedModel,
          channel: chatChannel,
          symbol: chatSymbol,
          include_context: includeContext,
        });
      } else {
        res = await api.aiStockChat(symbol, userMsg, provider, selectedModel);
      }
      if (res.status === "ok") {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: res.answer || res.reply || res.content || res.response || "",
            time: new Date().toLocaleTimeString(),
          },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: `⚠️ **调用 ${providerLabel(provider)} 遇到问题**：\n\n${res.message || "请求未成功"}`,
            time: new Date().toLocaleTimeString(),
          },
        ]);
      }
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `⚠️ **调用 ${providerLabel(provider)} 异常**：\n\n${err.message}`,
          time: new Date().toLocaleTimeString(),
        },
      ]);
    } finally {
      setLoading(false);
      if (provider === "google_api") {
        refreshStatus("google_api", true);
      }
    }
  };

  const handleClear = async () => {
    if (!confirm("确定要清空当前频道的 AI 对话记录吗？")) return;
    try {
      if (mode === "chat") {
        await api.aiClear(chatChannel, chatSymbol);
      } else {
        await api.aiStockClear(symbol);
      }
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

  const PRESETS =
    mode === "chat"
      ? [
          "诊断我当前的持仓成本、浮亏与做T降本空间",
          "复盘我的历史已清仓标的，看看哪些卖飞了、哪些成功逃顶",
          "总结我最近的交易习惯、胜率与下一步操作建议",
          "分析我目前的组合杠杆暴露与潜在回撤风险",
        ]
      : [
          `深度诊断 ${symbol} 当前估值、财务健康度与核心护城河`,
          `分析 ${symbol} 最新财报亮点、增长催化剂与潜在下行风险`,
          `对比 ${symbol} 与同行业龙头的技术形态及商业前景`,
        ];

  const renderStatusBadge = () => {
    const st = currentStatus;
    if (!st) {
      return (
        <span style={{ fontSize: "11px", padding: "2px 9px", borderRadius: "10px", background: "#F1F5F9", color: "#64748B" }}>
          正在检测状态...
        </span>
      );
    }
    if (provider === "google_api") {
      if (st.budget_exceeded) {
        return (
          <span style={{ fontSize: "11px", padding: "2px 9px", borderRadius: "10px", background: "#FEE2E2", color: "#991B1B", fontWeight: 600 }}>
            🔴 Google API 预算已达上限 (${st.spent_usd}/${st.budget_usd})
          </span>
        );
      }
      if (st.logged_in && st.budget_warning) {
        return (
          <span style={{ fontSize: "11px", padding: "2px 9px", borderRadius: "10px", background: "#FEF9C3", color: "#854D0E", fontWeight: 600 }}>
            🟡 已连接 Google API ({st.account_type} · 接近上限)
          </span>
        );
      }
      if (st.logged_in) {
        return (
          <span style={{ fontSize: "11px", padding: "2px 9px", borderRadius: "10px", background: "#DCFCE7", color: "#166534", fontWeight: 600 }}>
            🟢 已连接 Google API ({st.account_type})
          </span>
        );
      }
      return (
        <span style={{ fontSize: "11px", padding: "2px 9px", borderRadius: "10px", background: "#FEF9C3", color: "#854D0E" }}>
          🟡 未配置 Google API Key（点击左侧按钮配置）
        </span>
      );
    }

    const ok = Boolean(st.logged_in);
    const inst = Boolean(st.installed);
    const label = providerLabel(provider);
    const text = ok
      ? `🟢 已连接 ${label} (${st.account_type || (provider === "gemini" ? "Antigravity" : "ChatGPT")})`
      : inst
        ? `🟡 未登录 ${label}`
        : `🔴 未安装 ${label} CLI`;
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
        {text}
      </span>
    );
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", overflow: "hidden" }}>
      {/* 1. Top Header Card (与桌面端 1:1 对齐) */}
      <div
        className="card-panel"
        style={{
          padding: "14px 18px",
          display: "flex",
          flexDirection: "column",
          gap: "10px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            {mode === "chat" ? <Bot size={18} color="var(--brand)" /> : <TrendingUp size={18} color="var(--brand)" />}
            <span style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-heading)" }}>
              {mode === "chat" ? "AI 投资顾问 · 智能问答" : "AI 股票分析 · 标的深度研报"}
            </span>

            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value as AiProvider)}
              aria-label="选择 AI 引擎"
              className="input-control"
              style={{ padding: "4px 10px", fontSize: "12px", fontWeight: 600 }}
            >
              <option value="codex">Codex</option>
              <option value="gemini">Gemini</option>
              <option value="google_api">Google API</option>
            </select>

            {provider === "gemini" && (
              <select
                value={agyModel}
                onChange={(e) => handleAgyModelChange(e.target.value)}
                aria-label="选择 Antigravity 模型"
                className="input-control"
                style={{ padding: "4px 10px", fontSize: "12px", maxWidth: "220px" }}
              >
                {agyModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            )}

            {provider === "google_api" && (
              <button
                onClick={openGoogleModal}
                className="btn btn-quiet"
                style={{
                  padding: "4px 10px",
                  fontSize: "12px",
                  border: "1px solid var(--border-color)",
                  background: "var(--brand-selected)",
                }}
                title="配置 Google Gemini API Key、默认模型与月度防超支预算上限"
              >
                <Settings2 size={13} />
                <span>配置 Google API</span>
              </button>
            )}

            {renderStatusBadge()}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <button
              onClick={() => {
                refreshStatus(provider, true);
                if (provider === "gemini") fetchAgyModels(true);
              }}
              className="btn btn-quiet"
              style={{ padding: "4px 10px", fontSize: "12px" }}
              title="刷新引擎状态与模型列表"
            >
              <RefreshCw size={13} />
              <span>刷新状态</span>
            </button>
            <button
              onClick={handleClear}
              className="btn btn-quiet"
              style={{ padding: "4px 10px", fontSize: "12px", color: "#DC2626" }}
              title="清空当前对话历史"
            >
              <Trash2 size={13} />
              <span>清空对话</span>
            </button>
          </div>
        </div>

        <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
          {mode === "chat"
            ? "支持本地 Codex / Gemini (agy) 及 Google API 直连（含月度预算熔断保护）· 各股票/人员/渠道独立多会话隔离"
            : "支持本地 Codex / Gemini (agy) 及 Google API 直连（含月度预算熔断保护）· 聚焦标的基本面与商业前景 · 标的独立对话隔离"}
        </div>

        {/* 筛选条：投资问答（渠道/股票/重置）或 股票分析（锁定标的） */}
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          {mode === "chat" ? (
            <>
              <select
                value={chatChannel}
                onChange={(e) => setChatChannel(e.target.value)}
                className="input-control"
                style={{ padding: "4px 10px", fontSize: "12px", minWidth: "120px" }}
              >
                <option value="全部渠道">全部渠道</option>
                {availableChannels.map((ch) => (
                  <option key={ch} value={ch}>
                    {ch}
                  </option>
                ))}
              </select>

              <select
                value={chatSymbol}
                onChange={(e) => setChatSymbol(e.target.value)}
                className="input-control"
                style={{ padding: "4px 10px", fontSize: "12px", minWidth: "130px" }}
              >
                <option value="全部标的">全部股票</option>
                {availableTickers.map((tk) => (
                  <option key={tk} value={tk}>
                    {tk}
                  </option>
                ))}
              </select>

              <button
                onClick={() => {
                  setChatChannel("全部渠道");
                  setChatSymbol("全部标的");
                }}
                className="btn btn-quiet"
                style={{ padding: "4px 10px", fontSize: "12px" }}
              >
                <RotateCcw size={12} />
                <span>重置</span>
              </button>

              <span style={{ fontSize: "11px", color: "var(--text-subtle)", marginLeft: "4px" }}>
                当前会话频道：{chatChannel} · {chatSymbol === "全部标的" ? "全部股票" : chatSymbol}
              </span>
            </>
          ) : (
            <>
              <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-main)" }}>锁定研究股票:</span>
              <input
                type="text"
                list="ai-stock-tickers-list"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="输入 Ticker，如 NVDA"
                className="input-control font-mono"
                style={{ width: "130px", fontWeight: 700, padding: "4px 8px", fontSize: "12px" }}
              />
              <datalist id="ai-stock-tickers-list">
                {availableTickers.map((tk) => (
                  <option key={tk} value={tk} />
                ))}
              </datalist>
              <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>
                🎯 当前标的：{symbol || "全部股票"}（独立对话隔离）
              </span>
            </>
          )}
        </div>
      </div>

      {/* 2. 常驻快捷提问胶囊（与桌面端一致） */}
      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
        <span style={{ fontSize: "12px", color: "var(--text-muted)", fontWeight: 600 }}>快捷洞察:</span>
        {PRESETS.map((p, i) => (
          <button
            key={i}
            onClick={() => sendPrompt(p)}
            disabled={!canSend}
            className="btn"
            style={{
              padding: "4px 11px",
              fontSize: "12px",
              borderRadius: "16px",
              background: "var(--bg-panel)",
              border: "1px solid var(--border-color)",
            }}
          >
            💡 {p}
          </button>
        ))}
      </div>

      {/* 3. Messages Scroll Area */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: "16px",
          padding: "16px",
          background: "var(--bg-panel)",
          borderRadius: "var(--card-radius)",
          border: "1px solid var(--border-color)",
        }}
      >
        {messages.length === 0 ? (
          <div style={{ margin: "auto", textAlign: "center", maxWidth: "520px", color: "var(--text-muted)" }}>
            <Sparkles size={36} color="var(--brand)" style={{ marginBottom: "12px" }} />
            <div style={{ fontSize: "15px", fontWeight: 600, color: "var(--text-heading)", marginBottom: "6px" }}>
              {mode === "chat" ? "你好！我是你的专属 AI 财务投资顾问" : `开始对 ${symbol} 进行深度基本面研报分析`}
            </div>
            <p style={{ fontSize: "13px", lineHeight: "1.6", marginBottom: "12px" }}>
              {mode === "chat"
                ? "基于本地真实复式记账账本与逐笔成交流水，随时为你诊断持仓成本、收益归因与做T风控策略。"
                : "聚焦标的公司商业模式、财报核心指标、行业竞争壁垒与未来催化剂，与个人账本完全解耦。"}
            </p>
          </div>
        ) : (
          messages.map((m, idx) => {
            const isUser = m.role === "user";
            return (
              <div
                key={idx}
                style={{
                  display: "flex",
                  gap: "10px",
                  alignSelf: isUser ? "flex-end" : "flex-start",
                  maxWidth: "86%",
                }}
              >
                {!isUser && (
                  <div
                    style={{
                      width: "32px",
                      height: "32px",
                      borderRadius: "50%",
                      background: "var(--brand-selected)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                    }}
                  >
                    <Bot size={18} color="var(--brand)" />
                  </div>
                )}
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      padding: "12px 16px",
                      borderRadius: "12px",
                      background: isUser ? "var(--brand)" : "var(--table-alternate)",
                      color: isUser ? "#FFFFFF" : "var(--text-main)",
                      border: isUser ? "none" : "1px solid var(--border-color)",
                      fontSize: "13px",
                      lineHeight: "1.65",
                      wordBreak: "break-word",
                    }}
                  >
                    {isUser ? (
                      <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
                    ) : (
                      <MarkdownBlock text={m.content} />
                    )}
                  </div>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: isUser ? "flex-end" : "space-between",
                      gap: "10px",
                      marginTop: "4px",
                      fontSize: "11px",
                      color: "var(--text-subtle)",
                    }}
                  >
                    {(m.time_str || m.time) && <span>{m.time_str || m.time}</span>}
                    {!isUser && (
                      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        {(m.content.startsWith("⚠️") || m.content.startsWith("❌") || m.content.startsWith("⏸️")) && (() => {
                          let prevPrompt = "";
                          for (let j = idx - 1; j >= 0; j--) {
                            if (messages[j].role === "user" && messages[j].content.trim()) {
                              prevPrompt = messages[j].content.trim();
                              break;
                            }
                          }
                          if (!prevPrompt) return null;
                          return (
                            <button
                              onClick={() => sendPrompt(prevPrompt)}
                              disabled={!canSend}
                              className="btn btn-quiet"
                              style={{
                                padding: "1px 8px",
                                fontSize: "11px",
                                color: "#2563EB",
                                border: "1px solid #BFDBFE",
                                background: "#EFF6FF",
                                fontWeight: 600,
                              }}
                              title="使用当前引擎重新发送上一条提问"
                            >
                              <RotateCcw size={11} />
                              <span>重试</span>
                            </button>
                          );
                        })()}
                        <button
                          onClick={() => copyContent(m.content, idx)}
                          className="btn btn-quiet"
                          style={{ padding: "1px 6px", fontSize: "11px" }}
                        >
                          {copiedIdx === idx ? <Check size={11} /> : <Copy size={11} />}
                          <span>{copiedIdx === idx ? "已复制" : "复制回答"}</span>
                        </button>
                      </div>
                    )}
                  </div>
                </div>
                {isUser && (
                  <div
                    style={{
                      width: "32px",
                      height: "32px",
                      borderRadius: "50%",
                      background: "var(--border-color)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                    }}
                  >
                    <User size={18} color="var(--text-main)" />
                  </div>
                )}
              </div>
            );
          })
        )}
        {loading && (
          <div style={{ display: "flex", gap: "10px", alignSelf: "flex-start", alignItems: "center" }}>
            <div
              style={{
                width: "32px",
                height: "32px",
                borderRadius: "50%",
                background: "var(--brand-selected)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Bot size={18} color="var(--brand)" />
            </div>
            <div
              style={{
                padding: "10px 14px",
                borderRadius: "12px",
                background: "var(--table-alternate)",
                border: "1px solid var(--border-color)",
                fontSize: "12.5px",
                color: "var(--text-muted)",
              }}
            >
              🤖 {providerLabel(provider)} 正在思考并分析数据中...（已耗时 {elapsedSec} 秒）
            </div>
          </div>
        )}
      </div>

      {/* 4. Bottom Input Area */}
      <div className="card-panel" style={{ padding: "10px 14px", display: "flex", flexDirection: "column", gap: "8px" }}>
        {mode === "chat" && (
          <label
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              fontSize: "12px",
              color: "var(--text-muted)",
              cursor: "pointer",
              userSelect: "none",
            }}
          >
            <input
              type="checkbox"
              checked={includeContext}
              onChange={(e) => setIncludeContext(e.target.checked)}
            />
            <span>携带当前组合真实背景（向 AI 注入当前筛选范围的真实成本、持仓与逐笔流水）</span>
          </label>
        )}
        <div style={{ display: "flex", gap: "10px", alignItems: "flex-end" }}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              mode === "chat"
                ? "输入您的投资提问、成本分析或策略需求... (⌘+Enter 发送)"
                : `输入关于 ${symbol} 的财报、估值或商业模式提问... (⌘+Enter 发送)`
            }
            rows={2}
            disabled={loading}
            className="input-control"
            style={{ flex: 1, resize: "none", fontSize: "13px", padding: "8px 12px" }}
          />
          <button
            onClick={() => sendPrompt()}
            disabled={!canSend || !input.trim()}
            className="btn btn-primary"
            style={{ height: "42px", padding: "0 18px", fontWeight: 600 }}
          >
            <Send size={14} />
            <span>发送</span>
          </button>
        </div>
      </div>

      {/* 5. Google API 配置与月度预算保护弹窗 */}
      {googleModalOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15, 23, 42, 0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "16px",
          }}
          onClick={() => setGoogleModalOpen(false)}
        >
          <div
            className="card-panel"
            style={{
              width: "100%",
              maxWidth: "560px",
              padding: "20px 22px",
              display: "flex",
              flexDirection: "column",
              gap: "14px",
              boxShadow: "0 20px 50px rgba(15, 23, 42, 0.25)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-heading)" }}>
                配置 Google API (Gemini) 与预算保护
              </span>
              <button onClick={() => setGoogleModalOpen(false)} className="btn btn-quiet" style={{ padding: "4px" }}>
                <X size={16} />
              </button>
            </div>

            <div
              style={{
                fontSize: "12px",
                color: "var(--text-muted)",
                background: "var(--bg-subtle)",
                padding: "10px 12px",
                borderRadius: "8px",
                lineHeight: 1.55,
              }}
            >
              直接通过 Google Gemini API 调用模型（与桌面端共享本地配置与月度用量统计）。内置本地 Token 计费估算与月度硬性预算熔断，防止超出每月 $10 额度产生额外扣费。
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: "10px 12px", alignItems: "center" }}>
              <label style={{ fontSize: "12.5px", fontWeight: 600 }}>API Key:</label>
              <div style={{ display: "flex", gap: "6px" }}>
                <input
                  type={gShowKey ? "text" : "password"}
                  value={gKeyInput}
                  onChange={(e) => setGKeyInput(e.target.value)}
                  placeholder={
                    gHasKey
                      ? `已配置 (${gMaskedKey})，留空则保持原 Key 不变`
                      : "粘贴 Google AI Studio API Key (AIza...)"
                  }
                  className="input-control"
                  style={{ flex: 1, fontSize: "12.5px", padding: "6px 10px" }}
                />
                <button
                  type="button"
                  onClick={() => setGShowKey((v) => !v)}
                  className="btn btn-quiet"
                  style={{ fontSize: "12px", padding: "4px 10px", border: "1px solid var(--border-color)" }}
                >
                  {gShowKey ? "隐藏" : "显示"}
                </button>
              </div>

              <label style={{ fontSize: "12.5px", fontWeight: 600 }}>默认模型:</label>
              <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                <div style={{ display: "flex", gap: "6px" }}>
                  <select
                    value={gModel}
                    onChange={(e) => setGModel(e.target.value)}
                    className="input-control"
                    style={{ flex: 1, fontSize: "12.5px", padding: "6px 10px" }}
                  >
                    {gModels.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={handleFetchGoogleModels}
                    disabled={gFetchingModels}
                    className="btn btn-quiet"
                    style={{ fontSize: "12px", padding: "4px 10px", border: "1px solid var(--border-color)" }}
                  >
                    {gFetchingModels ? "拉取中..." : "拉取最新模型"}
                  </button>
                </div>
                {gFetchHint && <span style={{ fontSize: "11px", color: "#15803D" }}>{gFetchHint}</span>}
              </div>

              <label style={{ fontSize: "12.5px", fontWeight: 600 }}>月度安全上限:</label>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <input
                  type="number"
                  min={0.5}
                  max={10}
                  step={0.5}
                  value={gBudget}
                  onChange={(e) => setGBudget(e.target.value)}
                  className="input-control"
                  style={{ width: "110px", fontSize: "12.5px", padding: "6px 10px" }}
                />
                <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>USD / 月（达到上限自动熔断拦截）</span>
              </div>

              <label style={{ fontSize: "12.5px", fontWeight: 600 }}>单次输出上限:</label>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <input
                  type="number"
                  min={512}
                  max={32768}
                  step={512}
                  value={gMaxTokens}
                  onChange={(e) => setGMaxTokens(Number(e.target.value) || 4096)}
                  className="input-control"
                  style={{ width: "110px", fontSize: "12.5px", padding: "6px 10px" }}
                />
                <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>tokens（防单次超长输出烧额度）</span>
              </div>
            </div>

            {gUsage && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "10px",
                  padding: "8px 12px",
                  borderRadius: "8px",
                  background: "var(--table-alternate)",
                  border: "1px solid var(--border-color)",
                  fontSize: "12px",
                }}
              >
                <span>
                  本月 ({gUsage.month})：已请求 <b>{gUsage.requests || 0}</b> 次 · 输入{" "}
                  {(gUsage.prompt_tokens || 0).toLocaleString()} / 输出 {(gUsage.output_tokens || 0).toLocaleString()}{" "}
                  tokens · 预估已用 <b>${Number(gUsage.estimated_cost_usd || 0).toFixed(4)}</b>
                </span>
                <button
                  type="button"
                  onClick={handleCalibrateUsage}
                  className="btn btn-quiet"
                  style={{ fontSize: "11.5px", padding: "3px 8px", border: "1px solid var(--border-color)", flexShrink: 0 }}
                >
                  校准本月已用
                </button>
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "4px" }}>
              <button onClick={() => setGoogleModalOpen(false)} className="btn btn-quiet">
                取消
              </button>
              <button onClick={handleSaveGoogleConfig} disabled={gSaving} className="btn btn-primary">
                {gSaving ? "保存中..." : "保存配置"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
