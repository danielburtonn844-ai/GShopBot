"""v2.8.1 回归：4 个业务硬伤的修复。

对应 CHANGELOG v2.8.1：
  1) 补单入口（N-P0-2）：failed / expired 单能被后台翻出来并补账
  2) 负价兜底：create_product 拒写库、_validate_purchase 前置拦截
  3) 拉群即接管（N-P1-2）：未配置时不再自动写 setting，必须管理员确认
  4) save_order / save_user 丢更新：基线判定，没动过的列不会被写回

全部离线：临时 SQLite + 假 bot，不连 Telegram、不连支付平台。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
sys.path.insert(0, BASE)
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_ID", "100000000")
_TMP = tempfile.mkdtemp(prefix="v281_")
os.environ["SHOP_DB_PATH"] = os.path.join(_TMP, "shop.db")

from gshop.db.schema import Order, Product, User  # noqa: E402
from gshop.models import now_iso  # noqa: E402
from gshop.runtime import db, grab, service  # noqa: E402

PASS = 0
FAIL = 0
LOOP = asyncio.new_event_loop()

ADMIN = 100000000
U1 = 311111
U2 = 322222


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kw):
        self.sent.append((chat_id, text, reply_markup))
        return type("M", (), {"message_id": 9000 + len(self.sent)})()


# ======================================================================
# 1. 补单入口（N-P0-2）
# ======================================================================
def test_manual_recharge():
    print("\n=== 修复 1：补单入口（failed / expired 可查可补） ===")

    check("OPEN_RECHARGE_STATES 覆盖三态", db.OPEN_RECHARGE_STATES == ("pending", "failed", "expired"))

    u = User(user_id=U1, username="payuser", points=0, created_at=now_iso())
    db.save_user(u)

    # 造三单：pending / failed / expired / completed
    r_pending = service.create_recharge(U1, 10, "okpay")
    assert r_pending is not None
    r_failed = service.create_recharge(U1, 20, "okpay")
    assert r_failed is not None
    r_failed.status = "failed"
    db.save_recharge(r_failed)
    r_expired = service.create_recharge(U1, 30, "okpay")
    assert r_expired is not None
    r_expired.status = "expired"
    r_expired.chain = "trc20"
    db.save_recharge(r_expired)
    r_done = service.create_recharge(U1, 40, "okpay")
    assert r_done is not None
    r_done.status = "completed"
    db.save_recharge(r_done)

    total = db.count_recharges_multi(db.OPEN_RECHARGE_STATES)
    check("待核对计数 = 3（不含 completed）", total == 3, str(total))

    rows = db.list_recharges_multi(db.OPEN_RECHARGE_STATES, limit=50, offset=0)
    ids = {r.id for r in rows}
    check("列表含 failed 单", r_failed.id in ids)
    check("列表含 expired 单", r_expired.id in ids)
    check("列表不含 completed 单", r_done.id not in ids)

    # 分页：limit=2 时第二页只剩 1 条
    p0 = db.list_recharges_multi(db.OPEN_RECHARGE_STATES, limit=2, offset=0)
    p1 = db.list_recharges_multi(db.OPEN_RECHARGE_STATES, limit=2, offset=2)
    check("分页第一页 2 条", len(p0) == 2, str(len(p0)))
    check("分页第二页 1 条", len(p1) == 1, str(len(p1)))
    check("两页无重叠", not ({r.id for r in p0} & {r.id for r in p1}))

    # 补账 failed 单
    before = db.get_user(U1).points
    done = service.complete_recharge(r_failed.id)
    check("failed 单可补账", done is not None)
    after = db.get_user(U1).points
    check("补账后积分增加 20", after == before + r_failed.points, f"{before}->{after}")
    check("补账后状态 completed", db.get_recharge(r_failed.id).status == "completed")

    # 再补一次必须失败（幂等）
    again = service.complete_recharge(r_failed.id)
    check("重复补账被拒（completed 不在可补态）", again is None)
    check("重复补账未重复加积分", db.get_user(U1).points == after, str(db.get_user(U1).points))

    # 补账 expired 单
    before2 = db.get_user(U1).points
    done2 = service.complete_recharge(r_expired.id)
    check("expired 单可补账", done2 is not None)
    check("expired 补账后积分 +30", db.get_user(U1).points == before2 + r_expired.points)

    # 页面渲染
    from gshop.pages_admin import page_admin_recharge_confirm, page_admin_recharges

    text, kb = page_admin_recharges(0)
    check("列表页有标题", "待核对充值" in text)
    check("列表页显示笔数", "共 <code>" in text)
    btns = [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]
    check("列表页有核对按钮", any(b.startswith("a:rcq:") for b in btns), str(btns[:5]))
    check("列表页有返回财务按钮", "a:fin" in btns)
    check("前端回调长度均 < 64 字节", all(len(b.encode()) < 64 for b in btns))

    ctext, ckb = page_admin_recharge_confirm(r_pending.id)
    check("确认页显示金额", "USDT" in ctext)
    check("确认页显示用户", str(U1) in ctext)
    cbtns = [b.callback_data for row in ckb.inline_keyboard for b in row if b.callback_data]
    check("确认页有确认补账按钮", f"a:rcc:{r_pending.id}" in cbtns)

    miss, _ = page_admin_recharge_confirm("NO_SUCH_ID")
    check("确认页对不存在单给提示", "不存在" in miss)

    # 财务概览页入口
    from gshop.pages_admin import page_admin_finance
    ftext, fkb = page_admin_finance()
    fbtns = [b.callback_data for row in fkb.inline_keyboard for b in row if b.callback_data]
    check("财务页有补单入口", "a:rcl" in fbtns, str(fbtns))


# ======================================================================
# 2. 负价兜底
# ======================================================================
def test_bad_price():
    print("\n=== 修复 2：负价 / 0 价兜底 ===")

    from gshop.shop_service import BUY_FAIL_REASONS

    check("BUY_FAIL_REASONS 有 bad_price", "bad_price" in BUY_FAIL_REASONS)

    for bad in (0, -1, -99999):
        try:
            service.create_product({"name": f"负价{bad}", "points": bad, "kind": "product"})
            check(f"create_product 拒绝 points={bad}", False, "没抛异常")
        except ValueError:
            check(f"create_product 拒绝 points={bad}", True)

    # 正常价必须能创建
    p = service.create_product({"name": "正常品", "points": 50, "kind": "product"})
    check("create_product 接受 50", p.points == 50, str(p.points))

    # 字符串数字也接受（SQLite 兼容路径）
    p2 = service.create_product({"name": "字符串价", "points": "25", "kind": "product"})
    check("create_product 接受 '25' 字符串", p2.points == 25, str(p2.points))

    # _validate_purchase 前置拦截：直接构造负价 Product 绕过 create_product
    db.save_product(Product(id="NEG1", name="脏数据负价", points=-100, kind="product", status="on"))
    user = db.get_user(U1)
    code = service._validate_purchase(user, db.get_product("NEG1"))
    check("_validate_purchase 拦负价", code == "bad_price", str(code))

    db.save_product(Product(id="ZERO1", name="脏数据0价", points=0, kind="product", status="on"))
    code0 = service._validate_purchase(user, db.get_product("ZERO1"))
    check("_validate_purchase 拦 0 价", code0 == "bad_price", str(code0))

    # 正常商品不受影响
    ok_code = service._validate_purchase(user, db.get_product(p.id))
    check("正常商品校验通过（返非 bad_price）", ok_code != "bad_price", str(ok_code))


# ======================================================================
# 3. 拉群即接管
# ======================================================================
def test_grab_chat_guard():
    print("\n=== 修复 3：拉群不再自动接管 ===")

    bot = FakeBot()
    grab.set_bot(bot)

    # 清空接单群配置，模拟「从未配置过」
    db.set_setting("grab_chat_id", "")
    db.set_setting("grab_enabled", "0")
    check("前置：grab_chat_id 为空", grab.chat_id() == "", grab.chat_id())

    LOOP.run_until_complete(grab.handle_bot_added(-1007777777777, "陌生人的群"))

    check("被拉群后 grab_chat_id 仍为空", grab.chat_id() == "", grab.chat_id())
    check("被拉群后 grab_enabled 仍关闭", str(db.get_settings().get("grab_enabled")) in ("0", "", "False", "None"),
          str(db.get_settings().get("grab_enabled")))
    check("已给管理员发通知", len(bot.sent) > 0, str(len(bot.sent)))
    if bot.sent:
        _cid, txt, markup = bot.sent[0]
        check("通知里含群 ID", "-1007777777777" in txt)
        btns = [b.callback_data for row in (markup.inline_keyboard if markup else []) for b in row if b.callback_data]
        check("通知带「设为本群」按钮", "-1007777777777" in " ".join(btns), str(btns))

    # confirm_grab_chat 格式校验
    check("非法群 ID（正数）被拒", grab.confirm_grab_chat("123456") is False)
    check("非法群 ID 未写入", grab.chat_id() == "", grab.chat_id())
    check("非法群 ID（含字母）被拒", grab.confirm_grab_chat("-100abc") is False)
    check("空串被拒", grab.confirm_grab_chat("") is False)

    check("合法群 ID 被接受", grab.confirm_grab_chat("-1007777777777") is True)
    check("合法群 ID 已写入", grab.chat_id() == "-1007777777777", grab.chat_id())
    check("合法写入后总开关打开", grab.enabled() is True)

    # 已配置后再被拉进别的群：只提示，不改配置
    bot.sent.clear()
    LOOP.run_until_complete(grab.handle_bot_added(-1008888888888, "另一个群"))
    check("已配置后不自动换群", grab.chat_id() == "-1007777777777", grab.chat_id())
    check("已配置后仍发通知", len(bot.sent) > 0)

    # 复位，免得影响后续用例
    db.set_setting("grab_chat_id", "")
    db.set_setting("grab_enabled", "0")


# ======================================================================
# 4. 丢更新
# ======================================================================
def test_lost_update():
    print("\n=== 修复 4：save_order / save_user 不再丢更新 ===")

    # 订单
    db.save_order(Order(id="LU1", user_id=U2, product_name="丢更新测试", status="pending", note="初始"))
    a = db.get_order("LU1")
    b = db.get_order("LU1")
    b.status = "processing"
    db.save_order(b)

    a.note = "A 的回单"
    db.save_order(a)
    final = db.get_order("LU1")
    check("A 改的 note 生效", final.note == "A 的回单", final.note)
    check("A 没碰的 status 未被覆盖", final.status == "processing", final.status)

    # 用户
    db.save_user(User(user_id=U2, username="lu", points=500, last_active="t0"))
    ua = db.get_user(U2)
    db.update_user_points(U2, -200)
    check("前置：库里的分已扣到 300", db.get_user(U2).points == 300, str(db.get_user(U2).points))
    ua.first_name = "改了名字"
    db.save_user(ua)
    uf = db.get_user(U2)
    check("A 改的 first_name 生效", uf.first_name == "改了名字", uf.first_name)
    check("A 没碰的 points 未被写回 500", uf.points == 300, str(uf.points))

    # 基线不污染 to_dict
    check("User.to_dict 无 _baseline", "_baseline" not in uf.to_dict())
    check("Order.to_dict 无 _baseline", "_baseline" not in final.to_dict())


def main() -> int:
    test_manual_recharge()
    test_bad_price()
    test_grab_chat_guard()
    test_lost_update()

    print("\n" + "=" * 50)
    print(f"✅ 通过 {PASS} 项   ❌ 失败 {FAIL} 项")
    print("=" * 50)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
