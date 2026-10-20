"""命令入口（v2.4.0 模块化拆分）：/start、/admin 与全局错误兜底。

- cmd_start：注册/建档 → 封禁拦截 → 强制关注检查 → deep link（供需）→ 主菜单（可带欢迎图）
- cmd_admin：管理员面板快捷入口
- on_error：全局异常兜底——任何 handler 抛错都只记日志并给用户一句提示，
  不让用户面对「死机」无响应，也在日志里留下完整堆栈便于排查。
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from .runtime import db, grab, service
from .ui import clear_state, kb
from .pages_user import page_force_join, page_root
from .pages_supply import page_supply_new
from .pages_admin import page_admin
from .pages_grabber import page_grab_home

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    uid = update.effective_user.id
    service.get_or_create_user(
        uid,
        username=update.effective_user.username or "",
        first_name=update.effective_user.first_name or "",
        last_name=update.effective_user.last_name or "",
    )
    clear_state(context)

    user = db.get_user(uid)
    if user and user.is_banned:
        await update.message.reply_text("🚫 您的账号已被限制使用")
        return

    ok, missing = await service.check_user_channels(uid)
    if not ok:
        text, keyboard = page_force_join(missing)
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    # ---------- deep link：/start supply（频道「我也要发供需」按钮跳进来） ----------
    args = getattr(context, "args", None) or []
    if args and args[0] == "supply":
        text, keyboard = page_supply_new(uid)
        await update.message.reply_text(
            "👋 欢迎！你是从供需频道来的，直接开始发布吧\n\n" + text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    # ---------- deep link：/start grab_{订单号}（群内抢中后「去接单」落地页） ----------
    if args and args[0].startswith("grab_"):
        oid = args[0][5:]
        order = db.get_order(oid)
        g = db.get_grabber(uid)
        if not g:
            await update.message.reply_text(
                "⚠️ 你还不是接单员，请联系管理员开通",
                reply_markup=kb([[("🏠 主菜单", "m:root")]]),
            )
            return
        if not order or order.grab_status != "grabbed":
            await update.message.reply_text(
                "⚠️ 这单已不在进行中（可能超时释放或已回单）",
                reply_markup=kb([[("🥷 接单员中心", "gb:m")], [("🏠 主菜单", "m:root")]]),
            )
            return
        if order.grabber_id != uid:
            await update.message.reply_text(
                "⚠️ 这单不是你抢的，动别人单子小心被封哦",
                reply_markup=kb([[("🥷 接单员中心", "gb:m")]]),
            )
            return
        # 本人落地：直接推回单卡（含需求全文 + 回单/放弃按钮）
        await grab.send_reply_card(order, g)
        return

    settings = db.get_settings()
    text, keyboard = page_root(uid)
    welcome_image = settings.get("welcome_image", "")
    if welcome_image:
        try:
            await update.message.reply_photo(photo=welcome_image, caption=text, reply_markup=keyboard, parse_mode="HTML")
            return
        except Exception:
            pass
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    uid = update.effective_user.id
    if not db.is_admin(uid):
        await update.message.reply_text("❌ 无权限")
        return
    clear_state(context)
    text, keyboard = page_admin(uid)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def cmd_grabber(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/grabber 接单员中心快捷入口（v2.6.0）。"""
    if not update.effective_user or not update.message:
        return
    clear_state(context)
    text, keyboard = page_grab_home(update.effective_user.id)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def on_grab_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """接单群成员变动监听：接单员退群自动停权（防偷老板配套）。

    需要机器人是接单群管理员才能收到 chat_member 更新；收不到也不影响主流程。
    """
    upd = update.chat_member
    if not upd:
        return
    new_status = getattr(upd.new_chat_member, "status", "")
    if new_status not in ("left", "kicked"):
        return
    user = getattr(upd.new_chat_member, "user", None)
    if not user:
        return
    await grab.handle_member_left(upd.chat.id, user.id)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """全局错误兜底：记录完整堆栈；尽力给用户一句「开小差」提示，不静默吞掉。"""
    logger.error("handler 未捕获异常：%s", context.error, exc_info=context.error)
    try:
        if isinstance(update, Update):
            if update.callback_query:
                await update.callback_query.answer("⚠️ 开小差了，请再点一次", show_alert=True)
            elif update.message:
                await update.message.reply_text("⚠️ 系统开小差了，请重试或联系管理员")
    except Exception:
        pass
