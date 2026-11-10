"""Read-only domestic asset queries shared by Mac and Web."""
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

from portfolio.paths import DOMESTIC_ASSETS_DIR

from .storage import CALC_FILE, RAW_FILE, connect

LABELS = {
    "fund_code": "基金代码", "fund_name": "基金名称", "holding_shares": "持有份额",
    "unit_nav": "单位净值", "nav_date": "净值日期", "market_value": "市值（CNY）",
    "buy_shares": "累计买入份额", "sell_shares": "累计卖出份额", "valuation_status": "估值状态",
    "transaction_time": "交易时间", "confirmation_date": "确认日期", "transaction_type": "原始交易类型",
    "normalized": "账本方向", "application_amount": "申请金额", "confirmed_amount": "确认金额",
    "application_shares": "申请份额", "confirmed_shares": "确认份额", "fee": "手续费", "order_id": "订单号",
    "source_page": "PDF 页码", "parse_status": "状态", "month": "月份",
    "source_type": "文件类型", "source_filename": "文件名", "total_pages": "页数", "parsed_rows": "解析记录",
    "inserted_rows": "已入库", "error_rows": "异常", "duplicate_rows": "重复", "status": "状态",
    "created_at": "导入时间（UTC）", "check_code": "校验项", "detail": "说明",
}
COLUMNS = {
    "positions": ("fund_code", "fund_name", "holding_shares", "unit_nav", "nav_date", "market_value",
                  "buy_shares", "sell_shares", "valuation_status"),
    "transactions": ("transaction_time", "confirmation_date", "fund_name", "fund_code", "transaction_type",
                     "normalized", "application_amount", "confirmed_amount", "application_shares", "confirmed_shares",
                     "fee", "order_id", "source_page", "parse_status"),
    "monthly": ("month", "fund_code", "fund_name", "holding_shares", "unit_nav", "nav_date", "market_value", "valuation_status"),
    "imports": ("source_type", "source_filename", "total_pages", "parsed_rows", "inserted_rows", "error_rows",
                "duplicate_rows", "status", "created_at"),
}
DIRECTIONS = {"BUY": "买入", "SELL": "卖出", "TRANSFER_IN": "买入（转换转入）", "TRANSFER_OUT": "卖出（转换转出）",
              "REINVEST": "红利再投资", "CASH_DIVIDEND": "现金分红", "UNKNOWN": "待核对"}


def paged(db, sql, where, args, order, offset, size):
    filtered = " WHERE " + " AND ".join(where) if where else ""
    count = db.execute(f"SELECT COUNT(*) FROM ({sql}{filtered})", args).fetchone()[0]
    rows = [dict(row) for row in db.execute(f"{sql}{filtered} ORDER BY {order} LIMIT ? OFFSET ?", (*args, size, offset))]
    return count, rows


