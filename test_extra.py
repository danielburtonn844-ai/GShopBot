"""补充测试：覆盖主自检未触及的分支（UI 降级、供需定价与开关、链上工具函数）。

与 test_bot_full.py 的分工：
- test_bot_full.py  业务全链路（7200+ 断言）
- verify_issues.py  缺陷回归（43 项）
- test_extra.py     分支覆盖补强（本文件）

全部离线运行，使用临时 SQLite，不联网、不发 Telegram 消息。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_ID", "100000000")

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


# ==================================================================
# UI：键盘 / 转义 / 状态 / 消息概括 / 发送降级
# ==================================================================
def test_ui() -> None:
    print("\n=== UI 工具 ===")
    from telegram import InlineKeyboardMarkup

    from examples.ui import (
        audit,
        clear_state,
        get_points,
        h,
        kb,
        order_status_text,
        product_status_text,
        reply_html,
        send_or_edit,
        summarize_message,
        tags_text,
    )

    k = kb([[("按钮A", "a:1"), ("按钮B", "a:2")], [("单行", "b:1")]])
    check("kb 生成 InlineKeyboardMarkup", isinstance(k, InlineKeyboardMarkup))
    check("kb 行数正确", len(k.inline_keyboard) == 2)
    check("kb 按钮文字正确", k.inline_keyboard[0][0].text == "按钮A")

    check("h 转义 <", h("<script>") == "&lt;script&gt;")
    check("h 转义 &", h("a&b") == "a&amp;b")
    check("h 空值返回空串", h(None) == "")

    check("订单状态文案", "待处理" in order_status_text("pending"))
    check("未知订单状态回显原值", order_status_text("weird") == "weird")
    check("商品状态文案", "销售中" in product_status_text("on"))
    check("标签拼接", "/" in tags_text(["hot", "new"]) or "热销" in tags_text(["hot"]))
    check("空标签返回空串", tags_text([]) == "")

    class M:
        text = "带文字"
        photo = object()
        document = None
        video = None
        audio = None
        voice = None
        sticker = None
        caption = None

    out = summarize_message(M())
    check("图片+文字组合", "带文字" in out and "[图片]" in out, out)

    class M2:
        text = None
        caption = None
        photo = None
        document = object()
        video = None
        audio = None
        voice = None
        sticker = None

    check("纯文件消息", summarize_message(M2()) == "[文件]")

    class M3(M2):
        document = None

    check("空消息兜底", summarize_message(M3()) == "（空消息）")

    class Ctx:
        user_data: dict = {"state": "x", "payload": {"a": 1}}

    c = Ctx()
    clear_state(c)
    check("clear_state 清空 state", "state" not in c.user_data)
    check("clear_state 清空 payload", "payload" not in c.user_data)

    # send_or_edit 的降级路径：HTML 解析失败 → 纯文本重发
    # 注意：v2.6.6 起 send_or_edit 是媒体感知的，编辑的是 query.message 而非 query 本身
    class _Msg1:
        def __init__(self):
            self.photo = []
            self.document = None
            self.plain_called = False

        async def edit_message_text(self, text=None, reply_markup=None, parse_mode=None, **kw):
            if parse_mode == "HTML":
                from telegram.error import BadRequest

                raise BadRequest("Can't parse entities")
            self.plain_called = True
            return self

        async def reply_text(self, text=None, reply_markup=None, **kw):
            self.plain_called = True
            return self

    class Q1:
        def __init__(self):
            self.message = _Msg1()

    q1 = Q1()
    asyncio.new_event_loop().run_until_complete(send_or_edit(q1, "<b>坏标签", kb([[("x", "y")]])))
    check("HTML 解析失败降级为纯文本", q1.message.plain_called is True)

    # Message is not modified → 静默忽略（不重发、不报错）
    class _Msg2:
        def __init__(self):
            self.photo = []
            self.document = None
            self.replied = False

        async def edit_message_text(self, text=None, reply_markup=None, parse_mode=None, **kw):
            from telegram.error import BadRequest

            raise BadRequest("Message is not modified")

        async def reply_text(self, text=None, reply_markup=None, **kw):
            self.replied = True
            return self

    class Q2:
        def __init__(self):
            self.message = _Msg2()

    q2 = Q2()
    try:
        asyncio.new_event_loop().run_until_complete(send_or_edit(q2, "same", kb([[("x", "y")]])))
        check("Message is not modified 被静默忽略", q2.message.replied is False)
    except Exception as exc:
        check("Message is not modified 被静默忽略", False, repr(exc))

    # reply_html 发图 + 降级
    class Msg:
        sent = []

        async def reply_text(self, **kw):
            Msg.sent.append(("text", kw))
            return None

        async def reply_photo(self, **kw):
            Msg.sent.append(("photo", kw))
            return None

    asyncio.new_event_loop().run_until_complete(reply_html(Msg(), "<b>hi</b>", kb([[("x", "y")]])))
    check("reply_html 发送成功", len(Msg.sent) == 1)

    # audit / get_points 走真实库
    tmp = tempfile.mkdtemp()
    os.environ["SHOP_DB_PATH"] = os.path.join(tmp, "ui.db")
    import examples.runtime as rt

    rt.db.__init__(os.environ["SHOP_DB_PATH"])  # 重建到临时库
    audit(100000000, "测试动作", "细节")
    logs = rt.db.list_audit_logs(limit=5)
    check("audit 写入操作日志", any("测试动作" in (row.action or "") for row in logs))
    check("get_points 不存在的用户返回 0", get_points(999999999) == 0)


# ==================================================================
# 供需：定价 / 开关 / 渠道可用性
# ==================================================================
def test_supply_pricing() -> None:
    print("\n=== 供需定价与开关 ===")
    tmp = tempfile.mkdtemp()
    os.environ["SHOP_DB_PATH"] = os.path.join(tmp, "sp.db")
    from examples.models import Database
    from examples.supply_service import SupplyService

    db = Database(os.environ["SHOP_DB_PATH"])
    svc = SupplyService(db, None)

    db.set_setting("supply_price_bot", 10)
    db.set_setting("supply_price_channel", 30)
    db.set_setting("supply_discount", 5)

    check("bot 渠道价 = 10", svc.price_for("bot") == 10)
    check("channel 渠道价 = 30", svc.price_for("channel") == 30)
    check("both 渠道价 = 10+30-5 = 35", svc.price_for("both") == 35, str(svc.price_for("both")))
    check("频道腿(channel) = 30", svc.channel_leg_value("channel") == 30)
    check("频道腿(both) = 35-10 = 25", svc.channel_leg_value("both") == 25)
    check("频道腿(bot) = 0", svc.channel_leg_value("bot") == 0)

    # 价格极低时不出现 0 或负数
    db.set_setting("supply_price_bot", 0)
    db.set_setting("supply_price_channel", 0)
    check("价格为 0 时最低收 1 积分", svc.price_for("both") >= 1)

    # 优惠大于总价 → both 仍 >= 1，频道腿 >= 0
    db.set_setting("supply_price_bot", 10)
    db.set_setting("supply_price_channel", 10)
    db.set_setting("supply_discount", 999)
    check("优惠大于总价时 both 仍 >= 1", svc.price_for("both") >= 1)
    check("频道腿不为负", svc.channel_leg_value("both") >= 0)

    # 渠道可用性
    db.set_setting("supply_channel_id", "")
    check("未配频道时只开放 bot", svc.available_channels() == ["bot"])
    db.set_setting("supply_channel_id", "-1001234567890")
    check("配置频道后三个渠道都开放", svc.available_channels() == ["bot", "channel", "both"])
    check("channel_configured 为真", svc.channel_configured() is True)

    # 开关
    db.set_setting("supply_paused", True)
    check("paused 开关生效", svc.paused() is True)
    db.set_setting("supply_paused", False)
    check("paused 关闭后恢复", svc.paused() is False)
    db.set_setting("supply_review", True)
    check("人工审核开关生效", svc.review_enabled() is True)
    db.set_setting("supply_hall_open", False)
    check("大厅关闭开关生效", svc.hall_open() is False)


# ==================================================================
# 链上工具：金额解析 / 地址校验 / 时间换算
# ==================================================================
def test_chain_utils() -> None:
    print("\n=== 链上工具函数 ===")
    from decimal import Decimal

    from examples.chain_watch import (
        is_valid_ton,
        is_valid_tron,
        parse_chain_amount,
        ton_normalize_address,
        validate_chain_address,
    )

    check("6 位小数解析", parse_chain_amount("10500000") == Decimal("10.5"),
          str(parse_chain_amount("10500000")))
    check("0 值解析", parse_chain_amount("0") == 0)
    check("超大金额", parse_chain_amount("123456789012") == Decimal("123456.789012"))
    check("非法金额返回 0", parse_chain_amount("abc") == 0)
    check("负数字符串不崩", parse_chain_amount("-5") <= 0)

    # TRON：官方 USDT 合约地址本身是合法 T 地址
    check("合法 TRON 地址", is_valid_tron("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t") is True)
    check("TRON 地址长度错", is_valid_tron("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6") is False)
    check("TRON 非 T 开头", is_valid_tron("AR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t") is False)
    check("TRON 校验和错", is_valid_tron("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6u") is False)

    ton_addr = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
    check("合法 TON 地址", is_valid_ton(ton_addr) is True)
    check("TON 长度错", is_valid_ton(ton_addr[:-1]) is False)
    check("TON 前缀错", is_valid_ton("XQ" + ton_addr[2:]) is False)
    check("TON 校验和错", is_valid_ton(ton_addr[:-1] + "X") is False)

    check("通道分派 trc20", validate_chain_address("trc20", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t") is True)
    check("通道分派 ton", validate_chain_address("ton", ton_addr) is True)
    check("未知链一律拒绝", validate_chain_address("btc", "1A1zP1") is False)

    check("friendly → raw", ton_normalize_address(ton_addr)
          == "0:B113A994B5024A16719F69139328EB759596C38A25F59028B146FECDC3621DFE")
    check("UQ 前缀同样解析", ton_normalize_address("UQ" + ton_addr[2:]) == ton_normalize_address(ton_addr))
    check("raw 大写归一", ton_normalize_address("0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe")
          == ton_normalize_address(ton_addr))
    check("非法输入原样小写返回", ton_normalize_address("GARBAGE") == "garbage")
    check("空串返回空串", ton_normalize_address("") == "")


# ==================================================================
# 数据层拆分后的门面完整性
# ==================================================================
def test_db_facade() -> None:
    print("\n=== Database 门面 ===")
    from examples.db import Database
    from examples.db.mixins.admin import AdminMixin
    from examples.db.mixins.grab import GrabMixin
    from examples.db.mixins.user import UserMixin

    check("Database 继承 UserMixin", issubclass(Database, UserMixin))
    check("Database 继承 AdminMixin", issubclass(Database, AdminMixin))
    check("Database 继承 GrabMixin", issubclass(Database, GrabMixin))
    check("Database 自身不再定义业务方法",
          len([n for n in Database.__dict__ if not n.startswith("_")]) == 0,
          str([n for n in Database.__dict__ if not n.startswith("_")]))

    # 兼容层：老路径照样能拿到全部名字
    import examples.models as legacy

    for name in ("Database", "User", "Order", "Grabber", "now_iso", "ORDER_STATUS", "SUPPLY_CHANNELS"):
        check(f"models.{name} 仍可导入", hasattr(legacy, name))

    # 分组的唯一目标是「每张表只在一个 mixin 里读写」
    check("用户表方法在 UserMixin", hasattr(UserMixin, "get_user"))
    check("抢单表方法在 GrabMixin", hasattr(GrabMixin, "grab_order_atomic"))


# ==================================================================
# 欢迎图：主界面回退不丢图（媒体感知 send_or_edit）
# ==================================================================
def test_welcome_photo() -> None:
    print("\n=== 欢迎图：回主界面不丢图 ===")
    from telegram import InlineKeyboardMarkup

    from examples.ui import send_or_edit

    KBD = InlineKeyboardMarkup([[]])

    class PhotoSize:
        def __init__(self, fid):
            self.file_id = fid

    class Msg:
        """模拟 Telegram 消息：记录走了哪条编辑路径。"""

        def __init__(self, photo_fid=""):
            self.photo = [PhotoSize(photo_fid)] if photo_fid else []
            self.document = None
            self.video = None
            self.audio = None
            self.voice = None
            self.animation = None
            self.deleted = False
            self.calls: list[str] = []
            self.caption = ""
            self.text = ""

        async def edit_message_text(self, text=None, reply_markup=None, parse_mode=None, **kw):
            self.calls.append("edit_text")
            self.text = text or ""
            return self

        async def edit_message_caption(self, caption=None, reply_markup=None, parse_mode=None, **kw):
            self.calls.append("edit_caption")
            self.caption = caption or ""
            return self

        async def edit_message_media(self, media=None, reply_markup=None, **kw):
            self.calls.append("edit_media")
            return self

        async def reply_photo(self, photo=None, caption=None, reply_markup=None, **kw):
            self.calls.append("reply_photo")
            return self

        async def reply_text(self, text=None, reply_markup=None, **kw):
            self.calls.append("reply_text")
            return self

        async def delete(self, **kw):
            self.calls.append("delete")
            self.deleted = True
            return True

    class Q:
        def __init__(self, msg):
            self.message = msg

    WELCOME = "AgACAgUAAxkBAAIWELCOME"

    # 1) 当前已在带图主界面，再点主菜单 → 就地改图注，图保留，不删不发
    m = Msg(WELCOME)
    asyncio.new_event_loop().run_until_complete(send_or_edit(Q(m), "主菜单文字", KBD, photo=WELCOME))
    check("带图消息回主菜单：走 edit_caption", m.calls == ["edit_caption"], str(m.calls))
    check("带图消息回主菜单：图未丢（没删没重发）", not m.deleted and "reply_photo" not in m.calls)

    # 2) 当前是纯文本子页面，返回主菜单 → 删旧发新（Telegram 不支持文→图就地转换）
    m2 = Msg("")
    asyncio.new_event_loop().run_until_complete(send_or_edit(Q(m2), "主菜单文字", KBD, photo=WELCOME))
    check("纯文本消息回主菜单：先删旧消息", "delete" in m2.calls, str(m2.calls))
    check("纯文本消息回主菜单：再发带图新消息", "reply_photo" in m2.calls, str(m2.calls))
    check("纯文本消息回主菜单：不走 edit_text（否则图永远回不来）",
          "edit_text" not in m2.calls, str(m2.calls))

    # 3) 当前是另一张图，目标换图 → edit_media 原地换，不删消息
    m3 = Msg("OTHER_IMAGE")
    asyncio.new_event_loop().run_until_complete(send_or_edit(Q(m3), "主菜单文字", KBD, photo=WELCOME))
    check("异图切换：走 edit_media", m3.calls == ["edit_media"], str(m3.calls))
    check("异图切换：不删消息", not m3.deleted)

    # 4) 目标无图（子页面），当前是图 → 删旧发新
    m4 = Msg(WELCOME)
    asyncio.new_event_loop().run_until_complete(send_or_edit(Q(m4), "商品列表", KBD, photo=None))
    check("从主菜单进子页：删旧消息", "delete" in m4.calls, str(m4.calls))
    check("从主菜单进子页：发纯文字", "reply_text" in m4.calls, str(m4.calls))

    # 5) 目标无图，当前也无图 → 就地改文字，不删不重发
    m5 = Msg("")
    asyncio.new_event_loop().run_until_complete(send_or_edit(Q(m5), "商品列表", KBD, photo=None))
    check("纯文本页间切换：就地 edit_text", m5.calls == ["edit_text"], str(m5.calls))

    # 6) 文案超过 caption 上限 → 主动放弃带图，退回纯文字（避免编辑失败丢整页）
    m6 = Msg("")
    long_text = "字" * 1100
    asyncio.new_event_loop().run_until_complete(send_or_edit(Q(m6), long_text, KBD, photo=WELCOME))
    check("超长文案自动放弃带图", "reply_photo" not in m6.calls, str(m6.calls))
    check("超长文案仍能渲染文字", "reply_text" in m6.calls or "edit_text" in m6.calls, str(m6.calls))

    # 7) home_photo() 读的是 settings 里的 welcome_image
    tmp = tempfile.mkdtemp()
    os.environ["SHOP_DB_PATH"] = os.path.join(tmp, "wp.db")
    import examples.runtime as rt

    rt.db.__init__(os.environ["SHOP_DB_PATH"])
    from examples.pages_user import home_photo

    check("未设置欢迎图时返回空串", home_photo() == "")
    rt.db.update_settings(welcome_image=WELCOME)
    check("设置后 home_photo 返回 file_id", home_photo() == WELCOME, home_photo())


if __name__ == "__main__":
    test_ui()
    test_supply_pricing()
    test_chain_utils()
    test_db_facade()
    test_welcome_photo()
    print(f"\n{'=' * 50}")
    print(f"✅ 通过 {PASS} 项   ❌ 失败 {FAIL} 项")
    print("=" * 50)
    sys.exit(0 if FAIL == 0 else 1)
