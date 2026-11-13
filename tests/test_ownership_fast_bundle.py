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
