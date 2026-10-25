"""Backfill 2026 monthly high-frequency intraday PnL curves into live_quotes.sqlite3."""
import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

# Add money3/src to pythonpath
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from portfolio.domain.models import Ledger, QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.intraday_history import rebuild_history
from portfolio.services.quote_store import QuoteStore

DEFAULT_DB_PATH = PROJECT_DIR / "数据库/money.sqlite3"
DEFAULT_CRED_PATH = PROJECT_DIR / "longbridge_app_key.py"
DEFAULT_STORE_PATH = Path.home() / "Library/Application Support/MoneyPortfolio/Money Portfolio/live_quotes.sqlite3"
REPORT_MD_PATH = PROJECT_DIR / "reports" / "2026_全年月度日内走势补齐报告.md"
REPORT_JSON_PATH = PROJECT_DIR / "reports" / "2026_全年月度日内走势补齐报告.json"


def get_month_trading_days(conn, month_str, cutoff_day="2026-10-23"):
    """Query US trading days for a specific month up to cutoff_day."""
    cur = conn.cursor()
    cur.execute(
        "SELECT day FROM us_calendar WHERE day LIKE ? AND day <= ? ORDER BY day",
        (f"{month_str}%", cutoff_day),
    )
    days = [r[0] for r in cur.fetchall() if r[0]]
    if not days:
        # Fallback to market_prices if us_calendar lacks it
        db2 = sqlite3.connect(DEFAULT_DB_PATH)
        cur2 = db2.cursor()
        cur2.execute(
            "SELECT DISTINCT trade_date FROM market_prices WHERE trade_date LIKE ? AND trade_date <= ? ORDER BY trade_date",
            (f"{month_str}%", cutoff_day),
        )
        days = [r[0] for r in cur2.fetchall() if r[0]]
        db2.close()
    return days


def check_day_completed(store_conn, day):
    """Check if the given trading day has already been fully backfilled (288 points)."""
    cur = store_conn.cursor()
    cur.execute(
        "SELECT count(*) FROM historical_pnl_points WHERE day = ?",
        (day,),
    )
    row = cur.fetchone()
    # A complete day typically has 288 half-hour/5-min sampling points (or >= 288)
    return row[0] >= 288


def run_backfill(months=None, cutoff_day="2026-10-23", force=False):
    db_path = str(DEFAULT_DB_PATH)
    cred_path = str(DEFAULT_CRED_PATH)
    store = QuoteStore(DEFAULT_STORE_PATH)
    store_conn = sqlite3.connect(DEFAULT_STORE_PATH)

    if not months:
        # Default all months in 2026 up to October: 09 downto 01, plus 10 if incomplete
        months = [f"2026-{m:02d}" for m in range(9, 0, -1)]

    print("=" * 70)
    print("🚀 开始 2026 全年高频日内走势全量补齐")
    print(f"目标月份列表: {months}")
    print(f"源数据库: {db_path}")
    print(f"行情缓存库: {DEFAULT_STORE_PATH}")
    print("=" * 70)

    svc = AnalyticsService(db_path)
    now_utc = datetime.now(ZoneInfo("UTC"))

    overall_results = []
    total_days_processed = 0
    total_skipped = 0
    start_time = time.time()

    for month_str in months:
        month_days = get_month_trading_days(store_conn, month_str, cutoff_day)
        print(f"\n📅 【{month_str}】 待检查美股交易日: {len(month_days)} 天")
        month_res = {
            "month": month_str,
            "total_days": len(month_days),
            "days": [],
            "completed": 0,
            "skipped": 0,
            "failed": 0,
        }

        for idx, day in enumerate(month_days, 1):
            if not force and check_day_completed(store_conn, day):
                print(f"  [{idx}/{len(month_days)}] {day}: 已完整落库，跳过。")
                month_res["skipped"] += 1
                month_res["days"].append({
                    "day": day,
                    "status": "SKIPPED_EXISTING",
                    "points": 288,
                })
                total_skipped += 1
                continue

            t0 = time.time()
            max_retries = 2
            warning = None
            success = False

            for attempt in range(max_retries + 1):
                try:
                    scope = QueryScope(
                        as_of=date.fromisoformat(day),
                        ledger=Ledger.PERSON.value,
                        person_ids=(1,),
                        reporting_currency="USD",
                    )
                    analytics = svc.calculate(scope)
                    warning = rebuild_history(db_path, analytics, cred_path, store, day, now_utc)
                    success = True
                    break
                except Exception as e:
                    print(f"  ⚠️ {day} (尝试 {attempt+1}/{max_retries+1}) 异常: {e}")
                    if attempt < max_retries:
                        time.sleep(3)
                    else:
                        warning = f"执行异常：{e}"

            elapsed = time.time() - t0
            cur = store_conn.cursor()
            cur.execute("SELECT count(*) FROM historical_pnl_points WHERE day = ?", (day,))
            pts = cur.fetchone()[0]

            if success:
                print(f"  ✅ [{idx}/{len(month_days)}] {day}: 落库 {pts} 点 ({elapsed:.1f}s) | 备注: {warning or '正常'}")
                month_res["completed"] += 1
                month_res["days"].append({
                    "day": day,
                    "status": "SUCCESS",
                    "points": pts,
                    "warning": warning or "",
                    "elapsed_sec": round(elapsed, 2),
                })
                total_days_processed += 1
            else:
                print(f"  ❌ [{idx}/{len(month_days)}] {day}: 失败 ({elapsed:.1f}s) | {warning}")
                month_res["failed"] += 1
                month_res["days"].append({
                    "day": day,
                    "status": "FAILED",
                    "points": pts,
                    "warning": warning or "",
                    "elapsed_sec": round(elapsed, 2),
                })

            # Brief pause to respect API rate limits
            time.sleep(0.3)

        overall_results.append(month_res)
        _save_reports(overall_results, start_time, total_days_processed, total_skipped)

    store_conn.close()
    print("\n" + "=" * 70)
    print(f"🎉 2026 全年高频日内走势补齐完成！总耗时: {time.time()-start_time:.1f}s")
    print(f"新处理交易日: {total_days_processed}，已有跳过: {total_skipped}")
    print(f"报告已更新至: {REPORT_MD_PATH}")
    print("=" * 70)


