"""管理端：上架向导 / 商品 / 订单 / 用户 / 设置

由 handlers_callback.py 拆分而来（v2.6.4）。分支逻辑与原实现逐字一致，
仅把裸 `return` 改为 `return True`（已处理），函数末尾补 `return False`。
"""
from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from .pages_admin import (
    page_admin_admins,
    page_admin_channels,
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
from .pages_supply import (
    page_admin_supply,
)
from .runtime import db, service
from .shop_service import GATEWAY_NAMES
from .ui import audit, h, kb, send_or_edit

logger = logging.getLogger(__name__)




async def _dispatch_admin(
    query, context: ContextTypes.DEFAULT_TYPE, uid: int, data: str, tg_user
) -> bool:
    """管理端：上架向导 / 商品 / 订单 / 用户 / 设置。返回 True 表示该命名空间已处理，False 表示未命中。"""
    # ---------- 上架向导：类型选择 ----------
    if data.startswith("padd_kind:"):
        if context.user_data.get("state") != "padd_kind":
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "a:prod")]]))
            return True
        kind = data.split(":", 1)[1]
        if kind not in ("product", "report"):
            return True
        context.user_data["payload"]["kind"] = kind
        if kind == "report":
            # 报单类：人工处理，没有发货内容概念，直接问库存
            context.user_data["state"] = "padd_stock"
            await send_or_edit(
                query,
                "➕ <b>上架商品</b>\n\n第 5 步：请输入库存数量\n\n💡 -1 表示无限库存；库存扣到 0 会自动标记售罄，退款会自动回补库存",
                kb([[("❌ 取消", "a:prod")]]),
            )
            return True
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
        return True

    if data.startswith("padd_mode:"):
        if context.user_data.get("state") != "padd_mode":
            await send_or_edit(query, "❌ 会话已过期，请重新开始", kb([[("⬅️ 返回", "a:prod")]]))
            return True
        mode = data.split(":", 1)[1]
        if mode not in ("fixed", "pool"):
            return True
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
            return True
        context.user_data["state"] = "padd_stock"
        await send_or_edit(
            query,
            "➕ <b>上架商品</b>（固定内容模式）\n\n第 6 步：请输入库存数量\n\n💡 -1 表示无限库存；库存扣到 0 会自动标记售罄，退款会自动回补库存",
            kb([[("❌ 取消", "a:prod")]]),
        )
        return True

    # ---------- 管理端（a: 前缀需管理员权限） ----------
    if data.startswith("a:") and not db.is_admin(uid):
        await send_or_edit(query, "❌ 无权限", kb([[("🏠 主菜单", "m:root")]]))
        return True

    # ----- 商品管理 -----
    if data == "a:prod":
        text, keyboard = page_admin_prod()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:padd":
        context.user_data["state"] = "padd_name"
        context.user_data["payload"] = {}
        await send_or_edit(
            query,
            "➕ <b>上架商品</b>\n\n第 1 步：请输入名称\n\n💡 依次设置名称、介绍、价格、类型、发货模式、库存/限购（固定模式商品多一步发货内容）",
            kb([[("❌ 取消", "a:prod")]]),
        )
        return True

    if data.startswith("a:pedit:"):
        text, keyboard = page_admin_pedit(data[8:])
        await send_or_edit(query, text, keyboard)
        return True

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
                return True
            buttons = [[("🚫 未分类", f"a:pcatsel:{pid}:")]]
            for i, c in enumerate(cats[:11]):
                buttons.append([(f"📂 {c}", f"a:pcatsel:{pid}:{i}")])
            buttons.append([("❌ 取消", f"a:pedit:{pid}")])
            await send_or_edit(query, "✏️ 请选择商品分类：", kb(buttons))
            return True
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
        return True

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
        return True

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
                    return True
                p.delivery_mode = "fixed"
                p.stock = 0
                p.status = "sold_out"
                db.save_product(p)
                audit(uid, "切换发货模式", f"{p.name} → 固定内容")
        text, keyboard = page_admin_pedit(pid)
        await send_or_edit(query, "✅ 发货模式已切换\n\n" + text, keyboard)
        return True

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
        return True

    if data.startswith("a:ptop:"):
        pid = data[7:]
        p = db.get_product(pid)
        if p:
            p.is_top = not p.is_top
            db.save_product(p)
            audit(uid, "置顶切换", f"{p.name} → {'置顶' if p.is_top else '取消置顶'}")
        text, keyboard = page_admin_pedit(pid)
        await send_or_edit(query, text, keyboard)
        return True

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
        return True

    if data.startswith("a:pdelok:"):
        pid = data[9:]
        p = db.get_product(pid)
        service.delete_product(pid)
        audit(uid, "删除商品", p.name if p else pid)
        text, keyboard = page_admin_prod()
        await send_or_edit(query, "🗑 已删除\n\n" + text, keyboard)
        return True

    if data.startswith("a:pdel:"):
        text, keyboard = page_admin_pdel_confirm(data[7:])
        await send_or_edit(query, text, keyboard)
        return True

    # ----- 商品分类 -----
    if data == "a:pcat":
        text, keyboard = page_admin_pcats()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:pcatadd":
        context.user_data["state"] = "pcatadd"
        await send_or_edit(
            query,
            "➕ 请输入新分类名（不超过 12 字，最多 12 个分类）：",
            kb([[("❌ 取消", "a:pcat")]]),
        )
        return True

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
        return True

    # ----- 订单管理 -----
    if data == "a:order":
        text, keyboard = page_admin_order("all")
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:of:"):
        text, keyboard = page_admin_order(data[5:])
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:osearch":
        context.user_data["state"] = "osearch"
        await send_or_edit(
            query,
            "🔍 请输入订单号 / 用户名 / 商品名关键词：",
            kb([[("❌ 取消", "a:order")]]),
        )
        return True

    if data.startswith("a:od:"):
        text, keyboard = page_admin_order_detail(data[5:])
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:oc:"):
        oid = data[5:]
        o = db.get_order(oid)
        if not o or o.status not in ("pending", "processing"):
            await send_or_edit(query, "❌ 订单不存在或已处理完毕", kb([[("⬅️ 返回", "a:order")]]))
            return True
        context.user_data["state"] = f"oc:{oid}"
        await send_or_edit(
            query,
            f"✅ 完成订单 <code>{oid}</code>\n\n"
            f"请发送回单内容（支持文字/图片/文件等任意类型，将原样发给用户）：",
            kb([[("❌ 取消", f"a:od:{oid}")]]),
        )
        return True

    if data.startswith("a:op:"):
        oid = data[5:]
        o = db.get_order(oid)
        if o and o.status == "pending":
            o.status = "processing"
            db.save_order(o)
            audit(uid, "标记处理中", oid)
        text, keyboard = page_admin_order_detail(oid)
        await send_or_edit(query, text, keyboard)
        return True

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
        return True

    if data.startswith("a:or:"):
        text, keyboard = page_admin_refund_confirm(data[5:])
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:ou:"):
        text, keyboard = page_admin_user_detail(int(data[5:]))
        await send_or_edit(query, text, keyboard)
        return True

    # ----- 用户管理 -----
    if data == "a:user":
        text, keyboard = page_admin_user()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:usearch":
        context.user_data["state"] = "usearch"
        await send_or_edit(
            query,
            "🔍 请输入要搜索的用户 ID / 用户名关键词：",
            kb([[("❌ 取消", "a:user")]]),
        )
        return True

    if data.startswith("a:ud:"):
        text, keyboard = page_admin_user_detail(int(data[5:]))
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:uadd:"):
        target = int(data[7:])
        context.user_data["state"] = f"uadd:{target}"
        await send_or_edit(query, "➕ 请输入要增加的积分数量：", kb([[("❌ 取消", f"a:ud:{target}")]]))
        return True

    if data.startswith("a:usub:"):
        target = int(data[7:])
        context.user_data["state"] = f"usub:{target}"
        await send_or_edit(query, "➖ 请输入要扣除的积分数量：", kb([[("❌ 取消", f"a:ud:{target}")]]))
        return True

    if data.startswith("a:uban:"):
        target = int(data[7:])
        u = db.get_user(target)
        if u:
            u.is_banned = not u.is_banned
            db.save_user(u)
            audit(uid, "封禁" if u.is_banned else "解封", f"@{u.username or target}")
        text, keyboard = page_admin_user_detail(target)
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:uo:"):
        text, keyboard = page_admin_user_orders(int(data[5:]))
        await send_or_edit(query, text, keyboard)
        return True

    if data.startswith("a:uph:"):
        text, keyboard = page_admin_user_points(int(data[6:]))
        await send_or_edit(query, text, keyboard)
        return True

    # ----- 管理员管理 -----
    if data == "a:adm":
        text, keyboard = page_admin_admins()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:admadd":
        context.user_data["state"] = "admadd"
        await send_or_edit(query, "➕ 请输入新管理员的 Telegram ID：", kb([[("❌ 取消", "a:adm")]]))
        return True

    if data.startswith("a:admdel:"):
        from .runtime import ADMIN_ID
        target = int(data[9:])
        if target != ADMIN_ID:
            db.delete_admin(target)
            audit(uid, "移除管理员", str(target))
        text, keyboard = page_admin_admins()
        await send_or_edit(query, text, keyboard)
        return True

    # ----- 频道管理 -----
    if data == "a:ch":
        text, keyboard = page_admin_channels()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:chadd":
        context.user_data["state"] = "chadd"
        await send_or_edit(
            query,
            "➕ 请输入频道用户名（如 @channelname）或 chat_id（如 -1001234567890）：\n\n"
            "⚠️ 请先把机器人设为该频道管理员，否则无法检测用户关注状态",
            kb([[("❌ 取消", "a:ch")]]),
        )
        return True

    if data.startswith("a:chdel:"):
        service.remove_channel(data[8:])
        audit(uid, "移除频道", data[8:])
        text, keyboard = page_admin_channels()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:chforce":
        s = db.get_settings()
        db.update_settings(force_channels=not s.get("force_channels", False))
        audit(uid, "切换强制关注", "开" if not s.get("force_channels", False) else "关")
        text, keyboard = page_admin_channels()
        await send_or_edit(query, text, keyboard)
        return True

    # ----- 系统设置 -----
    if data == "a:set":
        text, keyboard = page_admin_settings()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:sr":
        context.user_data["state"] = "sr"
        await send_or_edit(query, "💰 请输入积分比例（1 USDT 兑换多少积分）：", kb([[("❌ 取消", "a:set")]]))
        return True

    if data == "a:smin":
        context.user_data["state"] = "smin"
        await send_or_edit(query, "💵 请输入最低充值金额（USDT，数字）：", kb([[("❌ 取消", "a:set")]]))
        return True

    if data == "a:sw":
        context.user_data["state"] = "sw"
        await send_or_edit(
            query,
            "📝 发送文字设置欢迎语；发送图片（可带文字）同时设置欢迎图：",
            kb([[("❌ 取消", "a:set")]]),
        )
        return True

    if data == "a:sm":
        s = db.get_settings()
        db.update_settings(maintenance_mode=not s.get("maintenance_mode", False))
        audit(uid, "切换维护模式", "开" if not s.get("maintenance_mode", False) else "关")
        text, keyboard = page_admin_settings()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:sf":
        s = db.get_settings()
        db.update_settings(force_channels=not s.get("force_channels", False))
        audit(uid, "切换强制关注", "开" if not s.get("force_channels", False) else "关")
        text, keyboard = page_admin_settings()
        await send_or_edit(query, text, keyboard)
        return True

    # ----- 支付设置（v2.5.0）：渠道开关 / 限额 / 冷钱包地址与 Key -----
    if data == "a:pay":
        text, keyboard = page_admin_payment()
        await send_or_edit(query, text, keyboard)
        return True

    if data == "a:pay:limit":
        s = db.get_settings()
        context.user_data["state"] = "pay_limit"
        await send_or_edit(
            query,
            f"💵 当前限额：<code>{s.get('min_recharge_usdt', 1):g} ~ {s.get('max_recharge_usdt', 1000):g}</code> USDT\n\n"
            "请输入新限额，格式：<code>最低 最高</code>（如 <code>1 500</code>；最高填 0 表示不限）：",
            kb([[("❌ 取消", "a:pay")]]),
        )
        return True

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
            return True
        s = db.get_settings()
        cur = bool(s.get(key, gw in ("okpay", "kkpay")))
        if not cur:  # 开启前校验：冷钱包必须先配地址
            if gw == "tron" and not (s.get("tron_wallet_address") or ""):
                await send_or_edit(query, "⚠️ 请先设置 TRON 收款地址再开启", kb([
                    [("🏦 设置 TRON 收款地址", "a:pay:addr:tron")],
                    [("⬅️ 返回支付设置", "a:pay")],
                ]))
                return True
            if gw == "ton" and not (s.get("ton_wallet_address") or ""):
                await send_or_edit(query, "⚠️ 请先设置 TON 收款地址再开启", kb([
                    [("🏦 设置 TON 收款地址", "a:pay:addr:ton")],
                    [("⬅️ 返回支付设置", "a:pay")],
                ]))
                return True
        db.update_settings(**{key: not cur})
        audit(uid, "切换支付渠道", f"{GATEWAY_NAMES.get(gw, gw)} {'开' if not cur else '关'}")
        text, keyboard = page_admin_payment()
        await send_or_edit(query, text, keyboard)
        return True

    if data in ("a:pay:addr:tron", "a:pay:addr:ton"):
        chain = "trc20" if data.endswith(":tron") else "ton"
        context.user_data["state"] = f"pay_addr:{chain}"
        tip = ("🏦 请发送 TRON 收款地址（T 开头 34 位）：" if chain == "trc20"
               else "🏦 请发送 TON 收款地址（EQ/UQ 开头 48 位）：")
        await send_or_edit(query, tip, kb([[("❌ 取消", "a:pay")]]))
        return True

    if data in ("a:pay:key:tron", "a:pay:key:ton"):
        chain = "tron" if data.endswith(":tron") else "ton"
        context.user_data["state"] = f"pay_key:{chain}"
        name = "TronGrid" if chain == "tron" else "TonCenter"
        await send_or_edit(
            query,
            f"🔑 请发送 {name} API Key（可加快链上查询；发送 <code>0</code> 清除已设置的 Key）：",
            kb([[("❌ 取消", "a:pay")]]),
        )
        return True

    # ----- 供需管理 -----
    if data == "a:sup":
        text, keyboard = page_admin_supply()
        await send_or_edit(query, text, keyboard)
        return True
    return False
