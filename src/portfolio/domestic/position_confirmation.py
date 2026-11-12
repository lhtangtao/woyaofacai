"""Local user confirmations, separate from reconstructed fund positions."""
from datetime import datetime
from zoneinfo import ZoneInfo

from .storage import CALC_FILE, backup, connect, operation

TABLE = "calc_fund_position_confirmations"
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    account_id TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    confirmed_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('持有中','已清仓')),
    latest_transaction_id TEXT,
    PRIMARY KEY(account_id,fund_code,confirmed_date)
)
"""


def read_confirmations(calc, cutoff):
    if not calc or not calc.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone():
        return {}
    return {(row["account_id"], row["fund_code"]): row for row in calc.execute(
        f"SELECT * FROM {TABLE} WHERE confirmed_date<=? ORDER BY confirmed_date", (cutoff,)
    )}


def confirm_current_positions(root):
    """Record the user's confirmation of the last known holdings and remaining historical funds."""
    from .holdings_query import holdings

    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    with operation(root) as root:
        rows = holdings(root=root, valuation_date=day)["rows"]
        if not rows or not any(row["last_confirmed_status"] == "持有中" for row in rows):
            raise ValueError("找不到可作为基准的已确认持仓，请先核对国内基金计算库")
        if any(row["coverage_through"] is None for row in rows):
            raise ValueError("部分账户缺少计算或对账覆盖信息，请先核对计算库")
        entries = [
            (row["account_id"], row["fund_code"], day,
             "持有中" if row["last_confirmed_status"] == "持有中" else "已清仓",
             row["latest_transaction_id"])
            for row in rows
        ]
        backup(root, names=(CALC_FILE,))
        with connect(root / CALC_FILE) as calc:
            calc.execute("BEGIN IMMEDIATE")
            calc.execute(SCHEMA)
            calc.executemany(f"INSERT OR REPLACE INTO {TABLE} VALUES (?,?,?,?,?)", entries)
    held = sum(entry[3] == "持有中" for entry in entries)
    return {"confirmed_date": day, "held": held, "closed": len(entries) - held}
