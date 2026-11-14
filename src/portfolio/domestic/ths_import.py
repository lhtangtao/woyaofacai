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

INITIAL_600900_RECORDS = [
    {
        "fund_code": "600900",
        "fund_name": "长江电力",
        "fund_type": "股票-主板",
        "transaction_time": "2024-06-18 14:28:19",
        "confirmation_date": "2024-06-18",
        "transaction_type_raw": "建仓",
        "confirmed_nav": "27.780",
        "confirmed_shares": "400",
        "confirmed_amount": "11112.00",
        "fee": "5.12",
    },
    {
        "fund_code": "600900",
        "fund_name": "长江电力",
        "fund_type": "股票-主板",
        "transaction_time": "2024-07-12 14:51:59",
        "confirmation_date": "2024-07-12",
        "transaction_type_raw": "清仓",
        "confirmed_nav": "29.910",
        "confirmed_shares": "-400",
        "confirmed_amount": "11964.00",
        "fee": "11.10",
    },
]

INITIAL_300014_RECORDS = [
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2021-12-10 10:57:37", "confirmation_date": "2021-12-10", "transaction_type_raw": "建仓", "confirmed_nav": "135.000", "confirmed_shares": "100", "confirmed_amount": "13500.00", "fee": "5.00"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2022-01-28 14:40:13", "confirmation_date": "2022-01-28", "transaction_type_raw": "买入", "confirmed_nav": "93.800", "confirmed_shares": "100", "confirmed_amount": "9380.00", "fee": "5.00"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2022-02-11 09:30:50", "confirmation_date": "2022-02-11", "transaction_type_raw": "卖出", "confirmed_nav": "83.860", "confirmed_shares": "-200", "confirmed_amount": "16772.00", "fee": "21.77"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2022-02-11 14:51:31", "confirmation_date": "2022-02-11", "transaction_type_raw": "买入", "confirmed_nav": "84.600", "confirmed_shares": "100", "confirmed_amount": "8460.00", "fee": "5.00"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2022-03-04 10:52:00", "confirmation_date": "2022-03-04", "transaction_type_raw": "买入", "confirmed_nav": "78.000", "confirmed_shares": "100", "confirmed_amount": "7800.00", "fee": "5.00"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2022-03-15 14:55:04", "confirmation_date": "2022-03-15", "transaction_type_raw": "卖出", "confirmed_nav": "70.900", "confirmed_shares": "-100", "confirmed_amount": "7090.00", "fee": "12.09"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2022-07-07 17:00:01", "confirmation_date": "2022-07-07", "transaction_type_raw": "分红", "confirmed_nav": "104.000", "confirmed_shares": "0", "confirmed_amount": "16.00", "fee": "0.00"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2023-02-01 10:05:58", "confirmation_date": "2023-02-01", "transaction_type_raw": "卖出", "confirmed_nav": "91.190", "confirmed_shares": "-100", "confirmed_amount": "9119.00", "fee": "14.12"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2023-02-01 14:50:44", "confirmation_date": "2023-02-01", "transaction_type_raw": "买入", "confirmed_nav": "90.000", "confirmed_shares": "100", "confirmed_amount": "9000.00", "fee": "5.00"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2023-02-20 10:31:02", "confirmation_date": "2023-02-20", "transaction_type_raw": "买入", "confirmed_nav": "73.800", "confirmed_shares": "100", "confirmed_amount": "7380.00", "fee": "5.00"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2023-06-16 17:00:01", "confirmation_date": "2023-06-16", "transaction_type_raw": "分红", "confirmed_nav": "60.990", "confirmed_shares": "0", "confirmed_amount": "32.00", "fee": "0.00"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2024-01-17 10:42:51", "confirmation_date": "2024-01-17", "transaction_type_raw": "买入", "confirmed_nav": "39.250", "confirmed_shares": "100", "confirmed_amount": "3925.00", "fee": "5.00"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2024-01-17 11:02:50", "confirmation_date": "2024-01-17", "transaction_type_raw": "买入", "confirmed_nav": "39.000", "confirmed_shares": "100", "confirmed_amount": "3900.00", "fee": "5.00"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2024-01-24 10:34:37", "confirmation_date": "2024-01-24", "transaction_type_raw": "卖出", "confirmed_nav": "37.740", "confirmed_shares": "-200", "confirmed_amount": "7548.00", "fee": "8.78"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2024-01-24 14:39:11", "confirmation_date": "2024-01-24", "transaction_type_raw": "清仓", "confirmed_nav": "38.000", "confirmed_shares": "-200", "confirmed_amount": "7600.00", "fee": "8.80"},
    {"fund_code": "300014", "fund_name": "亿纬锂能", "fund_type": "股票-创业板", "transaction_time": "2024-01-25 09:00:00", "confirmation_date": "2024-01-25", "transaction_type_raw": "股息红利税补缴", "confirmed_nav": "0.000", "confirmed_shares": "0", "confirmed_amount": "1.60", "fee": "0.00"},
]

