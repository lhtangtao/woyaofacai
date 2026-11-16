"""国内资产行情与净值自动增量同步服务（启动时与手动刷新时后台静默运行）。"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from portfolio.paths import DOMESTIC_ASSETS_DIR

from .nav_recent import check as check_recent_fund_nav
from .storage import RAW_FILE
from .ths_nav_sync import sync_ths_nav_daily

logger = logging.getLogger(__name__)
_SYNC_LOCK = threading.Lock()


def _nav_snapshot(db_path: Path) -> tuple[int, str]:
    if not db_path.is_file():
        return 0, ""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(nav_date), '') FROM raw_fund_nav_daily"
            ).fetchone()
            return int(row[0] or 0), str(row[1] or "")
        finally:
            conn.close()
    except Exception:
        return 0, ""


def sync_domestic_market_data(*, root=DOMESTIC_ASSETS_DIR) -> dict[str, Any]:
    """增量同步在持同花顺场内收盘价与国内基金最新净值，返回同步结果摘要。"""
    root_path = Path(root)
    db_path = root_path / RAW_FILE
    if not db_path.is_file():
        return {"updated": False, "inserted": 0, "latest_nav_date": "", "ths_inserted": 0}

    if not _SYNC_LOCK.acquire(blocking=False):
        return {"updated": False, "inserted": 0, "latest_nav_date": "", "ths_inserted": 0, "skipped": True}

    try:
        before_cnt, before_max = _nav_snapshot(db_path)
        ths_inserted = 0
        try:
            ths_res = sync_ths_nav_daily(
                root=root_path,
                create_backup=False,
                open_only=True,
                recent_days=14,
            )
            ths_inserted = int(ths_res.get("inserted") or 0)
        except Exception as exc:
            logger.warning("同花顺在持收盘价自动同步失败: %s", exc)

        try:
            check_recent_fund_nav(root_path, include_today=True, verbose=False)
        except Exception as exc:
            logger.warning("国内基金最近净值自动检查失败: %s", exc)

        after_cnt, after_max = _nav_snapshot(db_path)
        diff = max(0, after_cnt - before_cnt)
        updated = (after_cnt != before_cnt) or (after_max != before_max) or (ths_inserted > 0)
        return {
            "updated": updated,
            "inserted": diff,
            "latest_nav_date": after_max,
            "ths_inserted": ths_inserted,
        }
    finally:
        _SYNC_LOCK.release()
