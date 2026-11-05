"""状态 / 状态机 / 回调命名空间常量（审计 P2-7）。

之前状态字串散在 20+ 个文件里用裸字面量比较，拼错一个字母（pending / pendig）
不会报错，只会静默走到 else 分支。集中到这里后，typo 会变成 AttributeError。

兼顾历史字符串：这些常量的值全部保持与原字面量一样，DB 里已有数据不需要迁移。
="""
from __future__ import annotations


class OrderStatus:
    """订单状态。PENDING/PROCESSING 是活跃态，其余为终态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"

    #: 尚未终结，可以被管理员回单 / 接单员抢
    ACTIVE = (PENDING, PROCESSING)
    #: 已终结，拒绝任何状态迁移
    TERMINAL = (COMPLETED, CANCELLED, REFUNDED)


class GrabStatus:
    """抢单状态。空串表示未进派单池。"""

    NONE = ""
    WAITING = "waiting"
    GRABBED = "grabbed"
    DONE = "done"

    ACTIVE = (WAITING, GRABBED)


class ProductStatus:
    """商品状态。"""

    ON = "on"
    OFF = "off"
    SOLD_OUT = "sold_out"
    HIDDEN = "hidden"


class ProductKind:
    """商品类型。report = 报单（人工服务，不占库存）。"""

    PRODUCT = "product"
    REPORT = "report"


class RechargeStatus:
    """充值单状态。expired 是冷钱包超时待核对态。"""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"

    MANUAL_OK = (PENDING, FAILED, EXPIRED)


class CommissionStatus:
    """佣金状态：冻结 → 已释放（可提现）/ 已作废。"""

    FROZEN = "frozen"
    RELEASED = "released"
    VOIDED = "voided"


class GrabberStatus:
    """接单员状态。"""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class Cb:
    """按钮回调命名空间（分发给 cb_user / cb_supply / cb_admin / cb_grab）。

    分发是「顺序短路」，第一个返回 True 的 dispatcher 命中。
    新增按钮时把前缀加到对应模块，不要在多处重复字面量。
    """

    # cb_user.py
    MENU = "m:"
    DETAIL = "d:"
    BUY = "buy:"
    CONFIRM_BUY = "cbuy:"
    ORDER = "od:"
    URGE = "urge:"
    REDELIVER = "redeliver:"
    RESUBMIT = "rsub:"
    RECHARGE = "rc:"
    RECHARGE_PAY = "rcpay:"
    RECHARGE_CHECK = "rcchk:"

    # cb_supply.py
    SUPPLY = "sup:"

    # cb_admin.py
    ADMIN = "a:"
    PRODUCT_ADD = "padd_"

    # cb_grab.py
    GRAB = "gb:"


#: 处于输入状态时，这些前缀属于「流程内按钮」，保留 state 与 payload
STATE_KEEP_PREFIXES = ("padd_kind:", "padd_mode:", "a:bcsend", "sup:", "a:stkdone:")

__all__ = [
    "Cb",
    "CommissionStatus",
    "GrabStatus",
    "GrabberStatus",
    "OrderStatus",
    "ProductKind",
    "ProductStatus",
    "RechargeStatus",
    "STATE_KEEP_PREFIXES",
]