INITIAL_161725_CYCLE1_RECORDS = [
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2021-07-05 13:00:00", "confirmation_date": "2021-07-05", "transaction_type_raw": "建仓", "confirmed_nav": "1.463", "confirmed_shares": "20000", "confirmed_amount": "29260.00", "fee": "7.32"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2021-07-09 11:20:31", "confirmation_date": "2021-07-09", "transaction_type_raw": "买入", "confirmed_nav": "1.419", "confirmed_shares": "8000", "confirmed_amount": "11352.00", "fee": "5.00"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2021-09-09 17:00:01", "confirmation_date": "2021-09-09", "transaction_type_raw": "分红", "confirmed_nav": "1.216", "confirmed_shares": "0", "confirmed_amount": "336.00", "fee": "0.00"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2021-11-04 11:19:26", "confirmation_date": "2021-11-04", "transaction_type_raw": "卖出", "confirmed_nav": "1.300", "confirmed_shares": "-7000", "confirmed_amount": "9100.00", "fee": "5.00"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2021-11-24 10:42:37", "confirmation_date": "2021-11-24", "transaction_type_raw": "卖出", "confirmed_nav": "1.363", "confirmed_shares": "-7000", "confirmed_amount": "9541.00", "fee": "5.00"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2021-11-29 10:32:02", "confirmation_date": "2021-11-29", "transaction_type_raw": "买入", "confirmed_nav": "1.350", "confirmed_shares": "7000", "confirmed_amount": "9450.00", "fee": "5.00"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2021-12-08 14:28:08", "confirmation_date": "2021-12-08", "transaction_type_raw": "卖出", "confirmed_nav": "1.440", "confirmed_shares": "-5000", "confirmed_amount": "7200.00", "fee": "5.00"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2021-12-13 17:00:01", "confirmation_date": "2021-12-13", "transaction_type_raw": "分红", "confirmed_nav": "1.449", "confirmed_shares": "0", "confirmed_amount": "448.00", "fee": "0.00"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2022-01-05 17:00:01", "confirmation_date": "2022-01-05", "transaction_type_raw": "分红", "confirmed_nav": "1.309", "confirmed_shares": "0", "confirmed_amount": "720.00", "fee": "0.00"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2022-01-06 14:54:04", "confirmation_date": "2022-01-06", "transaction_type_raw": "买入", "confirmed_nav": "1.280", "confirmed_shares": "2700", "confirmed_amount": "3456.00", "fee": "5.00"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2022-01-10 09:34:54", "confirmation_date": "2022-01-10", "transaction_type_raw": "买入", "confirmed_nav": "1.230", "confirmed_shares": "2800", "confirmed_amount": "3444.00", "fee": "5.00"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2022-03-07 09:44:27", "confirmation_date": "2022-03-07", "transaction_type_raw": "买入", "confirmed_nav": "1.120", "confirmed_shares": "700", "confirmed_amount": "784.00", "fee": "5.00"},
    {"fund_code": "161725", "fund_name": "白酒基金LOF", "fund_type": "指数型-股票", "transaction_time": "2024-01-22 11:04:17", "confirmation_date": "2024-01-22", "transaction_type_raw": "清仓", "confirmed_nav": "0.853", "confirmed_shares": "-22200", "confirmed_amount": "18936.60", "fee": "5.00"},
]

INITIAL_515790_RECORDS = [
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2021-11-16 14:02:47", "confirmation_date": "2021-11-16", "transaction_type_raw": "建仓", "confirmed_nav": "1.738", "confirmed_shares": "6500", "confirmed_amount": "11297.00", "fee": "5.00"},
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2021-11-22 14:16:06", "confirmation_date": "2021-11-22", "transaction_type_raw": "卖出", "confirmed_nav": "1.828", "confirmed_shares": "-3200", "confirmed_amount": "5849.60", "fee": "5.00"},
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2021-11-24 10:05:59", "confirmation_date": "2021-11-24", "transaction_type_raw": "买入", "confirmed_nav": "1.798", "confirmed_shares": "6400", "confirmed_amount": "11507.20", "fee": "5.00"},
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2021-11-24 10:05:59", "confirmation_date": "2021-11-24", "transaction_type_raw": "买入", "confirmed_nav": "1.798", "confirmed_shares": "6400", "confirmed_amount": "11507.20", "fee": "5.00", "order_id": "THS-515790-2021-11-24 10:05:59-买入-2"},
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2023-03-24 09:30:25", "confirmation_date": "2023-03-24", "transaction_type_raw": "买入", "confirmed_nav": "1.300", "confirmed_shares": "3900", "confirmed_amount": "5070.00", "fee": "5.00"},
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2024-01-16 09:51:13", "confirmation_date": "2024-01-16", "transaction_type_raw": "卖出", "confirmed_nav": "0.888", "confirmed_shares": "-10000", "confirmed_amount": "8880.00", "fee": "5.00"},
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2024-01-17 10:11:33", "confirmation_date": "2024-01-17", "transaction_type_raw": "清仓", "confirmed_nav": "0.870", "confirmed_shares": "-10000", "confirmed_amount": "8700.00", "fee": "5.00"},
]

INITIAL_159995_RECORDS = [
    {"fund_code": "159995", "fund_name": "芯片ETF", "fund_type": "指数型-股票", "transaction_time": "2021-07-09 14:15:45", "confirmation_date": "2021-07-09", "transaction_type_raw": "建仓", "confirmed_nav": "1.618", "confirmed_shares": "13200", "confirmed_amount": "21357.60", "fee": "5.34"},
    {"fund_code": "159995", "fund_name": "芯片ETF", "fund_type": "指数型-股票", "transaction_time": "2022-05-10 14:25:31", "confirmation_date": "2022-05-10", "transaction_type_raw": "卖出", "confirmed_nav": "1.083", "confirmed_shares": "-6600", "confirmed_amount": "7147.80", "fee": "5.00"},
    {"fund_code": "159995", "fund_name": "芯片ETF", "fund_type": "指数型-股票", "transaction_time": "2022-09-05 09:33:36", "confirmation_date": "2022-09-05", "transaction_type_raw": "买入", "confirmed_nav": "1.134", "confirmed_shares": "800", "confirmed_amount": "907.20", "fee": "5.00"},
    {"fund_code": "159995", "fund_name": "芯片ETF", "fund_type": "指数型-股票", "transaction_time": "2023-04-14 14:10:57", "confirmation_date": "2023-04-14", "transaction_type_raw": "卖出", "confirmed_nav": "1.270", "confirmed_shares": "-800", "confirmed_amount": "1016.00", "fee": "5.00"},
    {"fund_code": "159995", "fund_name": "芯片ETF", "fund_type": "指数型-股票", "transaction_time": "2023-05-22 13:36:45", "confirmation_date": "2023-05-22", "transaction_type_raw": "买入", "confirmed_nav": "1.080", "confirmed_shares": "3200", "confirmed_amount": "3456.00", "fee": "5.00"},
    {"fund_code": "159995", "fund_name": "芯片ETF", "fund_type": "指数型-股票", "transaction_time": "2024-01-12 10:02:27", "confirmation_date": "2024-01-12", "transaction_type_raw": "清仓", "confirmed_nav": "0.839", "confirmed_shares": "-9800", "confirmed_amount": "8222.20", "fee": "5.00"},
]