def read_view(view, *, as_of="", start="", end="", fund="", kind="", page=1, size=50, ascending=False,
              root=DOMESTIC_ASSETS_DIR):
    columns = COLUMNS[view]
    start, end = (date.fromisoformat(value).isoformat() if value else "" for value in (start, end))
    if start and end and start > end:
        raise ValueError("确认日期区间无效")
    page, size = max(1, page), max(1, min(size, 100))
    offset = (page-1)*size
    root = Path(root)
    raw_path, calc_path = root / RAW_FILE, root / CALC_FILE
    result = dict(columns=[(key, LABELS[key]) for key in columns], rows=[], total=0, page=page, size=size,
                  raw_path=str(raw_path), calc_path=str(calc_path), checks=[], issues=[], run=None, as_of="", stale=False)
    if not raw_path.exists():
        return result
    with connect(raw_path, readonly=True) as raw:
        latest_import = raw.execute("SELECT MAX(created_at) FROM raw_import_batches").fetchone()[0]
        if view == "imports":
            result["total"], result["rows"] = paged(raw, "SELECT * FROM raw_import_batches", [], [],
                                                     "created_at DESC", offset, size)
            result["issues"] = [dict(row) for row in raw.execute("""SELECT b.source_filename, i.source_page,
                i.source_row_no, i.reason FROM raw_parse_issues i JOIN raw_import_batches b ON b.id=i.import_batch_id
                ORDER BY i.id DESC LIMIT 100""")]
        if view == "transactions":
            where, args = [], []
            for clause, value in (("confirmation_date>=?", start), ("confirmation_date<=?", end),
                                  ("fund_code=?", fund), ("transaction_type=?", kind)):
                if value:
                    where.append(clause)
                    args.append(value)
            result["total"], result["rows"] = paged(
                raw, "SELECT * FROM raw_alipay_fund_transactions", where, args,
                f"transaction_time {'ASC' if ascending else 'DESC'}, source_page, source_row_no, id", offset, size,
            )
            for row in result["rows"]:
                row.pop("raw_data")
    if not calc_path.exists():
        result["stale"] = bool(latest_import)
        return result

    with connect(calc_path, readonly=True) as calc:
        calc.execute("BEGIN")
        if view == "imports":
            latest = calc.execute("SELECT * FROM calc_runs ORDER BY completed_at DESC LIMIT 1").fetchone()
            result["run"] = dict(latest) if latest else None
        run = calc.execute("SELECT * FROM calc_runs WHERE status='完成' ORDER BY completed_at DESC LIMIT 1").fetchone()
        if run is None or (latest_import and latest_import > run["completed_at"]):
            result["stale"] = bool(latest_import)
            return result
        if view == "imports":
            result["checks"] = [dict(row) for row in calc.execute(
                "SELECT check_code,fund_code,detail FROM calc_data_quality_checks WHERE run_id=? ORDER BY id", (run["id"],)
            )]
        elif view == "transactions":
            for row in result["rows"]:
                normalized = calc.execute("SELECT transaction_type FROM calc_fund_transactions WHERE raw_transaction_id=?",
                                          (row["id"],)).fetchone()
                row["normalized"] = DIRECTIONS.get(normalized[0]) if normalized else None
        elif view == "monthly":
            where, args = (["p.fund_code=?"], [fund]) if fund else ([], [])
            result["total"], result["rows"] = paged(calc, """SELECT p.*, f.fund_name FROM calc_fund_positions_monthly p
                JOIN calc_fund_master f ON f.fund_code=p.fund_code""", where, args,
                "p.month DESC,p.fund_code", offset, size)
        elif view == "positions":
            as_of = date.fromisoformat(as_of).isoformat() if as_of else calc.execute(
                "SELECT MAX(trade_date) FROM calc_fund_positions_daily").fetchone()[0]
            result["as_of"] = as_of or ""
            if not as_of:
                return result
            where, args = ["p.trade_date=?"], [as_of]
            if fund:
                where.append("p.fund_code=?")
                args.append(fund)
            result["total"], result["rows"] = paged(calc, """SELECT p.*, f.fund_name FROM calc_fund_positions_daily p
                JOIN calc_fund_master f ON f.fund_code=p.fund_code""", where, args, "p.fund_code", offset, size)
            first = calc.execute("SELECT MIN(trade_date) FROM calc_fund_positions_daily").fetchone()[0]
            with connect(raw_path, readonly=True) as raw, localcontext() as precision:
                precision.prec = 60
                for row in result["rows"]:
                    code = row["fund_code"]
                    shares = row["holding_shares"]
                    opening = raw.execute("""SELECT unit_nav,unit_nav_date,market_value FROM raw_fund_opening_positions
                        WHERE fund_code=? AND snapshot_date=?""", (code, as_of)).fetchone()
                    prices = {entry[0] for entry in raw.execute(
                        "SELECT unit_nav FROM raw_fund_nav WHERE fund_code=? AND nav_date=?", (code, as_of))}
                    if opening:
                        row.update(unit_nav=opening[0], nav_date=opening[1], market_value=opening[2], valuation_status="资产证明")
                    elif len(prices) == 1 and shares is not None and Decimal(shares) >= 0:
                        price = prices.pop()
                        row.update(unit_nav=price, nav_date=as_of, market_value=str(Decimal(price)*Decimal(shares)),
                                   valuation_status="已估值")
                    else:
                        row["valuation_status"] = "持仓待核对" if shares is None or Decimal(shares)<0 else "缺少净值"
                    values = [Decimal(0), Decimal(0)]
                    for move in calc.execute("""SELECT movement_type,signed_shares FROM calc_fund_share_movements
                        WHERE fund_code=? AND effective_date>? AND effective_date<=?""", (code, first, as_of)):
                        if move[0] in ("BUY", "TRANSFER_IN"):
                            values[0] += Decimal(move[1])
                        elif move[0] in ("SELL", "TRANSFER_OUT"):
                            values[1] -= Decimal(move[1])
                    row["buy_shares"], row["sell_shares"] = map(str, values)
    return result


def read_transaction(raw_id, root=DOMESTIC_ASSETS_DIR):
    with connect(Path(root) / RAW_FILE, readonly=True) as raw:
        row = raw.execute("""SELECT t.*,b.source_filename FROM raw_alipay_fund_transactions t
                          JOIN raw_import_batches b ON b.id=t.import_batch_id WHERE t.id=?""", (raw_id,)).fetchone()
        return dict(row) if row is not None else None
