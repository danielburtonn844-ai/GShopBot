# -*- coding: utf-8 -*-
"""
富文本与自定义表情工具（v2.3.0 新增）
====================================
统一解决「会员图标没解析 / HTML 没解析」：

1. 自定义 Emoji（高级会员表情）图标库：
   - 按钮：InlineKeyboardButton(icon_custom_emoji_id=...)
   - 正文：<tg-emoji emoji-id="...">💎</tg-emoji>（需机器人具备自定义表情权限，
     即 Fragment 收藏品用户名；无权限时安全发送会自动降级，消息不丢）

2. 富文本收发规范：
   - 入库：用 message.text_html / caption_html（由 Telegram 实体生成，
     加粗/链接/自定义表情原样保留），不要再只存 message.text
   - 发送：作者本人写的富文本直接 parse_mode="HTML" 发出，不再二次转义；
     转义只用于「不可信纯展示数据」（用户名、订单号、用户备注等）

3. 安全发送 send_html / strip_html_tags：
   HTML 发送失败（表情权限失效、标签异常、旧数据含特殊字符）时，
   自动降级为纯文本重发，保证消息必达。
"""
from __future__ import annotations

import html as _html
import logging
import re

from telegram.error import BadRequest

logger = logging.getLogger(__name__)

# ==================================================================
# 自定义 Emoji 图标库（「小图标们」表情包，与按钮图标同一套）
# ==================================================================
ICON_DIAMOND = "6168012369260710038"   # 💎 钻石 - 会员/积分/余额
ICON_FIRE = "6170241083625178923"      # 🔥 火焰 - 热门/推荐/首页
ICON_HEART = "6168102421840005417"     # ❤️ 红心 - 收藏/好评
ICON_PARTY = "6167955813131356996"     # 🎉 庆祝 - 活动/优惠/社区
ICON_ZAP = "6167954971317765944"       # ⚡ 闪电 - 充值/快速/到账
ICON_STAR = "6167888987735198456"      # ⭐ 星星 - 评分/等级/排行
ICON_KEY = "6167924756222838982"       # 🔑 钥匙 - 管理/权限/设置
ICON_PHONE = "6167895584804964236"     # 📞 电话 - 客服/联系/帮助
ICON_CHECK = "6167787879910082314"     # ✅ 对勾 - 确认/完成/购买
ICON_HAT = "6287524786369206025"       # 🎩 帽子 - 管理员/后台
ICON_MAIL = "6169937871818982998"      # 📬 邮箱 - 订单/消息/通知
ICON_WRITE = "6168212003635595414"     # ✍️ 写字 - 编辑/提交/报单
ICON_MOON = "6168027058048862670"      # 🌙 月亮 - 个人中心/我的
ICON_GIFT = "6168107489901414679"      # 🌼 小花 - 商品/上新

# ==================================================================
# 星耀图标库（v2.6.0 抢单模块）：两套高级会员表情
# A 套（528*/525*/526*/527*/529* 号段）+ B 套「𝐃𝐞 @rsssa」（587* 号段，197 枚）
# 正文用 tg(icon, fallback) 内联，按钮用 icon_custom_emoji_id；无权限自动降级 fallback。
# ==================================================================
ICON_X_GRAB = "5879847998166405219"        # ⚡ 闪电 - 抢单/待抢（B 套）
ICON_X_NINJA = "5877516548184215512"       # 🥷 忍者 - 接单员（B 套）
ICON_X_MONEY = "5877661387366338545"       # 💰 钱袋 - 佣金/余额（B 套）
ICON_X_CHECK = "5879508399397278528"       # ✅ 对勾 - 完单/到账（B 套）
ICON_X_ALARM = "5879981992556105945"       # ⏰ 闹钟 - 时限/超时（B 套）
ICON_X_WARN = "5877455353490182825"        # ⚠️ 警告 - 预警/风控（B 套）
ICON_X_LOCK = "5879645864120553856"        # 🔒 锁 - 冻结中（B 套）
ICON_X_BELL = "5877688226616971725"        # 🔔 铃铛 - 通知/新单（B 套）
ICON_X_SPEAK = "5877491006013706139"       # 🗣️ 喊话 - 申诉/仲裁（B 套）
ICON_X_TOP = "5877222514723131451"         # 🔝 置顶 - 排行榜（B 套）
ICON_X_FIRE = "5877618652441743468"        # 🔥 火焰 - 热单/爆单（B 套）
ICON_X_SIGNAL = "5877608365995069781"      # 📶 信号 - 数据/统计（B 套）
ICON_X_HEARTB = "5879705834748907917"      # 🩵 心 - 好评（B 套）
ICON_X_CROSS = "5879798928165048370"       # ✝️ 十字 - 纪念（B 套）
ICON_X_CRY = "5877576170920219442"         # 🥺 拜托 - 求助/申诉补充（B 套）

