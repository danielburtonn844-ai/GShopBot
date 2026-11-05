"""版本号唯一来源（v2.7.1，审计 P2-1）。

之前三处各写各的：pyproject=2.6.1 / runtime=2.6.7 / main.py 注释=2.6.0 /
README=2.6.7，发版时改漏一处就会让「启动日志版本」与「安装包版本」不一致，
排查线上问题时先被版本号误导。

现在：仓库根 `VERSION` 文件是唯一来源，本模块负责读它，
- gshop/runtime.py 从本模块取 VERSION（启动日志用）
- pyproject.toml 用 setuptools dynamic version 从同一个 VERSION 文件读（打包用）
- test_version_consistency.py 断言 CHANGELOG / README / main.py 注释全部一致
"""
from __future__ import annotations

import os

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VERSION_FILE = os.path.join(_BASE_DIR, "VERSION")


def _read_version() -> str:
    """读 VERSION 文件；读不到时回退到 unknown，不让导入直接炸。"""
    try:
        with open(_VERSION_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "unknown"


VERSION = _read_version()
__version__ = VERSION

__all__ = ["VERSION", "__version__"]
