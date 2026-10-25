"""Backfill 2026 monthly high-frequency intraday PnL curves for 'OTHERS' (all non-personal enabled persons)."""
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


def run_others_backfill():
    source_path = str(DEFAULT_DB_PATH)
    store_path = str(DEFAULT_STORE_PATH)
    cred_path = str(DEFAULT_CRED_PATH)

    store = QuoteStore(DEFAULT_STORE_PATH)
    svc = AnalyticsService(source_path, quote_store=store)
    now_utc = datetime.now(ZoneInfo("UTC"))

    con = sqlite3.connect(store_path)
    cur = con.cursor()

    cur.execute("SELECT day FROM us_calendar WHERE day LIKE '2026-%' AND day <= '2026-10-23' ORDER BY day DESC")
    all_trading_days = [r[0] for r in cur.fetchall()]
    print(f"Total 2026 trading days: {len(all_trading_days)}")

    other_ids = (2, 3, 4, 5, 6, 7, 8, 9)
    total_start = time.time()
    completed = 0
    skipped = 0
    no_positions = 0
    failed = 0

    print("🚀 开始为【他人】(OTHERS: person_ids=(2..9)) 补充 2026 年全量高频数据...")

    for idx, day in enumerate(all_trading_days, 1):
        scope = QueryScope(
            as_of=date.fromisoformat(day),
            reporting_currency="USD",
            ledger=Ledger.PERSON,
            person_ids=other_ids,
        )
        key = scope_key(source_path, scope)
        cur.execute("SELECT count(*) FROM historical_pnl_points WHERE day = ? AND scope_key = ?", (day, key))
        existing_pts = cur.fetchone()[0]
        if existing_pts >= 288:
            skipped += 1
            continue

        try:
            analytics = svc.calculate(scope)
            res = rebuild_history(source_path, analytics, cred_path, store, day, now_utc)
            if res == "当前筛选没有当日期初持仓或成交。":
                no_positions += 1
            else:
                completed += 1
            if idx % 20 == 0 or idx == len(all_trading_days):
                print(f"  [{idx}/{len(all_trading_days)}] {day}: 处理完成 | {res or '正常'}")
        except Exception as e:
            failed += 1
            print(f"  ❌ [{idx}/{len(all_trading_days)}] {day}: 失败 - {e}")

    con.close()
    elapsed = time.time() - total_start
    print(f"🎉 【他人】2026 年全量高频数据处理完毕！完成={completed}, 跳过={skipped}, 无持仓={no_positions}, 失败={failed}, 耗时={elapsed:.2f}s")


if __name__ == "__main__":
    run_others_backfill()