INITIAL_600036_RECORDS = [
    {"fund_code": "600036", "fund_name": "招商银行", "fund_type": "股票-主板", "transaction_time": "2022-08-22 14:04:15", "confirmation_date": "2022-08-22", "transaction_type_raw": "建仓", "confirmed_nav": "33.500", "confirmed_shares": "200", "confirmed_amount": "6700.00", "fee": "5.07"},
    {"fund_code": "600036", "fund_name": "招商银行", "fund_type": "股票-主板", "transaction_time": "2022-08-23 09:40:48", "confirmation_date": "2022-08-23", "transaction_type_raw": "买入", "confirmed_nav": "33.330", "confirmed_shares": "200", "confirmed_amount": "6666.00", "fee": "5.07"},
    {"fund_code": "600036", "fund_name": "招商银行", "fund_type": "股票-主板", "transaction_time": "2022-11-29 15:00:00", "confirmation_date": "2022-11-29", "transaction_type_raw": "卖出", "confirmed_nav": "34.860", "confirmed_shares": "-200", "confirmed_amount": "6972.00", "fee": "12.04"},
    {"fund_code": "600036", "fund_name": "招商银行", "fund_type": "股票-主板", "transaction_time": "2023-01-11 10:28:27", "confirmation_date": "2023-01-11", "transaction_type_raw": "清仓", "confirmed_nav": "39.000", "confirmed_shares": "-200", "confirmed_amount": "7800.00", "fee": "12.88"},
]

