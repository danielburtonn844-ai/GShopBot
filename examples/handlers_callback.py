"""回调分发（v2.4.0 模块化拆分）：所有按钮点击的唯一入口。

回调命名空间：
  m:*            用户端菜单（m:shop:{i} / m:report:{i} 带分类筛选；m:search:{kind} 搜索）
  d:/buy:/cbuy:  商品详情与购买（池模式下单后私信发货，失败自动回滚退款）
  od:/urge:      用户订单；redeliver: 池内容重发；rsub: 到货提醒订阅
  rc:/rcpay:/rcchk:  充值流程
  sup:*          供需大厅与发布向导
  a:*            管理端（a:pool*/a:stk* 库存池；a:pcat* 商品分类；a:logs 操作日志）
  padd_kind:/padd_mode:  上架向导按钮步骤
"""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .models import STOCK_TYPES, SUPPLY_CHANNELS, now_iso
from .pages_admin import (
    _GRAB_SETTING_ITEMS,
    page_admin,
    page_admin_admins,
    page_admin_broadcast,
    page_admin_channels,
    page_admin_finance,
    page_admin_grab,
    page_admin_grab_appeals,
    page_admin_grab_orders,
    page_admin_grab_settings,
    page_admin_grab_withdrawals,
    page_admin_grabber_detail,
    page_admin_grabbers,
    page_admin_logs,
    page_admin_order,
    page_admin_order_detail,
    page_admin_payment,
    page_admin_pcats,
    page_admin_pdel_confirm,
    page_admin_pedit,
    page_admin_prod,
    page_admin_refund_confirm,
    page_admin_settings,
    page_admin_user,
    page_admin_user_detail,
    page_admin_user_orders,
    page_admin_user_points,
)
from .pages_grabber import (
    page_grab_appeals,
    page_grab_doing,
    page_grab_done,
    page_grab_home,
    page_grab_money,
    page_grab_rank,
    page_grab_withdraw,
)
from .pages_stock import (
    page_admin_pool,
    page_admin_pool_clear_confirm,
    page_admin_pool_item,
    page_admin_pool_prod,
    restock_keyboard,
    restock_prompt,
)
from .pages_supply import (
    SUPPLY_SWITCH_ON,
    SUPPLY_SWITCHES,
    page_admin_supply,
    page_admin_supply_cats,
    page_admin_supply_pdel,
    page_admin_supply_post,
    page_admin_supply_posts,
    page_admin_supply_price,
    page_admin_supply_review,
    page_admin_supply_sw,
    page_supply_category,
    page_supply_hall,
    page_supply_mine,
    page_supply_new,
    page_supply_preview,
    supply_content_prompt,
)
from .pages_user import (
    page_balance,
    page_buy_confirm,
    page_chain_pay,
    page_detail,
    page_force_join,
    page_help,
    page_list,
    page_order_detail,
    page_orders,
    page_profile,
    page_recharge,
    page_recharge_confirm,
    page_recharge_history,
    page_root,
)
from .richtext import TG_CHECK
from .runtime import db, grab, service, stock, supply
from .shop_service import BUY_FAIL_REASONS, CHAIN_GATEWAYS, GATEWAY_NAMES, PAY_GATEWAYS
from .supply_service import CREATE_FAIL_REASONS
from .ui import audit, clear_state, h, kb, send_or_edit

logger = logging.getLogger(__name__)