def _save_reports(results, start_time, total_processed, total_skipped):
    elapsed_total = round(time.time() - start_time, 1)
    report_data = {
        "generated_at": datetime.now().isoformat(),
        "elapsed_total_seconds": elapsed_total,
        "total_days_newly_processed": total_processed,
        "total_days_skipped_existing": total_skipped,
        "months": results,
    }
    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    # Generate Markdown Report
    lines = [
        "# 2026 全年月度高频日内走势补齐验收报告",
        "",
        f"- **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **总耗时**：{elapsed_total} 秒",
        f"- **新处理交易日**：{total_processed} 个",
        f"- **复用既有缓存交易日**：{total_skipped} 个",
        f"- **业务口径**：归属「个人」· 全部渠道 · USD",
        "",
        "## 各月份补齐概况",
        "",
        "| 月份 | 美股交易日 | 成功补齐 | 既有跳过 | 异常失败 | 状态 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for m in results:
        status_badge = "✅ 完整就绪" if m["failed"] == 0 else f"⚠️ 存在 {m['failed']} 天异常"
        lines.append(
            f"| {m['month']} | {m['total_days']} | {m['completed']} | {m['skipped']} | {m['failed']} | {status_badge} |"
        )

    lines.extend([
        "",
        "## 详细记录与特殊规则说明",
        "",
        "- **长桥高频K线**：按美东交易时段（夜盘、盘前、盘中、盘后）自动向长桥 OpenAPI 获取 5 分钟不复权 K 线并本地落库于 `intraday_candles` 表。",
        "- **第一证券开盘估算规则**：仅有日期且缺失准确 UTC 撮合时刻的第一证券流水统一按当日美东 09:30 开盘时刻估算（遵循系统统一既定口径），未改写原始流水。",
        "- **断点续传支持**：脚本支持秒级跳过已满 288 点的历史日期，多次运行安全幂等。",
    ])

    with open(REPORT_MD_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill 2026 intraday PnL curves")
    parser.add_argument("--months", nargs="+", help="Specific months to backfill, e.g. 2026-09 2026-08")
    parser.add_argument("--force", action="store_true", help="Force recalculate existing completed days")
    args = parser.parse_args()

    run_backfill(months=args.months, force=args.force)
