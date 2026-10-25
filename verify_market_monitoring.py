"""Standalone Verification Script for Market Monitoring Center (PRD v2.0).
Tests technical indicators, database storage, rule evaluator, and alerting workflows.
"""

import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from portfolio.domain.monitor_rules import (
    AlertEvent,
    ChannelType,
    CheckMode,
    ConditionType,
    MonitorRule,
    RepeatMode,
    RuntimeState,
    SessionScope,
    describe_rule,
)
from portfolio.domain.technical_indicators import DailyBar, series, technicals
from portfolio.services.monitor_store import MonitorStore
from portfolio.services.notification_service import notification_service
from portfolio.services.rule_evaluator import RuleEvaluator

D = Decimal
UTC = timezone.utc


def test_bollinger_and_indicators():
    print("=== 1. Testing Technical Indicators (including Bollinger Bands) ===")
    bars = []
    base_date = date(2025, 1, 1)
    # Generate 250 bars with predictable upward trend
    for i in range(250):
        d = base_date + timedelta(days=i)
        p = D(100 + i * 0.5)
        bars.append(DailyBar(d, p - D("1.0"), p + D("2.0"), p - D("2.0"), p, D("1000000")))

    curr_price = bars[-1].close
    metrics, ser = technicals(bars, curr_price)

    assert "BOLL_MID" in metrics, "BOLL_MID missing from metrics"
    assert "BOLL_UP" in metrics, "BOLL_UP missing from metrics"
    assert "BOLL_LOW" in metrics, "BOLL_LOW missing from metrics"
    assert metrics["BOLL_UP"] > metrics["BOLL_MID"] > metrics["BOLL_LOW"], "Bollinger bands order incorrect"
    assert "MA20" in metrics and "MA50" in metrics and "MA200" in metrics, "MAs missing"
    assert "RSI14" in metrics, "RSI missing"
    assert "MACD" in metrics and "DIF" in metrics and "DEA" in metrics, "MACD missing"
    assert "HIGH52" in metrics and "LOW52" in metrics, "52-week metrics missing"
    assert "AVAILABLE_HIGH" in metrics and "DRAWDOWN" in metrics, "ATH metrics missing"
    print("✓ Technical indicators & Bollinger Bands verified successfully!")


def test_store_and_migrations():
    print("=== 2. Testing MonitorStore CRUD & Migration ===")
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "monitoring.sqlite3"
        store = MonitorStore(db_path)

        scope_key = "test_scope_123"

        # 1. Manual targets
        store.add_manual_target(scope_key, "TSLA.US", "US")
        store.add_manual_target(scope_key, "NVDA.US", "US")
        targets = store.targets(scope_key)
        assert len(targets) == 2, f"Expected 2 targets, got {len(targets)}"
        assert targets[0]["source_type"] == "MANUAL", "Source type should be MANUAL"

        # 2. Holdings sync preserves manual targets
        sources = [{
            "analysis_symbol": "AAPL.US",
            "market": "US",
            "held_symbol": "AAPL.US",
            "account_id": 1,
            "person_id": 1,
            "quantity": "50",
            "product_name": "Apple Inc.",
        }]
        targets_synced = store.sync(scope_key, sources, datetime.now(UTC))
        assert len(targets_synced) == 3, f"Expected 3 targets (1 holding + 2 manual), got {len(targets_synced)}"

        # 3. Rules CRUD
        rule = MonitorRule(
            rule_id="r_test_1",
            scope_key=scope_key,
            analysis_symbol="AAPL.US",
            monitored_symbol="AAPL.US",
            rule_type="PRICE",
            expression={"operator": "CROSS_ABOVE", "threshold": "250.00"},
            enabled=False,  # Default paused
            name="AAPL 突破 $250",
        )
        store.add_rule(rule)
        fetched = store.get_rule("r_test_1")
        assert fetched is not None and fetched.name == "AAPL 突破 $250"
        assert fetched.enabled is False, "New rule must be paused by default"

        # Enable rule
        store.set_rule_enabled("r_test_1", True)
        assert store.get_rule("r_test_1").enabled is True

        # Duplicate rule
        dup = store.duplicate_rule("r_test_1")
        assert dup is not None and "(副本)" in dup.name
        assert dup.enabled is False, "Duplicated rule must be paused by default"

        # Stats
        stats = store.stats(scope_key)
        assert stats["targets_count"] == 3
        assert stats["manual_count"] == 2
        assert stats["enabled_rules"] == 1
        assert stats["total_rules"] == 2

        # Alert Event CRUD
        evt = AlertEvent(
            event_id="evt_test_1",
            rule_id="r_test_1",
            rule_version=1,
            rule_name="AAPL 突破 $250",
            analysis_symbol="AAPL.US",
            monitored_symbol="AAPL.US",
            observed_price="250.50",
            threshold_value="250.00",
            trigger_reason="突破高于目标价",
            quote_session="Intraday",
            observed_at=datetime.now(UTC).isoformat(),
            detected_at=datetime.now(UTC).isoformat(),
        )
        store.save_alert_event(evt)
        evts = store.get_alert_events()
        assert len(evts) == 1
        assert evts[0].event_id == "evt_test_1"

        store.mark_events_read(["evt_test_1"])
        assert store.get_alert_events(unread_only=True) == []

        # Remove manual target
        store.remove_manual_target(scope_key, "TSLA.US", "US")
        assert len(store.targets(scope_key)) == 2

    print("✓ MonitorStore CRUD & migrations verified successfully!")


