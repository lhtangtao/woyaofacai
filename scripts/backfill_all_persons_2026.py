"""Backfill 2026 monthly high-frequency intraday PnL curves for ALL persons into live_quotes.sqlite3."""
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


def run_all_persons_backfill():
    source_path = str(DEFAULT_DB_PATH)
    store_path = str(DEFAULT_STORE_PATH)
    cred_path = str(DEFAULT_CRED_PATH)

    store = QuoteStore(DEFAULT_STORE_PATH)
    svc = AnalyticsService(source_path, quote_store=store)
    now_utc = datetime.now(ZoneInfo("UTC"))

    con = sqlite3.connect(store_path)
    cur = con.cursor()

    # 获取所有交易日 (2026-01 至 2026-10-23)
    cur.execute("SELECT day FROM us_calendar WHERE day LIKE '2026-%' AND day <= '2026-10-23' ORDER BY day DESC")
    all_trading_days = [r[0] for r in cur.fetchall()]
    print(f"Total 2026 trading days: {len(all_trading_days)}")

    # 所有人清单 (None 代表 全部人员)
    # persons: [(1, '个人'), (2, '华'), (3, '巴小'), (4, '张薇'), (5, '拉科'), (6, '摸总'), (7, '百万'), (8, '钱'), (9, '陈行')]
    person_targets = [
        (5, "拉科"),
        (None, "全部人员"),
        (2, "华"),
        (6, "摸总"),
        (7, "百万"),
        (8, "钱"),
        (3, "巴小"),
        (4, "张薇"),
        (9, "陈行"),
        (1, "个人"),
    ]

    total_start = time.time()
    summary_report = {}

    for pid, pname in person_targets:
        print(f"\n==========================================")
        print(f"🚀 开始处理人员：【{pname}】 (person_id={pid})")
        print(f"==========================================")
        p_start = time.time()
        p_res = {"completed": 0, "skipped_existing": 0, "no_positions": 0, "failed": 0}

        for idx, day in enumerate(all_trading_days, 1):
            person_ids = (pid,) if pid is not None else None
            scope = QueryScope(
                as_of=date.fromisoformat(day),
                reporting_currency="USD",
                ledger=Ledger.PERSON,
                person_ids=person_ids,
            )
            key = scope_key(source_path, scope)

            cur.execute("SELECT count(*) FROM historical_pnl_points WHERE day = ? AND scope_key = ?", (day, key))
            existing_pts = cur.fetchone()[0]
            if existing_pts >= 288:
                p_res["skipped_existing"] += 1
                continue

            try:
                analytics = svc.calculate(scope)
                res = rebuild_history(source_path, analytics, cred_path, store, day, now_utc)
                if res == "当前筛选没有当日期初持仓或成交。":
                    p_res["no_positions"] += 1
                else:
                    cur.execute("SELECT count(*) FROM historical_pnl_points WHERE day = ? AND scope_key = ?", (day, key))
                    new_pts = cur.fetchone()[0]
                    p_res["completed"] += 1
                    if idx % 10 == 0 or idx == len(all_trading_days):
                        print(f"  [{idx}/{len(all_trading_days)}] {day}: 落库 {new_pts} 点 | {res or '正常'}")
            except Exception as e:
                p_res["failed"] += 1
                print(f"  ❌ [{idx}/{len(all_trading_days)}] {day}: 异常 - {e}")

        elapsed_p = time.time() - p_start
        print(f"✅ 【{pname}】完成: 落库={p_res['completed']}, 已有跳过={p_res['skipped_existing']}, 无仓位={p_res['no_positions']}, 耗时={elapsed_p:.2f}s")
        summary_report[pname] = p_res

    total_elapsed = time.time() - total_start
    print("\n==========================================")
    print(f"🎉 全部人员 2026 全年月度高频数据处理完毕！总耗时: {total_elapsed:.2f}s")
    print(json.dumps(summary_report, ensure_ascii=False, indent=2))
    print("==========================================")


if __name__ == "__main__":
    run_all_persons_backfill()