INITIAL_BATCH4_RECORDS = [
    # 漱玉平民 301017
    {"fund_code": "301017", "fund_name": "漱玉平民", "fund_type": "股票-创业板", "transaction_time": "2021-10-12 09:57:07", "confirmation_date": "2021-10-12", "transaction_type_raw": "建仓", "confirmed_nav": "23.200", "confirmed_shares": "200", "confirmed_amount": "4640.00", "fee": "5.00"},
    {"fund_code": "301017", "fund_name": "漱玉平民", "fund_type": "股票-创业板", "transaction_time": "2021-10-14 09:31:49", "confirmation_date": "2021-10-14", "transaction_type_raw": "卖出", "confirmed_nav": "28.030", "confirmed_shares": "-100", "confirmed_amount": "2803.00", "fee": "7.80"},
    {"fund_code": "301017", "fund_name": "漱玉平民", "fund_type": "股票-创业板", "transaction_time": "2021-10-14 09:36:39", "confirmation_date": "2021-10-14", "transaction_type_raw": "清仓", "confirmed_nav": "28.000", "confirmed_shares": "-100", "confirmed_amount": "2800.00", "fee": "7.80"},
    # 赛力斯 601127
    {"fund_code": "601127", "fund_name": "赛力斯", "fund_type": "股票-主板", "transaction_time": "2021-10-19 09:44:14", "confirmation_date": "2021-10-19", "transaction_type_raw": "建仓", "confirmed_nav": "70.000", "confirmed_shares": "100", "confirmed_amount": "7000.00", "fee": "5.14"},
    {"fund_code": "601127", "fund_name": "赛力斯", "fund_type": "股票-主板", "transaction_time": "2021-10-25 14:50:37", "confirmation_date": "2021-10-25", "transaction_type_raw": "清仓", "confirmed_nav": "71.250", "confirmed_shares": "-100", "confirmed_amount": "7125.00", "fee": "12.27"},
    # 光伏ETF 515790 (首轮 2021-09-08 ~ 2021-11-12)
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2021-09-08 14:24:28", "confirmation_date": "2021-09-08", "transaction_type_raw": "建仓", "confirmed_nav": "1.750", "confirmed_shares": "2500", "confirmed_amount": "4375.00", "fee": "5.00"},
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2021-09-17 11:26:19", "confirmation_date": "2021-09-17", "transaction_type_raw": "买入", "confirmed_nav": "1.620", "confirmed_shares": "3000", "confirmed_amount": "4860.00", "fee": "5.00"},
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2021-10-25 10:20:05", "confirmation_date": "2021-10-25", "transaction_type_raw": "卖出", "confirmed_nav": "1.816", "confirmed_shares": "-5500", "confirmed_amount": "9988.00", "fee": "5.00"},
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2021-10-25 14:39:23", "confirmation_date": "2021-10-25", "transaction_type_raw": "买入", "confirmed_nav": "1.819", "confirmed_shares": "200", "confirmed_amount": "363.80", "fee": "5.00"},
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2021-10-26 14:41:27", "confirmation_date": "2021-10-26", "transaction_type_raw": "买入", "confirmed_nav": "1.805", "confirmed_shares": "300", "confirmed_amount": "541.50", "fee": "5.00"},
    {"fund_code": "515790", "fund_name": "光伏ETF", "fund_type": "指数型-股票", "transaction_time": "2021-11-12 10:31:55", "confirmation_date": "2021-11-12", "transaction_type_raw": "清仓", "confirmed_nav": "1.835", "confirmed_shares": "-500", "confirmed_amount": "917.50", "fee": "5.00"},
    # 中航西飞 000768
    {"fund_code": "000768", "fund_name": "中航西飞", "fund_type": "股票-主板", "transaction_time": "2021-08-06 09:55:39", "confirmation_date": "2021-08-06", "transaction_type_raw": "建仓", "confirmed_nav": "33.770", "confirmed_shares": "200", "confirmed_amount": "6754.00", "fee": "5.00"},
    {"fund_code": "000768", "fund_name": "中航西飞", "fund_type": "股票-主板", "transaction_time": "2021-08-12 13:55:50", "confirmation_date": "2021-08-12", "transaction_type_raw": "买入", "confirmed_nav": "32.200", "confirmed_shares": "100", "confirmed_amount": "3220.00", "fee": "5.00"},
    {"fund_code": "000768", "fund_name": "中航西飞", "fund_type": "股票-主板", "transaction_time": "2021-09-17 11:16:44", "confirmation_date": "2021-09-17", "transaction_type_raw": "买入", "confirmed_nav": "31.330", "confirmed_shares": "200", "confirmed_amount": "6266.00", "fee": "5.00"},
    {"fund_code": "000768", "fund_name": "中航西飞", "fund_type": "股票-主板", "transaction_time": "2021-10-29 10:15:07", "confirmation_date": "2021-10-29", "transaction_type_raw": "卖出", "confirmed_nav": "30.200", "confirmed_shares": "-200", "confirmed_amount": "6040.00", "fee": "11.04"},
    {"fund_code": "000768", "fund_name": "中航西飞", "fund_type": "股票-主板", "transaction_time": "2021-11-11 14:48:47", "confirmation_date": "2021-11-11", "transaction_type_raw": "卖出", "confirmed_nav": "33.890", "confirmed_shares": "-100", "confirmed_amount": "3389.00", "fee": "8.39"},
    {"fund_code": "000768", "fund_name": "中航西飞", "fund_type": "股票-主板", "transaction_time": "2021-11-12 10:32:22", "confirmation_date": "2021-11-12", "transaction_type_raw": "清仓", "confirmed_nav": "34.500", "confirmed_shares": "-200", "confirmed_amount": "6900.00", "fee": "11.90"},
    # 川能动力 000155
    {"fund_code": "000155", "fund_name": "川能动力", "fund_type": "股票-主板", "transaction_time": "2021-10-27 14:52:18", "confirmation_date": "2021-10-27", "transaction_type_raw": "建仓", "confirmed_nav": "30.240", "confirmed_shares": "100", "confirmed_amount": "3024.00", "fee": "5.00"},
    {"fund_code": "000155", "fund_name": "川能动力", "fund_type": "股票-主板", "transaction_time": "2021-11-12 10:12:40", "confirmation_date": "2021-11-12", "transaction_type_raw": "清仓", "confirmed_nav": "26.000", "confirmed_shares": "-100", "confirmed_amount": "2600.00", "fee": "7.60"},
    # 永福股份 300712
    {"fund_code": "300712", "fund_name": "永福股份", "fund_type": "股票-创业板", "transaction_time": "2021-10-26 14:28:26", "confirmation_date": "2021-10-26", "transaction_type_raw": "建仓", "confirmed_nav": "79.900", "confirmed_shares": "100", "confirmed_amount": "7990.00", "fee": "5.00"},
    {"fund_code": "300712", "fund_name": "永福股份", "fund_type": "股票-创业板", "transaction_time": "2021-11-17 14:54:17", "confirmation_date": "2021-11-17", "transaction_type_raw": "清仓", "confirmed_nav": "62.300", "confirmed_shares": "-100", "confirmed_amount": "6230.00", "fee": "11.23"},
    # 晋控电力 000767
    {"fund_code": "000767", "fund_name": "晋控电力", "fund_type": "股票-主板", "transaction_time": "2021-12-15 09:32:41", "confirmation_date": "2021-12-15", "transaction_type_raw": "建仓", "confirmed_nav": "4.020", "confirmed_shares": "1000", "confirmed_amount": "4020.00", "fee": "5.00"},
    {"fund_code": "000767", "fund_name": "晋控电力", "fund_type": "股票-主板", "transaction_time": "2021-12-17 09:44:35", "confirmation_date": "2021-12-17", "transaction_type_raw": "清仓", "confirmed_nav": "4.300", "confirmed_shares": "-1000", "confirmed_amount": "4300.00", "fee": "9.30"},
    # 紫金矿业 601899
    {"fund_code": "601899", "fund_name": "紫金矿业", "fund_type": "股票-主板", "transaction_time": "2021-01-13 09:53:30", "confirmation_date": "2021-01-13", "transaction_type_raw": "建仓", "confirmed_nav": "11.320", "confirmed_shares": "200", "confirmed_amount": "2264.00", "fee": "5.05"},
    {"fund_code": "601899", "fund_name": "紫金矿业", "fund_type": "股票-主板", "transaction_time": "2021-01-14 14:30:09", "confirmation_date": "2021-01-14", "transaction_type_raw": "买入", "confirmed_nav": "10.430", "confirmed_shares": "100", "confirmed_amount": "1043.00", "fee": "5.02"},
    {"fund_code": "601899", "fund_name": "紫金矿业", "fund_type": "股票-主板", "transaction_time": "2021-01-22 10:07:28", "confirmation_date": "2021-01-22", "transaction_type_raw": "买入", "confirmed_nav": "10.860", "confirmed_shares": "300", "confirmed_amount": "3258.00", "fee": "5.07"},
    {"fund_code": "601899", "fund_name": "紫金矿业", "fund_type": "股票-主板", "transaction_time": "2021-01-28 10:49:55", "confirmation_date": "2021-01-28", "transaction_type_raw": "买入", "confirmed_nav": "9.950", "confirmed_shares": "200", "confirmed_amount": "1990.00", "fee": "5.04"},
    {"fund_code": "601899", "fund_name": "紫金矿业", "fund_type": "股票-主板", "transaction_time": "2021-02-22 09:25:00", "confirmation_date": "2021-02-22", "transaction_type_raw": "买入", "confirmed_nav": "14.460", "confirmed_shares": "200", "confirmed_amount": "2892.00", "fee": "5.06"},
    {"fund_code": "601899", "fund_name": "紫金矿业", "fund_type": "股票-主板", "transaction_time": "2021-07-08 16:00:00", "confirmation_date": "2021-07-08", "transaction_type_raw": "分红", "confirmed_nav": "9.730", "confirmed_shares": "0", "confirmed_amount": "120.00", "fee": "0.00"},
    {"fund_code": "601899", "fund_name": "紫金矿业", "fund_type": "股票-主板", "transaction_time": "2022-01-28 14:38:27", "confirmation_date": "2022-01-28", "transaction_type_raw": "清仓", "confirmed_nav": "9.720", "confirmed_shares": "-1000", "confirmed_amount": "9720.00", "fee": "14.91"},
    {"fund_code": "601899", "fund_name": "紫金矿业", "fund_type": "股票-主板", "transaction_time": "2022-02-07 00:00:00", "confirmation_date": "2022-02-07", "transaction_type_raw": "股息红利税补缴", "confirmed_nav": "0.000", "confirmed_shares": "0", "confirmed_amount": "2.40", "fee": "0.00"},
    {"fund_code": "601899", "fund_name": "紫金矿业", "fund_type": "股票-主板", "transaction_time": "2022-02-07 00:00:00", "confirmation_date": "2022-02-07", "transaction_type_raw": "股息红利税补缴", "confirmed_nav": "0.000", "confirmed_shares": "0", "confirmed_amount": "2.40", "fee": "0.00", "order_id": "THS-601899-2022-02-07 00:00:00-股息红利税补缴-2"},
]

