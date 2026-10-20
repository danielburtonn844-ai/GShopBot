"""管理端页面（v2.4.0 模块化拆分）：面板/商品/订单/用户/管理员/频道/设置/财务/广播/分类/日志。

v2.4.0 变更：
- 管理面板新增「库存池」「操作日志」入口，面板数据接入池统计
- 商品编辑页池模式专属操作（补货/看池子/切换发货模式），隐藏不适用的固定库存编辑
- 商品分类管理（前台列表筛选条的数据源）
- 订单管理支持搜索（订单号/用户名/商品名）
- 财务概览接入库存池现货/已售统计
"""

from __future__ import annotations

from telegram import InlineKeyboardMarkup

from .models import PRODUCT_KINDS, TAGS, fmt_time
from .runtime import ADMIN_ID, db, service
from .ui import h, kb, order_status_text, product_status_text, tags_text


def _pool_badge(p) -> str:
    """商品列表/编辑页共用的模式徽标：池模式显示实时件数。"""
    if getattr(p, "delivery_mode", "fixed") == "pool":
        return f"📦池 {db.count_stock_items(p.id, 'available')} 件"
    return f"库存 {'∞' if p.stock == -1 else p.stock}"


def _grid(items: list[tuple[str, str]], per_row: int = 3) -> list[list[tuple[str, str]]]:
    """按钮网格化：一行 per_row 个，整齐不竖条（v2.6.1）。"""
    return [items[i:i + per_row] for i in range(0, len(items), per_row)]


