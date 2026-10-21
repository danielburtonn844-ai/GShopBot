"""抢单派单业务核心（v2.6.0 新增）：匿名派单 → 群内抢单 → 深链回单 → 佣金结算。

设计要点
========
1. 匿名防偷老板：派单卡片只含项目名/需求/佣金/时限，不含买家任何信息；
   群内抢中公示只显示接单员编号 #0001，不透真实身份。
2. 只有「报单内容」派单：商品（product）付款即自动发货走库存池，不进派单流程；
   报单（report）才会派到接单群（grab_enabled 且配好 grab_chat_id 时）。
3. 原子抢单：UPDATE ... WHERE grab_status='waiting'，rowcount 定胜负，并发不双抢。
4. 佣金链路：抢中时按 订单积分 × 佣金比例 预算写入订单 → 完单冻结
   （grab_freeze_hours 小时，0=立即到账）→ 到点自动解冻进可提现余额 → 申请提现
   → 管理员审核打款。
5. 时限风控：grab_timeout_hours 为最晚回单时限；超时自动释放重派 +
   接单员记超时 + 冷静期禁抢；超时前 grab_warn_minutes 私聊预警。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .models import (
    LOCAL_TZ,
    GrabAppeal,
    Grabber,
    GrabCommission,
    Order,
    Withdrawal,
    escape_html,
    fmt_time,
    gen_withdraw_id,
    now_iso,
)
from .richtext import (
    ICON_A_MAIL,
    ICON_X_CHECK,
    ICON_X_GRAB,
    ICON_X_NINJA,
    ICON_X_SPEAK,
    TG_A_CHEERS,
    TG_A_KEY,
    TG_A_MAIL,
    TG_A_STAR,
    TG_A_UNLOCK,
    TG_X_ALARM,
    TG_X_BELL,
    TG_X_CHECK,
    TG_X_GRAB,
    TG_X_MONEY,
    TG_X_NINJA,
    TG_X_WARN,
    send_html,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(LOCAL_TZ)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(iso_str: str) -> datetime | None:
    """解析带时区 ISO；空串/异常返回 None。"""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str)
    except Exception:
        return None


class GrabService:
    """抢单模块门面：所有 DB 操作 + 通知都从这里走，handler 层只做参数解析。"""

    def __init__(self, db, shop) -> None:
        self.db = db
        self.shop = shop           # ShopService：回单/退款/通知买家复用它
        self._bot: Any = None
        self._bot_username: str = ""

    def set_bot(self, bot: Any) -> None:
        self._bot = bot

    # ==================================================================
    # 设置快捷读取
    # ==================================================================

    @property
    def settings(self) -> dict[str, Any]:
        return self.db.get_settings()

    def enabled(self) -> bool:
        s = self.settings
        flag = str(s.get("grab_enabled") or "").strip().lower()
        on = flag in ("1", "true", "on", "yes") or (flag.isdigit() and int(flag) > 0)
        return on and bool(str(s.get("grab_chat_id") or "").strip())

    def chat_id(self) -> str:
        return str(self.settings.get("grab_chat_id") or "").strip()

    # ---------- 积分 ⇄ U 汇率（v2.6.1：提现自动换算，后台可调） ----------

    def points_per_usdt(self) -> int:
        """多少积分 = 1 USDT（默认 100，后台「积分兑U汇率」可调）。"""
        try:
            v = int(float(str(self.settings.get("grab_points_per_usdt") or 100)))
            return max(1, v)
        except (TypeError, ValueError):
            return 100

    def points_to_usdt(self, points: int) -> float:
        """积分实时换算成 U，保留两位小数。
        Decimal 计算后 ROUND_HALF_UP 再落 float：直接用 round() 会走银行家舍入，
        500 积分 @ 200 汇率得 2.5 → round 得 2，实际应进 2.5（差 0.5U）。"""
        rate = self.points_per_usdt()
        q = (Decimal(int(points)) / Decimal(rate)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return float(q)

    def usdt_text(self, points: int) -> str:
        """积分 + 折算 U 的组合展示：500 积分 ≈ 5.00 U。"""
        return f"{points} 积分 ≈ {self.points_to_usdt(points):.2f} U"

    # ==================================================================
    # 佣金计算
    # ==================================================================

    def commission_for(self, order: Order) -> int:
        """预算佣金：商品单独比例优先（-1=跟随全局，0=无佣金单），按订单积分折算。"""
        product = self.db.get_product(order.product_id)
        pct = getattr(product, "commission_pct", -1) if product else -1
        if pct is None or pct < 0:
            pct = int(self.settings.get("grab_commission_pct", 20) or 0)
        if pct <= 0:
            return 0
        return max(0, int(order.points * pct / 100))

    # ==================================================================
    # 接单员档案
    # ==================================================================

    def add_grabber(self, user_id: int, username: str = "", name: str = "") -> tuple[Grabber | None, str]:
        """管理员添加接单员（建档发编号）。已存在则按现状返回提示。"""
        existing = self.db.get_grabber(user_id)
        if existing:
            if existing.status == "blacklist":
                return None, "该用户在黑名单中，请先在详情页移出黑名单"
            return existing, "该用户已是接单员"
        with self.db.atomic() as conn:
            seq = self.db.next_grabber_seq(conn=conn)
            g = Grabber(user_id=user_id, seq=seq, username=username, name=name, status="active")
            self.db.save_grabber(g, conn=conn)
        return g, ""

    def set_grabber_status(self, user_id: int, status: str, operator_id: int = 0) -> Grabber | None:
        """改接单员状态：active 在岗 / rest 请假 / suspended 停权 / blacklist 黑名单。"""
        g = self.db.get_grabber(user_id)
        if not g:
            return None
        g.status = status
        if status == "active":
            g.cooldown_until = ""  # 恢复上岗顺带清冷静期
        self.db.save_grabber(g)
        return g

    def grabber_can_grab(self, g: Grabber | None) -> str | None:
        """抢单资格校验，返回 None=可抢，否则为拒绝原因文案。"""
        if not g:
            return "你还不是接单员，请联系管理员开通"
        if g.status == "blacklist":
            return "你已被拉入黑名单，无法抢单"
        if g.status == "suspended":
            return "你已被停权，请联系管理员恢复"
        if g.status == "rest":
            return "你正在请假中，恢复在岗后才能抢单"
        cd = _parse(g.cooldown_until)
        if cd and cd > _now():
            return f"冷静期中（{fmt_time(g.cooldown_until)} 截止），暂不能抢单"
        return None

    # ==================================================================
    # 派单（匿名卡片到接单群）
    # ==================================================================

    def dispatch_card_text(self, order: Order, commission: int) -> str:
        """派单卡片：匿名——只有项目/需求/佣金/时限，绝无买家信息。"""
        s = self.settings
        timeout_h = s.get("grab_timeout_hours", 24)
        note = (order.note or "").strip()
        note_line = f"\n📝 <b>需求内容：</b>\n<blockquote>{escape_html(note[:300])}</blockquote>" if note else ""
        return (
            f"{TG_X_GRAB} <b>新单待抢</b> <code>#{order.id}</code>\n"
            f"━━━━━━━━━━━━━━\n"
            f"📦 <b>项目：</b>{escape_html(order.product_name)}\n"
            f"{TG_X_MONEY} <b>佣金：</b><code>{commission}</code> 积分\n"
            f"{TG_X_ALARM} <b>时限：</b>抢中后 <code>{timeout_h}</code> 小时内回单"
            f"{note_line}\n"
            f"━━━━━━━━━━━━━━\n"
            f"{TG_X_NINJA} 先到先得，抢中后跳转机器人查看详情并回单"
        )

    def _grab_keyboard(self, order_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "立即抢单", callback_data=f"gb:g:{order_id}",
                icon_custom_emoji_id=ICON_X_GRAB,
            )
        ]])

    async def _bot_name(self) -> str:
        """深链用的机器人用户名，缓存一次即可。"""
        if not self._bot_username and self._bot:
            try:
                me = await self._bot.get_me()
                self._bot_username = me.username or ""
            except Exception as exc:
                logger.warning("获取机器人用户名失败：%s", exc)
        return self._bot_username

    async def dispatch_order(self, order: Order) -> bool:
        """把报单订单派到接单群。返回是否成功发出。"""
        if not self._bot or not self.enabled():
            return False
        if order.grab_status in ("waiting", "grabbed"):
            return False  # 防重复派单
        commission = self.commission_for(order)
        try:
            msg = await send_html(
                self._bot, self.chat_id(), self.dispatch_card_text(order, commission),
                reply_markup=self._grab_keyboard(order.id),
            )
        except Exception as exc:
            logger.warning("派单发出失败（订单 %s）：%s", order.id, exc)
            await self._notify_admins(
                f"{TG_X_WARN} <b>报单派单失败</b>\n\n"
                f"订单：<code>#{order.id}</code>（{escape_html(order.product_name)}）\n"
                f"原因：<code>{escape_html(str(exc)[:200])}</code>\n\n"
                f"请检查：1) 机器人在接单群内且为管理员 2) 抢单设置里的接单群ID正确\n"
                f"修好后可到「派单中心 → 超时/重派」重发，或人工回单。"
            )
            return False
        order.grab_status = "waiting"
        order.commission = commission
        order.dispatch_chat = str(self.chat_id())
        order.dispatch_msg = msg.message_id
        order.dispatch_count = (order.dispatch_count or 0) + 1
        order.last_dispatch_at = now_iso()
        order.warn_sent = 0
        self.db.save_order(order)
        return True

    async def redispatch_order(self, order: Order, reason: str = "") -> bool:
        """重派：超时/放弃释放后再次发卡片。超过最大次数则交还管理员人工处理。"""
        max_n = int(self.settings.get("grab_max_dispatch", 3) or 3)
        if (order.dispatch_count or 0) >= max_n:
            # 派单流程关闭，订单回到管理员人工回单池
            order.grab_status = ""
            self.db.save_order(order)
            await self._notify_admins(
                f"{TG_X_WARN} <b>派单已达最大重派次数</b>\n\n"
                f"订单：<code>#{order.id}</code>（{escape_html(order.product_name)}）\n"
                f"原因：{escape_html(reason)}\n"
                f"该单已交还管理员，请在订单管理中人工回单。"
            )
            return False
        # 重新发一张卡片（老卡片留着做记录，内容为「已释放」由调用方更新）
        return await self.dispatch_order_new_card(order)

    async def dispatch_order_new_card(self, order: Order) -> bool:
        """重派用：清掉旧卡片绑定再发新卡（dispatch_order 里有 waiting 防重，先洗状态）。"""
        order.grab_status = ""
        self.db.save_order(order)
        return await self.dispatch_order(order)

    async def _edit_dispatch_card(self, order: Order, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
        """更新群内派单卡片（抢中/释放/超时）。失败只记日志不影响主流程。"""
        if not self._bot or not order.dispatch_chat or not order.dispatch_msg:
            return
        try:
            await self._bot.edit_message_text(
                chat_id=order.dispatch_chat,
                message_id=order.dispatch_msg,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.info("更新派单卡片失败（订单 %s）：%s", order.id, exc)

    # ==================================================================
    # 抢单
    # ==================================================================

    def try_grab(self, order_id: str, user_id: int, username: str, name: str) -> tuple[str, Order | None, Grabber | None]:
        """抢单主逻辑（纯 DB，同步）。返回 (结果码, 订单, 接单员)。

        结果码：ok 抢中 / taken 手慢无 / not_found 单不存在或不在待抢 /
                其他为拒绝原因文案（资格校验未过）。
        """
        s = self.settings
        cooldown_sec = int(s.get("grab_cooldown_seconds", 5) or 0)
        timeout_h = float(s.get("grab_timeout_hours", 24) or 24)

        with self.db.atomic() as conn:
            g = self.db.get_grabber(user_id, conn=conn)
            reason = self.grabber_can_grab(g)
            if reason:
                return reason, None, g

            # 抢单冷却：防连点刷接口
            last = _parse(g.last_grab_at)
            if cooldown_sec > 0 and last and (_now() - last).total_seconds() < cooldown_sec:
                return f"手太快了，{cooldown_sec} 秒后再试", None, g

            order = self.db.get_order(order_id, conn=conn)
            if not order or order.grab_status != "waiting":
                return "taken", order, g

            commission = order.commission or self.commission_for(order)
            grab_at = _now()
            deadline = _iso(grab_at + timedelta(hours=timeout_h))
            ok = self.db.grab_order_atomic(
                order_id, user_id, g.display(), commission,
                _iso(grab_at), deadline, conn=conn,
            )
            if not ok:
                return "taken", order, g

            g.last_grab_at = _iso(grab_at)
            # 抢中顺手刷新昵称快照，管理端看到的一直是最新名字
            g.username = username or g.username
            g.name = name or g.name
            self.db.save_grabber(g, conn=conn)

            order = self.db.get_order(order_id, conn=conn)
            return "ok", order, g

    async def on_grabbed(self, order: Order, grabber: Grabber) -> None:
        """抢中后续：群卡片改公示 + 私聊推回单卡 + 管理员通报。"""
        # 群卡片：只公示编号，不透身份
        deep = await self._deeplink(order.id)
        card = (
            f"{TG_X_CHECK} <b>此单已被抢中</b>\n\n"
            f"订单：<code>#{order.id}</code>\n"
            f"接单员：<b>{escape_html(grabber.display())}</b>\n"
            f"{TG_X_ALARM} 最晚回单：<code>{fmt_time(order.deadline)}</code>"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("去接单（仅抢中者）", url=deep, icon_custom_emoji_id=ICON_X_NINJA)
        ]]) if deep else None
        await self._edit_dispatch_card(order, card, kb)

        # 私聊回单卡
        await self.send_reply_card(order, grabber)

        # 管理员通报
        if self.settings.get("grab_notify_admin"):
            await self._notify_admins(
                f"{TG_X_NINJA} <b>订单已被抢中</b>\n\n"
                f"订单：<code>#{order.id}</code>（{escape_html(order.product_name)}）\n"
                f"接单员：{escape_html(grabber.display())}（<code>{grabber.user_id}</code>）\n"
                f"佣金：<code>{order.commission}</code> 积分 · 时限：<code>{fmt_time(order.deadline)}</code>"
            )

    async def _deeplink(self, order_id: str) -> str:
        name = await self._bot_name()
        return f"https://t.me/{name}?start=grab_{order_id}" if name else ""

    async def send_reply_card(self, order: Order, grabber: Grabber) -> None:
        """私聊回单卡：抢中推送 & 深链落地页共用。"""
        if not self._bot:
            return
        deadline = _parse(order.deadline)
        left = ""
        if deadline:
            secs = (deadline - _now()).total_seconds()
            if secs > 0:
                h = int(secs // 3600)
                m = int((secs % 3600) // 60)
                left = f"（剩 {h} 小时 {m} 分）"
        note = (order.note or "").strip() or "（买家未填写补充需求）"
        text = (
            f"{TG_A_MAIL} <b>你已抢到订单</b> <code>#{order.id}</code>\n"
            f"━━━━━━━━━━━━━━\n"
            f"📦 <b>项目：</b>{escape_html(order.product_name)}\n"
            f"{TG_X_MONEY} <b>佣金：</b><code>{order.commission}</code> 积分\n"
            f"{TG_X_ALARM} <b>最晚回单：</b><code>{fmt_time(order.deadline)}</code>{left}\n"
            f"━━━━━━━━━━━━━━\n"
            f"📝 <b>回单内容（买家需求）：</b>\n"
            f"<blockquote>{escape_html(note)}</blockquote>\n"
            f"━━━━━━━━━━━━━━\n"
            f"点 <b>「{escape_html('📫 回单')}」</b> 后直接发送内容，文字/图片/文件均可；\n"
            f"做不完可 <b>放弃</b>（记一次放弃 + 冷静期）。"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📫 回单", callback_data=f"gb:r:{order.id}",
                                  icon_custom_emoji_id=ICON_A_MAIL)],
            [InlineKeyboardButton("放弃这单", callback_data=f"gb:q:{order.id}",
                                  icon_custom_emoji_id=ICON_X_SPEAK)],
        ])
        try:
            await send_html(self._bot, grabber.user_id, text, reply_markup=keyboard)
        except Exception as exc:
            logger.warning("推送回单卡失败（接单员 %s）：%s", grabber.user_id, exc)

    async def on_grab_failed_taken(self, order: Order | None) -> None:
        """手慢无：群卡片若还是旧的可顺带刷新（抢中者那边已改，这里只兜底）。"""
        return

    # ==================================================================
    # 回单（接单员 → 买家）
    # ==================================================================

    def complete_by_grabber(
        self, order_id: str, grabber_id: int, reply_html: str
    ) -> tuple[Order | None, str | None]:
        """接单员回单。返回 (订单, None) 成功；(None, 原因) 失败。

        安全约束：必须是本人抢中的进行中订单；完单后佣金进冻结。
        """
        with self.db.atomic() as conn:
            order = self.db.get_order(order_id, conn=conn)
            if not order:
                return None, "订单不存在"
            if order.grab_status != "grabbed" or order.grabber_id != grabber_id:
                return None, "这单不是你抢的或已被释放"
            if order.status not in ("pending", "processing"):
                return None, "订单已是终态"

            g = self.db.get_grabber(grabber_id, conn=conn)
            if not g:
                return None, "接单员档案不存在"

            # 订单完工（复用 ShopService 的状态写法，操作者记接单员）
            now = now_iso()
            order.status = "completed"
            order.reply_content = reply_html
            order.reply_admin_id = grabber_id
            order.reply_at = now
            order.completed_at = now
            order.grab_status = "done"
            self.db.save_order(order, conn=conn)

            # 佣金冻结入库
            amount = order.commission or 0
            if amount > 0:
                freeze_h = float(self.settings.get("grab_freeze_hours", 24) or 0)
                frozen_until = "" if freeze_h <= 0 else _iso(_now() + timedelta(hours=freeze_h))
                self.db.add_grab_commission(
                    GrabCommission(
                        order_id=order.id, grabber_id=grabber_id, amount=amount,
                        status="frozen", frozen_until=frozen_until,
                    ),
                    conn=conn,
                )
                g.balance_frozen += amount
                g.total_commission += amount

            g.completed_orders += 1
            self.db.save_grabber(g, conn=conn)
            return order, None

    async def after_grabber_complete(self, order: Order, grabber_id: int, copy_from: tuple[int, int] | None = None) -> None:
        """回单后通知链路：买家收货 + 群卡片销单 + 管理员通报 + 立即解冻提示。"""
        g = self.db.get_grabber(grabber_id)
        # 买家通知（复用现有通道，copy_from 把接单员原始消息原样转发）
        try:
            await self.shop.notify_user_order_completed(order, copy_from=copy_from)
        except Exception as exc:
            logger.warning("通知买家失败（订单 %s）：%s", order.id, exc)

        # 群卡片销单
        card = (
            f"{TG_A_CHEERS} <b>此单已完成</b>\n\n"
            f"订单：<code>#{order.id}</code>\n"
            f"接单员：<b>{escape_html(g.display() if g else '未知')}</b>\n"
            f"{TG_A_STAR} 完单时间：<code>{fmt_time(order.completed_at)}</code>"
        )
        await self._edit_dispatch_card(order, card)

        # 接单员到账说明
        if order.commission > 0 and self._bot:
            freeze_h = float(self.settings.get("grab_freeze_hours", 24) or 0)
            if freeze_h > 0:
                tip = f"佣金 <code>{order.commission}</code> 积分已冻结，{freeze_h:g} 小时后自动解冻"
            else:
                tip = f"佣金 <code>{order.commission}</code> 积分已到账"
            try:
                await send_html(
                    self._bot, grabber_id,
                    f"{TG_A_CHEERS} <b>回单成功</b>\n\n"
                    f"订单：<code>#{order.id}</code>\n{tip}",
                )
            except Exception as exc:
                logger.info("通知接单员回单成功失败：%s", exc)

        if self.settings.get("grab_notify_admin"):
            await self._notify_admins(
                f"{TG_X_CHECK} <b>接单员已回单</b>\n\n"
                f"订单：<code>#{order.id}</code>（{escape_html(order.product_name)}）\n"
                f"接单员：{escape_html(g.display() if g else str(grabber_id))}\n"
                f"佣金：<code>{order.commission}</code> 积分"
            )

    # ==================================================================
    # 放弃 / 超时释放
    # ==================================================================

    async def giveup(self, order_id: str, grabber_id: int) -> tuple[bool, str]:
        """接单员主动放弃：记放弃 + 冷静期 + 释放重派。"""
        with self.db.atomic() as conn:
            order = self.db.get_order(order_id, conn=conn)
            if not order or order.grab_status != "grabbed" or order.grabber_id != grabber_id:
                return False, "这单不在你手上"
            g = self.db.get_grabber(grabber_id, conn=conn)
            if not g:
                return False, "接单员档案不存在"
            penalty_min = int(self.settings.get("grab_penalty_minutes", 60) or 60)
            g.giveup_orders += 1
            if penalty_min > 0:
                g.cooldown_until = _iso(_now() + timedelta(minutes=penalty_min))
            self.db.save_grabber(g, conn=conn)
            self.db.release_grab_order(order_id, conn=conn)
            order = self.db.get_order(order_id, conn=conn)

        await self._edit_dispatch_card(
            order,
            f"{TG_X_WARN} <b>接单员已放弃，此单稍后重新派单</b>\n\n订单：<code>#{order.id}</code>",
        )
        await self.redispatch_order(order, "接单员放弃")
        if self.settings.get("grab_notify_admin"):
            await self._notify_admins(
                f"{TG_X_WARN} <b>接单员放弃订单</b>\n\n"
                f"订单：<code>#{order.id}</code>（{escape_html(order.product_name)}）\n"
                f"接单员：{escape_html(g.display())} · 已重派"
            )
        return True, f"已放弃，冷静期 {penalty_min} 分钟"

    async def check_timeouts(self) -> int:
        """超时扫描（定时任务）：释放 + 记超时 + 冷静期 + 重派。返回处理单数。"""
        now = now_iso()
        orders = self.db.timeout_grab_orders(now)
        if not orders:
            return 0
        penalty_min = int(self.settings.get("grab_penalty_minutes", 60) or 60)
        handled = 0
        for order in orders:
            try:
                with self.db.atomic() as conn:
                    # 双重确认：扫描到处理之间可能已被回单
                    fresh = self.db.get_order(order.id, conn=conn)
                    if not fresh or fresh.grab_status != "grabbed":
                        continue
                    g = self.db.get_grabber(fresh.grabber_id, conn=conn)
                    if g:
                        g.timeout_orders += 1
                        if penalty_min > 0:
                            g.cooldown_until = _iso(_now() + timedelta(minutes=penalty_min))
                        self.db.save_grabber(g, conn=conn)
                    self.db.release_grab_order(fresh.id, conn=conn)
                    fresh = self.db.get_order(fresh.id, conn=conn)

                handled += 1
                await self._edit_dispatch_card(
                    fresh,
                    f"{TG_X_ALARM} <b>超时未回单，已释放</b>\n\n订单：<code>#{fresh.id}</code>\n稍后重新派单",
                )
                if g and self._bot:
                    try:
                        await send_html(
                            self._bot, g.user_id,
                            f"{TG_X_ALARM} <b>订单超时已释放</b>\n\n"
                            f"订单：<code>#{fresh.id}</code>\n"
                            f"记超时 <code>1</code> 次，冷静期 <code>{penalty_min}</code> 分钟。\n"
                            f"如有异议可在接单中心发起申诉。",
                        )
                    except Exception:
                        pass
                await self.redispatch_order(fresh, "超时未回单")
                if self.settings.get("grab_notify_admin"):
                    await self._notify_admins(
                        f"{TG_X_ALARM} <b>派单超时已释放重派</b>\n\n"
                        f"订单：<code>#{fresh.id}</code>（{escape_html(fresh.product_name)}）\n"
                        f"原接单员：{escape_html(g.display() if g else '未知')} · 第 {fresh.dispatch_count} 次派单"
                    )
            except Exception as exc:
                logger.warning("处理超时单 %s 失败：%s", order.id, exc)
        return handled

    async def check_warns(self) -> int:
        """超时预警（定时任务）：进入预警窗口仍未回单的私聊提醒一次。"""
        s = self.settings
        warn_min = int(s.get("grab_warn_minutes", 30) or 0)
        if warn_min <= 0 or not self._bot:
            return 0
        warn_before = _iso(_now() + timedelta(minutes=warn_min))
        orders = self.db.warn_due_grab_orders(warn_before, now_iso())
        sent = 0
        for order in orders:
            try:
                self.db.mark_grab_warned(order.id)
                if order.grabber_id:
                    await send_html(
                        self._bot, order.grabber_id,
                        f"{TG_X_WARN} <b>回单时限预警</b>\n\n"
                        f"订单：<code>#{order.id}</code>（{escape_html(order.product_name)}）\n"
                        f"最晚回单：<code>{fmt_time(order.deadline)}</code>\n"
                        f"还剩不到 <code>{warn_min}</code> 分钟，请尽快回单！",
                    )
                    sent += 1
            except Exception as exc:
                logger.info("预警发送失败（订单 %s）：%s", order.id, exc)
        return sent

    # ==================================================================
    # 佣金解冻
    # ==================================================================

    async def release_due_commissions(self) -> int:
        """到期解冻（定时任务）：frozen → released，冻结余额转可提现。返回解冻笔数。

        资金安全要点：每笔都用 `expect="frozen"` 做 CAS。本方法在发送 Telegram 通知处
        会 await 让出执行权，若管理员在此期间手动解冻了后面的佣金，恢复后 CAS 不命中，
        直接跳过，不会把同一笔佣金二次计入可提现余额。
        """
        if not self.settings.get("grab_auto_release"):
            return 0
        due = self.db.due_commissions(now_iso())
        count = 0
        for cm in due:
            try:
                with self.db.atomic() as conn:
                    g = self.db.get_grabber(cm.grabber_id, conn=conn)
                    if not g:
                        # 档案被删的孤儿佣金：直接标记释放不再入账
                        self.db.set_commission_status(cm.id, "released", now_iso(), conn=conn)
                        continue
                    if not self.db.set_commission_status(
                        cm.id, "released", now_iso(), conn=conn, expect="frozen"
                    ):
                        # 已被手动解冻（或并发解冻）抢先处理，跳过记账
                        logger.info("佣金 %s 已被并发解冻，跳过", cm.id)
                        continue
                    g.balance_frozen = max(0, g.balance_frozen - cm.amount)
                    g.balance_available += cm.amount
                    self.db.save_grabber(g, conn=conn)
                count += 1
                if self._bot:
                    try:
                        await send_html(
                            self._bot, cm.grabber_id,
                            f"{TG_A_UNLOCK} <b>佣金已解冻</b>\n\n"
                            f"订单：<code>#{cm.order_id}</code>\n"
                            f"<code>{cm.amount}</code> 积分已转入可提现余额",
                        )
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("解冻佣金 %s 失败：%s", cm.id, exc)
        return count

    def release_commission_manual(self, cid: int, operator_id: int = 0) -> bool:
        """管理员手动解冻（auto_release 关闭时使用）。

        与自动解冻共用 CAS 语义：只有库里状态仍是 frozen 才记账，
        避免管理员连点两次、或与定时任务撞车时重复加余额。
        """
        with self.db.atomic() as conn:
            # 直接按主键取单，不依赖分页列表（原实现只查前 500 条冻结记录，
            # 超过 500 笔后管理员无法解冻更早的佣金）
            target = self.db.get_grab_commission(cid, conn=conn)
            if not target or target.status != "frozen":
                return False
            g = self.db.get_grabber(target.grabber_id, conn=conn)
            if not g:
                return False
            if not self.db.set_commission_status(
                cid, "released", now_iso(), conn=conn, expect="frozen"
            ):
                return False
            g.balance_frozen = max(0, g.balance_frozen - target.amount)
            g.balance_available += target.amount
            self.db.save_grabber(g, conn=conn)
            return True

    # ==================================================================
    # 提现
    # ==================================================================

    def apply_withdrawal(self, grabber_id: int, amount: int, address: str) -> tuple[Withdrawal | None, str | None]:
        """申请提现：校验余额/最低额/无在审单 → 扣可提余额 → 建档（自动按汇率折算 U）。"""
        min_amount = int(self.settings.get("grab_withdraw_min", 100) or 0)
        if amount <= 0:
            return None, "提现金额必须大于 0"
        if amount < min_amount:
            return None, f"最低提现 {min_amount} 积分"
        if not address.strip():
            return None, "请填写收款地址/账号"
        with self.db.atomic() as conn:
            g = self.db.get_grabber(grabber_id, conn=conn)
            if not g:
                return None, "接单员档案不存在"
            if g.balance_available < amount:
                return None, f"可提现余额不足（当前 {g.balance_available} 积分）"
            if self.db.has_pending_withdrawal(grabber_id, conn=conn):
                return None, "你已有一笔待审核的提现，请等管理员处理完再申请"
            w = Withdrawal(
                id=gen_withdraw_id(), grabber_id=grabber_id, amount=amount,
                amount_usdt=self.points_to_usdt(amount),
                address=address.strip(), status="pending",
            )
            self.db.create_withdrawal(w, conn=conn)
            g.balance_available -= amount
            self.db.save_grabber(g, conn=conn)
            return w, None

    async def notify_admins_withdrawal(self, w: Withdrawal) -> None:
        """提现申请通报管理员（带通过/拒绝按钮，人工打款到接单员 OK 钱包）。"""
        if not self._bot:
            return
        g = self.db.get_grabber(w.grabber_id)
        usdt = w.amount_usdt or self.points_to_usdt(w.amount)
        text = (
            f"{TG_A_MAIL} <b>新提现申请 · 待人工打款</b>\n\n"
            f"单号：<code>{w.id}</code>\n"
            f"接单员：{escape_html(g.display() if g else str(w.grabber_id))}（<code>{w.grabber_id}</code>）\n"
            f"提现积分：<code>{w.amount}</code>\n"
            f"应打款：<b><code>{usdt:.2f}</code></b> USDT\n"
            f"OK钱包ID：<code>{escape_html(w.address)}</code>\n\n"
            f"👉 打开 OK 钱包向该账户ID转 <b>{usdt:.2f} USDT</b>，打完点「通过」并发送 txid/凭证；点「拒绝」退回积分。"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("通过（已打款）", callback_data=f"a:gwdok:{w.id}",
                                 icon_custom_emoji_id=ICON_X_CHECK),
            InlineKeyboardButton("拒绝", callback_data=f"a:gwdno:{w.id}",
                                 icon_custom_emoji_id=ICON_X_SPEAK),
        ]])
        for admin in self.db.list_admins():
            try:
                await send_html(self._bot, admin.user_id, text, reply_markup=keyboard)
            except Exception as exc:
                logger.info("提现通知管理员 %s 失败：%s", admin.user_id, exc)

    def approve_withdrawal(self, wid: str, admin_id: int, txid: str = "") -> Withdrawal | None:
        """审核通过（已人工打款）。必须在事务里读-改-写。

        原实现是「读 → 判断 status == pending → 写」，三步不在事务内：
        两个管理员同时点「通过」和「拒绝」时，拒绝方在事务里把状态改成 rejected
        并退回余额，通过方随后无条件覆盖成 paid —— 用户既拿到打款又拿回积分。
        这里用 atomic + CAS 把并发窗口关掉。
        """
        with self.db.atomic() as conn:
            w = self.db.get_withdrawal(wid, conn=conn)
            if not w or w.status != "pending":
                return None
            if not self.db.set_withdrawal_status(
                wid, "paid", txid=txid.strip(), admin_id=admin_id, expect="pending", conn=conn
            ):
                return None
            w.status = "paid"
            w.txid = txid.strip()
            w.processed_by = admin_id
            w.processed_at = now_iso()
            return w

    def reject_withdrawal(self, wid: str, admin_id: int, note: str = "") -> Withdrawal | None:
        """拒绝：退回可提现余额。"""
        with self.db.atomic() as conn:
            w = self.db.get_withdrawal(wid, conn=conn)
            if not w or w.status != "pending":
                return None
            w.status = "rejected"
            w.note = note.strip()
            w.processed_by = admin_id
            w.processed_at = now_iso()
            self.db.save_withdrawal(w, conn=conn)
            g = self.db.get_grabber(w.grabber_id, conn=conn)
            if g:
                g.balance_available += w.amount
                self.db.save_grabber(g, conn=conn)
            return w

    # ==================================================================
    # 申诉
    # ==================================================================

    def create_appeal(self, grabber_id: int, order_id: str, content: str) -> tuple[GrabAppeal | None, str | None]:
        content = content.strip()
        if not content:
            return None, "申诉内容不能为空"
        if len(content) > 500:
            return None, "申诉内容太长（500 字以内）"
        g = self.db.get_grabber(grabber_id)
        if not g:
            return None, "接单员档案不存在"
        aid = self.db.create_grab_appeal(
            GrabAppeal(grabber_id=grabber_id, order_id=order_id, content=content, status="pending")
        )
        return self.db.get_grab_appeal(aid), None

    async def notify_admins_appeal(self, appeal: GrabAppeal) -> None:
        if not self._bot:
            return
        g = self.db.get_grabber(appeal.grabber_id)
        text = (
            f"{TG_X_BELL} <b>新申诉待裁定</b>\n\n"
            f"申诉号：<code>#{appeal.id}</code>\n"
            f"接单员：{escape_html(g.display() if g else str(appeal.grabber_id))}\n"
            f"关联订单：<code>#{escape_html(appeal.order_id or '无')}</code>\n"
            f"内容：<blockquote>{escape_html(appeal.content)}</blockquote>"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("支持申诉", callback_data=f"a:gapok:{appeal.id}",
                                 icon_custom_emoji_id=ICON_X_CHECK),
            InlineKeyboardButton("驳回", callback_data=f"a:gapno:{appeal.id}",
                                 icon_custom_emoji_id=ICON_X_SPEAK),
        ]])
        for admin in self.db.list_admins():
            try:
                await send_html(self._bot, admin.user_id, text, reply_markup=keyboard)
            except Exception as exc:
                logger.info("申诉通知管理员 %s 失败：%s", admin.user_id, exc)

    def handle_appeal(self, aid: int, admin_id: int, approve: bool, reply: str = "") -> GrabAppeal | None:
        """裁定：支持申诉顺带清掉接单员冷静期（若是超时判罚引发的）。"""
        with self.db.atomic() as conn:
            a = self.db.get_grab_appeal(aid, conn=conn)
            if not a or a.status != "pending":
                return None
            a.status = "approved" if approve else "rejected"
            a.reply = reply.strip()
            a.handled_by = admin_id
            a.handled_at = now_iso()
            self.db.save_grab_appeal(a, conn=conn)
            if approve:
                g = self.db.get_grabber(a.grabber_id, conn=conn)
                if g and g.cooldown_until:
                    g.cooldown_until = ""
                    self.db.save_grabber(g, conn=conn)
            return a

    # ==================================================================
    # 退群自动停权（防偷老板配套：人不在群里就不许接单）
    # ==================================================================

    async def handle_member_left(self, chat_id: int | str, user_id: int) -> bool:
        """接单群成员离开 → 接单员自动停权 + 进群状态归零。返回是否触发了停权。"""
        if str(chat_id) != self.chat_id():
            return False
        g = self.db.get_grabber(user_id)
        if not g or g.status in ("blacklist",):
            return False
        g.joined_group = 0
        changed = g.status != "suspended"
        if changed:
            g.status = "suspended"
        self.db.save_grabber(g)
        if changed:
            if self._bot:
                try:
                    await send_html(
                        self._bot, user_id,
                        f"{TG_X_WARN} <b>你已退出接单群，账号已自动停权</b>\n\n"
                        f"重新入群后请联系管理员恢复在岗。",
                    )
                except Exception:
                    pass
            await self._notify_admins(
                f"{TG_X_WARN} <b>接单员退群自动停权</b>\n\n"
                f"接单员：{escape_html(g.display())}（<code>{user_id}</code>）"
            )
        return changed

    # ==================================================================
    # 专属入群链接（v2.6.1）：一人一条一次性链接，进群即作废，三方提示
    # ==================================================================

    def _chat_ref(self) -> int | str:
        """接单群 ID：纯数字转 int，否则原样（支持 @username 形式）。"""
        chat = self.chat_id()
        return int(chat) if chat.lstrip("-").isdigit() else chat

    async def ensure_invite_link(self, g: Grabber, force: bool = False) -> tuple[str, str]:
        """为接单员生成专属入群链接（member_limit=1，用过即废）。返回 (链接, 错误)。"""
        if not self._bot:
            return "", "机器人未就绪"
        if not self.chat_id():
            return "", "还没配置接单群"
        if g.invite_link and not force and not g.joined_group:
            return g.invite_link, ""
        try:
            link = await self._bot.create_chat_invite_link(
                chat_id=self._chat_ref(), member_limit=1, name=f"GB{g.seq:04d}",
            )
        except Exception as exc:
            logger.warning("创建专属入群链接失败（接单员 %s）：%s", g.user_id, exc)
            return "", "生成失败：请确认机器人是接单群管理员且有「邀请用户」权限"
        g.invite_link = link.invite_link
        g.joined_group = 0
        self.db.save_grabber(g)
        return g.invite_link, ""

    async def notify_grabber_welcome(self, g: Grabber) -> str:
        """添加接单员后的三连提示：欢迎卡 + 专属链接卡 + 接单须知。返回链接生成错误（空=成功）。"""
        if not self._bot:
            return "机器人未就绪"
        s = self.settings
        # 第 1 条：欢迎卡
        try:
            await send_html(
                self._bot, g.user_id,
                f"{TG_A_CHEERS} <b>欢迎加入接单团队</b>\n"
                f"━━━━━━━━━━━━━━\n"
                f"🪪 你的编号：<code>{g.display()}</code>\n"
                f"{TG_X_MONEY} 佣金比例：订单金额 <code>{s.get('grab_commission_pct', 20)}%</code>\n"
                f"{TG_X_ALARM} 回单时限：抢中后 <code>{s.get('grab_timeout_hours', 24)}</code> 小时内\n"
                f"━━━━━━━━━━━━━━\n"
                f"{TG_X_NINJA} 发送 /grabber 随时打开接单员中心",
            )
        except Exception as exc:
            logger.info("接单员欢迎卡发送失败 %s：%s", g.user_id, exc)
            return "私聊消息发不出去（对方可能没和机器人说过话），请让接单员先 /start 一次"
        # 第 2 条：专属链接卡
        link, err = await self.ensure_invite_link(g)
        if link:
            try:
                await send_html(
                    self._bot, g.user_id,
                    f"{TG_A_KEY} <b>你的专属入群链接</b>\n\n"
                    f"<blockquote>此链接只限你本人使用一次，进群即自动作废，切勿转发他人，否则会被踢出并停权。</blockquote>",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔗 点我进入接单群", url=link,
                                             icon_custom_emoji_id=ICON_X_NINJA),
                    ]]),
                )
            except Exception as exc:
                logger.info("专属链接卡发送失败 %s：%s", g.user_id, exc)
        # 第 3 条：接单须知
        try:
            await send_html(
                self._bot, g.user_id,
                f"{TG_A_STAR} <b>接单须知</b>\n\n"
                f"1️⃣ 新报单会实时推送到接单群，点「立即抢单」先到先得\n"
                f"2️⃣ 抢中后点「去接单」跳回本机器人查看买家需求\n"
                f"3️⃣ 按时回单，佣金冻结 {s.get('grab_freeze_hours', 24)} 小时后自动到账可提现\n"
                f"4️⃣ 超时/放弃会进冷静期，情节严重拉黑名单\n"
                f"5️⃣ 退群会自动停权，请勿退群",
            )
        except Exception:
            pass
        return err

    async def handle_member_joined(
        self, chat_id: int | str, user_id: int, username: str, name: str, invite_link: str = ""
    ) -> str:
        """接单群新成员进群处理。

        返回结果码：ok 本人专属链接进群 / self_join 接单员自行入群 /
        leaked 专属链接被他人使用（已踢出）/ stranger 非接单员入群 / ignored 非接单群。
        """
        if str(chat_id) != self.chat_id():
            return "ignored"
        g = self.db.get_grabber_by_invite(invite_link)
        if g and g.user_id == user_id:
            # 本人持专属链接进群：标记 + 作废链接 + 本人/群内/管理员三方提示
            g.joined_group = 1
            g.username = username or g.username
            g.name = name or g.name
            old_link = g.invite_link
            self.db.save_grabber(g)
            if old_link and self._bot:
                try:
                    await self._bot.revoke_chat_invite_link(
                        chat_id=self._chat_ref(), invite_link=old_link
                    )
                except Exception as exc:
                    logger.info("作废专属链接失败（member_limit=1 通常已自动失效）：%s", exc)
            if self._bot:
                try:
                    await send_html(
                        self._bot, user_id,
                        f"{TG_X_CHECK} <b>您已加入接单群</b>\n\n"
                        f"当前您的专属邀请链接已作废。\n"
                        f"新单推送后点「立即抢单」即可开工，发送 /grabber 打开接单员中心。",
                    )
                except Exception:
                    pass
                try:
                    await send_html(
                        self._bot, self._chat_ref(),
                        f"{TG_X_NINJA} 接单员 <b>#{g.seq:04d}</b> 已就位，欢迎加入 🎉",
                    )
                except Exception:
                    pass
            await self._notify_admins(
                f"{TG_X_CHECK} <b>接单员已入群</b>\n\n"
                f"接单员：{escape_html(g.display())}（<code>{user_id}</code>）\n"
                f"专属邀请链接已作废，无法再次使用。"
            )
            return "ok"

        if g and g.user_id != user_id:
            # 专属链接被他人使用：踢出 + 通报管理员
            if self._bot:
                try:
                    await self._bot.ban_chat_member(chat_id=self._chat_ref(), user_id=user_id)
                    await self._bot.unban_chat_member(chat_id=self._chat_ref(), user_id=user_id)
                except Exception as exc:
                    logger.warning("踢出冒名入群者失败 %s：%s", user_id, exc)
            await self._notify_admins(
                f"{TG_X_WARN} <b>专属链接被他人使用，已踢出</b>\n\n"
                f"冒名者：{escape_html(name or username or str(user_id))}（<code>{user_id}</code>）\n"
                f"链接归属：{escape_html(g.display())}\n"
                f"建议：到接单员详情页点「重发链接」给本人换一条新链接。"
            )
            return "leaked"

        g2 = self.db.get_grabber(user_id)
        if g2:
            # 接单员通过别的途径进群：照样标记，提示补专属链接
            g2.joined_group = 1
            g2.username = username or g2.username
            g2.name = name or g2.name
            self.db.save_grabber(g2)
            if self._bot:
                try:
                    await send_html(
                        self._bot, user_id,
                        f"{TG_X_CHECK} <b>您已加入接单群</b>\n\n发送 /grabber 打开接单员中心。",
                    )
                except Exception:
                    pass
            await self._notify_admins(
                f"{TG_X_BELL} <b>接单员已入群（非专属链接）</b>\n\n"
                f"接单员：{escape_html(g2.display())}（<code>{user_id}</code>）\n"
                f"提示：Ta 未使用专属链接，如为管理员直接拉入属正常。"
            )
            return "self_join"

        await self._notify_admins(
            f"{TG_X_WARN} <b>有非接单员进入接单群</b>\n\n"
            f"用户：{escape_html(name or username or str(user_id))}（<code>{user_id}</code>）\n"
            f"如非本人操作，请检查群内其他邀请链接是否泄漏。"
        )
        return "stranger"

    # ==================================================================
    # 机器人入群自动识别接单群（v2.6.1 零配置）
    # ==================================================================

    async def handle_bot_added(self, chat_id: int, chat_title: str) -> None:
        """机器人被拉进群/升为管理员时触发：自动识别接单群并通报管理员。"""
        if not self._bot:
            return
        cid = str(chat_id)
        current = self.chat_id()
        if current == cid:
            await self._notify_admins(
                f"{TG_X_CHECK} <b>机器人已在接单群就位</b>\n\n"
                f"群名：<b>{escape_html(chat_title)}</b>\n群ID：<code>{cid}</code>\n"
                f"报单会照常推送到本群。"
            )
            return
        if not current:
            # 从未配置过接单群：自动设为接单群并打开总开关（零配置）
            self.db.set_setting("grab_chat_id", cid)
            self.db.set_setting("grab_enabled", "1")
            try:
                await send_html(
                    self._bot, chat_id,
                    f"{TG_A_CHEERS} <b>派单机器人已就位</b>\n\n"
                    f"本群已被自动设为 <b>接单群</b>，新报单会实时推送到这里。\n"
                    f"接单员请使用各自的专属入群链接进群，入群后静待新单即可。",
                )
            except Exception as exc:
                logger.info("接单群就位公告发送失败：%s", exc)
            await self._notify_admins(
                f"{TG_A_STAR} <b>已自动识别接单群</b>\n\n"
                f"群名：<b>{escape_html(chat_title)}</b>\n"
                f"群ID：<code>{cid}</code>\n"
                f"派单总开关：已自动打开 ✅\n\n"
                f"如识别有误，到「🥷 接单管理 → ⚙️ 抢单设置」改群ID或关闭总开关即可。"
            )
            return
        # 已配置过别的群：给管理员一键换群按钮
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 换到这个群", callback_data=f"a:guse:{cid}",
                                 icon_custom_emoji_id=ICON_X_CHECK),
        ]])
        await self._notify_admins(
            f"{TG_X_BELL} <b>机器人加入了新群</b>\n\n"
            f"群名：<b>{escape_html(chat_title)}</b>\n群ID：<code>{cid}</code>\n\n"
            f"当前接单群：<code>{escape_html(current)}</code>\n"
            f"如需把接单群换成这个新群，点下方按钮即可。",
            reply_markup=keyboard,
        )

    def confirm_grab_chat(self, chat_id: str) -> None:
        """管理员确认换群：写入群ID并打开总开关。"""
        self.db.set_setting("grab_chat_id", str(chat_id))
        self.db.set_setting("grab_enabled", "1")

    # ==================================================================
    # 内部：管理员通知
    # ==================================================================

    async def _notify_admins(self, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        if not self._bot:
            return
        for admin in self.db.list_admins():
            try:
                await send_html(self._bot, admin.user_id, text, reply_markup=reply_markup)
            except Exception as exc:
                logger.info("抢单通报管理员 %s 失败：%s", admin.user_id, exc)