def _resolve_product_category(data: str) -> str:
    """m:shop:{i} / m:report:{i} → 分类名（下标越界/空 = 全部）。"""
    parts = data.split(":")
    if len(parts) < 3 or not parts[2]:
        return ""
    cats = db.get_settings().get("product_categories") or []
    idx = parts[2]
    return cats[int(idx)] if idx.isdigit() and int(idx) < len(cats) else ""


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tg_user = update.effective_user
    uid = tg_user.id
    data = query.data or ""

    service.get_or_create_user(
        uid,
        username=tg_user.username or "",
        first_name=tg_user.first_name or "",
        last_name=tg_user.last_name or "",
    )

    # ---------- 输入状态清理 ----------
    # 处于输入状态（等用户发消息）时点击任何按钮，视为放弃当前输入并清掉状态，
    # 防止用户点了「取消」后随手发一条消息仍被当成提交内容处理。
    # 例外：padd_kind:/padd_mode:/a:bcsend/sup:/a:stkdone: 是流程内按钮，必须保留状态与 payload。
    if context.user_data.get("state") and not data.startswith(
        ("padd_kind:", "padd_mode:", "a:bcsend", "sup:", "a:stkdone:")
    ):
        clear_state(context)

    # ---------- 用户端导航 ----------
    if data == "m:root":
        ok, missing = await service.check_user_channels(uid)
        if not ok:
            text, keyboard = page_force_join(missing)
        else:
            text, keyboard = page_root(uid)
        await send_or_edit(query, text, keyboard)
        return

    if data == "m:checkjoin":
        ok, missing = await service.check_user_channels(uid)
        if not ok:
            text, keyboard = page_force_join(missing)
            await send_or_edit(query, "❌ 还未全部加入\n\n" + text, keyboard)
        else:
            text, keyboard = page_root(uid)
            await send_or_edit(query, text, keyboard)
        return

    if data == "m:shop" or data.startswith("m:shop:"):
        text, keyboard = page_list(uid, "product", _resolve_product_category(data))
        await send_or_edit(query, text, keyboard)
        return

    if data == "m:report" or data.startswith("m:report:"):
        text, keyboard = page_list(uid, "report", _resolve_product_category(data))
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("m:search:"):
        kind = "report" if data.endswith(":report") else "product"
        context.user_data["state"] = f"search:{kind}"
        context.user_data["payload"] = {}
        await send_or_edit(
            query,
            "🔍 <b>搜索商品</b>\n\n请直接发送关键词（匹配名称/介绍/分类）：",
            kb([[("❌ 取消", "m:shop" if kind == "product" else "m:report")]]),
        )
        return

    if data == "m:recharge":
        text, keyboard = page_recharge(uid)
        await send_or_edit(query, text, keyboard)
        return

    if data == "m:rchist":
        text, keyboard = page_recharge_history(uid)
        await send_or_edit(query, text, keyboard)
        return

    if data == "m:orders":
        text, keyboard = page_orders(uid)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("m:ofl:"):
        text, keyboard = page_orders(uid, data[6:])
        await send_or_edit(query, text, keyboard)
        return

    if data == "m:balance":
        text, keyboard = page_balance(uid)
        await send_or_edit(query, text, keyboard)
        return

    if data == "m:profile":
        text, keyboard = page_profile(uid)
        await send_or_edit(query, text, keyboard)
        return

    if data == "m:help":
        text, keyboard = page_help()
        await send_or_edit(query, text, keyboard)
        return

    if data == "m:admin":
        text, keyboard = page_admin(uid)
        await send_or_edit(query, text, keyboard)
        return

    # ---------- 商品详情 / 购买 ----------
    if data.startswith("d:"):
        text, keyboard, photo = page_detail(uid, data[2:])
        await send_or_edit(query, text, keyboard, photo=photo)
        return

    if data.startswith("buy:"):
        text, keyboard = page_buy_confirm(uid, data[4:])
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("cbuy:"):
        pid = data[5:]
        user = db.get_user(uid)
        if user and user.is_banned:
            await send_or_edit(query, "🚫 您的账号已被限制，无法购买。如有疑问请联系管理员", kb([[("🏠 主菜单", "m:root")]]))
            return
        settings = db.get_settings()
        if settings.get("maintenance_mode") and not db.is_admin(uid):
            await send_or_edit(query, "🔧 系统维护中，暂时无法下单（充值不受影响）", kb([[("🏠 主菜单", "m:root")]]))
            return
        p = db.get_product(pid)
        if not p or p.status != "on":
            await send_or_edit(query, "❌ 该条目不存在或已下架", kb([[("🏠 主菜单", "m:root")]]))
            return

        # ---------- 报单类：先校验，再进入「提交所需信息」步骤（必填） ----------
        if p.kind == "report":
            reason = service.check_purchase(uid, p)
            if reason:
                text = f"❌ 无法购买：{BUY_FAIL_REASONS.get(reason, '未知原因')}"
                buttons = []
                if reason == "points":
                    buttons.append([("💰 去充值", "m:recharge")])
                buttons.append([("⬅️ 返回", f"d:{p.id}")])
                await send_or_edit(query, text, kb(buttons))
                return
            context.user_data["state"] = f"submit_info:{p.id}"
            context.user_data["payload"] = {}
            lines = [
                "<b>📝 提交所需信息</b>",
                "",
                f"商品：{h(p.name)}",
                f"价格：<code>{p.points}</code> 积分",
            ]
            if p.description:
                lines += ["", "<b>📖 商品介绍（请按介绍要求提供信息）：</b>", f"<i>{h(p.description)}</i>"]
            lines += [
                "",
                "✏️ 请直接发送所需信息，支持文字/图片/文件等任意类型",
                "⚠️ 此步骤为<b>必填</b>，提交后立即扣积分并生成订单，信息与订单一并发给管理员处理",
            ]
            await send_or_edit(query, "\n".join(lines), kb([[("❌ 取消", f"d:{p.id}")]]))
            return

        # ---------- 商品类：立即下单，自动发货 ----------
        order, reason = service.create_order(uid, pid)
        if not order:
            text = f"❌ 购买失败：{BUY_FAIL_REASONS.get(reason or '', '未知原因')}"
            buttons = []
            if reason == "points":
                cur = db.get_user(uid)
                text += f"\n\n当前积分：<code>{cur.points if cur else 0}</code>，还差 <code>{p.points - (cur.points if cur else 0)}</code>"
                buttons.append([("💰 去充值", "m:recharge")])
            elif reason == "sold_out" and getattr(p, "delivery_mode", "fixed") == "pool":
                buttons.append([("🔔 到货提醒", f"rsub:1:{p.id}")])
            buttons.append([("⬅️ 返回", f"d:{p.id}")])
            await send_or_edit(query, text, kb(buttons))
            return

        # ---------- 库存池商品：私信送达，失败自动回滚退款 ----------
        item_id = getattr(order, "stock_item_id", 0)
        if item_id:
            item = db.get_stock_item(item_id)
            delivered = await stock.deliver_item(uid, item) if item else False
            if not delivered:
                fresh = db.get_order(order.id)
                if fresh:
                    await stock.rollback_sale(fresh, "买家私信发送失败")
                await send_or_edit(
                    query,
                    f"❌ <b>发货失败</b>\n\n订单 <code>{order.id}</code> 已自动全额退款，积分分文不少\n请重新下单或联系管理员",
                    kb([[("⬅️ 返回商品", f"d:{p.id}")], [("🏠 主菜单", "m:root")]]),
                )
                return
            mark = STOCK_TYPES.get(item.content_type, item.content_type) if item else ""
            text = f"{TG_CHECK} <b>购买成功</b>\n\n订单号：<code>{order.id}</code>\n📦 专属内容：<b>编号 #{item_id}</b>（{mark}）"
            if order.reply_content and item and item.content_type == "text":
                text += f"\n\n<code>{h(order.reply_content)}</code>"
            else:
                text += "\n\n内容已私信送达，可在订单详情随时「重新发送」"
            text += "\n\n💡 可在「我的订单」中随时查看"
            await send_or_edit(query, text, kb([[("📋 我的订单", "m:orders")], [("🏠 主菜单", "m:root")]]))
            return

        text = f"{TG_CHECK} <b>购买成功</b>\n\n订单号：<code>{order.id}</code>"
        if order.reply_content:
            text += f"\n\n📤 发货内容：\n{h(order.reply_content)}"
        text += "\n\n💡 可在「我的订单」中随时查看"
        await send_or_edit(query, text, kb([[("📋 我的订单", "m:orders")], [("🏠 主菜单", "m:root")]]))
        return

    # ---------- 用户订单 ----------
    if data.startswith("od:"):
        text, keyboard = page_order_detail(uid, data[3:])
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("urge:"):
        oid = data[5:]
        order = db.get_order(oid)
        if order and order.user_id == uid and order.status in ("pending", "processing"):
            await service.notify_admins_urge(order, uid)
            text = "✅ 催单成功，管理员会尽快处理"
        else:
            text = "❌ 无法催单"
        await send_or_edit(query, text, kb([[("⬅️ 返回", f"od:{oid}")]]))
        return

    # ---------- 库存池：内容重发 / 到货提醒 ----------
    if data.startswith("redeliver:"):
        oid = data[10:]
        o = db.get_order(oid)
        item = db.get_stock_item(o.stock_item_id) if o and o.user_id == uid and o.stock_item_id else None
        if not o or o.user_id != uid:
            text = "❌ 订单不存在"
        elif not item:
            text = "❌ 购得内容记录已清理，无法重发"
        else:
            ok = await stock.deliver_item(uid, item)
            text = f"✅ 编号 #{item.id} 已重新发送，请查收私信" if ok else "❌ 发送失败，请稍后重试"
        t2, k2 = page_order_detail(uid, oid)
        await send_or_edit(query, text + "\n\n" + t2, k2)
        return

    if data.startswith("rsub:"):
        parts = data.split(":", 2)
        flag, pid = parts[1], parts[2]
        if flag == "1":
            db.add_restock_sub(pid, uid)
            note = "🔔 已订阅到货提醒，补货后第一时间私信通知你"
        else:
            db.remove_restock_sub(pid, uid)
            note = "🔕 已取消到货提醒"
        text, keyboard, photo = page_detail(uid, pid)
        await send_or_edit(query, note + "\n\n" + text, keyboard, photo=photo)
        return

    # ---------- 充值 ----------
    if data == "rc:custom":
        context.user_data["state"] = "rc_custom"
        await send_or_edit(query, "✏️ 请输入充值金额（USDT，数字）：", kb([[("❌ 取消", "m:recharge")]]))
        return

    if data.startswith("rc:"):
        try:
            amount = float(data[3:])
        except ValueError:
            await send_or_edit(query, "❌ 金额无效", kb([[("⬅️ 返回", "m:recharge")]]))
            return
        err = service.validate_recharge_amount(amount)
        if err:
            await send_or_edit(query, err, kb([[("⬅️ 返回", "m:recharge")]]))
            return
        text, keyboard = page_recharge_confirm(amount)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("rcpay:"):
        # 新格式 rcpay:{渠道}:{金额}；兼容旧格式 rcpay:{金额}（默认 okpay）
        payload_rc = data[6:]
        if ":" in payload_rc:
            gateway, amount_part = payload_rc.split(":", 1)
        else:
            gateway, amount_part = "okpay", payload_rc
        if gateway not in PAY_GATEWAYS:
            gateway = "okpay"
        try:
            amount = float(amount_part)
        except ValueError:
            await send_or_edit(query, "❌ 金额无效", kb([[("⬅️ 返回", "m:recharge")]]))
            return
        # 限额安全兜底：绕过确认页直接构造回调也过不去
        err = service.validate_recharge_amount(amount)
        if err:
            await send_or_edit(query, err, kb([[("⬅️ 返回", "m:recharge")]]))
            return
        # 冷钱包渠道：链上直转，不走支付平台
        if gateway in CHAIN_GATEWAYS:
            recharge = service.create_chain_recharge(uid, amount, gateway)
            if not recharge:
                await send_or_edit(
                    query,
                    "❌ 该渠道暂不可用或当前排队人数较多，请换其它支付方式",
                    kb([[("⬅️ 返回", "m:recharge")]]),
                )
                return
            text, keyboard = page_chain_pay(recharge)
            await send_or_edit(query, text, keyboard)
            return
        gateway_name = GATEWAY_NAMES.get(gateway, "OkPay")
        recharge, pay_url = await service.create_recharge_with_paylink(uid, amount, gateway)
        if recharge and pay_url:
            text = (
                f"<b>💰 充值订单已创建</b>\n\n"
                f"订单号：<code>{recharge.id}</code>\n"
                f"支付方式：<code>{gateway_name}</code>\n"
                f"金额：<code>{amount:g}</code> USDT\n"
                f"获得积分：<code>{recharge.points}</code>\n\n"
                f"⏰ 30 分钟内支付有效，支付后积分自动到账"
            )
            await send_or_edit(query, text, kb([
                [("💰 立即支付", pay_url)],
                [("🔄 查询支付状态", f"rcchk:{recharge.id}")],
                [("🏠 主菜单", "m:root")],
            ]))
        else:
            await send_or_edit(
                query,
                "❌ 创建支付链接失败，请稍后重试或联系管理员",
                kb([[("⬅️ 返回", "m:recharge")]]),
            )
        return

    if data.startswith("rcchk:"):
        rid = data[6:]
        recharge = db.get_recharge(rid)
        if recharge and recharge.status == "completed":
            text = "✅ 已到账"
        elif recharge and recharge.chain:
            # 冷钱包单：主动跑一轮链上匹配再看状态
            await service.check_chain_recharges()
            recharge = db.get_recharge(rid)
            if recharge and recharge.status == "completed":
                await service.notify_user_recharge_success(recharge)
                await service.notify_admins_recharge_paid(recharge)
                text = "✅ 支付成功，积分已到账"
            else:
                text = "⏳ 暂未检测到链上到账，请确认金额/备注无误后再查询（链上确认约 1~3 分钟）"
        else:
            # allow_failed=True：超时被标 failed 的单也要能补查，
            # 否则停机期间已付款的订单永久丢失
            paid = await service.check_recharge_status(rid, allow_failed=True)
            if paid:
                recharge = db.get_recharge(rid)
                if recharge:
                    await service.notify_user_recharge_success(recharge)
                    await service.notify_admins_recharge_paid(recharge)
                text = "✅ 支付成功，积分已到账"
            else:
                text = "⏳ 暂未检测到支付，请支付后再查询（支付后也会自动到账）"
        await send_or_edit(query, text, kb([[("🏠 主菜单", "m:root")]]))
        return

    # ==================================================================
    # 付费供需（用户端）：大厅 / 我的 / 发布向导
    # sup: 系列已加入顶部状态清理白名单，各分支自行管理 state/payload。
    # ==================================================================
    if data == "sup:hall" or data.startswith("sup:hall:"):
        clear_state(context)
        page, category = 0, ""
        if data.startswith("sup:hall:"):
            parts = data.split(":")
            if len(parts) > 2 and parts[2].isdigit():
                page = int(parts[2])
            cats = db.get_settings().get("supply_categories") or []
            if len(parts) > 3 and parts[3].isdigit() and int(parts[3]) < len(cats):
                category = cats[int(parts[3])]
        text, keyboard = page_supply_hall(uid, page, category)
        await send_or_edit(query, text, keyboard)
        return

    if data == "sup:mine":
        clear_state(context)
        text, keyboard = page_supply_mine(uid)
        await send_or_edit(query, text, keyboard)
        return

    if data == "sup:new":
        # 发布向导第 1 步：选渠道。清空旧草稿重新开始。
        clear_state(context)
        text, keyboard = page_supply_new(uid)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("sup:ch:"):
        channel = data[7:]
        if channel not in SUPPLY_CHANNELS or channel not in supply.available_channels():
            await send_or_edit(query, "❌ 渠道不可用，请重新选择", kb([[("⬅️ 返回", "sup:new")]]))
            return
        context.user_data["payload"] = {
            "channel": channel, "category": "",
            "content": "", "photo_file_id": "", "anonymous": False,
        }
        if db.get_settings().get("supply_categories"):
            # 配了分类 → 第 2 步选分类（按钮流程，无输入状态）
            text, keyboard = page_supply_category(context.user_data["payload"])
            await send_or_edit(query, text, keyboard)
            return
        # 没配分类 → 直接进内容输入
        context.user_data["state"] = "sup_text"
        await send_or_edit(
            query,
            supply_content_prompt(context.user_data["payload"]),
            kb([[("❌ 取消", "sup:cancel")]]),
        )
        return

    if data.startswith("sup:cat:"):
        payload = context.user_data.get("payload")
        if not payload:
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "sup:new")]]))
            return
        cats = db.get_settings().get("supply_categories") or []
        idx = data[8:]
        payload["category"] = cats[int(idx)] if idx.isdigit() and int(idx) < len(cats) else ""
        context.user_data["state"] = "sup_text"
        await send_or_edit(query, supply_content_prompt(payload), kb([[("❌ 取消", "sup:cancel")]]))
        return

    if data == "sup:nophoto":
        # 跳过附图 → 直接预览
        payload = context.user_data.get("payload")
        if context.user_data.get("state") != "sup_photo" or not payload:
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "sup:new")]]))
            return
        payload["photo_file_id"] = ""
        context.user_data["state"] = "sup_preview"
        user = db.get_user(uid)
        text, keyboard = page_supply_preview(payload, user)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("sup:id:"):
        # 预览页切换 实名/匿名（「允许匿名」开关关闭时此按钮不会出现，双保险再拦一次）
        if not supply.anonymous_allowed():
            await send_or_edit(query, "❌ 当前不允许匿名发布", kb([[("⬅️ 返回", "sup:new")]]))
            return
        payload = context.user_data.get("payload")
        if context.user_data.get("state") != "sup_preview" or not payload:
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "sup:new")]]))
            return
        payload["anonymous"] = data[7:] == "1"
        user = db.get_user(uid)
        text, keyboard = page_supply_preview(payload, user)
        await send_or_edit(query, text, keyboard)
        return

    if data == "sup:go":
        # 确认发布：原子扣费落库 → 异步分发
        payload = context.user_data.get("payload")
        if context.user_data.get("state") != "sup_preview" or not payload or not payload.get("content"):
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "sup:new")]]))
            return
        clear_state(context)  # 先清会话，防止连点/重放导致重复扣费
        user = db.get_user(uid)
        post, reason = supply.create_post(
            user,
            channel=payload.get("channel", "bot"),
            category=payload.get("category", ""),
            content=payload.get("content", ""),
            photo_file_id=payload.get("photo_file_id", ""),
            anonymous=payload.get("anonymous", False),
            content_html=payload.get("content_html", ""),
        )
        if not post:
            text = f"❌ 发布失败：{CREATE_FAIL_REASONS.get(reason or '', '未知原因')}"
            buttons = []
            if reason == "points":
                buttons.append([("💰 去充值", "m:recharge")])
            buttons.append([("✍️ 重新发布", "sup:new"), ("⬅️ 供需大厅", "sup:hall")])
            await send_or_edit(query, text, kb(buttons))
            return
        if post.status == "pending_review":
            # 人工审核模式：先扣费待审，通知管理员，通过后才分发
            await supply.notify_admins_review(post)
            await send_or_edit(
                query,
                f"🕐 <b>已提交审核</b>\n\n"
                f"帖子编号：<code>{post.id}</code>\n"
                f"实付：<code>{post.cost}</code> 积分（余额 <code>{user.points}</code>）\n\n"
                f"管理员审核通过后立即发布；若未通过，<code>{post.cost}</code> 积分将自动全额退回",
                kb([
                    [("📜 我的供需", "sup:mine"), ("📢 供需大厅", "sup:hall")],
                    [("🏠 主菜单", "m:root")],
                ]),
            )
            return
        asyncio.create_task(supply.distribute(post))
        text = (
            f"✅ <b>发布成功</b>\n\n"
            f"帖子编号：<code>{post.id}</code>\n"
            f"渠道：{SUPPLY_CHANNELS[post.channel]}\n"
            f"实付：<code>{post.cost}</code> 积分（余额 <code>{user.points}</code>）\n\n"
        )
        if post.channel in ("bot", "both"):
            text += "🤖 机器人广播正在后台推送中\n"
        if post.channel in ("channel", "both"):
            text += "📣 频道发送结果将以私信告知（失败自动退频道部分积分）\n"
        text += "\n帖子已上架供需大厅"
        await send_or_edit(query, text, kb([
            [("📢 供需大厅", "sup:hall"), ("📜 我的供需", "sup:mine")],
            [("🏠 主菜单", "m:root")],
        ]))
        return

    if data == "sup:cancel":
        clear_state(context)
        text, keyboard = page_supply_hall(uid)
        await send_or_edit(query, "已取消发布\n\n" + text, keyboard)
        return

    # ---------- 上架向导：类型选择 ----------
    if data.startswith("padd_kind:"):
        if context.user_data.get("state") != "padd_kind":
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "a:prod")]]))
            return
        kind = data.split(":", 1)[1]
        if kind not in ("product", "report"):
            return
        context.user_data["payload"]["kind"] = kind
        if kind == "report":
            # 报单类：人工处理，没有发货内容概念，直接问库存
            context.user_data["state"] = "padd_stock"
            await send_or_edit(
                query,
                "➕ <b>上架商品</b>\n\n第 5 步：请输入库存数量\n\n💡 -1 表示无限库存；库存扣到 0 会自动标记售罄，退款会自动回补库存",
                kb([[("❌ 取消", "a:prod")]]),
            )
            return
        # 商品类：多问一步发货模式（固定内容 / 库存池）
        context.user_data["state"] = "padd_mode"
        await send_or_edit(
            query,
            "➕ <b>上架商品</b>\n\n第 5 步：请选择发货模式\n\n"
            "🔁 <b>固定内容</b>：所有买家发同一份内容（卡密、资料、链接），按库存份数售卖\n"
            "📦 <b>库存池</b>：一件一卖，每件内容独一无二（文字/图片/文件/图册），先到先得、售罄可订阅到货提醒",
            kb([
                [("🔁 固定内容", "padd_mode:fixed"), ("📦 库存池", "padd_mode:pool")],
                [("❌ 取消", "a:prod")],
            ]),
        )
        return

    if data.startswith("padd_mode:"):
        if context.user_data.get("state") != "padd_mode":
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "a:prod")]]))
            return
        mode = data.split(":", 1)[1]
        if mode not in ("fixed", "pool"):
            return
        context.user_data["payload"]["delivery_mode"] = mode
        if mode == "pool":
            # 池模式没有固定库存/固定发货内容，直接问限购
            context.user_data["state"] = "padd_limit"
            await send_or_edit(
                query,
                "➕ <b>上架商品</b>（库存池模式）\n\n第 6 步：请输入每人限购数量（0 表示不限）\n\n"
                "💡 上架后到「库存池」补货，补多少卖多少；限购按用户累计有效订单数计算",
                kb([[("❌ 取消", "a:prod")]]),
            )
            return
        context.user_data["state"] = "padd_stock"
        await send_or_edit(
            query,
            "➕ <b>上架商品</b>（固定内容模式）\n\n第 6 步：请输入库存数量\n\n💡 -1 表示无限库存；库存扣到 0 会自动标记售罄，退款会自动回补库存",
            kb([[("❌ 取消", "a:prod")]]),
        )
        return

    # ---------- 管理端（a: 前缀需管理员权限） ----------
    if data.startswith("a:") and not db.is_admin(uid):
        await send_or_edit(query, "❌ 无权限", kb([[("🏠 主菜单", "m:root")]]))
        return

    # ----- 商品管理 -----
    if data == "a:prod":
        text, keyboard = page_admin_prod()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:padd":
        context.user_data["state"] = "padd_name"
        context.user_data["payload"] = {}
        await send_or_edit(
            query,
            "➕ <b>上架商品</b>\n\n第 1 步：请输入名称\n\n💡 依次设置名称、介绍、价格、类型、发货模式、库存/限购（固定模式商品多一步发货内容）",
            kb([[("❌ 取消", "a:prod")]]),
        )
        return

    if data.startswith("a:pedit:"):
        text, keyboard = page_admin_pedit(data[8:])
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:pf:"):
        parts = data.split(":", 3)
        pid, field_name = parts[2], parts[3]
        p = db.get_product(pid)
        if p and field_name == "category":
            cats = db.get_settings().get("product_categories") or []
            if not cats:
                await send_or_edit(
                    query,
                    "❌ 还没有配置商品分类，请先到「商品管理 → 商品分类」添加",
                    kb([[("📂 商品分类", "a:pcat")], [("⬅️ 返回", f"a:pedit:{pid}")]]),
                )
                return
            buttons = [[("🚫 未分类", f"a:pcatsel:{pid}:")]]
            for i, c in enumerate(cats[:11]):
                buttons.append([(f"📂 {c}", f"a:pcatsel:{pid}:{i}")])
            buttons.append([("❌ 取消", f"a:pedit:{pid}")])
            await send_or_edit(query, "✏️ 请选择商品分类：", kb(buttons))
            return
        pname = {
            "name": "名称",
            "description": "介绍（发图可同时设置商品图）",
            "points": "价格（积分，正整数）",
            "stock": "库存（-1 为无限）",
            "limit_per_user": "每人限购（0 为不限）",
            "auto_delivery": "自动发货内容",
        }.get(field_name, field_name)
        context.user_data["state"] = f"pedit:{pid}:{field_name}"
        await send_or_edit(query, f"✏️ 请输入新的{pname}：", kb([[("❌ 取消", f"a:pedit:{pid}")]]))
        return

    if data.startswith("a:pcatsel:"):
        # 分类选择是按钮流程，不需要输入状态
        parts = data.split(":", 3)
        pid, idx = parts[2], parts[3] if len(parts) > 3 else ""
        p = db.get_product(pid)
        if p:
            cats = db.get_settings().get("product_categories") or []
            p.category = cats[int(idx)] if idx.isdigit() and int(idx) < len(cats) else ""
            db.save_product(p)
            audit(uid, "改分类", f"{p.name} → {p.category or '未分类'}")
        text, keyboard = page_admin_pedit(pid)
        await send_or_edit(query, "✅ 分类已更新\n\n" + text, keyboard)
        return

    if data.startswith("a:pmode:"):
        pid = data[8:]
        p = db.get_product(pid)
        if p:
            cur = getattr(p, "delivery_mode", "fixed")
            if cur == "fixed":
                p.delivery_mode = "pool"
                p.stock = db.count_stock_items(pid, "available")
                if p.stock == 0 and p.status == "on":
                    p.status = "sold_out"  # 空池不可售
                db.save_product(p)
                audit(uid, "切换发货模式", f"{p.name} → 库存池")
            else:
                left = db.count_stock_items(pid, "available")
                if left:
                    await send_or_edit(
                        query,
                        f"❌ 池内还有 <code>{left}</code> 件现货，请先清空或售罄后再切换",
                        kb([[("📦 库存池", f"a:pool:{pid}")], [("⬅️ 返回", f"a:pedit:{pid}")]]),
                    )
                    return
                p.delivery_mode = "fixed"
                p.stock = 0
                p.status = "sold_out"
                db.save_product(p)
                audit(uid, "切换发货模式", f"{p.name} → 固定内容")
        text, keyboard = page_admin_pedit(pid)
        await send_or_edit(query, "✅ 发货模式已切换\n\n" + text, keyboard)
        return

    if data.startswith("a:ptoggle:"):
        pid = data[10:]
        p = db.get_product(pid)
        if p:
            cycle = ["on", "off", "sold_out", "hidden"]
            idx = cycle.index(p.status) if p.status in cycle else 0
            p.status = cycle[(idx + 1) % len(cycle)]
            db.save_product(p)
            audit(uid, "切换商品状态", f"{p.name} → {p.status}")
        text, keyboard = page_admin_pedit(pid)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:ptop:"):
        pid = data[7:]
        p = db.get_product(pid)
        if p:
            p.is_top = not p.is_top
            db.save_product(p)
            audit(uid, "置顶切换", f"{p.name} → {'置顶' if p.is_top else '取消置顶'}")
        text, keyboard = page_admin_pedit(pid)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:ptag:"):
        parts = data.split(":", 3)
        pid, tag = parts[2], parts[3]
        p = db.get_product(pid)
        if p:
            tags = list(p.tags or [])
            if tag in tags:
                tags.remove(tag)
            else:
                tags.append(tag)
            p.tags = tags
            db.save_product(p)
        text, keyboard = page_admin_pedit(pid)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:pdelok:"):
        pid = data[9:]
        p = db.get_product(pid)
        service.delete_product(pid)
        audit(uid, "删除商品", p.name if p else pid)
        text, keyboard = page_admin_prod()
        await send_or_edit(query, "🗑 已删除\n\n" + text, keyboard)
        return

    if data.startswith("a:pdel:"):
        text, keyboard = page_admin_pdel_confirm(data[7:])
        await send_or_edit(query, text, keyboard)
        return

    # ----- 商品分类 -----
    if data == "a:pcat":
        text, keyboard = page_admin_pcats()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:pcatadd":
        context.user_data["state"] = "pcatadd"
        await send_or_edit(
            query,
            "➕ 请输入新分类名（不超过 12 字，最多 12 个分类）：",
            kb([[("❌ 取消", "a:pcat")]]),
        )
        return

    if data.startswith("a:pcatdel:"):
        s = db.get_settings()
        cats = list(s.get("product_categories") or [])
        idx = data[10:]
        if idx.isdigit() and int(idx) < len(cats):
            removed = cats.pop(int(idx))
            db.update_settings(product_categories=cats)
            audit(uid, "删除商品分类", removed)
            text, keyboard = page_admin_pcats()
            await send_or_edit(query, f"✅ 已删除分类「{h(removed)}」\n\n" + text, keyboard)
        else:
            text, keyboard = page_admin_pcats()
            await send_or_edit(query, text, keyboard)
        return

    # ----- 订单管理 -----
    if data == "a:order":
        text, keyboard = page_admin_order("all")
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:of:"):
        text, keyboard = page_admin_order(data[5:])
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:osearch":
        context.user_data["state"] = "osearch"
        await send_or_edit(
            query,
            "🔍 请输入订单号 / 用户名 / 商品名关键词：",
            kb([[("❌ 取消", "a:order")]]),
        )
        return

    if data.startswith("a:od:"):
        text, keyboard = page_admin_order_detail(data[5:])
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:oc:"):
        oid = data[5:]
        o = db.get_order(oid)
        if not o or o.status not in ("pending", "processing"):
            await send_or_edit(query, "❌ 订单不存在或已处理完毕", kb([[("⬅️ 返回", "a:order")]]))
            return
        context.user_data["state"] = f"oc:{oid}"
        await send_or_edit(
            query,
            f"✅ 完成订单 <code>{oid}</code>\n\n"
            f"请发送回单内容（支持文字/图片/文件等任意类型，将原样发给用户）：",
            kb([[("❌ 取消", f"a:od:{oid}")]]),
        )
        return

    if data.startswith("a:op:"):
        oid = data[5:]
        o = db.get_order(oid)
        if o and o.status == "pending":
            o.status = "processing"
            db.save_order(o)
            audit(uid, "标记处理中", oid)
        text, keyboard = page_admin_order_detail(oid)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:orok:"):
        oid = data[7:]
        order = service.refund_order(oid, uid)
        if order:
            await service.notify_user_order_refunded(order)
            audit(uid, "退款", f"订单 {oid} · {order.points} 积分")
            text = f"💰 订单 <code>{oid}</code> 已退款并关闭"
        else:
            text = "❌ 退款失败（订单不存在或已退款）"
        await send_or_edit(query, text, kb([[("⬅️ 返回", "a:order")]]))
        return

    if data.startswith("a:or:"):
        text, keyboard = page_admin_refund_confirm(data[5:])
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:ou:"):
        text, keyboard = page_admin_user_detail(int(data[5:]))
        await send_or_edit(query, text, keyboard)
        return

    # ----- 用户管理 -----
    if data == "a:user":
        text, keyboard = page_admin_user()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:usearch":
        context.user_data["state"] = "usearch"
        await send_or_edit(
            query,
            "🔍 请输入要搜索的用户 ID / 用户名关键词：",
            kb([[("❌ 取消", "a:user")]]),
        )
        return

    if data.startswith("a:ud:"):
        text, keyboard = page_admin_user_detail(int(data[5:]))
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:uadd:"):
        target = int(data[7:])
        context.user_data["state"] = f"uadd:{target}"
        await send_or_edit(query, "➕ 请输入要增加的积分数量：", kb([[("❌ 取消", f"a:ud:{target}")]]))
        return

    if data.startswith("a:usub:"):
        target = int(data[7:])
        context.user_data["state"] = f"usub:{target}"
        await send_or_edit(query, "➖ 请输入要扣除的积分数量：", kb([[("❌ 取消", f"a:ud:{target}")]]))
        return

    if data.startswith("a:uban:"):
        target = int(data[7:])
        u = db.get_user(target)
        if u:
            u.is_banned = not u.is_banned
            db.save_user(u)
            audit(uid, "封禁" if u.is_banned else "解封", f"@{u.username or target}")
        text, keyboard = page_admin_user_detail(target)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:uo:"):
        text, keyboard = page_admin_user_orders(int(data[5:]))
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:uph:"):
        text, keyboard = page_admin_user_points(int(data[6:]))
        await send_or_edit(query, text, keyboard)
        return

    # ----- 管理员管理 -----
    if data == "a:adm":
        text, keyboard = page_admin_admins()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:admadd":
        context.user_data["state"] = "admadd"
        await send_or_edit(query, "➕ 请输入新管理员的 Telegram ID：", kb([[("❌ 取消", "a:adm")]]))
        return

    if data.startswith("a:admdel:"):
        from .runtime import ADMIN_ID
        target = int(data[9:])
        if target != ADMIN_ID:
            db.delete_admin(target)
            audit(uid, "移除管理员", str(target))
        text, keyboard = page_admin_admins()
        await send_or_edit(query, text, keyboard)
        return

    # ----- 频道管理 -----
    if data == "a:ch":
        text, keyboard = page_admin_channels()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:chadd":
        context.user_data["state"] = "chadd"
        await send_or_edit(
            query,
            "➕ 请输入频道用户名（如 @channelname）或 chat_id（如 -1001234567890）：\n\n"
            "⚠️ 请先把机器人设为该频道管理员，否则无法检测用户关注状态",
            kb([[("❌ 取消", "a:ch")]]),
        )
        return

    if data.startswith("a:chdel:"):
        service.remove_channel(data[8:])
        audit(uid, "移除频道", data[8:])
        text, keyboard = page_admin_channels()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:chforce":
        s = db.get_settings()
        db.update_settings(force_channels=not s.get("force_channels", False))
        audit(uid, "切换强制关注", "开" if not s.get("force_channels", False) else "关")
        text, keyboard = page_admin_channels()
        await send_or_edit(query, text, keyboard)
        return

    # ----- 系统设置 -----
    if data == "a:set":
        text, keyboard = page_admin_settings()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:sr":
        context.user_data["state"] = "sr"
        await send_or_edit(query, "💰 请输入积分比例（1 USDT 兑换多少积分）：", kb([[("❌ 取消", "a:set")]]))
        return

    if data == "a:smin":
        context.user_data["state"] = "smin"
        await send_or_edit(query, "💵 请输入最低充值金额（USDT，数字）：", kb([[("❌ 取消", "a:set")]]))
        return

    if data == "a:sw":
        context.user_data["state"] = "sw"
        await send_or_edit(
            query,
            "📝 发送文字设置欢迎语；发送图片（可带文字）同时设置欢迎图：",
            kb([[("❌ 取消", "a:set")]]),
        )
        return

    if data == "a:sm":
        s = db.get_settings()
        db.update_settings(maintenance_mode=not s.get("maintenance_mode", False))
        audit(uid, "切换维护模式", "开" if not s.get("maintenance_mode", False) else "关")
        text, keyboard = page_admin_settings()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:sf":
        s = db.get_settings()
        db.update_settings(force_channels=not s.get("force_channels", False))
        audit(uid, "切换强制关注", "开" if not s.get("force_channels", False) else "关")
        text, keyboard = page_admin_settings()
        await send_or_edit(query, text, keyboard)
        return

    # ----- 支付设置（v2.5.0）：渠道开关 / 限额 / 冷钱包地址与 Key -----
    if data == "a:pay":
        text, keyboard = page_admin_payment()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:pay:limit":
        s = db.get_settings()
        context.user_data["state"] = "pay_limit"
        await send_or_edit(
            query,
            f"💵 当前限额：<code>{s.get('min_recharge_usdt', 1):g} ~ {s.get('max_recharge_usdt', 1000):g}</code> USDT\n\n"
            "请输入新限额，格式：<code>最低 最高</code>（如 <code>1 500</code>；最高填 0 表示不限）：",
            kb([[("❌ 取消", "a:pay")]]),
        )
        return

    if data.startswith("a:pay:tg:"):
        gw = data[9:]
        key_map = {
            "okpay": "pay_okpay_enabled",
            "kkpay": "pay_kkpay_enabled",
            "tron": "pay_tron_enabled",
            "ton": "pay_ton_enabled",
        }
        key = key_map.get(gw)
        if not key:
            return
        s = db.get_settings()
        cur = bool(s.get(key, gw in ("okpay", "kkpay")))
        if not cur:  # 开启前校验：冷钱包必须先配地址
            if gw == "tron" and not (s.get("tron_wallet_address") or ""):
                await send_or_edit(query, "⚠️ 请先设置 TRON 收款地址再开启", kb([
                    [("🏦 设置 TRON 收款地址", "a:pay:addr:tron")],
                    [("⬅️ 返回支付设置", "a:pay")],
                ]))
                return
            if gw == "ton" and not (s.get("ton_wallet_address") or ""):
                await send_or_edit(query, "⚠️ 请先设置 TON 收款地址再开启", kb([
                    [("🏦 设置 TON 收款地址", "a:pay:addr:ton")],
                    [("⬅️ 返回支付设置", "a:pay")],
                ]))
                return
        db.update_settings(**{key: not cur})
        audit(uid, "切换支付渠道", f"{GATEWAY_NAMES.get(gw, gw)} {'开' if not cur else '关'}")
        text, keyboard = page_admin_payment()
        await send_or_edit(query, text, keyboard)
        return

    if data in ("a:pay:addr:tron", "a:pay:addr:ton"):
        chain = "trc20" if data.endswith(":tron") else "ton"
        context.user_data["state"] = f"pay_addr:{chain}"
        tip = ("🏦 请发送 TRON 收款地址（T 开头 34 位）：" if chain == "trc20"
               else "🏦 请发送 TON 收款地址（EQ/UQ 开头 48 位）：")
        await send_or_edit(query, tip, kb([[("❌ 取消", "a:pay")]]))
        return

    if data in ("a:pay:key:tron", "a:pay:key:ton"):
        chain = "tron" if data.endswith(":tron") else "ton"
        context.user_data["state"] = f"pay_key:{chain}"
        name = "TronGrid" if chain == "tron" else "TonCenter"
        await send_or_edit(
            query,
            f"🔑 请发送 {name} API Key（可加快链上查询；发送 <code>0</code> 清除已设置的 Key）：",
            kb([[("❌ 取消", "a:pay")]]),
        )
        return

    # ----- 供需管理 -----
    if data == "a:sup":
        text, keyboard = page_admin_supply()
        await send_or_edit(query, text, keyboard)
        return

    # ---------- 供需开关面板：每个开关独立翻转，立即生效 ----------
    if data == "a:sup:sw":
        text, keyboard = page_admin_supply_sw()
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:sup:sw:t:"):
        key = data[len("a:sup:sw:t:"):]
        valid_keys = {k for k, _, _ in SUPPLY_SWITCHES}
        if key in valid_keys:
            cur = bool(db.get_settings().get(key, SUPPLY_SWITCH_ON[key]))
            db.update_settings(**{key: not cur})
            audit(uid, "供需开关", f"{key} → {'开' if not cur else '关'}")
        text, keyboard = page_admin_supply_sw()
        await send_or_edit(query, text, keyboard)
        return

    # ---------- 供需审核队列与审核动作 ----------
    if data == "a:sup:rv":
        text, keyboard = page_admin_supply_review()
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:sup:ok:"):
        pid = data[len("a:sup:ok:"):]
        post = supply.approve_post(pid, operator_id=uid)
        if not post:
            await send_or_edit(query, "❌ 帖子不存在或已处理", kb([[("🕐 审核队列", "a:sup:rv")]]))
            return
        audit(uid, "供需审核通过", pid)
        asyncio.create_task(supply.distribute(post))
        asyncio.create_task(supply.notify_user_approved(post))
        text, keyboard = page_admin_supply_post(pid)
        await send_or_edit(query, f"✅ 帖子 <code>{pid}</code> 已通过审核，正在分发\n\n" + text, keyboard)
        return

    if data.startswith("a:sup:no:"):
        pid = data[len("a:sup:no:"):]
        post = supply.reject_post(pid, operator_id=uid)
        if not post:
            await send_or_edit(query, "❌ 帖子不存在或已处理", kb([[("🕐 审核队列", "a:sup:rv")]]))
            return
        audit(uid, "供需审核拒绝", pid)
        asyncio.create_task(supply.notify_user_rejected(post))
        text, keyboard = page_admin_supply_post(pid)
        await send_or_edit(
            query,
            f"❌ 帖子 <code>{pid}</code> 已拒绝，<code>{post.cost}</code> 积分已退还发布人\n\n" + text,
            keyboard,
        )
        return

    if data == "a:sup:posts" or data.startswith("a:sup:posts:"):
        page = 0
        if data.startswith("a:sup:posts:") and data[12:].isdigit():
            page = int(data[12:])
        text, keyboard = page_admin_supply_posts(page)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:sup:pd:"):
        text, keyboard = page_admin_supply_post(data[9:])
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:sup:pdel:"):
        text, keyboard = page_admin_supply_pdel(data[11:], refund=False)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:sup:pdelr:"):
        text, keyboard = page_admin_supply_pdel(data[12:], refund=True)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:sup:pdok:"):
        parts = data.split(":")
        pid, refund = parts[3], len(parts) > 4 and parts[4] == "1"
        post = supply.delete_post(pid, operator_id=uid, refund=refund)
        if not post:
            await send_or_edit(query, "❌ 帖子不存在或已删除", kb([[("⬅️ 帖子管理", "a:sup:posts")]]))
            return
        audit(uid, "删除供需帖", f"{pid}{'（并退款）' if refund else ''}")
        text = f"✅ 帖子 <code>{pid}</code> 已删除"
        if refund:
            text += f"，已退 <code>{post.cost}</code> 积分给发布人"
        await send_or_edit(query, text, kb([
            [("⬅️ 帖子管理", "a:sup:posts"), ("📢 供需管理", "a:sup")],
        ]))
        return

    if data == "a:sup:cats":
        text, keyboard = page_admin_supply_cats()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:sup:catadd":
        context.user_data["state"] = "sup_cat_add"
        await send_or_edit(
            query,
            "➕ 请输入新分类名（不超过 12 字，最多 8 个分类）：",
            kb([[("❌ 取消", "a:sup:cats")]]),
        )
        return

    if data.startswith("a:sup:catdel:"):
        s = db.get_settings()
        cats = list(s.get("supply_categories") or [])
        idx = data[13:]
        if idx.isdigit() and int(idx) < len(cats):
            removed = cats.pop(int(idx))
            db.update_settings(supply_categories=cats)
            audit(uid, "删除供需分类", removed)
            text, keyboard = page_admin_supply_cats()
            await send_or_edit(query, f"✅ 已删除分类「{h(removed)}」\n\n" + text, keyboard)
        else:
            text, keyboard = page_admin_supply_cats()
            await send_or_edit(query, text, keyboard)
        return

    if data == "a:sup:price":
        text, keyboard = page_admin_supply_price()
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:sup:set:"):
        key = data[10:]
        prompts = {
            "supply_price_bot": "🤖 请输入机器人供需价格（积分，≥1）：",
            "supply_price_channel": "📣 请输入频道供需价格（积分，≥1）：",
            "supply_discount": "🚀 请输入双发优惠积分（≥0，双发价 = 两价之和 - 优惠，最低 1）：",
            "supply_daily_limit": "🔢 请输入每人每日发布上限（0 = 不限）：",
            "supply_max_len": "📏 请输入内容字数上限（如 600）：",
        }
        if key not in prompts:
            return
        context.user_data["state"] = f"sup_set:{key}"
        await send_or_edit(query, prompts[key], kb([[("❌ 取消", "a:sup:price")]]))
        return

    if data == "a:sup:chid":
        context.user_data["state"] = "sup_chid"
        await send_or_edit(
            query,
            "📣 请输入供需频道的 @用户名 或 chat_id（如 -1001234567890）\n\n"
            "发送 <code>关闭</code> 可清空配置（前台将隐藏频道/双发选项）\n\n"
            "⚠️ 请先把机器人拉进该频道并设为管理员，否则频道供需会发送失败",
            kb([[("❌ 取消", "a:sup")]]),
        )
        return

    # ----- 库存池管理 -----
    if data == "a:pool":
        text, keyboard = page_admin_pool()
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:poolpg:"):
        parts = data.split(":")
        pid, page = parts[2], int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        text, keyboard = page_admin_pool_prod(pid, page)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:pool:"):
        text, keyboard = page_admin_pool_prod(data[7:])
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:stkitem:"):
        text, keyboard = page_admin_pool_item(int(data[10:]))
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:stkdel:"):
        item_id = int(data[9:])
        ok, info = stock.remove_item(item_id, uid)
        if ok:
            audit(uid, "删除库存", f"#{item_id}")
            text, keyboard = page_admin_pool_prod(info)
            await send_or_edit(query, f"🗑 已删除 #{item_id}\n\n" + text, keyboard)
        else:
            await send_or_edit(query, f"❌ {info}", kb([[("⬅️ 返回", "a:pool")]]))
        return

    if data.startswith("a:stkclearok:"):
        pid = data[13:]
        n = stock.clear_sold(pid)
        audit(uid, "清空已售记录", f"{pid} · {n} 条")
        text, keyboard = page_admin_pool_prod(pid)
        await send_or_edit(query, f"🧹 已清空 <code>{n}</code> 条已售记录\n\n" + text, keyboard)
        return

    if data.startswith("a:stkclear:"):
        text, keyboard = page_admin_pool_clear_confirm(data[11:])
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:stkadd:"):
        pid = data[9:]
        p = db.get_product(pid)
        if not p or getattr(p, "delivery_mode", "fixed") != "pool":
            await send_or_edit(query, "❌ 该商品不是库存池模式", kb([[("⬅️ 返回", "a:prod")]]))
            return
        context.user_data["state"] = f"restock:{pid}"
        context.user_data["payload"] = {"added": 0, "album": None}
        await send_or_edit(query, restock_prompt(pid), restock_keyboard(pid))
        return

    if data.startswith("a:stkdone:"):
        # 完成补货：先把还在缓冲的图册入库，再结算与触发到货提醒
        pid = data[10:]
        payload = context.user_data.get("payload") or {}
        stock.flush_album_buffer(payload, pid, uid)
        added = int(payload.get("added", 0) or 0)
        clear_state(context)
        if added:
            p = db.get_product(pid)
            audit(uid, "补货", f"{(p.name if p else pid)} +{added} 件")
            if p and p.status == "on":
                # 补货后商品在售 → 通知所有到货提醒订阅者
                asyncio.create_task(stock.notify_restock(pid))
        text, keyboard = page_admin_pool_prod(pid)
        if added:
            head = f"✅ 补货完成，本次入库 <code>{added}</code> 件；如有到货提醒订阅将自动私信推送"
        else:
            head = "已退出补货（本次未入库）"
        await send_or_edit(query, head + "\n\n" + text, keyboard)
        return

    # ----- 财务 / 日志 / 备份 / 广播 -----
    if data == "a:fin":
        text, keyboard = page_admin_finance()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:logs" or data.startswith("a:logs:"):
        page = int(data[7:]) if data.startswith("a:logs:") and data[7:].isdigit() else 0
        text, keyboard = page_admin_logs(page)
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:backup":
        try:
            await query.message.reply_document(
                document=open(db.path, "rb"),
                filename=f"shop_backup_{now_iso()[:10]}.db",
                caption="💾 数据库备份",
            )
            audit(uid, "手动备份", "")
        except Exception as exc:
            await send_or_edit(query, f"❌ 备份失败：{h(exc)}", kb([[("⬅️ 返回", "m:admin")]]))
        return

    if data == "a:bc":
        text, keyboard = page_admin_broadcast()
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:bct:"):
        target = data[6:]
        context.user_data["state"] = f"bc:{target}"
        tname = {"all": "全员", "with_points": "有积分用户", "no_points": "无积分用户"}.get(target, target)
        await send_or_edit(
            query,
            f"📣 广播目标：<b>{tname}</b>\n\n请发送要广播的内容（文字/图片/视频/文件均可）：",
            kb([[("❌ 取消", "a:bc")]]),
        )
        return

    if data == "a:bcsend":
        payload = context.user_data.get("payload") or {}
        target = payload.get("target", "all")
        src = payload.get("src")
        clear_state(context)
        if not src:
            await send_or_edit(query, "❌ 会话已过期，请重新发起广播", kb([[("⬅️ 返回", "a:bc")]]))
            return
        await send_or_edit(query, "📣 开始广播，请稍候…", kb([[("🏠 主菜单", "m:root")]]))
        result = await service.broadcast_message("", target=target, copy_from=src)
        audit(uid, "广播", f"目标 {target} · 成功 {result['success']} · 失败 {result['failed']}")
        await query.message.reply_text(
            f"📣 <b>广播完成</b>\n\n✅ 成功：<code>{result['success']}</code>\n❌ 失败：<code>{result['failed']}</code>",
            parse_mode="HTML",
            reply_markup=kb([[("⬅️ 返回", "m:admin")]]),
        )
        return

    # ==================================================================
    # 抢单模块（v2.6.0）：gb:* 接单员端 / a:g* 管理端接单管理
    # ==================================================================

    async def _toast(text: str, alert: bool = True) -> None:
        """二次弹 toast（入口处已 answer 过一次，这里容错）。"""
        try:
            await query.answer(text=text[:190], show_alert=alert)
        except Exception:
            pass

    # ---------- 群内抢单按钮 ----------
    if data.startswith("gb:g:"):
        oid = data[5:]
        code, order, g = grab.try_grab(
            oid, uid, tg_user.username or "", tg_user.first_name or ""
        )
        if code == "ok":
            await _toast("🎉 抢单成功！请到机器人私聊查看需求并回单")
            await grab.on_grabbed(order, g)
        elif code == "taken":
            await _toast("😢 手慢了，这单刚被抢走")
        else:
            await _toast(f"⚠️ {code}")
        return

    # ---------- 接单员中心 ----------
    if data == "gb:m":
        text, keyboard = page_grab_home(uid)
        await send_or_edit(query, text, keyboard)
        return

    if data == "gb:doing" or data.startswith("gb:doing:"):
        page = int(data.split(":")[2]) if data.count(":") == 2 and data.split(":")[2].isdigit() else 0
        text, keyboard = page_grab_doing(uid, page)
        await send_or_edit(query, text, keyboard)
        return

    if data == "gb:done" or data.startswith("gb:done:"):
        page = int(data.split(":")[2]) if data.count(":") == 2 and data.split(":")[2].isdigit() else 0
        text, keyboard = page_grab_done(uid, page)
        await send_or_edit(query, text, keyboard)
        return

    if data == "gb:money" or data.startswith("gb:money:"):
        page = int(data.split(":")[2]) if data.count(":") == 2 and data.split(":")[2].isdigit() else 0
        text, keyboard = page_grab_money(uid, page)
        await send_or_edit(query, text, keyboard)
        return

    if data == "gb:wd":
        text, keyboard = page_grab_withdraw(uid)
        await send_or_edit(query, text, keyboard)
        return

    if data == "gb:wdnew":
        g = db.get_grabber(uid)
        if not g:
            await _toast("你还不是接单员")
            return
        context.user_data["state"] = "gbwd"
        s = db.get_settings()
        await send_or_edit(
            query,
            f"💌 <b>申请提现 · 打款到 OK 钱包</b>\n\n"
            f"可提现余额：<code>{g.balance_available}</code> 积分（≈ <b>{grab.points_to_usdt(g.balance_available):.2f}</b> U）\n"
            f"当前汇率：<code>{grab.points_per_usdt()}</code> 积分 = 1 U\n"
            f"最低提现：<code>{s.get('grab_withdraw_min', 100)}</code> 积分\n\n"
            f"请发送：<code>积分 OK钱包ID</code>\n"
            f"例：<code>500 12345678</code>（500 积分 ≈ {grab.points_to_usdt(500):.2f} U，打到 OK 钱包 12345678）",
            kb([[("❌ 取消", "gb:wd")]]),
        )
        return

    # 接单员领取/查看专属入群链接
    if data == "gb:inv":
        g = db.get_grabber(uid)
        if not g:
            await _toast("你还不是接单员")
            return
        if g.joined_group:
            await _toast("你已在接单群里，无需链接")
            return
        link, err = await grab.ensure_invite_link(g)
        if not link:
            await _toast(err or "生成失败，请稍后再试", show_alert=True)
            return
        await send_or_edit(
            query,
            "🔑 <b>你的专属入群链接</b>\n\n"
            "<blockquote>此链接只限你本人使用一次，进群即自动作废，切勿转发他人。</blockquote>",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("🔗 点我进入接单群", url=link),
            ], [InlineKeyboardButton("⬅️ 接单中心", callback_data="gb:m")]]),
        )
        return

    if data == "gb:rank":
        text, keyboard = page_grab_rank(uid)
        await send_or_edit(query, text, keyboard)
        return

    if data == "gb:aps":
        text, keyboard = page_grab_appeals(uid)
        await send_or_edit(query, text, keyboard)
        return

    # 回单按钮：校验这单确实是他抢的，进入回单输入状态
    if data.startswith("gb:r:"):
        oid = data[5:]
        order = db.get_order(oid)
        if not order or order.grab_status != "grabbed" or order.grabber_id != uid:
            await _toast("⚠️ 这单不在你手上或已超时释放")
            text, keyboard = page_grab_doing(uid)
            await send_or_edit(query, text, keyboard)
            return
        context.user_data["state"] = f"gbreply:{oid}"
        await send_or_edit(
            query,
            f"📫 <b>回单</b> <code>#{oid}</code>\n\n"
            f"请直接发送回单内容（文字/图片/文件均可，支持加粗等排版）：",
            kb([[("❌ 取消", "gb:doing")]]),
        )
        return

    # 放弃订单
    if data.startswith("gb:q:"):
        oid = data[5:]
        ok, msg = await grab.giveup(oid, uid)
        await _toast(("✅ " if ok else "⚠️ ") + msg)
        text, keyboard = page_grab_home(uid)
        await send_or_edit(query, text, keyboard)
        return

    # 发起申诉（针对某单）
    if data.startswith("gb:ap:"):
        oid = data[5:]
        if not db.get_grabber(uid):
            await _toast("你还不是接单员")
            return
        context.user_data["state"] = f"gbappeal:{oid}"
        await send_or_edit(
            query,
            f"🗣 <b>发起申诉</b>（关联订单 <code>#{oid}</code>）\n\n"
            "请发送申诉说明（500 字以内）：",
            kb([[("❌ 取消", "gb:aps")]]),
        )
        return

    # ---------- 管理端：接单管理大分类 ----------
    if data == "a:grab":
        text, keyboard = page_admin_grab()
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:gbers" or data.startswith("a:gbers:"):
        parts = data.split(":")
        st = parts[2] if len(parts) > 2 else ""
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        text, keyboard = page_admin_grabbers(st, page)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:gber:"):
        text, keyboard = page_admin_grabber_detail(int(data[7:]))
        await send_or_edit(query, text, keyboard)
        return

    if data == "a:gbadd":
        context.user_data["state"] = "gbadd"
        await send_or_edit(
            query,
            "➕ <b>添加接单员</b>\n\n"
            "请发送对方的 <b>TG 数字 ID</b>（如 <code>123456789</code>）；\n"
            "对方需先与机器人 /start 过一次。\n\n"
            "添加成功后机器人会自动私聊 Ta：欢迎卡 + 专属入群链接 + 接单须知。",
            kb([[("❌ 取消", "a:gbers")]]),
        )
        return

    # 重发/生成接单员专属入群链接（链接泄漏或进群失败时用）
    if data.startswith("a:gblink:"):
        g = db.get_grabber(int(data[9:]))
        if not g:
            await _toast("接单员不存在")
            return
        link, err = await grab.ensure_invite_link(g, force=True)
        if not link:
            await _toast(err or "生成失败", show_alert=True)
            return
        audit(uid, "重发接单员入群链接", g.display())
        try:
            await context.bot.send_message(
                g.user_id,
                "🔑 <b>你的新专属入群链接</b>\n\n"
                "<blockquote>此链接只限你本人使用一次，进群即自动作废，切勿转发他人。</blockquote>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔗 点我进入接单群", url=link),
                ]]),
            )
            await _toast("✅ 新链接已私聊发给接单员")
        except Exception:
            await _toast("⚠️ 链接已生成，但私聊发送失败（对方需先 /start）", show_alert=True)
        text, keyboard = page_admin_grabber_detail(g.user_id)
        await send_or_edit(query, text, keyboard)
        return

    # 自动识群确认：把接单群切换到新群
    if data.startswith("a:guse:"):
        cid = data[7:]
        grab.confirm_grab_chat(cid)
        audit(uid, "切换接单群", cid)
        await _toast(f"✅ 接单群已切换为 {cid}，派单总开关已打开")
        text, keyboard = page_admin_grab_settings()
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:gbst:"):
        parts = data.split(":")
        g = grab.set_grabber_status(int(parts[2]), parts[3], operator_id=uid)
        if g:
            audit(uid, "接单员状态变更", f"{g.display()} → {parts[3]}")
            # 状态变更私信告知本人
            notice = {
                "active": "🟢 你已恢复在岗，可以抢单了",
                "rest": "🟡 你已被设为请假，恢复在岗后才能抢单",
                "suspended": "🔴 你已被停权，请联系管理员",
                "blacklist": "⛔ 你已被拉入黑名单",
            }.get(parts[3])
            if notice:
                try:
                    await context.bot.send_message(g.user_id, notice)
                except Exception:
                    pass
        text, keyboard = page_admin_grabber_detail(int(parts[2]))
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:gbdelok:"):
        uid2 = int(data[10:])
        g = db.get_grabber(uid2)
        if db.delete_grabber(uid2):
            audit(uid, "删除接单员档案", g.display() if g else str(uid2))
            await _toast("✅ 已删除档案")
        text, keyboard = page_admin_grabbers()
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:gbdel:"):
        uid2 = int(data[8:])
        g = db.get_grabber(uid2)
        name = g.display() if g else str(uid2)
        await send_or_edit(
            query,
            f"⚠️ <b>确认删除接单员档案？</b>\n\n{name}\n"
            f"删除后其余额/业绩档案将清空（订单记录保留），且无法恢复。",
            kb([[("🗑 确认删除", f"a:gbdelok:{uid2}")], [("⬅️ 取消", f"a:gber:{uid2}")]]),
        )
        return

    # 手动解冻该接单员全部冻结佣金
    if data.startswith("a:gbrel:"):
        uid2 = int(data[8:])
        released = 0
        for cm in db.list_grab_commissions(grabber_id=uid2, status="frozen", limit=200):
            if grab.release_commission_manual(cm.id, operator_id=uid):
                released += 1
        if released:
            audit(uid, "手动解冻佣金", f"{uid2} · {released} 笔")
            try:
                await context.bot.send_message(
                    uid2, f"🔓 管理员已手动解冻你的 <b>{released}</b> 笔佣金", parse_mode="HTML"
                )
            except Exception:
                pass
        await _toast(f"🔓 已解冻 {released} 笔")
        text, keyboard = page_admin_grabber_detail(uid2)
        await send_or_edit(query, text, keyboard)
        return

    # 看某接单员的进行中订单
    if data.startswith("a:gbo:"):
        uid2 = int(data[6:])
        g = db.get_grabber(uid2)
        orders = db.list_grab_orders("grabbed", grabber_id=uid2, limit=10)
        lines = [f"<b>📋 {h(g.display() if g else str(uid2))} 的进行中订单</b>", ""]
        buttons = []
        if not orders:
            lines.append("暂无进行中订单")
        for o in orders:
            lines.append(f"<code>#{o.id}</code> {h(o.product_name)} · 时限 <code>{o.deadline[:16].replace('T', ' ')}</code>")
            buttons.append([(f"📄 详情 #{o.id}", f"a:od:{o.id}"), ("🔓 强制释放", f"a:gfree:{o.id}")])
        buttons.append([("⬅️ 接单员详情", f"a:gber:{uid2}")])
        await send_or_edit(query, "\n".join(lines), kb(buttons))
        return

    # ---------- 派单中心 ----------
    if data == "a:gords" or data.startswith("a:gords:"):
        parts = data.split(":")
        st = parts[2] if len(parts) > 2 and parts[2] in ("waiting", "grabbed", "done") else "grabbed"
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        text, keyboard = page_admin_grab_orders(st, page)
        await send_or_edit(query, text, keyboard)
        return

    # 强制释放：不罚接单员（管理员主动操作），单回待抢重派
    if data.startswith("a:gfree:"):
        oid = data[8:]
        order = db.get_order(oid)
        if order and order.grab_status == "grabbed":
            with db.atomic() as conn:
                db.release_grab_order(oid, conn=conn)
            audit(uid, "强制释放派单", oid)
            fresh = db.get_order(oid)
            try:
                await context.bot.send_message(
                    order.grabber_id,
                    f"🔓 订单 <code>#{oid}</code> 已被管理员释放（不记惩罚）", parse_mode="HTML",
                )
            except Exception:
                pass
            await grab.redispatch_order(fresh, "管理员强制释放")
            await _toast("🔓 已释放并重派")
        text, keyboard = page_admin_grab_orders("grabbed")
        await send_or_edit(query, text, keyboard)
        return

    # 撤回派单：回管理员人工池，不再派单
    if data.startswith("a:grecall:"):
        oid = data[10:]
        order = db.get_order(oid)
        if order and order.grab_status == "waiting":
            order.grab_status = ""
            db.save_order(order)
            audit(uid, "撤回派单", oid)
            if order.dispatch_chat and order.dispatch_msg:
                try:
                    await context.bot.edit_message_text(
                        chat_id=order.dispatch_chat, message_id=order.dispatch_msg,
                        text=f"↩️ <b>此单已被管理员撤回人工处理</b>\n\n订单：<code>#{oid}</code>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            await _toast("↩️ 已撤回，请在订单管理中人工回单")
        text, keyboard = page_admin_grab_orders("waiting")
        await send_or_edit(query, text, keyboard)
        return

    # ---------- 提现审核 ----------
    if data == "a:gwds" or data.startswith("a:gwds:"):
        page = int(data.split(":")[2]) if data.count(":") == 2 and data.split(":")[2].isdigit() else 0
        text, keyboard = page_admin_grab_withdrawals(page)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:gwdok:"):
        wid = data[8:]
        w = db.get_withdrawal(wid)
        if not w or w.status != "pending":
            await _toast("⚠️ 该提现单已处理过")
            text, keyboard = page_admin_grab_withdrawals()
            await send_or_edit(query, text, keyboard)
            return
        context.user_data["state"] = f"gbwdtx:{wid}"
        await send_or_edit(
            query,
            f"✅ <b>通过提现</b> <code>{wid}</code>\n\n"
            f"提现积分：<code>{w.amount}</code>\n"
            f"应打款：<b><code>{(w.amount_usdt or grab.points_to_usdt(w.amount)):.2f}</code></b> USDT\n"
            f"OK钱包ID：<code>{w.address}</code>\n\n"
            "请发送打款凭证 txid；没有凭证发送 <code>跳过</code>：",
            kb([[("❌ 取消", "a:gwds")]]),
        )
        return

    if data.startswith("a:gwdno:"):
        wid = data[8:]
        w = grab.reject_withdrawal(wid, uid)
        if w:
            audit(uid, "拒绝提现", f"{wid} · {w.amount}")
            try:
                await context.bot.send_message(
                    w.grabber_id,
                    f"❌ <b>提现被拒绝</b>\n\n单号：<code>{wid}</code>\n"
                    f"<code>{w.amount}</code> 积分已退回可提现余额，有疑问请发起申诉。",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await _toast("❌ 已拒绝并退回余额")
        text, keyboard = page_admin_grab_withdrawals()
        await send_or_edit(query, text, keyboard)
        return

    # ---------- 申诉仲裁 ----------
    if data == "a:gaps" or data.startswith("a:gaps:"):
        page = int(data.split(":")[2]) if data.count(":") == 2 and data.split(":")[2].isdigit() else 0
        text, keyboard = page_admin_grab_appeals(page)
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:gapok:") or data.startswith("a:gapno:"):
        approve = data.startswith("a:gapok:")
        aid = int(data.split(":")[2])
        a = grab.handle_appeal(aid, uid, approve)
        if a:
            audit(uid, "裁定申诉", f"#{aid} {'支持' if approve else '驳回'}")
            notice = (
                f"✅ <b>申诉已支持</b>\n\n申诉 <code>#{aid}</code>（订单 <code>#{a.order_id or '-'}</code>）\n"
                "冷静期已解除（如有）。"
                if approve else
                f"❌ <b>申诉被驳回</b>\n\n申诉 <code>#{aid}</code>（订单 <code>#{a.order_id or '-'}</code>）"
            )
            try:
                await context.bot.send_message(a.grabber_id, notice, parse_mode="HTML")
            except Exception:
                pass
            await _toast("✅ 已支持" if approve else "❌ 已驳回")
        text, keyboard = page_admin_grab_appeals()
        await send_or_edit(query, text, keyboard)
        return

    # ---------- 抢单设置 ----------
    if data == "a:gset":
        text, keyboard = page_admin_grab_settings()
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:gstog:"):
        key = data[8:]
        valid = {k for k, _, _, kind in _GRAB_SETTING_ITEMS if kind == "bool"}
        if key in valid:
            cur = bool(db.get_settings().get(key))
            db.update_settings(**{key: not cur})
            audit(uid, "抢单设置", f"{key} → {'开' if not cur else '关'}")
        text, keyboard = page_admin_grab_settings()
        await send_or_edit(query, text, keyboard)
        return

    if data.startswith("a:gsedit:"):
        key = data[9:]
        meta = {k: (name, desc, kind) for k, name, desc, kind in _GRAB_SETTING_ITEMS}
        if key in meta:
            name, desc, kind = meta[key]
            context.user_data["state"] = f"gset:{key}"
            tip = "请输入新值（数字）：" if kind == "num" else "请输入新值："
            await send_or_edit(
                query,
                f"✏️ <b>修改{name}</b>\n\n<i>{desc}</i>\n\n{tip}",
                kb([[("❌ 取消", "a:gset")]]),
            )
        return

    # 未知回调兜底：回主菜单，绝不让界面卡死
    logger.warning("未处理的回调: %s", data)
    text, keyboard = page_root(uid)
    await send_or_edit(query, text, keyboard)