def page_admin(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    if not db.is_admin(user_id):
        return "❌ 无权限", kb([[("🏠 主菜单", "m:root")]])

    pending = db.count_orders(status="pending")
    processing = db.count_orders(status="processing")
    stats = service.get_finance_stats()
    review = db.count_supply_posts(status="pending_review")
    lines = [
        "<b>🔧 管理面板</b>",
        "<i>全店经营数据一览，点击按钮进入对应管理模块</i>",
        "",
        f"⏳ 待处理订单：<code>{pending}</code> · 🔄 处理中：<code>{processing}</code>",
        f"📅 今日订单：<code>{stats['today_orders']}</code> · 今日充值：<code>{stats['today_recharge']:.2f}</code> USDT",
        f"👤 用户总数：<code>{stats['total_users']}</code> · 今日新增：<code>{stats['today_users']}</code>",
        f"📦 库存池：<code>{stats['available']}</code> 件现货 · 已售 <code>{stats['sold']}</code> 件",
    ]
    if review:
        lines.append(f"🕐 供需待审核：<code>{review}</code> 条")
    # 抢单模块概览（v2.6.0）：有待抢/进行中/待审事项时高亮提醒
    gst = db.grab_stats()
    if db.get_settings().get("grab_enabled"):
        lines.append(
            f"⚡ 抢单：待抢 <code>{gst['waiting']}</code> · 进行中 <code>{gst['grabbed']}</code>"
            f" · 今日完单 <code>{gst['done_today']}</code>"
        )
        todo = []
        if gst["wd_pending"]:
            todo.append(f"提现待审 <code>{gst['wd_pending']}</code>")
        if gst["appeal_pending"]:
            todo.append(f"申诉待裁 <code>{gst['appeal_pending']}</code>")
        if todo:
            lines.append("🔔 " + " · ".join(todo))
    buttons = _grid([
        ("⚡ 接单管理", "a:grab"), ("📦 商品管理", "a:prod"), ("📋 订单管理", "a:order"),
        ("📦 库存池", "a:pool"), ("📢 供需管理", "a:sup"), ("👤 用户管理", "a:user"),
        ("🛡 管理员管理", "a:adm"), ("📢 频道管理", "a:ch"), ("📣 广播消息", "a:bc"),
        ("📊 财务概览", "a:fin"), ("📜 操作日志", "a:logs"), ("⚙️ 系统设置", "a:set"),
        ("💾 数据备份", "a:backup"),
    ], 3)
    buttons.append([("🏠 主菜单", "m:root")])
    return "\n".join(lines), kb(buttons)


# ==================================================================
# 商品管理
# ==================================================================

def page_admin_prod() -> tuple[str, InlineKeyboardMarkup]:
    products = db.list_products(limit=50)
    lines = [
        "<b>📦 商品管理</b>",
        "<i>点击商品可编辑价格/库存/限购/分类/发货内容、切换状态与标签；📦池 = 库存池模式</i>",
        "",
    ]
    buttons = []
    if not products:
        lines.append("暂无商品，点击下方上架")
    else:
        for p in products[:15]:
            cat = f"【{h(p.category)}】" if p.category else ""
            lines.append(
                f"{product_status_text(p.status)} {cat}{h(p.name)}  <code>{p.points}</code> 积分  {_pool_badge(p)}"
            )
            buttons.append([(f"⚙️ {h(p.name)}", f"a:pedit:{p.id}")])
    buttons.append([("➕ 上架商品", "a:padd"), ("📂 商品分类", "a:pcat")])
    buttons.append([("📦 库存池", "a:pool"), ("⬅️ 返回", "m:admin")])
    return "\n".join(lines), kb(buttons)


def page_admin_pedit(pid: str) -> tuple[str, InlineKeyboardMarkup]:
    p = db.get_product(pid)
    if not p:
        return "❌ 商品不存在", kb([[("⬅️ 返回", "a:prod")]])

    is_pool = getattr(p, "delivery_mode", "fixed") == "pool"
    stock_text = (
        f"{db.count_stock_items(p.id, 'available')} 件（库存池）"
        if is_pool else ("无限" if p.stock == -1 else str(p.stock))
    )
    limit_text = "不限" if p.limit_per_user == 0 else f"每人限 {p.limit_per_user}"
    lines = [
        f"<b>⚙️ 编辑：{h(p.name)}</b>",
        "",
        f"ID：<code>{p.id}</code>",
        f"类型：{PRODUCT_KINDS.get(p.kind, p.kind)}",
        f"发货模式：{'📦 库存池（一单一货）' if is_pool else '🔁 固定内容'}",
        f"分类：{h(p.category) if p.category else '未分类'}",
        f"价格：<code>{p.points}</code> 积分",
        f"库存：{stock_text}",
        f"限购：{limit_text}",
        f"状态：{product_status_text(p.status)}",
        f"置顶：{'✅ 是' if p.is_top else '❌ 否'}",
        f"标签：{tags_text(p.tags) or '无'}",
    ]
    if p.description:
        lines += ["", f"介绍：<i>{h(p.description[:100])}</i>"]
    if is_pool:
        sold = db.count_stock_items(p.id, "sold")
        lines += ["", f"📦 池子：现货 <code>{db.count_stock_items(p.id, 'available')}</code> 件 · 已售 <code>{sold}</code> 件"]
    elif p.auto_delivery:
        lines += ["", f"发货内容：<code>{h(p.auto_delivery[:60])}</code>"]

    tag_buttons = []
    for key, label in TAGS.items():
        mark = "✅" if key in (p.tags or []) else "☐"
        tag_buttons.append((f"{mark} {label}", f"a:ptag:{pid}:{key}"))

    buttons = [
        [("✏️ 改名称", f"a:pf:{pid}:name"), ("✏️ 改介绍", f"a:pf:{pid}:description")],
        [("✏️ 改价格", f"a:pf:{pid}:points"), ("✏️ 改限购", f"a:pf:{pid}:limit_per_user")],
        [("✏️ 改分类", f"a:pf:{pid}:category")],
    ]
    if is_pool:
        # 池模式：补货/池子管理替代固定库存与发货内容编辑
        buttons.append([("➕ 补货", f"a:stkadd:{pid}"), ("📦 库存池", f"a:pool:{pid}")])
    else:
        buttons.append([("✏️ 改库存", f"a:pf:{pid}:stock"), ("✏️ 改发货内容", f"a:pf:{pid}:auto_delivery")])
    buttons += [
        [("🔁 切换发货模式", f"a:pmode:{pid}")],
        [("🔄 切换状态", f"a:ptoggle:{pid}"), ("📌 置顶切换", f"a:ptop:{pid}")],
        tag_buttons[:2],
        tag_buttons[2:],
        [("🗑 删除商品", f"a:pdel:{pid}")],
        [("⬅️ 返回", "a:prod")],
    ]
    return "\n".join(lines), kb(buttons)


def page_admin_pdel_confirm(pid: str) -> tuple[str, InlineKeyboardMarkup]:
    p = db.get_product(pid)
    if not p:
        return "❌ 商品不存在", kb([[("⬅️ 返回", "a:prod")]])
    pool_warn = ""
    if getattr(p, "delivery_mode", "fixed") == "pool":
        left = db.count_stock_items(p.id, "available")
        if left:
            pool_warn = f"\n⚠️ 库存池内还有 <code>{left}</code> 件现货，删除后一并清空"
    text = (
        f"🗑 <b>确认删除商品？</b>\n\n"
        f"名称：{h(p.name)}\n"
        f"价格：<code>{p.points}</code> 积分\n\n"
        f"⚠️ 删除后用户将无法再看到该商品{pool_warn}"
    )
    buttons = [
        [("✅ 确认删除", f"a:pdelok:{pid}")],
        [("❌ 取消", f"a:pedit:{pid}")],
    ]
    return text, kb(buttons)


def page_admin_pcats() -> tuple[str, InlineKeyboardMarkup]:
    """商品分类管理（v2.4.0）：前台商品/报单列表顶部的筛选条数据源。"""
    cats = db.get_settings().get("product_categories") or []
    lines = [
        "<b>📂 商品分类管理</b>",
        "<i>配置后，商品/报单列表顶部出现分类筛选条，上架与编辑时可给商品归类</i>",
        "",
    ]
    buttons = []
    if not cats:
        lines.append("暂无分类（前台不显示筛选条）")
    else:
        in_use = {p.category for p in db.list_products(limit=500) if p.category}
        for i, c in enumerate(cats):
            used = f"（使用中 {sum(1 for p in db.list_products(limit=500) if p.category == c)}）" if c in in_use else ""
            lines.append(f"{i + 1}. {h(c)}{used}")
            buttons.append([(f"🗑 删除「{h(c)}」", f"a:pcatdel:{i}")])
    buttons.append([("➕ 添加分类", "a:pcatadd")])
    buttons.append([("⬅️ 商品管理", "a:prod")])
    return "\n".join(lines), kb(buttons)


# ==================================================================
# 订单管理
# ==================================================================

def page_admin_order(filter_status: str = "all") -> tuple[str, InlineKeyboardMarkup]:
    orders = db.list_orders(limit=200)
    if filter_status != "all":
        orders = [o for o in orders if o.status == filter_status]

    fname = {"all": "全部", "pending": "待处理", "processing": "处理中",
             "completed": "已完成", "refunded": "已退款"}.get(filter_status, "全部")
    lines = [f"<b>📋 订单管理</b>（{fname}，共 {len(orders)} 条）", ""]
    buttons = []
    if not orders:
        lines.append("暂无订单")
    else:
        for o in orders[:15]:
            pool_mark = "📦 " if getattr(o, "stock_item_id", 0) else ""
            lines.append(
                f"{order_status_text(o.status)} {pool_mark}<code>{o.id}</code> {h(o.product_name)} @{h(o.username or '-')}"
            )
            buttons.append([(f"📄 {o.id}", f"a:od:{o.id}")])
        if len(orders) > 15:
            lines.append(f"\n<i>仅显示最近 15 条，共 {len(orders)} 条</i>")

    buttons.append([("⏳ 待处理", "a:of:pending"), ("🔄 处理中", "a:of:processing")])
    buttons.append([("✅ 已完成", "a:of:completed"), ("💰 已退款", "a:of:refunded")])
    buttons.append([("🔵 全部", "a:of:all"), ("🔍 搜索订单", "a:osearch")])
    buttons.append([("⬅️ 返回", "m:admin")])
    return "\n".join(lines), kb(buttons)


def page_admin_order_detail(oid: str) -> tuple[str, InlineKeyboardMarkup]:
    o = db.get_order(oid)
    if not o:
        return "❌ 订单不存在", kb([[("⬅️ 返回", "a:order")]])

    lines = [
        "<b>📄 订单详情</b>",
        "",
        f"订单号：<code>{o.id}</code>",
        f"用户：@{h(o.username or '无')}（<code>{o.user_id}</code>）",
        f"商品：{h(o.product_name)}",
        f"类型：{PRODUCT_KINDS.get(o.product_kind, o.product_kind)}",
        f"积分：<code>{o.points}</code>",
        f"状态：{order_status_text(o.status)}",
        f"时间：<code>{fmt_time(o.created_at)}</code>",
    ]
    if o.completed_at:
        lines.append(f"完成：<code>{fmt_time(o.completed_at)}</code>")
    if o.note:
        lines += ["", f"📎 用户提交的信息：\n{h(o.note)}"]
    if o.reply_content:
        lines += ["", f"📝 回单：\n{h(o.reply_content)}"]
    if o.reply_admin_id:
        lines.append(f"处理人：<code>{o.reply_admin_id}</code>")

    # 库存池订单：显示发出的那件货及其编号，可跳转查看
    if getattr(o, "stock_item_id", 0):
        item = db.get_stock_item(o.stock_item_id)
        if item:
            lines += ["", f"📦 发出货物：<b>编号 #{item.id}</b>（{item.summary()}）"]
            buttons_item = [("📦 查看该件", f"a:stkitem:{item.id}")]
        else:
            lines += ["", "📦 发出货物：记录已清理"]
            buttons_item = []
    else:
        buttons_item = []

    buttons = []
    if o.status in ("pending", "processing"):
        buttons.append([("✅ 完成订单（回单）", f"a:oc:{oid}")])
        row = []
        if o.status == "pending":
            row.append(("🔄 标记处理中", f"a:op:{oid}"))
        row.append(("💰 退款关闭", f"a:or:{oid}"))
        buttons.append(row)
    if buttons_item:
        buttons.append(buttons_item)
    buttons.append([("👤 查看用户", f"a:ou:{o.user_id}")])
    buttons.append([("⬅️ 返回", "a:order")])
    return "\n".join(lines), kb(buttons)


def page_admin_refund_confirm(oid: str) -> tuple[str, InlineKeyboardMarkup]:
    o = db.get_order(oid)
    if not o:
        return "❌ 订单不存在", kb([[("⬅️ 返回", "a:order")]])
    pool_note = "\n📦 该单为库存池订单，退款后货物自动退回池中重新可售" if getattr(o, "stock_item_id", 0) else ""
    text = (
        f"💰 <b>确认退款？</b>\n\n"
        f"订单号：<code>{o.id}</code>\n"
        f"用户：@{h(o.username or '无')}\n"
        f"将退回积分：<code>{o.points}</code>\n\n"
        f"⚠️ 退款后订单关闭，积分退回用户账户{pool_note}"
    )
    buttons = [
        [("✅ 确认退款", f"a:orok:{oid}")],
        [("❌ 取消", f"a:od:{oid}")],
    ]
    return text, kb(buttons)


# ==================================================================
# 用户 / 管理员 / 频道
# ==================================================================

def page_admin_user() -> tuple[str, InlineKeyboardMarkup]:
    users = db.list_users(limit=50)
    lines = [f"<b>👤 用户管理</b>（共 {db.count_users()} 人）", ""]
    buttons = []
    if not users:
        lines.append("暂无用户")
    else:
        for u in users[:15]:
            ban = "🚫" if u.is_banned else "👤"
            lines.append(f"{ban} @{h(u.username or str(u.user_id))}  <code>{u.points}</code> 积分")
            buttons.append([(f"⚙️ @{h(u.username or str(u.user_id))}", f"a:ud:{u.user_id}")])
    buttons.append([("🔍 搜索用户", "a:usearch")])
    buttons.append([("⬅️ 返回", "m:admin")])
    return "\n".join(lines), kb(buttons)


def page_admin_user_detail(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    u = db.get_user(uid)
    if not u:
        return "❌ 用户不存在", kb([[("⬅️ 返回", "a:user")]])

    order_cnt = len(db.list_orders(user_id=uid, limit=1000))
    lines = [
        "<b>👤 用户详情</b>",
        "",
        f"ID：<code>{u.user_id}</code>",
        f"用户名：@{h(u.username or '无')}",
        f"昵称：{h((u.first_name + ' ' + u.last_name).strip() or '无')}",
        f"积分：<code>{u.points}</code>",
        f"累计充值：<code>{u.total_recharge:.2f}</code> USDT",
        f"累计消费：<code>{u.total_spent}</code> 积分",
        f"订单数：<code>{order_cnt}</code>",
        f"状态：{'🚫 已封禁' if u.is_banned else '✅ 正常'}",
        f"注册：<code>{fmt_time(u.created_at)}</code>",
        f"活跃：<code>{fmt_time(u.last_active)}</code>",
    ]
    buttons = [
        [("➕ 加积分", f"a:uadd:{uid}"), ("➖ 扣积分", f"a:usub:{uid}")],
        [("📜 积分记录", f"a:uph:{uid}"), ("📋 该用户订单", f"a:uo:{uid}")],
        [("🔓 解封" if u.is_banned else "🚫 封禁", f"a:uban:{uid}")],
        [("⬅️ 返回", "a:user")],
    ]
    return "\n".join(lines), kb(buttons)


def page_admin_user_points(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    """积分流水页：该用户最近 10 笔积分变动，笔笔可对账。"""
    u = db.get_user(uid)
    if not u:
        return "❌ 用户不存在", kb([[("⬅️ 返回", "a:user")]])

    records = db.list_point_records(uid, limit=10)
    name = h(u.username or str(uid))
    lines = [
        f"<b>📜 积分记录</b> @{name}",
        "",
        f"当前积分：<code>{u.points}</code>",
        "",
    ]
    if not records:
        lines.append("暂无积分变动记录")
    else:
        for r in records:
            sign = "+" if r.change >= 0 else ""
            op = f"（操作人 <code>{r.operator_id}</code>）" if r.operator_id else ""
            lines.append(
                f"<code>{sign}{r.change}</code> → 余 <code>{r.balance_after}</code>  "
                f"{h(r.reason)}{op}\n<code>{fmt_time(r.created_at)}</code>"
            )
        lines.append("")
        lines.append("<i>仅显示最近 10 笔</i>")
    return "\n".join(lines), kb([[("⬅️ 返回用户", f"a:ud:{uid}")]])


def page_admin_user_orders(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    u = db.get_user(uid)
    orders = db.list_orders(user_id=uid, limit=15)
    name = h(u.username or str(uid)) if u else str(uid)
    lines = [f"<b>📋 用户 @{name} 的订单</b>", ""]
    buttons = []
    if not orders:
        lines.append("暂无订单")
    else:
        for o in orders:
            lines.append(f"{order_status_text(o.status)} <code>{o.id}</code> {h(o.product_name)}")
            buttons.append([(f"📄 {o.id}", f"a:od:{o.id}")])
    buttons.append([("⬅️ 返回用户", f"a:ud:{uid}")])
    return "\n".join(lines), kb(buttons)


def page_admin_admins() -> tuple[str, InlineKeyboardMarkup]:
    admins = db.list_admins()
    lines = ["<b>🛡 管理员管理</b>", ""]
    buttons = []
    for a in admins:
        crown = "👑 " if a.user_id == ADMIN_ID else ""
        lines.append(f"🛡 {crown}{h(a.name or str(a.user_id))}（<code>{a.user_id}</code>）")
        if a.user_id != ADMIN_ID:
            buttons.append([(f"🗑 移除 {h(a.name or str(a.user_id))}", f"a:admdel:{a.user_id}")])
    buttons.append([("➕ 添加管理员", "a:admadd")])
    buttons.append([("⬅️ 返回", "m:admin")])
    return "\n".join(lines), kb(buttons)


def page_admin_channels() -> tuple[str, InlineKeyboardMarkup]:
    channels = db.list_channels()
    settings = db.get_settings()
    force = settings.get("force_channels", False)

    lines = [
        "<b>📢 频道管理</b>",
        "",
        f"强制关注：{'🟢 已开启' if force else '🔴 已关闭'}",
        "<i>开启后，用户必须关注下列全部频道才能使用机器人；机器人需为频道管理员才能检测</i>",
        "",
    ]
    buttons = []
    if not channels:
        lines.append("暂无频道")
    else:
        for ch in channels:
            lines.append(f"📢 {h(ch.title)}（<code>{h(ch.chat_id)}</code>）")
            buttons.append([(f"🗑 移除 {h(ch.title)}", f"a:chdel:{ch.id}")])
    buttons += _grid([
        ("➕ 添加频道", "a:chadd"),
        ("🔄 切换强制关注", "a:chforce"),
        ("⬅️ 返回", "m:admin"),
    ], 3)
    return "\n".join(lines), kb(buttons)


# ==================================================================
# 设置 / 财务 / 广播 / 日志
# ==================================================================

def page_admin_settings() -> tuple[str, InlineKeyboardMarkup]:
    s = db.get_settings()
    lines = [
        "<b>⚙️ 系统设置</b>",
        "",
        f"💰 积分比例：<code>1 USDT = {s.get('points_rate', 10)} 积分</code>",
        f"💵 充值限额：<code>{s.get('min_recharge_usdt', 1):g} ~ {s.get('max_recharge_usdt', 1000):g}</code> USDT",
        f"🔧 维护模式：{'🟢 开启（禁止下单）' if s.get('maintenance_mode') else '🔴 关闭'}",
        f"📢 强制关注：{'🟢 开启' if s.get('force_channels') else '🔴 关闭'}",
        f"👋 欢迎语：{h((s.get('welcome_text', '') or '')[:30])}",
        f"🖼 欢迎图：{'✅ 已设置' if s.get('welcome_image') else '❌ 未设置'}",
    ]
    buttons = _grid([
        ("💳 支付设置", "a:pay"), ("💰 积分比例", "a:sr"), ("💵 最低充值", "a:smin"),
        ("📝 欢迎语/图", "a:sw"), ("🔧 维护模式", "a:sm"), ("📢 强制关注", "a:sf"),
    ], 3)
    buttons.append([("⬅️ 返回", "m:admin")])
    return "\n".join(lines), kb(buttons)


def page_admin_payment() -> tuple[str, InlineKeyboardMarkup]:
    """支付设置面板（v2.5.0）：渠道开关 / 单笔限额 / 冷钱包收款地址与 API Key。

    在线渠道（OkPay/KKPay）的商户配置仍在 .env，这里只控制开关；
    冷钱包渠道的收款地址与 API Key 全部在这里设置，改完即时生效。
    """
    s = db.get_settings()

    def _on(key: str, default: bool = False) -> str:
        return "🟢 开启" if s.get(key, default) else "🔴 关闭"

    min_v = s.get("min_recharge_usdt", 1)
    max_v = s.get("max_recharge_usdt", 1000)
    tron_addr = s.get("tron_wallet_address", "") or ""
    ton_addr = s.get("ton_wallet_address", "") or ""
    ok_cfg = bool(service.okpay_id and service.okpay_token)
    kk_cfg = bool(service.kkpay_id and service.kkpay_token)

    lines = [
        "<b>💳 支付设置</b>",
        "",
        f"💵 单笔限额：<code>{min_v:g} ~ {max_v:g}</code> USDT",
        "",
        "<b>📱 在线支付</b>",
        f"OkPay：{_on('pay_okpay_enabled', True)}（{'✅ 已配置' if ok_cfg else '❌ .env 未配置'}）",
        f"KKPay：{_on('pay_kkpay_enabled', True)}（{'✅ 已配置' if kk_cfg else '❌ .env 未配置'}）",
        "",
        "<b>🌐 冷钱包（链上直转，自动到账）</b>",
        f"TRON（TRC20-USDT）：{_on('pay_tron_enabled')}",
        f"└ 收款地址：<code>{h(tron_addr) if tron_addr else '未设置'}</code>",
        f"└ TronGrid Key：{'✅ 已设置' if s.get('trongrid_api_key') else '未设置（公共额度）'}",
        f"TON（USDT）：{_on('pay_ton_enabled')}",
        f"└ 收款地址：<code>{h(ton_addr) if ton_addr else '未设置'}</code>",
        f"└ TonCenter Key：{'✅ 已设置' if s.get('toncenter_api_key') else '未设置（公共额度）'}",
        "",
        "<i>💡 开启冷钱包前必须先设置收款地址，格式错误会被拒绝保存；"
        "用户充值页只展示「已开启且配置齐全」的渠道</i>",
    ]
    buttons = _grid([
        ("💵 单笔限额", "a:pay:limit"), ("🔄 OkPay", "a:pay:tg:okpay"), ("🔄 KKPay", "a:pay:tg:kkpay"),
        ("🔄 TRON", "a:pay:tg:tron"), ("🔄 TON", "a:pay:tg:ton"),
    ], 3)
    buttons += _grid([
        ("🏦 TRON 地址", "a:pay:addr:tron"), ("🏦 TON 地址", "a:pay:addr:ton"),
        ("🔑 TronGrid", "a:pay:key:tron"), ("🔑 TonCenter", "a:pay:key:ton"),
    ], 2)
    buttons.append([("⬅️ 返回设置", "a:set")])
    return "\n".join(lines), kb(buttons)


def page_admin_finance() -> tuple[str, InlineKeyboardMarkup]:
    stats = service.get_finance_stats()
    lines = [
        "<b>📊 财务概览</b>",
        "<i>「今日」按东八区自然日统计，与订单号日期口径一致</i>",
        "",
        f"💰 总充值：<code>{stats['total_recharge']:.2f}</code> USDT",
        f"🎯 发放积分：<code>{stats['total_points']}</code>",
        f"🛒 消费积分：<code>{stats['total_spent']}</code>",
        "",
        f"📅 今日充值：<code>{stats['today_recharge']:.2f}</code> USDT",
        f"📅 今日订单：<code>{stats['today_orders']}</code>",
        f"📅 今日新用户：<code>{stats['today_users']}</code>",
        "",
        f"👤 用户总数：<code>{stats['total_users']}</code>（有积分 {stats['active_users']}）",
        f"📦 条目总数：<code>{stats['total_products']}</code>（商品 {stats['product_count']} / 报单 {stats['report_count']}）",
        "",
        f"📋 订单总数：<code>{stats['total_orders']}</code>",
        f"⏳ 待处理：<code>{stats['pending_orders']}</code>",
        f"✅ 已完成：<code>{stats['completed_orders']}</code>",
        "",
        f"🗄 库存池：<code>{stats['pool_products']}</code> 个池商品 · 现货 <code>{stats['available']}</code> 件 · 已售 <code>{stats['sold']}</code> 件",
    ]
    buttons = [
        [("📦 库存池", "a:pool"), ("📜 操作日志", "a:logs")],
        [("⬅️ 返回", "m:admin")],
    ]
    return "\n".join(lines), kb(buttons)


def page_admin_broadcast() -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "<b>📣 广播消息</b>",
        "",
        "支持发送任意类型消息：文字、图片、视频、文件等",
        "命中 Telegram 限流会自动等待重试，完成后回报成功/失败数",
        "",
        "请选择广播目标：",
    ]
    buttons = _grid([
        ("👥 全员广播", "a:bct:all"), ("💰 有积分用户", "a:bct:with_points"),
        ("💳 无积分用户", "a:bct:no_points"),
    ], 3)
    buttons.append([("⬅️ 返回", "m:admin")])
    return "\n".join(lines), kb(buttons)


LOGS_PAGE_SIZE = 12


def page_admin_logs(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """操作日志（v2.4.0）：管理员每笔关键改动都留痕，可追溯可甩锅。"""
    total = db.count_audit_logs()
    logs = db.list_audit_logs(limit=LOGS_PAGE_SIZE, offset=page * LOGS_PAGE_SIZE)
    lines = [f"<b>📜 操作日志</b>（共 {total} 条）", ""]
    if not logs:
        lines.append("暂无记录（改价/退款/上下架/补货等操作会自动记账）")
    for log in logs:
        lines.append(
            f"🛡 <code>{log.admin_id}</code> {h(log.action)}"
            + (f" · {h(log.detail)}" if log.detail else "")
            + f"\n<code>{fmt_time(log.created_at)}</code>"
        )
    buttons = []
    nav = []
    if page > 0:
        nav.append(("⬅️ 上一页", f"a:logs:{page - 1}"))
    if (page + 1) * LOGS_PAGE_SIZE < total:
        nav.append(("➡️ 下一页", f"a:logs:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("⬅️ 返回", "m:admin")])
    return "\n".join(lines), kb(buttons)


# ==================================================================
# 接单管理（v2.6.0）：大分类 → 二级菜单 → 三级详情
# ==================================================================

_GRABBER_STATUS_TEXT = {
    "active": "🟢 在岗",
    "rest": "🟡 请假",
    "suspended": "🔴 停权",
    "blacklist": "⛔ 黑名单",
}

_GRAB_STATUS_TEXT = {
    "waiting": "⚡ 待抢",
    "grabbed": "🔄 进行中",
    "done": "✅ 已完单",
}


def page_admin_grab() -> tuple[str, InlineKeyboardMarkup]:
    """接单管理首页（大分类入口）：抢单概览 + 五个二级模块。"""
    s = db.get_settings()
    st = db.grab_stats()
    enabled = s.get("grab_enabled")
    chat_id = str(s.get("grab_chat_id") or "").strip()
    lines = [
        "<b>⚡ 接单管理</b>",
        "<i>匿名派单 · 群内抢单 · 深链回单 · 佣金结算，一站式管理</i>",
        "━━━━━━━━━━━━━━",
        f"📶 派单状态：{'🟢 已开启' if enabled else '🔴 已关闭'}"
        + (f" · 接单群 <code>{h(chat_id)}</code>" if chat_id else " · <b>未配置接单群</b>"),
        "",
        f"⚡ 待抢订单：<code>{st['waiting']}</code> · 🔄 进行中：<code>{st['grabbed']}</code>",
        f"✅ 今日完单：<code>{st['done_today']}</code> · 🥷 在岗接单员：<code>{st['grabbers']}</code>",
    ]
    if st["wd_pending"]:
        lines.append(f"🔔 提现待审核：<code>{st['wd_pending']}</code> 笔")
    if st["appeal_pending"]:
        lines.append(f"🔔 申诉待裁定：<code>{st['appeal_pending']}</code> 条")
    lines += [
        "━━━━━━━━━━━━━━",
        f"💰 全局佣金比例 <code>{s.get('grab_commission_pct', 20)}%</code>"
        f" · 冻结 <code>{s.get('grab_freeze_hours', 24)}</code> 小时"
        f" · 回单时限 <code>{s.get('grab_timeout_hours', 24)}</code> 小时",
    ]
    buttons = _grid([
        ("🥷 接单员管理", "a:gbers"), ("📋 派单中心", "a:gords"), ("💌 提现审核", "a:gwds"),
        ("🗣 申诉仲裁", "a:gaps"), ("⚙️ 抢单设置", "a:gset"), ("⬅️ 管理面板", "m:admin"),
    ], 3)
    return "\n".join(lines), kb(buttons)


# ---------- 二级：接单员管理 ----------

def page_admin_grabbers(status: str = "", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """接单员列表：状态筛选条 + 编号/余额/业绩速览，点进详情做全部操作。"""
    size = 8
    grabbers = db.list_grabbers(status=status, limit=size, offset=page * size)
    total = db.count_grabbers(status=status)
    lines = [
        f"<b>🥷 接单员管理</b>（共 <code>{total}</code> 人）",
        "<i>点接单员进详情：改状态/解冻佣金/查单/删档</i>",
        "",
    ]
    buttons = []
    # 状态筛选条
    marks = [("全部", ""), ("在岗", "active"), ("请假", "rest"), ("停权", "suspended"), ("黑名单", "blacklist")]
    row = []
    for label, st in marks:
        mark = "✅ " if st == status else ""
        row.append((f"{mark}{label}", f"a:gbers:{st}:0"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if not grabbers:
        lines.append("该状态下暂无接单员")
    else:
        for g in grabbers:
            lines.append(
                f"{_GRABBER_STATUS_TEXT.get(g.status, g.status)} <code>{g.display()}</code>\n"
                f"💰 可提 <code>{g.balance_available}</code> · 🔒 冻结 <code>{g.balance_frozen}</code>"
                f" · 完单 <code>{g.completed_orders}</code> · 准时 <code>{g.on_time_rate}%</code>"
            )
            buttons.append([(f"⚙️ {g.display()}", f"a:gber:{g.user_id}")])
    nav = []
    if page > 0:
        nav.append(("⬅️ 上一页", f"a:gbers:{status}:{page - 1}"))
    if (page + 1) * size < total:
        nav.append(("➡️ 下一页", f"a:gbers:{status}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("➕ 添加接单员", "a:gbadd")])
    buttons.append([("⬅️ 接单管理", "a:grab")])
    return "\n".join(lines), kb(buttons)


def page_admin_grabber_detail(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    """接单员详情（三级）：全量信息卡 + 状态操作 + 佣金/订单/删档。"""
    g = db.get_grabber(uid)
    if not g:
        return "❌ 接单员不存在", kb([[("⬅️ 返回", "a:gbers")]])
    doing = db.count_grab_orders("grabbed", grabber_id=uid)
    frozen_cms = db.list_grab_commissions(grabber_id=uid, status="frozen", limit=5)
    lines = [
        "<b>🥷 接单员详情</b>",
        "━━━━━━━━━━━━━━",
        f"🪪 编号：<code>{g.display()}</code>",
        f"🆔 TG ID：<code>{g.user_id}</code>" + (f" · @{h(g.username)}" if g.username else ""),
        f"📶 状态：{_GRABBER_STATUS_TEXT.get(g.status, g.status)}",
        "━━━━━━━━━━━━━━",
        f"💰 可提现：<code>{g.balance_available}</code> 积分",
        f"🔒 冻结中：<code>{g.balance_frozen}</code> 积分（{db.count_grab_commissions(uid, 'frozen')} 笔）",
        f"⭐️ 累计佣金：<code>{g.total_commission}</code> 积分",
        "━━━━━━━━━━━━━━",
        f"✅ 完单 <code>{g.completed_orders}</code> · ⏰ 超时 <code>{g.timeout_orders}</code> · 👋 放弃 <code>{g.giveup_orders}</code>",
        f"🎯 准时率 <code>{g.on_time_rate}%</code> · 进行中 <code>{doing}</code> 笔",
        f"🕐 最近抢单：<code>{fmt_time(g.last_grab_at) or '—'}</code>",
        f"👥 接单群：{'✅ 已进群（专属链接已作废）' if g.joined_group else ('🔗 未进群 · 专属链接已发放' if g.invite_link else '⚠️ 未进群 · 链接未生成')}",
    ]
    if g.cooldown_until:
        lines.append(f"⏳ 冷静期至：<code>{fmt_time(g.cooldown_until)}</code>")
    if frozen_cms:
        lines += ["", "<b>🔒 冻结中佣金（前 5 笔）</b>"]
        for c in frozen_cms:
            until = fmt_time(c.frozen_until)[:16] if c.frozen_until else "立即可解冻"
            lines.append(f"<code>#{c.order_id}</code> +<code>{c.amount}</code> · {until}")
    lines.append("━━━━━━━━━━━━━━")

    buttons = []
    # 状态操作（按当前状态给出可切换项）
    st_row = []
    if g.status != "active":
        st_row.append(("🟢 恢复在岗", f"a:gbst:{uid}:active"))
    if g.status != "rest":
        st_row.append(("🟡 设为请假", f"a:gbst:{uid}:rest"))
    if g.status != "suspended":
        st_row.append(("🔴 停权", f"a:gbst:{uid}:suspended"))
    if st_row:
        buttons.append(st_row)
    if g.status == "blacklist":
        buttons.append([("💊 移出黑名单", f"a:gbst:{uid}:active")])
    else:
        buttons.append([("💣 拉入黑名单", f"a:gbst:{uid}:blacklist")])
    buttons.append(_grid([
        ("🔓 解冻佣金", f"a:gbrel:{uid}"),
        ("📋 他的进行中", f"a:gbo:{uid}"),
        ("🔗 重发链接", f"a:gblink:{uid}"),
    ], 3)[0])
    buttons.append([("🗑 删除档案", f"a:gbdel:{uid}")])
    buttons.append([("⬅️ 接单员列表", "a:gbers")])
    return "\n".join(lines), kb(buttons)


# ---------- 二级：派单中心 ----------

def page_admin_grab_orders(st: str = "grabbed", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """派单中心：待抢/进行中/已完单三个 tab；进行中可强制释放，待抢可撤回。"""
    st = st if st in ("waiting", "grabbed", "done") else "grabbed"
    size = 6
    orders = db.list_grab_orders(st, limit=size, offset=page * size)
    total = db.count_grab_orders(st)

    lines = [
        "<b>📋 派单中心</b>",
        f"<i>当前：{_GRAB_STATUS_TEXT[st]}（共 {total} 笔）——商品自动发货不走派单，这里全是报单内容</i>",
        "",
    ]
    buttons = [[
        ("⚡ 待抢", "a:gords:waiting:0"),
        ("🔄 进行中", "a:gords:grabbed:0"),
        ("✅ 已完单", "a:gords:done:0"),
    ]]
    if not orders:
        lines.append("该状态下暂无订单")
    else:
        for o in orders:
            lines.append(
                f"<code>#{o.id}</code> {h(o.product_name)} · 佣金 <code>{o.commission}</code>"
            )
            if st == "grabbed":
                lines.append(
                    f"└ 🥷 {h(o.grabber_name or '-')} · 时限 <code>{fmt_time(o.deadline)}</code>"
                )
                buttons.append([
                    (f"📄 详情 #{o.id}", f"a:od:{o.id}"),
                    ("🔓 强制释放", f"a:gfree:{o.id}"),
                ])
            elif st == "waiting":
                lines.append(f"└ 第 <code>{o.dispatch_count}</code> 次派单 · <code>{fmt_time(o.last_dispatch_at)[:16]}</code>")
                buttons.append([
                    (f"📄 详情 #{o.id}", f"a:od:{o.id}"),
                    ("↩️ 撤回派单", f"a:grecall:{o.id}"),
                ])
            else:
                lines.append(
                    f"└ 🥷 {h(o.grabber_name or '-')} · 完单 <code>{fmt_time(o.completed_at)[:16]}</code>"
                )
                buttons.append([(f"📄 详情 #{o.id}", f"a:od:{o.id}")])
    nav = []
    if page > 0:
        nav.append(("⬅️ 上一页", f"a:gords:{st}:{page - 1}"))
    if (page + 1) * size < total:
        nav.append(("➡️ 下一页", f"a:gords:{st}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("⬅️ 接单管理", "a:grab")])
    return "\n".join(lines), kb(buttons)


# ---------- 二级：提现审核 ----------

def page_admin_grab_withdrawals(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """提现审核：待审置顶带按钮，下面是最近处理记录。"""
    pending = db.list_withdrawals(status="pending", limit=10)
    size = 5
    history = db.list_withdrawals(limit=50)
    history = [w for w in history if w.status != "pending"][page * size:(page + 1) * size]

    lines = ["<b>💌 提现审核</b>", ""]
    buttons = []
    lines.append(f"<b>🕐 待审核（{db.count_withdrawals('pending')} 笔）</b>")
    if not pending:
        lines.append("暂无待审核提现")
    else:
        for w in pending:
            g = db.get_grabber(w.grabber_id)
            lines.append(
                f"<code>{w.id}</code>\n"
                f"└ 🥷 {h(g.display() if g else str(w.grabber_id))} · <code>{w.amount}</code> 积分 ≈ <b>{w.amount_usdt:.2f}</b> U\n"
                f"└ OK钱包ID：<code>{h(w.address)}</code> · <code>{fmt_time(w.created_at)[:16]}</code>"
            )
            buttons.append([
                (f"✅ 打款 {w.amount_usdt:.2f}U", f"a:gwdok:{w.id}"),
                ("❌ 拒绝", f"a:gwdno:{w.id}"),
            ])
    lines += ["", "<b>🗓 最近处理记录</b>"]
    if not history:
        lines.append("暂无记录")
    else:
        wd_status = {"paid": "✅ 已打款", "rejected": "❌ 已拒绝"}
        for w in history:
            lines.append(
                f"{wd_status.get(w.status, w.status)} <code>{w.id}</code> · "
                f"<code>{w.amount}</code> 积分 ≈ <code>{w.amount_usdt:.2f}</code> U · <code>{fmt_time(w.processed_at)[:16]}</code>"
            )
    nav = []
    if page > 0:
        nav.append(("⬅️ 上一页", f"a:gwds:{page - 1}"))
    if len(history) == size:
        nav.append(("➡️ 下一页", f"a:gwds:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("⬅️ 接单管理", "a:grab")])
    return "\n".join(lines), kb(buttons)


# ---------- 二级：申诉仲裁 ----------

def page_admin_grab_appeals(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """申诉仲裁：待裁置顶带按钮，下面是裁定历史。"""
    pending = db.list_grab_appeals(status="pending", limit=10)
    size = 5
    all_appeals = db.list_grab_appeals(limit=50)
    history = [a for a in all_appeals if a.status != "pending"][page * size:(page + 1) * size]

    ap_status = {"approved": "✅ 已支持", "rejected": "❌ 已驳回"}
    lines = ["<b>🗣 申诉仲裁</b>", ""]
    buttons = []
    lines.append(f"<b>🕐 待裁定（{db.count_grab_appeals('pending')} 条）</b>")
    if not pending:
        lines.append("暂无待裁定申诉")
    else:
        for a in pending:
            g = db.get_grabber(a.grabber_id)
            lines.append(
                f"<code>#{a.id}</code> 🥷 {h(g.display() if g else str(a.grabber_id))} · "
                f"订单 <code>#{h(a.order_id or '-')}</code>\n"
                f"<blockquote>{h(a.content[:120])}</blockquote>"
            )
            buttons.append([
                (f"✅ 支持 #{a.id}", f"a:gapok:{a.id}"),
                ("❌ 驳回", f"a:gapno:{a.id}"),
            ])
    lines += ["", "<b>🗓 裁定历史</b>"]
    if not history:
        lines.append("暂无记录")
    else:
        for a in history:
            lines.append(
                f"{ap_status.get(a.status, a.status)} <code>#{a.id}</code> · "
                f"订单 <code>#{h(a.order_id or '-')}</code> · <code>{fmt_time(a.handled_at)[:16]}</code>"
            )
    nav = []
    if page > 0:
        nav.append(("⬅️ 上一页", f"a:gaps:{page - 1}"))
    if len(history) == size:
        nav.append(("➡️ 下一页", f"a:gaps:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("⬅️ 接单管理", "a:grab")])
    return "\n".join(lines), kb(buttons)


# ---------- 二级：抢单设置 ----------

# 设置项元数据：(key, 名称, 单位/说明, 类型 bool/num/text)
_GRAB_SETTING_ITEMS = [
    ("grab_enabled", "派单总开关", "开=报单类新订单自动派到接单群", "bool"),
    ("grab_chat_id", "接单群 ID", "私密群 ID（负数，如 -1001234567890）；机器人须在群内", "text"),
    ("grab_commission_pct", "全局佣金比例", "%，按订单积分折算；商品可单独覆盖", "num"),
    ("grab_freeze_hours", "佣金冻结时长", "小时，0=完单立即到账", "num"),
    ("grab_timeout_hours", "最晚回单时限", "小时，从抢中时刻起算；超时自动释放重派", "num"),
    ("grab_warn_minutes", "超时预警提前量", "分钟，0=不预警", "num"),
    ("grab_cooldown_seconds", "抢单冷却", "秒，防连点", "num"),
    ("grab_penalty_minutes", "超时/放弃冷静期", "分钟，期间禁止抢单", "num"),
    ("grab_max_dispatch", "最大重派次数", "超过则交还管理员人工回单", "num"),
    ("grab_withdraw_min", "最低提现积分", "积分", "num"),
    ("grab_points_per_usdt", "积分兑U汇率", "多少积分 = 1 USDT，提现自动换算打款额", "num"),
    ("grab_auto_release", "佣金自动解冻", "开=到点自动转可提现；关=管理员手动解冻", "bool"),
    ("grab_notify_admin", "抢单动态通知管理员", "抢中/回单/超时/放弃通报", "bool"),
]


def page_admin_grab_settings() -> tuple[str, InlineKeyboardMarkup]:
    """抢单设置中心：每个参数一行展示 + 一个修改按钮，数值型进入输入状态。"""
    s = db.get_settings()
    lines = [
        "<b>⚙️ 抢单设置</b>",
        "<i>改动即时生效；开关点击即切，数值点击后按提示发送新值</i>",
        "━━━━━━━━━━━━━━",
    ]
    buttons = []
    for key, name, desc, kind in _GRAB_SETTING_ITEMS:
        val = s.get(key)
        if kind == "bool":
            show = "🟢 开启" if val else "🔴 关闭"
            lines.append(f"<b>{name}</b>：{show}\n└ <i>{desc}</i>")
            buttons.append((f"🔄 {name}", f"a:gstog:{key}"))
        else:
            show = str(val) if val not in (None, "") else "未设置"
            lines.append(f"<b>{name}</b>：<code>{h(show)}</code>\n└ <i>{desc}</i>")
            buttons.append((f"✏️ {name}", f"a:gsedit:{key}"))
    lines.append("━━━━━━━━━━━━━━")
    grid = _grid(buttons, 2)
    grid.append([("⬅️ 接单管理", "a:grab")])
    return "\n".join(lines), kb(grid)
