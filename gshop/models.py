"""数据层入口（显式导出，审计 P2-2）。

历史上全项目都写 `from .models import Database, User, ...`，所以这个模块
保留了，但不再用 `import *`：每一个对外的名字都显式列出，`__all__` 与
真实定义严格一一对应，`test_p2_fixes.py` 里有断言守着。

新代码直取子包：`from .db import Database, User`。
"""
from __future__ import annotations

from .db import Database
from .db.base import (
    APPEAL_STATUS,
    COMMISSION_STATUS,
    GRAB_STATUS,
    GRABBER_STATUS,
    LOCAL_TZ,
    ORDER_STATUS,
    PRIORITY,
    PRODUCT_KINDS,
    PRODUCT_STATUS,
    TAGS,
    WITHDRAW_STATUS,
    escape_html,
    fmt_time,
    format_duration,
    format_usdt,
    gen_product_id,
    gen_recharge_id,
    gen_supply_id,
    gen_withdraw_id,
    now_iso,
    now_str,
    safe_float,
)
from .db.schema import (
    STOCK_STATUS,
    STOCK_TYPES,
    SUPPLY_CHANNELS,
    Admin,
    AuditLog,
    Channel,
    GrabAppeal,
    Grabber,
    GrabCommission,
    Order,
    PointRecord,
    Product,
    Recharge,
    StockItem,
    SupplyPost,
    User,
    Withdrawal,
)

__all__ = [
    "Database",
    "LOCAL_TZ", "ORDER_STATUS", "PRODUCT_STATUS", "PRODUCT_KINDS",
    "TAGS", "PRIORITY", "GRABBER_STATUS", "GRAB_STATUS",
    "COMMISSION_STATUS", "WITHDRAW_STATUS", "APPEAL_STATUS",
    "now_iso", "now_str", "fmt_time", "gen_recharge_id", "gen_product_id",
    "gen_supply_id", "gen_withdraw_id", "format_usdt", "format_duration",
    "escape_html", "safe_float",
    "User", "Product", "Order", "Recharge", "Admin", "Channel",
    "PointRecord", "SupplyPost", "StockItem", "AuditLog",
    "Grabber", "GrabCommission", "Withdrawal", "GrabAppeal",
    "SUPPLY_CHANNELS", "STOCK_TYPES", "STOCK_STATUS",
]
