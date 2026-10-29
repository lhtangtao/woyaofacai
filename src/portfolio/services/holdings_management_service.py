"""Unified holdings management service powering both Desktop and Web.

Provides:
1. Holdings dashboard totals, breakdowns by person/broker/stock, and distribution donut data.
2. Unified holdings detail items supporting person, broker, and stock viewing dimensions.
3. Stock right drawer details:
   - Top metrics (quantity, cost, current price, market value, floating pnl, cumulative pnl)
   - 11-column trade history with cost changes and latest quote
   - Holding analysis (quantity and cost trajectory)
   - PnL analysis (cumulative return curve with buy/sell execution markers)
   - Monthly performance breakdown
"""
import logging
import sqlite3
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import time
from typing import Any

from portfolio.domain.analytics import ZERO, ratio, strict_sum
from portfolio.domain.models import QueryScope
from portfolio.paths import DATABASE_PATH
from portfolio.services.post_trade_positions import attach_post_trade_positions
from portfolio.services.trades_service import TradesService

logger = logging.getLogger(__name__)


def serialize_val(val: Any) -> Any:
    """Format Decimal or numbers safely for JSON or UI display."""
    if val is None:
        return None
    if isinstance(val, Decimal):
        if val.is_nan() or val.is_infinite():
            return None
        return float(val)
    return val


