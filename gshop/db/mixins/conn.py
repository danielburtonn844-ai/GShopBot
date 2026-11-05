"""连接访问协议：让 12 个 mixin 在静态检查下都能看到 `_get_conn` / `_maybe_conn`。

`Database` 由 12 个领域 mixin 组合而成，`_get_conn()` / `_maybe_conn()` 的
真实实现只在 `CoreMixin` 里。单独对某个 mixin 做类型检查（`mypy
gshop/db/mixins/user.py`）或按文件分析时，检查器解析不到 `CoreMixin`
的方法，就会报 `"UserMixin" has no attribute "_get_conn"`——本项目里
这类误报有 103 条，全部落在同一个根因上。

本模块提供空实现协议：所有 mixin 继承它，声明两个方法的签名。
运行时零副作用（方法体在 `TYPE_CHECKING` 内，不入 `__dict__`，不参与
MRO 的方法解析——真正被调用的仍是 `CoreMixin` 里的实现）。
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING


class ConnAccessMixin:
    """声明 `_get_conn` / `_maybe_conn` 签名，实现由 `CoreMixin` 提供。"""

    if TYPE_CHECKING:

        def _get_conn(self) -> sqlite3.Connection: ...

        @contextmanager
        def _maybe_conn(
            self, conn: sqlite3.Connection | None
        ) -> Iterator[sqlite3.Connection]: ...
