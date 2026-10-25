import hashlib
import json
import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from portfolio.domain.models import QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.fx_service import FxStore


def payload(*rows):
    return json.dumps([{'date': day, 'base': 'USD', 'quote': 'CNY', 'rate': rate} for day, rate in rows]).encode()


def test_sync_decimal_audit_idempotent(tmp_path):
    store = FxStore(tmp_path / 'fx.sqlite3')
    def fetch(url):
        return payload(('2026-01-02', '7.12345678'))
    result = store.sync('2026-01-01', '2026-01-04', fetch=fetch)
    assert result['count'] == 1
    again = store.sync('2026-01-01', '2026-01-04', fetch=lambda _: pytest.fail('unnecessary network'))
    assert again['cached']
    with sqlite3.connect(store.path) as c:
        assert c.execute('SELECT rate_e8 FROM fx_rates').fetchone()[0] == 712345678
        raw, digest = c.execute('SELECT payload_json,payload_sha256 FROM imports').fetchone()
        assert hashlib.sha256(raw.encode()).hexdigest() == digest


def test_prior_rate_dates_and_coverage_no_lookahead(tmp_path):
    store = FxStore(tmp_path / 'fx.sqlite3')
    store.sync('2026-01-01', '2026-01-12', fetch=lambda _: payload(('2026-01-02','7'),('2026-01-05','7.1')))
    rows = {r['rate_date']: r for r in store.supplement([], '2026-01-14')}
    assert '2026-01-01' not in rows
    assert rows['2026-01-04']['rate_e8'] == 700000000
    assert rows['2026-01-04']['published_date'] == '2026-01-02'
    assert rows['2026-01-05']['rate_e8'] == 710000000
    assert '2026-01-13' not in rows
    assert rows['2026-01-04']['source_url'].startswith('https://')


def test_stale_rates_stop_after_seven_days(tmp_path):
    store = FxStore(tmp_path / 'fx.sqlite3')
    store.sync('2026-01-01','2026-01-20',fetch=lambda _: payload(('2026-01-02','7')))
    days = {r['rate_date'] for r in store.supplement([], '2026-01-20')}
    assert '2026-01-09' in days and '2026-01-10' not in days


def test_original_rate_and_conflict_not_overwritten(tmp_path):
    store = FxStore(tmp_path / 'fx.sqlite3')
    store.sync('2026-01-01','2026-01-04',fetch=lambda _: payload(('2026-01-02','7')))
    source = [dict(rate_date='2026-01-02', base_currency='USD', quote_currency='CNY',rate_e8=680000000,provider='original',source_record_id=1)]
    rows = store.supplement(source,'2026-01-04')
    assert [r for r in rows if r['rate_date']=='2026-01-02'] == source
    assert source[0]['rate_e8'] == 680000000


@pytest.mark.parametrize('bad', [b'[]',b'{}',payload(('2026-01-02','-1')),payload(('2027-01-01','7')),payload(('2026-01-02','NaN'))])
def test_invalid_response_keeps_prior_cache(tmp_path,bad):
    store = FxStore(tmp_path/'fx.sqlite3')
    store.sync('2026-01-01','2026-01-04',fetch=lambda _:payload(('2026-01-02','7')))
    before=store.path.read_bytes()
    with pytest.raises(ValueError):
        store.sync('2026-01-01','2026-01-04',force=True,fetch=lambda _:bad)
    assert store.path.read_bytes()==before


def test_offline_preserves_cache(tmp_path):
    store=FxStore(tmp_path/'fx.sqlite3')
    store.sync('2026-01-01','2026-01-04',fetch=lambda _:payload(('2026-01-02','7')))
    def offline(url):
        raise TimeoutError()
    with pytest.raises(ValueError,match='已保留'):
        store.sync('2026-01-01','2026-01-04',force=True,fetch=offline)
    assert store.supplement([], '2026-01-04')


def test_reports_recalculate_after_fx_import_and_source_unchanged(tmp_path,demo_path):
    store=FxStore(tmp_path/'fx.sqlite3')
    service=AnalyticsService(demo_path,fx_store=store,audit_start=None)
    scope=QueryScope(date(2026,1,10),reporting_currency='CNY')
    before=demo_path.read_bytes()
    first=service.calculate(scope)
    assert first.metrics[0].value is None
    store.sync('2026-01-01','2026-01-10',fetch=lambda _:payload(('2026-01-02','7'),('2026-01-09','7')))
    second=service.calculate(scope)
    assert second is not first
    assert second.metrics[0].value == Decimal('4620')
    assert not any(i.rule=='DQ011' and '唯一 USD/CNY' in i.message for i in second.issues)
    assert demo_path.read_bytes()==before
    row=second.reports['当前持仓'].rows[0]
    assert row.metadata['fx_published_date']=='2026-01-09'
