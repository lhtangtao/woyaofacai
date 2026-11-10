"""Parse the two Alipay fund proof formats without writing to SQLite."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re

PARSER_VERSION = "1"
KINDS = {"定投买入", "用户买入", "用户卖出", "用户跨TA转换", "机构分红", "现金分红", "红利再投资"}


@dataclass
class Document:
    source_type: str
    filename: str
    content: bytes
    pages: int
    rows: list[dict]
    issues: list[dict]
    declared_value: str | None = None

    @property
    def digest(self):
        return sha256(self.content).hexdigest()

    def summary(self):
        return {
            "source_type": self.source_type, "pages": self.pages, "records": len(self.rows),
            "issues": len(self.issues), "funds": len({row["fund_code"] for row in self.rows if row["fund_code"]}),
            "types": dict(Counter(row["transaction_type"] for row in self.rows if "transaction_type" in row)),
            "duplicate_rows": len(self.rows) - len({row["id"] for row in self.rows}),
            "declared_value": self.declared_value,
            "opening_total": str(sum((Decimal(row["market_value"]) for row in self.rows), Decimal(0)))
            if self.source_type == "opening_asset" else None,
        }


def compact(text):
    return re.sub(r"\s+", "", text or "")


def raw_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def record_id(*values):
    return sha256(raw_json(values).encode()).hexdigest()


def number(value):
    value = compact(value)
    if value in {"", "/", "—"}:
        return None
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value):
        raise ValueError("金额或份额格式无法识别")
    normalized = value.replace(",", "")
    if len(normalized.replace(".", "")) > 24:
        raise ValueError("原始数值超过支持精度，需人工核对")
    return normalized


def trade_timestamp(value):
    value = compact(value)
    if not value or value == "/":
        return None
    return datetime.strptime(value, "%Y/%m/%d%H:%M").isoformat(timespec="minutes")


def parse_pdf(path: Path, source_type: str, snapshot: date | None = None) -> Document:
    path = Path(path)
    return parse_document(path.read_bytes(), path.name, source_type, snapshot)


def parse_document(content, filename, source_type, snapshot=None):
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("请先安装 Money Portfolio 的 domestic 可选依赖。") from exc

    with pdfplumber.open(BytesIO(content)) as pdf:
        if not pdf.pages:
            raise ValueError("PDF 没有可读页面")
        cover = compact(pdf.pages[0].extract_text(layout=True))
        doc = Document(source_type, filename, content, len(pdf.pages), [], [])
        if source_type == "opening_asset":
            if snapshot is None or "资产证明" not in cover or "基金" not in cover:
                raise ValueError("请提供基金资产证明及期初日期")
            match = re.search(r"合计约为([\d,]+\.\d+)元", cover)
            doc.declared_value = number(match[1]) if match else None
            if not re.search(rf"截止(?:至|日[:：]?)?{snapshot.year}\D*0?{snapshot.month}\D*0?{snapshot.day}(?!\d)", cover):
                raise ValueError("指定期初日期与资产证明首页不符")
            parse_opening(doc, pdf.pages, snapshot)
        elif source_type == "transaction":
            if "交易明细" not in cover:
                raise ValueError("请提供支付宝基金交易明细")
            parse_transactions(doc, pdf.pages)
        else:
            raise ValueError("未知 PDF 来源类型")
    return doc


def table_rows(pages):
    for page_no, page in enumerate(pages, 1):
        for row_no, row in enumerate((r for table in page.extract_tables() for r in table), 1):
            if any(compact(v) for v in row):
                yield page_no, row_no, row


def issue(doc, page, row, reason, raw):
    doc.issues.append(dict(source_page=page, source_row_no=row, reason=reason, raw_data=raw_json(raw)))


def parse_opening(doc, pages, snapshot):
    for page, row_no, row in table_rows(pages):
        if compact(row[0]) == "序号":
            continue
        try:
            _, account, name, code, shares, nav, nav_date, value = row
            code = compact(code)
            if not re.fullmatch(r"\d{6}", code):
                raise ValueError("期初基金代码无效")
            shares, nav, value = map(number, (shares, nav, value))
            if None in (shares, nav, value):
                raise ValueError("期初数值缺失")
            doc.rows.append(dict(
                id=record_id("opening", compact(account), snapshot.isoformat(), code),
                snapshot_date=snapshot.isoformat(), fund_code=code, fund_name=compact(name),
                total_shares=shares, unit_nav=nav,
                unit_nav_date=datetime.strptime(compact(nav_date), "%Y%m%d").date().isoformat(),
                market_value=value, source_page=page, raw_data=raw_json(row),
            ))
        except ValueError as exc:
            issue(doc, page, row_no, str(exc), row)
    total = sum((Decimal(row["market_value"]) for row in doc.rows), Decimal(0))
    if not doc.rows or doc.declared_value is None or total != Decimal(doc.declared_value):
        issue(doc, 1, 0, "期初逐项小计与证明首页总额无法核对", [])
    if len({row["id"] for row in doc.rows}) != len(doc.rows):
        issue(doc, 1, 0, "期初持仓出现重复基金", [])


def parse_transactions(doc, pages):
    fragments = []
    seen_pages = set()
    for page, row_no, row in table_rows(pages):
        seen_pages.add(page)
        if compact(row[0]) == "订单号":
            continue
        if len(row) != 12:
            issue(doc, page, row_no, "交易栏数与证明不符", row)
            continue
        if compact(row[2]) or re.match(r"20\d{2}/", compact(row[1])):
            if fragments:
                finish_transaction(doc, fragments)
            fragments = []
        elif not fragments or page != fragments[-1][0] + 1 or row_no != 1:
            issue(doc, page, row_no, "跨页续行无法归属", row)
            continue
        fragments.append((page, row_no, row))
    if fragments:
        finish_transaction(doc, fragments)
    for page in set(range(2, doc.pages + 1)) - seen_pages:
        issue(doc, page, 0, "交易页未读取到表格", [])


def finish_transaction(doc, fragments):
    page, row_no, _ = fragments[0]
    fields = [compact("".join(row[index] or "" for _, _, row in fragments)) for index in range(12)]
    order, when, kind, name, portfolio, code, *values, confirmation = fields
    errors = []
    if not re.fullmatch(r"\d{32}", order):
        errors.append("订单号无法完整还原")
    if not re.fullmatch(r"\d{6}", code):
        errors.append("基金代码缺失或无效")
    if kind not in KINDS:
        errors.append("未识别的交易类型")
    timestamps = []
    for value in (when, confirmation):
        try:
            timestamps.append(trade_timestamp(value))
        except ValueError:
            timestamps.append(None)
    if None in timestamps or timestamps[1][:10] < timestamps[0][:10]:
        errors.append("交易或确认日期缺失或异常")
    amounts = []
    for value in values:
        try:
            amounts.append(number(value))
        except ValueError as exc:
            amounts.append(None)
            errors.append(str(exc))
    if kind in {"用户买入", "定投买入", "用户卖出", "用户跨TA转换", "红利再投资"} and amounts[3] is None:
        errors.append("确认份额缺失")
    if errors:
        issue(doc, page, row_no, "；".join(dict.fromkeys(errors)), fragments)
    doc.rows.append(dict(
        id=record_id("alipay", order, code, kind, when, confirmation,
                     fields if errors else None),
        source_page=page, source_row_no=row_no, order_id=order,
        transaction_time=timestamps[0], transaction_type=kind, fund_name=name, portfolio_fund_name=portfolio or None,
        fund_code=code, application_amount=amounts[0], application_shares=amounts[1],
        confirmed_amount=amounts[2], confirmed_shares=amounts[3], fee=amounts[4],
        confirmation_date=timestamps[1][:10] if timestamps[1] else None,
        raw_data=raw_json(fragments), parse_status="待核对" if errors else "正常",
        parse_error="；".join(dict.fromkeys(errors)) if errors else None,
    ))
