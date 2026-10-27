"""Money3本地路径；不探测或回退到同级项目。"""

import os
from pathlib import Path

PROJECT_DIR = (
    Path(os.environ.get("MONEY3_HOME") or Path(__file__).resolve().parents[2]).expanduser().resolve()
)
DATABASE_PATH = PROJECT_DIR / "数据库" / "money.sqlite3"
API_CONFIG_PATH = PROJECT_DIR / "api_config.json"
CREDENTIALS_PATH = API_CONFIG_PATH if API_CONFIG_PATH.is_file() else PROJECT_DIR / "longbridge_app_key.py"
WORKBOOK_PATH = PROJECT_DIR / "交易流水.xlsx"


def derived_path(source=DATABASE_PATH):
    source = Path(source).expanduser().resolve()
    return source.with_name(source.stem + '_derived.sqlite3')


def quote_cache_path():
    # Matches the existing desktop cache; no QApplication or sibling project needed.
    import sys
    if sys.platform == 'darwin':
        root = Path.home() / 'Library' / 'Application Support'
    else:
        root = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    return root / 'MoneyPortfolio' / 'Money Portfolio' / 'live_quotes.sqlite3'
