"""
付费供需业务模块
================
规则（全部可在管理端调整，见 db.get_settings() 的 supply_* 配置项）：
    - 渠道：bot=机器人广播 / channel=频道 / both=双发
    - 定价：双发价 = 机器人价 + 频道价 - 优惠积分，最低 1 积分
    - 每日上限：机器人/频道共用，按东八区自然日统计
    - 身份：实名=展示可点击私聊；匿名=显示「🎭 匿名用户」（管理员后台仍可见真实身份）
    - 帖子不可编辑只能删除；删帖默认不退款，管理员可选「删除并退款」
    - 分发失败：上架即扣费成功；频道发送失败自动退频道那份积分并通知用户

事务约定（与购买/充值一致）：
    扣积分 + 写积分流水 + 帖子落库，三件事在同一 atomic 事务提交，
    任何一步失败整体回滚，不会出现"扣了钱没帖子"或"有帖子没扣钱"。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .models import (
    LOCAL_TZ,
    SUPPLY_CHANNELS,
    Database,
    SupplyPost,
    User,
    escape_html,
    gen_supply_id,
    now_iso,
)
from .richtext import (
    ICON_CHECK,
    ICON_HAT,
    ICON_KEY,
    ICON_MAIL,
    TG_DIAMOND,
    TG_MAIL,
    TG_STAR,
    send_html,
)

logger = logging.getLogger(__name__)

# 「我也要发供需」按钮图标：📬（与 main.py 的高级会员表情同一套）
ICON_INBOX = ICON_MAIL

# 发布失败原因码 → 用户提示
CREATE_FAIL_REASONS = {
    "banned": "您的账号已被限制，无法发布供需",
    "empty": "内容不能为空",
    "too_long": "内容超出字数限制",
    "limit": "今日发布次数已达上限",
    "points": "积分不足",
    "no_channel": "频道未配置，暂不可选频道供需",
    "paused": "供需发布已暂停，请稍后再试",
}

# 帖子状态 → 展示文案（用户端/管理端共用）
SUPPLY_STATUS_TEXT = {
    "active": "✅ 展示中",
    "pending_review": "⏳ 审核中",
    "rejected": "❌ 未通过",
    "deleted": "🗑 已删除",
}
SUPPLY_STATUS_ICON = {
    "active": "✅",
    "pending_review": "⏳",
    "rejected": "❌",
    "deleted": "🗑",
}


def _today_start_iso() -> str:
    """东八区今日 00:00 的 ISO 串，用于「每日上限」统计。"""
    now = datetime.now(LOCAL_TZ)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


class SupplyService:
    def __init__(self, db: Database, shop_service: Any) -> None:
        self.db = db
        self._shop = shop_service  # 复用其 _bot 与 list_users 等能力

    # ------------------------------------------------------------------ 配置与定价
    def settings(self) -> dict[str, Any]:
        return self.db.get_settings()

    def channel_configured(self) -> bool:
        return bool((self.settings().get("supply_channel_id") or "").strip())

    def price_for(self, channel: str) -> int:
        """按渠道算价。双发 = 两价之和 - 优惠，最低 1 积分。"""
        s = self.settings()
        p_bot = int(s.get("supply_price_bot", 10))
        p_ch = int(s.get("supply_price_channel", 10))
        discount = int(s.get("supply_discount", 10))
        if channel == "bot":
            return max(1, p_bot)
        if channel == "channel":
            return max(1, p_ch)
        return max(1, p_bot + p_ch - discount)

    def channel_leg_value(self, channel: str) -> int:
        """「频道」这一腿在实付金额里占多少（分发失败退款用）。"""
        if channel == "channel":
            return self.price_for("channel")
        if channel == "both":
            return max(0, self.price_for("both") - self.price_for("bot"))
        return 0

    def available_channels(self) -> list[str]:
        """前台可选项：频道未配置时自动隐藏 channel/both。"""
        if self.channel_configured():
            return ["bot", "channel", "both"]
        return ["bot"]

    # ------------------------------------------------------------------ 开关（v2.3.0）
    def paused(self) -> bool:
        """停止发布开关：开时全站暂停发布供需（大厅浏览不受影响）。"""
        return bool(self.settings().get("supply_paused", False))

    def review_enabled(self) -> bool:
        """人工审核开关：开时新帖先扣费进待审队列，管理员通过后分发，拒绝自动退款。"""
        return bool(self.settings().get("supply_review", False))

    def hall_open(self) -> bool:
        """大厅浏览开关：关时用户进大厅看到维护提示（管理员不受限）。"""
        return bool(self.settings().get("supply_hall_open", True))

    def anonymous_allowed(self) -> bool:
        """匿名发布开关：关时发布向导隐藏匿名切换，强制实名。"""
        return bool(self.settings().get("supply_anonymous", True))

    def pending_count(self) -> int:
        """待审核帖子数（管理端面板/队列角标用）。"""
        return self.db.count_supply_posts(status="pending_review")

    def today_count(self, user_id: int) -> int:
        return self.db.count_user_supply_posts(user_id, since_iso=_today_start_iso())

    def nth_of_user(self, user_id: int) -> int:
        """这是该用户第几条帖子（含本次待发的）。"""
        return self.db.count_user_supply_posts(user_id) + 1

    # ------------------------------------------------------------------ 文案
    def publisher_html(self, post: SupplyPost) -> str:
        """发布人展示：实名=可点击私聊链接；匿名=匿名用户。"""
        if post.anonymous:
            return "🎭 匿名用户"
        name = post.display_name or (f"@{post.username}" if post.username else str(post.user_id))
        return f'<a href="tg://user?id={post.user_id}">{escape_html(name)}</a>'

    def format_post_text(self, post: SupplyPost, nth: int) -> str:
        """广播/频道/大厅/预览统一的帖子格式（底部带信息栏）。

        内容优先用 content_html（发布时由 text_html 生成的富文本，加粗/链接/
        高级会员表情原样呈现）；老数据没有 content_html，自动回退为转义纯文本。
        信息栏的 💎📬⭐ 是正文内联高级会员表情，机器人无表情权限时由
        安全发送自动降级为普通 emoji，消息不丢。
        """
        cat = f"【{escape_html(post.category)}】" if post.category else ""
        body = post.content_html or escape_html(post.content)
        return (
            f"{TG_MAIL} {cat}{body}\n\n"
            f"——\n"
            f"{TG_DIAMOND} 本条消耗 <code>{post.cost}</code> 积分\n"
            f"👤 发布人：{self.publisher_html(post)}\n"
            f"{TG_STAR} 这是 TA 发布的第 <code>{nth}</code> 条供需"
        )

    def post_keyboard(self, in_channel: bool = False) -> InlineKeyboardMarkup | None:
        """「我也要发供需」裂变按钮：频道里用 deep link，机器人里用 callback。"""
        if in_channel:
            bot = getattr(self._shop, "_bot", None)
            username = getattr(bot, "username", None) if bot else None
            if not username:
                return None
            return InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "我也要发供需",
                    url=f"https://t.me/{username}?start=supply",
                    icon_custom_emoji_id=ICON_INBOX,
                )
            ]])
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "我也要发供需",
                callback_data="sup:new",
                icon_custom_emoji_id=ICON_INBOX,
            )
        ]])

    # ------------------------------------------------------------------ 发布（原子扣费）
    def create_post(
        self,
        user: User,
        channel: str,
        category: str,
        content: str,
        photo_file_id: str = "",
        anonymous: bool = False,
        content_html: str = "",
    ) -> tuple[SupplyPost | None, str | None]:
        """扣费 + 流水 + 落库，一个原子事务。返回 (帖子, 失败原因码)。

        content_html：发布向导里由 text_html 生成的富文本（含加粗/链接/
        自定义表情），与纯文本 content 一起入库；预览与正式发布保持一致。
        人工审核开关开启时，帖子落库为 pending_review：先扣费待审，
        管理员通过后分发（approve_post），拒绝则自动全额退款（reject_post）。
        """
        content = (content or "").strip()
        if user.is_banned:
            return None, "banned"
        if not content:
            return None, "empty"
        s = self.settings()
        if s.get("supply_paused", False):
            return None, "paused"
        max_len = int(s.get("supply_max_len", 600))
        if len(content) > max_len:
            return None, "too_long"
        if channel not in SUPPLY_CHANNELS:
            channel = "bot"
        if channel in ("channel", "both") and not self.channel_configured():
            return None, "no_channel"
        daily_limit = int(s.get("supply_daily_limit", 5))
        cost = self.price_for(channel)
        # 频道腿金额快照：退款必须按「发帖时实付」而不是「退款时的当前价」，
        # 否则管理员调价后分发失败会超额退款（原扣 1 分、调价后退 100 分）。
        channel_leg = self.channel_leg_value(channel)
        if not s.get("supply_anonymous", True):
            anonymous = False
        status = "pending_review" if s.get("supply_review", False) else "active"

        with self.db.atomic() as conn:
            fresh = self.db.get_user(user.user_id, conn=conn)
            if not fresh or fresh.points < cost:
                return None, "points"
            if daily_limit > 0:
                today = self.db.count_user_supply_posts(user.user_id, since_iso=_today_start_iso())
                if today >= daily_limit:
                    return None, "limit"
            post = SupplyPost(
                id=gen_supply_id(),
                user_id=user.user_id,
                username=user.username or "",
                display_name=(user.first_name + " " + user.last_name).strip(),
                category=category or "",
                content=content,
                content_html=(content_html or "").strip(),
                photo_file_id=photo_file_id or "",
                channel=channel,
                cost=cost,
                channel_leg=channel_leg,
                anonymous=anonymous,
                status=status,
                created_at=now_iso(),
            )
            fresh.points -= cost
            fresh.total_spent += cost
            fresh.last_active = now_iso()
            self.db.save_user(fresh, conn=conn)
            self.db.add_point_record(
                user.user_id, -cost, fresh.points,
                f"发布供需（{SUPPLY_CHANNELS[channel]}）",
                ref=post.id, conn=conn,
            )
            self.db.save_supply_post(post, conn=conn)
        user.points = fresh.points  # 同步内存里的余额，页面展示用
        return post, None

    # ------------------------------------------------------------------ 人工审核（v2.3.0）
    def approve_post(self, post_id: str, operator_id: int = 0) -> SupplyPost | None:
        """审核通过：pending_review → active。幂等，已处理的帖子返回 None。"""
        with self.db.atomic() as conn:
            post = self.db.get_supply_post(post_id, conn=conn)
            if not post or post.status != "pending_review":
                return None
            post.status = "active"
            self.db.save_supply_post(post, conn=conn)
        return post

    def reject_post(self, post_id: str, operator_id: int = 0) -> SupplyPost | None:
        """审核拒绝：pending_review → rejected，并全额退还本次花费（幂等）。"""
        with self.db.atomic() as conn:
            post = self.db.get_supply_post(post_id, conn=conn)
            if not post or post.status != "pending_review":
                return None
            post.status = "rejected"
            if not post.refunded and post.cost > 0:
                user = self.db.get_user(post.user_id, conn=conn)
                if user:
                    user.points += post.cost
                    user.total_spent = max(0, user.total_spent - post.cost)
                    self.db.save_user(user, conn=conn)
                    self.db.add_point_record(
                        post.user_id, post.cost, user.points,
                        "供需审核未通过退款",
                        operator_id=operator_id, ref=post.id, conn=conn,
                    )
                post.refunded = True
            self.db.save_supply_post(post, conn=conn)
        return post

    async def notify_admins_review(self, post: SupplyPost) -> None:
        """新帖进入待审：给每位管理员发审核卡片（通过/拒绝按钮带会员图标）。"""
        bot = getattr(self._shop, "_bot", None)
        if not bot:
            return
        nth = self.db.count_user_supply_posts(post.user_id)
        text = (
            f"🕐 <b>供需待审核</b>（帖子 <code>{post.id}</code>）\n\n"
            f"{self.format_post_text(post, nth)}\n\n"
            f"——\n"
            f"通过后立即按渠道分发；拒绝自动全额退款给发布人"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ 通过并发布", callback_data=f"a:sup:ok:{post.id}",
                icon_custom_emoji_id=ICON_CHECK,
            )],
            [InlineKeyboardButton(
                "❌ 拒绝并退款", callback_data=f"a:sup:no:{post.id}",
                icon_custom_emoji_id=ICON_HAT,
            )],
            [InlineKeyboardButton(
                "⚙️ 帖子详情", callback_data=f"a:sup:pd:{post.id}",
                icon_custom_emoji_id=ICON_KEY,
            )],
        ])
        for admin in self.db.list_admins():
            try:
                await send_html(
                    bot, admin.user_id, text,
                    reply_markup=keyboard, photo=post.photo_file_id or "",
                )
            except Exception as exc:
                logger.warning("通知管理员 %s 审核失败：%s", admin.user_id, exc)

    async def notify_user_approved(self, post: SupplyPost) -> None:
        bot = getattr(self._shop, "_bot", None)
        if not bot:
            return
        try:
            await send_html(
                bot, post.user_id,
                f"✅ <b>你的供需已通过审核并发布</b>\n\n"
                f"帖子编号：<code>{post.id}</code>\n"
                f"渠道：{escape_html(SUPPLY_CHANNELS.get(post.channel, post.channel))}",
            )
        except Exception as exc:
            logger.warning("通知用户 %s 审核通过失败：%s", post.user_id, exc)

    async def notify_user_rejected(self, post: SupplyPost) -> None:
        bot = getattr(self._shop, "_bot", None)
        if not bot:
            return
        try:
            await send_html(
                bot, post.user_id,
                f"❌ <b>你的供需未通过审核</b>\n\n"
                f"帖子编号：<code>{post.id}</code>\n"
                f"已退 <code>{post.cost}</code> 积分，可修改内容后重新发布",
            )
        except Exception as exc:
            logger.warning("通知用户 %s 审核拒绝失败：%s", post.user_id, exc)

    # ------------------------------------------------------------------ 分发
    async def distribute(self, post: SupplyPost) -> None:
        """按渠道分发：机器人广播（后台群发）和/或发频道。失败自动退对应腿。"""
        nth = self.db.count_user_supply_posts(post.user_id)
        text = self.format_post_text(post, nth)
        if post.channel in ("bot", "both"):
            asyncio.create_task(self._broadcast_bot(post, text))
        if post.channel in ("channel", "both"):
            await self._send_to_channel(post, text)

    async def _broadcast_bot(self, post: SupplyPost, text: str) -> None:
        """机器人供需 = 广播全员。复用 shop_service 的群发能力（自带频控）。"""
        bot = getattr(self._shop, "_bot", None)
        if not bot:
            return
        keyboard = self.post_keyboard(in_channel=False)
        success = 0
        failed = 0
        for u in self.db.list_users(limit=10000):
            try:
                # 安全发送：富文本（含高级会员表情）失败时自动降级纯文本重发
                await send_html(
                    bot, u.user_id, text,
                    reply_markup=keyboard, photo=post.photo_file_id or "",
                )
                success += 1
                await asyncio.sleep(0.05)
            except Exception as exc:
                logger.debug("供需广播给 %s 失败：%s", u.user_id, exc)
                failed += 1
        post.bot_ok = True
        self.db.save_supply_post(post)
        logger.info("供需 %s 广播完成：成功 %s 失败 %s", post.id, success, failed)

    async def _send_to_channel(self, post: SupplyPost, text: str) -> None:
        """频道供需 = 发到配置的频道。失败退频道腿积分并通知发布者。"""
        bot = getattr(self._shop, "_bot", None)
        chat_id = (self.settings().get("supply_channel_id") or "").strip()
        if not bot or not chat_id:
            await self._refund_channel_leg(post, "频道未配置")
            return
        keyboard = self.post_keyboard(in_channel=True)
        try:
            # 安全发送：富文本（含高级会员表情）失败时自动降级纯文本重发
            msg = await send_html(
                bot, chat_id, text,
                reply_markup=keyboard, photo=post.photo_file_id or "",
            )
            post.channel_ok = True
            post.channel_msg = f"{chat_id}:{msg.message_id}"
            self.db.save_supply_post(post)
        except Exception as exc:
            logger.warning("供需 %s 频道发送失败：%s", post.id, exc)
            await self._refund_channel_leg(post, str(exc))

    async def _refund_channel_leg(self, post: SupplyPost, why: str) -> None:
        """频道分发失败：退频道腿积分（幂等），并私信通知发布者。

        退款金额优先取 `post.channel_leg`（发帖时实付快照）。老数据该字段为 0，
        回退到「按当前价格估算」的旧口径，且退款额不会超过该帖实付总额 `post.cost`，
        杜绝调价后退款超过扣款。
        """
        amount = post.channel_leg or self.channel_leg_value(post.channel)
        if post.cost > 0:
            amount = min(amount, post.cost)
        if amount <= 0 or post.refunded:
            return
        with self.db.atomic() as conn:
            fresh = self.db.get_user(post.user_id, conn=conn)
            if not fresh:
                return
            fresh.points += amount
            fresh.total_spent = max(0, fresh.total_spent - amount)
            self.db.save_user(fresh, conn=conn)
            self.db.add_point_record(
                post.user_id, amount, fresh.points,
                "供需频道分发失败退款",
                ref=post.id, conn=conn,
            )
            post.refunded = True
            self.db.save_supply_post(post, conn=conn)
        bot = getattr(self._shop, "_bot", None)
        if bot:
            try:
                await send_html(
                    bot, post.user_id,
                    f"⚠️ 你的供需 <code>{post.id}</code> 频道分发失败（{escape_html(why[:80])}），"
                    f"已退 <code>{amount}</code> 积分\n"
                    f"帖子仍在供需大厅展示",
                )
            except Exception:
                pass

    # ------------------------------------------------------------------ 删帖 / 统计
    def delete_post(self, post_id: str, operator_id: int = 0, refund: bool = False) -> SupplyPost | None:
        """管理员删帖（软删除）。refund=True 时退还本次花费（幂等）。"""
        with self.db.atomic() as conn:
            post = self.db.get_supply_post(post_id, conn=conn)
            if not post or post.status == "deleted":
                return None
            post.status = "deleted"
            if refund and not post.refunded and post.cost > 0:
                user = self.db.get_user(post.user_id, conn=conn)
                if user:
                    user.points += post.cost
                    user.total_spent = max(0, user.total_spent - post.cost)
                    self.db.save_user(user, conn=conn)
                    self.db.add_point_record(
                        post.user_id, post.cost, user.points,
                        "供需删帖退款",
                        operator_id=operator_id, ref=post.id, conn=conn,
                    )
                post.refunded = True
            self.db.save_supply_post(post, conn=conn)
        return post

    def stats(self) -> dict[str, Any]:
        today_prefix = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
        return self.db.supply_stats(today_prefix)