INITIAL_BATCH5_RECORDS = [
    # 老白干酒 600559 (19笔，已去重)
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-11-12 10:29:23", "confirmation_date": "2020-11-12", "transaction_type_raw": "建仓", "confirmed_nav": "17.500", "confirmed_shares": "200", "confirmed_amount": "3500.00", "fee": "5.07"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-11-13 11:07:21", "confirmation_date": "2020-11-13", "transaction_type_raw": "买入", "confirmed_nav": "17.010", "confirmed_shares": "100", "confirmed_amount": "1701.00", "fee": "5.03"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-11-13 11:28:00", "confirmation_date": "2020-11-13", "transaction_type_raw": "买入", "confirmed_nav": "16.830", "confirmed_shares": "300", "confirmed_amount": "5049.00", "fee": "5.10"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-11-13 14:15:51", "confirmation_date": "2020-11-13", "transaction_type_raw": "买入", "confirmed_nav": "16.470", "confirmed_shares": "100", "confirmed_amount": "1647.00", "fee": "5.03"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-11-17 14:29:01", "confirmation_date": "2020-11-17", "transaction_type_raw": "买入", "confirmed_nav": "18.000", "confirmed_shares": "100", "confirmed_amount": "1800.00", "fee": "5.04"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-11-20 10:32:07", "confirmation_date": "2020-11-20", "transaction_type_raw": "卖出", "confirmed_nav": "20.090", "confirmed_shares": "-100", "confirmed_amount": "2009.00", "fee": "7.05"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-11-25 09:33:06", "confirmation_date": "2020-11-25", "transaction_type_raw": "买入", "confirmed_nav": "21.310", "confirmed_shares": "100", "confirmed_amount": "2131.00", "fee": "5.04"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-12-02 14:54:24", "confirmation_date": "2020-12-02", "transaction_type_raw": "买入", "confirmed_nav": "18.260", "confirmed_shares": "100", "confirmed_amount": "1826.00", "fee": "5.04"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-12-11 13:19:54", "confirmation_date": "2020-12-11", "transaction_type_raw": "买入", "confirmed_nav": "22.880", "confirmed_shares": "100", "confirmed_amount": "2288.00", "fee": "5.05"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-12-16 10:09:27", "confirmation_date": "2020-12-16", "transaction_type_raw": "买入", "confirmed_nav": "29.490", "confirmed_shares": "100", "confirmed_amount": "2949.00", "fee": "5.06"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-12-18 10:42:26", "confirmation_date": "2020-12-18", "transaction_type_raw": "卖出", "confirmed_nav": "30.000", "confirmed_shares": "-100", "confirmed_amount": "3000.00", "fee": "8.06"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-12-18 10:56:48", "confirmation_date": "2020-12-18", "transaction_type_raw": "卖出", "confirmed_nav": "30.200", "confirmed_shares": "-200", "confirmed_amount": "6040.00", "fee": "11.16"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-12-23 10:19:01", "confirmation_date": "2020-12-23", "transaction_type_raw": "卖出", "confirmed_nav": "30.680", "confirmed_shares": "-200", "confirmed_amount": "6136.00", "fee": "11.26"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-12-24 09:30:53", "confirmation_date": "2020-12-24", "transaction_type_raw": "买入", "confirmed_nav": "29.950", "confirmed_shares": "100", "confirmed_amount": "2995.00", "fee": "5.06"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2020-12-24 09:36:05", "confirmation_date": "2020-12-24", "transaction_type_raw": "买入", "confirmed_nav": "28.750", "confirmed_shares": "100", "confirmed_amount": "2875.00", "fee": "5.06"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2021-01-08 14:10:23", "confirmation_date": "2021-01-08", "transaction_type_raw": "卖出", "confirmed_nav": "35.260", "confirmed_shares": "-300", "confirmed_amount": "10578.00", "fee": "15.79"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2021-01-11 11:14:40", "confirmation_date": "2021-01-11", "transaction_type_raw": "买入", "confirmed_nav": "33.750", "confirmed_shares": "200", "confirmed_amount": "6750.00", "fee": "5.14"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2021-01-15 09:40:37", "confirmation_date": "2021-01-15", "transaction_type_raw": "卖出", "confirmed_nav": "28.000", "confirmed_shares": "-400", "confirmed_amount": "11200.00", "fee": "16.42"},
    {"fund_code": "600559", "fund_name": "老白干酒", "fund_type": "股票-主板", "transaction_time": "2021-01-19 11:07:29", "confirmation_date": "2021-01-19", "transaction_type_raw": "清仓", "confirmed_nav": "27.700", "confirmed_shares": "-300", "confirmed_amount": "8310.00", "fee": "13.48"},
    # 会稽山 601579 (8笔，已去重)
    {"fund_code": "601579", "fund_name": "会稽山", "fund_type": "股票-主板", "transaction_time": "2020-12-23 09:49:15", "confirmation_date": "2020-12-23", "transaction_type_raw": "建仓", "confirmed_nav": "16.210", "confirmed_shares": "200", "confirmed_amount": "3242.00", "fee": "5.06"},
    {"fund_code": "601579", "fund_name": "会稽山", "fund_type": "股票-主板", "transaction_time": "2020-12-23 14:34:05", "confirmation_date": "2020-12-23", "transaction_type_raw": "买入", "confirmed_nav": "14.900", "confirmed_shares": "300", "confirmed_amount": "4470.00", "fee": "5.09"},
    {"fund_code": "601579", "fund_name": "会稽山", "fund_type": "股票-主板", "transaction_time": "2020-12-24 09:32:08", "confirmation_date": "2020-12-24", "transaction_type_raw": "买入", "confirmed_nav": "13.750", "confirmed_shares": "200", "confirmed_amount": "2750.00", "fee": "5.06"},
    {"fund_code": "601579", "fund_name": "会稽山", "fund_type": "股票-主板", "transaction_time": "2021-01-05 11:08:48", "confirmation_date": "2021-01-05", "transaction_type_raw": "买入", "confirmed_nav": "14.000", "confirmed_shares": "200", "confirmed_amount": "2800.00", "fee": "5.06"},
    {"fund_code": "601579", "fund_name": "会稽山", "fund_type": "股票-主板", "transaction_time": "2021-01-08 14:29:13", "confirmation_date": "2021-01-08", "transaction_type_raw": "卖出", "confirmed_nav": "13.050", "confirmed_shares": "-200", "confirmed_amount": "2610.00", "fee": "7.66"},
    {"fund_code": "601579", "fund_name": "会稽山", "fund_type": "股票-主板", "transaction_time": "2021-01-11 14:46:57", "confirmation_date": "2021-01-11", "transaction_type_raw": "买入", "confirmed_nav": "11.800", "confirmed_shares": "100", "confirmed_amount": "1180.00", "fee": "5.02"},
    {"fund_code": "601579", "fund_name": "会稽山", "fund_type": "股票-主板", "transaction_time": "2021-01-19 14:07:28", "confirmation_date": "2021-01-19", "transaction_type_raw": "卖出", "confirmed_nav": "10.650", "confirmed_shares": "-400", "confirmed_amount": "4260.00", "fee": "9.34"},
    {"fund_code": "601579", "fund_name": "会稽山", "fund_type": "股票-主板", "transaction_time": "2021-01-19 14:56:30", "confirmation_date": "2021-01-19", "transaction_type_raw": "清仓", "confirmed_nav": "10.700", "confirmed_shares": "-400", "confirmed_amount": "4280.00", "fee": "9.37"},
    # 长城汽车 601633 (6笔)
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2021-02-26 14:12:19", "confirmation_date": "2021-02-26", "transaction_type_raw": "建仓", "confirmed_nav": "31.500", "confirmed_shares": "200", "confirmed_amount": "6300.00", "fee": "5.13"},
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2021-03-08 16:00:00", "confirmation_date": "2021-03-08", "transaction_type_raw": "分红", "confirmed_nav": "28.770", "confirmed_shares": "0", "confirmed_amount": "56.00", "fee": "0.00"},
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2021-04-19 13:14:33", "confirmation_date": "2021-04-19", "transaction_type_raw": "买入", "confirmed_nav": "34.520", "confirmed_shares": "100", "confirmed_amount": "3452.00", "fee": "5.07"},
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2021-06-01 11:01:22", "confirmation_date": "2021-06-01", "transaction_type_raw": "卖出", "confirmed_nav": "37.500", "confirmed_shares": "-200", "confirmed_amount": "7500.00", "fee": "12.65"},
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2021-06-02 00:00:00", "confirmation_date": "2021-06-02", "transaction_type_raw": "股息红利税补缴", "confirmed_nav": "0.000", "confirmed_shares": "0", "confirmed_amount": "5.60", "fee": "0.00"},
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2021-06-08 09:33:12", "confirmation_date": "2021-06-08", "transaction_type_raw": "清仓", "confirmed_nav": "39.000", "confirmed_shares": "-100", "confirmed_amount": "3900.00", "fee": "8.98"},
    # 涪陵榨菜 002507 (2笔)
    {"fund_code": "002507", "fund_name": "涪陵榨菜", "fund_type": "股票-主板", "transaction_time": "2020-12-21 10:36:22", "confirmation_date": "2020-12-21", "transaction_type_raw": "建仓", "confirmed_nav": "43.320", "confirmed_shares": "200", "confirmed_amount": "8664.00", "fee": "5.00"},
    {"fund_code": "002507", "fund_name": "涪陵榨菜", "fund_type": "股票-主板", "transaction_time": "2021-08-02 09:34:46", "confirmation_date": "2021-08-02", "transaction_type_raw": "清仓", "confirmed_nav": "28.300", "confirmed_shares": "-200", "confirmed_amount": "5660.00", "fee": "10.66"},
    # 青岛啤酒 600600 (2笔)
    {"fund_code": "600600", "fund_name": "青岛啤酒", "fund_type": "股票-主板", "transaction_time": "2020-12-22 14:33:45", "confirmation_date": "2020-12-22", "transaction_type_raw": "建仓", "confirmed_nav": "100.000", "confirmed_shares": "100", "confirmed_amount": "10000.00", "fee": "5.20"},
    {"fund_code": "600600", "fund_name": "青岛啤酒", "fund_type": "股票-主板", "transaction_time": "2021-08-06 09:55:20", "confirmation_date": "2021-08-06", "transaction_type_raw": "清仓", "confirmed_nav": "82.530", "confirmed_shares": "-100", "confirmed_amount": "8253.00", "fee": "13.42"},
    # 歌尔股份 002241 (8笔，已去重)
    {"fund_code": "002241", "fund_name": "歌尔股份", "fund_type": "股票-主板", "transaction_time": "2020-12-09 13:53:52", "confirmation_date": "2020-12-09", "transaction_type_raw": "建仓", "confirmed_nav": "40.000", "confirmed_shares": "100", "confirmed_amount": "4000.00", "fee": "5.00"},
    {"fund_code": "002241", "fund_name": "歌尔股份", "fund_type": "股票-主板", "transaction_time": "2021-01-20 14:35:51", "confirmation_date": "2021-01-20", "transaction_type_raw": "买入", "confirmed_nav": "41.850", "confirmed_shares": "100", "confirmed_amount": "4185.00", "fee": "5.00"},
    {"fund_code": "002241", "fund_name": "歌尔股份", "fund_type": "股票-主板", "transaction_time": "2021-01-25 09:38:29", "confirmation_date": "2021-01-25", "transaction_type_raw": "买入", "confirmed_nav": "37.800", "confirmed_shares": "200", "confirmed_amount": "7560.00", "fee": "5.00"},
    {"fund_code": "002241", "fund_name": "歌尔股份", "fund_type": "股票-主板", "transaction_time": "2021-05-19 17:00:01", "confirmation_date": "2021-05-19", "transaction_type_raw": "分红", "confirmed_nav": "39.320", "confirmed_shares": "0", "confirmed_amount": "60.00", "fee": "0.00"},
    {"fund_code": "002241", "fund_name": "歌尔股份", "fund_type": "股票-主板", "transaction_time": "2021-08-12 13:40:18", "confirmation_date": "2021-08-12", "transaction_type_raw": "卖出", "confirmed_nav": "43.000", "confirmed_shares": "-200", "confirmed_amount": "8600.00", "fee": "13.60"},
    {"fund_code": "002241", "fund_name": "歌尔股份", "fund_type": "股票-主板", "transaction_time": "2021-08-13 00:00:00", "confirmation_date": "2021-08-13", "transaction_type_raw": "股息红利税补缴", "confirmed_nav": "0.000", "confirmed_shares": "0", "confirmed_amount": "3.00", "fee": "0.00"},
    {"fund_code": "002241", "fund_name": "歌尔股份", "fund_type": "股票-主板", "transaction_time": "2021-08-23 09:59:27", "confirmation_date": "2021-08-23", "transaction_type_raw": "清仓", "confirmed_nav": "40.000", "confirmed_shares": "-200", "confirmed_amount": "8000.00", "fee": "13.00"},
    {"fund_code": "002241", "fund_name": "歌尔股份", "fund_type": "股票-主板", "transaction_time": "2021-08-24 00:00:00", "confirmation_date": "2021-08-24", "transaction_type_raw": "股息红利税补缴", "confirmed_nav": "0.000", "confirmed_shares": "0", "confirmed_amount": "3.00", "fee": "0.00"},
]

