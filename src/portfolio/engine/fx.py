from dataclasses import replace
from decimal import Decimal

from portfolio.domain.analytics import ZERO, Issue, ratio, strict_sum
from portfolio.domain.models import from_e8


def convert_reports(snapshot, reports, metrics, currency):
    """Presentation conversion of USD results; not a CNY total-return engine.

    Exact effective date; optional audited cache resolves prior published rates.
    Multiple provider rates on the same day require agreement; not averaged.
    """
    def rate(day):
        candidates = []
        for row in snapshot.extras.get("fx_rates", []):
            if row["rate_date"] == day:
                if (row["base_currency"], row["quote_currency"]) == ("USD", currency):
                    candidates.append((from_e8(row["rate_e8"]), row["provider"], row["source_record_id"], row.get("published_date", day), row.get("source_url"), row.get("payload_sha256")))
                elif (row["base_currency"], row["quote_currency"]) == (currency, "USD"):
                    candidates.append((Decimal(1) / from_e8(row["rate_e8"]), row["provider"], row["source_record_id"], row.get("published_date", day), row.get("source_url"), row.get("payload_sha256")))
        return candidates[0] if candidates and len({c[0] for c in candidates}) == 1 else None
    missing = set()
    for name, report in tuple(reports.items()):
        if name in ("交易回放", "行情状态", "资金事件", "资产调整", "汇率", "导入批次", "数据异常", "指标说明", "账户资产"):
            continue
        indexes = [i for i, column in enumerate(report.columns) if "$" in column or column in ("当前资产", "当前本金", "已清仓收益", "浮动盈亏", "总盈亏", "本年盈亏", "回本缺口", "当前现金", "首月收益", "次月收益", "末月收益")]
        rows = []
        for row in report.rows:
            day = row.metadata.get("valuation_date") or snapshot.scope.as_of.isoformat()
            selected = rate(day)
            values = list(row.values)
            for index in indexes:
                if isinstance(values[index], Decimal):
                    values[index] = values[index] * selected[0] if selected else None
            if not selected and indexes:
                missing.add(day)
            rows.append(replace(row, values=tuple(values), metadata={**row.metadata, "original_currency": "USD", "original_values": row.values,
                        "currency": currency, "fx_date": day, "fx_rate": selected[0] if selected else None,
                        "fx_provider": selected[1] if selected else None, "fx_source_id": selected[2] if selected else None, "fx_published_date": selected[3] if selected else None, "fx_source_url": selected[4] if selected else None, "fx_payload_sha256": selected[5] if selected else None}))
        reports[name] = replace(report, columns=tuple(c.replace("($)", "(¥)") for c in report.columns), rows=tuple(rows),
                                note=report.note + " 汇率优先源库当日值，否则采用ECB参考值；非发布日沿用此前最近值（最多7天），实际发布日期见行来源。USD 收益按对应估值日折算；不包含汇率损益，不是 CNY 本币回报率。")
    converted = []
    for metric in metrics:
        selected = rate(metric.day)
        value = metric.value if metric.ratio else metric.value * selected[0] if selected and metric.value is not None else None
        if selected is None and not metric.ratio:
            missing.add(metric.day)
        converted.append(replace(metric, value=value, status="MISSING_FX" if value is None else metric.status,
                                 formula=metric.formula + "；USD 结果按对应日期折算，收益率仍为 USD 原币口径"))
    # Aggregates must use the converted detail values, not a separate current FX
    # applied to a lifetime USD sum. In particular a missing historical rate must
    # not disappear behind an available as-of rate on a KPI card.
    daily = reports.get("每日持仓收益")
    monthly = reports.get("每月持仓收益")
    micro = reports.get("每月微操收益")
    if daily and monthly:
        daily_groups = {}
        for row in daily.rows:
            key = row.values[0][:7], row.metadata.get("account_id"), row.metadata.get("instrument_id")
            daily_groups.setdefault(key, []).append(row)
        changed = []
        for row in monthly.rows:
            key = row.values[0], row.metadata.get("account_id"), row.metadata.get("instrument_id")
            items = daily_groups.get(key, [])
            values = list(row.values)
            values[20] = strict_sum(r.values[19] for r in items) if items else None
            changed.append(replace(row, values=tuple(values), metadata={**row.metadata, "translated_profit_basis": "逐日折算收益之和；排除汇率损益"}))
        reports["每月持仓收益"] = replace(monthly, rows=tuple(changed))
    holdings, closed = reports.get("当前持仓"), reports.get("清仓收益")
    replacements = {}
    if holdings and closed:
        replacements["当前总资产"] = strict_sum(r.values[5] for r in holdings.rows)
        replacements["浮动盈亏"] = strict_sum(r.values[10] for r in holdings.rows)
        replacements["已清仓收益"] = strict_sum(r.values[7] for r in closed.rows)
        fp, cp = replacements["浮动盈亏"], replacements["已清仓收益"]
        replacements["全时期总盈亏"] = fp + cp if fp is not None and cp is not None else None
        replacements["回本缺口"] = strict_sum(max(-v, ZERO) if v is not None else None for v in
                                                [r.values[10] for r in holdings.rows] + [r.values[7] for r in closed.rows])
        for report_name, dimension in (("渠道汇总", 0), ("分类汇总", 1)):
            if report_name not in reports:
                continue
            updated = []
            for row in reports[report_name].rows:
                hs = [r for r in holdings.rows if r.values[dimension] == row.values[0]]
                cs = [r for r in closed.rows if r.values[dimension] == row.values[0]]
                values = list(row.values)
                values[1] = strict_sum(r.values[5] for r in hs)
                values[2] = strict_sum([r.values[9] for r in hs] + ([r.values[5] - r.values[6] if r.values[5] is not None and r.values[6] is not None else None for r in cs] if dimension == 0 else []))
                values[4] = ratio(values[1], replacements["当前总资产"])
                values[5], values[6] = strict_sum(r.values[7] for r in cs), strict_sum(r.values[10] for r in hs)
                annual = [r for r in daily.rows if str(r.values[0]).startswith(str(snapshot.scope.as_of.year)) and r.values[dimension + 1] == row.values[0]] if daily else []
                values[7] = strict_sum(r.values[19] for r in annual) if annual else None
                values[8] = values[5] + values[6] if values[5] is not None and values[6] is not None else None
                values[11] = strict_sum(max(-v, ZERO) if v is not None else None for v in [r.values[10] for r in hs] + [r.values[7] for r in cs])
                updated.append(replace(row, values=tuple(values)))
            reports[report_name] = replace(reports[report_name], rows=tuple(updated))
    if daily and daily.rows:
        days = sorted({r.values[0] for r in daily.rows})
        latest = days[-1]
        y, m = map(int, latest[:7].split("-"))
        prev_month = f"{y if m > 1 else y - 1:04}-{m - 1 if m > 1 else 12:02}"
        predicates = {"今日盈亏": lambda d: d == latest, "昨日盈亏": lambda d: len(days) > 1 and d == days[-2],
                      "本月盈亏": lambda d: d.startswith(latest[:7]), "上月盈亏": lambda d: d.startswith(prev_month),
                      "本年盈亏": lambda d: d.startswith(str(y)), "去年盈亏": lambda d: d.startswith(str(y - 1))}
        for name, predicate in predicates.items():
            selected_rows = [r for r in daily.rows if predicate(r.values[0])]
            replacements[name] = strict_sum(r.values[19] for r in selected_rows) if selected_rows else None
        if micro:
            for name, prefix in (("本月微操收益", latest[:7]), ("本年微操收益", str(y))):
                replacements[name] = strict_sum(r.values[11] for r in micro.rows if r.values[0].startswith(prefix))
    originally_defined = {metric.name: metric.value is not None for metric in metrics}
    converted = [replace(metric, value=replacements[metric.name] if originally_defined[metric.name] else None, status="MISSING_FX_OR_SOURCE" if replacements[metric.name] is None or not originally_defined[metric.name] else metric.status)
                 if metric.name in replacements else metric for metric in converted]
    for day in sorted(missing):
        snapshot.issues.append(Issue("DQ011", "WARN", f"缺少 {day} 的唯一 USD/{currency} 汇率；折算值保持缺失。", day))
    return tuple(converted)
