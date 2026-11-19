"""验证股票归属轻量化汇总 calculate_ownership_bundle 与全量 calculate_people 口径完全一致及缓存命中。"""

from datetime import UTC, date, datetime

from portfolio.db.connection import readonly_connection
from portfolio.db.repositories.analytics import instrument_catalog
from portfolio.domain.models import QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.ownership_analysis import analyze, choices


def test_ownership_bundle_parity_and_cache(demo_path):
    """轻量 bundle 在持仓中/已清仓/全部状态下与全量 calculate_people 结果完全一致，且支持缓存复用。"""
    service = AnalyticsService(demo_path)
    scope = QueryScope(date(2026, 11, 14), include_disabled=True, start_date=None)

    full_bundle = service.calculate_people(scope)
    fast_bundle = service.calculate_ownership_bundle(scope)
    cached_bundle = service.calculate_ownership_bundle(scope)
    assert cached_bundle is fast_bundle

    with readonly_connection(demo_path) as conn:
        catalog = instrument_catalog(conn)
    now = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)

    for status in ("holding", "closed", "all"):
        full_choices = choices(full_bundle, status=status)
        fast_choices = choices(fast_bundle, status=status)
        assert fast_choices == full_choices
        for item in fast_choices[:10]:
            inst_id = item["id"]
            full_res = analyze(full_bundle, inst_id, catalog, {}, None, now, status=status)
            fast_res = analyze(fast_bundle, inst_id, catalog, {}, None, now, status=status)
            assert fast_res == full_res


def test_stale_identity_cache_eviction_prevents_memory_bloat(demo_path, monkeypatch):
    """源数据/行情身份变化后，旧身份缓存应立即驱逐，且 raw_index 不会全表膨胀。"""
    service = AnalyticsService(demo_path)
    rev = ["v1"]
    orig_id = service.identity
    monkeypatch.setattr(service, "identity", lambda: (*orig_id(), rev[0]))

    scope1 = QueryScope(date(2026, 11, 14), person_ids=(1,))
    scope2 = QueryScope(date(2026, 11, 14), person_ids=(2,))
    res1 = service.calculate(scope1)
    service.calculate(scope2)
    assert len(service._cache) == 2
    assert len(res1.reports["原始记录"].rows) <= 500

    # 身份变更后重新计算，旧身份的 2 条失效缓存必须全部被驱逐，仅保留新身份的 1 条
    rev[0] = "v2"
    service.calculate(scope1)
    assert len(service._cache) == 1
    assert all(k[0][-1] == "v2" for k in service._cache)
