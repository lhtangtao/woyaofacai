import React, { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";
import { Users, Building2, PieChart } from "lucide-react";
import { api } from "../api/client";
import { BrokerPeoplePnl } from "./BrokerPeoplePnl";

interface EntitiesViewProps {
  type: "people" | "brokers" | "ownership";
  personId: number | null;
}

export const EntitiesView: React.FC<EntitiesViewProps> = ({ type, personId }) => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    let promise: Promise<any>;
    if (type === "people") {
      promise = api.people();
    } else if (type === "brokers") {
      promise = api.brokers();
    } else {
      promise = api.ownership();
    }

    promise
      .then((res) => {
        if (res.status === "ok") {
          setData(res);
        }
      })
      .catch((err) => console.error(`Failed to load ${type}:`, err))
      .finally(() => setLoading(false));
  }, [type, personId]);

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "300px", color: "var(--brand)" }}>
        正在穿透核算实体分布...
      </div>
    );
  }

  const items = data?.items || data?.rows || data?.people || data?.brokers || [];

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", overflowY: "auto" }}>
      <div style={{ fontSize: "14px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
        {type === "people" && <Users size={18} color="var(--brand)" />}
        {type === "brokers" && <Building2 size={18} color="var(--brand)" />}
        {type === "ownership" && <PieChart size={18} color="var(--brand)" />}
        <span>
          {type === "people" && "出资人员资产分布与各人权益核算"}
          {type === "brokers" && "券商真实渠道账户资产、现金与持仓明细"}
          {type === "ownership" && "底层标的股权归属与出资穿透核算"}
        </span>
      </div>

      {type === "brokers" && <BrokerPeoplePnl />}
      <div className="table-container">
        <table className="finance-table">
          <thead>
            <tr>
              {type === "people" && (
                <>
                  <th>出资人员</th>
                  <th style={{ textAlign: "right" }}>总权益金额 (USD)</th>
                  <th style={{ textAlign: "right" }}>权益占比</th>
                  <th>备注</th>
                </>
              )}
              {type === "brokers" && (
                <>
                  <th>券商名称</th>
                  <th style={{ textAlign: "right" }}>股票市值 (USD)</th>
                  <th style={{ textAlign: "right" }}>现金余额 (USD)</th>
                  <th style={{ textAlign: "right" }}>账户总资产 (USD)</th>
                  <th>占比</th>
                </>
              )}
              {type === "ownership" && (
                <>
                  <th>标的代码</th>
                  <th>归属出资人</th>
                  <th style={{ textAlign: "right" }}>持股数量</th>
                  <th style={{ textAlign: "right" }}>持仓市值 (USD)</th>
                  <th style={{ textAlign: "right" }}>归属份额比例</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {items.map((row: any, idx: number) => (
              <tr key={idx}>
                {type === "people" && (
                  <>
                    <td style={{ fontWeight: 600 }}>{row.name || row.person}</td>
                    <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                      ${Number(row.total_asset || row.amount || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right" }}>
                      {row.ratio ? `${(Number(row.ratio) * 100).toFixed(1)}%` : "—"}
                    </td>
                    <td style={{ color: "var(--text-muted)" }}>{row.note || "有效出资份额"}</td>
                  </>
                )}
                {type === "brokers" && (
                  <>
                    <td style={{ fontWeight: 700, color: "var(--text-heading)" }}>{row.broker || row.name}</td>
                    <td className="font-mono" style={{ textAlign: "right" }}>
                      ${Number(row.stock_val || row.stock_value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right" }}>
                      ${Number(row.cash_val || row.cash || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right", fontWeight: 700 }}>
                      ${Number(row.total_val || row.total || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right" }}>
                      {row.percentage ? `${row.percentage}%` : "—"}
                    </td>
                  </>
                )}
                {type === "ownership" && (
                  <>
                    <td style={{ fontWeight: 700 }}>{row.symbol}</td>
                    <td style={{ fontWeight: 600 }}>{row.person_name || row.person}</td>
                    <td className="font-mono" style={{ textAlign: "right" }}>
                      {Number(row.shares || row.quantity || 0).toLocaleString()}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right", fontWeight: 600 }}>
                      ${Number(row.market_value || row.amount || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="font-mono" style={{ textAlign: "right" }}>
                      {row.share_ratio ? `${(Number(row.share_ratio) * 100).toFixed(1)}%` : "100%"}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
