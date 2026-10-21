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
    class Q1:
        class message:
            @staticmethod
            async def reply_photo(**kw):
                raise AssertionError("不应走发图分支")

        @staticmethod
        async def edit_message_text(**kw):
            if kw.get("parse_mode") == "HTML":
                from telegram.error import BadRequest

                raise BadRequest("Can't parse entities")
            Q1.plain_called = True

        plain_called = False

    asyncio.new_event_loop().run_until_complete(send_or_edit(Q1(), "<b>坏标签", kb([[("x", "y")]])))
    check("HTML 解析失败降级为纯文本", Q1.plain_called is True)

    # Message is not modified → 静默忽略
    class Q2:
        @staticmethod
        async def edit_message_text(**kw):
            from telegram.error import BadRequest

            raise BadRequest("Message is not modified")

    try:
        asyncio.new_event_loop().run_until_complete(send_or_edit(Q2(), "same", kb([[("x", "y")]])))
        check("Message is not modified 被静默忽略", True)
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


if __name__ == "__main__":
    test_ui()
    test_supply_pricing()
    test_chain_utils()
    test_db_facade()
    print(f"\n{'=' * 50}")
    print(f"✅ 通过 {PASS} 项   ❌ 失败 {FAIL} 项")
    print("=" * 50)
    sys.exit(0 if FAIL == 0 else 1)
