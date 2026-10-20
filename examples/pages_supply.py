"""付费供需页面（v2.4.0 模块化拆分）：用户端大厅/发布向导 + 管理端统计/审核/开关/定价。"""

from __future__ import annotations

from telegram import InlineKeyboardMarkup

from .models import SUPPLY_CHANNELS, SupplyPost, fmt_time
from .richtext import strip_html_tags
from .runtime import db, supply
from .supply_service import SUPPLY_STATUS_ICON, SUPPLY_STATUS_TEXT
from .ui import h, kb

SUPPLY_PAGE_SIZE = 5
SUPPLY_ADMIN_PAGE_SIZE = 10


# ==================================================================
# 用户端
# ==================================================================

def page_supply_hall(user_id: int, page: int = 0, category: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """供需大厅：时间倒序分页 + 分类筛选。浏览免费。

    「大厅浏览」开关关闭时，普通用户看到维护提示（管理员不受限，方便巡检）。
    """
    if not supply.hall_open() and not db.is_admin(user_id):
        lines = [
            "<b>📢 供需大厅</b>",
            "",
            "🛠 大厅维护中，暂时无法浏览，稍后再来看看吧",
        ]
        buttons = []
        if not supply.paused():
            buttons.append([("✍️ 发布供需", "sup:new")])
        buttons.append([("🏠 主菜单", "m:root")])
        return "\n".join(lines), kb(buttons)
    total = db.count_supply_posts(status="active", category=category or None)
    posts = db.list_supply_posts(
        status="active", category=category or None,
        limit=SUPPLY_PAGE_SIZE, offset=page * SUPPLY_PAGE_SIZE,
    )
    s = db.get_settings()
    cats = s.get("supply_categories") or []

    lines = ["<b>📢 供需大厅</b>"]
    if category:
        lines.append(f"<i>当前筛选：{h(category)}</i>")
    lines.append("")
    if not posts:
        lines.append("暂无供需信息，来发第一条吧！")
    for p in posts:
        nth = db.count_user_supply_posts(p.user_id)  # 截至现在该用户共发了几条
        cat = f"【{h(p.category)}】" if p.category else ""
        brief = p.content[:60] + ("…" if len(p.content) > 60 else "")
        photo_mark = "🖼 " if p.photo_file_id else ""
        ch_mark = {"bot": "🤖", "channel": "📣", "both": "🚀"}.get(p.channel, "🤖")
        lines.append(f"{ch_mark} {photo_mark}{cat}{h(brief)}")
        lines.append(f"    {supply.publisher_html(p)} · 第 {nth} 条 · {fmt_time(p.created_at)[:16]}")
        lines.append("")

    buttons = []
    if cats:
        # 分类筛选：每行 4 个，最多 8 个分类两行排完
        row = [("📋 全部", "sup:hall:0:")]
        for i, c in enumerate(cats[:8]):
            mark = "✅ " if c == category else ""
            row.append((f"{mark}{c}", f"sup:hall:0:{i}"))
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
    nav = []
    if page > 0:
        nav.append(("⬅️ 上一页", f"sup:hall:{page - 1}:{cats.index(category) if category in cats else ''}"))
    if (page + 1) * SUPPLY_PAGE_SIZE < total:
        nav.append(("➡️ 下一页", f"sup:hall:{page + 1}:{cats.index(category) if category in cats else ''}"))
    if nav:
        buttons.append(nav)
    buttons.append([("✍️ 发布供需", "sup:new"), ("📜 我的供需", "sup:mine")])
    buttons.append([("🏠 主菜单", "m:root")])
    return "\n".join(lines), kb(buttons)


def page_supply_mine(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    # 帖子按时间倒序，先取一批再按用户过滤，避免别人刷屏把「我的」挤掉
    posts = [p for p in db.list_supply_posts(status=None, limit=200) if p.user_id == user_id][:10]
    lines = ["<b>📜 我的供需</b>", ""]
    if not posts:
        lines.append("你还没有发布过供需")
    for p in posts:
        status = SUPPLY_STATUS_TEXT.get(p.status, p.status)
        cat = f"【{h(p.category)}】" if p.category else ""
        brief = p.content[:40] + ("…" if len(p.content) > 40 else "")
        lines.append(f"{status} {cat}{h(brief)}")
        lines.append(f"    花费 <code>{p.cost}</code> 积分 · {fmt_time(p.created_at)[:16]}")
        lines.append("")
    return "\n".join(lines), kb([
        [("✍️ 发布供需", "sup:new")],
        [("⬅️ 供需大厅", "sup:hall"), ("🏠 主菜单", "m:root")],
    ])


def page_supply_new(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """发布向导第 1 步：选择投放渠道（按钮上直接显示当前价格）。

    「停止发布」开关开启时给出暂停页；「人工审核」开启时提示先审后发。
    """
    if supply.paused() and not db.is_admin(user_id):
        return "\n".join([
            "<b>✍️ 发布供需</b>",
            "",
            "⏸ 供需发布已暂停，恢复时间请留意公告",
        ]), kb([[("⬅️ 供需大厅", "sup:hall"), ("🏠 主菜单", "m:root")]])

    s = db.get_settings()
    limit = int(s.get("supply_daily_limit", 5))
    today = supply.today_count(user_id)
    lines = [
        "<b>✍️ 发布供需</b>（第 1 步：选择投放渠道）",
        "",
        f"🤖 机器人供需：广播给全部用户，<code>{supply.price_for('bot')}</code> 积分",
    ]
    if supply.channel_configured():
        lines.append(f"📣 频道供需：发布到供需频道，<code>{supply.price_for('channel')}</code> 积分")
        lines.append(f"🚀 双发：两处同时发，<code>{supply.price_for('both')}</code> 积分（已优惠 {s.get('supply_discount', 10)} 积分）")
    lines += [
        "",
        f"📊 今日已发 <code>{today}</code> / <code>{limit}</code> 条",
    ]
    if supply.review_enabled():
        lines.append("🕐 当前为人工审核模式：提交后先扣费待审，通过立即发布，拒绝自动全额退款")
    if supply.anonymous_allowed():
        lines.append("💡 发布后可匿名，帖子不可编辑，请谨慎填写")
    else:
        lines.append("💡 帖子不可编辑，请谨慎填写")
    buttons = []
    for ch in supply.available_channels():
        buttons.append([(f"{SUPPLY_CHANNELS[ch]} · {supply.price_for(ch)}积分", f"sup:ch:{ch}")])
    buttons.append([("⬅️ 供需大厅", "sup:hall"), ("🏠 主菜单", "m:root")])
    return "\n".join(lines), kb(buttons)


def page_supply_category(payload: dict) -> tuple[str, InlineKeyboardMarkup]:
    """发布向导第 2 步：选择分类（仅当管理端配置了分类时出现）。"""
    cats = db.get_settings().get("supply_categories") or []
    lines = [
        "<b>✍️ 发布供需</b>（第 2 步：选择分类）",
        "",
        f"渠道：{SUPPLY_CHANNELS.get(payload.get('channel', 'bot'), '机器人供需')}",
        "",
        "请为这条供需选择一个分类，方便大厅里的用户筛选查看：",
    ]
    buttons = []
    for i, c in enumerate(cats):
        buttons.append([(f"📂 {c}", f"sup:cat:{i}")])
    buttons.append([("🚫 不选分类", "sup:cat:-")])
    buttons.append([("⬅️ 上一步", "sup:new"), ("❌ 取消", "sup:cancel")])
    return "\n".join(lines), kb(buttons)


def supply_content_prompt(payload: dict) -> str:
    """发布向导「输入内容」步骤的提示文案（图片可与内容同发一条）。"""
    max_len = int(db.get_settings().get("supply_max_len", 600))
    lines = [
        "<b>✍️ 发布供需</b>（填写内容）",
        "",
        f"渠道：{SUPPLY_CHANNELS.get(payload.get('channel', 'bot'), '机器人供需')}",
    ]
    if payload.get("category"):
        lines.append(f"分类：【{h(payload['category'])}】")
    lines += [
        "",
        f"✏️ 请直接发送供需内容（限 <code>{max_len}</code> 字以内）",
        "🖼 如需附图，把图片和文字放在<b>同一条消息</b>里发送即可",
        "✨ 支持排版：加粗/斜体/链接/高级会员表情，发出来什么样，帖子里就什么样",
        "",
        "⚠️ 发布前会有预览确认；帖子发布后不可编辑",
    ]
    return "\n".join(lines)


def page_supply_preview(payload: dict, user) -> tuple[str, InlineKeyboardMarkup]:
    """发布向导最后一步：预览 + 确认扣费。

    预览正文与正式发布走同一个 format_post_text（富文本 content_html），
    看到什么样发出去就什么样。人工审核开启时按钮变为「提交审核」。
    """
    channel = payload.get("channel", "bot")
    cost = supply.price_for(channel)
    cat = payload.get("category", "")
    content = payload.get("content", "")
    anonymous = payload.get("anonymous", False) if supply.anonymous_allowed() else False
    review = supply.review_enabled()
    nth = supply.nth_of_user(user.user_id)
    preview = SupplyPost(
        user_id=user.user_id, username=user.username or "",
        display_name=(user.first_name + " " + user.last_name).strip(),
        category=cat, content=content, content_html=payload.get("content_html", ""),
        channel=channel, cost=cost, anonymous=anonymous,
    )
    lines = [
        "<b>🔍 发布预览</b>",
        "",
        supply.format_post_text(preview, nth),
        "",
        "——",
        f"渠道：{SUPPLY_CHANNELS[channel]}",
        f"身份：{'🎭 匿名' if anonymous else '👤 实名（可私聊）'}",
        f"附图：{'有' if payload.get('photo_file_id') else '无'}",
        f"本次扣费：<code>{cost}</code> 积分（当前余额 <code>{user.points}</code>）",
        "",
    ]
    if review:
        lines.append("🕐 人工审核模式：提交后先扣费待审，通过立即发布，拒绝自动全额退款")
    else:
        lines.append("⚠️ 确认后立即扣费发布，帖子不可编辑")
    buttons = [[("✅ 提交审核" if review else "✅ 确认发布", "sup:go")]]
    if supply.anonymous_allowed():
        buttons.append(
            [("🎭 匿名发布" if not anonymous else "👤 实名发布", "sup:id:1" if not anonymous else "sup:id:0")]
        )
    buttons.append([("❌ 取消", "sup:cancel")])
    return "\n".join(lines), kb(buttons)


# ==================================================================
# 管理端：统计面板 + 开关/审核/帖子/分类/定价
# ==================================================================

def page_admin_supply() -> tuple[str, InlineKeyboardMarkup]:
    """供需管理主页 = 统计面板。今日按东八区自然日统计。"""
    st = supply.stats()
    s = db.get_settings()
    ch_names = {"bot": "🤖 机器人", "channel": "📣 频道", "both": "🚀 双发"}
    ch_lines = " / ".join(f"{ch_names.get(k, k)} {v} 条" for k, v in st["channels"].items()) or "暂无"
    ch_status = s.get("supply_channel_id") or "未配置"
    pending = supply.pending_count()
    lines = [
        "<b>📢 供需管理 · 统计面板</b>",
        "<i>今日按东八区自然日统计；收入为供需消耗积分</i>",
        "",
        f"📅 今日发帖：<code>{st['today_cnt']}</code> 条 · 收入 <code>{st['today_income']}</code> 积分",
        f"📦 累计发帖：<code>{st['total_cnt']}</code> 条 · 收入 <code>{st['total_income']}</code> 积分",
        f"📊 渠道分布：{ch_lines}",
        "",
        f"🎛 开关：{'⏸ 已暂停发布' if supply.paused() else '▶️ 发布中'} · "
        f"{'🕐 审核模式' if supply.review_enabled() else '⚡ 免审直发'} · "
        f"大厅 {'✅ 开放' if supply.hall_open() else '🛠 维护'} · "
        f"匿名 {'✅ 允许' if supply.anonymous_allowed() else '⛔ 禁止'}",
    ]
    if pending:
        lines.append(f"🕐 待审核：<code>{pending}</code> 条，点击「审核队列」处理")
    lines += [
        "",
        f"💰 当前定价：机器人 <code>{s.get('supply_price_bot', 10)}</code> / "
        f"频道 <code>{s.get('supply_price_channel', 10)}</code> 积分，"
        f"双发优惠 <code>{s.get('supply_discount', 10)}</code> 积分",
        f"🔒 防刷：每日限 <code>{s.get('supply_daily_limit', 5)}</code> 条 · 限 <code>{s.get('supply_max_len', 600)}</code> 字",
        f"📣 供需频道：<code>{h(ch_status)}</code>",
    ]
    buttons = [
        [("🎛 开关面板", "a:sup:sw"), (f"🕐 审核队列（{pending}）", "a:sup:rv")],
        [("📜 帖子管理", "a:sup:posts"), ("📂 分类管理", "a:sup:cats")],
        [("💰 定价与防刷", "a:sup:price"), ("📣 设置供需频道", "a:sup:chid")],
        [("⬅️ 返回", "m:admin")],
    ]
    return "\n".join(lines), kb(buttons)


# 供需开关面板：每一项都是独立开关，点一下立即翻转生效
SUPPLY_SWITCHES = [
    ("supply_paused", "⏸ 停止发布供需", "开启后全站暂停发布（用户看到暂停页；大厅浏览不受影响）"),
    ("supply_review", "🕐 人工审核模式", "开启后新帖先扣费进待审队列，通过即发布，拒绝自动全额退款"),
    ("supply_hall_open", "📢 开放大厅浏览", "关闭后用户进大厅看到维护提示（管理员不受限）"),
    ("supply_anonymous", "🎭 允许匿名发布", "关闭后发布向导隐藏匿名切换，全部实名"),
]

# 开关的「生效状态」语义：True 时算开（hall_open/anonymous 默认开，其余默认关）
SUPPLY_SWITCH_ON = {
    "supply_paused": False,
    "supply_review": False,
    "supply_hall_open": True,
    "supply_anonymous": True,
}


def page_admin_supply_sw() -> tuple[str, InlineKeyboardMarkup]:
    """供需开关面板：每个开关独立一行，按钮显示当前状态，点击即翻转。"""
    s = db.get_settings()
    lines = [
        "<b>🎛 供需开关面板</b>",
        "<i>点一下立即生效，互不影响；默认：发布中 / 免审直发 / 大厅开放 / 允许匿名</i>",
        "",
    ]
    buttons = []
    for key, name, desc in SUPPLY_SWITCHES:
        cur = bool(s.get(key, SUPPLY_SWITCH_ON[key]))
        state_text = "✅ 开" if cur else "⛔ 关"
        lines.append(f"{name}：<b>{state_text}</b>")
        lines.append(f"<i>{desc}</i>")
        lines.append("")
        buttons.append([(f"{name}：{state_text}", f"a:sup:sw:t:{key}")])
    buttons.append([("🕐 审核队列", "a:sup:rv"), ("⬅️ 供需管理", "a:sup")])
    return "\n".join(lines), kb(buttons)


def page_admin_supply_review() -> tuple[str, InlineKeyboardMarkup]:
    """人工审核队列：待审核帖子，时间正序（先提交先审）。"""
    posts = db.list_supply_posts(status="pending_review", limit=50)
    posts = list(reversed(posts))  # 列表是倒序查的，翻转为先提交先审
    lines = [
        f"<b>🕐 供需审核队列</b>（待审 {len(posts)} 条）",
        "<i>通过 = 立即按渠道分发；拒绝 = 自动全额退款给发布人</i>",
        "",
    ]
    buttons = []
    if not posts:
        lines.append("🎉 没有待审核的帖子")
    for p in posts:
        brief = strip_html_tags(p.content_html)[:20] if p.content_html else (
            p.content[:20] + ("…" if len(p.content) > 20 else "")
        )
        ch_mark = {"bot": "🤖", "channel": "📣", "both": "🚀"}.get(p.channel, "🤖")
        anon = "🎭" if p.anonymous else "👤"
        lines.append(
            f"{ch_mark} {anon} <code>{p.id}</code> · <code>{p.cost}</code> 积分\n"
            f"    {h(brief)} · {fmt_time(p.created_at)[:16]}"
        )
        buttons.append([
            (f"✅ 通过 {p.id}", f"a:sup:ok:{p.id}"),
            (f"❌ 拒绝 {p.id}", f"a:sup:no:{p.id}"),
        ])
        buttons.append([(f"⚙️ 详情 {p.id}", f"a:sup:pd:{p.id}")])
    buttons.append([("🎛 开关面板", "a:sup:sw"), ("⬅️ 供需管理", "a:sup")])
    return "\n".join(lines), kb(buttons)


def page_admin_supply_posts(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """帖子管理：含已删除，时间倒序。匿名帖在此可见真实身份。"""
    total = db.count_supply_posts(status=None)
    posts = db.list_supply_posts(status=None, limit=SUPPLY_ADMIN_PAGE_SIZE, offset=page * SUPPLY_ADMIN_PAGE_SIZE)
    lines = [f"<b>📜 供需帖子管理</b>（共 {total} 条）", ""]
    buttons = []
    if not posts:
        lines.append("暂无帖子")
    for p in posts:
        status = SUPPLY_STATUS_ICON.get(p.status, "❓")
        anon = "🎭" if p.anonymous else "👤"
        brief = p.content[:20] + ("…" if len(p.content) > 20 else "")
        lines.append(f"{status} {anon} <code>{p.id}</code> {h(brief)} · {fmt_time(p.created_at)[:16]}")
        buttons.append([(f"⚙️ {p.id} · {h(brief)}", f"a:sup:pd:{p.id}")])
    nav = []
    if page > 0:
        nav.append(("⬅️ 上一页", f"a:sup:posts:{page - 1}"))
    if (page + 1) * SUPPLY_ADMIN_PAGE_SIZE < total:
        nav.append(("➡️ 下一页", f"a:sup:posts:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("⬅️ 供需管理", "a:sup")])
    return "\n".join(lines), kb(buttons)


def page_admin_supply_post(pid: str) -> tuple[str, InlineKeyboardMarkup]:
    p = db.get_supply_post(pid)
    if not p:
        return "❌ 帖子不存在", kb([[("⬅️ 返回", "a:sup:posts")]])
    uname = f"@{p.username}" if p.username else "无"
    status = SUPPLY_STATUS_TEXT.get(p.status, p.status)
    legs = []
    if p.channel in ("bot", "both"):
        legs.append(f"机器人广播 {'✅ 已推送' if p.bot_ok else '⏳ 推送中'}")
    if p.channel in ("channel", "both"):
        legs.append(f"频道 {'✅ 已发送' if p.channel_ok else '❌ 未成功'}")
    # 帖子正文：新数据带富文本（含格式/会员表情），老数据回退转义纯文本
    body = p.content_html or h(p.content)
    lines = [
        f"<b>⚙️ 供需帖子 {p.id}</b>",
        "",
        f"状态：{status}{' · 💰 已退款' if p.refunded else ''}",
        f"发布人：<a href=\"tg://user?id={p.user_id}\">{h(p.display_name or str(p.user_id))}</a>"
        f"（{uname} · ID <code>{p.user_id}</code>）",
        f"前台身份：{'🎭 匿名' if p.anonymous else '👤 实名'}",
        f"渠道：{SUPPLY_CHANNELS.get(p.channel, p.channel)} · 实付 <code>{p.cost}</code> 积分",
        f"分发：{'；'.join(legs) if legs else '—'}",
        f"发布时间：{fmt_time(p.created_at)}",
        "",
        f"{'【' + h(p.category) + '】' if p.category else ''}{body}",
    ]
    buttons = []
    if p.status == "pending_review":
        buttons.append([
            ("✅ 通过并发布", f"a:sup:ok:{p.id}"),
            ("❌ 拒绝并退款", f"a:sup:no:{p.id}"),
        ])
    if p.status == "active":
        buttons.append([("🗑 删除（不退款）", f"a:sup:pdel:{p.id}")])
        if not p.refunded and p.cost > 0:
            buttons.append([("🗑💰 删除并退款", f"a:sup:pdelr:{p.id}")])
    buttons.append([("💬 私聊发布人", f"tg://user?id={p.user_id}")])
    buttons.append([("⬅️ 帖子管理", "a:sup:posts"), ("📢 供需管理", "a:sup")])
    return "\n".join(lines), kb(buttons)


def page_admin_supply_pdel(pid: str, refund: bool) -> tuple[str, InlineKeyboardMarkup]:
    p = db.get_supply_post(pid)
    if not p:
        return "❌ 帖子不存在", kb([[("⬅️ 返回", "a:sup:posts")]])
    if refund:
        text = (
            f"🗑💰 <b>删除并退款？</b>\n\n"
            f"帖子：<code>{p.id}</code>\n"
            f"将退还 <code>{p.cost}</code> 积分给发布人（ID <code>{p.user_id}</code>）\n\n"
            f"⚠️ 删除后帖子从大厅消失，不可恢复"
        )
    else:
        text = (
            f"🗑 <b>确认删除（不退款）？</b>\n\n"
            f"帖子：<code>{p.id}</code>\n"
            f"发布人已付 <code>{p.cost}</code> 积分，<b>不会</b>退还\n\n"
            f"⚠️ 删除后帖子从大厅消失，不可恢复"
        )
    buttons = [
        [("✅ 确认删除", f"a:sup:pdok:{pid}:{1 if refund else 0}")],
        [("❌ 取消", f"a:sup:pd:{pid}")],
    ]
    return text, kb(buttons)


def page_admin_supply_cats() -> tuple[str, InlineKeyboardMarkup]:
    cats = db.get_settings().get("supply_categories") or []
    lines = [
        "<b>📂 供需分类管理</b>",
        "<i>配置后，发布向导会出现「选分类」步骤，大厅可按分类筛选；留空则关闭分类</i>",
        "",
    ]
    buttons = []
    if not cats:
        lines.append("暂无分类（当前发布无需选分类）")
    else:
        for i, c in enumerate(cats):
            lines.append(f"{i + 1}. {h(c)}")
            buttons.append([(f"🗑 删除「{h(c)}」", f"a:sup:catdel:{i}")])
    buttons.append([("➕ 添加分类", "a:sup:catadd")])
    buttons.append([("⬅️ 供需管理", "a:sup")])
    return "\n".join(lines), kb(buttons)


def page_admin_supply_price() -> tuple[str, InlineKeyboardMarkup]:
    s = db.get_settings()
    p_bot = int(s.get("supply_price_bot", 10))
    p_ch = int(s.get("supply_price_channel", 10))
    disc = int(s.get("supply_discount", 10))
    lines = [
        "<b>💰 供需定价与防刷</b>",
        "",
        f"🤖 机器人供需：<code>{p_bot}</code> 积分",
        f"📣 频道供需：<code>{p_ch}</code> 积分",
        f"🚀 双发优惠：减 <code>{disc}</code> 积分（双发实收 <code>{max(1, p_bot + p_ch - disc)}</code>）",
        f"🔢 每日发布上限：<code>{s.get('supply_daily_limit', 5)}</code> 条/人（0 = 不限）",
        f"📏 内容字数上限：<code>{s.get('supply_max_len', 600)}</code> 字",
        "",
        "<i>改动即时生效，按钮上的价格随改随变</i>",
    ]
    buttons = [
        [("🤖 改机器人价", "a:sup:set:supply_price_bot"), ("📣 改频道价", "a:sup:set:supply_price_channel")],
        [("🚀 改双发优惠", "a:sup:set:supply_discount"), ("🔢 改每日上限", "a:sup:set:supply_daily_limit")],
        [("📏 改字数上限", "a:sup:set:supply_max_len")],
        [("⬅️ 供需管理", "a:sup")],
    ]
    return "\n".join(lines), kb(buttons)
