"""数据模型：全部 dataclass 定义（纯数据，无业务逻辑）。"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .base import *  # noqa: F401,F403  个别 from_dict 会用到常量


@dataclass
class User:
    user_id: int
    username: str = ""
    first_name: str = ""
    last_name: str = ""
    points: int = 0
    total_recharge: float = 0.0
    total_spent: int = 0
    is_banned: bool = False
    invite_code: str = ""
    invited_by: str = ""
    created_at: str = ""
    last_active: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "points": self.points,
            "total_recharge": self.total_recharge,
            "total_spent": self.total_spent,
            "is_banned": self.is_banned,
            "invite_code": self.invite_code,
            "invited_by": self.invited_by,
            "created_at": self.created_at,
            "last_active": self.last_active,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> User:
        return cls(
            user_id=data.get("user_id", 0),
            username=data.get("username", "") or "",
            first_name=data.get("first_name", "") or "",
            last_name=data.get("last_name", "") or "",
            points=data.get("points", 0) or 0,
            total_recharge=data.get("total_recharge", 0.0) or 0.0,
            total_spent=data.get("total_spent", 0) or 0,
            is_banned=bool(data.get("is_banned", False)),
            invite_code=data.get("invite_code", "") or "",
            invited_by=data.get("invited_by", "") or "",
            created_at=data.get("created_at", "") or "",
            last_active=data.get("last_active", "") or "",
        )


@dataclass
class Product:
    id: str
    name: str = ""
    description: str = ""
    points: int = 0
    kind: str = "product"
    stock: int = -1
    limit_per_user: int = 0
    status: str = "on"
    is_top: bool = False
    tags: list[str] = field(default_factory=list)
    auto_delivery: str = ""
    photo_file_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    category: str = ""             # 商品分类（v2.4.0；空=未分类，配合 settings.product_categories）
    delivery_mode: str = "fixed"   # 发货模式：fixed=固定内容重复发 / pool=库存池一单一货（v2.4.0）
    commission_pct: int = -1       # 抢单佣金比例%（v2.6.0；-1=跟随全局 grab_commission_pct，0=该单无佣金）

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "points": self.points,
            "kind": self.kind,
            "stock": self.stock,
            "limit_per_user": self.limit_per_user,
            "status": self.status,
            "is_top": self.is_top,
            "tags": json.dumps(self.tags, ensure_ascii=False),
            "auto_delivery": self.auto_delivery,
            "photo_file_id": self.photo_file_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "category": self.category,
            "delivery_mode": self.delivery_mode,
            "commission_pct": self.commission_pct,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Product:
        tags = data.get("tags", "[]")
        if isinstance(tags, str):
            tags = json.loads(tags)
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            points=data.get("points", 0),
            kind=data.get("kind", "product"),
            stock=data.get("stock", -1),
            limit_per_user=data.get("limit_per_user", 0),
            status=data.get("status", "on"),
            is_top=bool(data.get("is_top", False)),
            tags=tags,
            auto_delivery=data.get("auto_delivery", ""),
            photo_file_id=data.get("photo_file_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            category=data.get("category", "") or "",
            delivery_mode=data.get("delivery_mode", "fixed") or "fixed",
            commission_pct=int(data.get("commission_pct", -1) if data.get("commission_pct") is not None else -1),
        )


@dataclass
class Order:
    id: str
    user_id: int = 0
    username: str = ""
    product_id: str = ""
    product_name: str = ""
    product_kind: str = "product"
    points: int = 0
    status: str = "pending"
    priority: str = "normal"
    note: str = ""
    reply_content: str = ""
    reply_admin_id: int = 0
    reply_at: str = ""
    locked_by: int = 0
    created_at: str = ""
    completed_at: str = ""
    stock_item_id: int = 0          # 库存池商品：本单发出的那件货（v2.4.0；0=非池商品）
    # ---------- 抢单字段（v2.6.0；非派单订单全部为空/0，行为与旧版一致） ----------
    grab_status: str = ""           # '' / waiting 待抢 / grabbed 进行中 / done 已完单
    grabber_id: int = 0             # 抢中的接单员 TG ID（0=未抢）
    grabber_name: str = ""          # 抢中时的接单员昵称快照
    commission: int = 0             # 本单佣金（积分，抢中时按当时比例锁定）
    grab_at: str = ""               # 抢中时间
    deadline: str = ""              # 最晚回单时间（抢中时间 + grab_timeout_min）
    dispatch_chat: str = ""         # 派单群 chat_id
    dispatch_msg: int = 0           # 派单群消息 message_id（更新「已接」/终态清理用）
    dispatch_count: int = 0         # 已派单次数（>1 表示重发过/释放重派过）
    last_dispatch_at: str = ""      # 最近一次派单时间（二次提醒判断用）
    warn_sent: int = 0              # 临期预警是否已发（0/1，防重复轰炸）

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "product_kind": self.product_kind,
            "points": self.points,
            "status": self.status,
            "priority": self.priority,
            "note": self.note,
            "reply_content": self.reply_content,
            "reply_admin_id": self.reply_admin_id,
            "reply_at": self.reply_at,
            "locked_by": self.locked_by,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "stock_item_id": self.stock_item_id,
            "grab_status": self.grab_status,
            "grabber_id": self.grabber_id,
            "grabber_name": self.grabber_name,
            "commission": self.commission,
            "grab_at": self.grab_at,
            "deadline": self.deadline,
            "dispatch_chat": self.dispatch_chat,
            "dispatch_msg": self.dispatch_msg,
            "dispatch_count": self.dispatch_count,
            "last_dispatch_at": self.last_dispatch_at,
            "warn_sent": self.warn_sent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Order:
        return cls(
            id=data.get("id", "") or "",
            user_id=data.get("user_id", 0) or 0,
            username=data.get("username", "") or "",
            product_id=data.get("product_id", "") or "",
            product_name=data.get("product_name", "") or "",
            product_kind=data.get("product_kind", "product") or "product",
            points=data.get("points", 0) or 0,
            status=data.get("status", "pending") or "pending",
            priority=data.get("priority", "normal") or "normal",
            note=data.get("note", "") or "",
            reply_content=data.get("reply_content", "") or "",
            reply_admin_id=data.get("reply_admin_id", 0) or 0,
            reply_at=data.get("reply_at", "") or "",
            locked_by=data.get("locked_by", 0) or 0,
            created_at=data.get("created_at", "") or "",
            completed_at=data.get("completed_at", "") or "",
            stock_item_id=int(data.get("stock_item_id", 0) or 0),
            grab_status=data.get("grab_status", "") or "",
            grabber_id=int(data.get("grabber_id", 0) or 0),
            grabber_name=data.get("grabber_name", "") or "",
            commission=int(data.get("commission", 0) or 0),
            grab_at=data.get("grab_at", "") or "",
            deadline=data.get("deadline", "") or "",
            dispatch_chat=data.get("dispatch_chat", "") or "",
            dispatch_msg=int(data.get("dispatch_msg", 0) or 0),
            dispatch_count=int(data.get("dispatch_count", 0) or 0),
            last_dispatch_at=data.get("last_dispatch_at", "") or "",
            warn_sent=int(data.get("warn_sent", 0) or 0),
        )


@dataclass
class Recharge:
    id: str
    user_id: int = 0
    username: str = ""
    amount: float = 0.0
    points: int = 0
    status: str = "pending"
    pay_url: str = ""
    okpay_order_id: str = ""   # 支付平台交易号（OkPay order_id / KKPay txid，字段名保持兼容）
    gateway: str = "okpay"     # 支付渠道：okpay / kkpay / tron / ton，老数据默认 okpay
    created_at: str = ""
    paid_at: str = ""
    # ---------- v2.5.0 冷钱包字段（网关单为空串，不影响老数据） ----------
    chain: str = ""            # 冷钱包链标识：trc20 / ton
    expected_amount: str = ""  # 期望到账金额（原样字符串，含识别尾数，如 "10.437"）
    txid: str = ""             # 链上交易哈希（入账凭证，防重放）
    memo: str = ""             # TON 转账备注（= 充值单号）

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "amount": self.amount,
            "points": self.points,
            "status": self.status,
            "pay_url": self.pay_url,
            "okpay_order_id": self.okpay_order_id,
            "gateway": self.gateway,
            "created_at": self.created_at,
            "paid_at": self.paid_at,
            "chain": self.chain,
            "expected_amount": self.expected_amount,
            "txid": self.txid,
            "memo": self.memo,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recharge:
        return cls(
            id=data.get("id", ""),
            user_id=data.get("user_id", 0),
            username=data.get("username", "") or "",
            amount=data.get("amount", 0.0) or 0.0,
            points=data.get("points", 0) or 0,
            status=data.get("status", "pending") or "pending",
            pay_url=data.get("pay_url", "") or "",
            okpay_order_id=data.get("okpay_order_id", "") or "",
            gateway=data.get("gateway", "okpay") or "okpay",
            created_at=data.get("created_at", "") or "",
            paid_at=data.get("paid_at", "") or "",
            chain=data.get("chain", "") or "",
            expected_amount=data.get("expected_amount", "") or "",
            txid=data.get("txid", "") or "",
            memo=data.get("memo", "") or "",
        )


@dataclass
class Admin:
    user_id: int
    name: str = ""
    permissions: list[str] = field(default_factory=list)
    added_by: int = 0
    added_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "name": self.name,
            "permissions": json.dumps(self.permissions, ensure_ascii=False),
            "added_by": self.added_by,
            "added_at": self.added_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Admin:
        perms = data.get("permissions", "[]")
        if isinstance(perms, str):
            perms = json.loads(perms)
        return cls(
            user_id=data.get("user_id", 0),
            name=data.get("name", ""),
            permissions=perms,
            added_by=data.get("added_by", 0),
            added_at=data.get("added_at", ""),
        )


@dataclass
class Channel:
    id: str
    chat_id: str = ""
    title: str = ""
    username: str = ""
    added_by: int = 0
    added_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "title": self.title,
            "username": self.username,
            "added_by": self.added_by,
            "added_at": self.added_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Channel:
        return cls(
            id=data.get("id", ""),
            chat_id=data.get("chat_id", ""),
            title=data.get("title", ""),
            username=data.get("username", ""),
            added_by=data.get("added_by", 0),
            added_at=data.get("added_at", ""),
        )


@dataclass
class PointRecord:
    """积分变动流水：每一笔积分增减（充值/消费/退款/管理员调整）都留痕，可对账。"""

    id: int = 0
    user_id: int = 0
    change: int = 0           # 正数为加分，负数为扣分
    balance_after: int = 0    # 变动后的积分余额
    reason: str = ""          # 变动原因，如「充值 10 USDT」「购买 xxx」
    operator_id: int = 0      # 操作人（管理员调整时为管理员 ID，系统行为为 0）
    ref: str = ""             # 关联单号（订单号 / 充值单号）
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PointRecord:
        return cls(
            id=data.get("id", 0),
            user_id=data.get("user_id", 0),
            change=data.get("change", 0) or 0,
            balance_after=data.get("balance_after", 0) or 0,
            reason=data.get("reason", "") or "",
            operator_id=data.get("operator_id", 0) or 0,
            ref=data.get("ref", "") or "",
            created_at=data.get("created_at", "") or "",
        )


@dataclass
class SupplyPost:
    """付费供需帖子：扣积分发布，按渠道广播到机器人全员和/或发到频道。"""

    id: str = ""
    user_id: int = 0
    username: str = ""         # 发布时的用户名快照（匿名帖不展示）
    display_name: str = ""     # 发布时的昵称快照
    category: str = ""         # 分类名，空串表示未启用分类
    content: str = ""          # 纯文本（字数校验/列表摘要/降级用）
    content_html: str = ""     # 富文本 HTML（text_html 生成，含自定义表情；空=老数据）
    photo_file_id: str = ""    # 可选，一张图
    channel: str = "bot"       # bot=机器人广播 / channel=频道 / both=双发
    cost: int = 0              # 本次实付积分
    channel_leg: int = 0       # 发帖时「频道腿」实付快照（退款按此金额，不受事后调价影响）
    anonymous: bool = False    # 匿名发布（管理员后台仍可见真实身份）
    status: str = "active"     # active / pending_review / rejected / deleted
    bot_ok: bool = False       # 机器人广播是否已执行
    channel_ok: bool = False   # 频道是否已发送成功
    channel_msg: str = ""      # 频道消息 "chat_id:message_id"
    refunded: bool = False     # 是否已退款（删帖退款/分发失败退款）
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "category": self.category,
            "content": self.content,
            "content_html": self.content_html,
            "photo_file_id": self.photo_file_id,
            "channel": self.channel,
            "cost": self.cost,
            "channel_leg": self.channel_leg,
            "anonymous": self.anonymous,
            "status": self.status,
            "bot_ok": self.bot_ok,
            "channel_ok": self.channel_ok,
            "channel_msg": self.channel_msg,
            "refunded": self.refunded,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SupplyPost:
        return cls(
            id=data.get("id", ""),
            user_id=data.get("user_id", 0),
            username=data.get("username", "") or "",
            display_name=data.get("display_name", "") or "",
            category=data.get("category", "") or "",
            content=data.get("content", "") or "",
            content_html=data.get("content_html", "") or "",
            photo_file_id=data.get("photo_file_id", "") or "",
            channel=data.get("channel", "bot") or "bot",
            cost=data.get("cost", 0) or 0,
            channel_leg=data.get("channel_leg", 0) or 0,
            anonymous=bool(data.get("anonymous", False)),
            status=data.get("status", "active") or "active",
            bot_ok=bool(data.get("bot_ok", False)),
            channel_ok=bool(data.get("channel_ok", False)),
            channel_msg=data.get("channel_msg", "") or "",
            refunded=bool(data.get("refunded", False)),
            created_at=data.get("created_at", "") or "",
        )


SUPPLY_CHANNELS = {
    "bot": "🤖 机器人供需",
    "channel": "📣 频道供需",
    "both": "🚀 双发（机器人+频道）",
}


# ---------- 库存池（v2.4.0） ----------

STOCK_TYPES = {
    "text": "📝 文字",
    "photo": "🖼 图片",
    "document": "📎 文件",
    "album": "🖼 图册",
}

STOCK_STATUS = {
    "available": "🟢 在售",
    "sold": "⚪ 已售",
}


@dataclass
class StockItem:
    """库存池里的一件货：文字/图片/文件/图册（多图合并算一件），一件只卖一次。"""

    id: int = 0
    product_id: str = ""
    content_type: str = "text"       # text / photo / document / album
    content_text: str = ""           # text 类型的正文
    file_ids: list[str] = field(default_factory=list)  # photo/document 单条；album 多条（同组多图）
    caption: str = ""                # 图片/文件/图册的说明文字（可空）
    status: str = "available"        # available / sold
    order_id: str = ""               # 售出关联的订单号
    buyer_id: int = 0
    created_at: str = ""
    sold_at: str = ""

    def summary(self) -> str:
        """列表/通知用的简短摘要，不泄露完整内容。"""
        if self.content_type == "text":
            t = self.content_text.replace("\n", " ")
            return t[:24] + ("…" if len(t) > 24 else "")
        mark = STOCK_TYPES.get(self.content_type, self.content_type)
        if self.content_type == "album":
            mark += f"×{len(self.file_ids)}"
        if self.caption:
            c = self.caption.replace("\n", " ")[:16]
            return f"{mark} {c}"
        return mark

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "product_id": self.product_id,
            "content_type": self.content_type,
            "content_text": self.content_text,
            "file_ids": json.dumps(self.file_ids, ensure_ascii=False),
            "caption": self.caption,
            "status": self.status,
            "order_id": self.order_id,
            "buyer_id": self.buyer_id,
            "created_at": self.created_at,
            "sold_at": self.sold_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StockItem:
        fids = data.get("file_ids", "[]")
        if isinstance(fids, str):
            try:
                fids = json.loads(fids)
            except Exception:
                fids = []
        return cls(
            id=int(data.get("id", 0) or 0),
            product_id=data.get("product_id", "") or "",
            content_type=data.get("content_type", "text") or "text",
            content_text=data.get("content_text", "") or "",
            file_ids=list(fids or []),
            caption=data.get("caption", "") or "",
            status=data.get("status", "available") or "available",
            order_id=data.get("order_id", "") or "",
            buyer_id=int(data.get("buyer_id", 0) or 0),
            created_at=data.get("created_at", "") or "",
            sold_at=data.get("sold_at", "") or "",
        )


@dataclass
class AuditLog:
    """管理员操作日志：谁、什么时候、干了什么（改价/退款/上下架/发货等）。"""

    id: int = 0
    admin_id: int = 0
    action: str = ""
    detail: str = ""
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AuditLog:
        return cls(
            id=int(row["id"]),
            admin_id=int(row["admin_id"] or 0),
            action=row["action"] or "",
            detail=row["detail"] or "",
            created_at=row["created_at"] or "",
        )


# ==================================================================
# 抢单模块数据类（v2.6.0）
# ==================================================================

@dataclass
class Grabber:
    """接单员档案：状态/余额/业绩全在一张卡上，管理端一目了然。"""

    user_id: int = 0
    seq: int = 0                   # 接单员编号（上岗顺序，群内公示用 #0001，不透真实身份）
    username: str = ""
    name: str = ""                 # 显示名快照（昵称）
    status: str = "active"         # active / rest / suspended / blacklist
    balance_available: int = 0     # 可提现余额（积分）
    balance_frozen: int = 0        # 冻结中佣金（积分）
    total_commission: int = 0      # 累计佣金（积分，含冻结与已提现）
    completed_orders: int = 0      # 累计完单
    timeout_orders: int = 0        # 累计超时
    giveup_orders: int = 0         # 累计放弃
    cooldown_until: str = ""       # 冷静期截止（超时/放弃惩罚，ISO 时间）
    last_grab_at: str = ""         # 最近一次抢中时间（抢单冷却判断用）
    invite_link: str = ""          # 专属入群链接（一次性，member_limit=1）
    joined_group: int = 0          # 是否已进接单群（进群置 1，退群归 0）
    created_at: str = ""
    updated_at: str = ""

    @property
    def on_time_rate(self) -> float:
        """准时率：完单 /（完单+超时），无数据按 100% 计。"""
        total = self.completed_orders + self.timeout_orders
        if total <= 0:
            return 100.0
        return round(self.completed_orders * 100.0 / total, 1)

    def display(self) -> str:
        """展示名：#编号 昵称。"""
        label = self.name or self.username or str(self.user_id)
        return f"#{self.seq:04d} {label}" if self.seq else label

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Grabber:
        return cls(
            user_id=int(data.get("user_id", 0) or 0),
            seq=int(data.get("seq", 0) or 0),
            username=data.get("username", "") or "",
            name=data.get("name", "") or "",
            status=data.get("status", "active") or "active",
            balance_available=int(data.get("balance_available", 0) or 0),
            balance_frozen=int(data.get("balance_frozen", 0) or 0),
            total_commission=int(data.get("total_commission", 0) or 0),
            completed_orders=int(data.get("completed_orders", 0) or 0),
            timeout_orders=int(data.get("timeout_orders", 0) or 0),
            giveup_orders=int(data.get("giveup_orders", 0) or 0),
            cooldown_until=data.get("cooldown_until", "") or "",
            last_grab_at=data.get("last_grab_at", "") or "",
            invite_link=data.get("invite_link", "") or "",
            joined_group=int(data.get("joined_group", 0) or 0),
            created_at=data.get("created_at", "") or "",
            updated_at=data.get("updated_at", "") or "",
        )


