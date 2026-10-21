"""常量与通用工具函数（时间、单号、格式化、HTML 转义、状态字典）。

由 models.py 拆分而来。所有模块共享同一套时区与格式化口径。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

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


__all__ = [
    "LOCAL_TZ", "ORDER_STATUS", "PRODUCT_STATUS", "PRODUCT_KINDS",
    "TAGS", "PRIORITY", "GRABBER_STATUS", "GRAB_STATUS",
    "COMMISSION_STATUS", "WITHDRAW_STATUS", "APPEAL_STATUS", "now_iso",
    "now_str", "fmt_time", "gen_recharge_id", "gen_product_id",
    "gen_supply_id", "format_usdt", "format_duration", "escape_html",
    "gen_withdraw_id",
]