class HoldingsManagementService:
    @staticmethod
    def get_dashboard_data(analytics, scope: QueryScope | None = None, person_id: int | None = None) -> dict[str, Any]:
        """Compute top 4 cards, person/broker/stock summaries, and distribution data."""
        start_t = time.monotonic()
        if not analytics or not hasattr(analytics, "reports"):
            return {
                "cards": {},
                "person_summary": [],
                "broker_summary": [],
                "stock_summary": [],
                "distribution": {"by_person": [], "by_broker": [], "by_stock": []},
            }

        pa = analytics.reports.get("人员资产")
        th = analytics.reports.get("当前持仓")
        ch = analytics.reports.get("清仓收益")
        sh = analytics.reports.get("股票汇总")
        qh = analytics.reports.get("渠道汇总")

        pa_rows = pa.rows if pa else ()
        if person_id is not None and pa_rows:
            pa_rows = tuple(r for r in pa_rows if r.metadata.get("person_id") == person_id)

        th_rows = th.rows if th else ()
        ch_rows = ch.rows if ch else ()
        sh_rows = sh.rows if sh else ()
        qh_rows = qh.rows if qh else ()

        # --- Top 4 Cards ---
        total_market_value = sum(
            (r.values[7] for r in pa_rows if r.values[4] == "持有中" and isinstance(r.values[7], Decimal)),
            Decimal(0),
        )
        total_floating_pnl = sum(
            (r.values[8] for r in pa_rows if r.values[4] == "持有中" and isinstance(r.values[8], Decimal)),
            Decimal(0),
        )
        total_closed_pnl = sum(
            (r.values[8] for r in pa_rows if r.values[4] == "已清仓" and isinstance(r.values[8], Decimal)),
            Decimal(0),
        )
        total_cumulative_pnl = total_floating_pnl + total_closed_pnl

        # Net invested / Total capital
        open_net = sum(
            (r.values[12] for r in pa_rows if r.values[4] == "持有中" and isinstance(r.values[12], Decimal)),
            Decimal(0),
        )
        closed_net = sum(
            (r.values[12] for r in pa_rows if r.values[4] == "已清仓" and isinstance(r.values[12], Decimal)),
            Decimal(0),
        )
        total_invested = open_net + closed_net
        if total_invested <= 0:
            total_invested = total_market_value - total_cumulative_pnl

        floating_pnl_rate = ratio(total_floating_pnl, open_net) if open_net > 0 else None
        cumulative_pnl_rate = ratio(total_cumulative_pnl, total_invested) if total_invested > 0 else None

        # Stock counts
        all_tickers = {r.values[2] for r in pa_rows if r.values[2]}
        open_tickers = {r.values[2] for r in pa_rows if r.values[4] == "持有中" and r.values[2]}
        closed_only_tickers = {r.values[2] for r in pa_rows if r.values[4] == "已清仓" and r.values[2]} - open_tickers

        cards = {
            "total_market_value": serialize_val(total_market_value),
            "floating_pnl": serialize_val(total_floating_pnl),
            "floating_pnl_rate": serialize_val(floating_pnl_rate),
            "total_stock_count": len(all_tickers),
            "open_stock_count": len(open_tickers),
            "closed_stock_count": len(closed_only_tickers),
            "cumulative_pnl": serialize_val(total_cumulative_pnl),
            "cumulative_pnl_rate": serialize_val(cumulative_pnl_rate),
            "total_invested": serialize_val(total_invested),
            "realized_pnl": serialize_val(total_closed_pnl),
        }

        # --- 1. 人员持仓汇总 ---
        person_groups: dict[Any, dict[str, Any]] = {}
        for r in pa_rows:
            p_id = r.metadata.get("person_id")
            p_name = r.values[0] or "未知人员"
            if p_id not in person_groups:
                person_groups[p_id] = {
                    "person_id": p_id,
                    "name": p_name,
                    "market_value": Decimal(0),
                    "total_pnl": Decimal(0),
                    "net_invested": Decimal(0),
                    "all_stocks": set(),
                    "open_stocks": set(),
                    "closed_stocks": set(),
                }
            item = person_groups[p_id]
            ticker = r.values[2]
            if ticker:
                item["all_stocks"].add(ticker)
                if r.values[4] == "持有中":
                    item["open_stocks"].add(ticker)
                    if isinstance(r.values[7], Decimal):
                        item["market_value"] += r.values[7]
                    if isinstance(r.values[8], Decimal):
                        item["total_pnl"] += r.values[8]
                    if isinstance(r.values[12], Decimal):
                        item["net_invested"] += r.values[12]
                elif r.values[4] == "已清仓":
                    item["closed_stocks"].add(ticker)
                    if isinstance(r.values[8], Decimal):
                        item["total_pnl"] += r.values[8]
                    if isinstance(r.values[12], Decimal):
                        item["net_invested"] += r.values[12]

        person_summary = []
        for p_id, item in sorted(person_groups.items(), key=lambda x: str(x[1]["name"])):
            closed_count = len(item["closed_stocks"] - item["open_stocks"])
            net = item["net_invested"]
            mv = item["market_value"]
            base = net if net > 0 else (mv - item["total_pnl"])
            p_rate = ratio(item["total_pnl"], base) if base > 0 else None
            person_summary.append({
                "person_id": p_id,
                "name": item["name"],
                "market_value": serialize_val(mv),
                "total_pnl": serialize_val(item["total_pnl"]),
                "pnl_rate": serialize_val(p_rate),
                "total_stocks": len(item["all_stocks"]),
                "open_stocks": len(item["open_stocks"]),
                "closed_stocks": closed_count,
            })

        # --- 2. 券商持仓汇总 ---
        broker_summary = []
        if qh_rows and person_id is None:
            for r in qh_rows:
                channel = r.values[0]
                mv = r.values[1] or Decimal(0)
                pnl = r.values[8] or Decimal(0)
                net = r.values[2] or Decimal(0)
                b_rate = ratio(pnl, net if net > 0 else (mv - pnl))
                cash = r.values[12]
                broker_summary.append({
                    "channel": channel,
                    "market_value": serialize_val(mv),
                    "total_pnl": serialize_val(pnl),
                    "pnl_rate": serialize_val(b_rate),
                    "cash": serialize_val(cash),
                })
        else:
            # Fallback by grouping pa_rows by channel
            b_groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"mv": Decimal(0), "pnl": Decimal(0), "net": Decimal(0)})
            for r in pa_rows:
                ch_name = r.values[1] or "未知渠道"
                if isinstance(r.values[7], Decimal) and r.values[4] == "持有中":
                    b_groups[ch_name]["mv"] += r.values[7]
                if isinstance(r.values[8], Decimal):
                    b_groups[ch_name]["pnl"] += r.values[8]
                if isinstance(r.values[12], Decimal):
                    b_groups[ch_name]["net"] += r.values[12]
            for ch_name, b_data in sorted(b_groups.items()):
                b_rate = ratio(b_data["pnl"], b_data["net"]) if b_data["net"] > 0 else None
                broker_summary.append({
                    "channel": ch_name,
                    "market_value": serialize_val(b_data["mv"]),
                    "total_pnl": serialize_val(b_data["pnl"]),
                    "pnl_rate": serialize_val(b_rate),
                    "cash": None,
                })

        # --- 3. 股票持仓汇总 ---
        stock_groups: dict[str, dict[str, Any]] = {}
        for r in pa_rows:
            tk = r.values[2]
            if not tk:
                continue
            if tk not in stock_groups:
                stock_groups[tk] = {
                    "ticker": tk,
                    "market_value": Decimal(0),
                    "floating_pnl": Decimal(0),
                    "total_pnl": Decimal(0),
                    "net_invested": Decimal(0),
                    "quantity": Decimal(0),
                    "status": "已清仓",
                }
            sg = stock_groups[tk]
            if r.values[4] == "持有中":
                sg["status"] = "持有中"
                if isinstance(r.values[3], Decimal):
                    sg["quantity"] += r.values[3]
                if isinstance(r.values[7], Decimal):
                    sg["market_value"] += r.values[7]
                if isinstance(r.values[8], Decimal):
                    sg["floating_pnl"] += r.values[8]
                    sg["total_pnl"] += r.values[8]
                if isinstance(r.values[12], Decimal):
                    sg["net_invested"] += r.values[12]
            elif r.values[4] == "已清仓":
                if isinstance(r.values[8], Decimal):
                    sg["total_pnl"] += r.values[8]
                if isinstance(r.values[12], Decimal):
                    sg["net_invested"] += r.values[12]

        stock_summary = []
        for tk, sg in sorted(stock_groups.items(), key=lambda x: (x[1]["status"] != "持有中", -x[1]["market_value"])):
            base = sg["net_invested"] if sg["net_invested"] > 0 else (sg["market_value"] - sg["total_pnl"])
            s_rate = ratio(sg["total_pnl"], base) if base > 0 else None
            stock_summary.append({
                "ticker": tk,
                "market_value": serialize_val(sg["market_value"]),
                "floating_pnl": serialize_val(sg["floating_pnl"]),
                "total_pnl": serialize_val(sg["total_pnl"]),
                "pnl_rate": serialize_val(s_rate),
                "quantity": serialize_val(sg["quantity"]),
                "status": sg["status"],
            })

        # --- 4. 资产分布图数据 (Donut) ---
        dist_person = []
        if total_market_value > 0:
            for item in sorted(person_summary, key=lambda x: -float(x["market_value"] or 0)):
                mv = float(item["market_value"] or 0)
                if mv > 0:
                    dist_person.append({
                        "name": item["name"],
                        "value": mv,
                        "percent": round(mv / float(total_market_value) * 100, 2),
                    })

        dist_broker = []
        if total_market_value > 0:
            for item in sorted(broker_summary, key=lambda x: -float(x["market_value"] or 0)):
                mv = float(item["market_value"] or 0)
                if mv > 0:
                    dist_broker.append({
                        "name": item["channel"],
                        "value": mv,
                        "percent": round(mv / float(total_market_value) * 100, 2),
                    })

        dist_stock = []
        if total_market_value > 0:
            for item in sorted(stock_summary, key=lambda x: -float(x["market_value"] or 0)):
                mv = float(item["market_value"] or 0)
                if mv > 0:
                    dist_stock.append({
                        "name": item["ticker"],
                        "value": mv,
                        "percent": round(mv / float(total_market_value) * 100, 2),
                    })

        elapsed_ms = (time.monotonic() - start_t) * 1000
        logger.info(
            f"[持仓管理主页] 仪表板数据就绪，耗时 {elapsed_ms:.1f}ms (人员汇总: {len(person_summary)} 条, 券商汇总: {len(broker_summary)} 条, 股票汇总: {len(stock_summary)} 条)"
        )

        return {
            "cards": cards,
            "person_summary": person_summary,
            "broker_summary": broker_summary,
            "stock_summary": stock_summary,
            "distribution": {
                "by_person": dist_person,
                "by_broker": dist_broker,
                "by_stock": dist_stock,
            },
        }

    @staticmethod
    def get_trade_counts_map(db_path: str | Path | None = None) -> dict[tuple[int, int, int], tuple[int, int]] | None:
        """Batch query historical valid buy and sell trade counts with read-only connection and busy timeout.
        
        Returns:
            Dict mapping (person_id, account_id, instrument_id) -> (buy_count, sell_count)
            or None if the query fails.
        """
        stats_map = HoldingsManagementService.get_trade_stats_map(db_path=db_path)
        if stats_map is None:
            return None
        return {k: (v["buy_count"], v["sell_count"]) for k, v in stats_map.items()}

    @staticmethod
    def get_corporate_actions_map(
        db_path: str | Path | None = None,
    ) -> tuple[dict[int, list[dict[str, Any]]], dict[int, str]]:
        """Batch query active corporate actions (SPLIT, REVERSE_SPLIT) and broker accounts.

        Returns:
            (actions_map, accounts_map):
            - actions_map: mapping instrument_id -> list of action dicts sorted by effective_date asc
            - accounts_map: mapping account_id -> broker_code
        """
        start_t = time.monotonic()
        target_path = Path(db_path) if db_path else DATABASE_PATH
        if not target_path.exists():
            return {}, {}
        try:
            conn = sqlite3.connect(f"{target_path.as_uri()}?mode=ro", uri=True, timeout=10.0)
            conn.execute("PRAGMA busy_timeout = 10000")
            cursor = conn.cursor()

            cursor.execute("SELECT id, broker_code FROM broker_accounts")
            accounts_map = {int(r[0]): str(r[1]).upper() for r in cursor.fetchall()}

            cursor.execute(
                """
                SELECT instrument_id, effective_date, action_type, broker_scope, ratio_numerator, ratio_denominator
                FROM corporate_actions
                WHERE record_state = 'ACTIVE' AND action_type IN ('SPLIT', 'REVERSE_SPLIT')
                ORDER BY effective_date ASC
                """
            )
            actions_map: dict[int, list[dict[str, Any]]] = {}
            for r in cursor.fetchall():
                inst_id = int(r[0])
                eff_date = str(r[1])[:10]
                act_type = str(r[2]).upper()
                broker_scope = str(r[3]).upper()
                num = int(r[4])
                den = int(r[5]) if r[5] else 1
                if den == 0:
                    continue
                factor = Decimal(num) / Decimal(den)
                if factor > 1:
                    ratio_desc = f"{num // den if num % den == 0 else f'{factor:.2f}'}:1 拆股"
                elif factor < 1:
                    inv = Decimal(1) / factor
                    ratio_desc = f"1:{round(inv) if abs(inv - round(inv)) < 0.001 else f'{inv:.2f}'} 并股"
                else:
                    ratio_desc = "1:1"
                actions_map.setdefault(inst_id, []).append({
                    "effective_date": eff_date,
                    "action_type": act_type,
                    "broker_scope": broker_scope,
                    "ratio_numerator": num,
                    "ratio_denominator": den,
                    "factor": factor,
                    "desc": f"{eff_date} {ratio_desc}",
                })
            conn.close()
            elapsed_ms = (time.monotonic() - start_t) * 1000
            logger.info(f"[除权统计查询] 成功加载 {len(actions_map)} 种标的除权拆并股事件，耗时 {elapsed_ms:.1f}ms")
            return actions_map, accounts_map
        except Exception as e:
            elapsed_ms = (time.monotonic() - start_t) * 1000
            logger.exception(f"[除权统计查询] 查询异常 (耗时 {elapsed_ms:.1f}ms): {e}")
            return {}, {}

    @staticmethod
    def get_trade_stats_map(db_path: str | Path | None = None) -> dict[tuple[int, int, int], dict[str, Any]] | None:
        """Batch query historical valid buy/sell trade counts and dates in one read-only SQL query.

        Returns:
            Dict mapping (person_id, account_id, instrument_id) -> {
                "buy_count": int,
                "sell_count": int,
                "first_buy_date": str | None,
                "last_sell_date": str | None,
            }
            or None if the query fails.
        """
        start_t = time.monotonic()
        target_path = Path(db_path) if db_path else DATABASE_PATH
        if not target_path.exists():
            logger.warning(f"[交易统计查询] 数据库路径不存在: {target_path}")
            return None
        try:
            # Connect in explicit read-only URI mode with 10s busy timeout
            conn = sqlite3.connect(f"{target_path.as_uri()}?mode=ro", uri=True, timeout=10.0)
            conn.execute("PRAGMA busy_timeout = 10000")
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 
                    po.person_id,
                    po.account_id,
                    po.instrument_id,
                    SUM(CASE WHEN po.operation_kind = 'MARKET_TRADE' AND po.side = 'BUY' THEN 1 ELSE 0 END) AS buy_count,
                    SUM(CASE WHEN po.operation_kind = 'MARKET_TRADE' AND po.side = 'SELL' THEN 1 ELSE 0 END) AS sell_count,
                    MIN(CASE WHEN po.operation_kind = 'MARKET_TRADE' AND po.side = 'BUY' THEN po.business_date ELSE NULL END) AS first_buy_date,
                    MAX(CASE WHEN po.operation_kind = 'MARKET_TRADE' AND po.side = 'SELL' THEN po.business_date ELSE NULL END) AS last_sell_date
                FROM person_operations po
                GROUP BY po.person_id, po.account_id, po.instrument_id
                """
            )
            rows = cursor.fetchall()
            conn.close()
            elapsed_ms = (time.monotonic() - start_t) * 1000
            logger.info(f"[交易统计查询] 批量统计历史买卖次数及日期成功，耗时 {elapsed_ms:.1f}ms，覆盖 {len(rows)} 组标的账户")
            return {
                (int(r[0]), int(r[1]), int(r[2])): {
                    "buy_count": int(r[3] or 0),
                    "sell_count": int(r[4] or 0),
                    "first_buy_date": str(r[5])[:10] if r[5] else None,
                    "last_sell_date": str(r[6])[:10] if r[6] else None,
                }
                for r in rows
            }
        except Exception as e:
            elapsed_ms = (time.monotonic() - start_t) * 1000
            logger.exception(f"[交易统计查询] 查询异常 (耗时 {elapsed_ms:.1f}ms, 目标路径: {target_path}): {e}")
            return None

    @staticmethod
    def get_holdings_detail_rows(
        analytics,
        dimension: str = "person",
        person_id: int | tuple[int, ...] | None = None,
        broker: str | None = None,
        ticker: str | None = None,
        status: str | None = "持有中",
        search: str | None = None,
        db_path: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Return unified holdings detail rows matching filters (13 cols for 持有中, 17 cols for 已清仓)."""
        start_t = time.monotonic()
        if not analytics or not hasattr(analytics, "reports"):
            return []

        pa = analytics.reports.get("人员资产")
        if not pa:
            return []

        positions_map = getattr(analytics, "positions", {}) or {}
        trade_stats_map = HoldingsManagementService.get_trade_stats_map(db_path=db_path)
        actions_map, accounts_map = HoldingsManagementService.get_corporate_actions_map(db_path=db_path)

        search_text = (search or "").strip().upper()
        ticker_filter = (ticker or "").strip().upper()
        broker_filter = (broker or "").strip().upper()
        person_ids = (person_id,) if isinstance(person_id, int) else tuple(person_id) if isinstance(person_id, (tuple, list)) else None

        filtered_items = []
        for r in pa.rows:
            p_name = r.values[0] or ""
            p_id = r.metadata.get("person_id")
            acc_id = r.metadata.get("account_id")
            inst_id = r.metadata.get("instrument_id")
            channel = r.values[1] or ""
            tk = r.values[2] or ""
            qty = r.values[3]
            st = r.values[4] or "持有中"
            cost_p = r.values[5]
            raw_curr_p = r.values[6]
            mv = r.values[7] or Decimal(0)
            pnl = r.values[8] if r.values[8] is not None else Decimal(0)

            # Filter checks
            if person_ids is not None and p_id not in person_ids:
                continue
            if broker_filter and broker_filter not in channel.upper():
                continue
            if ticker_filter and ticker_filter != tk.upper():
                continue
            if status in ("持有中", "当前持仓") and st != "持有中":
                continue
            if status == "已清仓" and st != "已清仓":
                continue
            if search_text:
                combined = f"{p_name} {channel} {tk}".upper()
                if search_text not in combined:
                    continue

            pos = positions_map.get((p_id, acc_id, inst_id))

            # For closed positions, analytics_service falls back to p.last_price when quote is missing.
            # Ensure current_price is strictly the latest market quote and None when quote is missing.
            if st == "已清仓":
                curr_p = raw_curr_p if r.metadata.get("valuation_date") is not None else None
            else:
                curr_p = raw_curr_p

            # Calculate cost total amount (成本总额 = 数量 * 成本价)
            cost_amount = (qty * cost_p) if (qty is not None and cost_p is not None) else None

            # Calculate pnl rate = (当前最新价 - 成本价) / 成本价
            if cost_p is not None and cost_p > Decimal(0) and curr_p is not None:
                calc_pnl_rate = (curr_p - cost_p) / cost_p
            else:
                calc_pnl_rate = None

            # Buy and Sell counts & dates from batch query
            if trade_stats_map is None:
                buy_count = None
                sell_count = None
                stats_first_buy = None
                stats_last_sell = None
            else:
                st_entry = trade_stats_map.get((p_id, acc_id, inst_id))
                if st_entry:
                    buy_count = st_entry["buy_count"]
                    sell_count = st_entry["sell_count"]
                    stats_first_buy = st_entry["first_buy_date"]
                    stats_last_sell = st_entry["last_sell_date"]
                else:
                    buy_count = 0
                    sell_count = 0
                    stats_first_buy = None
                    stats_last_sell = None

            # Closed position specific fields (PRD 17 columns)
            first_buy_date = stats_first_buy or (str(pos.first)[:10] if (pos and pos.first) else None)
            last_close_date = (str(pos.last)[:10] if (pos and pos.last) else None) or stats_last_sell
            closed_quantity = pos.sell_quantity if pos is not None else r.metadata.get("sell_quantity")
            total_buy_amount = pos.buy if (pos is not None and pos.buy is not None) else None
            total_sell_amount = pos.sell if (pos is not None and pos.sell is not None) else None

            if pos is not None and pos.buy is not None and pos.buy_quantity and pos.buy_quantity > ZERO:
                avg_buy_price = ratio(pos.buy, pos.buy_quantity)
            else:
                avg_buy_price = r.metadata.get("buy_cost")

            if pos is not None and pos.sell is not None and pos.sell_quantity and pos.sell_quantity > ZERO:
                avg_sell_price = ratio(pos.sell, pos.sell_quantity)
            else:
                avg_sell_price = r.metadata.get("sell_price")

            # 清仓价：最近一次使持仓数量归零的有效卖出成交价格
            if pos is not None and pos.last_price is not None and (sell_count is None or sell_count > 0 or (pos.sell_quantity and pos.sell_quantity > ZERO)):
                orig_close_price = pos.last_price
            else:
                orig_close_price = None

            # 清仓后拆股/并股折算逻辑：
            # 若清仓后发生了除权拆股/并股事件，需将历史成交清仓价折算至当前口径，以与当前市价进行可比的涨跌幅对比
            split_factor = Decimal(1)
            split_notes_list = []
            if st == "已清仓" and orig_close_price is not None and last_close_date and inst_id in actions_map:
                broker_code = accounts_map.get(acc_id, "")
                for act in actions_map[inst_id]:
                    if act["effective_date"] > last_close_date:
                        scope = act["broker_scope"]
                        if scope in ("ALL", broker_code):
                            split_factor *= act["factor"]
                            split_notes_list.append(act["desc"])

            is_split_adjusted = (split_factor != Decimal(1))
            if is_split_adjusted and split_factor > ZERO:
                adj_close_price = orig_close_price / split_factor
                split_notes = "; ".join(split_notes_list)
            else:
                adj_close_price = orig_close_price
                split_notes = None

            close_price = adj_close_price

            # 清仓后涨跌幅 = (当前价 - 清仓价) ÷ 清仓价（清仓价采用除权折算后价格，避免拆股引起失真）
            if curr_p is not None and close_price is not None and close_price > ZERO:
                post_close_change = (curr_p - close_price) / close_price
            else:
                post_close_change = None

            realized_pnl = pnl if st == "已清仓" else None
            if realized_pnl is not None and total_buy_amount is not None and total_buy_amount > ZERO:
                realized_pnl_rate = ratio(realized_pnl, total_buy_amount)
            else:
                realized_pnl_rate = None

            filtered_items.append({
                "person_name": p_name,
                "person_id": p_id,
                "account_id": acc_id,
                "instrument_id": inst_id,
                "channel": channel,
                "ticker": tk,
                "quantity": qty,
                "status": st,
                "cost_price": cost_p,
                "cost_amount": cost_amount,
                "current_price": curr_p,
                "market_value": mv,
                "pnl_rate": calc_pnl_rate,
                "total_pnl": pnl,
                "buy_count": buy_count,
                "sell_count": sell_count,
                # Closed position fields
                "first_buy_date": first_buy_date,
                "last_close_date": last_close_date,
                "closed_quantity": closed_quantity,
                "avg_buy_price": avg_buy_price,
                "avg_sell_price": avg_sell_price,
                "close_price": close_price,
                "orig_close_price": orig_close_price,
                "is_split_adjusted": is_split_adjusted,
                "split_factor": split_factor,
                "split_notes": split_notes,
                "post_close_change": post_close_change,
                "total_buy_amount": total_buy_amount,
                "total_sell_amount": total_sell_amount,
                "realized_pnl": realized_pnl,
                "realized_pnl_rate": realized_pnl_rate,
            })

        # Calculate weight relative to the filtered scope's total market value
        total_filtered_mv = sum((it["market_value"] for it in filtered_items if it["market_value"] and it["market_value"] > Decimal(0)), Decimal(0))

        rows = []
        for it in filtered_items:
            mv_val = it["market_value"] or Decimal(0)
            weight = (mv_val / total_filtered_mv) if total_filtered_mv > Decimal(0) else Decimal(0)
            rows.append({
                "person_name": it["person_name"],
                "person_id": it["person_id"],
                "channel": it["channel"],
                "ticker": it["ticker"],
                "quantity": serialize_val(it["quantity"]),
                "cost_price": serialize_val(it["cost_price"]),
                "cost_amount": serialize_val(it["cost_amount"]),
                "current_price": serialize_val(it["current_price"]),
                "market_value": serialize_val(it["market_value"]),
                "pnl_rate": serialize_val(it["pnl_rate"]),
                "total_pnl": serialize_val(it["total_pnl"]),
                "pnl": serialize_val(it["total_pnl"]),  # Compatibility
                "buy_count": it["buy_count"],
                "sell_count": it["sell_count"],
                "weight": serialize_val(weight),
                "status": it["status"],
                "account_id": it["account_id"],
                "instrument_id": it["instrument_id"],
                # Closed position 17-column fields
                "first_buy_date": it["first_buy_date"],
                "last_close_date": it["last_close_date"],
                "closed_quantity": serialize_val(it["closed_quantity"]),
                "avg_buy_price": serialize_val(it["avg_buy_price"]),
                "avg_sell_price": serialize_val(it["avg_sell_price"]),
                "close_price": serialize_val(it["close_price"]),
                "orig_close_price": serialize_val(it["orig_close_price"]),
                "is_split_adjusted": bool(it["is_split_adjusted"]),
                "split_factor": serialize_val(it["split_factor"]),
                "split_notes": it["split_notes"],
                "post_close_change": serialize_val(it["post_close_change"]),
                "total_buy_amount": serialize_val(it["total_buy_amount"]),
                "total_sell_amount": serialize_val(it["total_sell_amount"]),
                "realized_pnl": serialize_val(it["realized_pnl"]),
                "realized_pnl_rate": serialize_val(it["realized_pnl_rate"]),
            })

        # Sort according to status and selected dimension
        if status == "已清仓":
            # PRD 五.2: 默认按最后清仓日期倒序排列
            rows.sort(
                key=lambda x: (
                    x.get("last_close_date") or "",
                    float(x.get("realized_pnl") or 0),
                    x.get("ticker") or "",
                ),
                reverse=True,
            )
        elif dimension == "person":
            rows.sort(key=lambda x: (x["person_name"], x["status"] != "持有中", -float(x["market_value"] or 0)))
        elif dimension == "broker":
            rows.sort(key=lambda x: (x["channel"], x["status"] != "持有中", -float(x["market_value"] or 0)))
        elif dimension == "stock":
            rows.sort(key=lambda x: (x["ticker"], x["status"] != "持有中", -float(x["market_value"] or 0)))

        elapsed_ms = (time.monotonic() - start_t) * 1000
        logger.info(
            f"[持仓明细] 计算完成，耗时 {elapsed_ms:.1f}ms，返回 {len(rows)} 条标的记录 (维度: {dimension}, 人员: {person_id}, 券商: {broker}, 状态: {status})"
        )

        return rows

    @staticmethod
    def get_stock_drawer_data(
        source_path: str,
        analytics,
        ticker: str,
        person_id: int | None = None,
        broker: str | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """Fetch all data required by the 4-tab stock drawer."""
        start_t = time.monotonic()
        ticker = ticker.strip().upper()
        as_of = as_of or (analytics.scope.as_of if analytics and hasattr(analytics, "scope") else date.today())

        # 1. Top Summary Metrics
        pa = analytics.reports.get("人员资产") if analytics else None
        pa_rows = [
            r for r in (pa.rows if pa else ())
            if r.values[2] and r.values[2].upper() == ticker
            and (person_id is None or r.metadata.get("person_id") == person_id)
            and (broker is None or (r.values[1] and broker.upper() in r.values[1].upper()))
        ]

        active_rows = [r for r in pa_rows if r.values[4] == "持有中"]
        current_quantity = sum((r.values[3] for r in active_rows if isinstance(r.values[3], Decimal)), Decimal(0))
        market_value = sum((r.values[7] for r in active_rows if isinstance(r.values[7], Decimal)), Decimal(0))
        floating_pnl = sum((r.values[8] for r in active_rows if isinstance(r.values[8], Decimal)), Decimal(0))
        total_pnl = sum((r.values[8] for r in pa_rows if isinstance(r.values[8], Decimal)), Decimal(0))

        # Current Price & Cost Price
        current_price = None
        for r in active_rows:
            if isinstance(r.values[6], Decimal) and r.values[6] > 0:
                current_price = r.values[6]
                break

        cost_price = None
        if current_quantity > 0:
            open_net = sum((r.values[12] for r in active_rows if isinstance(r.values[12], Decimal)), Decimal(0))
            cost_price = open_net / current_quantity if open_net > 0 else (market_value - floating_pnl) / current_quantity

        net_invested = sum((r.values[12] for r in pa_rows if isinstance(r.values[12], Decimal)), Decimal(0))
        base_invested = net_invested if net_invested > 0 else (market_value - total_pnl)
        floating_pnl_rate = ratio(floating_pnl, open_net) if (current_quantity > 0 and open_net > 0) else None
        total_pnl_rate = ratio(total_pnl, base_invested) if base_invested > 0 else None

        summary = {
            "ticker": ticker,
            "company_name": f"{ticker} Corporation",
            "tags": ["美股", "标的"],
            "current_quantity": serialize_val(current_quantity),
            "cost_price": serialize_val(cost_price),
            "current_price": serialize_val(current_price),
            "market_value": serialize_val(market_value),
            "floating_pnl": serialize_val(floating_pnl),
            "floating_pnl_rate": serialize_val(floating_pnl_rate),
            "cumulative_pnl": serialize_val(total_pnl),
            "cumulative_pnl_rate": serialize_val(total_pnl_rate),
        }

        # 2. Tab 1: 交易记录 (11 Columns)
        ts = TradesService(source_path)
        trade_scope = QueryScope(
            as_of=as_of,
            person_ids=(person_id,) if person_id is not None else None,
            include_disabled=True,
            search=ticker,
        )
        trade_page = ts.get_trades(trade_scope)
        matched_trades = []
        for r in trade_page.rows:
            if r.values[3] and r.values[3].upper() == ticker:
                if broker and r.values[1] and broker.upper() not in r.values[1].upper():
                    continue
                matched_trades.append(r)

        trade_records = []
        for r in matched_trades:
            # 1. 交易时间
            trade_time = str(r.values[0])
            # 2. 归属人员
            p_name = r.metadata.get("person_name") or "个人"
            # 3. 券商渠道
            channel = r.values[1] or ""
            # 4. 交易方向
            side = r.values[4] or "买入"
            # 5. 成交数量
            qty = r.values[5]
            # 6. 当前价格
            c_price = r.metadata.get("current_price") or current_price
            # 7. 成交价格
            exec_price = r.values[6]
            # 8. 成交金额 (quantity * price, fee excluded)
            amount = (qty * exec_price) if (qty is not None and exec_price is not None) else r.values[7]
            # 9. 手续费
            fee_e8 = r.metadata.get("fees_e8")
            if fee_e8 is not None:
                fee = Decimal(fee_e8) / Decimal(100_000_000)
            else:
                fee = r.values[8] if len(r.values) > 8 and isinstance(r.values[8], Decimal) else Decimal(0)
            # 10. 成交后持仓
            post_q = r.metadata.get("post_quantity")
            # 11. 成本变化
            cost_diff = r.metadata.get("cost_diff")
            if post_q is not None and post_q == 0:
                cost_change_text = "清仓"
            elif cost_diff is not None:
                if cost_diff < 0:
                    cost_change_text = f"降低 ${abs(cost_diff):.2f}/股"
                elif cost_diff > 0:
                    cost_change_text = f"提高 ${cost_diff:.2f}/股"
                else:
                    cost_change_text = "持平"
            else:
                cost_change_text = "建仓" if (post_q and post_q > 0) else "—"

            trade_records.append({
                "trade_time": trade_time,
                "person_name": p_name,
                "channel": channel,
                "side": side,
                "quantity": serialize_val(qty),
                "current_price": serialize_val(c_price),
                "price": serialize_val(exec_price),
                "amount": serialize_val(amount),
                "fee": serialize_val(fee),
                "post_quantity": serialize_val(post_q),
                "cost_change": cost_change_text,
                "raw_cost_diff": serialize_val(cost_diff),
            })

        # Default sort descending by trade time
        trade_records.sort(key=lambda x: str(x["trade_time"]), reverse=True)

        # 3. Tab 2: 持仓分析 (Quantity and Cost changes over time)
        holding_analysis = {"dates": [], "quantities": [], "costs": []}
        # Chronological sort for the chart
        for t in sorted(trade_records, key=lambda x: str(x["trade_time"])):
            dt = str(t["trade_time"])[:10]
            holding_analysis["dates"].append(dt)
            holding_analysis["quantities"].append(t["post_quantity"] or 0)
            # Cost per share
            c_val = serialize_val(cost_price)
            holding_analysis["costs"].append(c_val or 0)

        # 4. Tab 3: 收益分析 (PnL Analysis)
        pnl_analysis = {
            "floating_pnl": serialize_val(floating_pnl),
            "floating_rate": serialize_val(floating_pnl_rate),
            "realized_pnl": serialize_val(total_pnl - floating_pnl),
            "cumulative_pnl": serialize_val(total_pnl),
            "cumulative_rate": serialize_val(total_pnl_rate),
            "chart_dates": [],
            "cumulative_rates": [],
            "floating_rates": [],
            "markers": [],
        }
        # Collect buy / sell execution markers
        for t in trade_records:
            pnl_analysis["markers"].append({
                "date": str(t["trade_time"])[:10],
                "side": t["side"],
                "price": t["price"],
                "quantity": t["quantity"],
            })

        # 5. Tab 4: 月度表现 (Monthly Performance)
        monthly_rep = analytics.reports.get("每月持仓收益") if analytics else None
        monthly_records = []
        if monthly_rep:
            for r in monthly_rep.rows:
                if r.values[3] and r.values[3].upper() == ticker:
                    if person_id is not None and r.metadata.get("person_id") != person_id:
                        continue
                    m_pnl = r.values[20]
                    m_mv = r.values[11]
                    m_rate = r.values[21] if len(r.values) > 21 else None
                    if m_rate is None and m_mv and m_mv > 0 and m_pnl is not None:
                        m_rate = ratio(m_pnl, m_mv - m_pnl)
                    monthly_records.append({
                        "month": str(r.values[0]),
                        "market_value": serialize_val(m_mv),
                        "monthly_pnl": serialize_val(m_pnl),
                        "return_rate": serialize_val(m_rate),
                    })
        monthly_records.sort(key=lambda x: x["month"], reverse=True)

        elapsed_ms = (time.monotonic() - start_t) * 1000
        logger.info(
            f"[股票抽屉] 标的 {ticker} 抽屉数据就绪，耗时 {elapsed_ms:.1f}ms (历史交易: {len(trade_records)} 笔, 月度记录: {len(monthly_records)} 条)"
        )

        return {
            "summary": summary,
            "trades": trade_records,
            "holding_analysis": holding_analysis,
            "pnl_analysis": pnl_analysis,
            "monthly_performance": monthly_records,
        }
