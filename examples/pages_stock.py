"""库存池管理页面（v2.4.0）：池子总览 / 单品库存列表 / 单件详情 / 补货向导文案。

补货交互：进向导后「发一条 = 补一件」——文字、单图、文件各算一件；
同一条多图消息（图册）自动聚合成一件。随时可点「完成补货」退出。
"""

from __future__ import annotations

from telegram import InlineKeyboardMarkup

from .models import STOCK_STATUS, STOCK_TYPES, fmt_time
from .runtime import db
from .ui import h, kb

POOL_PAGE_SIZE = 10


def page_admin_pool() -> tuple[str, InlineKeyboardMarkup]:
    """库存池总览：所有池模式商品的现货/已售一览。"""
    products = [p for p in db.list_products(limit=200) if getattr(p, "delivery_mode", "fixed") == "pool"]
    stats = db.stock_pool_stats()
    lines = [
        "<b>📦 库存池</b>",
        "<i>一件一卖：文字/图片/文件/图册，付款即发、售完即止</i>",
        "",
        f"🗄 池模式商品：<code>{stats['pool_products']}</code> 个 · 现货 <code>{stats['available']}</code> 件 · 已售 <code>{stats['sold']}</code> 件",
        "",
    ]
    buttons = []
    if not products:
        lines.append("暂无池模式商品")
        lines.append("💡 上架商品时把「发货模式」选为库存池，或在商品编辑页切换")
    else:
        for p in products[:15]:
            avail = db.count_stock_items(p.id, "available")
            sold = db.count_stock_items(p.id, "sold")
            mark = "🟢" if avail > 0 else "🟡"
            lines.append(f"{mark} {h(p.name)}：现货 <code>{avail}</code> · 已售 <code>{sold}</code>")
            buttons.append([(f"📦 {h(p.name)}（{avail}）", f"a:pool:{p.id}")])
    buttons.append([("⬅️ 返回", "m:admin")])
    return "\n".join(lines), kb(buttons)


def page_admin_pool_prod(pid: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """单个商品的池子明细：现货列表（正序，先补的先卖）+ 操作区。"""
    p = db.get_product(pid)
    if not p:
        return "❌ 商品不存在", kb([[("⬅️ 返回", "a:pool")]])
    avail = db.count_stock_items(pid, "available")
    sold = db.count_stock_items(pid, "sold")
    items = db.list_stock_items(pid, status="available", limit=POOL_PAGE_SIZE, offset=page * POOL_PAGE_SIZE)

    lines = [
        f"<b>📦 库存池 · {h(p.name)}</b>",
        "",
        f"现货 <code>{avail}</code> 件 · 已售 <code>{sold}</code> 件 · 单价 <code>{p.points}</code> 积分",
        f"状态：{'🟢 在售' if p.status == 'on' else '🟡 已售罄' if p.status == 'sold_out' else p.status}",
        "",
    ]
    buttons = []
    if not items:
        lines.append("池子空了，点「➕ 补货」上货")
    else:
        lines.append("<b>现货（编号小的先卖）：</b>")
        for it in items:
            lines.append(f"#{it.id} {STOCK_TYPES.get(it.content_type, it.content_type)} · {h(it.summary())}")
            buttons.append([(f"🔍 #{it.id} {it.summary()[:16]}", f"a:stkitem:{it.id}")])
    nav = []
    if page > 0:
        nav.append(("⬅️ 上一页", f"a:poolpg:{pid}:{page - 1}"))
    if (page + 1) * POOL_PAGE_SIZE < avail:
        nav.append(("➡️ 下一页", f"a:poolpg:{pid}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("➕ 补货", f"a:stkadd:{pid}")])
    if sold:
        buttons.append([("🧹 清空已售记录", f"a:stkclear:{pid}")])
    buttons.append([("⚙️ 商品编辑", f"a:pedit:{pid}"), ("⬅️ 库存池", "a:pool")])
    return "\n".join(lines), kb(buttons)


def page_admin_pool_item(item_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """单件详情：可看内容、未售可删。"""
    it = db.get_stock_item(item_id)
    if not it:
        return "❌ 该件不存在", kb([[("⬅️ 返回", "a:pool")]])
    p = db.get_product(it.product_id)
    pname = h(p.name) if p else it.product_id
    lines = [
        f"<b>🔍 库存 #{it.id}</b>",
        "",
        f"商品：{pname}",
        f"类型：{STOCK_TYPES.get(it.content_type, it.content_type)}",
        f"状态：{STOCK_STATUS.get(it.status, it.status)}",
        f"入库：<code>{fmt_time(it.created_at)}</code>",
    ]
    if it.content_type == "text":
        lines += ["", f"<code>{h(it.content_text)}</code>"]
    else:
        if it.content_type == "album":
            lines.append(f"图片数：<code>{len(it.file_ids)}</code> 张")
        if it.caption:
            lines.append(f"说明：{h(it.caption)}")
        lines.append("<i>媒体内容请在售出订单/买家私聊中查看</i>")
    if it.status == "sold":
        lines += [
            "",
            f"订单：<code>{it.order_id}</code>",
            f"买家：<code>{it.buyer_id}</code>",
            f"售出：<code>{fmt_time(it.sold_at)}</code>",
        ]

    buttons = []
    if it.status == "available":
        buttons.append([("🗑 删除该件", f"a:stkdel:{it.id}")])
    buttons.append([("⬅️ 返回池子", f"a:pool:{it.product_id}")])
    return "\n".join(lines), kb(buttons)


def page_admin_pool_clear_confirm(pid: str) -> tuple[str, InlineKeyboardMarkup]:
    p = db.get_product(pid)
    if not p:
        return "❌ 商品不存在", kb([[("⬅️ 返回", "a:pool")]])
    sold = db.count_stock_items(pid, "sold")
    text = (
        f"🧹 <b>确认清空已售记录？</b>\n\n"
        f"商品：{h(p.name)}\n"
        f"将删除 <code>{sold}</code> 条已售出货记录\n\n"
        f"<i>只清记录、不动现货；订单里的购得编号会变成「记录已清理」，建议月底再清</i>"
    )
    return text, kb([
        [("✅ 确认清空", f"a:stkclearok:{pid}")],
        [("❌ 取消", f"a:pool:{pid}")],
    ])


def restock_prompt(pid: str, added: int = 0) -> str:
    """补货向导提示：发一条 = 补一件。added 为本次已入库件数（刷新提示用）。"""
    p = db.get_product(pid)
    pname = h(p.name) if p else pid
    avail = db.count_stock_items(pid, "available")
    lines = [
        f"<b>➕ 补货 · {pname}</b>",
        "",
        f"当前现货：<code>{avail}</code> 件" + (f" · 本次已入库 <code>{added}</code> 件" if added else ""),
        "",
        "✏️ <b>发一条 = 补一件</b>，支持：",
        "· 文字：直接发送，一条文字一件货",
        "· 图片：单张发送（可带说明文字），一张一件",
        "· 文件：单个发送（可带说明文字），一个一件",
        "· 图册：同一条消息发多张图，<b>自动合并为一件</b>",
        "",
        "⚡ 可以连续发，发多少入多少；点「✅ 完成补货」退出",
    ]
    return "\n".join(lines)


def restock_keyboard(pid: str) -> InlineKeyboardMarkup:
    return kb([
        [("✅ 完成补货", f"a:stkdone:{pid}")],
        [("📦 查看池子", f"a:pool:{pid}")],
    ])
