"""Publish a position snapshot from an already-imported TianTian statement."""
from argparse import ArgumentParser
from collections import defaultdict
from datetime import date
from decimal import Decimal
from getpass import getpass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re

from portfolio.paths import DOMESTIC_ASSETS_DIR
from .storage import CALC_FILE, RAW_FILE, backup, connect, operation

SCHEMA = """
CREATE TABLE IF NOT EXISTS calc_tiantian_statement_positions (
 account_id TEXT NOT NULL, as_of TEXT NOT NULL, fund_code TEXT NOT NULL,
 fund_name TEXT NOT NULL, fund_type TEXT, holding_shares TEXT NOT NULL,
 unit_nav TEXT, nav_date TEXT, source_sha256 TEXT NOT NULL,
 PRIMARY KEY(account_id,as_of,fund_code)
);
"""


def read_snapshot(content, password):
    import pdfplumber

    holdings = defaultdict(list)
    totals = []
    with pdfplumber.open(BytesIO(content), password=password) as pdf:
        header = re.search(r"历史持仓明细\s*\(截止至(\d{4}-\d{2}-\d{2})\)", pdf.pages[0].extract_text())
        if not header:
            raise ValueError("找不到对账单持仓日期")
        as_of = date.fromisoformat(header[1]).isoformat()
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table or table[0][:7] != ["基金代码", "基金简称", "基金类型", "净值日期", "单位净值", "持有份额", "参考市值"]:
                    continue
                for code, name, kind, nav_day, nav, shares, value, *_ in table[1:]:
                    if not code and name and "合计" in name:
                        totals.append(Decimal(value.replace(",", "")))
                        continue
                    if not code or not re.fullmatch(r"\d{6}", code):
                        raise ValueError("对账单含无法识别的持仓行")
                    qty, price, stated = (Decimal(v.replace(",", "")) for v in (shares, nav, value))
                    nav_day = date.fromisoformat(nav_day).isoformat()
                    if qty < 0 or price <= 0 or nav_day > as_of:
                        raise ValueError("对账单份额、净值或净值日期无效")
                    holdings[code].append(("".join(name.splitlines()), kind, nav_day, price, qty, stated))
    if not holdings or not totals or sum(totals) != sum(r[5] for group in holdings.values() for r in group):
        raise ValueError("对账单持仓市值与合计不符")
    result = []
    for code, group in holdings.items():
        name, kind, _, _, _, _ = group[0]
        prices = {(r[2], r[3]) for r in group}
        nav_day, nav = next(iter(prices)) if len(prices) == 1 else (None, None)
        result.append((code, name, kind, str(sum(r[4] for r in group)), str(nav) if nav is not None else None, nav_day))
    return as_of, result


def publish_snapshot(root, content, password):
    digest = sha256(content).hexdigest()
    as_of, rows = read_snapshot(content, password)
    with operation(root):
        with connect(root / RAW_FILE, readonly=True) as raw:
            source = raw.execute("""SELECT f.account_id,f.period_end FROM raw_fund_source_files f
                JOIN raw_import_batches b ON b.id=f.id WHERE f.source_platform='TIANTIAN' AND b.file_sha256=?""",
                                 (digest,)).fetchone()
        if not source or source["period_end"] != as_of:
            raise ValueError("持仓快照与已入库对账单不匹配")
        account = source["account_id"]
        backup(root, names=(CALC_FILE,))
        with connect(root / CALC_FILE) as calc:
            calc.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            existing = calc.execute("""SELECT DISTINCT source_sha256 FROM calc_tiantian_statement_positions
                WHERE account_id=? AND as_of=?""", (account, as_of)).fetchall()
            if existing and any(row[0] != digest for row in existing):
                raise ValueError("同日已存在其他来源快照")
            calc.executemany("INSERT OR IGNORE INTO calc_tiantian_statement_positions VALUES (?,?,?,?,?,?,?,?,?)",
                             ((account, as_of, *row, digest) for row in rows))
    return as_of, len(rows)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("statement", type=Path)
    parser.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args()
    print(publish_snapshot(DOMESTIC_ASSETS_DIR, args.statement.read_bytes(), getpass("对账单 PDF 密码：")))
