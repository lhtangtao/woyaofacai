"""Extract original fund transactions from encrypted Tiantian statements."""
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re

from .alipay import compact, issue, number, raw_json, record_id

ACCOUNT = "TIANTIAN_FUND"
PLATFORM = "TIANTIAN"
SOURCE_TYPE = "tiantian_transaction"
VERSION = "1"
TRADE_HEADER = ("确认日期", "基金代码", "基金简称", "业务类型", "确认状态", "确认份额",
                "确认金额", "手续费", "确认净值", "关联银行卡")
DIVIDEND_HEADER = ("确认日期", "基金代码", "基金简称", "分红方式", "分红金额", "分红再投份额", "确认净值")
ADVISER_HEADER = ("确认日期", "投顾管理人", "投顾组合名称", "业务类型", "确认状态", "确认金额", "关联银行卡")
SECTIONS = {TRADE_HEADER: "基金交易", DIVIDEND_HEADER: "基金分红", ADVISER_HEADER: "投顾交易"}
REVIEW_TYPES = {"强减"}


@dataclass
class Statement:
    filename: str
    content: bytes
    pages: int
    period_start: str
    period_end: str
    rows: list[dict]
    issues: list[dict]
    repeated_looking: int = 0

    @property
    def digest(self):
        return sha256(self.content).hexdigest()

    @property
    def dates(self):
        return [row["confirmation_date"] for row in self.rows if row["confirmation_date"]]

    def summary(self):
        return {
            "period": [self.period_start, self.period_end],
            "actual_dates": [min(self.dates, default=None), max(self.dates, default=None)],
            "pages": self.pages, "records": len(self.rows),
            "funds": len({row["fund_code"] for row in self.rows if row["fund_code"]}),
            "types": dict(Counter(row["transaction_type_raw"] for row in self.rows)),
            "dividends": sum(row["source_section"] == "基金分红" for row in self.rows),
            "issues": len(self.issues), "review": sum(row["parse_status"] == "待核对" for row in self.rows),
            "same_fields_retained": self.repeated_looking,
        }


def parse_pdf(path, password):
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    path = Path(path)
    content = path.read_bytes()
    try:
        with pdfplumber.open(BytesIO(content), password=password) as pdf:
            if not pdf.pages:
                raise ValueError("PDF 无可读取的页面")
            return parse_pages(path.name, content, pdf.pages)
    except PDFPasswordIncorrect:
        raise ValueError("无法解密天天基金 PDF，请核对该文件的密码") from None


def parse_pages(filename, content, pages):
    cover = compact(pages[0].extract_text())
    period = re.search(r"历史交易明细[（(](\d{4}-\d\d-\d\d)[~～至](\d{4}-\d\d-\d\d)[）)]", cover)
    if not re.search(r"天天基金(?:月度)?对账单", cover) or not period:
        raise ValueError("无法识别天天基金交易表或对账期间")
    start, end = (date.fromisoformat(value).isoformat() for value in period.groups())
    if end < start:
        raise ValueError("天天基金对账期间无效")
    doc = Statement(filename, content, len(pages), start, end, [], [])
    header = None
    saw_trades = False
    occurrences = Counter()
    for page_no, page in enumerate(pages, 1):
        record_no = 0
        for table in page.extract_tables():
            if not table:
                continue
            fields = tuple(compact(cell) for cell in table[0])
            if fields in SECTIONS:
                header = fields
                saw_trades |= header == TRADE_HEADER
                data = table[1:]
            elif header and len(fields) == len(header):
                data = table
            else:
                header = None
                continue
            for cells in data:
                if not any(compact(cell) for cell in cells):
                    continue
                record_no += 1
                issue_count = len(doc.issues)
                row = parse_row(doc, header, cells, page_no, record_no)
                if row is None:
                    continue
                # No order number is supplied. Preserve same-field occurrences
                # within a file, and match each occurrence across overlapping PDFs.
                values = [row[k] for k in ("confirmation_date", "fund_code", "fund_name", "transaction_type_raw",
                                           "confirmation_status", "portfolio_fund_name")]
                values.append(compact(row["linked_bank_card"]))
                for key in ("confirmed_shares", "confirmed_amount", "fee", "confirmed_nav"):
                    value = row[key]
                    values.append(format(Decimal(value).normalize(), "f") if value is not None else None)
                fingerprint = record_id(PLATFORM, row["source_section"], values)
                if len(doc.issues) != issue_count:
                    fingerprint = record_id(fingerprint, doc.digest, page_no, record_no)
                occurrences[fingerprint] += 1
                occurrence = occurrences[fingerprint]
                row["id"] = record_id(fingerprint, occurrence)
                doc.repeated_looking += int(occurrence > 1)
                doc.rows.append(row)
    if not saw_trades:
        raise ValueError("未读取到天天基金历史交易明细表")
    return doc


def parse_row(doc, header, cells, page, record):
    if len(cells) != len(header):
        issue(doc, page, record, "交易字段数量不符", cells)
        return None
    values = dict(zip(header, map(compact, cells)))
    if not re.fullmatch(r"\d{4}-\d\d-\d\d", values["确认日期"]):
        issue(doc, page, record, "确认日期无法识别", cells)
        return None
    errors = []
    try:
        day = date.fromisoformat(values["确认日期"]).isoformat()
        if not doc.period_start <= day <= doc.period_end:
            errors.append("确认日期超出对账期间")
    except ValueError:
        day = None
        errors.append("确认日期无效")
    fund = values.get("基金代码") or None
    if header != ADVISER_HEADER and not re.fullmatch(r"\d{6}", fund or ""):
        errors.append("基金代码无效")
    kind = values.get("业务类型") or values.get("分红方式") or None
    if kind is None:
        errors.append("原始业务类型缺失")
    parsed = {}
    for key, value in (
        ("confirmed_shares", values.get("确认份额", values.get("分红再投份额"))),
        ("confirmed_amount", values.get("确认金额", values.get("分红金额"))),
        ("fee", values.get("手续费")),
        ("confirmed_nav", values.get("确认净值")),
    ):
        try:
            sign = value[0] if value and value[0] in "+-" else ""
            digits = None if value in {"-", "--"} else number(value[len(sign):] if value else None)
            parsed[key] = sign + digits if digits is not None else None
        except ValueError:
            parsed[key] = None
            errors.append(f"{key} 无法按原始精度解析")
    if errors:
        issue(doc, page, record, "；".join(errors), cells)
    return dict(
        source_page=page, source_record_no=record, source_section=SECTIONS[header],
        fund_code=fund, fund_name=values.get("基金简称") or None,
        portfolio_fund_name=values.get("投顾组合名称") or None, confirmation_date=day,
        transaction_type_raw=kind, confirmation_status=values.get("确认状态") or None,
        linked_bank_card=(dict(zip(header, cells)).get("关联银行卡") or None),
        raw_data=raw_json(dict(zip(header, cells))), parse_status="待核对" if errors or kind in REVIEW_TYPES else "正常",
        parse_error="；".join(errors) or ("强减业务口径待核对" if kind in REVIEW_TYPES else None),
        **parsed,
    )
