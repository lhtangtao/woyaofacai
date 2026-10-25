"""P0 observation pipeline: holdings readback, cached daily analytics, no alerts."""

import hashlib
import threading
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from portfolio.domain.live_quotes import us_session
from portfolio.domain.monitor_rules import AlertEvent, MonitorRule, RuntimeState
from portfolio.domain.technical_indicators import DailyBar, deviation, technicals
from portfolio.services.live_quotes import _SDK_VALUES_LOCK, create_context
from portfolio.services.monitor_mapping import normalize, resolve
from portfolio.services.monitor_store import BASIS
from portfolio.services.notification_service import notification_service
from portfolio.services.rule_evaluator import RuleEvaluator

UTC, NY = timezone.utc, ZoneInfo("America/New_York")
SESSION_NAMES = {"Pre": "盘前", "Intraday": "盘中", "Post": "盘后", "Overnight": "夜盘", "Closed": "休市"}


def key_for(path, scope):
    # Today's date changes, the observation configuration does not.
    return hashlib.sha256(
        (str(path) + "|" + repr((scope.person_ids, scope.account_ids, scope.ledger))).encode()
    ).hexdigest()


def holding_sources(analytics, instruments, overrides):
    """Consume the existing report; never rebuild ownership or stock quantities."""
    sources = []
    for row in analytics.reports["当前持仓"].rows:
        qty = row.values[3]
        if qty is None or qty <= 0:
            continue
        inst = instruments.get(row.metadata.get("instrument_id"))
        if not inst or not inst.get("market"):
            continue
        market = inst["market"].upper()
        symbol = normalize(inst["symbol"], market)
        mapping = resolve(symbol, str(row.values[1]), inst.get("name") or "", overrides.get(symbol))
        positions = getattr(analytics, "positions", None)
        if positions is not None:
            owners = [
                (key[0], position.quantity)
                for key, position in positions.items()
                if key[1:] == (row.metadata.get("account_id"), row.metadata.get("instrument_id"))
                and position.quantity != 0
            ]
        else:
            people = row.metadata.get("person_ids") or (None,)
            owners = [(person, qty if len(people) == 1 else None) for person in people]
        for person, owned_quantity in owners:
            sources.append(
                dict(
                    mapping.record(),
                    market=market,
                    account_id=row.metadata.get("account_id"),
                    person_id=person,
                    quantity=str(owned_quantity) if owned_quantity is not None else None,
                    holding_status=row.metadata.get("completeness", "COMPLETE"),
                )
            )
    return sources


class LongbridgeMonitorProvider:
    """Pull-only daily history. No duplicate subscriptions or additional login flow."""

    def __init__(self, credentials_path):
        self.path = credentials_path
        self.context = None
        self.days = None
        self.days_checked = None
        self.halves = set()

    def _context(self):
        if self.context is None:
            if not self.path:
                raise ValueError("请先在原有行情配置中选择长桥凭证")
            self.context = create_context(self.path)
        return self.context

    def completed_day(self, now):
        from longbridge.openapi import Market

        local = now.astimezone(NY)
        if self.days_checked != local.date():
            with _SDK_VALUES_LOCK:
                days = self._context().trading_days(
                    Market.US, local.date() - timedelta(days=15), local.date() + timedelta(days=7)
                )
                self.days = {d for d in days.trading_days}
                self.halves = set(days.half_trading_days)
            self.days_checked = local.date()
        closed = [
            d
            for d in self.days
            if datetime.combine(d, time(13 if d in self.halves else 16), NY) + timedelta(minutes=15) <= now
        ]
        if not closed:
            raise ValueError("尚未取得已完成交易日日历")
        return max(closed)

    def history(self, symbol, start, end):
        from longbridge.openapi import AdjustType, Period, TradeSessions

        with _SDK_VALUES_LOCK:
            ctx = self._context()
            raw = (
                ctx.history_candlesticks_by_offset(
                    symbol,
                    Period.Day,
                    AdjustType.ForwardAdjust,
                    False,
                    500,
                    datetime.combine(end, time(23, 59), NY).replace(tzinfo=None),
                    TradeSessions.Intraday,
                )
                if start is None
                else ctx.history_candlesticks_by_date(
                    symbol, Period.Day, AdjustType.ForwardAdjust, start, end, TradeSessions.Intraday
                )
            )
            # SDK naive datetimes are SYSTEM LOCAL; astimezone() deliberately honors
            # the machine timezone, rather than mislabelling them as UTC.
            bars = [
                DailyBar(
                    c.timestamp.astimezone(NY).date(),
                    *(Decimal(str(getattr(c, k))) for k in ("open", "high", "low", "close", "volume")),
                )
                for c in raw
            ]
        return sorted({b.day: b for b in bars if b.day <= end}.values(), key=lambda b: b.day)

    def quotes(self, symbols):
        with _SDK_VALUES_LOCK:
            values = self._context().quote(symbols)
            result = {}
            for q in values:
                options = [(q.timestamp, q.last_done, "Intraday", q.prev_close)]
                for attr, session in (
                    ("pre_market_quote", "Pre"),
                    ("post_market_quote", "Post"),
                    ("overnight_quote", "Overnight"),
                ):
                    item = getattr(q, attr, None)
                    if item is not None:
                        options.append((item.timestamp, item.last_done, session, q.prev_close))
                stamp, price, session, prev = max(options, key=lambda v: v[0].astimezone(UTC))
                result[q.symbol] = dict(
                    price=str(price),
                    stamp=stamp.astimezone(UTC).isoformat(),
                    session=session,
                    prev_close=str(prev),
                )
        return result