ICON_A_TARGET = "5256131095094652290"      # 🎯 靶心 - 进行中（A 套）
ICON_A_GEAR = "5267334530171169409"        # ⚙️ 齿轮 - 抢单设置（A 套）
ICON_A_KEY = "5278573677900752088"         # 🔑 钥匙 - 权限（A 套）
ICON_A_UNLOCK = "5291873529464122510"      # 🔓 解锁 - 佣金解冻（A 套）
ICON_A_MAIL = "5287533898803211359"        # 📫 信箱 - 回单/派单（A 套）
ICON_A_LETTER = "5285184156555306745"      # 💌 情书 - 提现申请（A 套）
ICON_A_CAL = "5287606810168028257"         # 🗓 日历 - 明细/记录（A 套）
ICON_A_STAR = "5289944036881230584"        # ⭐️ 星星 - 等级/评分（A 套）
ICON_A_CHEERS = "5260567255145539253"      # 🥂 碰杯 - 完单庆祝（A 套）
ICON_A_HANDS = "5285338659413846416"       # 🫶 比心 - 合作愉快（A 套）
ICON_A_RADAR = "5256134032852278918"       # 📡 雷达 - 监听/扫描（A 套）
ICON_A_PUZZLE = "5265120027853481187"      # 🧩 拼图 - 扩展功能（A 套）
ICON_A_BOMB = "5280569974404966639"        # 💣 炸弹 - 黑名单/封禁（A 套）
ICON_A_PILL = "5305715161087110779"        # 💊 药丸 - 恢复/解封（A 套）
ICON_A_WRITE = "5258500400918587241"       # ✍️ 写字 - 填写/提交（A 套）

# 关键词 → 图标 ID 映射（按按钮文字包含的关键词匹配，前面的优先）
ICON_MAP = [
    # 抢单模块关键词优先匹配（星耀图标库）
    ("抢单", ICON_X_GRAB), ("待抢", ICON_X_GRAB), ("派单", ICON_A_MAIL),
    ("接单员", ICON_X_NINJA), ("接单", ICON_X_NINJA), ("打手", ICON_X_NINJA),
    ("佣金", ICON_X_MONEY), ("提现", ICON_A_LETTER), ("余额", ICON_X_MONEY),
    ("回单", ICON_A_MAIL), ("完单", ICON_X_CHECK), ("申诉", ICON_X_SPEAK),
    ("仲裁", ICON_X_SPEAK), ("排行", ICON_X_TOP), ("榜", ICON_X_TOP),
    ("冻结", ICON_X_LOCK), ("解冻", ICON_A_UNLOCK), ("时限", ICON_X_ALARM),
    ("超时", ICON_X_WARN), ("预警", ICON_X_WARN), ("冷静期", ICON_X_ALARM),
    ("黑名单", ICON_A_BOMB), ("封禁", ICON_A_BOMB), ("解封", ICON_A_PILL),
    ("统计", ICON_X_SIGNAL), ("数据", ICON_X_SIGNAL), ("热单", ICON_X_FIRE),
    ("商品", ICON_GIFT), ("上新", ICON_GIFT), ("购买", ICON_CHECK),
    ("订单", ICON_MAIL), ("回单", ICON_MAIL),
    ("充值", ICON_ZAP), ("余额", ICON_DIAMOND), ("积分", ICON_DIAMOND),
    ("会员", ICON_DIAMOND), ("钻石", ICON_DIAMOND),
    ("管理", ICON_HAT), ("后台", ICON_HAT), ("面板", ICON_HAT), ("设置", ICON_KEY),
    ("管理员", ICON_KEY), ("权限", ICON_KEY),
    ("客服", ICON_PHONE), ("联系", ICON_PHONE), ("帮助", ICON_PHONE), ("反馈", ICON_PHONE),
    ("报单", ICON_WRITE), ("提交", ICON_WRITE), ("编辑", ICON_WRITE), ("发布", ICON_WRITE),
    ("供需", ICON_MAIL), ("大厅", ICON_MAIL), ("发帖", ICON_WRITE),
    ("匿名", ICON_HAT), ("实名", ICON_CHECK),
    ("社区", ICON_PARTY), ("活动", ICON_PARTY), ("优惠", ICON_PARTY), ("广播", ICON_PARTY),
    ("我的", ICON_MOON), ("个人", ICON_MOON), ("主页", ICON_MOON),
    ("排行", ICON_STAR), ("评分", ICON_STAR), ("统计", ICON_STAR),
    ("热门", ICON_FIRE), ("推荐", ICON_FIRE), ("首页", ICON_FIRE),
    ("频道", ICON_PARTY), ("群", ICON_PARTY),
    ("确认", ICON_CHECK), ("完成", ICON_CHECK), ("成功", ICON_CHECK),
    ("审核", ICON_CHECK), ("开关", ICON_KEY),
    ("返回", ICON_MOON), ("⬅️", ICON_MOON),
    ("好评", ICON_HEART), ("收藏", ICON_HEART),
]
DEFAULT_ICON = ICON_FIRE

