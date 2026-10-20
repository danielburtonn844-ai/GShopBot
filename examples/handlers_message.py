"""消息处理（v2.4.0 模块化拆分）：文本输入状态机 + 管理员引用回复回单 + 补货向导。

输入状态一览：
  用户端：rc_custom（自定义充值金额）/ submit_info:{pid}（报单提交信息）/
          sup_text / sup_photo（供需向导）/ search:{kind}（商品搜索）
  管理端：padd_*（上架向导）/ pedit:{pid}:{field}（改字段）/ oc:{oid}（回单）/
          uadd:/usub:/usearch/admadd/chadd/sr/smin/sw（系统设置）/
          sup_cat_add / sup_chid / sup_set:*（供需配置）/ osearch（订单搜索）/
          pcatadd（商品分类）/ restock:{pid}（库存池补货，支持图册聚合）
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from .chain_watch import validate_chain_address
from .richtext import rich_from_message
from .runtime import db, grab, service, stock
from .shop_service import BUY_FAIL_REASONS
from .ui import audit, clear_state, h, kb, order_status_text, summarize_message
from .pages_user import page_recharge_confirm, page_search_results
from .pages_supply import page_supply_preview, page_admin_supply, page_admin_supply_cats, page_admin_supply_price
from .pages_admin import (
    _GRAB_SETTING_ITEMS,
    page_admin_admins,
    page_admin_channels,
    page_admin_grab_settings,
    page_admin_grab_withdrawals,
    page_admin_grabber_detail,
    page_admin_grabbers,
    page_admin_order,
    page_admin_payment,
    page_admin_pcats,
    page_admin_pedit,
    page_admin_settings,
    page_admin_user_detail,
)
from .pages_grabber import page_grab_home, page_grab_withdraw
from .pages_stock import restock_keyboard, restock_prompt

logger = logging.getLogger(__name__)

# 图册聚合防抖秒数：同一条多图消息会拆成多条 update 陆续到达，等最后一张到了再入库
ALBUM_DEBOUNCE = 1.0


async def _album_commit_later(bot, chat_id: int, user_data: dict, pid: str, mgid: str, version: int, admin_id: int) -> None:
    """图册防抖入库：等待 ALBUM_DEBOUNCE 秒后，若缓冲未被新图刷新则聚合成一件入库。"""
    await asyncio.sleep(ALBUM_DEBOUNCE)
    payload = user_data.get("payload") or {}
    buf = payload.get("album")
    if not buf or buf.get("mgid") != mgid or buf.get("version") != version:
        return  # 期间又有同组新图到达 / 已被其它路径（完成补货）flush
    n_before = int(payload.get("added", 0) or 0)
    if stock.flush_album_buffer(payload, pid, admin_id):
        added = int(payload.get("added", 0) or 0)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🖼 图册已入库为 <b>1 件</b>（{len(buf['file_ids'])} 张）\n"
                    f"本次共入库 <code>{added}</code> 件"
                ),
                parse_mode="HTML",
                reply_markup=restock_keyboard(pid),
            )
        except Exception as exc:
            logger.warning("图册入库回执发送失败：%s", exc)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    uid = update.effective_user.id
    message = update.message
    text = (message.text or message.caption or "").strip()
    photo_id = message.photo[-1].file_id if message.photo else ""
    state = context.user_data.get("state", "")

    # ---------- 管理员「引用回复」订单通知消息 → 直接回单 ----------
    replied = message.reply_to_message
    if replied and db.is_admin(uid):
        oid = db.get_order_id_by_notification(message.chat_id, replied.message_id)
        if oid:
            order = db.get_order(oid)
            if not order:
                await message.reply_text("❌ 该订单不存在", reply_markup=kb([[("📋 订单管理", "a:order")]]))
                return
            if order.status not in ("pending", "processing"):
                await message.reply_text(
                    f"⚠️ 订单 <code>{oid}</code> 已是「{order_status_text(order.status)}」状态，无需回单",
                    parse_mode="HTML",
                    reply_markup=kb([[("📄 订单详情", f"a:od:{oid}")]]),
                )
                return
            summary = summarize_message(message)
            done = service.complete_order(oid, summary, uid)
            if done:
                audit(uid, "回单", f"订单 {oid}（引用快捷回单）")
                await service.notify_user_order_completed(done, copy_from=(message.chat_id, message.message_id))
                await message.reply_text(
                    f"✅ 订单 <code>{oid}</code> 已完成\n回单已发送给用户",
                    parse_mode="HTML",
                    reply_markup=kb([[("📄 订单详情", f"a:od:{oid}")], [("📋 订单管理", "a:order")]]),
                )
            else:
                await message.reply_text("❌ 回单失败，请重试")
            return

    if not state:
        return

    # ---------- 自定义充值金额 ----------
    if state == "rc_custom":
        clear_state(context)
        try:
            amount = float(text)
        except ValueError:
            await message.reply_text("❌ 请输入有效数字", reply_markup=kb([[("⬅️ 返回", "m:recharge")]]))
            return
        # 限额统一走业务层校验（最低/最高都拦）
        err = service.validate_recharge_amount(amount)
        if err:
            await message.reply_text(err, reply_markup=kb([[("⬅️ 返回", "m:recharge")]]), parse_mode="HTML")
            return
        t, keyboard = page_recharge_confirm(amount)
        await message.reply_text(t, reply_markup=keyboard, parse_mode="HTML")
        return

    # ---------- 商品搜索（v2.4.0） ----------
    if state.startswith("search:"):
        kind = state.split(":", 1)[1]
        clear_state(context)
        keyword = text[:30]
        if not keyword:
            await message.reply_text(
                "❌ 请输入文字关键词",
                reply_markup=kb([[("⬅️ 返回列表", "m:shop" if kind == "product" else "m:report")]]),
            )
            return
        t, keyboard = page_search_results(uid, kind, keyword)
        await message.reply_text(t, reply_markup=keyboard, parse_mode="HTML")
        return

    # ---------- 报单类：用户提交所需信息（必填步骤，任意消息类型） ----------
    if state.startswith("submit_info:"):
        pid = state.split(":", 1)[1]
        summary = summarize_message(message)
        order, reason = service.create_order(uid, pid, note=summary)
        if not order:
            clear_state(context)
            text = f"❌ 下单失败：{BUY_FAIL_REASONS.get(reason or '', '未知原因')}"
            buttons = []
            if reason == "points":
                buttons.append([("💰 去充值", "m:recharge")])
            buttons.append([("⬅️ 返回商品", f"d:{pid}")])
            await message.reply_text(text, reply_markup=kb(buttons), parse_mode="HTML")
            return
        clear_state(context)
        # 管理员通知里带上订单信息，并把用户提交信息的原始消息一并转过去
        await service.notify_admins_new_order(order, copy_from=(message.chat_id, message.message_id))
        # v2.6.0：报单内容自动派到接单群（商品走库存池自动发货，不派单）；
        # 匿名卡片不含买家信息，接单员抢单后深链回机器人回单
        if grab.enabled():
            asyncio.create_task(grab.dispatch_order(order))
        text = (
            f"✅ <b>报单成功</b>\n\n"
            f"订单号：<code>{order.id}</code>\n"
            f"状态：⏳ 待处理\n\n"
            f"📎 您提交的信息已随订单发给管理员\n"
            f"管理员处理完成后会第一时间通知您，可在「我的订单」中查看进度和回单"
        )
        await message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=kb([[("📋 我的订单", "m:orders")], [("🏠 主菜单", "m:root")]]),
        )
        return

    # ==================================================================
    # 抢单模块输入状态（v2.6.0）
    # ==================================================================

    # ---------- 接单员回单（任意消息类型，原样转给买家） ----------
    if state.startswith("gbreply:"):
        oid = state.split(":", 1)[1]
        # 只认本人抢中的进行中单
        order = db.get_order(oid)
        if not order or order.grab_status != "grabbed" or order.grabber_id != uid:
            clear_state(context)
            await message.reply_text(
                "⚠️ 这单不在你手上或已超时释放",
                reply_markup=kb([[("🥷 接单员中心", "gb:m")]]),
            )
            return
        clear_state(context)
        # 回单正文：优先富文本（接单员排的加粗/表情原样进订单），附消息类型摘要
        plain, html_text = rich_from_message(message)
        summary = summarize_message(message)
        reply_html = html_text or h(summary)
        done, err = grab.complete_by_grabber(oid, uid, reply_html)
        if not done:
            await message.reply_text(
                f"❌ 回单失败：{h(err or '未知原因')}",
                parse_mode="HTML",
                reply_markup=kb([[("🥷 接单员中心", "gb:m")]]),
            )
            return
        await grab.after_grabber_complete(done, uid, copy_from=(message.chat_id, message.message_id))
        await message.reply_text(
            f"✅ <b>回单成功</b>\n\n订单 <code>#{oid}</code> 已完成，佣金结算中",
            parse_mode="HTML",
            reply_markup=kb([[("🥷 接单员中心", "gb:m")], [("💰 我的钱包", "gb:money")]]),
        )
        return

    # ---------- 接单员提现申请（积分 OK钱包ID，自动换算U） ----------
    if state == "gbwd":
        clear_state(context)
        parts = text.split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isdigit():
            await message.reply_text(
                "❌ 格式不对，请发送：<code>积分 OK钱包ID</code>\n"
                f"例：<code>500 12345678</code>（500 积分 ≈ {grab.points_to_usdt(500):.2f} U，打到 OK 钱包 12345678）",
                parse_mode="HTML",
                reply_markup=kb([[("✍️ 重新填写", "gb:wdnew")], [("⬅️ 返回", "gb:wd")]]),
            )
            return
        w, err = grab.apply_withdrawal(uid, int(parts[0]), parts[1])
        if not w:
            t, keyboard = page_grab_withdraw(uid)
            await message.reply_text(
                f"❌ {h(err)}\n\n" + t, parse_mode="HTML", reply_markup=keyboard
            )
            return
        await grab.notify_admins_withdrawal(w)
        await message.reply_text(
            f"✅ <b>提现申请已提交</b>\n\n"
            f"单号：<code>{w.id}</code>\n"
            f"提现积分：<code>{w.amount}</code>\n"
            f"自动换算：<b>{w.amount_usdt:.2f}</b> USDT\n"
            f"打款账户：OK钱包 <code>{h(w.address)}</code>\n\n"
            f"管理员按 <b>{w.amount_usdt:.2f} USDT</b> 人工打款到你的 OK 钱包，到账以 OK 钱包为准",
            parse_mode="HTML",
            reply_markup=kb([[("💰 我的钱包", "gb:money")], [("🥷 接单员中心", "gb:m")]]),
        )
        return

    # ---------- 接单员申诉 ----------
    if state.startswith("gbappeal:"):
        oid = state.split(":", 1)[1]
        clear_state(context)
        appeal, err = grab.create_appeal(uid, oid, text)
        if not appeal:
            await message.reply_text(
                f"❌ {h(err)}",
                parse_mode="HTML",
                reply_markup=kb([[("🗣 我的申诉", "gb:aps")], [("🥷 接单员中心", "gb:m")]]),
            )
            return
        await grab.notify_admins_appeal(appeal)
        await message.reply_text(
            f"✅ <b>申诉已提交</b>\n\n申诉号：<code>#{appeal.id}</code>\n管理员裁定后会私信通知你",
            parse_mode="HTML",
            reply_markup=kb([[("🗣 我的申诉", "gb:aps")], [("🥷 接单员中心", "gb:m")]]),
        )
        return

    # ---------- 管理端：添加接单员 ----------
    if state == "gbadd":
        if not db.is_admin(uid):
            clear_state(context)
            return
        clear_state(context)
        target = text.strip().lstrip("@")
        target_id = 0
        if target.isdigit():
            target_id = int(target)
        else:
            # 尝试按 username 在用户表里找
            conn_row = None
            for u in db.list_users(limit=500):
                if (u.username or "").lower() == target.lower():
                    conn_row = u
                    break
            if conn_row:
                target_id = conn_row.user_id
        if not target_id:
            await message.reply_text(
                "❌ 没找到这个用户。请确认对方已与机器人 /start 过，并直接发送其 TG 数字 ID。",
                reply_markup=kb([[("➕ 重新添加", "a:gbadd")], [("⬅️ 返回", "a:gbers")]]),
            )
            return
        u = db.get_user(target_id)
        g, msg = grab.add_grabber(
            target_id,
            username=(u.username if u else "") or "",
            name=((u.first_name or "") + " " + (u.last_name or "")).strip() if u else "",
        )
        if not g:
            await message.reply_text(
                f"❌ {h(msg)}",
                parse_mode="HTML",
                reply_markup=kb([[("⬅️ 返回", "a:gbers")]]),
            )
            return
        audit(uid, "添加接单员", g.display())
        if msg:
            await message.reply_text(
                f"ℹ️ {h(msg)}",
                parse_mode="HTML",
                reply_markup=kb([[("⚙️ 查看详情", f"a:gber:{g.user_id}")], [("⬅️ 返回", "a:gbers")]]),
            )
            return
        # 三连提示：欢迎卡 + 专属入群链接卡 + 接单须知（v2.6.1）
        link_err = await grab.notify_grabber_welcome(g)
        if link_err:
            await message.reply_text(
                f"⚠️ 专属入群链接未生成：{h(link_err)}",
                parse_mode="HTML",
                reply_markup=kb([[("🔗 重发入群链接", f"a:gblink:{g.user_id}")], [("⚙️ 查看详情", f"a:gber:{g.user_id}")]]),
            )
        t, keyboard = page_admin_grabber_detail(g.user_id)
        await message.reply_text(
            f"✅ <b>接单员已建档</b>：{h(g.display())}\n\n" + t,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # ---------- 管理端：抢单设置数值/文本 ----------
    if state.startswith("gset:"):
        if not db.is_admin(uid):
            clear_state(context)
            return
        key = state.split(":", 1)[1]
        meta = {k: (name, desc, kind) for k, name, desc, kind in _GRAB_SETTING_ITEMS}
        if key not in meta:
            clear_state(context)
            return
        name, desc, kind = meta[key]
        clear_state(context)
        raw = text.strip()
        if kind == "num":
            try:
                val = float(raw)
                if val < 0:
                    raise ValueError
                # 秒/分钟/次数用整数，小时与比例允许小数
                val = int(val) if key not in ("grab_timeout_hours", "grab_freeze_hours") else val
            except ValueError:
                await message.reply_text(
                    "❌ 请输入不小于 0 的数字",
                    reply_markup=kb([[("⬅️ 返回抢单设置", "a:gset")]]),
                )
                return
        else:
            val = raw
            if key == "grab_chat_id" and val and not (val.lstrip("-").isdigit()):
                await message.reply_text(
                    "❌ 群 ID 格式不对，应为纯数字（负数），如 <code>-1001234567890</code>",
                    parse_mode="HTML",
                    reply_markup=kb([[("⬅️ 返回抢单设置", "a:gset")]]),
                )
                return
        db.update_settings(**{key: val})
        audit(uid, "抢单设置", f"{name} → {val}")
        t, keyboard = page_admin_grab_settings()
        await message.reply_text(
            f"✅ <b>{h(name)}</b> 已更新为 <code>{h(str(val))}</code>\n\n" + t,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # ---------- 管理端：提现通过填 txid ----------
    if state.startswith("gbwdtx:"):
        if not db.is_admin(uid):
            clear_state(context)
            return
        wid = state.split(":", 1)[1]
        clear_state(context)
        txid = "" if text.strip() in ("跳过", "-", "0") else text.strip()
        w = grab.approve_withdrawal(wid, uid, txid)
        if not w:
            await message.reply_text(
                "⚠️ 该提现单已被处理过",
                reply_markup=kb([[("⬅️ 返回提现审核", "a:gwds")]]),
            )
            return
        audit(uid, "通过提现", f"{wid} · {w.amount}")
        usdt = w.amount_usdt or grab.points_to_usdt(w.amount)
        try:
            await context.bot.send_message(
                w.grabber_id,
                f"✅ <b>提现已打款到 OK 钱包</b>\n\n"
                f"单号：<code>{w.id}</code>\n"
                f"提现积分：<code>{w.amount}</code>\n"
                f"实收：<b>{usdt:.2f}</b> USDT → OK钱包 <code>{h(w.address)}</code>"
                + (f"\n凭证：<code>{h(txid)}</code>" if txid else "")
                + "\n\n请打开 OK 钱包查收，未到账请联系管理员",
                parse_mode="HTML",
            )
        except Exception:
            pass
        t, keyboard = page_admin_grab_withdrawals()
        await message.reply_text(
            f"✅ 提现 <code>{wid}</code> 已标记打款\n\n" + t,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # ---------- 供需发布向导：填写内容 ----------
    if state == "sup_text":
        payload = context.user_data.get("payload")
        if not payload:
            clear_state(context)
            await message.reply_text("❌ 会话已过期，请重新开始", reply_markup=kb([[("✍️ 发布供需", "sup:new")]]))
            return
        if not text:
            await message.reply_text("❌ 内容不能为空，请发送文字内容", reply_markup=kb([[("❌ 取消", "sup:cancel")]]))
            return
        max_len = int(db.get_settings().get("supply_max_len", 600))
        if len(text) > max_len:
            await message.reply_text(
                f"❌ 内容超长：当前 {len(text)} 字，限制 {max_len} 字以内，请精简后重发",
                reply_markup=kb([[("❌ 取消", "sup:cancel")]]),
            )
            return
        # 纯文本用于字数校验/列表摘要；HTML（含加粗/链接/高级会员表情）用于预览与发布展示
        plain, rich = rich_from_message(message)
        payload["content"] = plain or text
        payload["content_html"] = rich
        if photo_id:
            # 图文同发 → 无需附图步骤，直接预览
            payload["photo_file_id"] = photo_id
            context.user_data["state"] = "sup_preview"
            user = db.get_user(uid)
            t, keyboard = page_supply_preview(payload, user)
            await message.reply_text(t, reply_markup=keyboard, parse_mode="HTML")
            return
        # 纯文字 → 给一个可选附图步骤
        context.user_data["state"] = "sup_photo"
        await message.reply_text(
            "🖼 <b>可选：为这条供需附一张图</b>\n\n"
            "直接发送图片即可（图片会随帖展示，无文字说明）；不需要就点「跳过」",
            parse_mode="HTML",
            reply_markup=kb([[("⏭ 跳过，直接预览", "sup:nophoto")], [("❌ 取消", "sup:cancel")]]),
        )
        return

    # ---------- 供需发布向导：可选附图 ----------
    if state == "sup_photo":
        payload = context.user_data.get("payload")
        if not payload:
            clear_state(context)
            await message.reply_text("❌ 会话已过期，请重新开始", reply_markup=kb([[("✍️ 发布供需", "sup:new")]]))
            return
        if not photo_id:
            await message.reply_text(
                "❌ 请发送图片，或点击「跳过，直接预览」",
                reply_markup=kb([[("⏭ 跳过，直接预览", "sup:nophoto")], [("❌ 取消", "sup:cancel")]]),
            )
            return
        payload["photo_file_id"] = photo_id
        context.user_data["state"] = "sup_preview"
        user = db.get_user(uid)
        t, keyboard = page_supply_preview(payload, user)
        await message.reply_text(t, reply_markup=keyboard, parse_mode="HTML")
        return

    # 以下均为管理员操作
    if not db.is_admin(uid):
        clear_state(context)
        return

    # ---------- 库存池补货（v2.4.0）：发一条 = 补一件，图册自动聚合 ----------
    if state.startswith("restock:"):
        pid = state.split(":", 1)[1]
        payload = context.user_data.setdefault("payload", {"added": 0, "album": None})
        p = db.get_product(pid)
        if not p or getattr(p, "delivery_mode", "fixed") != "pool":
            clear_state(context)
            await message.reply_text("❌ 商品不存在或已不是库存池模式", reply_markup=kb([[("⬅️ 返回", "a:pool")]]))
            return

        mgid = getattr(message, "media_group_id", None)
        if mgid and message.photo:
            # 图册：同 media_group_id 的多张图聚合成一件，防抖等待最后一张
            buf = payload.get("album")
            if buf and buf.get("mgid") != mgid:
                # 上一组图册还没入库就来了新组：先把旧组入库
                stock.flush_album_buffer(payload, pid, uid)
                buf = None
            if not buf:
                buf = {"mgid": mgid, "file_ids": [], "caption": "", "version": 0}
                payload["album"] = buf
            buf["file_ids"].append(photo_id)
            cap = (message.caption or "").strip()
            if cap:
                buf["caption"] = cap
            buf["version"] += 1
            asyncio.get_event_loop().create_task(
                _album_commit_later(context.bot, message.chat_id, context.user_data, pid, mgid, buf["version"], uid)
            )
            return

        # 非图册消息：先把可能挂着的图册缓冲入库，再处理本条
        stock.flush_album_buffer(payload, pid, uid)
        item = stock.parse_message(message)
        if not item:
            await message.reply_text(
                "❌ 无法识别的内容，支持：文字 / 图片 / 文件 / 图册",
                reply_markup=restock_keyboard(pid),
            )
            return
        added_item = stock.add_item(pid, item, uid)
        if not added_item:
            clear_state(context)
            await message.reply_text("❌ 入库失败，商品可能已被删除", reply_markup=kb([[("⬅️ 返回", "a:pool")]]))
            return
        payload["added"] = int(payload.get("added", 0) or 0) + 1
        from .models import STOCK_TYPES
        mark = STOCK_TYPES.get(item["content_type"], item["content_type"])
        await message.reply_text(
            f"✅ 已入库 <b>编号 #{added_item.id}</b>（{mark}）· 本次共 <code>{payload['added']}</code> 件\n"
            f"现货总数：<code>{db.count_stock_items(pid, 'available')}</code> 件",
            parse_mode="HTML",
            reply_markup=restock_keyboard(pid),
        )
        return

    # ---------- 上架商品向导 ----------
    if state == "padd_name":
        context.user_data["payload"]["name"] = text
        context.user_data["state"] = "padd_desc"
        await message.reply_text(
            "第 2 步：请输入商品介绍\n\n"
            "💡 介绍会展示在商品详情页。若是报单类商品，请在介绍里写清楚"
            "<b>需要用户提供哪些信息</b>（如：游戏ID、区服、账号截图），"
            "用户购买时会被要求按介绍提交这些信息\n\n"
            "📷 发送图片可同时设置商品图",
            parse_mode="HTML",
            reply_markup=kb([[("❌ 取消", "a:prod")]]),
        )
        return

    if state == "padd_desc":
        context.user_data["payload"]["description"] = text
        if photo_id:
            context.user_data["payload"]["photo_file_id"] = photo_id
        context.user_data["state"] = "padd_points"
        await message.reply_text(
            "第 3 步：请输入价格（积分，正整数）\n\n💡 用户需充值积分后购买，例如 100",
            reply_markup=kb([[("❌ 取消", "a:prod")]]),
        )
        return

    if state == "padd_points":
        try:
            points = int(text)
            if points <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text("❌ 请输入正整数", reply_markup=kb([[("❌ 取消", "a:prod")]]))
            return
        context.user_data["payload"]["points"] = points
        context.user_data["state"] = "padd_kind"
        await message.reply_text(
            "第 4 步：请选择类型\n\n"
            "🛒 <b>商品</b>：用户付款后立即自动发货（固定内容或库存池，下一步选）\n"
            "📝 <b>报单</b>：用户付款时提交所需信息，管理员人工处理后回单（适合代办、查档等）",
            parse_mode="HTML",
            reply_markup=kb([[("🛒 商品（自动发货）", "padd_kind:product"), ("📝 报单（人工处理）", "padd_kind:report")]]),
        )
        return

    if state == "padd_stock":
        try:
            stock_val = int(text)
        except ValueError:
            await message.reply_text("❌ 请输入整数（-1 表示无限）", reply_markup=kb([[("❌ 取消", "a:prod")]]))
            return
        context.user_data["payload"]["stock"] = stock_val
        context.user_data["state"] = "padd_limit"
        await message.reply_text(
            "第 6 步：请输入每人限购数量（0 表示不限）\n\n💡 限购按用户累计有效订单数计算，退款单不计入",
            reply_markup=kb([[("❌ 取消", "a:prod")]]),
        )
        return

    if state == "padd_limit":
        try:
            limit = int(text)
            if limit < 0:
                raise ValueError
        except ValueError:
            await message.reply_text("❌ 请输入非负整数", reply_markup=kb([[("❌ 取消", "a:prod")]]))
            return
        context.user_data["payload"]["limit_per_user"] = limit
        payload = context.user_data["payload"]
        if payload.get("delivery_mode") == "pool":
            # 库存池模式：现货数由池子决定，无需固定库存与发货内容
            payload["stock"] = 0
            payload["auto_delivery"] = ""
            product = service.create_product(payload)
            clear_state(context)
            audit(uid, "上架商品", f"{product.name}（库存池）")
            await message.reply_text(
                f"✅ 上架成功\n\n名称：{h(product.name)}\n类型：商品（📦 库存池）\n"
                f"价格：<code>{product.points}</code> 积分\n\n"
                f"下一步：到「库存池」给 TA 补货，补多少卖多少",
                parse_mode="HTML",
                reply_markup=kb([[("➕ 立即补货", f"a:stkadd:{product.id}")], [("📦 商品管理", "a:prod")]]),
            )
            return
        if payload.get("kind") == "product":
            context.user_data["state"] = "padd_auto"
            await message.reply_text(
                "第 7 步：请输入自动发货内容（用户购买后立即收到）\n\n💡 例如卡密、下载链接、教程内容；多个内容请提前写在一条消息里",
                reply_markup=kb([[("❌ 取消", "a:prod")]]),
            )
        else:
            product = service.create_product(payload)
            clear_state(context)
            audit(uid, "上架报单", product.name)
            await message.reply_text(
                f"✅ 上架成功\n\n名称：{h(product.name)}\n类型：报单（人工处理）\n价格：<code>{product.points}</code> 积分",
                parse_mode="HTML",
                reply_markup=kb([[("📦 商品管理", "a:prod")]]),
            )
        return

    if state == "padd_auto":
        context.user_data["payload"]["auto_delivery"] = text
        product = service.create_product(context.user_data["payload"])
        clear_state(context)
        audit(uid, "上架商品", product.name)
        await message.reply_text(
            f"✅ 上架成功\n\n名称：{h(product.name)}\n类型：商品（自动发货）\n价格：<code>{product.points}</code> 积分",
            parse_mode="HTML",
            reply_markup=kb([[("📦 商品管理", "a:prod")]]),
        )
        return

    # ---------- 编辑商品字段 ----------
    if state.startswith("pedit:"):
        _, pid, field_name = state.split(":", 2)
        p = db.get_product(pid)
        clear_state(context)
        if not p:
            await message.reply_text("❌ 商品不存在", reply_markup=kb([[("⬅️ 返回", "a:prod")]]))
            return
        if field_name in ("points", "stock", "limit_per_user"):
            try:
                value = int(text)
            except ValueError:
                await message.reply_text("❌ 请输入整数", reply_markup=kb([[("❌ 取消", f"a:pedit:{pid}")]]))
                return
            setattr(p, field_name, value)
        else:
            setattr(p, field_name, text)
            if field_name == "description" and photo_id:
                p.photo_file_id = photo_id
        db.save_product(p)
        audit(uid, "改商品字段", f"{p.name}.{field_name}")
        t, keyboard = page_admin_pedit(pid)
        await message.reply_text("✅ 已更新\n\n" + t, reply_markup=keyboard, parse_mode="HTML")
        return

    # ---------- 完成订单（回单，支持任意消息类型） ----------
    if state.startswith("oc:"):
        oid = state[3:]
        clear_state(context)
        order = db.get_order(oid)
        if not order or order.status not in ("pending", "processing"):
            await message.reply_text("❌ 订单不存在或已处理完毕", reply_markup=kb([[("⬅️ 返回", "a:order")]]))
            return
        summary = summarize_message(message)
        done = service.complete_order(oid, summary, uid)
        if done:
            audit(uid, "回单", f"订单 {oid}")
            await service.notify_user_order_completed(done, copy_from=(message.chat_id, message.message_id))
            await message.reply_text(
                f"✅ 订单 <code>{oid}</code> 已完成，回单已发送给用户",
                parse_mode="HTML",
                reply_markup=kb([[("📋 订单管理", "a:order")]]),
            )
        else:
            await message.reply_text("❌ 完成失败")
        return

    # ---------- 加/扣积分 ----------
    if state.startswith("uadd:") or state.startswith("usub:"):
        action, target = state.split(":", 1)
        target_uid = int(target)
        clear_state(context)
        try:
            amount = int(text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text("❌ 请输入正整数", reply_markup=kb([[("⬅️ 返回", f"a:ud:{target_uid}")]]))
            return
        delta = amount if action == "uadd" else -amount
        reason = "管理员加分" if action == "uadd" else "管理员减分"
        service.update_user_points(target_uid, delta, operator_id=uid, reason=reason)
        audit(uid, "加分" if action == "uadd" else "减分", f"用户 {target_uid} · {delta}")
        t, keyboard = page_admin_user_detail(target_uid)
        await message.reply_text("✅ 已更新\n\n" + t, reply_markup=keyboard, parse_mode="HTML")
        return

    # ---------- 搜索用户 ----------
    if state == "usearch":
        clear_state(context)
        results = db.search_users(text, limit=10)
        if not results:
            await message.reply_text("🔍 未找到匹配用户", reply_markup=kb([[("⬅️ 返回", "a:user")]]))
            return
        lines = [f"<b>🔍 搜索结果</b>（{len(results)} 条）", ""]
        buttons = []
        for u in results:
            ban = "🚫" if u.is_banned else "👤"
            lines.append(f"{ban} @{h(u.username or str(u.user_id))}  <code>{u.points}</code> 积分")
            buttons.append([(f"⚙️ @{h(u.username or str(u.user_id))}", f"a:ud:{u.user_id}")])
        buttons.append([("⬅️ 返回", "a:user")])
        await message.reply_text("\n".join(lines), reply_markup=kb(buttons), parse_mode="HTML")
        return

    # ---------- 搜索订单（v2.4.0） ----------
    if state == "osearch":
        clear_state(context)
        results = db.search_orders(text, limit=15)
        if not results:
            await message.reply_text("🔍 未找到匹配订单", reply_markup=kb([[("⬅️ 返回", "a:order")]]))
            return
        lines = [f"<b>🔍 订单搜索</b>（{len(results)} 条）", ""]
        buttons = []
        for o in results:
            lines.append(f"{order_status_text(o.status)} <code>{o.id}</code> {h(o.product_name)} @{h(o.username or '-')}")
            buttons.append([(f"📄 {o.id}", f"a:od:{o.id}")])
        buttons.append([("⬅️ 返回", "a:order")])
        await message.reply_text("\n".join(lines), reply_markup=kb(buttons), parse_mode="HTML")
        return

    # ---------- 添加管理员 ----------
    if state == "admadd":
        clear_state(context)
        try:
            new_admin = int(text)
        except ValueError:
            await message.reply_text("❌ 请输入数字 ID", reply_markup=kb([[("⬅️ 返回", "a:adm")]]))
            return
        service.add_admin(new_admin, f"管理员{new_admin}")
        audit(uid, "添加管理员", str(new_admin))
        t, keyboard = page_admin_admins()
        await message.reply_text("✅ 已添加\n\n" + t, reply_markup=keyboard, parse_mode="HTML")
        return

    # ---------- 添加频道 ----------
    if state == "chadd":
        clear_state(context)
        if not text:
            await message.reply_text("❌ 请输入频道用户名或 chat_id", reply_markup=kb([[("⬅️ 返回", "a:ch")]]))
            return
        if text.startswith("@"):
            chat_id, username = text, text.lstrip("@")
        elif text.startswith("-100"):
            chat_id, username = text, ""
        else:
            chat_id, username = f"@{text}", text
        service.add_channel(chat_id=chat_id, title=username or chat_id, username=username, added_by=uid)
        audit(uid, "添加频道", chat_id)
        t, keyboard = page_admin_channels()
        await message.reply_text(
            "✅ 已添加\n\n⚠️ 请确保已将机器人设为该频道管理员，否则无法检测用户关注状态\n\n" + t,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    # ---------- 积分比例 ----------
    if state == "sr":
        clear_state(context)
        try:
            rate = int(text)
            if rate <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text("❌ 请输入正整数", reply_markup=kb([[("⬅️ 返回", "a:set")]]))
            return
        db.update_settings(points_rate=rate)
        audit(uid, "改积分比例", f"1 USDT = {rate} 积分")
        t, keyboard = page_admin_settings()
        await message.reply_text("✅ 已更新\n\n" + t, reply_markup=keyboard, parse_mode="HTML")
        return

    # ---------- 最低充值金额 ----------
    if state == "smin":
        clear_state(context)
        try:
            min_amount = float(text)
            if min_amount <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text("❌ 请输入有效数字", reply_markup=kb([[("⬅️ 返回", "a:set")]]))
            return
        db.update_settings(min_recharge_usdt=min_amount)
        audit(uid, "改最低充值", f"{min_amount} USDT")
        t, keyboard = page_admin_settings()
        await message.reply_text("✅ 已更新\n\n" + t, reply_markup=keyboard, parse_mode="HTML")
        return

    # ---------- 支付设置（v2.5.0）：限额 / 冷钱包地址 / API Key ----------
    if state == "pay_limit":
        clear_state(context)
        parts = text.replace("，", " ").split()
        try:
            min_v = float(parts[0])
            max_v = float(parts[1]) if len(parts) > 1 else 0.0
            if min_v <= 0 or max_v < 0 or (max_v > 0 and max_v < min_v):
                raise ValueError
        except (ValueError, IndexError):
            await message.reply_text(
                "❌ 格式错误，请输入：<code>最低 最高</code>（如 <code>1 500</code>，最高填 0 表示不限）",
                reply_markup=kb([[("⬅️ 返回", "a:pay")]]),
                parse_mode="HTML",
            )
            return
        db.update_settings(min_recharge_usdt=min_v, max_recharge_usdt=max_v)
        audit(uid, "改充值限额", f"{min_v:g} ~ {max_v:g} USDT")
        t, keyboard = page_admin_payment()
        await message.reply_text("✅ 限额已更新\n\n" + t, reply_markup=keyboard, parse_mode="HTML")
        return

    if state.startswith("pay_addr:"):
        clear_state(context)
        chain = state.split(":", 1)[1]  # trc20 / ton
        addr = text.strip()
        if not validate_chain_address(chain, addr):
            await message.reply_text(
                "❌ 地址格式不正确，未保存\n"
                + ("TRON 地址应为 T 开头 34 位" if chain == "trc20" else "TON 地址应为 EQ/UQ 开头 48 位"),
                reply_markup=kb([[("⬅️ 返回", "a:pay")]]),
            )
            return
        key = "tron_wallet_address" if chain == "trc20" else "ton_wallet_address"
        db.update_settings(**{key: addr})
        audit(uid, "设置收款地址", f"{chain} {addr[:10]}…")
        t, keyboard = page_admin_payment()
        await message.reply_text("✅ 收款地址已保存\n\n" + t, reply_markup=keyboard, parse_mode="HTML")
        return

    if state.startswith("pay_key:"):
        clear_state(context)
        chain = state.split(":", 1)[1]  # tron / ton
        api_key = "" if text.strip() == "0" else text.strip()[:100]
        key = "trongrid_api_key" if chain == "tron" else "toncenter_api_key"
        db.update_settings(**{key: api_key})
        audit(uid, "设置链上 API Key", f"{chain} {'已清除' if not api_key else '已设置'}")
        t, keyboard = page_admin_payment()
        await message.reply_text(
            ("✅ API Key 已清除\n\n" if not api_key else "✅ API Key 已保存\n\n") + t,
            reply_markup=keyboard, parse_mode="HTML",
        )
        return

    # ---------- 欢迎语 / 欢迎图 ----------
    if state == "sw":
        clear_state(context)
        # 双写：纯文本做摘要/备份，HTML（含加粗/链接/高级会员表情）做前台展示
        plain, rich = rich_from_message(message)
        if photo_id:
            db.update_settings(welcome_image=photo_id)
            if text:
                db.update_settings(welcome_text=plain or text, welcome_text_html=rich)
            audit(uid, "设置欢迎图", "")
            await message.reply_text("✅ 欢迎图（及文字）已设置", reply_markup=kb([[("⚙️ 系统设置", "a:set")]]))
        elif text:
            db.update_settings(welcome_text=plain or text, welcome_text_html=rich)
            audit(uid, "设置欢迎语", (plain or text)[:30])
            await message.reply_text("✅ 欢迎语已更新", reply_markup=kb([[("⚙️ 系统设置", "a:set")]]))
        else:
            await message.reply_text("❌ 请发送文字或图片", reply_markup=kb([[("⚙️ 系统设置", "a:set")]]))
        return

    # ---------- 商品分类：添加 ----------
    if state == "pcatadd":
        clear_state(context)
        name = text.strip()
        s = db.get_settings()
        cats = list(s.get("product_categories") or [])
        if not name or len(name) > 12:
            await message.reply_text("❌ 分类名需为 1~12 字", reply_markup=kb([[("⬅️ 返回", "a:pcat")]]))
            return
        if name in cats:
            await message.reply_text("❌ 该分类已存在", reply_markup=kb([[("⬅️ 返回", "a:pcat")]]))
            return
        if len(cats) >= 12:
            await message.reply_text("❌ 最多 12 个分类，请先删除不用的", reply_markup=kb([[("⬅️ 返回", "a:pcat")]]))
            return
        cats.append(name)
        db.update_settings(product_categories=cats)
        audit(uid, "添加商品分类", name)
        t, keyboard = page_admin_pcats()
        await message.reply_text(f"✅ 已添加分类「{h(name)}」\n\n" + t, reply_markup=keyboard, parse_mode="HTML")
        return

    # ---------- 供需：添加分类 ----------
    if state == "sup_cat_add":
        clear_state(context)
        name = text.strip()
        s = db.get_settings()
        cats = list(s.get("supply_categories") or [])
        if not name or len(name) > 12:
            await message.reply_text("❌ 分类名需为 1~12 字", reply_markup=kb([[("⬅️ 返回", "a:sup:cats")]]))
            return
        if name in cats:
            await message.reply_text("❌ 该分类已存在", reply_markup=kb([[("⬅️ 返回", "a:sup:cats")]]))
            return
        if len(cats) >= 8:
            await message.reply_text("❌ 最多 8 个分类，请先删除不用的", reply_markup=kb([[("⬅️ 返回", "a:sup:cats")]]))
            return
        cats.append(name)
        db.update_settings(supply_categories=cats)
        audit(uid, "添加供需分类", name)
        t, keyboard = page_admin_supply_cats()
        await message.reply_text(f"✅ 已添加分类「{h(name)}」\n\n" + t, reply_markup=keyboard, parse_mode="HTML")
        return

    # ---------- 供需：设置频道 ----------
    if state == "sup_chid":
        clear_state(context)
        val = text.strip()
        if val in ("关闭", "clear", "off", "无"):
            db.update_settings(supply_channel_id="")
            audit(uid, "清空供需频道", "")
            t, keyboard = page_admin_supply()
            await message.reply_text("✅ 已清空供需频道配置（前台频道/双发选项已隐藏）\n\n" + t, reply_markup=keyboard, parse_mode="HTML")
            return
        if not (val.startswith("@") or val.startswith("-100")):
            await message.reply_text(
                "❌ 格式不对：请输入 @用户名 或 -100 开头的 chat_id，或发送「关闭」清空",
                reply_markup=kb([[("⬅️ 返回", "a:sup")]]),
            )
            return
        db.update_settings(supply_channel_id=val)
        audit(uid, "设置供需频道", val)
        t, keyboard = page_admin_supply()
        await message.reply_text(
            f"✅ 供需频道已设置为 <code>{h(val)}</code>\n\n"
            "⚠️ 请确认机器人已在该频道且为管理员，否则频道供需发送失败会自动退款\n\n" + t,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    # ---------- 供需：定价/防刷数值 ----------
    if state.startswith("sup_set:"):
        key = state.split(":", 1)[1]
        clear_state(context)
        try:
            val = int(text)
        except ValueError:
            await message.reply_text("❌ 请输入整数", reply_markup=kb([[("⬅️ 返回", "a:sup:price")]]))
            return
        minimum = 1 if key in ("supply_price_bot", "supply_price_channel", "supply_max_len") else 0
        if val < minimum:
            await message.reply_text(f"❌ 不能小于 {minimum}", reply_markup=kb([[("⬅️ 返回", "a:sup:price")]]))
            return
        db.update_settings(**{key: val})
        audit(uid, "改供需配置", f"{key} = {val}")
        t, keyboard = page_admin_supply_price()
        await message.reply_text("✅ 已更新\n\n" + t, reply_markup=keyboard, parse_mode="HTML")
        return

    # ---------- 广播（接收内容 → 确认） ----------
    if state.startswith("bc:"):
        target = state[3:]
        context.user_data["state"] = f"bc_confirm:{target}"
        context.user_data["payload"] = {
            "target": target,
            "src": (message.chat_id, message.message_id),
        }
        tname = {"all": "全员", "with_points": "有积分用户", "no_points": "无积分用户"}.get(target, target)
        await message.reply_text(
            f"📣 <b>广播内容已收到</b>\n\n目标：{tname}\n\n确认发送？",
            parse_mode="HTML",
            reply_markup=kb([[("✅ 确认发送", "a:bcsend")], [("❌ 取消", "a:bc")]]),
        )
        return

    if state.startswith("bc_confirm:"):
        await message.reply_text(
            "请点击上方「✅ 确认发送」或「❌ 取消」",
            reply_markup=kb([[("✅ 确认发送", "a:bcsend")], [("❌ 取消", "a:bc")]]),
        )
        return

    clear_state(context)
