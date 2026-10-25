"""Read-only Excel golden snapshot reconciliation CLI and reusable service."""
import argparse
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domain.models import QueryScope
from portfolio.excel_reference import extract_reference
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.export_service import export_report

DIFF_COLUMNS = ("规则ID", "日期", "人员", "账户", "证券", "Excel值", "数据库计算值", "差异", "容差", "状态", "口径", "完整性", "原始记录", "备注", "建议动作")
BROKERS = {"长桥": "LONGBRIDGE", "第一证券": "FIRSTRADE", "嘉信": "SCHWAB"}


def _column(n):
    text = ""
    while n:
        n, digit = divmod(n - 1, 26)
        text = chr(65 + digit) + text
    return text


def _value(cell):
    value = cell.get("value") if cell else None
    if value is None or value in ("-", "—", ""):
        return None
    if cell.get("type") == "n":
        try:
            return Decimal(value)
        except InvalidOperation:
            return value
    return value


def _day(value):
    if isinstance(value, Decimal):
        return (datetime(1899, 12, 30) + timedelta(days=int(value))).isoformat(sep=" ")[:10]
    return str(value or "")[:10]


def reconcile(analytics, excel_path, *, person_name="个人", cancel=None):
    reference = extract_reference(Path(excel_path))
    sheets = {s["name"]: s for s in reference["sheets"]}
    results = []
    issues = Counter(i.rule for i in analytics.issues)
    def compare(rule, day, account, symbol, expected, actual, tolerance, source, note="", completeness="COMPLETE"):
        difference = actual - expected if isinstance(expected, (Decimal, int)) and isinstance(actual, (Decimal, int)) else None
        status = ("UNAVAILABLE" if expected is None or actual is None else "PASS" if expected == actual or (difference is not None and abs(difference) <= tolerance) else "DIFF")
        if status != "PASS":
            reasons = []
            if analytics.latest_profit_day and analytics.latest_profit_day != analytics.scope.as_of.isoformat():
                reasons.append("统计日/收益日不同")
            if issues["DQ001"]:
                reasons.append("源账本含负仓轨迹，精度未按旧版四位股数截断")
            if issues["DQ010"]:
                reasons.append("缺价或历史价格口径待确认")
            if issues["DQ004"]:
                reasons.append("划转未完全配对")
            if rule.startswith("每月微操") or "微操" in rule:
                reasons.append("旧程序 LIFO/残额分摊/逐配对舍入与精确引擎差异")
            note += "；" + "；".join(reasons or ["数据日期、输入快照或计算定义不同，需逐源行复核"])
        results.append(ReportRow((rule, day, person_name, account, symbol, expected, actual, difference, tolerance, status,
                                  "EXCEL_COMPAT", completeness, source, note, "按日期/人员/来源核对，保持原库不变。"), {"status": status}))
    for name in ("当前持仓", "股票汇总", "每日持仓收益", "每月持仓收益", "每月微操收益", "月度净投入明细", "清仓收益", "交易回放"):
        excel_name = "交易明细" if name == "交易回放" else name
        sheet, report = sheets.get(excel_name), analytics.reports[name]
        if not sheet:
            continue
        indexed = defaultdict(list)
        def key(values):
            values = list(values)
            if name in ("每日持仓收益", "每月持仓收益", "每月微操收益", "月度净投入明细"):
                return _day(values[0]) if name == "每日持仓收益" else str(values[0]), BROKERS.get(values[1], values[1]), str(values[3])
            if name == "交易回放":
                # Exact timestamp + broker + symbol + side/quantity/amount makes
                # collisions explicit; repeated identical rows kept as a multiset.
                time = str(values[0])
                if isinstance(values[0], Decimal):
                    time = (datetime(1899, 12, 30) + timedelta(seconds=int(values[0] * 86400))).isoformat(sep=" ")
                side = {"转入": "他人→个人", "转出": "个人→他人"}.get(values[4], values[4])
                return time, BROKERS.get(values[1], values[1]), str(values[3]), side, values[5], values[7]
            return BROKERS.get(values[0], values[0]), str(values[2])
        for row in report.rows:
            indexed[key(row.values)].append(row)
        indices = sorted({int(re.search(r"\d+$", address)[0]) for address in sheet["cells"] if int(re.search(r"\d+$", address)[0]) > 1})
        for row_index in indices:
            if cancel and cancel.is_set():
                raise ValueError("对账已取消。")
            expected_values = [_value(sheet["cells"].get(f"{_column(col + 1)}{row_index}")) for col in range(len(report.columns))]
            if all(v is None for v in expected_values):
                continue
            row_key = key(expected_values)
            row = indexed[row_key].pop(0) if indexed.get(row_key) else None
            for col, expected in enumerate(expected_values):
                actual = row.values[col] if row else None
                if isinstance(expected, str) and expected in BROKERS:
                    expected = BROKERS[expected]
                if col == 0 and name == "每日持仓收益":
                    expected = _day(expected)
                if name == "交易回放" and col == 0:
                    expected = row_key[0]
                # Monetary display precision is $0.01, quantities 1e-8; ratio
                # display had four decimals. Never enlarge tolerances to pass.
                column = report.columns[col]
                tolerance = Decimal("0.00000001") if "数量" in column else Decimal("0.0001") if any(t in column for t in ("比例", "占比", "涨幅")) else Decimal("0.01") if "$" in column else Decimal(0)
                source = f"{excel_name}!{_column(col + 1)}{row_index} | DB sources={row.metadata.get('source_record_ids', ()) if row else ()}"
                compare(f"{excel_name}/{column}", analytics.scope.as_of.isoformat(), str(row_key[-2] if len(row_key) > 2 else row_key[0]), str(row_key[-1]), expected, actual, tolerance, source,
                        "参考 Excel 为缓存快照，未重新计算；" + ("缺少对应 DB 行" if row is None else ""), row.metadata.get("completeness", "UNKNOWN") if row else "MISSING_ROW")
        for remaining in indexed.values():
            for row in remaining:
                compare(f"{excel_name}/新增行", analytics.scope.as_of.isoformat(), "", "", None, str(row.values), Decimal(0), str(row.metadata.get("source_record_ids", ())), "数据库存在 Excel 快照之外的记录。", "NEW_ROW")
    # Dashboard cells are located by their visible label rather than hardcoded
    # coordinates; the value is directly below the KPI label in the reference.
    dashboard = sheets["总览"]["cells"]
    # The workbook has empty formula caches for dashboard KPIs. Recompute ONLY
    # their documented definitions from the saved reference detail values, not
    # from the current database; report clearly labels this separate evidence.
    from portfolio.domain.analytics import Snapshot
    from portfolio.engine.dashboard import overview
    reference_reports = {}
    for name in ("当前持仓", "清仓收益", "每日持仓收益", "每月微操收益"):
        report = analytics.reports[name]
        sheet = sheets[name]
        row_indices = sorted({int(re.search(r"\d+$", address)[0]) for address in sheet["cells"] if int(re.search(r"\d+$", address)[0]) > 1})
        rows = []
        for row_index in row_indices:
            values = tuple(_value(sheet["cells"].get(f"{_column(c + 1)}{row_index}")) for c in range(len(report.columns)))
            if any(v is not None for v in values):
                if name == "每日持仓收益":
                    values = (_day(values[0]), *values[1:])
                rows.append(ReportRow(values, {"account_id": BROKERS.get(values[0], values[0]) if name in ("当前持仓", "清仓收益") else "REFERENCE"}))
        reference_reports[name] = Report(name, report.columns, tuple(rows))
    golden = {m.name: m for m in overview(Snapshot(analytics.scope, [], {}, {}, {}, [], [], []), reference_reports)[0]}
    for metric in analytics.metrics:
        matches = [address for address, cell in dashboard.items() if cell.get("value") == metric.name]
        if matches:
            address = matches[0]
            col, row_num = re.fullmatch(r"([A-Z]+)(\d+)", address).groups()
            cell = f"{col}{int(row_num) + 1}"
            cached = _value(dashboard.get(cell))
            expected = cached if cached is not None else golden[metric.name].value
            compare("KPI/" + metric.name, metric.day, "", "", expected, metric.value,
                    Decimal("0.0001") if metric.ratio else Decimal("0.01"), "总览!" + cell,
                    metric.formula + ("；原公式无缓存，参考值按保存的 Excel 明细独立重算" if cached is None else ""), metric.status)
    for report_name, heading in (("渠道汇总", "渠道"), ("分类汇总", "分类")):
        report = analytics.reports[report_name]
        candidates = [address for address, cell in dashboard.items() if re.fullmatch(r"A\d+", address) and cell.get("value") == heading]
        if not candidates:
            compare(report_name + "/表头", analytics.scope.as_of.isoformat(), "", "", heading, None, Decimal(0), "总览", "未找到唯一汇总块，不能猜测数据范围。")
            continue
        start = int(re.search(r"\d+$", candidates[0])[0])
        by_name = {r.values[0]: r for r in report.rows}
        for index in range(start + 1, start + 20):
            label = _value(dashboard.get(f"A{index}"))
            name = BROKERS.get(label, label)
            if name not in by_name:
                break
            row = by_name[name]
            for col, field in enumerate(report.columns):
                cell = f"{_column(col + 1)}{index}"
                expected = _value(dashboard.get(cell))
                if col == 0:
                    expected = BROKERS.get(expected, expected)
                compare(report_name + "/" + field, analytics.scope.as_of.isoformat(), str(name), "", expected, row.values[col],
                        Decimal("0.0001") if "占比" in field else Decimal("0.01") if col not in (0, 3, 9, 10) else Decimal(0),
                        "总览!" + cell, "缓存缺失时保持无法比较，不把数据库值当作参考值。")
    # Chart series cardinality and cached sums; dynamic named formulas with no
    # cache stay UNAVAILABLE, not a fabricated reference zero.
    for chart, original in zip(analytics.charts, reference["charts"]):
        compare(f"CHART{chart.id:02}/系列数", analytics.scope.as_of.isoformat(), "", "", len(original["series"]), len(chart.series), Decimal(0), original["xml"], chart.title)
        for number, series in enumerate(chart.series):
            cached = original["series"][number]["cached_values"] if number < len(original["series"]) else []
            valid = [Decimal(v) for v in cached if v is not None]
            from portfolio.domain.analytics import strict_sum
            actual = strict_sum(v for key, v in series.points if key.strip())
            compare(f"CHART{chart.id:02}/系列{number + 1}/合计", analytics.scope.as_of.isoformat(), "", "", sum(valid, Decimal(0)) if valid else None,
                    actual, Decimal("0.01"), original["xml"], "含动态命名范围时原图可能无缓存；图24–27的渠道与分类是两组重复维度，不能视为组合总收益。")
            compare(f"CHART{chart.id:02}/系列{number + 1}/点数", analytics.scope.as_of.isoformat(), "", "", len(cached) if cached else None,
                    len(series.points), Decimal(0), original["xml"], chart.title)
    return Report("Excel差异核对", DIFF_COLUMNS, tuple(results), "差异报告逐单元格核对；PASS 不代表其他 DIFF 已解决。")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Money3 只读 Excel 黄金快照对账")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--excel", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--person", default="个人")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    from portfolio.services.trades_service import TradesService
    info = TradesService(args.db).source_info()
    matches = [p["id"] for p in info.persons if p["name"] == args.person]
    if len(matches) != 1:
        parser.error("人员名称不唯一或不存在；请用 --person 指定来源 Excel 对应人员。")
    with localcontext() as ctx:
        ctx.prec = 50
        analytics = AnalyticsService(args.db).calculate(QueryScope(args.as_of, person_ids=tuple(matches), include_disabled=True))
        report = reconcile(analytics, args.excel, person_name=args.person)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    parameters = {"as_of": str(args.as_of), "person": args.person, "source_identity": analytics.source_identity, "latest_profit_day": analytics.latest_profit_day}
    export_report(report, args.report, parameters=parameters)
    if args.report.suffix.lower() != ".json":
        export_report(report, args.report.with_suffix(".json"), parameters=parameters)
    summary = Counter(r.values[9] for r in report.rows)
    print("对账已导出：" + str(dict(summary)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
