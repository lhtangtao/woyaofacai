"""Money3本地同步组件；从既有账本适配器迁入并裁剪非同步入口。"""

from __future__ import annotations
import pandas as pd


from portfolio.sync.order_id_utils import normalize_order_id


TICKER_ALIASES = {"SKHYV": "SKHY"}


EPSILON = 1e-4


DISPLAY_QUANTITY_ZERO_EPSILON = 0.005


TRADE_DETAILS_COLUMNS = [
    "交易时间",
    "渠道",
    "分类",
    "Ticker",
    "方向",
    "成交数量",
    "成交均价($)",
    "成交金额($)",
    "后持仓数量",
    "后累计净投入($)",
    "后累计净投入均价($)",
    "交易类型",
    "划转对象",
    "订单ID",
    "微操标记",
    "数据来源",
]


def _number(value: object) -> float:
    if pd.isna(value):
        return 0.0
    text = str(value).strip().replace("$", "").replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    v = pd.to_numeric(text, errors="coerce")
    return 0.0 if pd.isna(v) else float(v)


def _ticker(value: object) -> str:
    ticker = str(value or "").strip().upper().split(".")[0].replace(".0", "")
    return TICKER_ALIASES.get(ticker, ticker)


def replay_trade_details(
    operations: pd.DataFrame,
    actions: pd.DataFrame,
    person_name: str = "个人",
    tag_map: dict[int, str] | None = None,
) -> tuple[pd.DataFrame, dict[tuple[str, str], dict]]:
    """逐笔重放交易明细，计算后持仓数量、后累计净投入与后累计净投入均价（真实净投入保本价）。"""
    if operations.empty:
        return pd.DataFrame(columns=TRADE_DETAILS_COLUMNS), {}

    ops = operations.copy()
    ops["_source_index"] = range(len(ops))
    ops["_trade_time"] = pd.to_datetime(ops["交易时间"], errors="coerce")
    ops = (
        ops.dropna(subset=["_trade_time"])
        .sort_values(["_trade_time", "_source_index"], kind="stable")
        .reset_index(drop=True)
    )

    events = []
    for _, r in ops.iterrows():
        events.append(
            {
                "time": r["_trade_time"],
                "type": "trade",
                "row": r,
                "source_index": int(r["_source_index"]),
            }
        )

    if not actions.empty:
        for _, act in actions.iterrows():
            events.append(
                {
                    "time": act["生效日期"],
                    "type": "action",
                    "ticker": act["Ticker"],
                    "channel": act["适用渠道"],
                    "ratio": float(act["比例"]),
                }
            )

    events.sort(key=lambda x: (x["time"], 0 if x["type"] == "action" else 1))

    holdings_state: dict[tuple[str, str], dict] = {}
    output_rows = []
    for ev in events:
        if ev["type"] == "action":
            t = ev["ticker"]
            ch = ev["channel"]
            ratio = ev["ratio"]
            for (broker, ticker), pos in holdings_state.items():
                if ticker == t and (ch in ("ALL", broker)):
                    pos["quantity"] *= ratio
                    pos["buy_quantity"] *= ratio
                    pos["sell_quantity"] *= ratio
            continue

        r = ev["row"]
        src_idx = ev["source_index"]
        broker = str(r["渠道"]).strip().upper()
        ticker = _ticker(r["Ticker"])
        side = str(r["方向"]).strip()
        qty = _number(r["成交数量"])
        price = _number(r["成交均价($)"])
        amt = _number(r["成交金额($)"])
        cat = str(r.get("分类", "正股")).strip()

        key = (broker, ticker)
        pos = holdings_state.setdefault(
            key,
            {
                "quantity": 0.0,
                "buy_quantity": 0.0,
                "sell_quantity": 0.0,
                "buy_amount": 0.0,
                "sell_amount": 0.0,
                "first_trade_time": r["_trade_time"],
                "last_trade_time": r["_trade_time"],
                "trade_count": 0,
            },
        )

        pos["last_trade_time"] = r["_trade_time"]
        pos["trade_count"] += 1

        is_buy = False
        is_sell = False
        if side in ("买入", "转入"):
            is_buy = True
        elif side in ("卖出", "转出"):
            is_sell = True
        elif side == "他人→个人":
            is_buy = person_name == "个人"
            is_sell = not is_buy
        elif side == "个人→他人":
            is_sell = person_name == "个人"
            is_buy = not is_sell

        if is_buy:
            pos["quantity"] += qty
            pos["buy_quantity"] += qty
            pos["buy_amount"] += amt
        elif is_sell:
            pos["quantity"] -= qty
            pos["sell_quantity"] += qty
            pos["sell_amount"] += amt

        curr_qty = round(pos["quantity"], 4)
        if abs(curr_qty) < EPSILON:
            curr_qty = 0.0

        net_inv = round(pos["buy_amount"] - pos["sell_amount"], 2)
        avg_cost = round(net_inv / curr_qty, 2) if curr_qty > EPSILON else None
        tag = tag_map.get(src_idx, "") if tag_map else ""

        output_rows.append(
            {
                "交易时间": r["_trade_time"].strftime("%Y-%m-%d %H:%M:%S"),
                "渠道": broker,
                "分类": cat,
                "Ticker": ticker,
                "方向": side,
                "成交数量": qty,
                "成交均价($)": price,
                "成交金额($)": amt,
                "微操标记": tag,
                "后持仓数量": curr_qty,
                "后累计净投入($)": net_inv,
                "后累计净投入均价($)": avg_cost if avg_cost is not None else "-",
                "交易类型": str(r.get("交易类型", "市场交易")),
                "划转对象": str(r.get("划转对象", "")).replace("nan", "")
                if pd.notna(r.get("划转对象"))
                else "",
                "订单ID": normalize_order_id(r.get("订单ID")),
                "数据来源": str(r.get("数据来源", "")),
            }
        )

    # 仅对重放结束后仍为负数的标的告警；中途短暂负数、最终已回补的记录不提示。
    final_negative_positions = [
        (broker, ticker, position)
        for (broker, ticker), position in holdings_state.items()
        if _normalized_final_quantity(position.get("quantity")) < 0
    ]
    if final_negative_positions:
        print(f"[WARN] [负数持仓] {person_name} 存在最终持仓为负的标的，需人工核对：")
        for broker, ticker, position in sorted(final_negative_positions):
            when = pd.Timestamp(position["last_trade_time"]).strftime("%Y-%m-%d %H:%M:%S")
            qty = _normalized_final_quantity(position["quantity"])
            net_inv = _number(position["buy_amount"]) - _number(position["sell_amount"])
            print(f"  - {when} | {broker} | {ticker} | 最终持仓 {qty:.2f} 股 | 累计净投入 {net_inv:,.2f}")

    df_details = pd.DataFrame(output_rows)
    if not df_details.empty:
        df_details = df_details.sort_values("交易时间", ascending=False).reset_index(drop=True)
    return df_details, holdings_state


def _normalized_final_quantity(value) -> float:
    """按报表股数显示精度消除浮点残差，避免把 -0.00 误报为负持仓。"""
    quantity = _number(value)
    return 0.0 if abs(quantity) < DISPLAY_QUANTITY_ZERO_EPSILON else quantity
