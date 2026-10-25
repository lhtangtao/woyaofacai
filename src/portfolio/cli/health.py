import argparse
import json
from pathlib import Path

from portfolio.db.connection import SourceError, readonly_connection
from portfolio.db.schema_validator import validate_schema


def inspect_database(path, *, integrity=False):
    with readonly_connection(path) as connection:
        result = validate_schema(connection).to_dict()
        result["source_mode"] = "READ_ONLY"
        result["source_bytes"] = Path(path).stat().st_size
        result["counts"] = {}
        if result["compatible"]:
            # 标识符仅来自已经检查的 DDL 白名单。
            result["counts"] = {table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                                for table in result["tables"]}
        if integrity:
            result["integrity"] = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            result["foreign_key_issues"] = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="SQLite 只读结构检查，不执行迁移或修复。")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--integrity", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = inspect_database(args.db, integrity=args.integrity)
    except (SourceError, OSError) as exc:
        print(json.dumps({"compatible": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["compatible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
