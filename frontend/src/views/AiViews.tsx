import React, { useEffect, useState, useRef } from "react";
import { Bot, Send, Trash2, TrendingUp, Sparkles, User } from "lucide-react";
import { api } from "../api/client";

interface AiViewsProps {
  mode: "chat" | "stock";
  personId: number | null;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  time?: string;
}

export const AiViews: React.FC<AiViewsProps> = ({ mode, personId }) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [symbol, setSymbol] = useState("NVDA");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const fetchHistory = () => {
    const promise = mode === "chat" ? api.aiChatHistory() : api.aiStockHistory();
    promise
      .then((res) => {
        if (res.status === "ok") {
          setMessages(res.messages || res.history || []);
        }
      })
      .catch((err) => console.error("Failed to load AI history:", err));
  };

  useEffect(() => {
    fetchHistory();
  }, [mode]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || loading) return;
    const userMsg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMsg, time: new Date().toLocaleTimeString() }]);
    setLoading(true);

    try {
      let res: any;
      if (mode === "chat") {
        res = await api.aiChat(userMsg, personId);
      } else {
        res = await api.aiStockChat(symbol, userMsg);
      }
      if (res.status === "ok") {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: res.reply || res.content || res.response, time: new Date().toLocaleTimeString() },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `❌ 发生错误: ${res.message || "请求未成功"}`, time: new Date().toLocaleTimeString() },
        ]);
      }
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `❌ 交互异常: ${err.message}`, time: new Date().toLocaleTimeString() },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleClear = async () => {
    if (!confirm("确定要清空当前的 AI 对话记录吗？")) return;
    try {
      await api.aiClear();
      setMessages([]);
    } catch (err: any) {
      alert("清空失败: " + err.message);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      handleSend();
    }
  };

  const PRESETS =
    mode === "chat"
      ? [
          "分析我目前的组合杠杆暴露与潜在风险",
          "持仓中哪些标的回撤最大，持仓成本与盈亏比如何？",
          "本月累计收益与最大回撤日复盘",
          "当前现金与正股配置比例是否稳健？",
        ]
      : [
          `深度诊断 ${symbol} 当前估值与支撑位`,
          `${symbol} 财报前后历史波动规律与预期`,
          `对比 ${symbol} 与同行业龙头的技术形态`,
        ];

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", overflow: "hidden" }}>
      {/* Top Banner */}
      <div
        className="card-panel"
        style={{
          padding: "10px 16px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          {mode === "chat" ? <Bot size={18} color="var(--brand)" /> : <TrendingUp size={18} color="var(--brand)" />}
          <span style={{ fontSize: "14px", fontWeight: 700 }}>
            {mode === "chat" ? "AI 智能投资问答与组合顾问" : "AI 单标的深度技术与基本面研报"}
          </span>
          {mode === "stock" && (
            <div style={{ display: "flex", alignItems: "center", gap: "6px", marginLeft: "12px" }}>
              <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>研报标的:</span>
              <input
                type="text"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                className="input-control font-mono"
                style={{ width: "90px", fontWeight: 700, padding: "3px 6px" }}
              />
            </div>
          )}
        </div>

        <button onClick={handleClear} className="btn btn-quiet" title="清空对话历史">
          <Trash2 size={14} />
          <span>清空对话</span>
        </button>
      </div>

      {/* Messages Scroll Area */}
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
          <div style={{ margin: "auto", textAlign: "center", maxWidth: "500px", color: "var(--text-muted)" }}>
            <Sparkles size={36} color="var(--brand)" style={{ marginBottom: "12px" }} />
            <div style={{ fontSize: "15px", fontWeight: 600, color: "var(--text-heading)", marginBottom: "6px" }}>
              你好！我是你的专属 AI 财务投资顾问
            </div>
            <p style={{ fontSize: "13px", lineHeight: "1.6", marginBottom: "16px" }}>
              基于底层真实复式记账账本与实时行情，随时为你解答持仓配置、收益归因、风险敞口与标的深度分析。
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {PRESETS.map((p, i) => (
                <button
                  key={i}
                  onClick={() => {
                    setInput(p);
                  }}
                  className="btn"
                  style={{ textAlign: "left", fontSize: "12px" }}
                >
                  💡 {p}
                </button>
              ))}
            </div>
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
                  maxWidth: "80%",
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
                <div>
                  <div
                    style={{
                      padding: "12px 16px",
                      borderRadius: "12px",
                      background: isUser ? "var(--brand)" : "var(--table-alternate)",
                      color: isUser ? "#FFFFFF" : "var(--text-main)",
                      border: isUser ? "none" : "1px solid var(--border-color)",
                      fontSize: "13px",
                      lineHeight: "1.6",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                    }}
                  >
                    {m.content}
                  </div>
                  {m.time && (
                    <div
                      style={{
                        fontSize: "11px",
                        color: "var(--text-subtle)",
                        marginTop: "4px",
                        textAlign: isUser ? "right" : "left",
                      }}
                    >
                      {m.time}
                    </div>
                  )}
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
          <div style={{ display: "flex", gap: "10px", alignSelf: "flex-start" }}>
            <div style={{ width: "32px", height: "32px", borderRadius: "50%", background: "var(--brand-selected)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Bot size={18} color="var(--brand)" />
            </div>
            <div style={{ padding: "12px 16px", borderRadius: "12px", background: "var(--table-alternate)", border: "1px solid var(--border-color)", color: "var(--brand)", fontSize: "13px" }}>
              AI 正在穿透核算账本并生成研报...
            </div>
          </div>
        )}
      </div>

      {/* Input Box Row */}
      <div
        className="card-panel"
        style={{
          padding: "10px 14px",
          display: "flex",
          gap: "10px",
          alignItems: "center",
        }}
      >
        <textarea
          rows={2}
          placeholder="提出你的投资问题... (⌘+Enter 发送)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          className="input-control"
          style={{ flex: 1, resize: "none", height: "50px", lineHeight: "1.5" }}
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim()}
          className="btn btn-primary"
          style={{ height: "50px", padding: "0 20px" }}
        >
          <Send size={16} />
          <span>发送</span>
        </button>
      </div>
    </div>
  );
};
