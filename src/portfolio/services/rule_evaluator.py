"""Core rule evaluator for monitoring technical conditions, price breakouts, and alerts."""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from portfolio.domain.monitor_rules import AlertEvent, MonitorRule, RuntimeState

logger = logging.getLogger(__name__)
D = Decimal


class RuleEvaluator:
    @staticmethod
    def evaluate(
        rule: MonitorRule,
        state: RuntimeState,
        quote_data: Dict[str, Any],
        indicators: Dict[str, Any],
        series_data: Dict[str, List[Any]],
        bars: list,
        now_utc: datetime,
    ) -> Tuple[Optional[AlertEvent], RuntimeState]:
        """
        Evaluate a single MonitorRule against current market state.
        Returns (AlertEvent, updated RuntimeState) or (None, updated RuntimeState).
        """
        if not rule.enabled or rule.completed:
            return None, state

        # 1. Trading Session Validation
        quote_session = quote_data.get("session") or quote_data.get("quote_session") or "Closed"
        if rule.session_scope == "REGULAR":
            if quote_session not in ("Intraday", "盘中"):
                return None, state
        elif rule.session_scope == "EXTENDED":
            if quote_session not in ("Intraday", "盘中", "Pre", "盘前", "Post", "盘后"):
                return None, state

        # 2. Check Cooldown
        if rule.repeat_mode == "COOLDOWN" and state.cooldown_until:
            try:
                cooldown_time = datetime.fromisoformat(state.cooldown_until)
                if now_utc < cooldown_time:
                    return None, state
            except Exception:
                pass

        # 3. Extract Current Price & Valid Data
        curr_price_raw = quote_data.get("price")
        if curr_price_raw is None:
            return None, state
        try:
            curr_price = Decimal(str(curr_price_raw))
            if not curr_price.is_finite() or curr_price <= 0:
                return None, state
        except Exception:
            return None, state

        prev_close = None
        if quote_data.get("prev_close"):
            try:
                prev_close = Decimal(str(quote_data["prev_close"]))
            except Exception:
                pass

        # 4. Check Check-Mode (Intraday vs Daily Close)
        if rule.check_mode == "DAILY_CLOSE":
            # Must have completed bar and use completed bar close
            if not bars:
                return None, state
            curr_price = bars[-1].close

        # 5. Baseline Initialization
        # On first observation of this session/rule, establish baseline without triggering.
        if not state.last_observed_value:
            state.last_observed_value = str(curr_price)
            state.last_observed_at = now_utc.isoformat()
            state.rearmed = True
            return None, state

        try:
            prev_price = Decimal(state.last_observed_value)
        except Exception:
            prev_price = curr_price

        # 6. Condition Type Evaluation
        triggered = False
        trigger_reason = ""
        threshold_val_str = ""
        is_rearmed_state = state.rearmed

        expr = rule.expression or {}
        op = expr.get("operator", "")
        t = rule.rule_type

        # --- A. PRICE ---
        if t == "PRICE":
            th_raw = expr.get("threshold", "0")
            threshold_val_str = str(th_raw)
            try:
                threshold = Decimal(str(th_raw))
            except Exception:
                threshold = D(0)

            if op == "CROSS_ABOVE":
                if curr_price < threshold:
                    is_rearmed_state = True
                if is_rearmed_state and prev_price < threshold and curr_price >= threshold:
                    triggered = True
                    trigger_reason = f"现价 ${curr_price:,.2f} 上穿目标价 ${threshold:,.2f}"
            elif op == "CROSS_BELOW":
                if curr_price > threshold:
                    is_rearmed_state = True
                if is_rearmed_state and prev_price > threshold and curr_price <= threshold:
                    triggered = True
                    trigger_reason = f"现价 ${curr_price:,.2f} 跌破目标价 ${threshold:,.2f}"
            elif op == "PCT_CHANGE_ABOVE" and prev_close:
                curr_pct = (curr_price / prev_close - 1) * 100
                prev_pct = (prev_price / prev_close - 1) * 100
                if curr_pct < threshold:
                    is_rearmed_state = True
                if is_rearmed_state and prev_pct < threshold and curr_pct >= threshold:
                    triggered = True
                    trigger_reason = f"今日涨幅 +{curr_pct:.2f}% 突破 +{threshold:.2f}%"
            elif op == "PCT_CHANGE_BELOW" and prev_close:
                curr_pct = (curr_price / prev_close - 1) * 100
                prev_pct = (prev_price / prev_close - 1) * 100
                target_drop = -abs(threshold)
                if curr_pct > target_drop:
                    is_rearmed_state = True
                if is_rearmed_state and prev_pct > target_drop and curr_pct <= target_drop:
                    triggered = True
                    trigger_reason = f"今日跌幅 {curr_pct:.2f}% 跌破 {target_drop:.2f}%"

        # --- B. PRICE_MA_CROSS ---
        elif t == "PRICE_MA_CROSS":
            ma_period = expr.get("period", expr.get("ma_period", 20))
            ma_val = indicators.get(f"MA{ma_period}")
            if ma_val is not None:
                threshold_val_str = f"MA{ma_period} (${ma_val:,.2f})"
                prev_diff = prev_price - ma_val
                curr_diff = curr_price - ma_val
                if op == "CROSS_ABOVE":
                    if curr_diff < 0:
                        is_rearmed_state = True
                    if is_rearmed_state and prev_diff < 0 and curr_diff >= 0:
                        triggered = True
                        trigger_reason = f"股价 ${curr_price:,.2f} 上穿 MA{ma_period} (${ma_val:,.2f})"
                elif op == "CROSS_BELOW":
                    if curr_diff > 0:
                        is_rearmed_state = True
                    if is_rearmed_state and prev_diff > 0 and curr_diff <= 0:
                        triggered = True
                        trigger_reason = f"股价 ${curr_price:,.2f} 跌破 MA{ma_period} (${ma_val:,.2f})"

        # --- C. MA_CROSS (Daily K-line only) ---
        elif t == "MA_CROSS":
            s_p = expr.get("fast_period", expr.get("ma_short", 50))
            l_p = expr.get("slow_period", expr.get("ma_long", 200))
            threshold_val_str = f"MA{s_p}/MA{l_p}"
            s_series = series_data.get(f"MA{s_p}") or []
            l_series = series_data.get(f"MA{l_p}") or []
            if len(s_series) >= 2 and len(l_series) >= 2:
                s_prev, s_curr = s_series[-2], s_series[-1]
                l_prev, l_curr = l_series[-2], l_series[-1]
                if all(v is not None for v in (s_prev, s_curr, l_prev, l_curr)):
                    prev_diff = s_prev - l_prev
                    curr_diff = s_curr - l_curr
                    if op in ("GOLDEN_CROSS", "CROSS_ABOVE"):
                        if curr_diff <= 0:
                            is_rearmed_state = True
                        if is_rearmed_state and prev_diff <= 0 and curr_diff > 0:
                            triggered = True
                            trigger_reason = f"均线金叉形成：MA{s_p} (${s_curr:,.2f}) 上穿 MA{l_p} (${l_curr:,.2f})"
                    elif op in ("DEATH_CROSS", "CROSS_BELOW"):
                        if curr_diff >= 0:
                            is_rearmed_state = True
                        if is_rearmed_state and prev_diff >= 0 and curr_diff < 0:
                            triggered = True
                            trigger_reason = f"均线死叉形成：MA{s_p} (${s_curr:,.2f}) 跌破 MA{l_p} (${l_curr:,.2f})"

        # --- D. MACD ---
        elif t == "MACD":
            dif_series = series_data.get("DIF") or []
            dea_series = series_data.get("DEA") or []
            macd_series = series_data.get("MACD") or []
            threshold_val_str = op
            if len(dif_series) >= 2 and len(dea_series) >= 2 and len(macd_series) >= 2:
                d1, d2 = dif_series[-2], dif_series[-1]
                e1, e2 = dea_series[-2], dea_series[-1]
                m1, m2 = macd_series[-2], macd_series[-1]
                if op in ("DIF_CROSS_DEA_GOLDEN", "GOLDEN_CROSS") and d1 is not None and e1 is not None and d2 is not None and e2 is not None:
                    if d2 <= e2:
                        is_rearmed_state = True
                    if is_rearmed_state and (d1 - e1) <= 0 and (d2 - e2) > 0:
                        triggered = True
                        trigger_reason = f"MACD 金叉：DIF ({d2:.3f}) 上穿 DEA ({e2:.3f})"
                elif op in ("DIF_CROSS_DEA_DEATH", "DEATH_CROSS") and d1 is not None and e1 is not None and d2 is not None and e2 is not None:
                    if d2 >= e2:
                        is_rearmed_state = True
                    if is_rearmed_state and (d1 - e1) >= 0 and (d2 - e2) < 0:
                        triggered = True
                        trigger_reason = f"MACD 死叉：DIF ({d2:.3f}) 跌破 DEA ({e2:.3f})"
                elif op == "HIST_TURN_POSITIVE" and m1 is not None and m2 is not None:
                    if m2 <= 0:
                        is_rearmed_state = True
                    if is_rearmed_state and m1 <= 0 and m2 > 0:
                        triggered = True
                        trigger_reason = f"MACD 柱状图翻红：由负转正 ({m2:.3f})"
                elif op == "HIST_TURN_NEGATIVE" and m1 is not None and m2 is not None:
                    if m2 >= 0:
                        is_rearmed_state = True
                    if is_rearmed_state and m1 >= 0 and m2 < 0:
                        triggered = True
                        trigger_reason = f"MACD 柱状图翻绿：由正转负 ({m2:.3f})"

        # --- E. RSI ---
        elif t == "RSI":
            th_raw = expr.get("threshold", 70)
            threshold_val_str = str(th_raw)
            try:
                threshold = Decimal(str(th_raw))
            except Exception:
                threshold = D(70)
            rsi_series = series_data.get("RSI14") or []
            if len(rsi_series) >= 2 and rsi_series[-2] is not None and rsi_series[-1] is not None:
                r1, r2 = rsi_series[-2], rsi_series[-1]
                if op == "CROSS_ABOVE":
                    if r2 < threshold:
                        is_rearmed_state = True
                    if is_rearmed_state and r1 < threshold and r2 >= threshold:
                        triggered = True
                        trigger_reason = f"RSI14 ({r2:.1f}) 上穿阈值线 {threshold}"
                elif op == "CROSS_BELOW":
                    if r2 > threshold:
                        is_rearmed_state = True
                    if is_rearmed_state and r1 > threshold and r2 <= threshold:
                        triggered = True
                        trigger_reason = f"RSI14 ({r2:.1f}) 跌破阈值线 {threshold}"

        # --- F. VOLUME ---
        elif t == "VOLUME":
            ma_p = expr.get("ma_period", 20)
            ratio = Decimal(str(expr.get("ratio", 2.0)))
            vma = indicators.get(f"VMA{ma_p}")
            if vma is not None and vma > 0 and bars:
                curr_vol = bars[-1].volume
                vol_ratio = curr_vol / vma
                threshold_val_str = f"{ratio}x VMA{ma_p}"
                if vol_ratio < ratio:
                    is_rearmed_state = True
                if is_rearmed_state and vol_ratio >= ratio:
                    triggered = True
                    trigger_reason = f"成交量放量 {vol_ratio:.1f}倍 突破 {ratio}倍 VMA{ma_p}"

        # --- G. WEEK52 ---
        elif t == "WEEK52":
            high52 = indicators.get("HIGH52")
            low52 = indicators.get("LOW52")
            if op == "NEW_HIGH" and high52 is not None:
                threshold_val_str = f"52周高点 ${high52:,.2f}"
                if curr_price < high52:
                    is_rearmed_state = True
                if is_rearmed_state and prev_price < high52 and curr_price >= high52:
                    triggered = True
                    trigger_reason = f"突破 52周最高价 ${high52:,.2f}"
            elif op == "NEW_LOW" and low52 is not None:
                threshold_val_str = f"52周低点 ${low52:,.2f}"
                if curr_price > low52:
                    is_rearmed_state = True
                if is_rearmed_state and prev_price > low52 and curr_price <= low52:
                    triggered = True
                    trigger_reason = f"跌破 52周最低价 ${low52:,.2f}"

        # --- H. ATH_DRAWDOWN ---
        elif t == "ATH_DRAWDOWN":
            ath = indicators.get("AVAILABLE_HIGH")
            if ath is not None and ath > 0:
                if op == "ATH_BREAKOUT":
                    threshold_val_str = f"ATH ${ath:,.2f}"
                    if curr_price < ath:
                        is_rearmed_state = True
                    if is_rearmed_state and prev_price < ath and curr_price >= ath:
                        triggered = True
                        trigger_reason = f"突破历史最高价 ${ath:,.2f} 创出新高！"
                elif op == "DRAWDOWN_CROSS":
                    th_raw = expr.get("threshold", 10.0)
                    threshold_val_str = f"回撤 {th_raw}%"
                    try:
                        th_pct = Decimal(str(th_raw))
                    except Exception:
                        th_pct = D(10)
                    curr_dd = (1 - curr_price / ath) * 100
                    prev_dd = (1 - prev_price / ath) * 100
                    if curr_dd < th_pct:
                        is_rearmed_state = True
                    if is_rearmed_state and prev_dd < th_pct and curr_dd >= th_pct:
                        triggered = True
                        trigger_reason = f"自历史最高位 (${ath:,.2f}) 回撤达 {curr_dd:.1f}%，跨越 {th_pct}% 阈值"

        # --- I. COMPOSITE (Multi-condition) ---
        elif t == "COMPOSITE":
            logic = expr.get("logic", "ALL")
            sub_conds = expr.get("conditions", [])
            sub_results = []
            sub_reasons = []
            for sub in sub_conds:
                dummy_rule = MonitorRule(
                    rule_id=f"{rule.rule_id}_sub",
                    scope_key=rule.scope_key,
                    analysis_symbol=rule.analysis_symbol,
                    monitored_symbol=rule.monitored_symbol,
                    rule_type=sub.get("rule_type", "PRICE"),
                    expression=sub,
                    check_mode=rule.check_mode,
                    session_scope=rule.session_scope,
                    enabled=True,
                )
                sub_state = RuntimeState(
                    rule_id=dummy_rule.rule_id,
                    last_observed_value=state.last_observed_value,
                    rearmed=state.rearmed,
                )
                evt, _ = RuleEvaluator.evaluate(
                    dummy_rule, sub_state, quote_data, indicators, series_data, bars, now_utc
                )
                sub_results.append(bool(evt))
                if evt:
                    sub_reasons.append(evt.trigger_reason)

            if logic == "ALL":
                if all(sub_results) and sub_results:
                    triggered = True
                    trigger_reason = "同时满足：" + "；".join(sub_reasons)
            else:  # ANY
                if any(sub_results):
                    triggered = True
                    trigger_reason = "满足条件：" + "；".join(sub_reasons)

        # 7. Update Runtime State
        state.last_observed_value = str(curr_price)
        state.last_observed_at = now_utc.isoformat()
        state.rearmed = is_rearmed_state

        if not triggered:
            return None, state

        # 8. Handling Trigger Success (Repeat Policies)
        state.rearmed = False  # Mark as triggered, needs rearm
        if rule.repeat_mode == "ONCE":
            rule.completed = True
            rule.enabled = False
        elif rule.repeat_mode == "COOLDOWN":
            from datetime import timedelta
            cooldown_delta = timedelta(seconds=rule.cooldown_seconds)
            state.cooldown_until = (now_utc + cooldown_delta).isoformat()

        rule.trigger_count += 1
        rule.last_trigger_at = now_utc.isoformat()

        # Build AlertEvent
        event = AlertEvent(
            event_id="",
            rule_id=rule.rule_id,
            rule_version=rule.version,
            rule_name=rule.name,
            analysis_symbol=rule.analysis_symbol,
            monitored_symbol=rule.monitored_symbol,
            observed_price=f"{curr_price:,.2f}",
            threshold_value=threshold_val_str,
            trigger_reason=trigger_reason,
            quote_session=quote_session,
            observed_at=quote_data.get("stamp", now_utc.isoformat()),
            detected_at=now_utc.isoformat(),
            input_snapshot={"quote": quote_data},
            indicator_snapshot={k: str(v) for k, v in indicators.items() if v is not None},
            notification_status="DELIVERED",
        )
        return event, state