def test_rule_evaluator_crossing_semantics():
    print("=== 3. Testing RuleEvaluator Crossing Semantics & Conditions ===")
    now = datetime(2026, 10, 26, 14, 0, 0, tzinfo=UTC)

    # 1. Price Breakout (CROSS_ABOVE) with baseline initialization
    rule = MonitorRule(
        rule_id="r_cross",
        scope_key="test",
        analysis_symbol="TSLA.US",
        monitored_symbol="TSLA.US",
        rule_type="PRICE",
        expression={"operator": "CROSS_ABOVE", "threshold": "200.00"},
        enabled=True,
        repeat_mode="REARM_CROSSING",
    )
    state = RuntimeState(rule_id="r_cross")

    # Tick 1: Price is 205 (Already above threshold on startup).
    # MUST NOT trigger on first observation! Establishes baseline.
    evt, state = RuleEvaluator.evaluate(
        rule=rule,
        state=state,
        quote_data={"price": "205.00", "session": "Intraday"},
        indicators={},
        series_data={},
        bars=[],
        now_utc=now,
    )
    assert evt is None, "Should NOT trigger on first baseline tick even if price > threshold"
    assert state.last_observed_value == "205.00"

    # Tick 2: Price drops to 198 (Crosses below, arms for next crossing above)
    evt, state = RuleEvaluator.evaluate(
        rule=rule,
        state=state,
        quote_data={"price": "198.00", "session": "Intraday"},
        indicators={},
        series_data={},
        bars=[],
        now_utc=now + timedelta(seconds=10),
    )
    assert evt is None, "Price drop should not trigger CROSS_ABOVE"
    assert state.rearmed is True

    # Tick 3: Price crosses above 200 (from 198 to 201)
    evt, state = RuleEvaluator.evaluate(
        rule=rule,
        state=state,
        quote_data={"price": "201.00", "session": "Intraday"},
        indicators={},
        series_data={},
        bars=[],
        now_utc=now + timedelta(seconds=20),
    )
    assert evt is not None, "Must trigger on true upward crossing"
    assert "上穿" in evt.trigger_reason or "突破" in evt.trigger_reason
    assert state.rearmed is False, "Must disarm after trigger under REARM_CROSSING"

    # Tick 4: Price stays at 203 (Still above, but not rearmed)
    evt, state = RuleEvaluator.evaluate(
        rule=rule,
        state=state,
        quote_data={"price": "203.00", "session": "Intraday"},
        indicators={},
        series_data={},
        bars=[],
        now_utc=now + timedelta(seconds=30),
    )
    assert evt is None, "Must NOT re-trigger without dipping below threshold first"

    # 2. RepeatMode: ONCE
    rule_once = MonitorRule(
        rule_id="r_once",
        scope_key="test",
        analysis_symbol="NVDA.US",
        monitored_symbol="NVDA.US",
        rule_type="PRICE",
        expression={"operator": "CROSS_ABOVE", "threshold": "140.00"},
        enabled=True,
        repeat_mode="ONCE",
    )
    state_once = RuntimeState(rule_id="r_once", last_observed_value="138.00", rearmed=True)
    evt, state_once = RuleEvaluator.evaluate(
        rule=rule_once,
        state=state_once,
        quote_data={"price": "141.00", "session": "Intraday"},
        indicators={},
        series_data={},
        bars=[],
        now_utc=now,
    )
    assert evt is not None, "Should trigger once"
    assert rule_once.completed is True and rule_once.enabled is False, "Rule must be completed and disabled"

    # 3. Technical Indicator: Golden Cross (MA_CROSS)
    rule_gc = MonitorRule(
        rule_id="r_gc",
        scope_key="test",
        analysis_symbol="AAPL.US",
        monitored_symbol="AAPL.US",
        rule_type="MA_CROSS",
        expression={"operator": "CROSS_ABOVE", "fast_period": 50, "slow_period": 200},
        enabled=True,
    )
    state_gc = RuntimeState(rule_id="r_gc", last_observed_value="200.00", rearmed=True)
    # Series with MA50 crossing above MA200:
    # prev: MA50=180, MA200=185
    # curr: MA50=186, MA200=185
    series_data = {
        "MA50": [D(180), D(186)],
        "MA200": [D(185), D(185)],
    }
    evt, _ = RuleEvaluator.evaluate(
        rule=rule_gc,
        state=state_gc,
        quote_data={"price": "190.00", "session": "Intraday"},
        indicators={"MA50": D(186), "MA200": D(185)},
        series_data=series_data,
        bars=[],
        now_utc=now,
    )
    assert evt is not None, "MA Golden Cross should trigger"
    assert "金叉" in evt.trigger_reason

    # 4. RSI Overbought (RSI cross below 70)
    rule_rsi = MonitorRule(
        rule_id="r_rsi",
        scope_key="test",
        analysis_symbol="MSFT.US",
        monitored_symbol="MSFT.US",
        rule_type="RSI",
        expression={"operator": "CROSS_BELOW", "threshold": "70.0"},
        enabled=True,
    )
    state_rsi = RuntimeState(rule_id="r_rsi", last_observed_value="400.00", rearmed=True)
    evt, _ = RuleEvaluator.evaluate(
        rule=rule_rsi,
        state=state_rsi,
        quote_data={"price": "400.00", "session": "Intraday"},
        indicators={"RSI14": D("68.5")},
        series_data={"RSI14": [D("72.0"), D("68.5")]},
        bars=[],
        now_utc=now,
    )
    assert evt is not None, "RSI cross below 70 should trigger"
    assert "跌破" in evt.trigger_reason

    # 5. Composite Condition (ALL)
    rule_comp = MonitorRule(
        rule_id="r_comp",
        scope_key="test",
        analysis_symbol="GOOG.US",
        monitored_symbol="GOOG.US",
        rule_type="COMPOSITE",
        expression={
            "logic": "ALL",
            "conditions": [
                {"rule_type": "PRICE", "operator": "CROSS_ABOVE", "threshold": "170.00"},
                {"rule_type": "RSI", "operator": "CROSS_ABOVE", "threshold": "50.0"},
            ],
        },
        enabled=True,
    )
    state_comp = RuntimeState(rule_id="r_comp", last_observed_value="168.00", rearmed=True)
    evt, _ = RuleEvaluator.evaluate(
        rule=rule_comp,
        state=state_comp,
        quote_data={"price": "172.00", "session": "Intraday"},
        indicators={"RSI14": D("55.0")},
        series_data={"RSI14": [D("48.0"), D("55.0")]},
        bars=[],
        now_utc=now,
    )
    assert evt is not None, "Composite condition (ALL) should trigger when both match"
    assert "同时满足" in evt.trigger_reason

    # 6. Session Scope Filtering (REGULAR vs EXTENDED)
    rule_reg = MonitorRule(
        rule_id="r_reg",
        scope_key="test",
        analysis_symbol="AMZN.US",
        monitored_symbol="AMZN.US",
        rule_type="PRICE",
        expression={"operator": "CROSS_ABOVE", "threshold": "180.00"},
        session_scope="REGULAR",
        enabled=True,
    )
    state_reg = RuntimeState(rule_id="r_reg", last_observed_value="175.00", rearmed=True)
    # Quote from Pre-market
    evt, _ = RuleEvaluator.evaluate(
        rule=rule_reg,
        state=state_reg,
        quote_data={"price": "182.00", "session": "Pre"},
        indicators={},
        series_data={},
        bars=[],
        now_utc=now,
    )
    assert evt is None, "Regular session rule must NOT trigger in Pre-market"

    rule_ext = MonitorRule(
        rule_id="r_ext",
        scope_key="test",
        analysis_symbol="AMZN.US",
        monitored_symbol="AMZN.US",
        rule_type="PRICE",
        expression={"operator": "CROSS_ABOVE", "threshold": "180.00"},
        session_scope="EXTENDED",
        enabled=True,
    )
    evt, _ = RuleEvaluator.evaluate(
        rule=rule_ext,
        state=state_reg,
        quote_data={"price": "182.00", "session": "Pre"},
        indicators={},
        series_data={},
        bars=[],
        now_utc=now,
    )
    assert evt is not None, "Extended session rule MUST trigger in Pre-market"

    print("✓ RuleEvaluator crossing semantics & condition combinations verified successfully!")


def test_notification_service():
    print("=== 4. Testing NotificationService Delivery ===")
    evt = AlertEvent(
        event_id="test_notif",
        rule_id="r_1",
        rule_version=1,
        rule_name="测试提醒",
        analysis_symbol="NVDA.US",
        monitored_symbol="NVDA.US",
        observed_price="150.00",
        threshold_value="145.00",
        trigger_reason="突破高于目标价",
        quote_session="Intraday",
        observed_at=datetime.now(UTC).isoformat(),
        detected_at=datetime.now(UTC).isoformat(),
    )
    # Notification delivery shouldn't raise exception
    notification_service.notify_event(evt, channels="desktop,sound", sound_name="Glass")
    print("✓ NotificationService executed without errors!")


if __name__ == "__main__":
    try:
        test_bollinger_and_indicators()
        test_store_and_migrations()
        test_rule_evaluator_crossing_semantics()
        test_notification_service()
        print("\n=======================================================")
        print("ALL MARKET MONITORING CENTER VERIFICATIONS PASSED 100%!")
        print("=======================================================")
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