_CLEAN_RE = re.compile(
    r"^[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    r"\U0001F1E6-\U0001F1FF\u2190-\u21FF\u2300-\u23FF\u2B50\uFE0F\u200D\s]+"
)


def match_icon(label: str) -> str:
    """按按钮文字匹配自定义 emoji 图标 ID。"""
    for kw, icon in ICON_MAP:
        if kw in label:
            return icon
    return DEFAULT_ICON


def clean_label(label: str) -> str:
    """剥掉按钮文字开头的 emoji 与空格，避免与自定义图标重复显示。"""
    cleaned = _CLEAN_RE.sub("", label)
    return cleaned.strip() or label.strip()


def tg(icon_id: str, fallback: str) -> str:
    """正文内联高级会员表情 <tg-emoji> 标签；fallback 为无权限时的替代字符。"""
    return f'<tg-emoji emoji-id="{icon_id}">{fallback}</tg-emoji>'


def __getattr__(name: str):
    """兼容层（PEP 562）：其他模块若引用了本库未定义的 TG_* / ICON_* 名字
    （例如线上旧版页面模块），自动以默认图标生成，避免 ImportError 导致全站起不来。"""
    if name.startswith("TG_"):
        logger.warning("richtext 未定义 %s，按默认图标降级", name)
        return tg(DEFAULT_ICON, "🔹")
    if name.startswith("ICON_"):
        logger.warning("richtext 未定义 %s，按默认图标降级", name)
        return DEFAULT_ICON
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# 常用内联表情（直接拼进 HTML 文案即可；发送失败会自动降级为 fallback 字符）
TG_DIAMOND = tg(ICON_DIAMOND, "💎")
TG_FIRE = tg(ICON_FIRE, "🔥")
TG_ZAP = tg(ICON_ZAP, "⚡")
TG_STAR = tg(ICON_STAR, "⭐")
TG_CHECK = tg(ICON_CHECK, "✅")
TG_MAIL = tg(ICON_MAIL, "📬")
TG_WRITE = tg(ICON_WRITE, "✍️")
TG_HAT = tg(ICON_HAT, "🎩")
TG_PARTY = tg(ICON_PARTY, "🎉")
TG_PHONE = tg(ICON_PHONE, "📞")
TG_HEART = tg(ICON_HEART, "❤️")
TG_GIFT = tg(ICON_GIFT, "🌼")
TG_MOON = tg(ICON_MOON, "🌙")
TG_KEY = tg(ICON_KEY, "🔑")

# ==================================================================
# 旧版本自定义图标映射（全部指向星耀会员表情，杜绝默认图标降级）
# 线上旧代码引用的 TG_* 名字 → 星耀 B 套 / A 套 ID
# ==================================================================
TG_CHANNEL = tg("5877491006013706139", "📢")    # 频道/公告 → 🗣️ 星耀
TG_CLOCK = tg("5879981992556105945", "⏰")      # 时钟/限时 → ⏰ 星耀
TG_FORBID = tg("5877438285290147517", "🚫")     # 禁止/限制 → 🚫 星耀
TG_JD = tg("5877516548184215512", "🥷")         # 接单 → 🥷 星耀
TG_JOIN = tg("5877539796842188140", "🫶")       # 加入/关注 → 🫶 星耀
TG_LIGHT = tg("5879738528039965401", "💡")      # 提示/灯泡 → 💡 星耀
TG_LOCK = tg("5879645864120553856", "🔒")       # 锁/冻结 → 🔒 星耀
TG_MEITUAN = tg("5879830371620622775", "🛵")    # 美团/外卖 → 🌟 星耀
TG_PROFILE = tg("5877752921709351128", "👤")    # 个人资料 → 👤 星耀
TG_QUERY = tg("5877682969577001481", "🔍")      # 查询 → 👁 星耀

