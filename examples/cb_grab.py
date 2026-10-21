"""抢单派单：群内抢单 / 接单员中心 / 接单管理 / 提现 / 申诉

由 handlers_callback.py 拆分而来（v2.6.4）。分支逻辑与原实现逐字一致，
仅把裸 `return` 改为 `return True`（已处理），函数末尾补 `return False`。
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .pages_admin import (
    _GRAB_SETTING_ITEMS,
    page_admin_grab,
    page_admin_grab_appeals,
    page_admin_grab_orders,
    page_admin_grab_settings,
    page_admin_grab_withdrawals,
    page_admin_grabber_detail,
    page_admin_grabbers,
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
from .runtime import db, grab
from .ui import audit, h, kb, send_or_edit

logger = logging.getLogger(__name__)




async def _dispatch_grab(
    query, context: ContextTypes.DEFAULT_TYPE, uid: int, data: str, tg_user
) -> bool:
    """抢单派单：群内抢单 / 接单员中心 / 接单管理 / 提现 / 申诉。返回 True 表示该命名空间已处理，False 表示未命中。"""
    # ==================================================================
    # 抢单模块（v2.6.0）：gb:* 接单员端 / a:g* 管理端接单管理
    # ==================================================================

    async def _toast(text: str, show_alert: bool = True, **_ignored) -> None:
        """二次弹 toast（入口处已 answer 过一次，这里容错）。

        参数名与 Telegram Bot API 的 `answerCallbackQuery` 保持一致（show_alert），
        早期实现写成 `alert`，而 3 处调用点传的是 `show_alert=True`，导致这些
        错误提示路径（邀请链接生成失败 / 重发失败 / 私聊发送失败）直接抛
        TypeError，用户看到的是「点了没反应」而不是错误原因。
        `**_ignored` 兜住历史调用里的其它别名，避免同类问题再炸。
        """
        try:
            await query.answer(text=text[:190], show_alert=show_alert)
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
        return True

    # ---------- 接单员中心 ----------
    if data == "gb:m":
        text, keyboard = page_grab_home(uid)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "gb:doing" or data.startswith("gb:doing:"):
        page = int(data.split(":")[2]) if data.count(":") == 2 and data.split(":")[2].isdigit() else 0
        text, keyboard = page_grab_doing(uid, page)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "gb:done" or data.startswith("gb:done:"):
        page = int(data.split(":")[2]) if data.count(":") == 2 and data.split(":")[2].isdigit() else 0
        text, keyboard = page_grab_done(uid, page)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "gb:money" or data.startswith("gb:money:"):
        page = int(data.split(":")[2]) if data.count(":") == 2 and data.split(":")[2].isdigit() else 0
        text, keyboard = page_grab_money(uid, page)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "gb:wd":
        text, keyboard = page_grab_withdraw(uid)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "gb:wdnew":
        g = db.get_grabber(uid)
        if not g:
            await _toast("你还不是接单员")
            return True
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
        return True

    # 接单员领取/查看专属入群链接
    if data == "gb:inv":
        g = db.get_grabber(uid)
        if not g:
            await _toast("你还不是接单员")
            return True
        if g.joined_group:
            await _toast("你已在接单群里，无需链接")
            return True
        link, err = await grab.ensure_invite_link(g)
        if not link:
            await _toast(err or "生成失败，请稍后再试", show_alert=True)
            return True
        await send_or_edit(
            query,
            "🔑 <b>你的专属入群链接</b>\n\n"
            "<blockquote>此链接只限你本人使用一次，进群即自动作废，切勿转发他人。</blockquote>",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("🔗 点我进入接单群", url=link),
            ], [InlineKeyboardButton("⬅️ 接单中心", callback_data="gb:m")]]),
        )
        return True

    if data == "gb:rank":
        text, keyboard = page_grab_rank(uid)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "gb:aps":
        text, keyboard = page_grab_appeals(uid)
        await send_or_edit(query, text, keyboard)
        return True

    # 回单按钮：校验这单确实是他抢的，进入回单输入状态
    if data.startswith("gb:r:"):
        oid = data[5:]
        order = db.get_order(oid)
        if not order or order.grab_status != "grabbed" or order.grabber_id != uid:
            await _toast("⚠️ 这单不在你手上或已超时释放")
            text, keyboard = page_grab_doing(uid)
            await send_or_edit(query, text, keyboard)
            return True
        context.user_data["state"] = f"gbreply:{oid}"
        await send_or_edit(
            query,
            f"📫 <b>回单</b> <code>#{oid}</code>\n\n"
            f"请直接发送回单内容（文字/图片/文件均可，支持加粗等排版）：",
            kb([[("❌ 取消", "gb:doing")]]),
        )
        return True

    # 放弃订单
    if data.startswith("gb:q:"):
        oid = data[5:]
        ok, msg = await grab.giveup(oid, uid)
        await _toast(("✅ " if ok else "⚠️ ") + msg)
        text, keyboard = page_grab_home(uid)
        await send_or_edit(query, text, keyboard)
        return True

    # 发起申诉（针对某单）
    if data.startswith("gb:ap:"):
        oid = data[5:]
        if not db.get_grabber(uid):
            await _toast("你还不是接单员")
            return True
        context.user_data["state"] = f"gbappeal:{oid}"
        await send_or_edit(
            query,
            f"🗣 <b>发起申诉</b>（关联订单 <code>#{oid}</code>）\n\n"
            "请发送申诉说明（500 字以内）：",
            kb([[("❌ 取消", "gb:aps")]]),
        )
        return True

    # ---------- 管理端：接单管理大分类 ----------
    if data == "a:grab":
        text, keyboard = page_admin_grab()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:gbers" or data.startswith("a:gbers:"):
        parts = data.split(":")
        st = parts[2] if len(parts) > 2 else ""
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        text, keyboard = page_admin_grabbers(st, page)
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:gber:"):
        text, keyboard = page_admin_grabber_detail(int(data[7:]))
        await send_or_edit(query, text, keyboard)
        return True

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
        return True

    # 重发/生成接单员专属入群链接（链接泄漏或进群失败时用）
    if data.startswith("a:gblink:"):
        g = db.get_grabber(int(data[9:]))
        if not g:
            await _toast("接单员不存在")
            return True
        link, err = await grab.ensure_invite_link(g, force=True)
        if not link:
            await _toast(err or "生成失败", show_alert=True)
            return True
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
        return True

    # 自动识群确认：把接单群切换到新群
    if data.startswith("a:guse:"):
        cid = data[7:]
        grab.confirm_grab_chat(cid)
        audit(uid, "切换接单群", cid)
        await _toast(f"✅ 接单群已切换为 {cid}，派单总开关已打开")
        text, keyboard = page_admin_grab_settings()
        await send_or_edit(query, text, keyboard)
        return True

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
        return True

    if data.startswith("a:gbdelok:"):
        uid2 = int(data[10:])
        g = db.get_grabber(uid2)
        if db.delete_grabber(uid2):
            audit(uid, "删除接单员档案", g.display() if g else str(uid2))
            await _toast("✅ 已删除档案")
        text, keyboard = page_admin_grabbers()
        await send_or_edit(query, text, keyboard)
        return True

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
        return True

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
        return True

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
        return True

    # ---------- 派单中心 ----------
    if data == "a:gords" or data.startswith("a:gords:"):
        parts = data.split(":")
        st = parts[2] if len(parts) > 2 and parts[2] in ("waiting", "grabbed", "done") else "grabbed"
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        text, keyboard = page_admin_grab_orders(st, page)
        await send_or_edit(query, text, keyboard)
        return True

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
        return True

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
        return True

    # ---------- 提现审核 ----------
    if data == "a:gwds" or data.startswith("a:gwds:"):
        page = int(data.split(":")[2]) if data.count(":") == 2 and data.split(":")[2].isdigit() else 0
        text, keyboard = page_admin_grab_withdrawals(page)
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:gwdok:"):
        wid = data[8:]
        w = db.get_withdrawal(wid)
        if not w or w.status != "pending":
            await _toast("⚠️ 该提现单已处理过")
            text, keyboard = page_admin_grab_withdrawals()
            await send_or_edit(query, text, keyboard)
            return True
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
        return True

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
        return True

    # ---------- 申诉仲裁 ----------
    if data == "a:gaps" or data.startswith("a:gaps:"):
        page = int(data.split(":")[2]) if data.count(":") == 2 and data.split(":")[2].isdigit() else 0
        text, keyboard = page_admin_grab_appeals(page)
        await send_or_edit(query, text, keyboard)
        return True

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
        return True

    # ---------- 抢单设置 ----------
    if data == "a:gset":
        text, keyboard = page_admin_grab_settings()
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:gstog:"):
        key = data[8:]
        valid = {k for k, _, _, kind in _GRAB_SETTING_ITEMS if kind == "bool"}
        if key in valid:
            cur = bool(db.get_settings().get(key))
            db.update_settings(**{key: not cur})
            audit(uid, "抢单设置", f"{key} → {'开' if not cur else '关'}")
        text, keyboard = page_admin_grab_settings()
        await send_or_edit(query, text, keyboard)
        return True

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
        return True

    # 未知 gb:/a:g* 前缀：交回入口的兜底分支处理，这里不渲染
    logger.debug("cb_grab 未命中: %s", data)
    return False
