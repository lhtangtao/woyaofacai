"""Sync all historical_pnl_points, versions, warnings from money_2 scope_keys to money3 scope_keys."""
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
P2 = "/Users/tangtao/money/money3/数据库/money.sqlite3"
STORE_PATH = "/Users/tangtao/Library/Application Support/MoneyPortfolio/Money Portfolio/live_quotes.sqlite3"


def sync_keys():
    con = sqlite3.connect(STORE_PATH)
    cur = con.cursor()

    cur.execute("SELECT DISTINCT day FROM historical_pnl_points WHERE day LIKE '2026-%'")
    days = [r[0] for r in cur.fetchall()]

    persons = [
        None,  # 全部人员
        "OTHERS",  # 他人
        1, 2, 3, 4, 5, 6, 7, 8, 9,
    ]

    key_map = {}
    for d in days:
        for target in persons:
            if target == "OTHERS":
                pids = (2, 3, 4, 5, 6, 7, 8, 9)
            elif target is not None:
                pids = (target,)
            else:
                pids = None
            s = QueryScope(as_of=date.fromisoformat(d), reporting_currency="USD", ledger=Ledger.PERSON, person_ids=pids)
            k1 = scope_key(P1, s)
            k2 = scope_key(P2, s)
            key_map[(d, k1)] = k2

    t0 = time.time()
    print("🚀 开始双向同步 scope_key (money_2 <-> money3)...")

    # 1. points
    cur.execute("SELECT scope_key, day, stamp, session, payload_json FROM historical_pnl_points")
    pts = cur.fetchall()
    to_insert_pts = []
    for k, d, stamp, sess, p_json in pts:
        target_k = key_map.get((d, k))
        if target_k:
            to_insert_pts.append((target_k, d, stamp, sess, p_json))

    print(f"  准备同步 {len(to_insert_pts)} 个高频点位...")
    cur.executemany(
        "INSERT OR REPLACE INTO historical_pnl_points (scope_key, day, stamp, session, payload_json) VALUES (?, ?, ?, ?, ?)",
        to_insert_pts,
    )

    # 2. versions
    cur.execute("SELECT scope_key, day, source_version FROM historical_pnl_versions")
    vers = cur.fetchall()
    to_insert_vers = []
    for k, d, ver in vers:
        target_k = key_map.get((d, k))
        if target_k:
            to_insert_vers.append((target_k, d, ver))
    cur.executemany(
        "INSERT OR REPLACE INTO historical_pnl_versions (scope_key, day, source_version) VALUES (?, ?, ?)",
        to_insert_vers,
    )

    # 3. warnings
    cur.execute("SELECT scope_key, day, warning FROM historical_pnl_warnings")
    warns = cur.fetchall()
    to_insert_warns = []
    for k, d, w in warns:
        target_k = key_map.get((d, k))
        if target_k:
            to_insert_warns.append((target_k, d, w))
    cur.executemany(
        "INSERT OR REPLACE INTO historical_pnl_warnings (scope_key, day, warning) VALUES (?, ?, ?)",
        to_insert_warns,
    )

    con.commit()
    con.close()
    print(f"🎉 同步完成！耗时: {time.time() - t0:.2f}s")


if __name__ == "__main__":
    sync_keys()
