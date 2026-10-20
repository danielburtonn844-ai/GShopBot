"""接单员端页面（v2.6.0 新增）：接单中心/进行中/完单记录/钱包/提现/排行/申诉。

设计风格与主站一致：标题加粗、副标题斜体、数据 <code> 等宽体、
星耀会员表情内联、按钮自动匹配图标。匿名原则贯穿始终——
接单员看到的只有订单号/项目/需求/佣金，永远没有买家信息。
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .models import fmt_time
from .richtext import (
    TG_A_CAL,
    TG_A_KEY,
    TG_A_MAIL,
    TG_A_STAR,
    TG_A_UNLOCK,
    TG_X_ALARM,
    TG_X_CHECK,
    TG_X_DIAMOND,
    TG_X_GRAB,
    TG_X_LOCK,
    TG_X_MONEY,
    TG_X_NINJA,
    TG_X_SPEAK,
    TG_X_TOP,
)
from .runtime import db, grab
from .ui import h, kb

# 接单员状态展示
_GSTATUS = {
    "active": "🟢 在岗",
    "rest": "🟡 请假",
    "suspended": "🔴 停权",
    "blacklist": "⛔ 黑名单",
}


def _grabber_or_none(user_id: int):
    return db.get_grabber(user_id)


def page_grab_home(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """接单员中心首页：个人数据卡 + 功能入口。"""
    g = _grabber_or_none(user_id)
    if not g:
        return (
            f"{TG_X_NINJA} <b>接单员中心</b>\n\n"
            "你还不是接单员。\n"
            "想成为接单员赚佣金？请联系管理员开通。",
            kb([[("🏠 主菜单", "m:root")]]),
        )

    doing = db.count_grab_orders("grabbed", grabber_id=user_id)
    lines = [
        f"{TG_X_NINJA} <b>接单员中心</b>",
        "━━━━━━━━━━━━━━",
        f"🪪 编号：<code>{g.display()}</code>",
        f"📶 状态：{_GSTATUS.get(g.status, g.status)}",
        f"{TG_X_MONEY} 可提现：<code>{g.balance_available}</code> 积分（≈ <b>{grab.points_to_usdt(g.balance_available):.2f}</b> U）",
        f"{TG_X_LOCK} 冻结中：<code>{g.balance_frozen}</code> 积分（≈ {grab.points_to_usdt(g.balance_frozen):.2f} U）",
        "━━━━━━━━━━━━━━",
        f"{TG_X_CHECK} 完单 <code>{g.completed_orders}</code> · "
        f"{TG_X_ALARM} 超时 <code>{g.timeout_orders}</code> · "
        f"👋 放弃 <code>{g.giveup_orders}</code>",
        f"{TG_A_STAR} 准时率 <code>{g.on_time_rate}%</code> · 累计佣金 <code>{g.total_commission}</code>（≈ {grab.points_to_usdt(g.total_commission):.2f} U）",
    ]
    if g.cooldown_until:
        lines.append(f"⏳ 冷静期至：<code>{fmt_time(g.cooldown_until)}</code>")
    if doing:
        lines.append(f"{TG_X_GRAB} 进行中订单：<code>{doing}</code> 笔")
    # 入群状态：已进群公示 / 未进群给专属链接入口
    if g.joined_group:
        lines.append(f"{TG_X_CHECK} 已加入接单群（专属链接已作废）")
    elif g.invite_link:
        lines.append(f"{TG_A_KEY} 你还没进接单群，点下方专属链接进群（限用一次）")
    else:
        lines.append(f"{TG_A_KEY} 你还没进接单群，点下方按钮领取专属入群链接")
    lines.append("━━━━━━━━━━━━━━")

    buttons = [
        [(f"⚡ 进行中的单（{doing}）", "gb:doing"), ("✅ 完单记录", "gb:done")],
        [("💰 我的钱包", "gb:money"), ("💌 申请提现", "gb:wd")],
        [("🔝 接单排行", "gb:rank"), ("🗣 我的申诉", "gb:aps")],
        [("🏠 主菜单", "m:root")],
    ]
    markup = kb(buttons)
    if not g.joined_group:
        rows = [list(r) for r in markup.inline_keyboard]
        if g.invite_link:
            rows.insert(0, [InlineKeyboardButton("🔗 点我进入接单群（专属链接）", url=g.invite_link)])
        else:
            rows.insert(0, [InlineKeyboardButton("🔗 领取我的专属入群链接", callback_data="gb:inv")])
        markup = InlineKeyboardMarkup(rows)
    return "\n".join(lines), markup


def page_grab_doing(user_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """进行中订单：每单一张卡（回单/放弃按钮）。"""
    g = _grabber_or_none(user_id)
    if not g:
        return page_grab_home(user_id)
    size = 5
    orders = db.list_grab_orders("grabbed", grabber_id=user_id, limit=size, offset=page * size)
    total = db.count_grab_orders("grabbed", grabber_id=user_id)

    lines = [
        f"{TG_X_GRAB} <b>进行中的订单</b>（共 <code>{total}</code> 笔）",
        "<i>点击订单进入回单界面</i>",
        "",
    ]
    buttons = []
    if not orders:
        lines.append("当前没有进行中的订单")
    else:
        for o in orders:
            lines.append(
                f"<code>#{o.id}</code> {h(o.product_name)} · "
                f"{TG_X_MONEY} <code>{o.commission}</code> 积分\n"
                f"{TG_X_ALARM} 最晚回单 <code>{fmt_time(o.deadline)}</code>"
            )
            buttons.append([(f"📫 回单 #{o.id}", f"gb:r:{o.id}")])
            buttons.append([("😔 放弃", f"gb:q:{o.id}"), ("🗣 申诉", f"gb:ap:{o.id}")])
    nav = []
    if page > 0:
        nav.append(("⬅️ 上一页", f"gb:doing:{page - 1}"))
    if (page + 1) * size < total:
        nav.append(("➡️ 下一页", f"gb:doing:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("⬅️ 接单中心", "gb:m")])
    return "\n".join(lines), kb(buttons)


def page_grab_done(user_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """完单记录。"""
    g = _grabber_or_none(user_id)
    if not g:
        return page_grab_home(user_id)
    size = 8
    orders = db.list_grab_orders("done", grabber_id=user_id, limit=size, offset=page * size)
    total = db.count_grab_orders("done", grabber_id=user_id)

    lines = [
        f"{TG_X_CHECK} <b>完单记录</b>（共 <code>{total}</code> 笔）",
        "",
    ]
    buttons = []
    if not orders:
        lines.append("暂无完单记录")
    else:
        for o in orders:
            lines.append(
                f"<code>#{o.id}</code> {h(o.product_name)} · "
                f"+<code>{o.commission}</code> 积分 · <code>{fmt_time(o.completed_at)[:16]}</code>"
            )
    nav = []
    if page > 0:
        nav.append(("⬅️ 上一页", f"gb:done:{page - 1}"))
    if (page + 1) * size < total:
        nav.append(("➡️ 下一页", f"gb:done:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("⬅️ 接单中心", "gb:m")])
    return "\n".join(lines), kb(buttons)


def page_grab_money(user_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """我的钱包：余额三栏 + 佣金流水。"""
    g = _grabber_or_none(user_id)
    if not g:
        return page_grab_home(user_id)
    size = 8
    cms = db.list_grab_commissions(grabber_id=user_id, limit=size, offset=page * size)
    total = db.count_grab_commissions(grabber_id=user_id)

    status_text = {"frozen": "🔒 冻结中", "released": "✅ 已到账", "clawed": "💣 已追回"}
    lines = [
        f"{TG_X_MONEY} <b>我的钱包</b>",
        "━━━━━━━━━━━━━━",
        f"{TG_A_UNLOCK} 可提现：<code>{g.balance_available}</code> 积分（≈ <b>{grab.points_to_usdt(g.balance_available):.2f}</b> U）",
        f"{TG_X_LOCK} 冻结中：<code>{g.balance_frozen}</code> 积分（≈ {grab.points_to_usdt(g.balance_frozen):.2f} U）",
        f"{TG_A_STAR} 累计佣金：<code>{g.total_commission}</code> 积分（≈ {grab.points_to_usdt(g.total_commission):.2f} U）",
        f"{TG_X_DIAMOND} 当前汇率：<code>{grab.points_per_usdt()}</code> 积分 = 1 U",
        "━━━━━━━━━━━━━━",
        f"{TG_A_CAL} <b>佣金流水</b>（共 <code>{total}</code> 条）",
        "",
    ]
    if not cms:
        lines.append("暂无佣金记录")
    else:
        for c in cms:
            until = f" · 解冻 {fmt_time(c.frozen_until)[:16]}" if c.status == "frozen" and c.frozen_until else ""
            lines.append(
                f"{status_text.get(c.status, c.status)} <code>#{c.order_id}</code> "
                f"+<code>{c.amount}</code> 积分{until}"
            )
    buttons = [[("💌 申请提现", "gb:wd")]]
    nav = []
    if page > 0:
        nav.append(("⬅️ 上一页", f"gb:money:{page - 1}"))
    if (page + 1) * size < total:
        nav.append(("➡️ 下一页", f"gb:money:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("⬅️ 接单中心", "gb:m")])
    return "\n".join(lines), kb(buttons)


def page_grab_withdraw(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """申请提现页：余额 + 规则 + 历史。"""
    g = _grabber_or_none(user_id)
    if not g:
        return page_grab_home(user_id)
    s = db.get_settings()
    min_amount = int(s.get("grab_withdraw_min", 100) or 0)
    wds = db.list_withdrawals(grabber_id=user_id, limit=5)

    wd_status = {"pending": "🕐 待打款", "paid": "✅ 已打款", "rejected": "❌ 已拒绝"}
    lines = [
        f"{TG_A_MAIL} <b>申请提现 · 打款到 OK 钱包</b>",
        "━━━━━━━━━━━━━━",
        f"{TG_A_UNLOCK} 可提现余额：<code>{g.balance_available}</code> 积分（≈ <b>{grab.points_to_usdt(g.balance_available):.2f}</b> U）",
        f"{TG_X_DIAMOND} 当前汇率：<code>{grab.points_per_usdt()}</code> 积分 = 1 U（自动换算）",
        f"📏 最低提现：<code>{min_amount}</code> 积分 · 一次一笔，打款后可再提",
        "━━━━━━━━━━━━━━",
        "点下方按钮，按提示一行发送：<code>积分 OK钱包ID</code>",
        f"例：<code>500 12345678</code>（即提 500 积分 ≈ {grab.points_to_usdt(500):.2f} U，打到 OK 钱包 12345678）",
    ]
    if wds:
        lines += ["", f"{TG_A_CAL} <b>最近提现</b>"]
        for w in wds:
            usdt = w.amount_usdt or grab.points_to_usdt(w.amount)
            lines.append(
                f"{wd_status.get(w.status, w.status)} <code>{w.id}</code> "
                f"<code>{w.amount}</code> 积分 ≈ <code>{usdt:.2f}</code> U · <code>{fmt_time(w.created_at)[:16]}</code>"
            )
    buttons = [[("✍️ 填写提现信息", "gb:wdnew")]]
    buttons.append([("⬅️ 钱包", "gb:money"), ("⬅️ 接单中心", "gb:m")])
    return "\n".join(lines), kb(buttons)


def page_grab_rank(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """接单排行：完单榜 + 佣金榜（公示只显示编号，匿名）。"""
    grabbers = [g for g in db.list_grabbers(limit=200) if g.status != "blacklist"]
    by_done = sorted(grabbers, key=lambda x: (x.completed_orders, x.total_commission), reverse=True)[:10]
    by_money = sorted(grabbers, key=lambda x: x.total_commission, reverse=True)[:10]

    lines = [
        f"{TG_X_TOP} <b>接单排行榜</b>",
        "",
        f"{TG_X_CHECK} <b>完单榜 TOP10</b>",
    ]
    if not by_done:
        lines.append("暂无数据")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, g in enumerate(by_done):
            medal = medals[i] if i < 3 else f"<code>{i + 1}.</code>"
            lines.append(
                f"{medal} {h(g.display())} · 完单 <code>{g.completed_orders}</code> · "
                f"准时率 <code>{g.on_time_rate}%</code>"
            )
    lines += ["", f"{TG_X_MONEY} <b>佣金榜 TOP10</b>"]
    if not by_money:
        lines.append("暂无数据")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, g in enumerate(by_money):
            medal = medals[i] if i < 3 else f"<code>{i + 1}.</code>"
            lines.append(f"{medal} {h(g.display())} · 累计 <code>{g.total_commission}</code> 积分")
    return "\n".join(lines), kb([[("⬅️ 接单中心", "gb:m")]])


def page_grab_appeals(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """我的申诉记录 + 发起入口。"""
    g = _grabber_or_none(user_id)
    if not g:
        return page_grab_home(user_id)
    appeals = db.list_grab_appeals(grabber_id=user_id, limit=8)
    ap_status = {"pending": "🕐 待裁定", "approved": "✅ 已支持", "rejected": "❌ 已驳回"}
    lines = [
        f"{TG_X_SPEAK} <b>我的申诉</b>",
        "<i>对超时/判罚有异议？提交申诉由管理员裁定</i>",
        "",
    ]
    if not appeals:
        lines.append("暂无申诉记录")
    else:
        for a in appeals:
            lines.append(
                f"{ap_status.get(a.status, a.status)} <code>#{a.id}</code> "
                f"订单 <code>#{h(a.order_id or '-')}</code>\n"
                f"<blockquote>{h(a.content[:80])}</blockquote>"
            )
            if a.reply:
                lines.append(f"📢 裁定：{h(a.reply[:80])}")
    buttons = [[("⬅️ 接单中心", "gb:m")]]
    return "\n".join(lines), kb(buttons)
