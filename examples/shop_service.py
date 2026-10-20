"""业务逻辑层：充值、购买、订单处理、通知。"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from decimal import Decimal
from typing import Any

import os

from telegram.error import RetryAfter

from .models import (
    LOCAL_TZ,
    Admin,
    Channel,
    Database,
    Order,
    Product,
    Recharge,
    StockItem,
    User,
    escape_html,
    fmt_time,
    gen_product_id,
    gen_recharge_id,
    now_iso,
    now_str,
)
from .okpay import OkPay
from .kkpay import KKPay
from .chain_watch import (
    ChainTx,
    dec_to_str,
    fetch_ton_usdt_incoming,
    fetch_tron_usdt_incoming,
)
from .richtext import (
    ICON_CHECK,
    ICON_DIAMOND,
    ICON_MAIL,
    ICON_ZAP,
    send_html,
)

# 全部支付渠道（充值页按 gateway_available 过滤后给出选择按钮）
PAY_GATEWAYS = ("okpay", "kkpay", "trc20", "ton")
GATEWAY_NAMES = {"okpay": "OkPay", "kkpay": "KKPay", "trc20": "冷钱包·TRON", "ton": "冷钱包·TON"}
LINK_GATEWAYS = ("okpay", "kkpay")   # 走支付平台链接的渠道
CHAIN_GATEWAYS = ("trc20", "ton")    # 走冷钱包链上直转的渠道（v2.5.0）

# 下单失败原因码 → 给用户看的中文提示
BUY_FAIL_REASONS = {
    "not_found": "商品不存在或已下架",
    "sold_out": "已售罄",
    "limit": "已达限购数量",
    "banned": "账号已被限制，无法购买",
    "points": "积分不足",
}

logger = logging.getLogger(__name__)


class ShopService:
    def __init__(self, db: Database, okpay: Any = None) -> None:
        self.db = db
        self.okpay = okpay
        self.okpay_id = os.getenv("OKPAY_ID", "")
        self.okpay_token = os.getenv("OKPAY_TOKEN", "")
        self.kkpay_id = os.getenv("KKPAY_ID", "")
        self.kkpay_token = os.getenv("KKPAY_TOKEN", "")
        self._bot = None
        self._okpay: OkPay | None = None  # 懒加载并复用，避免每次请求新建连接
        self._kkpay: KKPay | None = None

    def _get_okpay(self) -> OkPay:
        if self._okpay is None:
            self._okpay = OkPay(self.okpay_id, self.okpay_token)
        return self._okpay

    def _get_kkpay(self) -> KKPay:
        if not self.kkpay_id or not self.kkpay_token:
            raise RuntimeError("KKPay 未配置：请在 .env 中填写 KKPAY_ID 与 KKPAY_TOKEN")
        if self._kkpay is None:
            self._kkpay = KKPay(self.kkpay_id, self.kkpay_token)
        return self._kkpay

    def _get_gateway(self, gateway: str):
        """按渠道名取支付客户端。OkPay/KKPay 接口口径一致（create_payment/check_payment/is_paid）。"""
        if gateway == "kkpay":
            return self._get_kkpay()
        return self._get_okpay()

    def gateway_available(self, gateway: str) -> bool:
        """渠道是否可用 = 后台开关打开 + 该渠道所需配置齐全（充值页只展示可用渠道）。"""
        settings = self.db.get_settings()
        if gateway == "kkpay":
            return bool(settings.get("pay_kkpay_enabled", True)) and bool(self.kkpay_id and self.kkpay_token)
        if gateway == "okpay":
            return bool(settings.get("pay_okpay_enabled", True)) and bool(self.okpay_id and self.okpay_token)
        if gateway == "trc20":
            return bool(settings.get("pay_tron_enabled", False)) and bool(settings.get("tron_wallet_address", ""))
        if gateway == "ton":
            return bool(settings.get("pay_ton_enabled", False)) and bool(settings.get("ton_wallet_address", ""))
        return False

    def validate_recharge_amount(self, amount: float) -> str | None:
        """充值金额限额校验（安全兜底：所有渠道统一下单前必须过这里）。
        返回 None 表示合法，否则返回给用户看的中文错误提示。"""
        settings = self.db.get_settings()
        try:
            min_v = float(settings.get("min_recharge_usdt", 1) or 1)
        except (TypeError, ValueError):
            min_v = 1.0
        try:
            max_v = float(settings.get("max_recharge_usdt", 1000) or 1000)
        except (TypeError, ValueError):
            max_v = 1000.0
        if amount < min_v:
            return f"❌ 单笔最低充值 <code>{min_v:g}</code> USDT"
        if max_v > 0 and amount > max_v:
            return f"❌ 单笔最高充值 <code>{max_v:g}</code> USDT（如需大额请联系客服）"
        return None

    def set_bot(self, bot: Any) -> None:
        self._bot = bot

    def add_admin(self, user_id: int, name: str = "") -> None:
        from .models import Admin
        admin = Admin(
            user_id=user_id,
            name=name,
            permissions=["all"],
            added_by=user_id,
            added_at=now_iso(),
        )
        self.db.save_admin(admin)

    # ---------- 频道检查 ----------

    async def check_user_channels(self, user_id: int) -> tuple[bool, list[Channel]]:
        """检查用户是否关注了所有强制频道。
        返回 (是否全部关注, 未关注的频道列表)。
        """
        settings = self.db.get_settings()
        if not settings.get("force_channels", False):
            return True, []

        channels = self.db.list_channels()
        if not channels:
            return True, []

        if not self._bot:
            return True, []

        missing = []
        for channel in channels:
            try:
                member = await self._bot.get_chat_member(chat_id=channel.chat_id, user_id=user_id)
                if member.status in ("left", "kicked"):
                    missing.append(channel)
            except Exception as exc:
                logger.warning("检查用户 %s 是否关注频道 %s 失败: %s", user_id, channel.chat_id, exc)
                # 如果机器人不是频道管理员，无法检查，暂时跳过
                pass

        return len(missing) == 0, missing

    # ---------- 用户 ----------

    def get_or_create_user(self, user_id: int, username: str = "", first_name: str = "", last_name: str = "") -> User:
        user = self.db.get_user(user_id)
        if not user:
            user = User(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                created_at=now_iso(),
                last_active=now_iso(),
            )
            self.db.save_user(user)
        else:
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.last_active = now_iso()
            self.db.save_user(user)
        return user

    def update_user_points(self, user_id: int, points: int, operator_id: int = 0, reason: str = "") -> bool:
        """管理员调整积分（加/扣）。变动与流水记录在同一事务提交。"""
        with self.db.atomic() as conn:
            user = self.db.get_user(user_id, conn=conn)
            if not user:
                return False
            user.points += points
            user.last_active = now_iso()
            self.db.save_user(user, conn=conn)
            self.db.add_point_record(
                user_id, points, user.points,
                reason or ("管理员加分" if points >= 0 else "管理员减分"),
                operator_id=operator_id, conn=conn,
            )
        return True

    # ---------- 商品 ----------

    def create_product(self, data: dict[str, Any]) -> Product:
        product = Product(
            id=data.get("id") or gen_product_id(),
            name=data.get("name", ""),
            description=data.get("description", ""),
            points=data.get("points", 0),
            kind=data.get("kind", "product"),
            stock=data.get("stock", -1),
            limit_per_user=data.get("limit_per_user", 0),
            status=data.get("status", "on"),
            is_top=data.get("is_top", False),
            tags=data.get("tags", []),
            auto_delivery=data.get("auto_delivery", ""),
            photo_file_id=data.get("photo_file_id", ""),
            created_at=now_iso(),
            category=data.get("category", ""),
            delivery_mode=data.get("delivery_mode", "fixed"),
        )
        self.db.save_product(product)
        return product

    def update_product(self, product_id: str, **kwargs: Any) -> Product | None:
        product = self.db.get_product(product_id)
        if not product:
            return None
        for key, value in kwargs.items():
            if hasattr(product, key):
                setattr(product, key, value)
        self.db.save_product(product)
        return product

    def delete_product(self, product_id: str) -> bool:
        product = self.db.get_product(product_id)
        if not product:
            return False
        self.db.delete_product(product_id)
        return True

    # ---------- 订单 ----------

    @staticmethod
    def _is_pool(product: Product | None) -> bool:
        """是否库存池发货模式（v2.4.0）。老数据没有该字段，默认 fixed。"""
        return bool(product) and getattr(product, "delivery_mode", "fixed") == "pool"

    def _pool_available(self, product_id: str, conn=None) -> int:
        """池模式现货件数（事务内/外都可用）。"""
        if conn is not None:
            row = conn.execute(
                "SELECT COUNT(*) FROM stock_items WHERE product_id = ? AND status = 'available'",
                (product_id,),
            ).fetchone()
            return int(row[0])
        return self.db.count_stock_items(product_id, "available")

    def _validate_purchase(self, user: User | None, product: Product | None, conn=None) -> str | None:
        """购买前置校验。返回 None 表示可购买，否则返回原因码（见 BUY_FAIL_REASONS）。"""
        if not product or product.status == "off":
            return "not_found"
        if product.status == "sold_out":
            return "sold_out"  # 售罄要如实告知，不能报「不存在」误导用户
        if product.status != "on":
            return "not_found"
        if self._is_pool(product):
            # 池模式：现货数以库存池为准（product.stock 只是镜像）
            if self._pool_available(product.id, conn=conn) <= 0:
                return "sold_out"
        elif product.stock == 0:
            return "sold_out"
        if not user:
            return "not_found"
        if user.is_banned:
            return "banned"
        if product.limit_per_user > 0:
            count = self.db.count_user_product_orders(user.user_id, product.id, conn=conn)
            if count >= product.limit_per_user:
                return "limit"
        if user.points < product.points:
            return "points"
        return None

    def check_purchase(self, user_id: int, product: Product | None) -> str | None:
        """只读校验（用于报单类先校验再进入提交信息步骤）。返回 None 或原因码。"""
        user = self.db.get_user(user_id)
        return self._validate_purchase(user, product)

    def create_order(self, user_id: int, product_id: str, note: str = "") -> tuple[Order | None, str | None]:
        """下单。整个扣积分/减库存/建单/记流水在一个事务里原子提交。

        返回 (订单, None) 成功；(None, 原因码) 失败，原因码见 BUY_FAIL_REASONS。
        note: 报单类用户提交的所需信息摘要，会写入订单并展示给管理员。
        """
        with self.db.atomic() as conn:
            product = self.db.get_product(product_id, conn=conn)
            user = self.db.get_user(user_id, conn=conn)
            reason = self._validate_purchase(user, product, conn=conn)
            if reason:
                return None, reason

            # 先生成订单号；池模式在同一事务内原子取货——取不到说明恰好售罄，
            # 此时还没写任何数据，直接返回，积分分文不动（不超卖、不误扣）。
            order_id = self._gen_daily_order_id(conn)
            stock_item: StockItem | None = None
            is_pool = self._is_pool(product)
            if is_pool and product.kind == "product":
                stock_item = self.db.take_stock_item(product.id, order_id, user_id, conn)
                if not stock_item:
                    return None, "sold_out"

            # 扣积分 + 记流水
            user.points -= product.points
            user.total_spent += product.points
            user.last_active = now_iso()
            self.db.save_user(user, conn=conn)

            # 减库存，扣到 0 自动售罄；池模式同步镜像现货数
            if is_pool:
                left = self._pool_available(product.id, conn=conn)
                product.stock = left
                if left == 0:
                    product.status = "sold_out"
                self.db.save_product(product, conn=conn)
            elif product.stock > 0:
                product.stock -= 1
                if product.stock == 0:
                    product.status = "sold_out"
                self.db.save_product(product, conn=conn)

            # 创建订单
            order = Order(
                id=order_id,
                user_id=user_id,
                username=user.username,
                product_id=product.id,
                product_name=product.name,
                product_kind=product.kind,
                points=product.points,
                status="completed" if product.kind == "product" else "pending",
                note=note,
                created_at=now_iso(),
                completed_at=now_iso() if product.kind == "product" else "",
                stock_item_id=stock_item.id if stock_item else 0,
            )
            # 发货内容：固定模式写死的内容；池模式文字件直接写进订单（媒体件由机器人私信送达）
            if product.kind == "product":
                if stock_item is not None:
                    if stock_item.content_type == "text":
                        order.reply_content = stock_item.content_text
                    else:
                        order.reply_content = f"库存池专属内容 编号 #{stock_item.id}（已私信送达）"
                elif product.auto_delivery:
                    order.reply_content = product.auto_delivery
            self.db.save_order(order, conn=conn)

            self.db.add_point_record(
                user_id, -product.points, user.points,
                f"购买 {product.name}", ref=order.id, conn=conn,
            )
            return order, None

    def _gen_daily_order_id(self, conn) -> str:
        """生成 YYYYMMDD-NNN 格式订单号（日期与全站时区一致）。必须在事务内调用。"""
        from datetime import datetime
        today = datetime.now(LOCAL_TZ).strftime("%Y%m%d")
        row = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE id LIKE ?",
            (f"{today}-%",),
        ).fetchone()
        seq = (row[0] or 0) + 1
        return f"{today}-{seq:03d}"

    def get_order(self, order_id: str) -> Order | None:
        return self.db.get_order(order_id)

    def complete_order(self, order_id: str, reply_content: str, admin_id: int) -> Order | None:
        order = self.db.get_order(order_id)
        if not order or order.status not in ("pending", "processing"):
            return None  # 已终态的订单拒绝再次回单，防止覆盖
        order.status = "completed"
        order.reply_content = reply_content
        order.reply_admin_id = admin_id
        order.reply_at = now_iso()
        order.completed_at = now_iso()
        self.db.save_order(order)
        return order

    def refund_order(self, order_id: str, admin_id: int) -> Order | None:
        """退款关闭。退积分、恢复库存、改状态、记流水在一个事务里原子提交。"""
        with self.db.atomic() as conn:
            order = self.db.get_order(order_id, conn=conn)
            if not order or order.status == "refunded":
                return None

            # 退积分 + 记流水
            user = self.db.get_user(order.user_id, conn=conn)
            if user:
                user.points += order.points
                user.total_spent -= order.points
                self.db.save_user(user, conn=conn)
                self.db.add_point_record(
                    order.user_id, order.points, user.points,
                    f"退款 订单 {order.id}", operator_id=admin_id, ref=order.id, conn=conn,
                )

            # 恢复库存，售罄的自动回到销售中
            product = self.db.get_product(order.product_id, conn=conn)
            if product and self._is_pool(product):
                # 池模式：本单那件货退回池中重新可售，镜像现货数同步刷新
                if order.stock_item_id:
                    self.db.release_stock_item(order.stock_item_id, conn=conn)
                left = self._pool_available(product.id, conn=conn)
                product.stock = left
                if product.status == "sold_out" and left > 0:
                    product.status = "on"
                self.db.save_product(product, conn=conn)
            elif product and product.stock >= 0:
                product.stock += 1
                if product.status == "sold_out":
                    product.status = "on"
                self.db.save_product(product, conn=conn)

            order.status = "refunded"
            order.reply_admin_id = admin_id
            order.reply_at = now_iso()
            self.db.save_order(order, conn=conn)
            return order

    # ---------- 充值 ----------

    def create_recharge(self, user_id: int, amount: float) -> Recharge | None:
        # 安全兜底：限额校验在这里做死，任何渠道/入口都绕不过
        if self.validate_recharge_amount(amount):
            logger.warning("拒绝越限充值单：user=%s amount=%s", user_id, amount)
            return None
        settings = self.db.get_settings()
        rate = settings.get("points_rate", 10)
        points = int(amount * rate)  # 规格：向下取整

        user = self.db.get_user(user_id)
        recharge = Recharge(
            id=gen_recharge_id(),
            user_id=user_id,
            username=user.username if user else "",
            amount=amount,
            points=points,
            status="pending",
            created_at=now_iso(),
        )
        self.db.save_recharge(recharge)
        return recharge

    async def create_recharge_with_paylink(self, user_id: int, amount: float, gateway: str = "okpay") -> tuple[Recharge | None, str | None]:
        """创建充值单并调用所选支付渠道（OkPay/KKPay）的 payLink 生成支付链接。

        金额按官方要求原样字符串提交（如 "10"、"4.35"），不重新格式化。
        两个渠道的 create_payment 返回口径一致（pay_url + order_id），
        平台交易号统一存进 recharge.okpay_order_id（字段名保持历史兼容）。
        """
        if gateway not in LINK_GATEWAYS:
            gateway = "okpay"  # 冷钱包渠道走 create_chain_recharge，不进支付平台
        if not self.gateway_available(gateway):
            return None, None  # 渠道被后台关闭或未配置，拒绝下单
        recharge = self.create_recharge(user_id, amount)
        if not recharge:
            return None, None

        try:
            client = self._get_gateway(gateway)
            data = await client.create_payment(recharge.id, f"{amount:g}")
            pay_url = data.get("pay_url", "")
            recharge.pay_url = pay_url
            recharge.okpay_order_id = data.get("order_id", "")
            recharge.gateway = gateway
            self.db.save_recharge(recharge)
            return recharge, pay_url
        except Exception as exc:
            logger.error("创建支付链接失败(%s): %s", gateway, exc)
            recharge.status = "failed"
            recharge.gateway = gateway
            self.db.save_recharge(recharge)
            return recharge, None

    async def check_recharge_status(self, recharge_id: str) -> bool:
        """按充值单记录的渠道（OkPay/KKPay）向对应平台 checkDeposit 查询是否到账。
        到账则在一个事务里「标记充值完成 + 给用户加积分 + 记流水」，返回 True。"""
        recharge = self.db.get_recharge(recharge_id)
        if not recharge or recharge.status != "pending":
            return False
        if recharge.chain:
            return False  # 冷钱包单由 check_chain_recharges 链上匹配，不查支付平台

        try:
            client = self._get_gateway(recharge.gateway)
            data = await client.check_payment(recharge.id)
            if not OkPay.is_paid(data):
                return False
            with self.db.atomic() as conn:
                fresh = self.db.get_recharge(recharge_id, conn=conn)
                if not fresh or fresh.status != "pending":
                    return False  # 并发下已被处理，防重复入账
                fresh.status = "completed"
                fresh.paid_at = now_iso()
                self.db.save_recharge(fresh, conn=conn)

                user = self.db.get_user(fresh.user_id, conn=conn)
                if user:
                    user.points += fresh.points
                    user.total_recharge += fresh.amount
                    user.last_active = now_iso()
                    self.db.save_user(user, conn=conn)
                    self.db.add_point_record(
                        fresh.user_id, fresh.points, user.points,
                        f"充值 {fresh.amount:g} USDT", ref=fresh.id, conn=conn,
                    )
            return True
        except Exception as exc:
            logger.error("检查充值状态失败: %s", exc)
        return False

    def complete_recharge(self, recharge_id: str) -> Recharge | None:
        """手动确认到账（与 check_recharge_status 同一事务口径）。"""
        with self.db.atomic() as conn:
            recharge = self.db.get_recharge(recharge_id, conn=conn)
            if not recharge or recharge.status != "pending":
                return None

            recharge.status = "completed"
            recharge.paid_at = now_iso()
            self.db.save_recharge(recharge, conn=conn)

            user = self.db.get_user(recharge.user_id, conn=conn)
            if user:
                user.points += recharge.points
                user.total_recharge += recharge.amount
                user.last_active = now_iso()
                self.db.save_user(user, conn=conn)
                self.db.add_point_record(
                    recharge.user_id, recharge.points, user.points,
                    f"充值 {recharge.amount:g} USDT", ref=recharge.id, conn=conn,
                )
            return recharge

    # ---------- 冷钱包充值（v2.5.0：TRON 尾数识别 / TON 备注识别） ----------

    def _alloc_tron_suffix(self) -> int | None:
        """分配一个当前未被占用的 TRON 识别尾数（1~999 的整数，表示 0.001~0.999）。
        占用 = 存在同尾数的 pending 冷钱包单。尾数池占满返回 None（999 单并发才可能，兜底拒绝）。"""
        used: set[int] = set()
        for r in self.db.list_pending_recharges():
            if r.chain != "trc20" or not r.expected_amount:
                continue
            try:
                frac = Decimal(r.expected_amount) % 1
                used.add(int(frac * 1000))
            except Exception:
                continue
        free = [s for s in range(1, 1000) if s not in used]
        if not free:
            return None
        return random.choice(free)

    def create_chain_recharge(self, user_id: int, amount: float, chain: str) -> Recharge | None:
        """创建冷钱包充值单。

        - TRON（trc20）：TRC20 转账没有备注 → 在整数金额上追加唯一识别尾数，
          expected_amount 为含尾数的应付金额（如 10.437），用户须原样转账；
        - TON（ton）：Jetton 转账带 comment → memo = 充值单号，用户转账时填入备注；
        - 积分按应付金额换算（TRON 多付的尾数也计入积分），统一向下取整；
        - 限额校验/渠道开关/钱包地址检查任一不过返回 None。
        """
        if chain not in CHAIN_GATEWAYS:
            return None
        if self.validate_recharge_amount(amount):
            return None
        settings = self.db.get_settings()
        if chain == "trc20":
            if not settings.get("pay_tron_enabled", False):
                return None
            wallet = settings.get("tron_wallet_address", "") or ""
        else:
            if not settings.get("pay_ton_enabled", False):
                return None
            wallet = settings.get("ton_wallet_address", "") or ""
        if not wallet:
            return None

        rate = settings.get("points_rate", 10)
        expected_str = ""
        pay_amount = amount
        if chain == "trc20":
            suffix = self._alloc_tron_suffix()
            if suffix is None:
                logger.warning("TRON 尾数池已占满，拒绝新单：user=%s amount=%s", user_id, amount)
                return None
            expected = Decimal(int(amount)) + Decimal(suffix) / Decimal(1000)
            expected_str = dec_to_str(expected)
            pay_amount = float(expected_str)  # 积分按实际应付（含尾数）换算

        user = self.db.get_user(user_id)
        recharge = Recharge(
            id=gen_recharge_id(),
            user_id=user_id,
            username=user.username if user else "",
            amount=pay_amount,
            points=int(pay_amount * rate),  # 规格：向下取整
            status="pending",
            created_at=now_iso(),
            gateway=chain,
            chain=chain,
            expected_amount=expected_str,
            memo="",
        )
        if chain == "ton":
            recharge.memo = recharge.id  # TON 用转账备注匹配单号
        self.db.save_recharge(recharge)
        return recharge

    async def check_chain_recharges(self) -> list[Recharge]:
        """轮询两条链的冷钱包入账，匹配到待支付单即入账。返回本轮新到账的充值单。"""
        settings = self.db.get_settings()
        paid: list[Recharge] = []
        pending = [r for r in self.db.list_pending_recharges() if r.chain in CHAIN_GATEWAYS]
        if not pending:
            return paid

        if settings.get("pay_tron_enabled") and settings.get("tron_wallet_address"):
            txs = await fetch_tron_usdt_incoming(
                settings["tron_wallet_address"],
                api_key=settings.get("trongrid_api_key", "") or "",
            )
            paid.extend(self._match_tron_txs(pending, txs))

        if settings.get("pay_ton_enabled") and settings.get("ton_wallet_address"):
            txs = await fetch_ton_usdt_incoming(
                settings["ton_wallet_address"],
                api_key=settings.get("toncenter_api_key", "") or "",
            )
            paid.extend(self._match_ton_txs(pending, txs))
        return paid

    def _match_tron_txs(self, pending: list[Recharge], txs: list[ChainTx]) -> list[Recharge]:
        """TRON 匹配：金额精确相等（含识别尾数）+ 交易时间不早于下单前 5 分钟。
        尾数意外碰撞时按创建时间最早的一单先匹配。"""
        paid: list[Recharge] = []
        pool = [r for r in pending if r.chain == "trc20" and r.status == "pending" and r.expected_amount]
        for tx in txs:
            if self.db.chain_txid_seen(tx.txid):
                continue
            candidates = []
            for r in pool:
                try:
                    if Decimal(r.expected_amount) != tx.amount:
                        continue
                except Exception:
                    continue
                try:
                    created_ts = datetime.fromisoformat(r.created_at).timestamp()
                except Exception:
                    continue
                if tx.timestamp < created_ts - 300:  # 交易比下单早太多 → 不是这笔单
                    continue
                candidates.append(r)
            if not candidates:
                continue
            candidates.sort(key=lambda r: r.created_at)
            target = candidates[0]
            done = self._complete_chain_recharge(target, tx)
            if done:
                paid.append(done)
                pool = [x for x in pool if x.id != target.id]
        return paid

    def _match_ton_txs(self, pending: list[Recharge], txs: list[ChainTx]) -> list[Recharge]:
        """TON 匹配：memo == 充值单号 且 实付 >= 应付（少付不入账，留给管理员人工处理）。"""
        paid: list[Recharge] = []
        by_memo = {r.memo: r for r in pending if r.chain == "ton" and r.status == "pending" and r.memo}
        for tx in txs:
            if self.db.chain_txid_seen(tx.txid):
                continue
            r = by_memo.get(tx.memo)
            if not r:
                continue
            try:
                if tx.amount < Decimal(str(r.amount)):
                    logger.warning("TON 充值少付不入账：单号=%s 应付=%s 实付=%s", r.id, r.amount, tx.amount_str)
                    continue
            except Exception:
                continue
            done = self._complete_chain_recharge(r, tx)
            if done:
                paid.append(done)
                by_memo.pop(tx.memo, None)
        return paid

    def _complete_chain_recharge(self, recharge: Recharge, tx: ChainTx) -> Recharge | None:
        """链上到账入账：写 txid（幂等防重放）+ 完成充值单 + 加积分 + 记流水，一个事务提交。"""
        with self.db.atomic() as conn:
            fresh = self.db.get_recharge(recharge.id, conn=conn)
            if not fresh or fresh.status != "pending":
                return None
            row = conn.execute("SELECT 1 FROM chain_txids WHERE txid = ?", (tx.txid,)).fetchone()
            if row:
                return None  # 同一笔链上交易已入账过，重放直接丢弃
            self.db.add_chain_txid(tx.txid, fresh.id, fresh.chain or "", conn=conn)
            fresh.status = "completed"
            fresh.txid = tx.txid
            fresh.paid_at = now_iso()
            self.db.save_recharge(fresh, conn=conn)

            user = self.db.get_user(fresh.user_id, conn=conn)
            if user:
                user.points += fresh.points
                user.total_recharge += fresh.amount
                user.last_active = now_iso()
                self.db.save_user(user, conn=conn)
                self.db.add_point_record(
                    fresh.user_id, fresh.points, user.points,
                    f"充值 {fresh.amount:g} USDT（{GATEWAY_NAMES.get(fresh.chain, fresh.chain)}）",
                    ref=fresh.id, conn=conn,
                )
            return fresh

    # ---------- 频道管理 ----------

    def add_channel(self, chat_id: str, title: str = "", username: str = "", added_by: int = 0) -> Channel:
        import uuid
        channel = Channel(
            id=f"CH_{uuid.uuid4().hex[:8].upper()}",
            chat_id=chat_id,
            title=title or chat_id,
            username=username,
            added_by=added_by,
            added_at=now_iso(),
        )
        self.db.save_channel(channel)
        return channel

    def remove_channel(self, channel_id: str) -> bool:
        channel = self.db.get_channel(channel_id)
        if not channel:
            return False
        self.db.delete_channel(channel_id)
        return True

    # ---------- 通知 ----------

    async def notify_admins_new_order(self, order: Order, copy_from: tuple[int, int] | None = None) -> None:
        """给所有管理员推送新订单通知（带操作按钮）。

        发出的每条通知消息会记录 (chat_id, message_id) -> order_id 映射，
        管理员直接「引用回复」该通知消息即可回单。
        copy_from: (chat_id, message_id) —— 报单类用户提交所需信息的那条原始消息，
        会原样复制给每位管理员，管理员可直接看到用户发的图片/文件/文字。
        """
        if not self._bot:
            return
        admins = self.db.list_admins()
        if not admins:
            return

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        kind_text = "商品" if order.product_kind == "product" else "报单"
        text = (
            f"🛒 <b>收到一个新订单</b>\n\n"
            f"类型：{kind_text}\n"
            f"订单号：<code>{order.id}</code>\n"
            f"用户：@{escape_html(order.username or '无')}（<code>{order.user_id}</code>）\n"
            f"商品：{escape_html(order.product_name)}\n"
            f"积分：<code>{order.points}</code>\n"
            f"时间：<code>{fmt_time(order.created_at)}</code>"
        )
        if order.note:
            text += f"\n\n📎 <b>用户提交的信息：</b>\n{escape_html(order.note)}"
        text += "\n\n💡 直接<b>引用回复本消息</b>并输入内容即可回单"
        # 按钮带高级会员表情图标（v2.3.0 补齐，与主菜单 kb() 同一套图标库）
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "完成（回单）", callback_data=f"a:oc:{order.id}",
                icon_custom_emoji_id=ICON_CHECK,
            )],
            [
                InlineKeyboardButton(
                    "处理中", callback_data=f"a:op:{order.id}",
                    icon_custom_emoji_id=ICON_ZAP,
                ),
                InlineKeyboardButton(
                    "退款", callback_data=f"a:or:{order.id}",
                    icon_custom_emoji_id=ICON_DIAMOND,
                ),
            ],
            [InlineKeyboardButton(
                "订单详情", callback_data=f"a:od:{order.id}",
                icon_custom_emoji_id=ICON_MAIL,
            )],
        ])

        for admin in admins:
            try:
                msg = await self._bot.send_message(
                    chat_id=admin.user_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                self.db.save_order_notification(admin.user_id, msg.message_id, order.id)
                if copy_from:
                    # 把用户提交信息的原始消息（任意类型）原样转给管理员
                    await self._bot.copy_message(
                        chat_id=admin.user_id,
                        from_chat_id=copy_from[0],
                        message_id=copy_from[1],
                    )
            except Exception as exc:
                logger.warning("通知管理员 %s 失败：%s", admin.user_id, exc)

    async def notify_user_order_completed(
        self,
        order: Order,
        copy_from: tuple[int, int] | None = None,
    ) -> None:
        """通知用户订单已完成。

        copy_from: (chat_id, message_id) —— 若提供，则把管理员的那条原始消息
        （文字/图片/文件/视频任意类型）原样复制给用户，支持全部消息类型。
        """
        if not self._bot:
            return
        text = (
            f"✅ <b>订单已完成</b>\n\n"
            f"订单号：<code>{order.id}</code>\n"
            f"商品：{order.product_name}\n"
            f"积分：<code>{order.points}</code>\n\n"
            f"📝 回单内容：\n{order.reply_content or '（见下方消息）'}"
        )
        try:
            # 安全发送：回单内容可能含管理员排的格式/表情，失败自动降级纯文本
            await send_html(self._bot, order.user_id, text)
            if copy_from:
                await self._bot.copy_message(
                    chat_id=order.user_id,
                    from_chat_id=copy_from[0],
                    message_id=copy_from[1],
                )
        except Exception as exc:
            logger.warning("通知用户 %s 失败：%s", order.user_id, exc)

    async def notify_user_order_refunded(self, order: Order) -> None:
        """通知用户订单已退款。"""
        if not self._bot:
            return
        text = (
            f"💰 <b>订单已退款</b>\n\n"
            f"订单号：<code>{order.id}</code>\n"
            f"商品：{order.product_name}\n"
            f"已退回积分：<code>{order.points}</code>"
        )
        try:
            await self._bot.send_message(
                chat_id=order.user_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("退款通知用户 %s 失败：%s", order.user_id, exc)

    async def notify_admins_recharge_paid(self, recharge: Recharge) -> None:
        """充值到账后通知管理员。"""
        if not self._bot:
            return
        admins = self.db.list_admins()
        channel_name = GATEWAY_NAMES.get(recharge.gateway or "", recharge.gateway or "未知渠道")
        text = (
            f"💰 <b>充值到账</b>\n\n"
            f"用户：@{recharge.username or '无'}（<code>{recharge.user_id}</code>）\n"
            f"金额：<code>{recharge.amount:g}</code> USDT\n"
            f"积分：<code>{recharge.points}</code>\n"
            f"渠道：{channel_name}\n"
            f"单号：<code>{recharge.id}</code>"
        )
        if recharge.txid:
            text += f"\n链上凭证：<code>{recharge.txid}</code>"
        for admin in admins:
            try:
                await self._bot.send_message(chat_id=admin.user_id, text=text, parse_mode="HTML")
            except Exception as exc:
                logger.warning("充值通知管理员 %s 失败：%s", admin.user_id, exc)

    # ---------- 充值自动轮询 ----------

    async def check_all_pending_recharges(self, max_age_hours: int = 2) -> list[Recharge]:
        """轮询所有待支付充值单（支付平台渠道），返回本轮新到账的列表。
        冷钱包单不在此查支付平台，仅做过期清理；到账匹配由 check_chain_recharges 负责。"""
        from datetime import datetime, timedelta

        paid_list: list[Recharge] = []
        pending = self.db.list_pending_recharges()
        cutoff = datetime.now(LOCAL_TZ) - timedelta(hours=max_age_hours)

        for r in pending:
            try:
                created = datetime.fromisoformat(r.created_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=LOCAL_TZ)
            except Exception:
                continue
            if created < cutoff:
                # 超过 max_age_hours 未支付的标记为失败，不再轮询（冷钱包单同样过期）
                r.status = "failed"
                self.db.save_recharge(r)
                continue
            if r.chain:
                continue  # 冷钱包单走链上监听，不查支付平台
            try:
                if await self.check_recharge_status(r.id):
                    fresh = self.db.get_recharge(r.id)
                    if fresh:
                        paid_list.append(fresh)
            except Exception as exc:
                logger.warning("轮询充值单 %s 失败：%s", r.id, exc)
        return paid_list

    async def notify_admins_urge(self, order: Order, user_id: int) -> None:
        """用户催单时通知管理员。"""
        if not self._bot:
            return
        admins = self.db.list_admins()
        if not admins:
            return

        text = (
            f"🔄 用户催单提醒\n\n"
            f"订单号：<code>{order.id}</code>\n"
            f"用户：{order.username or order.user_id}\n"
            f"商品：{order.product_name}\n"
            f"积分：<code>{order.points}</code>\n"
            f"创建时间：{fmt_time(order.created_at)}"
        )

        for admin in admins:
            try:
                await self._bot.send_message(
                    chat_id=admin.user_id,
                    text=text,
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.warning("催单通知管理员 %s 失败：%s", admin.user_id, exc)

    async def broadcast_message(
        self,
        text: str,
        photo: str = "",
        target: str = "all",
        copy_from: tuple[int, int] | None = None,
    ) -> dict[str, int]:
        """广播消息。

        copy_from: (chat_id, message_id) —— 若提供，则把该消息原样复制给所有目标用户，
        支持文字/图片/视频/文件等全部消息类型。
        """
        if not self._bot:
            return {"success": 0, "failed": 0}

        users = self.db.list_users(limit=10000)
        if target == "with_points":
            users = [u for u in users if u.points > 0]
        elif target == "no_points":
            users = [u for u in users if u.points == 0]

        success = 0
        failed = 0
        for user in users:
            try:
                await self._broadcast_one(user.user_id, text, photo, copy_from)
                success += 1
                await asyncio.sleep(0.05)  # 防止触发限流
            except Exception as exc:
                logger.warning("广播给用户 %s 失败：%s", user.user_id, exc)
                failed += 1

        return {"success": success, "failed": failed}

    async def _broadcast_one(
        self,
        user_id: int,
        text: str,
        photo: str,
        copy_from: tuple[int, int] | None,
    ) -> None:
        """广播单条发送（v2.4.0）：命中 Telegram 限流（RetryAfter/429）时
        按官方要求等待后自动重试，最多 3 次；其他错误直接抛给上层计数。"""
        for attempt in range(3):
            try:
                if copy_from:
                    await self._bot.copy_message(
                        chat_id=user_id,
                        from_chat_id=copy_from[0],
                        message_id=copy_from[1],
                    )
                elif photo:
                    await self._bot.send_photo(
                        chat_id=user_id,
                        photo=photo,
                        caption=text,
                        parse_mode="HTML",
                    )
                else:
                    await self._bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode="HTML",
                    )
                return
            except RetryAfter as exc:
                # 限流：等待官方要求的秒数后重试
                wait = float(getattr(exc, "retry_after", 1)) + 0.5
                logger.info("广播限流，等待 %.1f 秒后重试（%s/3）", wait, attempt + 1)
                await asyncio.sleep(wait)

    def get_finance_stats(self) -> dict[str, Any]:
        """获取财务统计。"""
        conn = self.db._get_conn()
        try:
            # 总充值金额
            row = conn.execute("SELECT SUM(amount) FROM recharges WHERE status = 'completed'").fetchone()
            total_recharge = row[0] or 0.0

            # 总充值积分
            row = conn.execute("SELECT SUM(points) FROM recharges WHERE status = 'completed'").fetchone()
            total_points = row[0] or 0

            # 总消费积分
            row = conn.execute("SELECT SUM(points) FROM orders WHERE status IN ('completed', 'processing', 'pending')").fetchone()
            total_spent = row[0] or 0

            # 订单统计
            row = conn.execute("SELECT COUNT(*) FROM orders").fetchone()
            total_orders = row[0] or 0

            row = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'").fetchone()
            pending_orders = row[0] or 0

            row = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'completed'").fetchone()
            completed_orders = row[0] or 0

            # 用户统计
            row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
            total_users = row[0] or 0

            row = conn.execute("SELECT COUNT(*) FROM users WHERE points > 0").fetchone()
            active_users = row[0] or 0

            # 商品统计
            row = conn.execute("SELECT COUNT(*) FROM products").fetchone()
            total_products = row[0] or 0

            row = conn.execute("SELECT COUNT(*) FROM products WHERE kind = 'product'").fetchone()
            product_count = row[0] or 0

            row = conn.execute("SELECT COUNT(*) FROM products WHERE kind = 'report'").fetchone()
            report_count = row[0] or 0

            # 今日统计（与全站时区一致）
            from datetime import datetime
            today = datetime.now(LOCAL_TZ).strftime("%Y%m%d")
            today_date = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
            row = conn.execute("SELECT COUNT(*) FROM orders WHERE id LIKE ?", (f"{today}-%",)).fetchone()
            today_orders = row[0] or 0
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM recharges WHERE status = 'completed' AND paid_at >= ?",
                (today_date,),
            ).fetchone()
            today_recharge = row[0] or 0.0
            row = conn.execute(
                "SELECT COUNT(*) FROM users WHERE created_at >= ?",
                (today_date,),
            ).fetchone()
            today_users = row[0] or 0

            return {
                "total_recharge": total_recharge,
                "total_points": total_points,
                "total_spent": total_spent,
                "total_orders": total_orders,
                "pending_orders": pending_orders,
                "completed_orders": completed_orders,
                "total_users": total_users,
                "active_users": active_users,
                "total_products": total_products,
                "product_count": product_count,
                "report_count": report_count,
                "today_orders": today_orders,
                "today_recharge": today_recharge,
                "today_users": today_users,
                # 库存池概览（v2.4.0）：现货件数/已售件数/池模式商品数
                **self.db.stock_pool_stats(),
            }
        finally:
            conn.close()

    async def notify_user_recharge_success(self, recharge: Recharge) -> None:
        if not self._bot:
            return
        user = self.db.get_user(recharge.user_id)
        if not user:
            return
        text = (
            f"✅ 充值成功\n\n"
            f"金额：<code>{recharge.amount}</code> USDT\n"
            f"获得积分：<code>{recharge.points}</code>\n"
            f"当前余额：<code>{user.points}</code>"
        )
        try:
            await self._bot.send_message(
                chat_id=recharge.user_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("通知用户 %s 失败：%s", recharge.user_id, exc)
