import csv
import json
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from portfolio.domain.models import TRADE_COLUMNS, exact_text


def safe_text(value):
    text = exact_text(value)
    if isinstance(value, str) and text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return "" if value is None else text


def export_report(report, path, *, parameters=None):
    """Export derived read models only. Exact decimals stored as text in XLSX.

    OOXML is generated with the standard library: no financial value is coerced
    through float or Excel's 15-digit number limit; no formulas/macros/links.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(report.columns)
            writer.writerows([safe_text(v) for v in row.values] for row in report.rows)
    elif suffix == ".json":
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"report": report.name, "parameters": parameters or {}, "columns": report.columns,
                       "rows": [{"values": r.values, "metadata": r.metadata} for r in report.rows]}, stream,
                      ensure_ascii=False, indent=2, default=exact_text)
    elif suffix == ".xlsx":
        _export_xlsx(report, path, parameters or {})
    else:
        raise ValueError("请选择 .csv、.json 或 .xlsx 导出格式。")


def _export_xlsx(report, path, parameters):
    def letter(index):
        value = ""
        while index:
            index, digit = divmod(index - 1, 26)
            value = chr(65 + digit) + value
        return value

    def xml_sheet(rows):
        parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane xSplit="1" ySplit="1" topLeftCell="B2" state="frozen"/></sheetView></sheetViews><sheetData>']
        for index, row in enumerate(rows, 1):
            parts.append(f'<row r="{index}">')
            for col, value in enumerate(row, 1):
                if value is not None:
                    text = escape(safe_text(value))
                    parts.append(f'<c r="{letter(col)}{index}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>')
            parts.append('</row>')
        parts.append('</sheetData></worksheet>')
        return "".join(parts)
    with path.open("xb") as stream:
        with ZipFile(stream, "w", ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="数据" sheetId="1" r:id="rId1"/><sheet name="参数说明" sheetId="2" r:id="rId2"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml', xml_sheet([report.columns, *(r.values for r in report.rows)]))
            z.writestr('xl/worksheets/sheet2.xml', xml_sheet([("报表", report.name), ("说明", report.note), ("精度", "财务值按十进制文本保存，避免 Excel 15 位数值限制"), *[(str(k), exact_text(v)) for k, v in parameters.items()]]))


def export_page(page, path: Path):
    """仅导出用户已查询的一页；精度不丢失，不覆盖已有文件。"""
    def safe_text(value):
        text = exact_text(value)
        # 外部文件字段不能作为 Excel 公式执行。负数 Decimal 是可信数值，不加前缀。
        if isinstance(value, str) and text.startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + text
        return "" if value is None else text
    with Path(path).open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(TRADE_COLUMNS)
        writer.writerows([safe_text(value) for value in row.values] for row in page.rows)
