"""Local-only command line for domestic PDF preflight, import and rebuild."""
from argparse import ArgumentParser
from datetime import date
from getpass import GetPassWarning, getpass
import json
from pathlib import Path
import warnings

from portfolio.paths import DOMESTIC_ASSETS_DIR

from .alipay import parse_pdf
from .service import publish, run_calculation
from .storage import backup, operation


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DOMESTIC_ASSETS_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("preview", "import"):
        pdf = commands.add_parser(command)
        pdf.add_argument("--opening", type=Path)
        pdf.add_argument("--transactions", type=Path)
        pdf.add_argument("--snapshot", type=date.fromisoformat, default=date(2021, 11, 11))
        if command == "import":
            pdf.add_argument("--confirm", action="store_true", required=True)
    recalc = commands.add_parser("rebuild")
    recalc.add_argument("--full", action="store_true")
    for command in ("tiantian-preview", "tiantian-import"):
        pdf = commands.add_parser(command)
        pdf.add_argument("files", nargs="+", type=Path)
        if command == "tiantian-import":
            pdf.add_argument("--confirm", action="store_true", required=True)
    nav = commands.add_parser("collect-nav", help="采集历史基金净值到国内原始库")
    nav.add_argument("--fund", action="append", help="可重复指定基金代码")
    nav.add_argument("--attempts", type=int, choices=range(1, 6), default=3)
    nav.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args()
    if args.command == "collect-nav":
        from .nav_collection import run

        try:
            return 2 if run(args.directory, code=args.fund, attempts=args.attempts) else 0
        except ImportError:
            parser.error("请先安装国内净值采集依赖 pip install '.[domestic-nav]'")
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    if args.command.startswith("tiantian-"):
        from .fund_transactions import import_tiantian, preview_tiantian
        from .tiantian import parse_pdf as parse_tiantian

        documents = []
        for path in args.files:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", GetPassWarning)
                    password = getpass(f"{path.name} 的 PDF 密码：")
                try:
                    documents.append(parse_tiantian(path, password))
                finally:
                    del password
            except GetPassWarning:
                parser.error("请在支持密码隐藏输入的终端中运行")
            except (OSError, ValueError) as exc:
                parser.error(f"{path.name}：{exc}")
        print(json.dumps(preview_tiantian(args.directory, documents), ensure_ascii=False), flush=True)
        if args.command == "tiantian-import":
            print(json.dumps(import_tiantian(documents, root=args.directory), ensure_ascii=False))
        return
    if args.command == "rebuild":
        with operation(args.directory) as root:
            backup(root)
            print(json.dumps(run_calculation(root, full=args.full), ensure_ascii=False))
        return
    documents = []
    if args.opening:
        documents.append(parse_pdf(args.opening, "opening_asset", args.snapshot))
    if args.transactions:
        documents.append(parse_pdf(args.transactions, "transaction"))
    if not documents:
        parser.error("至少指定一个来源 PDF")
    print(json.dumps([doc.summary() for doc in documents], ensure_ascii=False))
    if args.command == "import":
        print(json.dumps(publish(documents, root=args.directory), ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
