"""测试监控规则评估器及序列化"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest

from portfolio.domain.monitor_rules import AlertEvent, MonitorRule, RuntimeState
from portfolio.services.rule_evaluator import RuleEvaluator
from portfolio.services.monitor_store import MonitorStore
from portfolio.services.monitor_service import MonitorService


def test_alert_event_json_dumps_decimal():
    """验证 AlertEvent to_record 能够安全序列化包含 Decimal 的 snapshot 数据"""
    event = AlertEvent(
        event_id="test_evt_1",
        rule_id="test_rule_1",
        rule_version=1,
        rule_name="MU 上穿",
        analysis_symbol="MU.US",
        monitored_symbol="MU.US",
        observed_price="703.39",
        threshold_value="693.0",
        trigger_reason="现价 $703.39 上穿目标价 $693.00",
        quote_session="Intraday",
        observed_at="2026-11-09T14:40:00+00:00",
        detected_at="2026-11-09T14:40:00+00:00",
        input_snapshot={"quote": {"price": Decimal("703.39"), "prev_close": Decimal("690.00")}},
        indicator_snapshot={"MA5": Decimal("700.50")},
        notification_status="DELIVERED",
    )
    rec = event.to_record()
    assert "703.39" in rec["input_snapshot_json"]
    assert "700.50" in rec["indicator_snapshot_json"]


def test_rule_evaluator_cross_above_trigger(tmp_path):
    """验证价格从低于阈值上穿大于等于阈值时正常触发，且能成功持久化到数据库"""
    store = MonitorStore(tmp_path / "monitoring.sqlite3")
    rule = MonitorRule(
        rule_id="rule_cross_test",
        scope_key="test_scope",
        analysis_symbol="MU.US",
        monitored_symbol="MU.US",
        market="US",
        rule_type="PRICE",
        expression={"operator": "CROSS_ABOVE", "threshold": "693.0"},
        check_mode="INTRADAY",
        session_scope="EXTENDED",
        repeat_mode="REARM_CROSSING",
        enabled=True,
        name="MU.US 现价上穿 $693.0",
    )
    store.add_rule(rule)

    service = MonitorService(store, None)
    # 模拟突破前价格 690.00
    with store.connect() as db:
        db.execute(
            "UPDATE monitor_runtime_state SET rearmed=1, last_observed_value='690.000' WHERE rule_id='rule_cross_test'"
        )

    # 当前价格 703.39
    market_data = {
        "MU.US": {
            "price": Decimal("703.39"),
            "session": "Intraday",
            "quote_session": "Intraday",
            "quote_time": "2026-11-09T14:40:00+00:00",
        }
    }
    events = service.evaluate_rules("test_scope", market_data)
    assert len(events) == 1
    assert "上穿目标价 $693.00" in events[0].trigger_reason

    # 验证数据库中 alert_events 与 trigger_count 成功写入
    with store.connect() as db:
        alerts = db.execute("SELECT * FROM alert_events WHERE rule_id='rule_cross_test'").fetchall()
        assert len(alerts) == 1
        r_db = db.execute("SELECT trigger_count FROM monitor_rules WHERE rule_id='rule_cross_test'").fetchone()
        assert r_db["trigger_count"] == 1