class MonitorService:
    def __init__(self, store, provider, clock=None):
        self.store, self.provider = store, provider
        self.clock = clock or (lambda: datetime.now(UTC))
        self.lock = threading.RLock()
        self.calendar_failed_at = None

    def load(self, targets, cancel, live_quotes=None, network=True):
        """One worker per page; cached daily data reused while quotes refresh."""
        with self.lock:
            result = self._load(targets, cancel, live_quotes or {}, network)
            archived = [dict(t, archive_state="HELD") for t in targets if t["archive_state"] != "HELD"]
            if archived and not cancel.is_set():
                cached = self._load(archived, cancel, {}, False)
                for value in cached.values():
                    value["quote_status"] = "已不再持有 · 缓存"
                result.update(cached)
            return result

    def _load(self, targets, cancel, live_quotes, network):
        now = self.clock()
        result = {}
        symbols = sorted(
            {t["analysis_symbol"] for t in targets if t["archive_state"] == "HELD" and t["market"] == "US"}
        )
        for target in targets:
            if target["archive_state"] == "HELD" and target["market"] != "US":
                result[target["analysis_symbol"]] = dict(
                    bars=[],
                    metrics=technicals([])[0],
                    series={},
                    price=None,
                    change=None,
                    session="未接入",
                    quote_status="无数据",
                    quote_time="",
                    history_updated="",
                    stale=True,
                    error="已保留此市场持仓；P0技术行情仅接入美股，未用美股交易日替代。",
                    basis=BASIS,
                )
        expected, calendar_error = None, False
        calendar_backoff = self.calendar_failed_at and now - self.calendar_failed_at < timedelta(minutes=5)
        if calendar_backoff:
            calendar_error = True
        if network and symbols and not cancel.is_set() and not calendar_backoff:
            try:
                expected = self.provider.completed_day(now)
                self.calendar_failed_at = None
            except Exception:
                calendar_error = True
                self.calendar_failed_at = now
        # Borrow shared feed quotes when available. Pull only missing/stale symbols.
        pulled, pull_error = {}, set()
        wanted = []
        for symbol in symbols:
            q = live_quotes.get(symbol)
            if q and timedelta(seconds=-5) <= now - q.timestamp <= timedelta(seconds=90):
                pulled[symbol] = dict(price=str(q.price), stamp=q.timestamp.isoformat(), session=q.session)
            elif network:
                state = self.store.state(symbol)
                attempt = state.get("quote_attempt")
                if not attempt or now - datetime.fromisoformat(attempt) >= timedelta(seconds=60):
                    wanted.append(symbol)
        if wanted and not cancel.is_set():
            try:
                pulled.update(self.provider.quotes(wanted))
                pull_error.update(set(wanted) - pulled.keys())
            except Exception:
                pull_error.update(wanted)
        for symbol in symbols:
            if cancel.is_set():
                break
            bars, state = self.store.bars(symbol), self.store.state(symbol)
            error = ""
            attempt = state.get("history_attempt")
            current_session = us_session(
                now, getattr(self.provider, "days", None) or (), getattr(self.provider, "halves", ())
            )
            # A split may become effective before a new daily candle completes.
            # Recheck a small overlap once per session, not every quote tick.
            adjustment_key = (
                current_session.start.isoformat() + current_session.name
                if current_session
                else now.astimezone(NY).date().isoformat() + "Closed"
            )
            due = expected and (
                not state.get("covered_through")
                or state["covered_through"] < expected.isoformat()
                or state.get("adjustment_checked_session") != adjustment_key
            )
            if (
                network
                and due
                and (not attempt or now - datetime.fromisoformat(attempt) >= timedelta(minutes=5))
            ):
                state["history_attempt"] = now.isoformat()
                try:
                    if cancel.wait(0.2):
                        break
                    start = bars[-1].day - timedelta(days=10) if bars else None
                    new = self.provider.history(symbol, start, expected)
                    if cancel.is_set():
                        break
                    if not new or new[-1].day < expected:
                        raise ValueError("日线未覆盖完整交易日")
                    old = {b.day: b for b in bars}
                    revised = any(b.day in old and b != old[b.day] for b in new)
                    if revised:
                        # Corporate actions revise forward-adjusted history. Never mix scales.
                        new = self.provider.history(symbol, None, expected)
                        if not new or new[-1].day < expected:
                            raise ValueError("复权历史刷新不完整")
                        old = {}
                        state.pop("quote", None)
                        incoming = pulled.get(symbol)
                        if incoming and now - datetime.fromisoformat(incoming["stamp"]) > timedelta(
                            seconds=90
                        ):
                            pulled.pop(symbol, None)
                    old.update({b.day: b for b in new})
                    bars = sorted((b for b in old.values() if b.day <= expected), key=lambda b: b.day)[-500:]
                    state.update(
                        covered_through=expected.isoformat(),
                        history_updated=now.isoformat(),
                        history_error=False,
                        adjustment_checked_session=adjustment_key,
                    )
                    self.store.save_market(symbol, state, bars, replace=True)
                except Exception:
                    state["history_error"] = True
            if symbol in wanted:
                state["quote_attempt"] = now.isoformat()
                state["quote_error"] = symbol in pull_error
            if symbol in pulled:
                incoming = pulled[symbol]
                try:
                    value = Decimal(incoming["price"])
                    moment = datetime.fromisoformat(incoming["stamp"])
                    if (
                        not value.is_finite()
                        or value <= 0
                        or moment.tzinfo is None
                        or moment > now + timedelta(seconds=5)
                    ):
                        raise ValueError("无效报价")
                    state["quote"] = incoming
                    state["quote_error"] = False
                except (ValueError, ArithmeticError):
                    state["quote_error"] = True
            q = state.get("quote", {})
            price = Decimal(q["price"]) if q.get("price") else (bars[-1].close if bars else None)
            stamp = datetime.fromisoformat(q["stamp"]) if q.get("stamp") else None
            fresh = bool(
                stamp
                and timedelta(seconds=-5) <= now - stamp <= timedelta(seconds=90)
                and not state.get("quote_error")
            )
            stale = (
                calendar_error
                or bool(state.get("history_error"))
                or (expected is not None and (not bars or bars[-1].day < expected))
            )
            metrics, series = technicals(bars, price)
            previous = (
                Decimal(q["prev_close"]) if q.get("prev_close") and Decimal(q["prev_close"]) > 0 else None
            )
            if previous is None and bars:
                # Intraday latest uses last completed close; a quote on its own
                # completed trading date instead compares the preceding close.
                quote_day = stamp.astimezone(NY).date() if stamp else bars[-1].day
                eligible = [b for b in bars if b.day < quote_day]
                previous = eligible[-1].close if eligible else None
            state["bar_date"] = bars[-1].day.isoformat() if bars else ""
            session = us_session(
                now, getattr(self.provider, "days", None) or (), getattr(self.provider, "halves", ())
            )
            fresh = bool(
                fresh
                and session
                and q.get("session") == session.name
                and session.start <= stamp < session.end
            )
            self.store.save_market(symbol, state, indicators=metrics)
            retry = (
                (datetime.fromisoformat(state["history_attempt"]) + timedelta(minutes=5))
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .strftime("%H:%M")
                if state.get("history_error") and state.get("history_attempt")
                else ""
            )
            error = (
                ("日K未更新，保留缓存；检查网络、凭证或行情权限" + ("；可重试 " + retry if retry else ""))
                if stale
                else ""
            )
            if state.get("quote_error"):
                error += " 报价请求失败，保留旧值；一分钟后可重试。"
            result[symbol] = dict(
                bars=bars,
                metrics=metrics,
                series=series,
                price=price,
                change=deviation(price, previous),
                session=SESSION_NAMES.get(session.name if session else "Closed"),
                quote_session=SESSION_NAMES.get(q.get("session"), "最近收盘"),
                quote_status="实时" if fresh else ("已过期" if stamp else "最近收盘" if bars else "无数据"),
                quote_time=q.get("stamp", ""),
                history_updated=state.get("history_updated", ""),
                stale=stale,
                error=error,
                basis=BASIS,
            )
        return result

    def evaluate_rules(
        self,
        scope_key: str,
        market_data: dict,
        now_utc: datetime | None = None,
    ) -> list[AlertEvent]:
        """
        Evaluate all enabled monitoring rules for this scope against current market data.
        Dispatches desktop / sound notifications and saves alert events to database.
        """
        if not scope_key or not market_data:
            return []

        now = now_utc or self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        enabled_rules = self.store.get_rules(scope_key=scope_key, enabled_only=True)
        if not enabled_rules:
            return []

        triggered_events: list[AlertEvent] = []

        for rule in enabled_rules:
            sym = rule.monitored_symbol or rule.analysis_symbol
            item = market_data.get(sym)
            if not item and rule.analysis_symbol in market_data:
                item = market_data[rule.analysis_symbol]
            if not item:
                continue

            quote_data = {
                "price": item.get("price"),
                "session": item.get("session") or item.get("quote_session"),
                "quote_session": item.get("quote_session"),
                "stamp": item.get("quote_time"),
                "prev_close": None,
            }
            indicators = item.get("metrics") or {}
            series_data = item.get("series") or {}
            bars = item.get("bars") or []

            runtime_state = self.store.get_runtime_state(rule.rule_id)
            event, updated_state = RuleEvaluator.evaluate(
                rule=rule,
                state=runtime_state,
                quote_data=quote_data,
                indicators=indicators,
                series_data=series_data,
                bars=bars,
                now_utc=now,
            )

            # Persist updated state
            self.store.save_runtime_state(updated_state)

            if event:
                # Save alert event to database
                self.store.save_alert_event(event)

                # Deliver notification (desktop and/or sound)
                notification_service.notify_event(
                    event,
                    channels=rule.notification_channels,
                    sound_name=rule.sound_name,
                )

                # Persist rule update (trigger count, last_trigger_at, completion status)
                self.store.update_rule(rule)
                triggered_events.append(event)

        return triggered_events

    def get_status(self, scope_key: str) -> dict:
        stats = self.store.stats(scope_key) if scope_key else {}
        has_cred = bool(self.provider and self.provider.path)
        calendar_error = bool(self.calendar_failed_at)

        if not has_cred:
            status_desc = "未配置凭证"
            status_level = "WARNING"
        elif calendar_error:
            status_desc = "日线受限 · 保留缓存"
            status_level = "WARNING"
        elif stats.get("enabled_rules", 0) > 0:
            status_desc = f"监控中 ({stats.get('enabled_rules', 0)}条规则)"
            status_level = "RUNNING"
        else:
            status_desc = "待命中 (无启用规则)"
            status_level = "IDLE"

        return {
            "status_desc": status_desc,
            "status_level": status_level,
            "targets_count": stats.get("targets_count", 0),
            "manual_count": stats.get("manual_count", 0),
            "enabled_rules": stats.get("enabled_rules", 0),
            "total_rules": stats.get("total_rules", 0),
            "today_alerts": stats.get("today_alerts", 0),
        }

