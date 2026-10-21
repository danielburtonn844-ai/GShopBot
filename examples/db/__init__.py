"""数据层包：Database 由各领域 mixin 组合而成。

拆分前是单个 2639 行、102 个方法的 God Class；现在按领域分成
12 个 mixin，每个 100~500 行。对外接口完全不变：
`from examples.models import Database` 与 `db.get_user(...)` 全部照旧可用。

| mixin | 领域 |
|---|---|
| CoreMixin | 连接、事务、建表与迁移 |
| UserMixin | 用户 |
| ProductMixin | 商品 |
| OrderMixin | 订单 |
| RechargeMixin | 充值单与链上防重 |
| AdminMixin | 管理员 |
| ChannelMixin | 频道 |
| SettingMixin | 设置项 |
| PointMixin | 积分流水与操作日志 |
| SupplyMixin | 付费供需 |
| StockMixin | 库存池与到货提醒 |
| GrabMixin | 抢单派单（接单员/佣金/提现/申诉） |

分组的唯一依据是「表」，同一张表的所有读写落在同一个 mixin 里，
跨表事务仍在 `core.atomic()` 里完成，不跨 mixin 建索引假设。
"""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .mixins.admin import AdminMixin
from .mixins.channel import ChannelMixin
from .mixins.core import CoreMixin
from .mixins.grab import GrabMixin
from .mixins.order import OrderMixin
from .mixins.point import PointMixin
from .mixins.product import ProductMixin
from .mixins.recharge import RechargeMixin
from .mixins.setting import SettingMixin
from .mixins.stock import StockMixin
from .mixins.supply import SupplyMixin
from .mixins.user import UserMixin
from .schema import *  # noqa: F401,F403


class Database(CoreMixin, UserMixin, ProductMixin, OrderMixin, RechargeMixin, AdminMixin, ChannelMixin, SettingMixin, PointMixin, SupplyMixin, StockMixin, GrabMixin):
    """SQLite 持久化门面：所有领域方法由 mixin 提供，自身不再定义业务方法。"""


__all__ = ["Database"]
