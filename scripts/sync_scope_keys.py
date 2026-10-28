"""Sync all historical_pnl_points, versions, warnings from money_2 scope_keys to money3 scope_keys."""

if __name__ == "__main__":
    raise SystemExit("跨项目盈亏缓存重贴标签已停用。请运行 python -m portfolio.derived rebuild --mode changed，从Money3事实重新计算。")

from datetime import date
from pathlib import Path
import sqlite3
import sys
import time

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from portfolio.domain.models import Ledger, QueryScope
from portfolio.services.intraday_history import scope_key

P1 = "/Users/tangtao/money/money_2/数据库/money.sqlite3"
P2_OLD = "/Users/tangtao/money/money3/数据库/money.sqlite3"
P3 = "/Users/tangtao/money3/数据库/money.sqlite3"
STORE_PATH = "/Users/tangtao/Library/Application Support/MoneyPortfolio/Money Portfolio/live_quotes.sqlite3"


def sync_keys():
    raise RuntimeError("跨项目盈亏重贴标签已停用；请通过portfolio.derived从当前Money3事实重算。")
    from portfolio.services.intraday_history import source_version

    con = sqlite3.connect(STORE_PATH)
    cur = con.cursor()

    cur.execute("SELECT DISTINCT day FROM historical_pnl_points WHERE day LIKE '2026-%'")
    days = [r[0] for r in cur.fetchall()]

    persons = [
        None,  # 全部人员
        "OTHERS",  # 他人
        1, 2, 3, 4, 5, 6, 7, 8, 9,
    ]

    source_paths = [P3, P2_OLD, P1]
    start_dates = [date(2025, 1, 1), None]
    current_ver = source_version(P3) if Path(P3).exists() else None

    key_map = {}
    for d in days:
        day_obj = date.fromisoformat(d)
        for target in persons:
            if target == "OTHERS":
                pids = (2, 3, 4, 5, 6, 7, 8, 9)
            elif target is not None:
                pids = (target,)
            else:
                pids = None
            target_keys = [
                scope_key(
                    P3,
                    QueryScope(
                        as_of=day_obj,
                        start_date=st,
                        reporting_currency="USD",
                        ledger=Ledger.PERSON,
                        person_ids=pids,
                    ),
                )
                for st in start_dates
            ]
            for sp in source_paths:
                for st in start_dates:
                    src_k = scope_key(
                        sp,
                        QueryScope(
                            as_of=day_obj,
                            start_date=st,
                            reporting_currency="USD",
                            ledger=Ledger.PERSON,
                            person_ids=pids,
                        ),
                    )
                    key_map[(d, src_k)] = target_keys

    t0 = time.time()
    print("🚀 开始同步 scope_key 至当前工作区 (money3 + start_date=2025-01-01/None)...")

    # 优先读取已经存在于 P3 的点位，使用 INSERT OR IGNORE 保证不覆盖 P3 最新重算结果
    cur.execute("SELECT scope_key, day, stamp, session, payload_json FROM historical_pnl_points")
    pts = cur.fetchall()
    to_insert_pts = []
    for k, d, stamp, sess, p_json in pts:
        target_ks = key_map.get((d, k))
        if target_ks:
            for tk in target_ks:
                if tk != k:
                    to_insert_pts.append((tk, d, stamp, sess, p_json))

    print(f"  准备补齐 {len(to_insert_pts)} 个高频点位映射...")
    cur.executemany(
        "INSERT OR IGNORE INTO historical_pnl_points (scope_key, day, stamp, session, payload_json) VALUES (?, ?, ?, ?, ?)",
        to_insert_pts,
    )

    # 2. versions
    cur.execute("SELECT scope_key, day, source_version FROM historical_pnl_versions")
    vers = cur.fetchall()
    to_insert_vers = []
    for k, d, ver in vers:
        target_ks = key_map.get((d, k))
        if target_ks:
            for tk in target_ks:
                to_insert_vers.append((tk, d, current_ver or ver))
    cur.executemany(
        "INSERT OR REPLACE INTO historical_pnl_versions (scope_key, day, source_version) VALUES (?, ?, ?)",
        to_insert_vers,
    )

    # 3. warnings
    cur.execute("SELECT scope_key, day, warning FROM historical_pnl_warnings")
    warns = cur.fetchall()
    to_insert_warns = []
    for k, d, w in warns:
        target_ks = key_map.get((d, k))
        if target_ks:
            for tk in target_ks:
                if tk != k:
                    to_insert_warns.append((tk, d, w))
    cur.executemany(
        "INSERT OR IGNORE INTO historical_pnl_warnings (scope_key, day, warning) VALUES (?, ?, ?)",
        to_insert_warns,
    )

    con.commit()
    con.close()
    print(f"🎉 同步完成！耗时: {time.time() - t0:.2f}s")


if __name__ == "__main__":
    sync_keys()
