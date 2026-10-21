"""用户端：主菜单 / 商品 / 购买 / 订单 / 充值

由 handlers_callback.py 拆分而来（v2.6.4）。分支逻辑与原实现逐字一致，
仅把裸 `return` 改为 `return True`（已处理），函数末尾补 `return False`。
"""
from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from .models import STOCK_TYPES
from .pages_admin import (
    page_admin,
)
from .pages_user import (
    home_photo,
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
from .runtime import db, service, stock
from .shop_service import BUY_FAIL_REASONS, CHAIN_GATEWAYS, GATEWAY_NAMES, PAY_GATEWAYS
from .ui import h, kb, send_or_edit

logger = logging.getLogger(__name__)


def _resolve_product_category(data: str) -> str:
    """m:shop:{i} / m:report:{i} → 分类名（下标越界/空 = 全部）。"""
    parts = data.split(":")
    if len(parts) < 3 or not parts[2]:
        return ""
    cats = db.get_settings().get("product_categories") or []
    idx = parts[2]
    return cats[int(idx)] if idx.isdigit() and int(idx) < len(cats) else ""




async def _dispatch_user(
    query, context: ContextTypes.DEFAULT_TYPE, uid: int, data: str, tg_user
) -> bool:
    """用户端：主菜单 / 商品 / 购买 / 订单 / 充值。返回 True 表示该命名空间已处理，False 表示未命中。"""
    # ---------- 用户端导航 ----------
    if data == "m:root":
        ok, missing = await service.check_user_channels(uid)
        if not ok:
            text, keyboard = page_force_join(missing)
            await send_or_edit(query, text, keyboard)
        else:
            text, keyboard = page_root(uid)
            # 回主界面必须带欢迎图：不带的话 send_or_edit 会把图文消息改成纯文字
            await send_or_edit(query, text, keyboard, photo=home_photo() or None)
        return True

    if data == "m:checkjoin":
        ok, missing = await service.check_user_channels(uid)
        if not ok:
            text, keyboard = page_force_join(missing)
            await send_or_edit(query, "❌ 还未全部加入\n\n" + text, keyboard)
        else:
            text, keyboard = page_root(uid)
            await send_or_edit(query, text, keyboard, photo=home_photo() or None)
        return True

    if data == "m:shop" or data.startswith("m:shop:"):
        text, keyboard = page_list(uid, "product", _resolve_product_category(data))
        await send_or_edit(query, text, keyboard)
        return True

    if data == "m:report" or data.startswith("m:report:"):
        text, keyboard = page_list(uid, "report", _resolve_product_category(data))
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("m:search:"):
        kind = "report" if data.endswith(":report") else "product"
        context.user_data["state"] = f"search:{kind}"
        context.user_data["payload"] = {}
        await send_or_edit(
            query,
            "🔍 <b>搜索商品</b>\n\n请直接发送关键词（匹配名称/介绍/分类）：",
            kb([[("❌ 取消", "m:shop" if kind == "product" else "m:report")]]),
        )
        return True

    if data == "m:recharge":
        text, keyboard = page_recharge(uid)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "m:rchist":
        text, keyboard = page_recharge_history(uid)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "m:orders":
        text, keyboard = page_orders(uid)
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("m:ofl:"):
        text, keyboard = page_orders(uid, data[6:])
        await send_or_edit(query, text, keyboard)
        return True

    if data == "m:balance":
        text, keyboard = page_balance(uid)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "m:profile":
        text, keyboard = page_profile(uid)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "m:help":
        text, keyboard = page_help()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "m:admin":
        text, keyboard = page_admin(uid)
        await send_or_edit(query, text, keyboard)
        return True

    # ---------- 商品详情 / 购买 ----------
    if data.startswith("d:"):
        text, keyboard, photo = page_detail(uid, data[2:])
        await send_or_edit(query, text, keyboard, photo=photo)
        return True

    if data.startswith("buy:"):
        text, keyboard = page_buy_confirm(uid, data[4:])
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("cbuy:"):
        pid = data[5:]
        user = db.get_user(uid)
        if user and user.is_banned:
            await send_or_edit(query, "🚫 您的账号已被限制，无法购买。如有疑问请联系管理员", kb([[("🏠 主菜单", "m:root")]]))
            return True
        settings = db.get_settings()
        if settings.get("maintenance_mode") and not db.is_admin(uid):
            await send_or_edit(query, "🔧 系统维护中，暂时无法下单（充值不受影响）", kb([[("🏠 主菜单", "m:root")]]))
            return True
        p = db.get_product(pid)
        if not p or p.status != "on":
            await send_or_edit(query, "❌ 该条目不存在或已下架", kb([[("🏠 主菜单", "m:root")]]))
            return True

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
                return True
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
            return True

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
            return True

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
                return True
            mark = STOCK_TYPES.get(item.content_type, item.content_type) if item else ""
            text = f"{TG_CHECK} <b>购买成功</b>\n\n订单号：<code>{order.id}</code>\n📦 专属内容：<b>编号 #{item_id}</b>（{mark}）"
            if order.reply_content and item and item.content_type == "text":
                text += f"\n\n<code>{h(order.reply_content)}</code>"
            else:
                text += "\n\n内容已私信送达，可在订单详情随时「重新发送」"
            text += "\n\n💡 可在「我的订单」中随时查看"
            await send_or_edit(query, text, kb([[("📋 我的订单", "m:orders")], [("🏠 主菜单", "m:root")]]))
            return True

        text = f"{TG_CHECK} <b>购买成功</b>\n\n订单号：<code>{order.id}</code>"
        if order.reply_content:
            text += f"\n\n📤 发货内容：\n{h(order.reply_content)}"
        text += "\n\n💡 可在「我的订单」中随时查看"
        await send_or_edit(query, text, kb([[("📋 我的订单", "m:orders")], [("🏠 主菜单", "m:root")]]))
        return True

    # ---------- 用户订单 ----------
    if data.startswith("od:"):
        text, keyboard = page_order_detail(uid, data[3:])
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("urge:"):
        oid = data[5:]
        order = db.get_order(oid)
        if order and order.user_id == uid and order.status in ("pending", "processing"):
            await service.notify_admins_urge(order, uid)
            text = "✅ 催单成功，管理员会尽快处理"
        else:
            text = "❌ 无法催单"
        await send_or_edit(query, text, kb([[("⬅️ 返回", f"od:{oid}")]]))
        return True

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
        return True

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
        return True

    # ---------- 充值 ----------
    if data == "rc:custom":
        context.user_data["state"] = "rc_custom"
        await send_or_edit(query, "✏️ 请输入充值金额（USDT，数字）：", kb([[("❌ 取消", "m:recharge")]]))
        return True

    if data.startswith("rc:"):
        try:
            amount = float(data[3:])
        except ValueError:
            await send_or_edit(query, "❌ 金额无效", kb([[("⬅️ 返回", "m:recharge")]]))
            return True
        err = service.validate_recharge_amount(amount)
        if err:
            await send_or_edit(query, err, kb([[("⬅️ 返回", "m:recharge")]]))
            return True
        text, keyboard = page_recharge_confirm(amount)
        await send_or_edit(query, text, keyboard)
        return True

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
            return True
        # 限额安全兜底：绕过确认页直接构造回调也过不去
        err = service.validate_recharge_amount(amount)
        if err:
            await send_or_edit(query, err, kb([[("⬅️ 返回", "m:recharge")]]))
            return True
        # 冷钱包渠道：链上直转，不走支付平台
        if gateway in CHAIN_GATEWAYS:
            recharge = service.create_chain_recharge(uid, amount, gateway)
            if not recharge:
                await send_or_edit(
                    query,
                    "❌ 该渠道暂不可用或当前排队人数较多，请换其它支付方式",
                    kb([[("⬅️ 返回", "m:recharge")]]),
                )
                return True
            text, keyboard = page_chain_pay(recharge)
            await send_or_edit(query, text, keyboard)
            return True
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
        return True

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
        return True
    return False
