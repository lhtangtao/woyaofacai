import argparse
from pathlib import Path

from portfolio.demo import build_demo


def main():
    parser = argparse.ArgumentParser(description="生成明确标记的合成示例库；拒绝覆盖任何已有文件。")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"合成示例库已生成：{build_demo(args.output)}。全部为合成数据。")


if __name__ == "__main__":
    main()
