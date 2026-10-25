"""M0 可重复生成结构、Excel 口径和图表引用清单，不读取/写入真实账本内容。"""
import json
from pathlib import Path

from portfolio.db.schema_validator import expected_schema
from portfolio.excel_reference import extract_reference

ROOT = Path(__file__).resolve().parents[1]


def main():
    snapshot = extract_reference(ROOT / "references/个人投资总览.xlsx")
    sheets = {sheet["name"]: sheet for sheet in snapshot["sheets"]}
    lines = ["# M0 来源清单", "", "原始 Excel 只读扫描所有已保存单元格、公式和 chart XML；未重算来源。", "",
             "| Sheet | 范围 | 有内容单元格 | 公式 | 缓存错误 |", "|---|---|---:|---:|---:|"]
    for sheet in snapshot["sheets"]:
        lines.append(f"| {sheet['name']} | {sheet['dimension']} | {sheet['cell_count']} | {sheet['formula_count']} | {len(sheet['cached_errors'])} |")
    lines += ["", "## 全部图表与原始系列引用", "", "只盘点原始 series，不根据图表名称猜测维度。"]
    for index, chart in enumerate(snapshot["charts"], 1):
        lines.extend(["", f"### {index:02d} {chart['title']}", ""])
        lines.extend(f"- 系列 {i}: `{'`、`'.join(series['references'])}`；缓存点数 {len(series['cached_values'])}。"
                     for i, series in enumerate(chart["series"], 1))
    lines += ["", "## 13 Sheet 表头", ""]
    for name, sheet in sheets.items():
        if name not in {"总览", "图表数据", "图表趋势数据"}:
            lines.extend([f"### {name}", "", "、".join(str(v) for v in sheet["headers"]), ""])
    (ROOT / "docs/SOURCE_INVENTORY.md").write_text("\n".join(lines) + "\n")

    lines = ["# 数据库字段映射与账本边界", "", "由 references/sqlite_schema.sql 在内存中解析；真实源库不执行 DDL。", "",
             "## 运行时数据流", "", "Money3本地券商适配 → 本目录交易流水.xlsx → 本目录数据库；展示层只读SQLite。",
             "Excel 是 money3 的迁移参考，不会在 money3 启动时充当运行时账本。", "",
             "人员页读取 person_operations，券商页读取 broker_trades，两个账本不合并求和。",
             "ACTIVE/ACCOUNT_LEDGER 为有效券商范围；撤销/参考记录只在显式审计模式展示。",
             "人员默认只显示启用人员，可启用‘含停用人员’；历史对账必须包含停用人员。", "",
             "## 16 列交易页映射", "", "| 显示列 | 人员账本 | 券商账本 |", "|---|---|---|"]
    mapping = [
        ("交易时间", "o.book_time", "t.book_time"), ("渠道", "a.broker_code", "a.broker_code"),
        ("分类", "i.category 原值", "i.category 原值"), ("Ticker", "i.symbol + i.market", "i.symbol + i.market"),
        ("方向", "legacy_direction / side", "side"), ("成交数量", "quantity_e8", "quantity_e8"),
        ("成交均价($)", "price_e8；NULL 保留缺失", "price_e8；NULL 保留缺失"),
        ("成交金额($)", "amount_e8", "calculation_amount_e8"),
        ("微操标记", "M5 待实现，不伪造", "M5 待实现，不伪造"),
        ("后持仓数量 / 后累计净投入 / 后均价", "M2 待回放", "M2 待回放"),
        ("交易类型", "operation_kind", "MARKET_TRADE"), ("划转对象", "counterparty_person_id / legacy_counterparty", "不适用"),
        ("订单ID", "legacy_order_id 文本", "broker_order_id 文本"),
        ("数据来源", "raw_records → import_batches", "raw_records → import_batches"),
    ]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in mapping]
    for name, obj in sorted(expected_schema().items()):
        lines += ["", f"## {name}（{obj['type']}）", "", "| 字段 | 类型 | NOT NULL | 主键 |", "|---|---|---|---|"]
        lines += [f"| {c['name']} | {c['type'] or '派生列'} | {c['notnull']} | {c['pk']} |" for c in obj["columns"]]
    (ROOT / "docs/DB_MAPPING.md").write_text("\n".join(lines) + "\n")

    lines = ["# Excel 兼容指标字典", "", "依据原 Excel 两张逻辑说明页提取；保留来源坐标，未声称实现计算。", "",
             "M1 仅显示账本原始列；KPI、回放、收益率、微操按 M2–M5 实施。",
             "EXCEL_COMPAT 与 ACCOUNTING 分开；M1 禁止以缺失当 0，禁止把现金并入股票市值。", ""]
    cells = sheets["总览取值逻辑"]["cells"]
    for row in range(5, 23):
        def get(col, row=row):
            return cells.get(f"{col}{row}", {}).get("value", "")
        lines += [f"## {row - 4:02d} {get('A')}", "", f"- 公式：{get('D')}",
                  f"- 来源：{get('C')}", f"- 限制：{get('E')}", f"- 证据：总览取值逻辑!A{row}:E{row}", ""]
    lines += ["## 其余报表口径（原文提取）", ""]
    for name, start, columns in (("取值逻辑", 2, "BCDEFGH"), ("总览取值逻辑", 27, "ABCDE")):
        cells = sheets[name]["cells"]
        maximum = max(int("".join(c for c in key if c.isdigit())) for key in cells)
        for row in range(start, maximum + 1):
            values = [cells.get(f"{col}{row}", {}).get("value") for col in columns]
            if any(values):
                lines += [f"- {name}!{columns[0]}{row}:{columns[-1]}{row}：" + "；".join(str(v) for v in values if v)]
    (ROOT / "docs/METRIC_DICTIONARY.md").write_text("\n".join(lines) + "\n")
    summary = {"sheet_count": len(sheets), "chart_count": len(snapshot["charts"]),
               "table_count": sum(x["type"] == "table" for x in expected_schema().values()),
               "view_count": sum(x["type"] == "view" for x in expected_schema().values()), "kpi_count": 18}
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
