"""Local-only command line for Alipay PDF preflight, import and rebuild."""
from argparse import ArgumentParser
from datetime import date
import json
from pathlib import Path

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
    args = parser.parse_args()
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
    main()
