"""Re-run rebuild_history for all persons on previously impacted dates (holidays & month-ends)."""
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from portfolio.domain.models import Ledger, QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.intraday_history import rebuild_history, scope_key
from portfolio.services.quote_store import QuoteStore

DEFAULT_DB_PATH = PROJECT_DIR / "数据库/money.sqlite3"
DEFAULT_STORE_PATH = Path.home() / "Library/Application Support/MoneyPortfolio/Money Portfolio/live_quotes.sqlite3"
DEFAULT_CRED_PATH = PROJECT_DIR / "longbridge_app_key.py"

TARGET_DATES = [
    # 假期间隔日
    "2026-01-20",
    "2026-02-17",
    "2026-04-06",
    "2026-05-26",
    "2026-06-22",
    "2026-07-06",
    "2026-09-08",
    # 月末封账日
    "2026-01-30",
    "2026-02-27",
    "2026-03-31",
    "2026-04-30",
    "2026-05-29",
    "2026-06-30",
    "2026-07-31",
    "2026-08-31",
    "2026-09-30",
]

PERSON_TARGETS = [
    (None, "全部人员"),
    (1, "个人"),
    (2, "华"),
    (3, "巴小"),
    (4, "张薇"),
    (5, "拉科"),
    (6, "摸总"),
    (7, "百万"),
    (8, "钱"),
    (9, "陈行"),
]


def run_fixed_dates_backfill():
    source_path = str(DEFAULT_DB_PATH)
    store_path = str(DEFAULT_STORE_PATH)
    cred_path = str(DEFAULT_CRED_PATH)

    store = QuoteStore(DEFAULT_STORE_PATH)
    svc = AnalyticsService(source_path, quote_store=store)
    now_utc = datetime.now(ZoneInfo("UTC"))

    con = sqlite3.connect(store_path)
    cur = con.cursor()

    total_start = time.time()
    print(f"🚀 开始对 {len(TARGET_DATES)} 个修复日期执行全人员重新落库...")

    for pid, pname in PERSON_TARGETS:
        person_ids = (pid,) if pid is not None else None
        p_start = time.time()
        success = 0
        for day in TARGET_DATES:
            scope = QueryScope(
                as_of=date.fromisoformat(day),
                reporting_currency="USD",
                ledger=Ledger.PERSON,
                person_ids=person_ids,
            )
            key = scope_key(source_path, scope)
            # 先删除旧的可能全为 None 的异常点
            cur.execute("DELETE FROM historical_pnl_points WHERE day = ? AND scope_key = ?", (day, key))
            cur.execute("DELETE FROM historical_pnl_warnings WHERE day = ? AND scope_key = ?", (day, key))
            con.commit()

            try:
                analytics = svc.calculate(scope)
                res = rebuild_history(source_path, analytics, cred_path, store, day, now_utc)
                success += 1
            except Exception as e:
                print(f"  ❌ [{pname}] {day} 失败: {e}")

        elapsed = time.time() - p_start
        print(f"✅ 【{pname}】完成 {success}/{len(TARGET_DATES)} 天, 耗时 {elapsed:.2f}s")

    con.close()
    print(f"🎉 全部人员受影响日期重新落库完毕，总耗时: {time.time() - total_start:.2f}s")


if __name__ == "__main__":
    run_fixed_dates_backfill()