# 星耀内联表情（v2.6.0 抢单模块专用，正文直接拼 HTML）
TG_X_GRAB = tg(ICON_X_GRAB, "⚡")
TG_X_NINJA = tg(ICON_X_NINJA, "🥷")
TG_X_MONEY = tg(ICON_X_MONEY, "💰")
TG_X_CHECK = tg(ICON_X_CHECK, "✅")
TG_X_ALARM = tg(ICON_X_ALARM, "⏰")
TG_X_WARN = tg(ICON_X_WARN, "⚠️")
TG_X_LOCK = tg(ICON_X_LOCK, "🔒")
TG_X_BELL = tg(ICON_X_BELL, "🔔")
TG_X_SPEAK = tg(ICON_X_SPEAK, "🗣️")
TG_X_TOP = tg(ICON_X_TOP, "🔝")
TG_X_FIRE = tg(ICON_X_FIRE, "🔥")
TG_X_SIGNAL = tg(ICON_X_SIGNAL, "📶")
TG_A_TARGET = tg(ICON_A_TARGET, "🎯")
TG_A_GEAR = tg(ICON_A_GEAR, "⚙️")
TG_A_KEY = tg(ICON_A_KEY, "🔑")
TG_A_UNLOCK = tg(ICON_A_UNLOCK, "🔓")
TG_A_MAIL = tg(ICON_A_MAIL, "📫")
TG_A_LETTER = tg(ICON_A_LETTER, "💌")
TG_A_CAL = tg(ICON_A_CAL, "🗓")
TG_A_STAR = tg(ICON_A_STAR, "⭐️")
TG_A_CHEERS = tg(ICON_A_CHEERS, "🥂")
TG_A_HANDS = tg(ICON_A_HANDS, "🫶")
TG_A_RADAR = tg(ICON_A_RADAR, "📡")
TG_A_BOMB = tg(ICON_A_BOMB, "💣")
TG_A_WRITE = tg(ICON_A_WRITE, "✍️")

# ==================================================================
# HTML 降级
# ==================================================================
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html_tags(text: str | None) -> str:
    """把 HTML 文本降级为纯文本：去标签、保留 tg-emoji 的回退字符、反转义。"""
    if not text:
        return ""
    t = re.sub(r"<tg-emoji[^>]*>", "", str(text))
    t = t.replace("</tg-emoji>", "")
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</blockquote>", "\n", t, flags=re.I)
    t = _TAG_RE.sub("", t)
    return _html.unescape(t).strip()


def rich_from_message(message) -> tuple[str, str]:
    """从一条消息提取 (纯文本, HTML)。

    HTML 由 Telegram 实体生成（text_html/caption_html），用户加的加粗、链接、
    自定义表情都会原样保留为合法 HTML；纯文本用于字数校验与列表摘要。
    """
    plain = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    try:
        html_text = (
            getattr(message, "text_html", None)
            or getattr(message, "caption_html", None)
            or ""
        ).strip()
    except Exception:
        html_text = ""
    if not html_text and plain:
        html_text = _html.escape(plain)
    return plain, html_text


def is_not_modified(exc: Exception) -> bool:
    """edit_message_text 的「内容没变」错误：不算失败，直接忽略。"""
    return "message is not modified" in str(exc).lower()


# ==================================================================
# 安全发送：HTML 失败自动降级纯文本，保证消息必达
# ==================================================================
async def send_html(bot, chat_id, text: str, reply_markup=None, photo: str = ""):
    """以 HTML 发送（可带图）；BadRequest（表情权限失效/标签异常）时降级纯文本重发。

    其他异常（用户拉黑、网络等）照常抛出，由调用方处理。返回 Message。
    """
    try:
        if photo:
            return await bot.send_photo(
                chat_id=chat_id, photo=photo, caption=text,
                parse_mode="HTML", reply_markup=reply_markup,
            )
        return await bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", reply_markup=reply_markup,
        )
    except BadRequest as exc:
        if is_not_modified(exc):
            raise
        logger.warning("HTML 发送失败（chat_id=%s），降级纯文本：%s", chat_id, exc)
        plain = strip_html_tags(text)
        if photo:
            return await bot.send_photo(
                chat_id=chat_id, photo=photo, caption=plain,
                reply_markup=reply_markup,
            )
        return await bot.send_message(
            chat_id=chat_id, text=plain, reply_markup=reply_markup,
        )
