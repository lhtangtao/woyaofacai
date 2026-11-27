"""美股（NYSE/Nasdaq）官方交易日历与半日市（Early Close）规则模块。

零外部第三方依赖，严格遵循 NYSE/Nasdaq 官方休市与提前收盘制度：
- 休市日：周末 + 10 个美股法定休市日（含周末顺延/提前补休规则）+ 特殊历史休市日；
- 半日市（常规盘 13:00 ET 收盘、盘后 17:00 ET 结束）：感恩节次日（黑五）、平安夜（周一至周四）、独立日前一日（7/3 周一至周四）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from functools import lru_cache
from typing import Dict, Optional

# 特殊非周期性美股全天休市日（如总统国葬等）
SPECIAL_MARKET_CLOSURES: Dict[date, str] = {
    date(2025, 1, 9): "前总统卡特国葬休市",
}


@dataclass(frozen=True)
class USMarketSession:
    """单个美东自然日的美股交易时段元数据。"""

    trade_date: date
    is_trading_day: bool
    is_early_close: bool
    holiday_name: str
    regular_close_et: Optional[time]
    after_hours_close_et: Optional[time]
    recommended_trigger_et: Optional[time]

    def to_dict(self) -> Dict[str, object]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "is_trading_day": self.is_trading_day,
            "is_early_close": self.is_early_close,
            "holiday_name": self.holiday_name,
            "regular_close_et": self.regular_close_et.strftime("%H:%M") if self.regular_close_et else None,
            "after_hours_close_et": self.after_hours_close_et.strftime("%H:%M") if self.after_hours_close_et else None,
            "recommended_trigger_et": self.recommended_trigger_et.strftime("%H:%M") if self.recommended_trigger_et else None,
        }


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian 算法计算指定年份的复活节周日。"""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    rem = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * rem) // 451
    month = (h + rem - 7 * m + 114) // 31
    day = ((h + rem - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """返回指定年月第 n 个星期几（weekday: 0=周一 .. 6=周日）。"""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """返回指定年月最后一个星期几。"""
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    last = next_first - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _observed_fixed_holiday(d: date, *, allow_friday_if_saturday: bool = True) -> Optional[date]:
    """按 NYSE 规则计算固定日期假期的实际补休交易日。"""
    wd = d.weekday()
    if wd == 5:  # 周六
        return (d - timedelta(days=1)) if allow_friday_if_saturday else None
    if wd == 6:  # 周日
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def us_market_holidays_for_year(year: int) -> Dict[date, str]:
    """返回指定年份 NYSE/Nasdaq 所有官方休市日（不含普通周末）。"""
    holidays: Dict[date, str] = {}

    # 1. 元旦（1/1：周日顺延至周一 1/2；NYSE 规则下若 1/1 为周六则不提前在去年 12/31 休市）
    nyd = _observed_fixed_holiday(date(year, 1, 1), allow_friday_if_saturday=False)
    if nyd is not None:
        holidays[nyd] = "元旦 (New Year's Day)"

    # 2. 马丁·路德·金纪念日（1 月第 3 个周一）
    holidays[_nth_weekday_of_month(year, 1, 0, 3)] = "马丁·路德·金纪念日 (MLK Day)"

    # 3. 总统日（2 月第 3 个周一）
    holidays[_nth_weekday_of_month(year, 2, 0, 3)] = "总统日 (Presidents' Day)"

    # 4. 耶稣受难日（复活节前周五）
    holidays[_easter_sunday(year) - timedelta(days=2)] = "耶稣受难日 (Good Friday)"

    # 5. 阵亡将士纪念日（5 月最后一个周一）
    holidays[_last_weekday_of_month(year, 5, 0)] = "阵亡将士纪念日 (Memorial Day)"

    # 6. 六月节（2022 年起成为 NYSE 法定休市日）
    if year >= 2022:
        jt = _observed_fixed_holiday(date(year, 6, 19), allow_friday_if_saturday=True)
        if jt is not None:
            holidays[jt] = "六月节 (Juneteenth)"

    # 7. 独立日（7/4）
    ind = _observed_fixed_holiday(date(year, 7, 4), allow_friday_if_saturday=True)
    if ind is not None:
        holidays[ind] = "独立日 (Independence Day)"

    # 8. 劳动节（9 月第 1 个周一）
    holidays[_nth_weekday_of_month(year, 9, 0, 1)] = "劳动节 (Labor Day)"

    # 9. 感恩节（11 月第 4 个周四）
    holidays[_nth_weekday_of_month(year, 11, 3, 4)] = "感恩节 (Thanksgiving Day)"

    # 10. 圣诞节（12/25）
    xmas = _observed_fixed_holiday(date(year, 12, 25), allow_friday_if_saturday=True)
    if xmas is not None:
        holidays[xmas] = "圣诞节 (Christmas Day)"

    # 特殊非周期性休市
    for d, name in SPECIAL_MARKET_CLOSURES.items():
        if d.year == year:
            holidays[d] = name

    return holidays


@lru_cache(maxsize=64)
def us_market_early_closes_for_year(year: int) -> Dict[date, str]:
    """返回指定年份 NYSE/Nasdaq 所有官方半日市（13:00 ET 收盘、17:00 ET 盘后结束）。"""
    early: Dict[date, str] = {}
    holidays = us_market_holidays_for_year(year)

    # 1. 感恩节次日（黑色星期五）
    thanksgiving = _nth_weekday_of_month(year, 11, 3, 4)
    black_friday = thanksgiving + timedelta(days=1)
    if black_friday not in holidays and black_friday.weekday() < 5:
        early[black_friday] = "感恩节次日半日市 (Black Friday)"

    # 2. 平安夜（12/24，若为周一至周四且本身非圣诞节补休日）
    xmas_eve = date(year, 12, 24)
    if xmas_eve.weekday() <= 3 and xmas_eve not in holidays:
        early[xmas_eve] = "平安夜半日市 (Christmas Eve)"

    # 3. 独立日前一日（7/3，若为周一至周四且本身非独立日补休日）
    july_3 = date(year, 7, 3)
    if july_3.weekday() <= 3 and july_3 not in holidays:
        early[july_3] = "独立日前一日半日市"

    return early


def get_us_market_session(d: date) -> USMarketSession:
    """查询指定美东日期的美股交易状态（正常交易日 / 半日市 / 休市日）。"""
    if d.weekday() >= 5:
        return USMarketSession(
            trade_date=d,
            is_trading_day=False,
            is_early_close=False,
            holiday_name="周末休市",
            regular_close_et=None,
            after_hours_close_et=None,
            recommended_trigger_et=None,
        )

    holidays = us_market_holidays_for_year(d.year)
    if d in holidays:
        return USMarketSession(
            trade_date=d,
            is_trading_day=False,
            is_early_close=False,
            holiday_name=holidays[d],
            regular_close_et=None,
            after_hours_close_et=None,
            recommended_trigger_et=None,
        )

    early = us_market_early_closes_for_year(d.year)
    if d in early:
        return USMarketSession(
            trade_date=d,
            is_trading_day=True,
            is_early_close=True,
            holiday_name=early[d],
            regular_close_et=time(13, 0),
            after_hours_close_et=time(17, 0),
            recommended_trigger_et=time(17, 10),
        )

    return USMarketSession(
        trade_date=d,
        is_trading_day=True,
        is_early_close=False,
        holiday_name="",
        regular_close_et=time(16, 0),
        after_hours_close_et=time(20, 0),
        recommended_trigger_et=time(20, 10),
    )


def is_us_trading_day(d: date) -> bool:
    """判断指定美东日期是否为美股交易日。"""
    return get_us_market_session(d).is_trading_day


def previous_us_trading_day(d: date) -> date:
    """返回严格早于 d 的最近一个美股交易日。"""
    cur = d - timedelta(days=1)
    while not is_us_trading_day(cur):
        cur -= timedelta(days=1)
    return cur


def format_session_hint(session: USMarketSession) -> str:
    """生成适用于顶部状态栏的简洁中文提示文案。"""
    if not session.is_trading_day:
        return f"今日美股休市（{session.holiday_name}），自动任务将跳过"
    if session.is_early_close:
        trig = session.recommended_trigger_et.strftime("%H:%M") if session.recommended_trigger_et else "17:10"
        return f"今日美股半日市（13:00 ET 收盘），美东 {trig} 自动同步并生成「个人」日报"
    trig = session.recommended_trigger_et.strftime("%H:%M") if session.recommended_trigger_et else "20:10"
    return f"美东 {trig} 自动同步并生成「个人」日报"
