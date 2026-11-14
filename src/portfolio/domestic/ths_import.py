"""同花顺（THS）国内股票/ETF 交易记录幂等导入工具。"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import uuid

from portfolio.paths import DOMESTIC_ASSETS_DIR

from .storage import RAW_FILE, backup, connect
from .ths_analytics import THS_ACCOUNT, THS_PLATFORM, THS_SOURCE

PARSER_VERSION = "ths-manual-1.0"


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _order_id(row):
    if row.get("order_id"):
        return str(row["order_id"])
    return f"THS-{row['fund_code']}-{row['transaction_time']}-{row['transaction_type_raw']}"


def import_ths_trades(records, *, root=DOMESTIC_ASSETS_DIR, source_filename="tonghuashun_manual_import.json",
                      create_backup=True):
    """将同花顺股票/ETF 交易记录幂等导入 domestic_assets_raw.db。"""
    if not records:
        return {"inserted": 0, "duplicates": 0, "batch_id": None}
    if create_backup and (root / RAW_FILE).exists():
        backup(root)
    now = _utc_now()
    payload_bytes = json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
    file_sha256 = hashlib.sha256(payload_bytes).hexdigest()

    with connect(root / RAW_FILE) as raw:
        raw.execute(
            """INSERT OR IGNORE INTO raw_asset_accounts
               VALUES (?, '同花顺股票账户', ?, '股票/ETF', 'CNY', ?)""",
            (THS_ACCOUNT, THS_PLATFORM, now),
        )
        batch_id = str(uuid.uuid4())
        raw.execute(
            """INSERT INTO raw_import_batches
               (id, source_type, source_filename, file_sha256, parser_version, total_pages,
                parsed_rows, error_rows, duplicate_rows, inserted_rows, status, created_at)
               VALUES (?, 'transaction', ?, ?, ?, 1, ?, 0, 0, 0, '处理中', ?)""",
            (batch_id, source_filename, file_sha256, PARSER_VERSION, len(records), now),
        )
        dates = [str(r.get("confirmation_date") or str(r["transaction_time"])[:10]).strip() for r in records]
        raw.execute(
            """INSERT INTO raw_fund_source_files
               (id, source_platform, account_id, period_start, period_end, statement_year, actual_start, actual_end)
               VALUES (?, ?, ?, ?, ?, NULL, ?, ?)""",
            (batch_id, THS_SOURCE, THS_ACCOUNT, min(dates), max(dates), min(dates), max(dates)),
        )

        inserted = 0
        duplicates = 0
        seen_funds = {}
        for idx, row in enumerate(records, start=1):
            code = str(row["fund_code"]).strip()
            name = str(row["fund_name"]).strip()
            ftype = str(row.get("fund_type") or "股票/ETF").strip()
            seen_funds[code] = (name, ftype)
            oid = _order_id(row)
            exists = raw.execute(
                """SELECT 1 FROM raw_fund_transactions
                   WHERE account_id=? AND source_platform=? AND order_id=?""",
                (THS_ACCOUNT, THS_SOURCE, oid),
            ).fetchone()
            if exists:
                duplicates += 1
                continue

            tx_time = str(row["transaction_time"]).strip()
            conf_date = str(row.get("confirmation_date") or tx_time[:10]).strip()
            shares = Decimal(str(row["confirmed_shares"]))
            amount = Decimal(str(row["confirmed_amount"]))
            fee = Decimal(str(row.get("fee", "0.00")))
            price = Decimal(str(row["confirmed_nav"])) if row.get("confirmed_nav") is not None else None
            raw.execute(
                """INSERT INTO raw_fund_transactions (
                       account_id, source_platform, source_file_id, import_batch_id,
                       source_page, source_record_no, source_section, order_id,
                       transaction_time, transaction_type_raw, fund_code, fund_name, fund_type,
                       portfolio_fund_name, application_amount, application_shares,
                       confirmed_amount, confirmed_shares, fee, confirmation_date,
                       confirmed_nav, confirmation_status, linked_bank_card, raw_data,
                       parse_status, parse_error
                   ) VALUES (?, ?, ?, ?, 1, ?, '同花顺手工导入', ?, ?, ?, ?, ?, ?, NULL,
                             ?, ?, ?, ?, ?, ?, ?, '成功', NULL, ?, '正常', NULL)""",
                (
                    THS_ACCOUNT,
                    THS_SOURCE,
                    batch_id,
                    batch_id,
                    idx,
                    oid,
                    tx_time,
                    str(row["transaction_type_raw"]).strip(),
                    code,
                    name,
                    ftype,
                    str(amount),
                    str(abs(shares)),
                    str(amount),
                    str(shares),
                    str(fee),
                    conf_date,
                    str(price) if price is not None else None,
                    json.dumps(row, ensure_ascii=False),
                ),
            )
            inserted += 1

        for code, (name, ftype) in seen_funds.items():
            payload = json.dumps({"fund_code": code, "fund_name": name, "fund_type": ftype}, ensure_ascii=False)
            raw.execute(
                """INSERT INTO raw_fund_info
                   (fund_code, fund_name, fund_type, currency, source, fetched_at, raw_payload)
                   VALUES (?, ?, ?, 'CNY', '同花顺手工导入', ?, ?)
                   ON CONFLICT(fund_code) DO UPDATE SET
                     fund_name=excluded.fund_name,
                     fund_type=excluded.fund_type""",
                (code, name, ftype, now, payload),
            )

        raw.execute(
            """UPDATE raw_import_batches
               SET duplicate_rows=?, inserted_rows=?, status='完成'
               WHERE id=?""",
            (duplicates, inserted, batch_id),
        )
        raw.commit()
    return {"inserted": inserted, "duplicates": duplicates, "batch_id": batch_id}


INITIAL_513300_RECORDS = [
    {
        "fund_code": "513300",
        "fund_name": "纳斯达克ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2025-02-10 13:40:42",
        "confirmation_date": "2025-02-10",
        "transaction_type_raw": "建仓",
        "confirmed_nav": "2.033",
        "confirmed_shares": "5700",
        "confirmed_amount": "11588.10",
        "fee": "5.00",
    },
    {
        "fund_code": "513300",
        "fund_name": "纳斯达克ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2025-12-25 10:36:52",
        "confirmation_date": "2025-12-25",
        "transaction_type_raw": "买入",
        "confirmed_nav": "2.355",
        "confirmed_shares": "4300",
        "confirmed_amount": "10126.50",
        "fee": "5.00",
    },
    {
        "fund_code": "513300",
        "fund_name": "纳斯达克ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2026-05-20 16:00:00",
        "confirmation_date": "2026-05-20",
        "transaction_type_raw": "余额入账（清仓）",
        "confirmed_nav": "2.516",
        "confirmed_shares": "-10000",
        "confirmed_amount": "25160.00",
        "fee": "0.00",
    },
]

INITIAL_513520_RECORDS = [
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-10-18 13:16:44",
        "confirmation_date": "2024-10-18",
        "transaction_type_raw": "建仓",
        "confirmed_nav": "1.430",
        "confirmed_shares": "14400",
        "confirmed_amount": "20592.00",
        "fee": "5.15",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-12-03 14:35:53",
        "confirmation_date": "2024-12-03",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.466",
        "confirmed_shares": "-7200",
        "confirmed_amount": "10555.20",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-12-04 09:49:55",
        "confirmation_date": "2024-12-04",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.465",
        "confirmed_shares": "-4800",
        "confirmed_amount": "7032.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2025-01-15 10:58:31",
        "confirmation_date": "2025-01-15",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.451",
        "confirmed_shares": "7000",
        "confirmed_amount": "10157.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2025-01-17 09:58:21",
        "confirmation_date": "2025-01-17",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.510",
        "confirmed_shares": "-7400",
        "confirmed_amount": "11174.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2025-01-27 14:58:11",
        "confirmation_date": "2025-01-27",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.473",
        "confirmed_shares": "10000",
        "confirmed_amount": "14730.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2025-05-13 14:23:09",
        "confirmation_date": "2025-05-13",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.444",
        "confirmed_shares": "8000",
        "confirmed_amount": "11552.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2025-12-16 10:03:00",
        "confirmation_date": "2025-12-16",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.783",
        "confirmed_shares": "-15000",
        "confirmed_amount": "26745.00",
        "fee": "6.69",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2025-12-26 10:06:42",
        "confirmation_date": "2025-12-26",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.832",
        "confirmed_shares": "9100",
        "confirmed_amount": "16671.20",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2026-03-04 13:03:20",
        "confirmation_date": "2026-03-04",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.925",
        "confirmed_shares": "-7000",
        "confirmed_amount": "13475.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2026-03-20 13:16:35",
        "confirmation_date": "2026-03-20",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.885",
        "confirmed_shares": "7200",
        "confirmed_amount": "13572.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2026-05-20 16:00:00",
        "confirmation_date": "2026-05-20",
        "transaction_type_raw": "余额入账（清仓）",
        "confirmed_nav": "2.034",
        "confirmed_shares": "-14300",
        "confirmed_amount": "29086.20",
        "fee": "0.00",
    },
]

INITIAL_161725_RECORDS = [
    {
        "fund_code": "161725",
        "fund_name": "白酒基金LOF",
        "fund_type": "指数型-股票",
        "transaction_time": "2024-10-08 09:36:53",
        "confirmation_date": "2024-10-08",
        "transaction_type_raw": "建仓",
        "confirmed_nav": "1.050",
        "confirmed_shares": "30900",
        "confirmed_amount": "32445.00",
        "fee": "8.11",
    },
    {
        "fund_code": "161725",
        "fund_name": "白酒基金LOF",
        "fund_type": "指数型-股票",
        "transaction_time": "2024-10-08 13:51:54",
        "confirmation_date": "2024-10-08",
        "transaction_type_raw": "买入",
        "confirmed_nav": "0.945",
        "confirmed_shares": "20600",
        "confirmed_amount": "19467.01",
        "fee": "5.00",
    },
    {
        "fund_code": "161725",
        "fund_name": "白酒基金LOF",
        "fund_type": "指数型-股票",
        "transaction_time": "2024-10-18 09:43:07",
        "confirmation_date": "2024-10-18",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "0.800",
        "confirmed_shares": "-25700",
        "confirmed_amount": "20560.00",
        "fee": "5.14",
    },
    {
        "fund_code": "161725",
        "fund_name": "白酒基金LOF",
        "fund_type": "指数型-股票",
        "transaction_time": "2024-11-26 14:53:39",
        "confirmation_date": "2024-11-26",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "0.845",
        "confirmed_shares": "-18000",
        "confirmed_amount": "15210.00",
        "fee": "5.00",
    },
    {
        "fund_code": "161725",
        "fund_name": "白酒基金LOF",
        "fund_type": "指数型-股票",
        "transaction_time": "2024-11-27 10:59:42",
        "confirmation_date": "2024-11-27",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "0.857",
        "confirmed_shares": "-7700",
        "confirmed_amount": "6598.90",
        "fee": "5.00",
    },
    {
        "fund_code": "161725",
        "fund_name": "白酒基金LOF",
        "fund_type": "指数型-股票",
        "transaction_time": "2025-11-03 10:44:51",
        "confirmation_date": "2025-11-03",
        "transaction_type_raw": "清仓",
        "confirmed_nav": "0.777",
        "confirmed_shares": "-100",
        "confirmed_amount": "77.70",
        "fee": "5.00",
    },
]

INITIAL_513520_CYCLE1_RECORDS = [
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-01-22 11:04:43",
        "confirmation_date": "2024-01-22",
        "transaction_type_raw": "建仓",
        "confirmed_nav": "1.475",
        "confirmed_shares": "7000",
        "confirmed_amount": "10325.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-01-22 13:58:56",
        "confirmation_date": "2024-01-22",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.541",
        "confirmed_shares": "-6900",
        "confirmed_amount": "10632.90",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-01-23 10:32:02",
        "confirmation_date": "2024-01-23",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.597",
        "confirmed_shares": "8000",
        "confirmed_amount": "12776.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-01-24 10:32:36",
        "confirmation_date": "2024-01-24",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.683",
        "confirmed_shares": "-4000",
        "confirmed_amount": "6732.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-01-24 14:12:54",
        "confirmation_date": "2024-01-24",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.650",
        "confirmed_shares": "6000",
        "confirmed_amount": "9900.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-01-24 14:39:57",
        "confirmation_date": "2024-01-24",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.626",
        "confirmed_shares": "2700",
        "confirmed_amount": "4390.20",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-01-24 14:57:36",
        "confirmation_date": "2024-01-24",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.532",
        "confirmed_shares": "5000",
        "confirmed_amount": "7660.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-06-18 14:27:59",
        "confirmation_date": "2024-06-18",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.363",
        "confirmed_shares": "-8900",
        "confirmed_amount": "12130.70",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-06-26 11:19:13",
        "confirmation_date": "2024-06-26",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.450",
        "confirmed_shares": "-6000",
        "confirmed_amount": "8700.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-06-26 14:45:02",
        "confirmation_date": "2024-06-26",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.426",
        "confirmed_shares": "6000",
        "confirmed_amount": "8556.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-07-01 09:35:41",
        "confirmation_date": "2024-07-01",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.440",
        "confirmed_shares": "-8900",
        "confirmed_amount": "12816.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-07-01 14:55:35",
        "confirmation_date": "2024-07-01",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.430",
        "confirmed_shares": "9700",
        "confirmed_amount": "13871.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-07-03 09:59:22",
        "confirmation_date": "2024-07-03",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.467",
        "confirmed_shares": "-6500",
        "confirmed_amount": "9535.50",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-07-12 14:53:13",
        "confirmation_date": "2024-07-12",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.495",
        "confirmed_shares": "9100",
        "confirmed_amount": "13604.50",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-07-30 09:30:57",
        "confirmation_date": "2024-07-30",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.463",
        "confirmed_shares": "-6100",
        "confirmed_amount": "8924.30",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-08-02 09:38:11",
        "confirmation_date": "2024-08-02",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.432",
        "confirmed_shares": "6200",
        "confirmed_amount": "8878.40",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-08-05 13:41:51",
        "confirmation_date": "2024-08-05",
        "transaction_type_raw": "卖出",
        "confirmed_nav": "1.296",
        "confirmed_shares": "-6200",
        "confirmed_amount": "8035.20",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-09-12 13:50:31",
        "confirmation_date": "2024-09-12",
        "transaction_type_raw": "买入",
        "confirmed_nav": "1.432",
        "confirmed_shares": "7500",
        "confirmed_amount": "10740.00",
        "fee": "5.00",
    },
    {
        "fund_code": "513520",
        "fund_name": "日经ETF华夏",
        "fund_type": "指数型-海外股票",
        "transaction_time": "2024-10-08 13:45:48",
        "confirmation_date": "2024-10-08",
        "transaction_type_raw": "清仓",
        "confirmed_nav": "1.424",
        "confirmed_shares": "-13700",
        "confirmed_amount": "19508.80",
        "fee": "5.00",
    },
]