INITIAL_601633_CYCLE1_RECORDS = [
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2020-11-09 09:33:57", "confirmation_date": "2020-11-09", "transaction_type_raw": "建仓", "confirmed_nav": "27.790", "confirmed_shares": "200", "confirmed_amount": "5558.00", "fee": "5.11"},
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2020-12-02 14:21:08", "confirmation_date": "2020-12-02", "transaction_type_raw": "买入", "confirmed_nav": "26.500", "confirmed_shares": "100", "confirmed_amount": "2650.00", "fee": "5.05"},
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2020-12-02 14:36:24", "confirmation_date": "2020-12-02", "transaction_type_raw": "买入", "confirmed_nav": "26.380", "confirmed_shares": "100", "confirmed_amount": "2638.00", "fee": "5.05"},
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2020-12-10 14:20:18", "confirmation_date": "2020-12-10", "transaction_type_raw": "买入", "confirmed_nav": "24.370", "confirmed_shares": "100", "confirmed_amount": "2437.00", "fee": "5.05"},
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2020-12-21 10:29:48", "confirmation_date": "2020-12-21", "transaction_type_raw": "卖出", "confirmed_nav": "30.530", "confirmed_shares": "-300", "confirmed_amount": "9159.00", "fee": "14.34"},
    {"fund_code": "601633", "fund_name": "长城汽车", "fund_type": "股票-主板", "transaction_time": "2020-12-22 09:57:46", "confirmation_date": "2020-12-22", "transaction_type_raw": "清仓", "confirmed_nav": "31.200", "confirmed_shares": "-200", "confirmed_amount": "6240.00", "fee": "11.36"},
]

