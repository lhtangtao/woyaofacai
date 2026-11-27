"""美股（NYSE/Nasdaq）官方交易日历与半日市规则单元测试。"""
from datetime import date, time

from portfolio.domain.us_market_calendar import (
    format_session_hint,
    get_us_market_session,
    is_us_trading_day,
    previous_us_trading_day,
    us_market_early_closes_for_year,
    us_market_holidays_for_year,
)


def test_us_market_holidays_and_observed_rules():
    """验证主要法定休市日、周末补休规则及特殊历史休市日。"""
    h2026 = us_market_holidays_for_year(2026)
    # 2026-07-04 为周六，提前至周五 2026-07-03 补休
    assert date(2026, 7, 3) in h2026
    # 2026 感恩节为 11/26（第 4 个周四）
    assert date(2026, 11, 26) in h2026
    # 2026 圣诞节为 12/25（周五）
    assert date(2026, 12, 25) in h2026

    # 2022 年元旦为周六，NYSE 规则不提前在 2021-12-31 休市
    h2022 = us_market_holidays_for_year(2022)
    assert date(2021, 12, 31) not in us_market_holidays_for_year(2021)
    assert date(2022, 1, 1) not in h2022

    # 2023 年元旦为周日，顺延至周一 2023-01-02 休市
    assert date(2023, 1, 2) in us_market_holidays_for_year(2023)

    # 2025-01-09 卡特国葬特殊休市
    assert date(2025, 1, 9) in us_market_holidays_for_year(2025)


def test_us_market_early_close_and_session_metadata():
    """验证半日市（感恩节次日黑五、平安夜）及交易时段提示。"""
    early_2026 = us_market_early_closes_for_year(2026)
    # 2026-11-27 感恩节次日（黑五）为半日市
    assert date(2026, 11, 27) in early_2026
    # 2026-07-03 是独立日补休全天休市，不应算作半日市
    assert date(2026, 7, 3) not in early_2026
    # 2026-12-24 周四平安夜为半日市
    assert date(2026, 12, 24) in early_2026

    bf = get_us_market_session(date(2026, 11, 27))
    assert bf.is_trading_day is True
    assert bf.is_early_close is True
    assert bf.regular_close_et == time(13, 0)
    assert bf.after_hours_close_et == time(17, 0)
    assert bf.recommended_trigger_et == time(17, 10)
    assert "半日市" in format_session_hint(bf)

    tg = get_us_market_session(date(2026, 11, 26))
    assert tg.is_trading_day is False
    assert "感恩节" in tg.holiday_name
    assert "休市" in format_session_hint(tg)


def test_previous_us_trading_day_skips_holidays_and_weekends():
    """验证 previous_us_trading_day 连续跳过假期与周末。"""
    # 2026-11-27（黑五）上一个交易日应跳过 11-26（感恩节）直接回到 11-25（周三）
    assert previous_us_trading_day(date(2026, 11, 27)) == date(2026, 11, 25)
    # 2026-07-06（周一）上一个交易日应跳过周末和 07-03（独立日补休）回到 07-02（周四）
    assert is_us_trading_day(date(2026, 7, 3)) is False
    assert previous_us_trading_day(date(2026, 7, 6)) == date(2026, 7, 2)
