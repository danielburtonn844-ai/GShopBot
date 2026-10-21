"""UI 共用工具（v2.4.0 模块化拆分）：键盘构造、HTML 转义、安全发送、状态管理。

所有页面与回调模块共用的基础设施集中在此，页面模块只关心内容组装。
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.error import BadRequest

from .models import PRODUCT_STATUS, TAGS, escape_html
from .richtext import clean_label, is_not_modified, match_icon, strip_html_tags
from .runtime import db

# Telegram 图片说明（caption）上限 1024 字符。超出时改用纯文字消息，
# 避免「带图 + 超长正文」直接编辑失败导致整页打不开。
CAPTION_LIMIT = 1024

# ---------- 自定义 Emoji 图标（高级会员表情按钮左侧图标） ----------
# 图标库在 examples/richtext.py，按钮经 kb() 统一自动匹配图标；正文内联表情用 TG_*。


def kb(buttons: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """buttons: [[(label, callback_or_url), ...], ...]，url 以 http 或 tg:// 开头。
    自动为每个按钮按文字匹配自定义 emoji 图标（需 Premium 机器人），
    并剥掉文字里原有的 emoji，避免双图标。"""
    rows = []
    for row in buttons:
        r = []
        for label, data in row:
            icon = match_icon(label)
            text = clean_label(label)
            if data.startswith("http") or data.startswith("tg://"):
                r.append(InlineKeyboardButton(text, url=data, icon_custom_emoji_id=icon))
            else:
                r.append(InlineKeyboardButton(text, callback_data=data, icon_custom_emoji_id=icon))
        rows.append(r)
    return InlineKeyboardMarkup(rows)


def h(text) -> str:
    return escape_html(str(text) if text is not None else "")


def order_status_text(s: str) -> str:
    return {
        "pending": "⏳ 待处理",
        "processing": "🔄 处理中",
        "completed": "✅ 已完成",
        "cancelled": "❌ 已取消",
        "refunded": "💰 已退款",
    }.get(s, s)


def product_status_text(s: str) -> str:
    return PRODUCT_STATUS.get(s, s)


def tags_text(tags: list[str]) -> str:
    return " ".join(TAGS.get(t, t) for t in (tags or []))


def summarize_message(message) -> str:
    """把一条任意类型的消息概括为可存入订单回单的文本。"""
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    kind = ""
    if getattr(message, "photo", None):
        kind = "[图片]"
    elif getattr(message, "document", None):
        kind = "[文件]"
    elif getattr(message, "video", None):
        kind = "[视频]"
    elif getattr(message, "audio", None):
        kind = "[音频]"
    elif getattr(message, "voice", None):
        kind = "[语音]"
    elif getattr(message, "sticker", None):
        kind = "[贴纸]"
    if text and kind:
        return f"{text}\n{kind}"
    return text or kind or "（空消息）"


def _media_of(message) -> str:
    """消息当前承载的媒体 file_id（图片/文件/视频），无媒体返回空串。"""
    photo = getattr(message, "photo", None)
    if photo:
        return photo[-1].file_id
    for attr in ("document", "video", "animation", "audio", "voice"):
        media = getattr(message, attr, None)
        fid = getattr(media, "file_id", "") if media else ""
        if fid:
            return fid
    return ""


def _caption_fits(text: str) -> bool:
    """文案是否放得进图片说明。超长时改用纯文字消息，避免编辑失败导致丢图。"""
    return len(text or "") <= CAPTION_LIMIT


async def _drop_message(message) -> None:
    """删除上一条消息（失败忽略）。

    Telegram 不支持把「图片消息」就地变成「文字消息」（反之亦然），
    这类切换只能删旧发新，否则界面会残留过期消息、越堆越多。
    """
    try:
        await message.delete()
    except Exception:
        pass


async def _edit_text(message, text: str, keyboard) -> bool:
    """就地改文字；HTML 非法时降级纯文本。返回是否成功。"""
    try:
        await message.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
        return True
    except BadRequest as exc:
        if is_not_modified(exc):
            return True
        try:
            await message.edit_message_text(text=strip_html_tags(text), reply_markup=keyboard)
            return True
        except Exception:
            return False
    except Exception:
        return False


async def _edit_caption(message, text: str, keyboard) -> bool:
    """就地改图注（保留图片）；HTML 非法时降级纯文本。返回是否成功。"""
    try:
        await message.edit_message_caption(caption=text, reply_markup=keyboard, parse_mode="HTML")
        return True
    except BadRequest as exc:
        if is_not_modified(exc):
            return True
        try:
            await message.edit_message_caption(caption=strip_html_tags(text), reply_markup=keyboard)
            return True
        except Exception:
            return False
    except Exception:
        return False


async def _edit_media(message, photo: str, text: str, keyboard) -> bool:
    """原地换图并改图注（图 → 图，不删消息、不闪烁）。返回是否成功。"""
    try:
        await message.edit_message_media(
            media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
            reply_markup=keyboard,
        )
        return True
    except BadRequest as exc:
        if is_not_modified(exc):
            return True
        try:
            await message.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=strip_html_tags(text)),
                reply_markup=keyboard,
            )
            return True
        except Exception:
            return False
    except Exception:
        return False


async def _reply_photo(message, photo: str, text: str, keyboard) -> bool:
    """发送一条图文消息；HTML 非法或 file_id 失效时返回 False 由调用方兜底。"""
    try:
        await message.reply_photo(photo=photo, caption=text, reply_markup=keyboard, parse_mode="HTML")
        return True
    except BadRequest:
        try:
            await message.reply_photo(photo=photo, caption=strip_html_tags(text), reply_markup=keyboard)
            return True
        except Exception:
            return False
    except Exception:
        return False


async def _reply_text(message, text: str, keyboard) -> None:
    """发送一条纯文字消息；HTML 非法时降级纯文本。"""
    try:
        await message.reply_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    except BadRequest:
        try:
            await message.reply_text(text=strip_html_tags(text), reply_markup=keyboard)
        except Exception:
            pass
    except Exception:
        pass


async def send_or_edit(query, text: str, keyboard: InlineKeyboardMarkup, photo: str | None = None) -> None:
    """媒体感知的「就地更新」，保证界面上始终只有一条当前页消息。

    - 图文消息 → 改图注（欢迎图 / 商品图原地保留，不再新发消息）
    - 文字消息 → 改文字
    - 图 ↔ 文 互切（Telegram 不支持就地转换）→ 删旧发新，避免消息堆积

    v2.3.0 的安全降级保留：HTML 解析失败（高级会员表情权限失效、老数据残留
    特殊字符）自动降级纯文本重发；「Message is not modified」直接忽略。

    v2.6.6 起主菜单回退时带 photo，解决「点进子页再返回，欢迎图消失」：
    原实现无图一律走 edit_message_text，会把带图消息的图抹掉。
    """
    message = getattr(query, "message", None)
    if message is None:
        return
    if photo and not _caption_fits(text):
        # 图注放不下：退回纯文字，保证内容完整可见
        photo = None
    current = _media_of(message)

    if photo:
        if current:
            # 当前已是图文消息：同图改图注，异图原地换图，都不删消息
            if current == photo:
                if await _edit_caption(message, text, keyboard):
                    return
            elif await _edit_media(message, photo, text, keyboard):
                return
        # 当前是文字消息（或就地编辑失败）：Telegram 无法把文字消息变成媒体消息，
        # 只能删旧发新，否则界面会同时留下「没图的旧消息」和「带图的新消息」
        await _drop_message(message)
        if await _reply_photo(message, photo, text, keyboard):
            return
        await _reply_text(message, text, keyboard)
        return

    # 目标不带图：当前是图文消息则删旧发新，否则就地改文字
    if current:
        await _drop_message(message)
        await _reply_text(message, text, keyboard)
        return
    if await _edit_text(message, text, keyboard):
        return
    await _reply_text(message, text, keyboard)


async def reply_html(message, text: str, keyboard: InlineKeyboardMarkup | None = None, photo: str = "") -> None:
    """对一条消息做 HTML 回复（可带图）；失败自动降级纯文本，保证必达。"""
    if photo:
        try:
            await message.reply_photo(photo=photo, caption=text, reply_markup=keyboard, parse_mode="HTML")
            return
        except BadRequest:
            try:
                await message.reply_photo(
                    photo=photo, caption=strip_html_tags(text), reply_markup=keyboard,
                )
                return
            except Exception:
                pass
        except Exception:
            pass
    try:
        await message.reply_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    except BadRequest:
        try:
            await message.reply_text(text=strip_html_tags(text), reply_markup=keyboard)
        except Exception:
            pass
    except Exception:
        pass


def clear_state(context) -> None:
    context.user_data.pop("state", None)
    context.user_data.pop("payload", None)


def get_points(uid: int) -> int:
    u = db.get_user(uid)
    return u.points if u else 0


def audit(admin_id: int, action: str, detail: str = "") -> None:
    """管理员操作日志快捷入口（v2.4.0）：任何后台改动都记一笔，可追溯。"""
    try:
        db.add_audit_log(admin_id, action, detail)
    except Exception:
        pass  # 日志失败绝不影响业务主流程
