import hashlib
import json
from collections import Counter
from datetime import date

from test_replay import event, snapshot

from portfolio.domain.analytics import Issue
from portfolio.domain.audit_period import AUDIT_START_DATE, issues_in_period
from portfolio.domain.models import QueryScope
from portfolio.engine.audit import audit
from portfolio.engine.replay import replay
from portfolio.services.analytics_service import AnalyticsService


def scoped_snapshot():
    s = snapshot([])
    s.scope = QueryScope(date(2026, 7, 2))
    return s


def test_boundary_is_inclusive_and_future_and_old_are_excluded():
    s = scoped_snapshot()
    s.issues = [Issue('DQ001', 'ERROR', 'old', '2026-06-30'),
                Issue('DQ001', 'ERROR', 'boundary', '2026-07-01'),
                Issue('DQ001', 'ERROR', 'last', '2026-07-02'),
                Issue('DQ001', 'ERROR', 'future', '2026-07-03'),
                Issue('DQ018', 'WARN', 'old month', '2026-06'),
                Issue('DQ018', 'WARN', 'new month', '2026-07')]
    kept = issues_in_period(s, AUDIT_START_DATE)
    assert [i.message for i in kept] == ['boundary', 'last', 'new month']


def test_undated_source_uses_business_date_never_import_date():
    s = scoped_snapshot()
    s.extras['person_operations'] = [dict(source_record_id=1, business_date='2026-06-30'),
                                     dict(source_record_id=2, business_date='2026-07-01')]
    s.issues = [Issue('DQ005', 'ERROR', 'old', source_ids=(1,)),
                Issue('DQ005', 'ERROR', 'new', source_ids=(2,)),
                Issue('DQ005', 'ERROR', 'unknown', source_ids=(3,))]
    s.extras['import_batches'] = [dict(imported_at_utc='2026-07-02T10:00:00Z')]
    kept = issues_in_period(s, AUDIT_START_DATE)
    assert [(i.message, i.day) for i in kept if i.severity == 'ERROR'] == [('new', '2026-07-01')]
    assert len([i for i in kept if i.severity == 'INFO']) == 1
    assert all(i.severity != 'ERROR' for i in kept if 3 in i.source_ids)


def test_raw_review_dates_and_unknown_are_classified_without_guessing():
    s = scoped_snapshot()
    s.extras['raw_review'] = [dict(id=n, payload_json=json.dumps({'fields':fields})) for n, fields in (
        (1, {'日期':'2026-06-30'}), (2, {'交易日期':'2026/07/01'}),
        (3, {'说明':'no date'}), (4, {'date':'2026-07-03'}))]
    report = audit(s, {}, start_date=AUDIT_START_DATE)
    warnings = [r for r in report.rows if r.values[0] == 'DQ007' and r.values[1] == 'WARN']
    assert len(warnings) == 1 and warnings[0].values[7] == '2'
    assert any(r.values[0] == 'DQ007' and r.values[1] == 'INFO' for r in report.rows)
    assert '2026-07-01' in report.note


def test_cost_replay_keeps_june_purchase_and_audit_only_shows_july():
    s = scoped_snapshot()
    s.events = [event(1, 'BUY', '10', '100', '2026-06-30'),
                event(2, 'SELL', '3', '36', '2026-07-01')]
    positions, traces, _ = replay(s)
    s.issues += [Issue('DQ006','WARN','old ownership','2026-06-30'),
                 Issue('DQ006','WARN','new ownership','2026-07-01')]
    audit(s, positions, start_date=AUDIT_START_DATE)
    assert positions[(1,1,1)].quantity == 7 and positions[(1,1,1)].net == 64
    assert traces[2][0] == 7 and traces[2][1] == 64
    assert any(i.message == 'new ownership' for i in s.issues)
    assert not any(i.message == 'old ownership' for i in s.issues)


def test_default_service_audit_before_cutoff_retains_numbers_and_missing_values(demo_path):
    before = hashlib.sha256(demo_path.read_bytes()).hexdigest()
    scope = QueryScope(date(2026,1,10))
    full = AnalyticsService(demo_path, audit_start=None).calculate(scope)
    limited = AnalyticsService(demo_path).calculate(scope)
    assert any(i.severity != 'INFO' for i in full.issues)
    assert not any(i.severity != 'INFO' for i in limited.issues)
    for name, report in full.reports.items():
        if name != '数据异常':
            assert [r.values for r in report.rows] == [r.values for r in limited.reports[name].rows], name
    assert [(m.name,m.value,m.day) for m in full.metrics] == [(m.name,m.value,m.day) for m in limited.metrics]
    assert hashlib.sha256(demo_path.read_bytes()).hexdigest() == before


def test_full_service_across_boundary_changes_only_quality_state(demo_path):
    scope = QueryScope(date(2026,7,2))
    limited = AnalyticsService(demo_path).calculate(scope)
    full = AnalyticsService(demo_path, audit_start=None).calculate(scope)
    assert Counter(i.severity for i in limited.issues)['ERROR'] < Counter(i.severity for i in full.issues)['ERROR']
    assert [(m.value,m.day) for m in limited.metrics] == [(m.value,m.day) for m in full.metrics]
    assert any(m.value is None for m in limited.metrics)  # ignoring old alerts never fills missing finances
    assert all(not i.day or i.day >= '2026-07-01' for i in limited.issues if i.severity != 'INFO')
