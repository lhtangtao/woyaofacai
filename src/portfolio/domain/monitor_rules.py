"""Data models and serialization for monitoring rules and alert events."""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

D = Decimal


class ConditionType:
    PRICE = "PRICE"
    PRICE_MA_CROSS = "PRICE_MA_CROSS"
    MA_CROSS = "MA_CROSS"
    MACD = "MACD"
    RSI = "RSI"
    VOLUME = "VOLUME"
    WEEK52 = "WEEK52"
    ATH_DRAWDOWN = "ATH_DRAWDOWN"
    COMPOSITE = "COMPOSITE"


class RepeatMode:
    ONCE = "ONCE"
    REARM_CROSSING = "REARM_CROSSING"
    COOLDOWN = "COOLDOWN"


class SessionScope:
    REGULAR = "REGULAR"
    EXTENDED = "EXTENDED"


class CheckMode:
    INTRADAY = "INTRADAY"
    DAILY_CLOSE = "DAILY_CLOSE"


class ChannelType:
    IN_APP = "in_app"
    DESKTOP = "desktop"
    SOUND = "sound"


@dataclass
class MonitorRule:
    rule_id: str
    scope_key: str
    analysis_symbol: str
    monitored_symbol: str
    market: str = "US"
    rule_type: str = "PRICE"  # PRICE, PRICE_MA_CROSS, MA_CROSS, MACD, RSI, VOLUME, WEEK52, ATH_DRAWDOWN, COMPOSITE
    expression: Dict[str, Any] = field(default_factory=dict)
    check_mode: str = "INTRADAY"  # INTRADAY, DAILY_CLOSE
    session_scope: str = "REGULAR"  # REGULAR, EXTENDED
    repeat_mode: str = "REARM_CROSSING"  # ONCE, REARM_CROSSING, COOLDOWN
    cooldown_seconds: int = 1800  # 30 mins
    notification_channels: str = "in_app,desktop"  # in_app, desktop, sound
    sound_name: str = "Glass"
    enabled: bool = False  # Default paused
    completed: bool = False
    trigger_count: int = 0
    last_trigger_at: str = ""
    name: str = ""
    note: str = ""
    version: int = 1
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.rule_id:
            self.rule_id = f"rule_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if not self.name:
            self.name = describe_rule(self)

    def to_record(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "scope_key": self.scope_key,
            "analysis_symbol": self.analysis_symbol,
            "monitored_symbol": self.monitored_symbol,
            "market": self.market,
            "rule_type": self.rule_type,
            "expression_json": json.dumps(self.expression, ensure_ascii=False),
            "check_mode": self.check_mode,
            "session_scope": self.session_scope,
            "repeat_mode": self.repeat_mode,
            "cooldown_seconds": self.cooldown_seconds,
            "notification_channels": self.notification_channels,
            "sound_name": self.sound_name,
            "enabled": 1 if self.enabled else 0,
            "completed": 1 if self.completed else 0,
            "trigger_count": self.trigger_count,
            "last_trigger_at": self.last_trigger_at,
            "name": self.name,
            "note": self.note,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_record(cls, row: Dict[str, Any]) -> "MonitorRule":
        raw_expr = row.get("expression_json", "{}")
        try:
            expr = json.loads(raw_expr) if isinstance(raw_expr, str) else dict(raw_expr)
        except Exception:
            expr = {}
        return cls(
            rule_id=str(row["rule_id"]),
            scope_key=str(row.get("scope_key", "")),
            analysis_symbol=str(row.get("analysis_symbol", "")),
            monitored_symbol=str(row.get("monitored_symbol", "")),
            market=str(row.get("market", "US")),
            rule_type=str(row.get("rule_type", "PRICE")),
            expression=expr,
            check_mode=str(row.get("check_mode", "INTRADAY")),
            session_scope=str(row.get("session_scope", "REGULAR")),
            repeat_mode=str(row.get("repeat_mode", "REARM_CROSSING")),
            cooldown_seconds=int(row.get("cooldown_seconds", 1800)),
            notification_channels=str(row.get("notification_channels", "in_app,desktop")),
            sound_name=str(row.get("sound_name", "Glass")),
            enabled=bool(row.get("enabled", 0)),
            completed=bool(row.get("completed", 0)),
            trigger_count=int(row.get("trigger_count", 0)),
            last_trigger_at=str(row.get("last_trigger_at", "") or ""),
            name=str(row.get("name", "")),
            note=str(row.get("note", "") or ""),
            version=int(row.get("version", 1)),
            created_at=str(row.get("created_at", "")),
            updated_at=str(row.get("updated_at", "")),
        )


@dataclass
class AlertEvent:
    event_id: str
    rule_id: str
    rule_version: int
    rule_name: str
    analysis_symbol: str
    monitored_symbol: str
    observed_price: str
    threshold_value: str
    trigger_reason: str
    quote_session: str
    observed_at: str
    detected_at: str
    input_snapshot: Dict[str, Any] = field(default_factory=dict)
    indicator_snapshot: Dict[str, Any] = field(default_factory=dict)
    notification_status: str = "DELIVERED"  # DELIVERED, FAILED, MUTED
    read_at: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.event_id:
            self.event_id = f"evt_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        if not self.detected_at:
            self.detected_at = now
        if not self.created_at:
            self.created_at = now

    def to_record(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "rule_name": self.rule_name,
            "analysis_symbol": self.analysis_symbol,
            "monitored_symbol": self.monitored_symbol,
            "observed_price": self.observed_price,
            "threshold_value": self.threshold_value,
            "trigger_reason": self.trigger_reason,
            "quote_session": self.quote_session,
            "observed_at": self.observed_at,
            "detected_at": self.detected_at,
            "input_snapshot_json": json.dumps(self.input_snapshot, ensure_ascii=False),
            "indicator_snapshot_json": json.dumps(self.indicator_snapshot, ensure_ascii=False),
            "notification_status": self.notification_status,
            "read_at": self.read_at,
            "created_at": self.created_at,
        }

    @classmethod
    def from_record(cls, row: Dict[str, Any]) -> "AlertEvent":
        input_snap = json.loads(row.get("input_snapshot_json") or "{}")
        ind_snap = json.loads(row.get("indicator_snapshot_json") or "{}")
        return cls(
            event_id=str(row["event_id"]),
            rule_id=str(row["rule_id"]),
            rule_version=int(row.get("rule_version", 1)),
            rule_name=str(row.get("rule_name", "")),
            analysis_symbol=str(row.get("analysis_symbol", "")),
            monitored_symbol=str(row.get("monitored_symbol", "")),
            observed_price=str(row.get("observed_price", "")),
            threshold_value=str(row.get("threshold_value", "")),
            trigger_reason=str(row.get("trigger_reason", "")),
            quote_session=str(row.get("quote_session", "")),
            observed_at=str(row.get("observed_at", "")),
            detected_at=str(row.get("detected_at", "")),
            input_snapshot=input_snap,
            indicator_snapshot=ind_snap,
            notification_status=str(row.get("notification_status", "DELIVERED")),
            read_at=str(row.get("read_at", "") or ""),
            created_at=str(row.get("created_at", "")),
        )


@dataclass
class RuntimeState:
    rule_id: str
    last_observed_value: str = ""
    last_observed_at: str = ""
    rearmed: bool = True
    cooldown_until: str = ""
    last_eval_state: str = ""
    updated_at: str = ""

    def to_record(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "last_observed_value": self.last_observed_value,
            "last_observed_at": self.last_observed_at,
            "rearmed": 1 if self.rearmed else 0,
            "cooldown_until": self.cooldown_until,
            "last_eval_state": self.last_eval_state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def from_record(cls, row: Dict[str, Any]) -> "RuntimeState":
        return cls(
            rule_id=str(row["rule_id"]),
            last_observed_value=str(row.get("last_observed_value", "") or ""),
            last_observed_at=str(row.get("last_observed_at", "") or ""),
            rearmed=bool(row.get("rearmed", 1)),
            cooldown_until=str(row.get("cooldown_until", "") or ""),
            last_eval_state=str(row.get("last_eval_state", "") or ""),
            updated_at=str(row.get("updated_at", "") or ""),
        )


def describe_rule(rule: MonitorRule) -> str:
    """Generate human-readable sentence explaining the rule in Simplified Chinese."""
    expr = rule.expression or {}
    op = expr.get("operator", "")
    t = rule.rule_type
    sym = rule.monitored_symbol or rule.analysis_symbol

    if t == "PRICE":
        th = expr.get("threshold", "")
        if op == "CROSS_ABOVE":
            return f"{sym} 现价上穿 ${th}"
        elif op == "CROSS_BELOW":
            return f"{sym} 现价跌破 ${th}"
        elif op == "PCT_CHANGE_ABOVE":
            return f"{sym} 日涨幅达到 +{th}%"
        elif op == "PCT_CHANGE_BELOW":
            return f"{sym} 日跌幅达到 -{th}%"
        return f"{sym} 价格条件 ({op} {th})"

    elif t == "PRICE_MA_CROSS":
        ma = expr.get("ma_period", 20)
        if op == "CROSS_ABOVE":
            return f"{sym} 股价上穿 MA{ma}"
        elif op == "CROSS_BELOW":
            return f"{sym} 股价跌破 MA{ma}"
        return f"{sym} 股价与 MA{ma} 交叉"

    elif t == "MA_CROSS":
        s, l = expr.get("ma_short", 50), expr.get("ma_long", 200)
        if op == "GOLDEN_CROSS":
            return f"{sym} 均线金叉 (MA{s} 上穿 MA{l})"
        elif op == "DEATH_CROSS":
            return f"{sym} 均线死叉 (MA{s} 跌破 MA{l})"
        return f"{sym} 均线交叉 (MA{s}/MA{l})"

    elif t == "MACD":
        if op == "DIF_CROSS_DEA_GOLDEN":
            return f"{sym} MACD 金叉 (DIF 上穿 DEA)"
        elif op == "DIF_CROSS_DEA_DEATH":
            return f"{sym} MACD 死叉 (DIF 跌破 DEA)"
        elif op == "HIST_TURN_POSITIVE":
            return f"{sym} MACD 柱状图翻红 (由负转正)"
        elif op == "HIST_TURN_NEGATIVE":
            return f"{sym} MACD 柱状图翻绿 (由正转负)"
        return f"{sym} MACD 信号 ({op})"

    elif t == "RSI":
        th = expr.get("threshold", 70)
        if op == "CROSS_ABOVE":
            return f"{sym} RSI14 上穿超买线 {th}"
        elif op == "CROSS_BELOW":
            return f"{sym} RSI14 跌破超卖线 {th}"
        return f"{sym} RSI14 ({op} {th})"

    elif t == "VOLUME":
        ma = expr.get("ma_period", 20)
        ratio = expr.get("ratio", 2.0)
        return f"{sym} 成交量突破 {ratio}倍 VMA{ma}"

    elif t == "WEEK52":
        if op == "NEW_HIGH":
            return f"{sym} 突破 52周新高"
        elif op == "NEW_LOW":
            return f"{sym} 跌破 52周新低"
        return f"{sym} 52周高低点 ({op})"

    elif t == "ATH_DRAWDOWN":
        if op == "ATH_BREAKOUT":
            return f"{sym} 突破历史最高价 (ATH)"
        th = expr.get("threshold", 10.0)
        return f"{sym} 历史高点回撤达到 {th}%"

    elif t == "COMPOSITE":
        logic = "同时满足" if expr.get("logic") == "ALL" else "任一满足"
        sub_count = len(expr.get("conditions", []))
        return f"{sym} 组合条件 ({logic}，共{sub_count}项)"

    return f"{sym} 监控规则 ({rule.rule_type})"
