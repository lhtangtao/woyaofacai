import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from portfolio.demo import build_demo


@pytest.fixture
def demo_path(tmp_path):
    return build_demo(tmp_path / "中文路径 带空格 # ?" / "合成.sqlite3")
