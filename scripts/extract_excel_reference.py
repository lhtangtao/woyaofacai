import argparse
from pathlib import Path

from portfolio.excel_reference import write_snapshot


def main():
    parser = argparse.ArgumentParser(description="只读抽取 Excel 公式、原始缓存值和全部图表系列。")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = write_snapshot(args.input, args.output)
    print(f"已读取 {len(snapshot['sheets'])} 张 Sheet、{len(snapshot['charts'])} 张图表；原文件未重算或改写。")


if __name__ == "__main__":
    main()