INITIAL_513300_CYCLE1_RECORDS = [
    {"fund_code": "513300", "fund_name": "纳斯达克ETF华夏", "fund_type": "指数型-海外股票", "transaction_time": "2024-01-12 10:05:02", "confirmation_date": "2024-01-12", "transaction_type_raw": "建仓", "confirmed_nav": "1.519", "confirmed_shares": "5400", "confirmed_amount": "8202.60", "fee": "5.00"},
    {"fund_code": "513300", "fund_name": "纳斯达克ETF华夏", "fund_type": "指数型-海外股票", "transaction_time": "2024-01-16 09:52:02", "confirmation_date": "2024-01-16", "transaction_type_raw": "买入", "confirmed_nav": "1.525", "confirmed_shares": "5800", "confirmed_amount": "8845.00", "fee": "5.00"},
    {"fund_code": "513300", "fund_name": "纳斯达克ETF华夏", "fund_type": "指数型-海外股票", "transaction_time": "2024-01-17 11:24:40", "confirmation_date": "2024-01-17", "transaction_type_raw": "买入", "confirmed_nav": "1.523", "confirmed_shares": "600", "confirmed_amount": "913.80", "fee": "5.00"},
    {"fund_code": "513300", "fund_name": "纳斯达克ETF华夏", "fund_type": "指数型-海外股票", "transaction_time": "2024-01-23 13:10:45", "confirmation_date": "2024-01-23", "transaction_type_raw": "买入", "confirmed_nav": "1.590", "confirmed_shares": "4000", "confirmed_amount": "6360.00", "fee": "5.00"},
    {"fund_code": "513300", "fund_name": "纳斯达克ETF华夏", "fund_type": "指数型-海外股票", "transaction_time": "2024-07-04 09:43:59", "confirmation_date": "2024-07-04", "transaction_type_raw": "买入", "confirmed_nav": "1.975", "confirmed_shares": "4000", "confirmed_amount": "7900.00", "fee": "5.00"},
    {"fund_code": "513300", "fund_name": "纳斯达克ETF华夏", "fund_type": "指数型-海外股票", "transaction_time": "2024-08-05 14:46:10", "confirmation_date": "2024-08-05", "transaction_type_raw": "买入", "confirmed_nav": "1.654", "confirmed_shares": "4900", "confirmed_amount": "8104.60", "fee": "5.00"},
    {"fund_code": "513300", "fund_name": "纳斯达克ETF华夏", "fund_type": "指数型-海外股票", "transaction_time": "2024-09-12 11:08:37", "confirmation_date": "2024-09-12", "transaction_type_raw": "卖出", "confirmed_nav": "1.800", "confirmed_shares": "-6000", "confirmed_amount": "10800.00", "fee": "5.00"},
    {"fund_code": "513300", "fund_name": "纳斯达克ETF华夏", "fund_type": "指数型-海外股票", "transaction_time": "2024-10-08 09:36:16", "confirmation_date": "2024-10-08", "transaction_type_raw": "清仓", "confirmed_nav": "1.736", "confirmed_shares": "-18700", "confirmed_amount": "32463.20", "fee": "8.12"},
]

