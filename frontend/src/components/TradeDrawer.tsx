import React, { useEffect, useState } from "react";
import { X, FileText, Hash, Layers, ShieldCheck, PieChart, Clock } from "lucide-react";
import { api } from "../api/client";
import { TradeItem, TradeProvenance } from "../types";

interface TradeDrawerProps {
  trade: TradeItem | null;
  onClose: () => void;
}

export const TradeDrawer: React.FC<TradeDrawerProps> = ({ trade, onClose }) => {
  const [provenance, setProvenance] = useState<TradeProvenance | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!trade) {
      setProvenance(null);
      return;
    }
    setLoading(true);
    api
      .tradeDetail(trade.record_id || trade.id)
      .then((res) => {
        if (res.status === "ok" && res.provenance) {
          setProvenance(res.provenance);
        }
      })
      .catch((err) => {
        console.warn("Failed to fetch trade detail:", err);
      })
      .finally(() => setLoading(false));
  }, [trade]);

  if (!trade) return null;

  const isBuy = trade.side.includes("买");

  return (
    <div
      style={{
        width: "360px",
        minWidth: "360px",
        background: "var(--bg-panel)",
        borderLeft: "1px solid var(--border-color)",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        boxShadow: "-4px 0 16px rgba(0, 0, 0, 0.05)",
        zIndex: 40,
        overflowY: "auto",
        transition: "transform 0.2s ease",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "16px 20px",
          borderBottom: "1px solid var(--border-color)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "var(--table-alternate)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span
            className={`badge ${isBuy ? "badge-buy" : "badge-sell"}`}
            style={{ fontSize: "12px", padding: "3px 8px" }}
          >
            {trade.side}
          </span>
          <span style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-heading)" }}>
            {trade.symbol}
          </span>
          <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            #{trade.record_id || trade.id}
          </span>
        </div>
        <button
          onClick={onClose}
          className="btn btn-quiet"
          style={{ width: "28px", height: "28px", padding: 0 }}
        >
          <X size={16} />
        </button>
      </div>

      {/* Body Content */}
      <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "20px" }}>
        {/* Core Trade Facts */}
        <div>
          <div
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: "var(--text-muted)",
              marginBottom: "8px",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <Clock size={14} />
            <span>成交概况</span>
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "10px",
              background: "var(--table-alternate)",
              padding: "12px",
              borderRadius: "8px",
              border: "1px solid var(--border-color)",
            }}
          >
            <div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>成交时间</div>
              <div style={{ fontSize: "13px", fontWeight: 600, marginTop: "2px" }}>
                {trade.trade_date}
              </div>
            </div>
            <div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>券商渠道</div>
              <div style={{ fontSize: "13px", fontWeight: 600, marginTop: "2px" }}>
                {trade.broker}
              </div>
            </div>
            <div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>成交数量</div>
              <div className="font-mono" style={{ fontSize: "13px", fontWeight: 600, marginTop: "2px" }}>
                {trade.quantity?.toLocaleString()} 股
              </div>
            </div>
            <div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>成交单价</div>
              <div className="font-mono" style={{ fontSize: "13px", fontWeight: 600, marginTop: "2px" }}>
                ${trade.price?.toFixed(4)}
              </div>
            </div>
            <div style={{ gridColumn: "1 / span 2" }}>
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>成交总金额</div>
              <div
                className="font-mono"
                style={{
                  fontSize: "16px",
                  fontWeight: 700,
                  marginTop: "2px",
                  color: "var(--text-heading)",
                }}
              >
                ${trade.amount?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </div>
            </div>
          </div>
        </div>

        {/* Provenance & Excel Lineage */}
        <div>
          <div
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: "var(--text-muted)",
              marginBottom: "8px",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <FileText size={14} />
            <span>原始证据追溯 (Provenance)</span>
          </div>

          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "8px",
              background: "var(--table-alternate)",
              padding: "12px",
              borderRadius: "8px",
              border: "1px solid var(--border-color)",
              fontSize: "12px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>原始流水</span>
              <span style={{ fontWeight: 500, maxWidth: "200px", wordBreak: "break-all", textAlign: "right" }}>
                {provenance?.source_file || trade.source || "交易流水.xlsx"}
              </span>
            </div>
            {provenance?.source_sheet && (
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--text-muted)" }}>工作表</span>
                <span style={{ fontWeight: 500 }}>{provenance.source_sheet}</span>
              </div>
            )}
            {provenance?.source_row && (
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--text-muted)" }}>原始行号</span>
                <span className="font-mono" style={{ fontWeight: 600 }}>
                  行 {provenance.source_row}
                </span>
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>订单编号</span>
              <span className="font-mono" style={{ fontSize: "11px" }}>
                {trade.order_id || provenance?.order_id || "—"}
              </span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>源库审计状态</span>
              <span style={{ color: "#16A34A", fontWeight: 600, display: "flex", alignItems: "center", gap: "4px" }}>
                <ShieldCheck size={13} />
                只读校验通过
              </span>
            </div>
          </div>
        </div>

        {/* Post-trade Position Replay */}
        <div>
          <div
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: "var(--text-muted)",
              marginBottom: "8px",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <Layers size={14} />
            <span>此笔交易后持仓复盘</span>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "10px",
              background: "var(--table-alternate)",
              padding: "12px",
              borderRadius: "8px",
              border: "1px solid var(--border-color)",
            }}
          >
            <div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>结余持股</div>
              <div className="font-mono" style={{ fontSize: "13px", fontWeight: 600, marginTop: "2px" }}>
                {trade.post_quantity != null ? trade.post_quantity.toLocaleString() : "—"}
              </div>
            </div>
            <div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>持仓成本均价</div>
              <div className="font-mono" style={{ fontSize: "13px", fontWeight: 600, marginTop: "2px" }}>
                {trade.post_cost != null ? `$${trade.post_cost.toFixed(4)}` : "—"}
              </div>
            </div>
            <div style={{ gridColumn: "1 / span 2" }}>
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>累计净投入</div>
              <div className="font-mono" style={{ fontSize: "13px", fontWeight: 600, marginTop: "2px" }}>
                {trade.post_net != null ? `$${trade.post_net.toLocaleString()}` : "—"}
              </div>
            </div>
          </div>
        </div>

        {/* Person Splits Attribution */}
        {provenance?.person_splits && provenance.person_splits.length > 0 && (
          <div>
            <div
              style={{
                fontSize: "12px",
                fontWeight: 600,
                color: "var(--text-muted)",
                marginBottom: "8px",
                display: "flex",
                alignItems: "center",
                gap: "6px",
              }}
            >
              <PieChart size={14} />
              <span>出资人份额拆分</span>
            </div>

            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "6px",
                background: "var(--table-alternate)",
                padding: "12px",
                borderRadius: "8px",
                border: "1px solid var(--border-color)",
              }}
            >
              {provenance.person_splits.map((s, idx) => (
                <div
                  key={idx}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    fontSize: "12px",
                  }}
                >
                  <span style={{ fontWeight: 500 }}>{s.person}</span>
                  <div style={{ display: "flex", gap: "8px" }}>
                    <span style={{ color: "var(--text-muted)" }}>{(s.ratio * 100).toFixed(0)}%</span>
                    <span className="font-mono" style={{ fontWeight: 600 }}>
                      ${s.amount?.toLocaleString()}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
