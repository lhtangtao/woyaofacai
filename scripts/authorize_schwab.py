#!/usr/bin/env python3
"""仅在用户明确运行时，通过浏览器更新Money3本地嘉信授权。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main():
    from portfolio.paths import PROJECT_DIR
    from portfolio.sync.schwab_api import load_config, load_schwab_auth, token_path

    config = load_config(require_enabled=False)
    if config is None:
        raise ValueError("请先在Money3目录准备api_config.json或schwab_config.json")
    target = token_path(config).resolve()
    if not target.is_relative_to(PROJECT_DIR):
        raise ValueError("嘉信token_file必须位于Money3目录内")
    target.parent.mkdir(parents=True, exist_ok=True)
    load_schwab_auth().client_from_manual_flow(
        config["app_key"], config["app_secret"], config["callback_url"], str(target)
    )
    target.chmod(0o600)
    print("[OK] 嘉信授权已更新到Money3本地文件。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"[ERROR] 嘉信授权未完成（{type(exc).__name__}），请核对本地配置、回调地址及网络。",
            file=sys.stderr,
        )
        raise SystemExit(1)