@dataclass
class GrabCommission:
    """佣金流水：每一单佣金的冻结/解冻/追回全留痕，可对账。"""

    id: int = 0
    order_id: str = ""
    grabber_id: int = 0
    amount: int = 0                # 积分
    status: str = "frozen"         # frozen / released / clawed
    frozen_until: str = ""         # 解冻时刻（grab_freeze_hours 算出；空=立即解冻）
    released_at: str = ""
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GrabCommission:
        return cls(
            id=int(data.get("id", 0) or 0),
            order_id=data.get("order_id", "") or "",
            grabber_id=int(data.get("grabber_id", 0) or 0),
            amount=int(data.get("amount", 0) or 0),
            status=data.get("status", "frozen") or "frozen",
            frozen_until=data.get("frozen_until", "") or "",
            released_at=data.get("released_at", "") or "",
            created_at=data.get("created_at", "") or "",
        )


@dataclass
class Withdrawal:
    """接单员提现单：申请 → 管理员审核打款 → 填 txid 完成。"""

    id: str = ""
    grabber_id: int = 0
    amount: int = 0                # 提现积分
    amount_usdt: float = 0.0       # 按汇率自动换算的 USDT（打款依据）
    address: str = ""              # OK钱包账户ID（接单员申请时填）
    status: str = "pending"        # pending / paid / rejected
    txid: str = ""                 # 打款凭证（管理员填）
    note: str = ""                 # 管理员备注（拒绝原因等）
    processed_by: int = 0
    processed_at: str = ""
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Withdrawal:
        return cls(
            id=data.get("id", "") or "",
            grabber_id=int(data.get("grabber_id", 0) or 0),
            amount=int(data.get("amount", 0) or 0),
            amount_usdt=float(data.get("amount_usdt", 0) or 0),
            address=data.get("address", "") or "",
            status=data.get("status", "pending") or "pending",
            txid=data.get("txid", "") or "",
            note=data.get("note", "") or "",
            processed_by=int(data.get("processed_by", 0) or 0),
            processed_at=data.get("processed_at", "") or "",
            created_at=data.get("created_at", "") or "",
        )


@dataclass
class GrabAppeal:
    """接单员申诉：对超时/扣款判定的异议，管理员裁定留档。"""

    id: int = 0
    grabber_id: int = 0
    order_id: str = ""
    content: str = ""
    status: str = "pending"        # pending / approved / rejected
    reply: str = ""                # 管理员裁定说明
    handled_by: int = 0
    handled_at: str = ""
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GrabAppeal:
        return cls(
            id=int(data.get("id", 0) or 0),
            grabber_id=int(data.get("grabber_id", 0) or 0),
            order_id=data.get("order_id", "") or "",
            content=data.get("content", "") or "",
            status=data.get("status", "pending") or "pending",
            reply=data.get("reply", "") or "",
            handled_by=int(data.get("handled_by", 0) or 0),
            handled_at=data.get("handled_at", "") or "",
            created_at=data.get("created_at", "") or "",
        )


__all__ = [
    "User", "Product", "Order", "Recharge",
    "Admin", "Channel", "PointRecord", "SupplyPost",
    "StockItem", "AuditLog", "Grabber", "GrabCommission",
    "Withdrawal", "GrabAppeal", "SUPPLY_CHANNELS", "STOCK_TYPES",
    "STOCK_STATUS",
]
