#!/usr/bin/env python3
"""一次性复制旧项目的数据库和授权配置到Money3；不修改来源、不覆盖已有目标。"""

import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from uuid import uuid4

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))


def copy_new(source, target, *, private=False):
    if not source.is_file() or target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if private:
        target.chmod(0o600)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="明确指定的一次性迁移来源目录")
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    if source == PROJECT_DIR:
        parser.error("来源不能是Money3本身")
    original = source / "数据库/money.sqlite3"
    target = PROJECT_DIR / "数据库/money.sqlite3"
    if not original.is_file():
        raise FileNotFoundError("来源目录缺少数据库/money.sqlite3")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_name(".migration-" + uuid4().hex + ".sqlite3")
        try:
            with sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as old:
                with sqlite3.connect(temporary) as new:
                    old.backup(new)
                    if new.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("迁移副本结构检查失败")
                    tables = [
                        r[0]
                        for r in old.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                        )
                    ]
                    for table in tables:
                        quoted = '"' + table.replace('"', '""') + '"'
                        if (
                            old.execute(f"SELECT count(*) FROM {quoted}").fetchone()
                            != new.execute(f"SELECT count(*) FROM {quoted}").fetchone()
                        ):
                            raise ValueError("迁移副本行数核对失败")
            if target.exists():
                raise FileExistsError("迁移目标已出现，停止覆盖")
            temporary.rename(target)
        finally:
            temporary.unlink(missing_ok=True)
        print("[OK] 数据库一致性副本已迁入Money3，全部业务表行数核对一致。")
    else:
        print("[SKIP] Money3已有本地数据库，未覆盖。")
    copy_new(source / "原始数据/手动维护/交易流水.xlsx", PROJECT_DIR / "交易流水.xlsx")
    copy_new(source / "longbridge_app_key.py", PROJECT_DIR / "longbridge_app_key.py", private=True)
    config_file = source / "schwab_config.json"
    local_config = PROJECT_DIR / "schwab_config.json"
    if config_file.is_file() and not local_config.exists():
        config = json.loads(config_file.read_text())
        original_token = Path(config.get("token_file", "schwab_token.json")).expanduser()
        if not original_token.is_absolute():
            original_token = source / original_token
        copy_new(original_token, PROJECT_DIR / "schwab_token.json", private=True)
        config["token_file"] = "schwab_token.json"
        with local_config.open("x") as stream:
            local_config.chmod(0o600)
            json.dump(config, stream, ensure_ascii=False, indent=2)
    for name in ("交易订单数据bbae.xlsx", "交易订单数据firstrade.csv", "交易订单数据firstrade.xlsx"):
        relative = Path("原始数据/手动维护") / name
        copy_new(source / relative, PROJECT_DIR / relative)
    print("[OK] 本机配置和券商导出文件已按需复制，已有Money3流水及文件未覆盖；未查询API、未执行交易入库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
