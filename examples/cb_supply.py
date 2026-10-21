"""付费供需：大厅 / 发布向导 / 管理端开关与审核

由 handlers_callback.py 拆分而来（v2.6.4）。分支逻辑与原实现逐字一致，
仅把裸 `return` 改为 `return True`（已处理），函数末尾补 `return False`。
"""
from __future__ import annotations

import asyncio
import logging

from telegram.ext import ContextTypes

from .models import SUPPLY_CHANNELS, now_iso
from .pages_admin import (
    page_admin_broadcast,
    page_admin_finance,
    page_admin_logs,
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
from .runtime import db, service, stock, supply
from .supply_service import CREATE_FAIL_REASONS
from .ui import audit, clear_state, h, kb, send_or_edit

logger = logging.getLogger(__name__)




async def _dispatch_supply(
    query, context: ContextTypes.DEFAULT_TYPE, uid: int, data: str, tg_user
) -> bool:
    """付费供需：大厅 / 发布向导 / 管理端开关与审核。返回 True 表示该命名空间已处理，False 表示未命中。"""
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
        return True

    if data == "sup:mine":
        clear_state(context)
        text, keyboard = page_supply_mine(uid)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "sup:new":
        # 发布向导第 1 步：选渠道。清空旧草稿重新开始。
        clear_state(context)
        text, keyboard = page_supply_new(uid)
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("sup:ch:"):
        channel = data[7:]
        if channel not in SUPPLY_CHANNELS or channel not in supply.available_channels():
            await send_or_edit(query, "❌ 渠道不可用，请重新选择", kb([[("⬅️ 返回", "sup:new")]]))
            return True
        context.user_data["payload"] = {
            "channel": channel, "category": "",
            "content": "", "photo_file_id": "", "anonymous": False,
        }
        if db.get_settings().get("supply_categories"):
            # 配了分类 → 第 2 步选分类（按钮流程，无输入状态）
            text, keyboard = page_supply_category(context.user_data["payload"])
            await send_or_edit(query, text, keyboard)
            return True
        # 没配分类 → 直接进内容输入
        context.user_data["state"] = "sup_text"
        await send_or_edit(
            query,
            supply_content_prompt(context.user_data["payload"]),
            kb([[("❌ 取消", "sup:cancel")]]),
        )
        return True

    if data.startswith("sup:cat:"):
        payload = context.user_data.get("payload")
        if not payload:
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "sup:new")]]))
            return True
        cats = db.get_settings().get("supply_categories") or []
        idx = data[8:]
        payload["category"] = cats[int(idx)] if idx.isdigit() and int(idx) < len(cats) else ""
        context.user_data["state"] = "sup_text"
        await send_or_edit(query, supply_content_prompt(payload), kb([[("❌ 取消", "sup:cancel")]]))
        return True

    if data == "sup:nophoto":
        # 跳过附图 → 直接预览
        payload = context.user_data.get("payload")
        if context.user_data.get("state") != "sup_photo" or not payload:
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "sup:new")]]))
            return True
        payload["photo_file_id"] = ""
        context.user_data["state"] = "sup_preview"
        user = db.get_user(uid)
        text, keyboard = page_supply_preview(payload, user)
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("sup:id:"):
        # 预览页切换 实名/匿名（「允许匿名」开关关闭时此按钮不会出现，双保险再拦一次）
        if not supply.anonymous_allowed():
            await send_or_edit(query, "❌ 当前不允许匿名发布", kb([[("⬅️ 返回", "sup:new")]]))
            return True
        payload = context.user_data.get("payload")
        if context.user_data.get("state") != "sup_preview" or not payload:
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "sup:new")]]))
            return True
        payload["anonymous"] = data[7:] == "1"
        user = db.get_user(uid)
        text, keyboard = page_supply_preview(payload, user)
        await send_or_edit(query, text, keyboard)
        return True

    if data == "sup:go":
        # 确认发布：原子扣费落库 → 异步分发
        payload = context.user_data.get("payload")
        if context.user_data.get("state") != "sup_preview" or not payload or not payload.get("content"):
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "sup:new")]]))
            return True
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
            return True
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
            return True
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
        return True

    if data == "sup:cancel":
        clear_state(context)
        text, keyboard = page_supply_hall(uid)
        await send_or_edit(query, "已取消发布\n\n" + text, keyboard)
        return True

    # ---------- 供需开关面板：每个开关独立翻转，立即生效 ----------
    if data == "a:sup:sw":
        text, keyboard = page_admin_supply_sw()
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:sup:sw:t:"):
        key = data[len("a:sup:sw:t:"):]
        valid_keys = {k for k, _, _ in SUPPLY_SWITCHES}
        if key in valid_keys:
            cur = bool(db.get_settings().get(key, SUPPLY_SWITCH_ON[key]))
            db.update_settings(**{key: not cur})
            audit(uid, "供需开关", f"{key} → {'开' if not cur else '关'}")
        text, keyboard = page_admin_supply_sw()
        await send_or_edit(query, text, keyboard)
        return True

    # ---------- 供需审核队列与审核动作 ----------
    if data == "a:sup:rv":
        text, keyboard = page_admin_supply_review()
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:sup:ok:"):
        pid = data[len("a:sup:ok:"):]
        post = supply.approve_post(pid, operator_id=uid)
        if not post:
            await send_or_edit(query, "❌ 帖子不存在或已处理", kb([[("🕐 审核队列", "a:sup:rv")]]))
            return True
        audit(uid, "供需审核通过", pid)
        asyncio.create_task(supply.distribute(post))
        asyncio.create_task(supply.notify_user_approved(post))
        text, keyboard = page_admin_supply_post(pid)
        await send_or_edit(query, f"✅ 帖子 <code>{pid}</code> 已通过审核，正在分发\n\n" + text, keyboard)
        return True

    if data.startswith("a:sup:no:"):
        pid = data[len("a:sup:no:"):]
        post = supply.reject_post(pid, operator_id=uid)
        if not post:
            await send_or_edit(query, "❌ 帖子不存在或已处理", kb([[("🕐 审核队列", "a:sup:rv")]]))
            return True
        audit(uid, "供需审核拒绝", pid)
        asyncio.create_task(supply.notify_user_rejected(post))
        text, keyboard = page_admin_supply_post(pid)
        await send_or_edit(
            query,
            f"❌ 帖子 <code>{pid}</code> 已拒绝，<code>{post.cost}</code> 积分已退还发布人\n\n" + text,
            keyboard,
        )
        return True

    if data == "a:sup:posts" or data.startswith("a:sup:posts:"):
        page = 0
        if data.startswith("a:sup:posts:") and data[12:].isdigit():
            page = int(data[12:])
        text, keyboard = page_admin_supply_posts(page)
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:sup:pd:"):
        text, keyboard = page_admin_supply_post(data[9:])
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:sup:pdel:"):
        text, keyboard = page_admin_supply_pdel(data[11:], refund=False)
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:sup:pdelr:"):
        text, keyboard = page_admin_supply_pdel(data[12:], refund=True)
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:sup:pdok:"):
        parts = data.split(":")
        pid, refund = parts[3], len(parts) > 4 and parts[4] == "1"
        post = supply.delete_post(pid, operator_id=uid, refund=refund)
        if not post:
            await send_or_edit(query, "❌ 帖子不存在或已删除", kb([[("⬅️ 帖子管理", "a:sup:posts")]]))
            return True
        audit(uid, "删除供需帖", f"{pid}{'（并退款）' if refund else ''}")
        text = f"✅ 帖子 <code>{pid}</code> 已删除"
        if refund:
            text += f"，已退 <code>{post.cost}</code> 积分给发布人"
        await send_or_edit(query, text, kb([
            [("⬅️ 帖子管理", "a:sup:posts"), ("📢 供需管理", "a:sup")],
        ]))
        return True

    if data == "a:sup:cats":
        text, keyboard = page_admin_supply_cats()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:sup:catadd":
        context.user_data["state"] = "sup_cat_add"
        await send_or_edit(
            query,
            "➕ 请输入新分类名（不超过 12 字，最多 8 个分类）：",
            kb([[("❌ 取消", "a:sup:cats")]]),
        )
        return True

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
        return True

    if data == "a:sup:price":
        text, keyboard = page_admin_supply_price()
        await send_or_edit(query, text, keyboard)
        return True

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
            return True
        context.user_data["state"] = f"sup_set:{key}"
        await send_or_edit(query, prompts[key], kb([[("❌ 取消", "a:sup:price")]]))
        return True

    if data == "a:sup:chid":
        context.user_data["state"] = "sup_chid"
        await send_or_edit(
            query,
            "📣 请输入供需频道的 @用户名 或 chat_id（如 -1001234567890）\n\n"
            "发送 <code>关闭</code> 可清空配置（前台将隐藏频道/双发选项）\n\n"
            "⚠️ 请先把机器人拉进该频道并设为管理员，否则频道供需会发送失败",
            kb([[("❌ 取消", "a:sup")]]),
        )
        return True

    # ----- 库存池管理 -----
    if data == "a:pool":
        text, keyboard = page_admin_pool()
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:poolpg:"):
        parts = data.split(":")
        pid, page = parts[2], int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        text, keyboard = page_admin_pool_prod(pid, page)
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:pool:"):
        text, keyboard = page_admin_pool_prod(data[7:])
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:stkitem:"):
        text, keyboard = page_admin_pool_item(int(data[10:]))
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:stkdel:"):
        item_id = int(data[9:])
        ok, info = stock.remove_item(item_id, uid)
        if ok:
            audit(uid, "删除库存", f"#{item_id}")
            text, keyboard = page_admin_pool_prod(info)
            await send_or_edit(query, f"🗑 已删除 #{item_id}\n\n" + text, keyboard)
        else:
            await send_or_edit(query, f"❌ {info}", kb([[("⬅️ 返回", "a:pool")]]))
        return True

    if data.startswith("a:stkclearok:"):
        pid = data[13:]
        n = stock.clear_sold(pid)
        audit(uid, "清空已售记录", f"{pid} · {n} 条")
        text, keyboard = page_admin_pool_prod(pid)
        await send_or_edit(query, f"🧹 已清空 <code>{n}</code> 条已售记录\n\n" + text, keyboard)
        return True

    if data.startswith("a:stkclear:"):
        text, keyboard = page_admin_pool_clear_confirm(data[11:])
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:stkadd:"):
        pid = data[9:]
        p = db.get_product(pid)
        if not p or getattr(p, "delivery_mode", "fixed") != "pool":
            await send_or_edit(query, "❌ 该商品不是库存池模式", kb([[("⬅️ 返回", "a:prod")]]))
            return True
        context.user_data["state"] = f"restock:{pid}"
        context.user_data["payload"] = {"added": 0, "album": None}
        await send_or_edit(query, restock_prompt(pid), restock_keyboard(pid))
        return True

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
        return True

    # ----- 财务 / 日志 / 备份 / 广播 -----
    if data == "a:fin":
        text, keyboard = page_admin_finance()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:logs" or data.startswith("a:logs:"):
        page = int(data[7:]) if data.startswith("a:logs:") and data[7:].isdigit() else 0
        text, keyboard = page_admin_logs(page)
        await send_or_edit(query, text, keyboard)
        return True

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
        return True

    if data == "a:bc":
        text, keyboard = page_admin_broadcast()
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:bct:"):
        target = data[6:]
        context.user_data["state"] = f"bc:{target}"
        tname = {"all": "全员", "with_points": "有积分用户", "no_points": "无积分用户"}.get(target, target)
        await send_or_edit(
            query,
            f"📣 广播目标：<b>{tname}</b>\n\n请发送要广播的内容（文字/图片/视频/文件均可）：",
            kb([[("❌ 取消", "a:bc")]]),
        )
        return True

    if data == "a:bcsend":
        payload = context.user_data.get("payload") or {}
        target = payload.get("target", "all")
        src = payload.get("src")
        clear_state(context)
        if not src:
            await send_or_edit(query, "❌ 会话已过期，请重新发起广播", kb([[("⬅️ 返回", "a:bc")]]))
            return True
        await send_or_edit(query, "📣 开始广播，请稍候…", kb([[("🏠 主菜单", "m:root")]]))
        result = await service.broadcast_message("", target=target, copy_from=src)
        audit(uid, "广播", f"目标 {target} · 成功 {result['success']} · 失败 {result['failed']}")
        await query.message.reply_text(
            f"📣 <b>广播完成</b>\n\n✅ 成功：<code>{result['success']}</code>\n❌ 失败：<code>{result['failed']}</code>",
            parse_mode="HTML",
            reply_markup=kb([[("⬅️ 返回", "m:admin")]]),
        )
        return True
    return False
