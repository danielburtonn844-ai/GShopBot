"""用户端页面（v2.4.0 模块化拆分）：主菜单/商品/购买/充值/订单/余额/个人中心/帮助。

v2.4.0 新增：商品分类筛选、商品搜索、库存池实时余量与「仅剩 X 件」稀缺提示、
售罄商品「到货提醒」订阅、池模式订单的内容回看/一键重发。
"""

from __future__ import annotations

from telegram import InlineKeyboardMarkup

from .models import PRODUCT_KINDS, STOCK_TYPES, Recharge, fmt_time
from .richtext import TG_DIAMOND, TG_MAIL, TG_STAR
from .runtime import VERSION, db, service
from .ui import get_points, h, kb, order_status_text, tags_text


def _stock_line_for(p) -> str:
    """列表/详情共用的库存文案：池模式实时数池子，固定模式读 stock 字段。"""
    if getattr(p, "delivery_mode", "fixed") == "pool":
        n = db.count_stock_items(p.id, "available")
        return f"仅剩 {n} 件" if n <= 10 else f"现货 {n} 件"
    return "∞" if p.stock == -1 else str(p.stock)


def page_root(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    user = db.get_user(user_id)
    points = user.points if user else 0
    settings = db.get_settings()

    # 欢迎语：优先用富文本版（管理员发的加粗/链接/高级会员表情原样呈现）；
    # 老数据没有 HTML 版，回退为转义纯文本。发送侧有安全降级兜底。
    welcome = settings.get("welcome_text_html") or f"<b>{h(settings.get('welcome_text', '👋 欢迎光临'))}</b>"

    lines = [
        welcome,
        "",
        f"{TG_DIAMOND} 当前积分：<code>{points}</code>",
        "",
        "🛒 <b>商品</b>：付款后立即自动发货",
        "📝 <b>报单</b>：提交所需信息，管理员人工处理",
        f"{TG_MAIL} <b>供需</b>：花积分发供需，机器人广播/频道同步曝光",
        f"{TG_STAR} <b>充值</b>：USDT 充值换积分，自动到账",
    ]
    if settings.get("maintenance_mode") and not db.is_admin(user_id):
        lines += ["", "🔧 系统维护中：暂停下单，充值正常"]

    buttons = [
        [("🛒 商品", "m:shop"), ("📝 报单", "m:report")],
        [("📢 供需大厅", "sup:hall"), ("✍️ 发布供需", "sup:new")],
        [("💰 充值积分", "m:recharge"), ("📋 我的订单", "m:orders")],
        [("💳 我的余额", "m:balance"), ("👤 个人中心", "m:profile")],
        [("❓ 使用帮助", "m:help")],
    ]
    if db.is_admin(user_id):
        buttons.append([("🔧 管理面板", "m:admin")])

    return "\n".join(lines), kb(buttons)


def page_list(user_id: int, kind: str, category: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """商品/报单列表：分类筛选（v2.4.0）+ 搜索入口 + 池模式实时余量。

    售罄条目不再隐藏，而是标注「已售罄」仍可点进详情订阅到货提醒——商城式体验。
    """
    items = db.list_products(kind=kind, limit=60, category=category or None)
    # 列表展示 在售 + 售罄（售罄可订阅到货提醒）；下架/隐藏不展示
    items = [p for p in items if p.status in ("on", "sold_out")][:30]
    if kind == "product":
        title = "🛒 商品列表"
        subtitle = "购买后立即自动发货，无需等待"
    else:
        title = "📝 报单列表"
        subtitle = "购买时需按介绍提交所需信息，管理员人工处理后回单"

    lines = [f"<b>{title}</b>", f"<i>{subtitle}</i>"]
    if category:
        lines.append(f"<i>当前分类：{h(category)}</i>")
    lines.append("")

    buttons = []
    # 分类筛选条（每行 3 个，最多 12 个分类）——只有管理端配置了分类才出现
    cats = db.get_settings().get("product_categories") or []
    if cats:
        row = [("📋 全部", f"m:shop:{''}" if kind == "product" else "m:report:")]
        for i, c in enumerate(cats[:11]):
            mark = "✅ " if c == category else ""
            row.append((f"{mark}{c}", f"m:{'shop' if kind == 'product' else 'report'}:{i}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    if not items:
        lines.append("暂无可购买的条目")
    else:
        for p in items:
            stock = _stock_line_for(p)
            tag = tags_text(p.tags)
            top = "📌 " if p.is_top else ""
            sold_mark = "🟡 " if p.status == "sold_out" else ""
            lines.append(f"{top}{sold_mark}{tag}{h(p.name)}  <code>{p.points}</code> 积分  库存 {stock}")
            btn_label = f"{top}{'🟡 ' if p.status == 'sold_out' else ''}{h(p.name)}  {p.points}积分"
            buttons.append([(btn_label, f"d:{p.id}")])

    buttons.append([("🔍 搜索", f"m:search:{kind}")])
    buttons.append([("🏠 主菜单", "m:root")])
    return "\n".join(lines), kb(buttons)


def page_search_results(user_id: int, kind: str, keyword: str) -> tuple[str, InlineKeyboardMarkup]:
    """商品搜索结果页（v2.4.0）：按名称/介绍/分类模糊匹配在售条目。"""
    results = db.search_products(keyword, kind=kind, status="on", limit=15)
    lines = [f"<b>🔍 搜索「{h(keyword)}」</b>（{len(results)} 条结果）", ""]
    buttons = []
    if not results:
        lines.append("没有找到匹配的条目，换个关键词试试")
    else:
        for p in results:
            lines.append(f"{h(p.name)}  <code>{p.points}</code> 积分  库存 {_stock_line_for(p)}")
            buttons.append([(f"{h(p.name)}  {p.points}积分", f"d:{p.id}")])
    buttons.append([("⬅️ 返回列表", "m:shop" if kind == "product" else "m:report")])
    buttons.append([("🏠 主菜单", "m:root")])
    return "\n".join(lines), kb(buttons)


def page_detail(user_id: int, product_id: str) -> tuple[str, InlineKeyboardMarkup, str | None]:
    p = db.get_product(product_id)
    if not p or p.status not in ("on", "sold_out"):
        return "❌ 该条目不存在或已下架", kb([[("🏠 主菜单", "m:root")]]), None

    kind_text = PRODUCT_KINDS.get(p.kind, p.kind)
    is_pool = getattr(p, "delivery_mode", "fixed") == "pool"
    if is_pool:
        stock_text = f"{db.count_stock_items(p.id, 'available')} 件（库存池）"
    else:
        stock_text = "无限" if p.stock == -1 else f"{p.stock} 个"
    limit_text = "不限" if p.limit_per_user == 0 else f"每人限 {p.limit_per_user} 个"

    lines = [
        f"<b>{h(p.name)}</b>",
        "",
        f"类型：{kind_text}",
        f"价格：<code>{p.points}</code> 积分",
        f"库存：{stock_text}",
        f"限购：{limit_text}",
    ]
    if p.category:
        lines.append(f"分类：📂 {h(p.category)}")
    if tags_text(p.tags):
        lines.append(f"标签：{tags_text(p.tags)}")
    if p.description:
        lines += ["", f"<i>{h(p.description)}</i>"]
    if p.kind == "report":
        lines += [
            "",
            "⏳ 人工处理流程：",
            "1️⃣ 确认购买后，按上方介绍提交所需信息（必填，支持文字/图片/文件）",
            "2️⃣ 提交后扣积分并生成订单，信息一并发给管理员",
            "3️⃣ 管理员处理完成后回单，可在订单详情查看",
        ]
    elif is_pool:
        lines += [
            "",
            "📦 库存池发货：每件内容独一无二、先到先得",
            "⚡ 付款后立即自动发货，购得内容带专属编号",
            "💡 购买后可在「我的订单 → 订单详情」随时回看或重新发送",
        ]
    else:
        lines += ["", "⚡ 购买后立即自动发货，发货内容当场显示"]

    user = db.get_user(user_id)
    if user and user.points < p.points:
        lines += ["", f"⚠️ 积分不足（当前 <code>{user.points}</code>），请先充值"]

    buttons = []
    if p.status == "sold_out":
        lines += ["", "🟡 <b>已售罄</b>，补货后可购买"]
        if is_pool:
            # 到货提醒：订阅后补货第一时间私信通知，提醒一次即退订
            if db.has_restock_sub(p.id, user_id):
                buttons.append([("🔕 取消到货提醒", f"rsub:0:{p.id}")])
            else:
                buttons.append([("🔔 到货提醒", f"rsub:1:{p.id}")])
    else:
        buttons.append([("✅ 立即购买", f"buy:{p.id}")])
    buttons.append([("⬅️ 返回列表", "m:shop" if p.kind == "product" else "m:report")])
    buttons.append([("🏠 主菜单", "m:root")])
    return "\n".join(lines), kb(buttons), p.photo_file_id or None


def page_buy_confirm(user_id: int, product_id: str) -> tuple[str, InlineKeyboardMarkup]:
    settings = db.get_settings()
    if settings.get("maintenance_mode") and not db.is_admin(user_id):
        return "🔧 系统维护中，暂时无法下单（充值不受影响）", kb([[("🏠 主菜单", "m:root")]])

    p = db.get_product(product_id)
    if not p or p.status != "on":
        return "❌ 该条目不存在或已下架", kb([[("🏠 主菜单", "m:root")]])

    is_pool = getattr(p, "delivery_mode", "fixed") == "pool"
    pool_left = db.count_stock_items(p.id, "available") if is_pool else -1
    if (is_pool and pool_left <= 0) or (not is_pool and p.stock == 0):
        return f"❌ {h(p.name)}\n\n已售罄", kb([[("⬅️ 返回", f"d:{p.id}")]])

    if p.limit_per_user > 0:
        count = db.count_user_product_orders(user_id, p.id)
        if count >= p.limit_per_user:
            return f"❌ {h(p.name)}\n\n已达每人限购数量（{p.limit_per_user}）", kb([[("⬅️ 返回", f"d:{p.id}")]])

    points = get_points(user_id)
    enough = points >= p.points

    lines = [
        "<b>🛒 确认订单</b>",
        "",
        f"名称：{h(p.name)}",
        f"类型：{PRODUCT_KINDS.get(p.kind, p.kind)}",
        f"价格：<code>{p.points}</code> 积分",
        f"当前余额：<code>{points}</code> 积分",
    ]
    if is_pool:
        lines.append(f"现货：<code>{pool_left}</code> 件（售完即止）")
    if enough:
        lines.append(f"支付后余额：<code>{points - p.points}</code> 积分")
    else:
        lines += ["", f"⚠️ 积分不足，还差 <code>{p.points - points}</code> 积分，请先充值"]

    if p.kind == "report":
        lines += ["", "📝 报单类：点「确认支付」后还需按介绍提交所需信息（必填），提交后才生成订单"]
    elif is_pool:
        lines += ["", "📦 库存池商品：点「确认支付」后随机发出一件专属内容，可在订单详情随时回看"]
    else:
        lines += ["", "⚡ 商品类：点「确认支付」后立即扣积分并自动发货"]

    buttons = []
    if enough:
        buttons.append([("✅ 确认支付", f"cbuy:{p.id}")])
    else:
        buttons.append([("💰 去充值", "m:recharge")])
    buttons.append([("⬅️ 返回", f"d:{p.id}")])

    return "\n".join(lines), kb(buttons)


def page_recharge(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    settings = db.get_settings()
    rate = settings.get("points_rate", 10)
    min_amount = settings.get("min_recharge_usdt", 1)
    max_amount = settings.get("max_recharge_usdt", 1000)

    lines = [
        "<b>💰 充值积分</b>",
        "",
        f"当前汇率：<code>1 USDT = {rate} 积分</code>",
        f"单笔限额：<code>{min_amount:g} ~ {max_amount:g}</code> USDT",
        f"当前积分：<code>{get_points(user_id)}</code>",
        "",
        "📖 充值说明：",
        "1️⃣ 支持 OkPay / KKPay 在线支付，也支持 TRON / TON 链上直转冷钱包",
        "2️⃣ 支付链接 30 分钟内有效，支付成功后积分自动到账（约 1 分钟内）",
        "3️⃣ 链上转账请严格按页面显示的金额与备注操作，否则无法自动到账",
        "4️⃣ 如未及时到账，可在支付页面点「查询支付状态」手动刷新",
        "",
        "请选择充值金额：",
    ]

    buttons = [
        [("5 USDT", "rc:5"), ("10 USDT", "rc:10")],
        [("20 USDT", "rc:20"), ("50 USDT", "rc:50")],
        [("100 USDT", "rc:100"), ("✏️ 自定义金额", "rc:custom")],
        [("📜 充值记录", "m:rchist")],
        [("🏠 主菜单", "m:root")],
    ]
    return "\n".join(lines), kb(buttons)


def page_recharge_confirm(amount: float) -> tuple[str, InlineKeyboardMarkup]:
    settings = db.get_settings()
    rate = settings.get("points_rate", 10)
    points = int(amount * rate)
    amount_text = f"{amount:g}"

    lines = [
        "<b>💰 确认充值</b>",
        "",
        f"充值金额：<code>{amount_text}</code> USDT",
        f"获得积分：<code>{points}</code>",
        f"汇率：<code>1 USDT = {rate} 积分</code>",
        "",
        "⏰ 支付链接 30 分钟内有效，支付成功后积分自动到账",
        "",
        "请选择支付方式：",
    ]

    # 只展示后台已开启且配置齐全的支付渠道（充值页按钮与 gateway_available 保持一致）
    buttons = []
    if service.gateway_available("okpay"):
        buttons.append([("💳 OkPay 支付", f"rcpay:okpay:{amount_text}")])
    if service.gateway_available("kkpay"):
        buttons.append([("💳 KKPay 支付", f"rcpay:kkpay:{amount_text}")])
    if service.gateway_available("trc20"):
        buttons.append([("🌐 TRON 链上转账（TRC20）", f"rcpay:trc20:{amount_text}")])
    if service.gateway_available("ton"):
        buttons.append([("💎 TON 链上转账", f"rcpay:ton:{amount_text}")])
    buttons.append([("⬅️ 返回", "m:recharge")])
    return "\n".join(lines), kb(buttons)


def page_chain_pay(recharge: Recharge) -> tuple[str, InlineKeyboardMarkup]:
    """冷钱包支付详情页：收款地址 + 应付金额（TRON 含识别尾数）/ 备注（TON）。

    金额与地址都用 <code> 展示，用户长按即可复制，避免手输出错。
    """
    settings = db.get_settings()
    if recharge.chain == "trc20":
        wallet = settings.get("tron_wallet_address", "") or ""
        chain_name = "TRON（TRC20-USDT）"
        amount_text = recharge.expected_amount or f"{recharge.amount:g}"
        key_lines = [
            f"收款地址：<code>{wallet}</code>",
            f"应付金额：<code>{amount_text}</code> USDT",
            "",
            "⚠️ 金额尾数是本单唯一识别码，请务必<b>原样转账</b>（多转少转都无法自动到账）",
        ]
    else:
        wallet = settings.get("ton_wallet_address", "") or ""
        chain_name = "TON（USDT）"
        amount_text = f"{recharge.amount:g}"
        key_lines = [
            f"收款地址：<code>{wallet}</code>",
            f"应付金额：<code>{amount_text}</code> USDT",
            f"转账备注：<code>{recharge.memo}</code>",
            "",
            "⚠️ 备注（Memo/Comment）必须<b>原样填写</b>，否则无法自动到账",
        ]

    lines = [
        "<b>🌐 链上充值订单已创建</b>",
        "",
        f"订单号：<code>{recharge.id}</code>",
        f"支付链：<code>{chain_name}</code>",
        f"获得积分：<code>{recharge.points}</code>",
        "",
        *key_lines,
        "",
        "⏰ 订单 2 小时内有效，链上确认后积分自动到账（约 1~3 分钟）",
        "💡 转账完成后可点下方按钮手动刷新",
    ]
    return "\n".join(lines), kb([
        [("🔄 查询支付状态", f"rcchk:{recharge.id}")],
        [("🏠 主菜单", "m:root")],
    ])


def page_recharge_history(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    records = db.list_recharges(user_id=user_id, limit=15)
    lines = [
        "<b>📜 充值记录</b>",
        "<i>⏳ 待支付  ✅ 已到账  ❌ 已失效（超时未支付）</i>",
        "",
    ]
    if not records:
        lines.append("暂无充值记录")
    else:
        mark = {"pending": "⏳", "completed": "✅", "failed": "❌"}.get
        for r in records:
            lines.append(
                f"{mark(r.status, '❓')} <code>{r.id}</code>  {r.amount:g} USDT → {r.points} 积分"
            )
    return "\n".join(lines), kb([[("💰 去充值", "m:recharge")], [("🏠 主菜单", "m:root")]])


def page_orders(user_id: int, flt: str = "all") -> tuple[str, InlineKeyboardMarkup]:
    orders = db.list_orders(user_id=user_id, limit=100)
    if flt != "all":
        orders = [o for o in orders if o.status == flt]
    orders = orders[:15]

    fname = {"all": "全部", "pending": "待处理", "processing": "处理中",
             "completed": "已完成", "refunded": "已退款"}.get(flt, "全部")
    lines = [f"<b>📋 我的订单</b>（{fname}）", ""]
    buttons = []
    if not orders:
        lines.append("暂无订单")
    else:
        for o in orders:
            pool_mark = "📦 " if getattr(o, "stock_item_id", 0) else ""
            lines.append(f"{order_status_text(o.status)}  {pool_mark}<code>{o.id}</code>  {h(o.product_name)}")
            buttons.append([(f"📄 {o.id}", f"od:{o.id}")])

    buttons.append([("⏳ 待处理", "m:ofl:pending"), ("🔄 处理中", "m:ofl:processing")])
    buttons.append([("✅ 已完成", "m:ofl:completed"), ("🔵 全部", "m:ofl:all")])
    buttons.append([("🏠 主菜单", "m:root")])
    return "\n".join(lines), kb(buttons)


def page_order_detail(user_id: int, order_id: str) -> tuple[str, InlineKeyboardMarkup]:
    o = db.get_order(order_id)
    if not o or o.user_id != user_id:
        return "❌ 订单不存在", kb([[("⬅️ 返回", "m:orders")]])

    lines = [
        "<b>📋 订单详情</b>",
        "",
        f"订单号：<code>{o.id}</code>",
        f"名称：{h(o.product_name)}",
        f"类型：{PRODUCT_KINDS.get(o.product_kind, o.product_kind)}",
        f"积分：<code>{o.points}</code>",
        f"状态：{order_status_text(o.status)}",
        f"时间：<code>{fmt_time(o.created_at)}</code>",
    ]
    if o.completed_at:
        lines.append(f"完成：<code>{fmt_time(o.completed_at)}</code>")
    if o.note:
        lines += ["", f"📎 我提交的信息：\n{h(o.note)}"]
    if o.reply_content:
        lines += ["", f"📝 回单内容：\n{h(o.reply_content)}"]

    # ---------- 库存池订单（v2.4.0）：展示购得内容与专属编号 ----------
    buttons = []
    item_id = getattr(o, "stock_item_id", 0)
    if item_id:
        item = db.get_stock_item(item_id)
        if item:
            lines += ["", f"📦 购得内容：<b>编号 #{item.id}</b>（{STOCK_TYPES.get(item.content_type, item.content_type)}）"]
            if item.content_type == "text":
                lines += ["", f"<code>{h(item.content_text)}</code>"]
            else:
                if item.caption:
                    lines.append(f"说明：{h(item.caption)}")
                lines.append("<i>内容已私信送达；点下方「重新发送」可随时再收一次</i>")
                buttons.append([("🔄 重新发送", f"redeliver:{o.id}")])
        else:
            lines += ["", "📦 购得内容：记录已清理"]

    if o.status in ("pending", "processing"):
        buttons.append([("🔄 催单", f"urge:{o.id}")])
    buttons.append([("⬅️ 返回", "m:orders")])
    return "\n".join(lines), kb(buttons)


def page_balance(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    user = db.get_user(user_id)
    if not user:
        return "❌ 用户不存在", kb([[("🏠 主菜单", "m:root")]])

    recharges = db.list_recharges(user_id=user_id, limit=5)
    lines = [
        f"<b>{TG_DIAMOND} 我的余额</b>",
        "",
        f"当前积分：<code>{user.points}</code>",
        f"累计充值：<code>{user.total_recharge:.2f}</code> USDT",
        f"累计消费：<code>{user.total_spent}</code> 积分",
        "",
        "<i>积分可通过 USDT 充值获得，用于购买商品和报单</i>",
    ]
    if recharges:
        lines += ["", "<b>最近充值：</b>"]
        mark = {"pending": "⏳", "completed": "✅", "failed": "❌"}.get
        for r in recharges:
            lines.append(f"{mark(r.status, '❓')} {r.amount:g} USDT → {r.points} 积分")

    buttons = [
        [("💰 充值", "m:recharge"), ("📜 充值记录", "m:rchist")],
        [("🏠 主菜单", "m:root")],
    ]
    return "\n".join(lines), kb(buttons)


def page_profile(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    user = db.get_user(user_id)
    if not user:
        return "❌ 用户不存在", kb([[("🏠 主菜单", "m:root")]])

    my_orders = db.count_user_orders(user_id)
    lines = [
        "<b>👤 个人中心</b>",
        "",
        f"ID：<code>{user.user_id}</code>",
        f"用户名：@{h(user.username or '无')}",
        f"昵称：{h((user.first_name + ' ' + user.last_name).strip() or '无')}",
        f"积分：<code>{user.points}</code>",
        f"我的订单：<code>{my_orders}</code> 单",
        f"注册时间：<code>{fmt_time(user.created_at)}</code>",
        f"状态：{'🚫 已限制' if user.is_banned else '✅ 正常'}",
    ]
    return "\n".join(lines), kb([[("🏠 主菜单", "m:root")]])


def page_help() -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "<b>❓ 使用帮助</b>",
        "",
        "<b>💳 积分是什么？</b>",
        "积分是本站通用货币，通过 USDT 充值获得，可购买商品和报单。",
        "",
        "<b>💰 怎么充值？</b>",
        "进入「充值积分」选择金额 → 生成 OkPay 支付链接 → 在 Telegram 内完成支付 → 积分自动到账。支付链接 30 分钟有效。",
        "",
        "<b>🛒 商品怎么买？</b>",
        "进入「商品」挑选 → 确认订单 → 确认支付 → 立即扣积分并自动发货，发货内容当场显示。支持分类筛选与搜索。",
        "",
        "<b>📦 库存池商品</b>",
        "带「库存池」标识的商品，每件内容独一无二、一件只卖一次、先到先得；"
        "付款后自动收到专属内容并附编号，可在「我的订单 → 订单详情」随时回看或重新发送；"
        "售罄后点「🔔 到货提醒」，补货第一时间私信通知你。",
        "",
        "<b>📝 报单怎么买？</b>",
        "进入「报单」挑选 → 看清介绍里要求提供的信息 → 确认支付 → 按提示提交所需信息（必填，支持文字/图片/文件）→ 生成订单等待管理员人工处理 → 处理完成收到回单通知。",
        "",
        "<b>📢 供需怎么发？</b>",
        "进入「发布供需」→ 选投放渠道（机器人广播 / 频道 / 双发）→ 选分类 → 填内容（可带一张图）→ 预览确认后扣积分发布。发布后展示在供需大厅，可匿名发布；帖子不可编辑。逛「供需大厅」完全免费。",
        "",
        "<b>📋 订单状态说明</b>",
        "⏳ 待处理：已提交，等待管理员接单",
        "🔄 处理中：管理员已接单，正在处理",
        "✅ 已完成：已回单，详情页可查看回单内容",
        "💰 已退款：订单关闭，积分已退回账户",
        "",
        "<b>🔄 催单</b>：订单长时间未处理时，可在订单详情点「催单」提醒管理员",
        "",
        "如有其他问题请联系管理员",
        "",
        f"<i>版本 v{VERSION}</i>",
    ]
    return "\n".join(lines), kb([[("🏠 主菜单", "m:root")]])


def page_force_join(missing: list) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "<b>🔒 请先加入以下频道</b>",
        "",
        "加入后点击「✅ 我已加入」继续使用",
        "",
    ]
    buttons = []
    for ch in missing:
        uname = (ch.username or "").lstrip("@")
        lines.append(f"📢 {h(ch.title or ch.chat_id)}")
        if uname:
            buttons.append([(f"📢 加入 {h(ch.title or uname)}", f"https://t.me/{uname}")])
    buttons.append([("✅ 我已加入", "m:checkjoin")])
    return "\n".join(lines), kb(buttons)
