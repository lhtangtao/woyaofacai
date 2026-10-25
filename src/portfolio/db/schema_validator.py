import sqlite3
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib.resources import files


def schema_sql() -> str:
    return files("portfolio").joinpath("resources/schema.sql").read_text(encoding="utf-8")


def inspect_schema(connection):
    result = {}
    for obj in connection.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
    ).fetchall():
        name = obj["name"]
        # 名称来自 SQLite 元数据，双引号转义；不接受用户输入 SQL 标识符。
        quoted = '"' + name.replace('"', '""') + '"'
        columns = [dict(row) for row in connection.execute(f"PRAGMA table_info({quoted})")]
        result[name] = {"type": obj["type"], "sql": obj["sql"], "columns": columns}
    return result


@lru_cache(maxsize=1)
def expected_schema():
    # DDL 只在内存中执行，绝不对用户选中的源库执行。
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(schema_sql())
        return inspect_schema(connection)
    finally:
        connection.close()


@dataclass(frozen=True)
class SchemaReport:
    compatible: bool
    tables: tuple[str, ...]
    views: tuple[str, ...]
    issues: tuple[str, ...]

    def to_dict(self):
        return asdict(self)


def validate_schema(connection) -> SchemaReport:
    actual = inspect_schema(connection)
    issues = []
    for name, expected in expected_schema().items():
        found = actual.get(name)
        if found is None or found["type"] != expected["type"]:
            issues.append(f"缺少{expected['type']}：{name}")
            continue
        columns = {row["name"]: row for row in found["columns"]}
        for wanted in expected["columns"]:
            column = columns.get(wanted["name"])
            if column is None:
                issues.append(f"缺少字段：{name}.{wanted['name']}")
            elif any(column[key] != wanted[key] for key in ("type", "notnull", "pk")):
                issues.append(f"字段类型/约束不兼容：{name}.{wanted['name']}")
    return SchemaReport(
        not issues,
        tuple(sorted(k for k, v in actual.items() if v["type"] == "table")),
        tuple(sorted(k for k, v in actual.items() if v["type"] == "view")),
        tuple(issues),
    )
