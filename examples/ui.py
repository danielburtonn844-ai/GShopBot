"""UI 共用工具（v2.4.0 模块化拆分）：键盘构造、HTML 转义、安全发送、状态管理。

所有页面与回调模块共用的基础设施集中在此，页面模块只关心内容组装。
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from .models import PRODUCT_STATUS, TAGS, escape_html
from .richtext import clean_label, is_not_modified, match_icon, strip_html_tags
from .runtime import db


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


async def send_or_edit(query, text: str, keyboard: InlineKeyboardMarkup, photo: str | None = None) -> None:
    """有图发新消息，无图优先编辑原消息。

    v2.3.0 起带安全降级：HTML 解析失败（如正文含高级会员表情但机器人权限失效、
    老数据残留特殊字符）时自动降级为纯文本重发，保证页面一定能打开；
    「Message is not modified」（重复点击同一页）直接忽略，不再刷重复消息。
    """
    if photo:
        try:
            await query.message.reply_photo(photo=photo, caption=text, reply_markup=keyboard, parse_mode="HTML")
            return
        except BadRequest:
            try:
                await query.message.reply_photo(
                    photo=photo, caption=strip_html_tags(text), reply_markup=keyboard,
                )
                return
            except Exception:
                pass
        except Exception:
            pass
    try:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    except BadRequest as exc:
        if is_not_modified(exc):
            return
        plain = strip_html_tags(text)
        try:
            await query.edit_message_text(text=plain, reply_markup=keyboard)
        except Exception:
            try:
                await query.message.reply_text(text=plain, reply_markup=keyboard)
            except Exception:
                pass
    except Exception:
        try:
            await query.message.reply_text(text=text, reply_markup=keyboard, parse_mode="HTML")
        except BadRequest:
            try:
                await query.message.reply_text(text=strip_html_tags(text), reply_markup=keyboard)
            except Exception:
                pass
        except Exception:
            pass


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
