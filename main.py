"""G Shop Bot v2.6.0 — 自助报单 + 积分充值 + 抢单派单机器人（模块化版）。

自 v2.4.0 起，单体 main.py 拆分为 examples/ 包内的职责模块：

  runtime.py          运行时单例：db / service / supply / stock / grab 的唯一实例
  ui.py               键盘构造、HTML 转义、安全发送、输入状态管理
  pages_user.py       用户端页面：主菜单/商品/购买/充值/订单/帮助/搜索/到货提醒
  pages_supply.py     供需页面：用户端大厅与发布向导 + 管理端审核/开关/定价
  pages_admin.py      管理端页面：商品/订单/用户/财务/设置/支付设置/日志/接单员大分类
  pages_grabber.py    接单员页面（v2.6.0）：中心/进行中/完单/钱包/提现/排行/申诉
  pages_stock.py      库存池页面：总览/补货向导/单件管理
  handlers_command.py /start /admin 命令 + 抢单深链落地页 + 全局错误兜底
  handlers_callback.py 所有按钮回调的唯一入口（含群内抢单按钮 gb:g:*）
  handlers_message.py 文本输入状态机 + 管理员引用回单 + 接单员回单 + 补货向导
  jobs.py             后台任务：充值轮询 / 链上监听 / 抢单时效三件套 / 经营日报 / 自动备份
  models.py           数据层（SQLite，WAL + 原子事务）
  shop_service.py     商城业务层（购买/退款/充值/冷钱包/广播）
  grab_service.py     抢单派单业务层（v2.6.0：匿名派单/原子抢单/佣金结算/提现/风控）
  supply_service.py   供需业务层
  stock_service.py    库存池业务层（补货/原子售卖/失败回滚/到货提醒）
  richtext.py         富文本与自定义 emoji 图标（星耀会员表情库）
  okpay.py / kkpay.py 支付渠道
  chain_watch.py      冷钱包链上监听（v2.5.0：TRON/TON 只读 API + 地址校验）

本文件只负责装配：建 Application、挂 handler、拉起后台任务。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from examples.handlers_callback import on_callback
from examples.handlers_command import cmd_admin, cmd_grabber, cmd_start, on_error
from examples.handlers_message import on_message
from examples.jobs import (
    auto_backup,
    auto_check_chain_recharges,
    auto_check_recharges,
    auto_grab_housekeeping,
    daily_report,
)
from examples.runtime import ADMIN_ID, BOT_TOKEN, VERSION, grab, service, stock

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(app: Application) -> None:
    """启动后拉起常驻后台任务（每个任务内部自带异常兜底，不会退出）。"""
    asyncio.create_task(auto_check_recharges())
    asyncio.create_task(auto_check_chain_recharges())  # v2.5.0 冷钱包链上监听
    asyncio.create_task(auto_grab_housekeeping())      # v2.6.0 抢单超时/预警/佣金解冻
    asyncio.create_task(daily_report())
    asyncio.create_task(auto_backup())


async def on_grab_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """接单群成员变动监听：进群校验专属链接 / 退群自动停权（防偷老板）。

    v2.6.1：进群（member/administrator）→ 核对接单员专属链接，本人进群即
    作废链接并三方提示，冒名进群直接踢出；退群/被踢 → 自动停权。
    注意：机器人必须是接单群管理员，才能收到群成员的 chat_member 更新。
    """
    upd = update.chat_member
    if not upd or not upd.new_chat_member:
        return
    try:
        old = upd.old_chat_member.status if upd.old_chat_member else ""
        new = upd.new_chat_member.status
        if new in ("left", "kicked"):
            await grab.handle_member_left(upd.chat.id, upd.new_chat_member.user.id)
        elif new in ("member", "administrator") and old not in ("member", "administrator", "creator"):
            u = upd.new_chat_member.user
            link = upd.invite_link.invite_link if upd.invite_link else ""
            await grab.handle_member_joined(
                upd.chat.id, u.id, u.username or "", u.full_name or "", link
            )
    except Exception as exc:
        logger.warning("接单群成员变动处理失败：%s", exc)


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """机器人被拉进群/升为管理员（v2.6.1）：自动识别接单群并通报管理员。"""
    upd = update.my_chat_member
    if not upd or not upd.new_chat_member:
        return
    if upd.chat.type not in ("group", "supergroup"):
        return
    if upd.new_chat_member.status not in ("member", "administrator"):
        return
    try:
        await grab.handle_bot_added(upd.chat.id, upd.chat.title or str(upd.chat.id))
    except Exception as exc:
        logger.warning("机器人入群识别处理失败：%s", exc)


# Telegram Bot API 的「默认更新集合」明确不含 chat_member（官方文档 getUpdates 一节：
# 空 allowed_updates 表示除 chat_member / message_reaction / message_reaction_count 外的全部）。
# 不显式订阅的话，ChatMemberHandler.CHAT_MEMBER 永远收不到事件，退群停权、
# 专属入群链接校验、入群识别这些风控逻辑全部静默失效。这里显式列出所需类型。
ALLOWED_UPDATES = [
    Update.MESSAGE,         # 命令与私聊消息
    Update.CALLBACK_QUERY,  # 按钮回调
    Update.CHAT_MEMBER,     # 接单群成员变动（退群停权 / 邀请链接校验）
    Update.MY_CHAT_MEMBER,  # 机器人自身被拉群 / 升为管理员
]


def main() -> None:
    if not BOT_TOKEN:
        logger.error("请在 .env 中设置 BOT_TOKEN")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    # supply 复用 shop 的 bot（见 SupplyService._shop），无需单独注入
    service.set_bot(app.bot)
    stock.set_bot(app.bot)
    grab.set_bot(app.bot)  # v2.6.0 抢单派单

    private_msg = filters.ChatType.PRIVATE & ~filters.COMMAND

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("grabber", cmd_grabber))  # v2.6.0 接单员中心
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(private_msg, on_message))
    # 接单群成员变动（进群校验专属链接 / 退群自动停权）；机器人需在接单群内为管理员才能收到该更新
    app.add_handler(ChatMemberHandler(on_grab_chat_member, ChatMemberHandler.CHAT_MEMBER))
    # 机器人入群/升管理员 → 自动识别接单群（v2.6.1 零配置）
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_error_handler(on_error)

    logger.info("🤖 G Shop Bot v%s 已启动（模块化版）", VERSION)
    logger.info("管理员 ID: %s", ADMIN_ID)

    app.run_polling(allowed_updates=ALLOWED_UPDATES)


if __name__ == "__main__":
    main()