INITIAL_600036_EARLY_RECORDS = [
    {"fund_code": "600036", "fund_name": "招商银行", "fund_type": "股票-主板", "transaction_time": "2020-07-08 14:44:04", "confirmation_date": "2020-07-08", "transaction_type_raw": "建仓", "confirmed_nav": "41.400", "confirmed_shares": "100", "confirmed_amount": "4140.00", "fee": "5.08"},
    {"fund_code": "600036", "fund_name": "招商银行", "fund_type": "股票-主板", "transaction_time": "2020-07-09 16:00:00", "confirmation_date": "2020-07-09", "transaction_type_raw": "分红", "confirmed_nav": "39.910", "confirmed_shares": "0", "confirmed_amount": "120.00", "fee": "0.00"},
    {"fund_code": "600036", "fund_name": "招商银行", "fund_type": "股票-主板", "transaction_time": "2020-09-18 13:20:06", "confirmation_date": "2020-09-18", "transaction_type_raw": "清仓", "confirmed_nav": "38.600", "confirmed_shares": "-100", "confirmed_amount": "3860.00", "fee": "8.94"},
    {"fund_code": "600036", "fund_name": "招商银行", "fund_type": "股票-主板", "transaction_time": "2020-09-30 13:54:01", "confirmation_date": "2020-09-30", "transaction_type_raw": "建仓", "confirmed_nav": "36.000", "confirmed_shares": "100", "confirmed_amount": "3600.00", "fee": "5.07"},
    {"fund_code": "600036", "fund_name": "招商银行", "fund_type": "股票-主板", "transaction_time": "2020-11-10 11:16:27", "confirmation_date": "2020-11-10", "transaction_type_raw": "清仓", "confirmed_nav": "43.670", "confirmed_shares": "-100", "confirmed_amount": "4367.00", "fee": "9.46"},
]

INITIAL_002235_RECORDS = [
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-03-11 13:29:20", "confirmation_date": "2019-03-11", "transaction_type_raw": "建仓", "confirmed_nav": "6.990", "confirmed_shares": "200", "confirmed_amount": "1398.00", "fee": "5.00"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-03-12 10:05:06", "confirmation_date": "2019-03-12", "transaction_type_raw": "清仓", "confirmed_nav": "7.800", "confirmed_shares": "-200", "confirmed_amount": "1560.00", "fee": "6.56"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-04-23 14:56:25", "confirmation_date": "2019-04-23", "transaction_type_raw": "建仓", "confirmed_nav": "9.780", "confirmed_shares": "100", "confirmed_amount": "978.00", "fee": "5.00"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-04-24 13:33:55", "confirmation_date": "2019-04-24", "transaction_type_raw": "清仓", "confirmed_nav": "10.000", "confirmed_shares": "-100", "confirmed_amount": "1000.00", "fee": "6.00"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-05-20 09:45:32", "confirmation_date": "2019-05-20", "transaction_type_raw": "建仓", "confirmed_nav": "8.700", "confirmed_shares": "300", "confirmed_amount": "2610.00", "fee": "5.00"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-05-23 13:41:02", "confirmation_date": "2019-05-23", "transaction_type_raw": "买入", "confirmed_nav": "8.250", "confirmed_shares": "100", "confirmed_amount": "825.00", "fee": "5.00"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-06-04 13:10:35", "confirmation_date": "2019-06-04", "transaction_type_raw": "卖出", "confirmed_nav": "8.270", "confirmed_shares": "-200", "confirmed_amount": "1654.00", "fee": "6.65"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-06-05 10:01:28", "confirmation_date": "2019-06-05", "transaction_type_raw": "卖出", "confirmed_nav": "8.530", "confirmed_shares": "-100", "confirmed_amount": "853.00", "fee": "5.85"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-06-06 14:39:09", "confirmation_date": "2019-06-06", "transaction_type_raw": "清仓", "confirmed_nav": "8.150", "confirmed_shares": "-100", "confirmed_amount": "815.00", "fee": "5.82"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-06-10 14:42:09", "confirmation_date": "2019-06-10", "transaction_type_raw": "建仓", "confirmed_nav": "7.500", "confirmed_shares": "100", "confirmed_amount": "750.00", "fee": "5.00"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-06-25 14:37:54", "confirmation_date": "2019-06-25", "transaction_type_raw": "清仓", "confirmed_nav": "9.000", "confirmed_shares": "-100", "confirmed_amount": "900.00", "fee": "5.90"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-07-02 14:49:53", "confirmation_date": "2019-07-02", "transaction_type_raw": "建仓", "confirmed_nav": "8.610", "confirmed_shares": "100", "confirmed_amount": "861.00", "fee": "5.00"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-07-08 13:47:03", "confirmation_date": "2019-07-08", "transaction_type_raw": "买入", "confirmed_nav": "7.890", "confirmed_shares": "100", "confirmed_amount": "789.00", "fee": "5.00"},
    {"fund_code": "002235", "fund_name": "安妮股份", "fund_type": "股票-主板", "transaction_time": "2019-07-19 13:47:12", "confirmation_date": "2019-07-19", "transaction_type_raw": "清仓", "confirmed_nav": "8.250", "confirmed_shares": "-200", "confirmed_amount": "1650.00", "fee": "6.65"},
]
