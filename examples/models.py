"""数据层入口（兼容层）。

历史原因，全项目都写 `from .models import Database, User, ...`。
v2.6.4 起实现拆到 examples/db/ 包（base / schema / mixins），
本文件只做转导出，保持所有调用点零改动。

新代码可以直接写 `from .db import Database, User`。
"""
from __future__ import annotations

from .db import *  # noqa: F401,F403
from .db.base import *  # noqa: F401,F403
from .db.schema import *  # noqa: F401,F403
