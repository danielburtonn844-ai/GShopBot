"""数据模型与 SQLite 持久化。"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 全站统一时区：东八区。所有写入与展示的时间都以它为基准，订单号日期、
# 「今日」统计、页面显示的时间全部一致，不再出现 UTC 与本地时间混用。
LOCAL_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(LOCAL_TZ).isoformat()


def now_str() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def fmt_time(iso_str: str | None) -> str:
    """把 ISO 时间戳格式化为普通人看得懂的「年-月-日 时:分:秒」。"""
    if not iso_str:
        return ""
    return iso_str[:19].replace("T", " ")


def gen_recharge_id() -> str:
    """充值单号：ORDER_日期_随机段，与 OkPay 的 unique_id 一一对应。"""
    return f"ORDER_{datetime.now(LOCAL_TZ).strftime('%Y%m%d')}_{uuid.uuid4().hex[:8].upper()}"


def gen_product_id() -> str:
    return f"P_{uuid.uuid4().hex[:8].upper()}"


def gen_supply_id() -> str:
    """供需帖子单号：SP_日期_随机段。"""
    return f"SP_{datetime.now(LOCAL_TZ).strftime('%Y%m%d')}_{uuid.uuid4().hex[:6].upper()}"


def format_usdt(amount: float) -> str:
    return f"{amount:.2f} USDT"


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def escape_html(text: str | None) -> str:
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


ORDER_STATUS = {
    "pending": "⏳ 待处理",
    "processing": "🔄 处理中",
    "completed": "✅ 已完成",
    "cancelled": "❌ 已取消",
    "refunded": "💰 已退款",
}

PRODUCT_STATUS = {
    "on": "🟢 销售中",
    "off": "🔴 已下架",
    "sold_out": "🟡 售罄",
    "hidden": "⚪ 隐藏",
}

PRODUCT_KINDS = {
    "product": "🛒 商品",
    "report": "📝 报单",
}

TAGS = {
    "hot": "🔥 热销",
    "new": "🆕 新品",
    "limited": "⚡ 限时",
    "recommend": "⭐ 推荐",
}

PRIORITY = {
    "high": "🔴 加急",
    "normal": "🟡 普通",
    "low": "🟢 低优先级",
}

# ==================================================================
# 抢单模块（v2.6.0）：接单员 / 佣金 / 提现 / 申诉
# ==================================================================

# 接单员状态：active 在岗 / rest 请假 / suspended 停权 / blacklist 黑名单
GRABBER_STATUS = {
    "active": "🟢 在岗",
    "rest": "🟡 请假",
    "suspended": "🔴 停权",
    "blacklist": "⛔ 黑名单",
}

# 订单抢单状态机：'' 非派单 / waiting 待抢 / grabbed 已抢到进行中 / done 已回单完单
GRAB_STATUS = {
    "": "—",
    "waiting": "⚡ 待抢",
    "grabbed": "🔄 进行中",
    "done": "✅ 已完单",
}

# 佣金状态：frozen 冻结中 / released 已解冻（进可提现）/ clawed 已追回（退款扣回）
COMMISSION_STATUS = {
    "frozen": "🧊 冻结中",
    "released": "💰 已解冻",
    "clawed": "↩️ 已追回",
}

# 提现状态：pending 待审核 / paid 已打款 / rejected 已拒绝
WITHDRAW_STATUS = {
    "pending": "⏳ 待审核",
    "paid": "✅ 已打款",
    "rejected": "❌ 已拒绝",
}

# 申诉状态：pending 待裁定 / approved 支持 / rejected 驳回
APPEAL_STATUS = {
    "pending": "⏳ 待裁定",
    "approved": "✅ 支持",
    "rejected": "❌ 驳回",
}


def gen_withdraw_id() -> str:
    """提现单号：W_日期_随机段。"""
    return f"W_{datetime.now(LOCAL_TZ).strftime('%Y%m%d')}_{uuid.uuid4().hex[:6].upper()}"


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


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        # WAL 模式 + busy_timeout：读写并发更稳，避免偶发 database is locked
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def atomic(self):
        """事务上下文：块内所有写操作要么全部提交，要么全部回滚。

        涉及资金（积分扣减/退回/到账、库存变动、订单写入）的多步操作必须
        放在同一个 atomic 块里，防止中途崩溃造成钱货不一致。
        """
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _maybe_conn(self, conn: sqlite3.Connection | None):
        """有事务连接就复用（不提交不关闭），否则自己开一个（自动提交）。"""
        if conn is not None:
            yield conn
        else:
            own = self._get_conn()
            try:
                yield own
                own.commit()
            finally:
                own.close()

    def _init_db(self) -> None:
        conn = self._get_conn()
        try:
            # 检查 admins 表是否有 added_by 字段
            cursor = conn.execute("PRAGMA table_info(admins)")
            columns = [row[1] for row in cursor.fetchall()]
            if "added_by" not in columns:
                # 旧表结构，需要重建
                logger.info("检测到旧数据库结构，重建 admins 表")
                conn.execute("DROP TABLE IF EXISTS admins")
                conn.execute("""
                    CREATE TABLE admins (
                        user_id INTEGER PRIMARY KEY,
                        name TEXT DEFAULT '',
                        permissions TEXT DEFAULT '[]',
                        added_by INTEGER DEFAULT 0,
                        added_at TEXT DEFAULT ''
                    )
                """)
                conn.commit()

            # 检查 channels 表是否存在
            cursor = conn.execute("PRAGMA table_info(channels)")
            if not cursor.fetchall():
                conn.execute("""
                    CREATE TABLE channels (
                        id TEXT PRIMARY KEY,
                        chat_id TEXT DEFAULT '',
                        title TEXT DEFAULT '',
                        username TEXT DEFAULT '',
                        added_by INTEGER DEFAULT 0,
                        added_at TEXT DEFAULT ''
                    )
                """)
                conn.commit()

            # 创建其他表
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    last_name TEXT DEFAULT '',
                    points INTEGER DEFAULT 0,
                    total_recharge REAL DEFAULT 0,
                    total_spent INTEGER DEFAULT 0,
                    is_banned INTEGER DEFAULT 0,
                    invite_code TEXT DEFAULT '',
                    invited_by TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',
                    last_active TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS products (
                    id TEXT PRIMARY KEY,
                    name TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    points INTEGER DEFAULT 0,
                    kind TEXT DEFAULT 'product',
                    stock INTEGER DEFAULT -1,
                    limit_per_user INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'on',
                    is_top INTEGER DEFAULT 0,
                    tags TEXT DEFAULT '[]',
                    auto_delivery TEXT DEFAULT '',
                    photo_file_id TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    delivery_mode TEXT DEFAULT 'fixed',
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER DEFAULT 0,
                    username TEXT DEFAULT '',
                    product_id TEXT DEFAULT '',
                    product_name TEXT DEFAULT '',
                    product_kind TEXT DEFAULT 'product',
                    points INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    priority TEXT DEFAULT 'normal',
                    note TEXT DEFAULT '',
                    reply_content TEXT DEFAULT '',
                    reply_admin_id INTEGER DEFAULT 0,
                    reply_at TEXT DEFAULT '',
                    locked_by INTEGER DEFAULT 0,
                    stock_item_id INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT '',
                    completed_at TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS recharges (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER DEFAULT 0,
                    username TEXT DEFAULT '',
                    amount REAL DEFAULT 0,
                    points INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    pay_url TEXT DEFAULT '',
                    okpay_order_id TEXT DEFAULT '',
                    gateway TEXT DEFAULT 'okpay',
                    created_at TEXT DEFAULT '',
                    paid_at TEXT DEFAULT '',
                    chain TEXT DEFAULT '',
                    expected_amount TEXT DEFAULT '',
                    txid TEXT DEFAULT '',
                    memo TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS chain_txids (
                    txid TEXT PRIMARY KEY,
                    recharge_id TEXT DEFAULT '',
                    chain TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS order_notifications (
                    chat_id INTEGER,
                    message_id INTEGER,
                    order_id TEXT,
                    created_at TEXT DEFAULT '',
                    PRIMARY KEY (chat_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS point_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER DEFAULT 0,
                    change INTEGER DEFAULT 0,
                    balance_after INTEGER DEFAULT 0,
                    reason TEXT DEFAULT '',
                    operator_id INTEGER DEFAULT 0,
                    ref TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
                CREATE INDEX IF NOT EXISTS idx_orders_product ON orders(product_id);
                CREATE INDEX IF NOT EXISTS idx_point_history_user ON point_history(user_id);
                CREATE TABLE IF NOT EXISTS supply_posts (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER DEFAULT 0,
                    username TEXT DEFAULT '',
                    display_name TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    content_html TEXT DEFAULT '',
                    photo_file_id TEXT DEFAULT '',
                    channel TEXT DEFAULT 'bot',
                    cost INTEGER DEFAULT 0,
                    anonymous INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    bot_ok INTEGER DEFAULT 0,
                    channel_ok INTEGER DEFAULT 0,
                    channel_msg TEXT DEFAULT '',
                    refunded INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_supply_user ON supply_posts(user_id);
                CREATE INDEX IF NOT EXISTS idx_supply_status ON supply_posts(status, created_at);
                -- 库存池（v2.4.0）：一件货只卖一次，售出与订单绑定可追溯
                CREATE TABLE IF NOT EXISTS stock_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id TEXT NOT NULL,
                    content_type TEXT DEFAULT 'text',
                    content_text TEXT DEFAULT '',
                    file_ids TEXT DEFAULT '[]',
                    caption TEXT DEFAULT '',
                    status TEXT DEFAULT 'available',
                    order_id TEXT DEFAULT '',
                    buyer_id INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT '',
                    sold_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_stock_ps ON stock_items(product_id, status, id);
                CREATE INDEX IF NOT EXISTS idx_stock_order ON stock_items(order_id);
                -- 到货提醒订阅（v2.4.0）：售罄商品补货后自动私信订阅者，提醒后即删除
                CREATE TABLE IF NOT EXISTS restock_subs (
                    product_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at TEXT DEFAULT '',
                    PRIMARY KEY (product_id, user_id)
                );
                -- 管理员操作日志（v2.4.0）：多管理员协作可追溯
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER DEFAULT 0,
                    action TEXT DEFAULT '',
                    detail TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(id);
                -- 接单员档案（v2.6.0 抢单）：状态/余额/业绩一表管
                CREATE TABLE IF NOT EXISTS grabbers (
                    user_id INTEGER PRIMARY KEY,
                    seq INTEGER DEFAULT 0,
                    username TEXT DEFAULT '',
                    name TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    balance_available INTEGER DEFAULT 0,
                    balance_frozen INTEGER DEFAULT 0,
                    total_commission INTEGER DEFAULT 0,
                    completed_orders INTEGER DEFAULT 0,
                    timeout_orders INTEGER DEFAULT 0,
                    giveup_orders INTEGER DEFAULT 0,
                    cooldown_until TEXT DEFAULT '',
                    last_grab_at TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                );
                -- 佣金流水（v2.6.0）：冻结/解冻/追回全留痕
                CREATE TABLE IF NOT EXISTS grab_commissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT DEFAULT '',
                    grabber_id INTEGER DEFAULT 0,
                    amount INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'frozen',
                    frozen_until TEXT DEFAULT '',
                    released_at TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_gc_grabber ON grab_commissions(grabber_id, status);
                CREATE INDEX IF NOT EXISTS idx_gc_order ON grab_commissions(order_id);
                -- 提现单（v2.6.0）：申请/审核/打款留痕
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id TEXT PRIMARY KEY,
                    grabber_id INTEGER DEFAULT 0,
                    amount INTEGER DEFAULT 0,
                    address TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    txid TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    processed_by INTEGER DEFAULT 0,
                    processed_at TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_wd_grabber ON withdrawals(grabber_id, status);
                -- 申诉（v2.6.0）：接单员异议与管理员裁定
                CREATE TABLE IF NOT EXISTS grab_appeals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    grabber_id INTEGER DEFAULT 0,
                    order_id TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    reply TEXT DEFAULT '',
                    handled_by INTEGER DEFAULT 0,
                    handled_at TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_ga_status ON grab_appeals(status, id);
            """)
            conn.commit()

            # 迁移：老库的 recharges 表补 gateway 列（支付渠道，老数据默认 okpay）。
            # CREATE TABLE IF NOT EXISTS 不会给已存在的表加列，必须单独 ALTER。
            cursor = conn.execute("PRAGMA table_info(recharges)")
            rc_columns = [row[1] for row in cursor.fetchall()]
            if rc_columns and "gateway" not in rc_columns:
                logger.info("检测到旧 recharges 表结构，补充 gateway 列")
                conn.execute("ALTER TABLE recharges ADD COLUMN gateway TEXT DEFAULT 'okpay'")
                conn.commit()

            # 迁移：老库的 supply_posts 表补 content_html 列（v2.3.0 富文本，
            # 老数据该列为空串，发送时自动走「转义纯文本」的旧逻辑，完全兼容）。
            cursor = conn.execute("PRAGMA table_info(supply_posts)")
            sp_columns = [row[1] for row in cursor.fetchall()]
            if sp_columns and "content_html" not in sp_columns:
                logger.info("检测到旧 supply_posts 表结构，补充 content_html 列")
                conn.execute("ALTER TABLE supply_posts ADD COLUMN content_html TEXT DEFAULT ''")
                conn.commit()

            # 迁移：老库 products 表补 category / delivery_mode 列（v2.4.0 商品分类与库存池，
            # 老数据默认 无分类 + fixed 固定发货，行为与旧版完全一致）。
            cursor = conn.execute("PRAGMA table_info(products)")
            pr_columns = [row[1] for row in cursor.fetchall()]
            if pr_columns and "category" not in pr_columns:
                logger.info("检测到旧 products 表结构，补充 category 列")
                conn.execute("ALTER TABLE products ADD COLUMN category TEXT DEFAULT ''")
                conn.commit()
            if pr_columns and "delivery_mode" not in pr_columns:
                logger.info("检测到旧 products 表结构，补充 delivery_mode 列")
                conn.execute("ALTER TABLE products ADD COLUMN delivery_mode TEXT DEFAULT 'fixed'")
                conn.commit()

            # 迁移：老库 orders 表补 stock_item_id 列（v2.4.0 库存池发货绑定，0=非池商品）。
            cursor = conn.execute("PRAGMA table_info(orders)")
            od_columns = [row[1] for row in cursor.fetchall()]
            if od_columns and "stock_item_id" not in od_columns:
                logger.info("检测到旧 orders 表结构，补充 stock_item_id 列")
                conn.execute("ALTER TABLE orders ADD COLUMN stock_item_id INTEGER DEFAULT 0")
                conn.commit()

            # 迁移：老库 orders 表补抢单十一列（v2.6.0，老数据全为空/0=非派单订单，行为不变）。
            cursor = conn.execute("PRAGMA table_info(orders)")
            od_cols = [row[1] for row in cursor.fetchall()]
            if od_cols:
                for col, ddl in (
                    ("grab_status", "ALTER TABLE orders ADD COLUMN grab_status TEXT DEFAULT ''"),
                    ("grabber_id", "ALTER TABLE orders ADD COLUMN grabber_id INTEGER DEFAULT 0"),
                    ("grabber_name", "ALTER TABLE orders ADD COLUMN grabber_name TEXT DEFAULT ''"),
                    ("commission", "ALTER TABLE orders ADD COLUMN commission INTEGER DEFAULT 0"),
                    ("grab_at", "ALTER TABLE orders ADD COLUMN grab_at TEXT DEFAULT ''"),
                    ("deadline", "ALTER TABLE orders ADD COLUMN deadline TEXT DEFAULT ''"),
                    ("dispatch_chat", "ALTER TABLE orders ADD COLUMN dispatch_chat TEXT DEFAULT ''"),
                    ("dispatch_msg", "ALTER TABLE orders ADD COLUMN dispatch_msg INTEGER DEFAULT 0"),
                    ("dispatch_count", "ALTER TABLE orders ADD COLUMN dispatch_count INTEGER DEFAULT 0"),
                    ("last_dispatch_at", "ALTER TABLE orders ADD COLUMN last_dispatch_at TEXT DEFAULT ''"),
                    ("warn_sent", "ALTER TABLE orders ADD COLUMN warn_sent INTEGER DEFAULT 0"),
                ):
                    if col not in od_cols:
                        logger.info("检测到旧 orders 表结构，补充 %s 列", col)
                        conn.execute(ddl)
                        conn.commit()

            # 迁移：老库 products 表补 commission_pct 列（v2.6.0 抢单佣金比例，-1=跟随全局）。
            cursor = conn.execute("PRAGMA table_info(products)")
            pr_cols = [row[1] for row in cursor.fetchall()]
            if pr_cols and "commission_pct" not in pr_cols:
                logger.info("检测到旧 products 表结构，补充 commission_pct 列")
                conn.execute("ALTER TABLE products ADD COLUMN commission_pct INTEGER DEFAULT -1")
                conn.commit()

            # 迁移：老库 grabbers 表补专属入群链接两列（v2.6.1）。
            cursor = conn.execute("PRAGMA table_info(grabbers)")
            gb_cols = [row[1] for row in cursor.fetchall()]
            if gb_cols:
                for col, ddl in (
                    ("invite_link", "ALTER TABLE grabbers ADD COLUMN invite_link TEXT DEFAULT ''"),
                    ("joined_group", "ALTER TABLE grabbers ADD COLUMN joined_group INTEGER DEFAULT 0"),
                ):
                    if col not in gb_cols:
                        logger.info("检测到旧 grabbers 表结构，补充 %s 列", col)
                        conn.execute(ddl)
                        conn.commit()

            # 迁移：老库 withdrawals 表补 amount_usdt 列（v2.6.1，提现按汇率自动折算 USDT）。
            cursor = conn.execute("PRAGMA table_info(withdrawals)")
            wd_cols = [row[1] for row in cursor.fetchall()]
            if wd_cols and "amount_usdt" not in wd_cols:
                logger.info("检测到旧 withdrawals 表结构，补充 amount_usdt 列")
                conn.execute("ALTER TABLE withdrawals ADD COLUMN amount_usdt REAL DEFAULT 0")
                conn.commit()

            # 迁移：老库 recharges 表补冷钱包四列（v2.5.0，老数据全部为空串，行为不变）。
            cursor = conn.execute("PRAGMA table_info(recharges)")
            rc_cols = [row[1] for row in cursor.fetchall()]
            if rc_cols:
                for col, ddl in (
                    ("chain", "ALTER TABLE recharges ADD COLUMN chain TEXT DEFAULT ''"),
                    ("expected_amount", "ALTER TABLE recharges ADD COLUMN expected_amount TEXT DEFAULT ''"),
                    ("txid", "ALTER TABLE recharges ADD COLUMN txid TEXT DEFAULT ''"),
                    ("memo", "ALTER TABLE recharges ADD COLUMN memo TEXT DEFAULT ''"),
                ):
                    if col not in rc_cols:
                        logger.info("检测到旧 recharges 表结构，补充 %s 列", col)
                        conn.execute(ddl)
                        conn.commit()
            # 链上交易防重表（txid 主键，天然幂等）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chain_txids (
                    txid TEXT PRIMARY KEY,
                    recharge_id TEXT DEFAULT '',
                    chain TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                )
            """)
            conn.commit()
        finally:
            conn.close()

    # ---------- 用户 ----------

    def get_user(self, user_id: int, conn: sqlite3.Connection | None = None) -> User | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return User.from_dict(dict(row))
            return None

    def save_user(self, user: User, conn: sqlite3.Connection | None = None) -> None:
        d = user.to_dict()
        with self._maybe_conn(conn) as c:
            c.execute("""
                INSERT INTO users (user_id, username, first_name, last_name, points, total_recharge, total_spent, is_banned, invite_code, invited_by, created_at, last_active)
                VALUES (:user_id, :username, :first_name, :last_name, :points, :total_recharge, :total_spent, :is_banned, :invite_code, :invited_by, :created_at, :last_active)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    points=excluded.points,
                    total_recharge=excluded.total_recharge,
                    total_spent=excluded.total_spent,
                    is_banned=excluded.is_banned,
                    invite_code=excluded.invite_code,
                    invited_by=excluded.invited_by,
                    created_at=excluded.created_at,
                    last_active=excluded.last_active
            """, d)

    def list_users(self, limit: int = 50, offset: int = 0) -> list[User]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [User.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    # ---------- 商品 ----------

    def get_product(self, product_id: str, conn: sqlite3.Connection | None = None) -> Product | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if row:
                return Product.from_dict(dict(row))
            return None

    def save_product(self, product: Product, conn: sqlite3.Connection | None = None) -> None:
        product.updated_at = now_iso()
        d = product.to_dict()
        with self._maybe_conn(conn) as c:
            c.execute("""
                INSERT INTO products (id, name, description, points, kind, stock, limit_per_user, status, is_top, tags, auto_delivery, photo_file_id, category, delivery_mode, commission_pct, created_at, updated_at)
                VALUES (:id, :name, :description, :points, :kind, :stock, :limit_per_user, :status, :is_top, :tags, :auto_delivery, :photo_file_id, :category, :delivery_mode, :commission_pct, :created_at, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    points=excluded.points,
                    kind=excluded.kind,
                    stock=excluded.stock,
                    limit_per_user=excluded.limit_per_user,
                    status=excluded.status,
                    is_top=excluded.is_top,
                    tags=excluded.tags,
                    auto_delivery=excluded.auto_delivery,
                    photo_file_id=excluded.photo_file_id,
                    category=excluded.category,
                    delivery_mode=excluded.delivery_mode,
                    commission_pct=excluded.commission_pct,
                    updated_at=excluded.updated_at
            """, d)

    def delete_product(self, product_id: str) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
        finally:
            conn.close()

    def list_products(
        self,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        category: str | None = None,
    ) -> list[Product]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM products WHERE 1=1"
            params: list[Any] = []
            if kind:
                query += " AND kind = ?"
                params.append(kind)
            if status:
                query += " AND status = ?"
                params.append(status)
            if category:
                query += " AND category = ?"
                params.append(category)
            query += " ORDER BY is_top DESC, created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
            return [Product.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def search_products(
        self, keyword: str, kind: str | None = None, status: str | None = None, limit: int = 15
    ) -> list[Product]:
        """商品搜索（v2.4.0）：名称/介绍/分类模糊匹配，用户端传 status='on' 只搜在售。"""
        conn = self._get_conn()
        try:
            query = "SELECT * FROM products WHERE (name LIKE ? OR description LIKE ? OR category LIKE ?)"
            like = f"%{keyword}%"
            params: list[Any] = [like, like, like]
            if kind:
                query += " AND kind = ?"
                params.append(kind)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY is_top DESC, created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [Product.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def search_orders(self, keyword: str, limit: int = 15) -> list[Order]:
        """订单搜索（v2.4.0 管理端）：订单号精确/前缀、用户名模糊、商品名模糊。"""
        conn = self._get_conn()
        try:
            like = f"%{keyword}%"
            rows = conn.execute(
                "SELECT * FROM orders WHERE id LIKE ? OR username LIKE ? OR product_name LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
            return [Order.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    # ---------- 订单 ----------

    def get_order(self, order_id: str, conn: sqlite3.Connection | None = None) -> Order | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if row:
                return Order.from_dict(dict(row))
            return None

    def save_order(self, order: Order, conn: sqlite3.Connection | None = None) -> None:
        d = order.to_dict()
        with self._maybe_conn(conn) as c:
            c.execute("""
                INSERT INTO orders (id, user_id, username, product_id, product_name, product_kind, points, status, priority, note, reply_content, reply_admin_id, reply_at, locked_by, stock_item_id, created_at, completed_at, grab_status, grabber_id, grabber_name, commission, grab_at, deadline, dispatch_chat, dispatch_msg, dispatch_count, last_dispatch_at, warn_sent)
                VALUES (:id, :user_id, :username, :product_id, :product_name, :product_kind, :points, :status, :priority, :note, :reply_content, :reply_admin_id, :reply_at, :locked_by, :stock_item_id, :created_at, :completed_at, :grab_status, :grabber_id, :grabber_name, :commission, :grab_at, :deadline, :dispatch_chat, :dispatch_msg, :dispatch_count, :last_dispatch_at, :warn_sent)
                ON CONFLICT(id) DO UPDATE SET
                    user_id=excluded.user_id,
                    username=excluded.username,
                    product_id=excluded.product_id,
                    product_name=excluded.product_name,
                    product_kind=excluded.product_kind,
                    points=excluded.points,
                    status=excluded.status,
                    priority=excluded.priority,
                    note=excluded.note,
                    reply_content=excluded.reply_content,
                    reply_admin_id=excluded.reply_admin_id,
                    reply_at=excluded.reply_at,
                    locked_by=excluded.locked_by,
                    stock_item_id=excluded.stock_item_id,
                    completed_at=excluded.completed_at,
                    grab_status=excluded.grab_status,
                    grabber_id=excluded.grabber_id,
                    grabber_name=excluded.grabber_name,
                    commission=excluded.commission,
                    grab_at=excluded.grab_at,
                    deadline=excluded.deadline,
                    dispatch_chat=excluded.dispatch_chat,
                    dispatch_msg=excluded.dispatch_msg,
                    dispatch_count=excluded.dispatch_count,
                    last_dispatch_at=excluded.last_dispatch_at,
                    warn_sent=excluded.warn_sent
            """, d)

    def count_user_product_orders(
        self, user_id: int, product_id: str, conn: sqlite3.Connection | None = None
    ) -> int:
        """该用户对某商品的有效购买次数（取消/退款不计），用于限购校验。

        用 SQL 直接 COUNT，无论历史订单多少条都数得准。
        """
        with self._maybe_conn(conn) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM orders WHERE user_id = ? AND product_id = ? AND status NOT IN ('cancelled', 'refunded')",
                (user_id, product_id),
            ).fetchone()
            return row[0] if row else 0

    def count_user_orders(self, user_id: int) -> int:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (user_id,)).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def list_orders(
        self,
        user_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Order]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM orders WHERE 1=1"
            params: list[Any] = []
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
            return [Order.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def count_orders(self, status: str | None = None) -> int:
        conn = self._get_conn()
        try:
            if status:
                row = conn.execute("SELECT COUNT(*) FROM orders WHERE status = ?", (status,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM orders").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    # ---------- 充值记录 ----------

    def get_recharge(self, recharge_id: str, conn: sqlite3.Connection | None = None) -> Recharge | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM recharges WHERE id = ?", (recharge_id,)).fetchone()
            if row:
                return Recharge.from_dict(dict(row))
            return None

    def save_recharge(self, recharge: Recharge, conn: sqlite3.Connection | None = None) -> None:
        d = recharge.to_dict()
        with self._maybe_conn(conn) as c:
            c.execute("""
                INSERT INTO recharges (id, user_id, username, amount, points, status, pay_url, okpay_order_id, gateway, created_at, paid_at, chain, expected_amount, txid, memo)
                VALUES (:id, :user_id, :username, :amount, :points, :status, :pay_url, :okpay_order_id, :gateway, :created_at, :paid_at, :chain, :expected_amount, :txid, :memo)
                ON CONFLICT(id) DO UPDATE SET
                    user_id=excluded.user_id,
                    username=excluded.username,
                    amount=excluded.amount,
                    points=excluded.points,
                    status=excluded.status,
                    pay_url=excluded.pay_url,
                    okpay_order_id=excluded.okpay_order_id,
                    gateway=excluded.gateway,
                    paid_at=excluded.paid_at,
                    chain=excluded.chain,
                    expected_amount=excluded.expected_amount,
                    txid=excluded.txid,
                    memo=excluded.memo
            """, d)

    def list_recharges(self, user_id: int | None = None, status: str | None = None, limit: int = 50) -> list[Recharge]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM recharges WHERE 1=1"
            params: list[Any] = []
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [Recharge.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    # ---------- 管理员 ----------

    def get_admin(self, user_id: int) -> Admin | None:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM admins WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return Admin.from_dict(dict(row))
            return None
        finally:
            conn.close()

    def save_admin(self, admin: Admin) -> None:
        conn = self._get_conn()
        try:
            d = admin.to_dict()
            conn.execute("""
                INSERT INTO admins (user_id, name, permissions, added_by, added_at)
                VALUES (:user_id, :name, :permissions, :added_by, :added_at)
                ON CONFLICT(user_id) DO UPDATE SET
                    name=excluded.name,
                    permissions=excluded.permissions,
                    added_by=excluded.added_by,
                    added_at=excluded.added_at
            """, d)
            conn.commit()
        finally:
            conn.close()

    def delete_admin(self, user_id: int) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()

    def list_admins(self) -> list[Admin]:
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM admins ORDER BY added_at").fetchall()
            return [Admin.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def is_admin(self, user_id: int) -> bool:
        return self.get_admin(user_id) is not None

    # ---------- 频道 ----------

    def get_channel(self, channel_id: str) -> Channel | None:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
            if row:
                return Channel.from_dict(dict(row))
            return None
        finally:
            conn.close()

    def save_channel(self, channel: Channel) -> None:
        conn = self._get_conn()
        try:
            d = channel.to_dict()
            conn.execute("""
                INSERT INTO channels (id, chat_id, title, username, added_by, added_at)
                VALUES (:id, :chat_id, :title, :username, :added_by, :added_at)
                ON CONFLICT(id) DO UPDATE SET
                    chat_id=excluded.chat_id,
                    title=excluded.title,
                    username=excluded.username,
                    added_by=excluded.added_by,
                    added_at=excluded.added_at
            """, d)
            conn.commit()
        finally:
            conn.close()

    def delete_channel(self, channel_id: str) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
            conn.commit()
        finally:
            conn.close()

    def list_channels(self) -> list[Channel]:
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM channels ORDER BY added_at").fetchall()
            return [Channel.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    # ---------- 系统设置 ----------

    def get_setting(self, key: str, default: Any = None) -> Any:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row:
                return json.loads(row[0])
            return default
        finally:
            conn.close()

    def set_setting(self, key: str, value: Any) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()

    def get_settings(self) -> dict[str, Any]:
        return {
            "welcome_text": self.get_setting("welcome_text", "👋 欢迎光临"),
            # 欢迎语富文本版（text_html 生成，含加粗/链接/高级会员表情）；
            # 空串表示老数据，展示时自动回退到转义 welcome_text
            "welcome_text_html": self.get_setting("welcome_text_html", ""),
            "welcome_image": self.get_setting("welcome_image", ""),
            "points_rate": self.get_setting("points_rate", 10),
            "min_recharge_usdt": self.get_setting("min_recharge_usdt", 1),
            # ---------- 支付设置（v2.5.0）：渠道开关 / 冷钱包地址与Key / 单笔上限 ----------
            "max_recharge_usdt": self.get_setting("max_recharge_usdt", 1000),
            "pay_okpay_enabled": self.get_setting("pay_okpay_enabled", True),
            "pay_kkpay_enabled": self.get_setting("pay_kkpay_enabled", True),
            "pay_tron_enabled": self.get_setting("pay_tron_enabled", False),
            "pay_ton_enabled": self.get_setting("pay_ton_enabled", False),
            "tron_wallet_address": self.get_setting("tron_wallet_address", ""),
            "ton_wallet_address": self.get_setting("ton_wallet_address", ""),
            "trongrid_api_key": self.get_setting("trongrid_api_key", ""),
            "toncenter_api_key": self.get_setting("toncenter_api_key", ""),
            "maintenance_mode": self.get_setting("maintenance_mode", False),
            "order_expire_minutes": self.get_setting("order_expire_minutes", 30),
            "force_channels": self.get_setting("force_channels", False),
            # ---------- 付费供需 ----------
            "supply_price_bot": self.get_setting("supply_price_bot", 10),
            "supply_price_channel": self.get_setting("supply_price_channel", 10),
            "supply_discount": self.get_setting("supply_discount", 10),
            "supply_daily_limit": self.get_setting("supply_daily_limit", 5),
            "supply_max_len": self.get_setting("supply_max_len", 600),
            "supply_channel_id": self.get_setting("supply_channel_id", ""),
            "supply_categories": self.get_setting("supply_categories", []),
            # ---------- 供需开关（v2.3.0，管理端「开关面板」逐项切换） ----------
            "supply_paused": self.get_setting("supply_paused", False),        # 停止发布（大厅仍可逛）
            "supply_review": self.get_setting("supply_review", False),        # 人工审核：先扣费待审，通过后发布
            "supply_hall_open": self.get_setting("supply_hall_open", True),   # 大厅是否开放浏览
            "supply_anonymous": self.get_setting("supply_anonymous", True),   # 是否允许匿名发布
            # ---------- 商品分类（v2.4.0） ----------
            "product_categories": self.get_setting("product_categories", []),
            # ---------- 每日定时任务（v2.4.0）：经营日报 + 自动备份 ----------
            "daily_report_enabled": self.get_setting("daily_report_enabled", True),   # 每天定时推送昨日经营日报
            "daily_backup_enabled": self.get_setting("daily_backup_enabled", True),   # 每天定时自动备份数据库发管理员
            "daily_task_hour": self.get_setting("daily_task_hour", 8),                # 触发时刻（东八区，0~23 点）
            # ---------- 抢单派单（v2.6.0）：匿名派单到接单群，接单员抢单回单 ----------
            "grab_enabled": self.get_setting("grab_enabled", False),              # 抢单总开关（开=报单类订单自动派到接单群）
            "grab_chat_id": self.get_setting("grab_chat_id", ""),                 # 接单群 ID（私密群，负号数字串）
            "grab_commission_pct": self.get_setting("grab_commission_pct", 20),   # 全局佣金比例%（按订单积分；商品可单独覆盖）
            "grab_freeze_hours": self.get_setting("grab_freeze_hours", 24),       # 佣金冻结小时数（0=完单立即到账）
            "grab_timeout_hours": self.get_setting("grab_timeout_hours", 24),     # 最晚回单时限（小时，从抢中时刻起算）
            "grab_warn_minutes": self.get_setting("grab_warn_minutes", 30),       # 超时前多少分钟提醒接单员
            "grab_cooldown_seconds": self.get_setting("grab_cooldown_seconds", 5),  # 抢单冷却（秒，防连点）
            "grab_penalty_minutes": self.get_setting("grab_penalty_minutes", 60), # 超时/放弃惩罚冷静期（分钟，期间禁止抢单）
            "grab_max_dispatch": self.get_setting("grab_max_dispatch", 3),        # 单笔最大重派次数（超时释放自动重派）
            "grab_withdraw_min": self.get_setting("grab_withdraw_min", 100),      # 最低提现积分
            "grab_points_per_usdt": self.get_setting("grab_points_per_usdt", 100),  # 积分兑U汇率（多少积分=1 USDT，提现自动换算）
            "grab_auto_release": self.get_setting("grab_auto_release", True),     # 完单后佣金到点自动解冻
            "grab_notify_admin": self.get_setting("grab_notify_admin", True),     # 抢中/回单/超时是否通知管理员
        }

    def update_settings(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            self.set_setting(key, value)

    # ---------- 订单通知映射（管理员引用回复回单） ----------

    def save_order_notification(self, chat_id: int, message_id: int, order_id: str) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO order_notifications (chat_id, message_id, order_id, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, message_id, order_id, now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_order_id_by_notification(self, chat_id: int, message_id: int) -> str | None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT order_id FROM order_notifications WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    # ---------- 搜索与统计 ----------

    def search_users(self, query: str, limit: int = 20) -> list[User]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM users WHERE CAST(user_id AS TEXT) LIKE ? OR username LIKE ? OR first_name LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            return [User.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def count_users(self) -> int:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def list_pending_recharges(self, limit: int = 200) -> list[Recharge]:
        return self.list_recharges(status="pending", limit=limit)

    # ---------- 链上交易防重（v2.5.0 冷钱包：同一 txid 绝不二次入账） ----------

    def chain_txid_seen(self, txid: str) -> bool:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT 1 FROM chain_txids WHERE txid = ?", (txid,)).fetchone()
            return row is not None
        finally:
            conn.close()

    def add_chain_txid(self, txid: str, recharge_id: str, chain: str,
                       conn: sqlite3.Connection | None = None) -> None:
        """记录已入账的链上交易哈希；INSERT OR IGNORE 保证并发/重放下不炸不重复。"""
        with self._maybe_conn(conn) as c:
            c.execute(
                "INSERT OR IGNORE INTO chain_txids (txid, recharge_id, chain, created_at) VALUES (?, ?, ?, ?)",
                (txid, recharge_id, chain, now_iso()),
            )

    # ---------- 积分流水 ----------

    def add_point_record(
        self,
        user_id: int,
        change: int,
        balance_after: int,
        reason: str,
        operator_id: int = 0,
        ref: str = "",
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """记一笔积分变动流水。必须与实际积分变动处于同一事务。"""
        with self._maybe_conn(conn) as c:
            c.execute(
                "INSERT INTO point_history (user_id, change, balance_after, reason, operator_id, ref, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, change, balance_after, reason, operator_id, ref, now_iso()),
            )

    def list_point_records(self, user_id: int, limit: int = 20) -> list[PointRecord]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM point_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [PointRecord.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    # ---------- 付费供需 ----------

    def save_supply_post(self, post: SupplyPost, conn: sqlite3.Connection | None = None) -> None:
        d = post.to_dict()
        d["anonymous"] = int(post.anonymous)
        d["bot_ok"] = int(post.bot_ok)
        d["channel_ok"] = int(post.channel_ok)
        d["refunded"] = int(post.refunded)
        with self._maybe_conn(conn) as c:
            c.execute(
                "INSERT OR REPLACE INTO supply_posts (id, user_id, username, display_name, category, content, "
                "content_html, photo_file_id, channel, cost, anonymous, status, bot_ok, channel_ok, channel_msg, refunded, created_at) "
                "VALUES (:id, :user_id, :username, :display_name, :category, :content, :content_html, :photo_file_id, :channel, "
                ":cost, :anonymous, :status, :bot_ok, :channel_ok, :channel_msg, :refunded, :created_at)",
                d,
            )

    def get_supply_post(self, post_id: str, conn: sqlite3.Connection | None = None) -> SupplyPost | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM supply_posts WHERE id = ?", (post_id,)).fetchone()
            return SupplyPost.from_dict(dict(row)) if row else None

    def list_supply_posts(
        self,
        status: str | None = "active",
        category: str | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> list[SupplyPost]:
        sql = "SELECT * FROM supply_posts WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        conn = self._get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [SupplyPost.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def count_supply_posts(self, status: str | None = "active", category: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM supply_posts WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if category:
            sql += " AND category = ?"
            params.append(category)
        conn = self._get_conn()
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def count_user_supply_posts(self, user_id: int, since_iso: str | None = None) -> int:
        """用户累计发帖数；传 since_iso 则只算该时间之后（用于每日上限）。"""
        sql = "SELECT COUNT(*) FROM supply_posts WHERE user_id = ?"
        params: list[Any] = [user_id]
        if since_iso:
            sql += " AND created_at >= ?"
            params.append(since_iso)
        conn = self._get_conn()
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def supply_stats(self, today_prefix: str) -> dict[str, Any]:
        """供需统计：今日/累计的发帖数与积分收入、渠道分布。today_prefix 如 '2026-09-07'。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt, COALESCE(SUM(cost), 0) AS income FROM supply_posts "
                "WHERE created_at >= ?",
                (today_prefix,),
            ).fetchone()
            today_cnt, today_income = row[0], row[1]
            row = conn.execute(
                "SELECT COUNT(*) AS cnt, COALESCE(SUM(cost), 0) AS income FROM supply_posts"
            ).fetchone()
            total_cnt, total_income = row[0], row[1]
            channels = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT channel, COUNT(*) FROM supply_posts GROUP BY channel"
                ).fetchall()
            }
            return {
                "today_cnt": today_cnt,
                "today_income": today_income,
                "total_cnt": total_cnt,
                "total_income": total_income,
                "channels": channels,
            }
        finally:
            conn.close()

    # ---------- 库存池（v2.4.0） ----------

    def add_stock_item(
        self,
        product_id: str,
        content_type: str,
        content_text: str = "",
        file_ids: list[str] | None = None,
        caption: str = "",
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """入库一件货，返回自增 id（即「编号」）。"""
        with self._maybe_conn(conn) as c:
            cur = c.execute(
                "INSERT INTO stock_items (product_id, content_type, content_text, file_ids, caption, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'available', ?)",
                (
                    product_id,
                    content_type,
                    content_text,
                    json.dumps(file_ids or [], ensure_ascii=False),
                    caption,
                    now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def get_stock_item(self, item_id: int, conn: sqlite3.Connection | None = None) -> StockItem | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM stock_items WHERE id = ?", (item_id,)).fetchone()
            return StockItem.from_dict(dict(row)) if row else None

    def list_stock_items(
        self,
        product_id: str,
        status: str | None = "available",
        limit: int = 50,
        offset: int = 0,
    ) -> list[StockItem]:
        conn = self._get_conn()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM stock_items WHERE product_id = ? AND status = ? ORDER BY id LIMIT ? OFFSET ?",
                    (product_id, status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM stock_items WHERE product_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (product_id, limit, offset),
                ).fetchall()
            return [StockItem.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def count_stock_items(self, product_id: str, status: str | None = "available") -> int:
        conn = self._get_conn()
        try:
            if status:
                row = conn.execute(
                    "SELECT COUNT(*) FROM stock_items WHERE product_id = ? AND status = ?",
                    (product_id, status),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM stock_items WHERE product_id = ?", (product_id,)
                ).fetchone()
            return int(row[0])
        finally:
            conn.close()

    def take_stock_item(
        self, product_id: str, order_id: str, buyer_id: int, conn: sqlite3.Connection
    ) -> StockItem | None:
        """原子取货（必须在 atomic 事务内调用）：锁定池中最早一件可用货并标记售出。

        UPDATE ... WHERE status='available' 自带条件竞争防护——两个并发事务里只有一个
        能改到同一行（另一事务在 BEGIN IMMEDIATE 阶段已被串行化），不会超卖。
        """
        row = conn.execute(
            "SELECT id FROM stock_items WHERE product_id = ? AND status = 'available' ORDER BY id LIMIT 1",
            (product_id,),
        ).fetchone()
        if not row:
            return None
        item_id = int(row[0])
        cur = conn.execute(
            "UPDATE stock_items SET status = 'sold', order_id = ?, buyer_id = ?, sold_at = ? "
            "WHERE id = ? AND status = 'available'",
            (order_id, buyer_id, now_iso(), item_id),
        )
        if cur.rowcount != 1:
            return None
        return self.get_stock_item(item_id, conn=conn)

    def release_stock_item(self, item_id: int, conn: sqlite3.Connection | None = None) -> None:
        """发货失败回滚：货退回池（清掉订单绑定），积分退款由调用方另行处理。"""
        with self._maybe_conn(conn) as c:
            c.execute(
                "UPDATE stock_items SET status = 'available', order_id = '', buyer_id = 0, sold_at = '' WHERE id = ?",
                (item_id,),
            )

    def delete_stock_item(self, item_id: int) -> bool:
        conn = self._get_conn()
        try:
            cur = conn.execute("DELETE FROM stock_items WHERE id = ?", (item_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def clear_sold_stock_items(self, product_id: str) -> int:
        """清空某商品已售记录，返回清理条数。"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "DELETE FROM stock_items WHERE product_id = ? AND status = 'sold'", (product_id,)
            )
            conn.commit()
            return int(cur.rowcount)
        finally:
            conn.close()

    def stock_pool_stats(self) -> dict[str, int]:
        """全站库存池概览（财务页用）：在售件数/已售件数/池模式商品数。"""
        conn = self._get_conn()
        try:
            avail = conn.execute(
                "SELECT COUNT(*) FROM stock_items WHERE status = 'available'"
            ).fetchone()[0]
            sold = conn.execute("SELECT COUNT(*) FROM stock_items WHERE status = 'sold'").fetchone()[0]
            pool_products = conn.execute(
                "SELECT COUNT(*) FROM products WHERE delivery_mode = 'pool'"
            ).fetchone()[0]
            return {"available": int(avail), "sold": int(sold), "pool_products": int(pool_products)}
        finally:
            conn.close()

    # ---------- 到货提醒订阅（v2.4.0） ----------

    def add_restock_sub(self, product_id: str, user_id: int) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO restock_subs (product_id, user_id, created_at) VALUES (?, ?, ?)",
                (product_id, user_id, now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def remove_restock_sub(self, product_id: str, user_id: int) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "DELETE FROM restock_subs WHERE product_id = ? AND user_id = ?",
                (product_id, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def has_restock_sub(self, product_id: str, user_id: int) -> bool:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM restock_subs WHERE product_id = ? AND user_id = ?",
                (product_id, user_id),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def pop_restock_subscribers(self, product_id: str) -> list[int]:
        """取出并清空某商品的全部订阅者（提醒一次即退订，不骚扰）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT user_id FROM restock_subs WHERE product_id = ?", (product_id,)
            ).fetchall()
            conn.execute("DELETE FROM restock_subs WHERE product_id = ?", (product_id,))
            conn.commit()
            return [int(r[0]) for r in rows]
        finally:
            conn.close()

    # ---------- 管理员操作日志（v2.4.0） ----------

    def add_audit_log(self, admin_id: int, action: str, detail: str = "") -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO audit_logs (admin_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
                (admin_id, action, detail[:500], now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def list_audit_logs(self, limit: int = 15, offset: int = 0) -> list[AuditLog]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
            return [AuditLog.from_row(r) for r in rows]
        finally:
            conn.close()

    def count_audit_logs(self) -> int:
        conn = self._get_conn()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0])
        finally:
            conn.close()

    # ==================================================================
    # 抢单模块（v2.6.0）：接单员档案 / 佣金流水 / 提现单 / 申诉单 / 派单查询
    # ==================================================================

    # ---------- 接单员档案 ----------

    def save_grabber(self, g: Grabber, conn: sqlite3.Connection | None = None) -> None:
        g.updated_at = now_iso()
        if not g.created_at:
            g.created_at = g.updated_at
        with self._maybe_conn(conn) as c:
            c.execute("""
                INSERT INTO grabbers (user_id, seq, username, name, status, balance_available, balance_frozen,
                                      total_commission, completed_orders, timeout_orders, giveup_orders,
                                      cooldown_until, last_grab_at, invite_link, joined_group,
                                      created_at, updated_at)
                VALUES (:user_id, :seq, :username, :name, :status, :balance_available, :balance_frozen,
                        :total_commission, :completed_orders, :timeout_orders, :giveup_orders,
                        :cooldown_until, :last_grab_at, :invite_link, :joined_group,
                        :created_at, :updated_at)
                ON CONFLICT(user_id) DO UPDATE SET
                    seq=excluded.seq,
                    username=excluded.username,
                    name=excluded.name,
                    status=excluded.status,
                    balance_available=excluded.balance_available,
                    balance_frozen=excluded.balance_frozen,
                    total_commission=excluded.total_commission,
                    completed_orders=excluded.completed_orders,
                    timeout_orders=excluded.timeout_orders,
                    giveup_orders=excluded.giveup_orders,
                    cooldown_until=excluded.cooldown_until,
                    last_grab_at=excluded.last_grab_at,
                    invite_link=excluded.invite_link,
                    joined_group=excluded.joined_group,
                    updated_at=excluded.updated_at
            """, {
                "user_id": g.user_id, "seq": g.seq, "username": g.username, "name": g.name,
                "status": g.status, "balance_available": g.balance_available, "balance_frozen": g.balance_frozen,
                "total_commission": g.total_commission, "completed_orders": g.completed_orders,
                "timeout_orders": g.timeout_orders, "giveup_orders": g.giveup_orders,
                "cooldown_until": g.cooldown_until, "last_grab_at": g.last_grab_at,
                "invite_link": g.invite_link, "joined_group": g.joined_group,
                "created_at": g.created_at, "updated_at": g.updated_at,
            })

    def get_grabber(self, user_id: int, conn: sqlite3.Connection | None = None) -> Grabber | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM grabbers WHERE user_id = ?", (user_id,)).fetchone()
            return Grabber.from_dict(dict(row)) if row else None

    def get_grabber_by_invite(self, invite_link: str, conn: sqlite3.Connection | None = None) -> Grabber | None:
        """按专属入群链接反查接单员（进群归属判定用）。"""
        if not invite_link:
            return None
        with self._maybe_conn(conn) as c:
            row = c.execute(
                "SELECT * FROM grabbers WHERE invite_link = ?", (invite_link,)
            ).fetchone()
            return Grabber.from_dict(dict(row)) if row else None

    def next_grabber_seq(self, conn: sqlite3.Connection | None = None) -> int:
        """下一个接单员编号（#0001 起）。与建档放同一事务里用，防并发撞号。"""
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT MAX(seq) FROM grabbers").fetchone()
            return int(row[0] or 0) + 1

    def list_grabbers(self, status: str = "", limit: int = 30, offset: int = 0) -> list[Grabber]:
        conn = self._get_conn()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM grabbers WHERE status = ? ORDER BY seq ASC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM grabbers ORDER BY seq ASC LIMIT ? OFFSET ?", (limit, offset)
                ).fetchall()
            return [Grabber.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def count_grabbers(self, status: str = "") -> int:
        conn = self._get_conn()
        try:
            if status:
                return int(conn.execute("SELECT COUNT(*) FROM grabbers WHERE status = ?", (status,)).fetchone()[0])
            return int(conn.execute("SELECT COUNT(*) FROM grabbers").fetchone()[0])
        finally:
            conn.close()

    def delete_grabber(self, user_id: int) -> bool:
        conn = self._get_conn()
        try:
            cur = conn.execute("DELETE FROM grabbers WHERE user_id = ?", (user_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def is_grabber_active(self, user_id: int) -> bool:
        """快捷判断：在岗接单员（抢单按钮回调的高频路径用）。"""
        g = self.get_grabber(user_id)
        return bool(g and g.status == "active")

    # ---------- 佣金流水 ----------

    def add_grab_commission(self, cm: GrabCommission, conn: sqlite3.Connection | None = None) -> int:
        with self._maybe_conn(conn) as c:
            cur = c.execute(
                "INSERT INTO grab_commissions (order_id, grabber_id, amount, status, frozen_until, released_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cm.order_id, cm.grabber_id, cm.amount, cm.status, cm.frozen_until, cm.released_at, now_iso()),
            )
            return int(cur.lastrowid or 0)

    def get_commission_by_order(self, order_id: str, conn: sqlite3.Connection | None = None) -> GrabCommission | None:
        with self._maybe_conn(conn) as c:
            row = c.execute(
                "SELECT * FROM grab_commissions WHERE order_id = ? ORDER BY id DESC LIMIT 1", (order_id,)
            ).fetchone()
            return GrabCommission.from_dict(dict(row)) if row else None

    def list_grab_commissions(
        self, grabber_id: int = 0, status: str = "", limit: int = 20, offset: int = 0
    ) -> list[GrabCommission]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM grab_commissions WHERE 1=1"
            params: list[Any] = []
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            return [GrabCommission.from_dict(dict(r)) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

    def get_grab_commission(
        self, cid: int, conn: sqlite3.Connection | None = None
    ) -> GrabCommission | None:
        """按主键取单笔佣金（手动解冻用，不依赖分页列表）。"""
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM grab_commissions WHERE id = ?", (cid,)).fetchone()
            return GrabCommission.from_dict(dict(row)) if row else None

    def count_grab_commissions(self, grabber_id: int = 0, status: str = "") -> int:
        conn = self._get_conn()
        try:
            query = "SELECT COUNT(*) FROM grab_commissions WHERE 1=1"
            params: list[Any] = []
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            return int(conn.execute(query, params).fetchone()[0])
        finally:
            conn.close()

    def due_commissions(self, now: str, limit: int = 200) -> list[GrabCommission]:
        """到期该解冻的冻结佣金（frozen_until 空串视为立即到期）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM grab_commissions WHERE status = 'frozen' AND (frozen_until = '' OR frozen_until <= ?)"
                " ORDER BY id ASC LIMIT ?",
                (now, limit),
            ).fetchall()
            return [GrabCommission.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def set_commission_status(
        self,
        cid: int,
        status: str,
        released_at: str = "",
        conn: sqlite3.Connection | None = None,
        expect: str | None = None,
    ) -> bool:
        """更新佣金状态。

        expect 非空时做 CAS（compare-and-swap）：只有库中当前状态等于 expect 才更新，
        返回是否命中。资金记账必须用 CAS 形式——自动解冻与手动解冻可能交错执行，
        无条件的 UPDATE 会让同一笔佣金被两次计入可提现余额。
        """
        with self._maybe_conn(conn) as c:
            if expect is None:
                c.execute(
                    "UPDATE grab_commissions SET status = ?, released_at = ? WHERE id = ?",
                    (status, released_at, cid),
                )
                return True
            cur = c.execute(
                "UPDATE grab_commissions SET status = ?, released_at = ? WHERE id = ? AND status = ?",
                (status, released_at, cid, expect),
            )
            return cur.rowcount > 0

    # ---------- 提现单 ----------

    def create_withdrawal(self, w: Withdrawal, conn: sqlite3.Connection | None = None) -> None:
        with self._maybe_conn(conn) as c:
            c.execute(
                "INSERT INTO withdrawals (id, grabber_id, amount, amount_usdt, address, status, txid, note, processed_by, processed_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (w.id, w.grabber_id, w.amount, w.amount_usdt, w.address, w.status, w.txid, w.note,
                 w.processed_by, w.processed_at, now_iso()),
            )

    def get_withdrawal(self, wid: str, conn: sqlite3.Connection | None = None) -> Withdrawal | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM withdrawals WHERE id = ?", (wid,)).fetchone()
            return Withdrawal.from_dict(dict(row)) if row else None

    def save_withdrawal(self, w: Withdrawal, conn: sqlite3.Connection | None = None) -> None:
        with self._maybe_conn(conn) as c:
            c.execute("""
                UPDATE withdrawals SET status = :status, txid = :txid, note = :note,
                       processed_by = :processed_by, processed_at = :processed_at
                WHERE id = :id
            """, {
                "id": w.id, "status": w.status, "txid": w.txid, "note": w.note,
                "processed_by": w.processed_by, "processed_at": w.processed_at,
            })

    def list_withdrawals(
        self, status: str = "", grabber_id: int = 0, limit: int = 20, offset: int = 0
    ) -> list[Withdrawal]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM withdrawals WHERE 1=1"
            params: list[Any] = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            return [Withdrawal.from_dict(dict(r)) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

    def count_withdrawals(self, status: str = "", grabber_id: int = 0) -> int:
        conn = self._get_conn()
        try:
            query = "SELECT COUNT(*) FROM withdrawals WHERE 1=1"
            params: list[Any] = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            return int(conn.execute(query, params).fetchone()[0])
        finally:
            conn.close()

    def has_pending_withdrawal(self, grabber_id: int, conn: sqlite3.Connection | None = None) -> bool:
        """是否已有待审提现单（同一时间只许一单在审，防重复申请）。"""
        with self._maybe_conn(conn) as c:
            row = c.execute(
                "SELECT 1 FROM withdrawals WHERE grabber_id = ? AND status = 'pending' LIMIT 1",
                (grabber_id,),
            ).fetchone()
            return row is not None

    # ---------- 申诉单 ----------

    def create_grab_appeal(self, a: GrabAppeal, conn: sqlite3.Connection | None = None) -> int:
        with self._maybe_conn(conn) as c:
            cur = c.execute(
                "INSERT INTO grab_appeals (grabber_id, order_id, content, status, reply, handled_by, handled_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (a.grabber_id, a.order_id, a.content, a.status, a.reply, a.handled_by, a.handled_at, now_iso()),
            )
            return int(cur.lastrowid or 0)

    def get_grab_appeal(self, aid: int, conn: sqlite3.Connection | None = None) -> GrabAppeal | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM grab_appeals WHERE id = ?", (aid,)).fetchone()
            return GrabAppeal.from_dict(dict(row)) if row else None

    def save_grab_appeal(self, a: GrabAppeal, conn: sqlite3.Connection | None = None) -> None:
        with self._maybe_conn(conn) as c:
            c.execute(
                "UPDATE grab_appeals SET status = ?, reply = ?, handled_by = ?, handled_at = ? WHERE id = ?",
                (a.status, a.reply, a.handled_by, a.handled_at, a.id),
            )

    def list_grab_appeals(
        self, status: str = "", grabber_id: int = 0, limit: int = 20, offset: int = 0
    ) -> list[GrabAppeal]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM grab_appeals WHERE 1=1"
            params: list[Any] = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            query += " ORDER BY id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            return [GrabAppeal.from_dict(dict(r)) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

    def count_grab_appeals(self, status: str = "") -> int:
        conn = self._get_conn()
        try:
            if status:
                return int(conn.execute("SELECT COUNT(*) FROM grab_appeals WHERE status = ?", (status,)).fetchone()[0])
            return int(conn.execute("SELECT COUNT(*) FROM grab_appeals").fetchone()[0])
        finally:
            conn.close()

    # ---------- 派单/抢单订单查询 ----------

    def grab_order_atomic(
        self,
        order_id: str,
        grabber_id: int,
        grabber_name: str,
        commission: int,
        grab_at: str,
        deadline: str,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        """原子抢单：只有 still waiting 的单才抢得到，rowcount=0 即手慢无。

        并发安全靠单条 UPDATE 的 WHERE 条件保证——SQLite 写锁串行化，
        两个接单员同时点也只有一个人的 rowcount 是 1。
        """
        with self._maybe_conn(conn) as c:
            cur = c.execute(
                "UPDATE orders SET grab_status = 'grabbed', grabber_id = ?, grabber_name = ?,"
                " commission = ?, grab_at = ?, deadline = ?"
                " WHERE id = ? AND grab_status = 'waiting'",
                (grabber_id, grabber_name, commission, grab_at, deadline, order_id),
            )
            return cur.rowcount == 1

    def release_grab_order(
        self, order_id: str, conn: sqlite3.Connection | None = None
    ) -> None:
        """释放回待抢池：清接单员/时限/预警标记，保留 dispatch_count 计数。"""
        with self._maybe_conn(conn) as c:
            c.execute(
                "UPDATE orders SET grab_status = 'waiting', grabber_id = 0, grabber_name = '',"
                " grab_at = '', deadline = '', warn_sent = 0"
                " WHERE id = ?",
                (order_id,),
            )

    def list_grab_orders(
        self, grab_status: str = "", grabber_id: int = 0, limit: int = 20, offset: int = 0
    ) -> list[Order]:
        """派单列表查询：grab_status 传 'grabbed' 等精确值；传 '' 表示全部派单（grab_status != ''）。"""
        conn = self._get_conn()
        try:
            query = "SELECT * FROM orders WHERE grab_status != ''"
            params: list[Any] = []
            if grab_status:
                query = "SELECT * FROM orders WHERE grab_status = ?"
                params.append(grab_status)
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            query += " ORDER BY id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            return [Order.from_dict(dict(r)) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

    def count_grab_orders(self, grab_status: str = "", grabber_id: int = 0) -> int:
        conn = self._get_conn()
        try:
            if grab_status:
                query = "SELECT COUNT(*) FROM orders WHERE grab_status = ?"
                params: list[Any] = [grab_status]
            else:
                query = "SELECT COUNT(*) FROM orders WHERE grab_status != ''"
                params = []
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            return int(conn.execute(query, params).fetchone()[0])
        finally:
            conn.close()

    def timeout_grab_orders(self, now: str, limit: int = 100) -> list[Order]:
        """已过最晚回单时限仍在进行中的单（定时任务扫描用）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM orders WHERE grab_status = 'grabbed' AND deadline != '' AND deadline <= ?"
                " ORDER BY deadline ASC LIMIT ?",
                (now, limit),
            ).fetchall()
            return [Order.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def warn_due_grab_orders(self, warn_before: str, now: str, limit: int = 100) -> list[Order]:
        """进入预警窗口（now < deadline <= warn_before）且还没发过预警的单。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM orders WHERE grab_status = 'grabbed' AND warn_sent = 0"
                " AND deadline != '' AND deadline > ? AND deadline <= ? LIMIT ?",
                (now, warn_before, limit),
            ).fetchall()
            return [Order.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def mark_grab_warned(self, order_id: int | str, conn: sqlite3.Connection | None = None) -> None:
        with self._maybe_conn(conn) as c:
            c.execute("UPDATE orders SET warn_sent = 1 WHERE id = ?", (str(order_id),))

    def grab_stats(self) -> dict[str, int]:
        """管理面板抢单概览：待抢/进行中/今日完单/接单员数/待审提现/待审申诉。"""
        conn = self._get_conn()
        try:
            waiting = conn.execute("SELECT COUNT(*) FROM orders WHERE grab_status = 'waiting'").fetchone()[0]
            grabbed = conn.execute("SELECT COUNT(*) FROM orders WHERE grab_status = 'grabbed'").fetchone()[0]
            today = now_str()[:10]
            done_today = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE grab_status = 'done' AND substr(completed_at, 1, 10) = ?",
                (today,),
            ).fetchone()[0]
            grabbers = conn.execute("SELECT COUNT(*) FROM grabbers WHERE status = 'active'").fetchone()[0]
            wd_pending = conn.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'").fetchone()[0]
            appeal_pending = conn.execute("SELECT COUNT(*) FROM grab_appeals WHERE status = 'pending'").fetchone()[0]
            return {
                "waiting": int(waiting), "grabbed": int(grabbed), "done_today": int(done_today),
                "grabbers": int(grabbers), "wd_pending": int(wd_pending), "appeal_pending": int(appeal_pending),
            }
        finally:
            conn.close()
