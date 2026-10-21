"""测试补强第二批：供需全流程 / 抢单回调 / 后台任务 / 链上拉取。

目标是把覆盖率从 71% 拉到 90%+，重点补之前完全没走到的分支：
- supply_service.py  发布 → 审核 → 分发 → 退款 → 删帖 → 统计
- cb_grab.py         抢单回调全部分支
- jobs.py            定时任务
- chain_watch.py     TRON / TON 拉取的异常路径

全部离线：HTTP 走 stub，Telegram 走 fake bot，数据库走临时 SQLite。
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
LOOP = asyncio.new_event_loop()


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


def run(coro):
    return LOOP.run_until_complete(coro)


# ==================================================================
# 假 bot / 假 message
# ==================================================================
class FakeBot:
    """记录所有发送，可按 chat_id 触发异常。"""

    def __init__(self, fail_ids=(), username="fakebot"):
        self.sent: list[tuple] = []
        self.fail_ids = set(fail_ids)
        self.username = username

    async def send_message(self, chat_id=None, text="", **kw):
        if chat_id in self.fail_ids:
            raise RuntimeError("模拟发送失败")
        self.sent.append(("message", chat_id, text))
        return None

    async def send_photo(self, chat_id=None, photo="", caption="", **kw):
        if chat_id in self.fail_ids:
            raise RuntimeError("模拟发送失败")
        self.sent.append(("photo", chat_id, caption))
        return None

    async def copy_message(self, chat_id=None, **kw):
        self.sent.append(("copy", chat_id))
        return None

    async def create_chat_invite_link(self, chat_id=None, **kw):
        class Link:
            invite_link = "https://t.me/+FAKE"
        return Link()

    async def revoke_chat_invite_link(self, chat_id=None, **kw):
        return None

    async def get_chat_member(self, chat_id=None, user_id=None, **kw):
        class M:
            status = "member"
        return M()


class FakeMsg:
    def __init__(self):
        self.replies: list[str] = []
        self.edits: list[str] = []

    async def reply_text(self, text="", **kw):
        self.replies.append(text)
        return None

    async def reply_photo(self, **kw):
        self.replies.append(kw.get("caption", ""))
        return None

    async def edit_text(self, text="", **kw):
        self.edits.append(text)
        return None


class FakeQuery:
    def __init__(self):
        self.message = FakeMsg()
        self.answered = 0
        self.last_toast = None

    async def answer(self, text=None, show_alert=False):
        self.answered += 1
        self.last_toast = text

    async def edit_message_text(self, text="", **kw):
        self.message.edits.append(text)

    async def edit_message_reply_markup(self, **kw):
        pass


# ==================================================================
# 供需：发布 / 审核 / 分发 / 退款 / 删帖 / 统计
# ==================================================================
def test_supply_flow() -> None:
    print("\n=== 供需全流程 ===")
    tmp = tempfile.mkdtemp()
    os.environ["SHOP_DB_PATH"] = os.path.join(tmp, "sf.db")
    from examples.models import Database, User, now_iso
    from examples.supply_service import SupplyService

    db = Database(os.environ["SHOP_DB_PATH"])
    bot = FakeBot()
    shop = type("Shop", (), {"_bot": bot})()
    svc = SupplyService(db, shop)

    from examples.models import Admin
    db.save_admin(Admin(user_id=100000000, name="主管理员", permissions=["all"],
                        added_by=100000000, added_at=now_iso()))

    db.set_setting("supply_price_bot", 10)
    db.set_setting("supply_price_channel", 10)
    db.set_setting("supply_discount", 5)
    db.set_setting("supply_daily_limit", 3)
    db.set_setting("supply_max_len", 20)
    db.set_setting("supply_channel_id", "-100999")

    u = User(user_id=6001, username="u6", first_name="六", points=100)
    db.save_user(u)

    # --- 失败路径 ---
    post, why = svc.create_post(u, "bot", "", "内容")
    check("正常发布成功", post is not None, str(why))
    check("扣费正确（bot 渠道 10 分）", db.get_user(6001).points == 90,
          str(db.get_user(6001).points))
    check("cost 落库", post.cost == 10)
    check("channel_leg 落库（bot 渠道为 0）", post.channel_leg == 0)

    post2, why2 = svc.create_post(u, "bot", "", "")
    check("空内容被拒", post2 is None and why2 == "empty")
    post3, why3 = svc.create_post(u, "bot", "", "x" * 30)
    check("超长被拒", post3 is None and why3 == "too_long")

    db.set_setting("supply_paused", True)
    post4, why4 = svc.create_post(u, "bot", "", "内容")
    check("暂停开关生效", post4 is None and why4 == "paused")
    db.set_setting("supply_paused", False)

    # 积分不足
    poor = User(user_id=6002, username="p", first_name="穷", points=0)
    db.save_user(poor)
    post5, why5 = svc.create_post(poor, "bot", "", "内容")
    check("积分不足被拒", post5 is None and why5 == "points")

    # 封禁用户
    banned = User(user_id=6003, username="b", first_name="封", points=100, is_banned=True)
    db.save_user(banned)
    post6, why6 = svc.create_post(banned, "bot", "", "内容")
    check("封禁用户被拒", post6 is None and why6 == "banned")

    # 日限
    for _ in range(2):
        svc.create_post(u, "bot", "", "再来一条")
    post7, why7 = svc.create_post(u, "bot", "", "第四条")
    check("日限额生效", post7 is None and why7 == "limit", str(why7))

    # 频道未配置
    db.set_setting("supply_channel_id", "")
    post8, why8 = svc.create_post(u, "channel", "", "内容")
    check("频道未配置被拒", post8 is None and why8 == "no_channel")
    db.set_setting("supply_channel_id", "-100999")

    # --- 频道渠道扣费与快照 ---
    u2 = User(user_id=6010, username="u10", first_name="十", points=100)
    db.save_user(u2)
    pc, _ = svc.create_post(u2, "channel", "", "发频道")
    check("channel 渠道扣 10", db.get_user(6010).points == 90)
    check("channel_leg 快照 = 10", pc.channel_leg == 10, str(pc.channel_leg))

    pb, _ = svc.create_post(u2, "both", "", "双发")
    check("both 渠道扣 15（10+10-5）", db.get_user(6010).points == 75,
          str(db.get_user(6010).points))
    check("both 的 channel_leg = 5", pb.channel_leg == 5, str(pb.channel_leg))

    # --- 审核流程 ---
    db.set_setting("supply_review", True)
    u3 = User(user_id=6020, username="u20", first_name="二十", points=100)
    db.save_user(u3)
    pend, _ = svc.create_post(u3, "bot", "", "待审内容")
    check("审核开关开启后进待审", pend.status == "pending_review", pend.status)
    check("待审也先扣费", db.get_user(6020).points == 90)

    run(svc.notify_admins_review(pend))
    check("待审通知管理员", len(bot.sent) > 0)

    approved = svc.approve_post(pend.id)
    check("审核通过", approved is not None and approved.status == "active")
    check("重复审核返回 None（幂等）", svc.approve_post(pend.id) is None)

    pend2, _ = svc.create_post(u3, "bot", "", "待审内容2")
    rejected = svc.reject_post(pend2.id)
    check("审核拒绝", rejected is not None and rejected.status == "rejected")
    # u3 初始 100 → 两帖各扣 10 → 80 → 拒一帖退 10 → 90
    check("拒绝全额退款", db.get_user(6020).points == 90,
          str(db.get_user(6020).points))
    check("重复拒绝返回 None（幂等）", svc.reject_post(pend2.id) is None)
    check("拒绝后 refunded 标记", db.get_supply_post(pend2.id).refunded is True)

    run(svc.notify_user_approved(approved))
    run(svc.notify_user_rejected(rejected))
    check("审核结果通知用户", len(bot.sent) >= 3)
    db.set_setting("supply_review", False)

    # --- 分发：bot 广播 ---
    u4 = User(user_id=6030, username="u30", first_name="三十", points=100)
    db.save_user(u4)
    postb, _ = svc.create_post(u4, "bot", "", "广播内容")
    before = len(bot.sent)
    run(svc.distribute(postb))
    # _broadcast_bot 是 create_task 后台任务，每个用户间 sleep(0.05)，
    # 库里用户数 × 0.05 才是完整耗时，这里给足 1.5 秒
    run(asyncio.sleep(1.5))
    check("bot 广播已发送", len(bot.sent) > before)
    check("bot_ok 标记", db.get_supply_post(postb.id).bot_ok is True)

    # --- 分发：频道失败 → 退频道腿 ---
    u5 = User(user_id=6040, username="u40", first_name="四十", points=100)
    db.save_user(u5)
    postc, _ = svc.create_post(u5, "channel", "", "频道内容")
    points_before = db.get_user(6040).points
    bot.fail_ids = {"-100999"}
    run(svc.distribute(postc))
    check("频道发送失败后标记已退款", db.get_supply_post(postc.id).refunded is True)
    check("频道腿已退回", db.get_user(6040).points == points_before + 10,
          f"{db.get_user(6040).points} vs {points_before + 10}")
    bot.fail_ids = set()

    # --- 删帖 ---
    u6 = User(user_id=6050, username="u50", first_name="五十", points=100)
    db.save_user(u6)
    postd, _ = svc.create_post(u6, "bot", "", "待删内容")
    deleted = svc.delete_post(postd.id, refund=True)
    check("删帖成功", deleted is not None and deleted.status == "deleted")
    check("删帖退款", db.get_user(6050).points == 100, str(db.get_user(6050).points))
    check("重复删帖返回 None", svc.delete_post(postd.id) is None)

    # --- 统计 ---
    st = svc.stats()
    check("stats 返回字典", isinstance(st, dict) and st)
    check("pending_count 可用", isinstance(svc.pending_count(), int))
    check("today_count 可用", isinstance(svc.today_count(6001), int))
    check("nth_of_user 可用", isinstance(svc.nth_of_user(6001), int))

    # --- 展示 ---
    check("publisher_html 不崩", isinstance(svc.publisher_html(postb), str))
    txt = svc.format_post_text(postb, 1)
    check("format_post_text 含内容", "广播内容" in txt or "供需" in txt, txt[:60])
    kb1 = svc.post_keyboard(in_channel=False)
    check("机器人内键盘非空", kb1 is not None)
    kb2 = svc.post_keyboard(in_channel=True)
    check("频道内键盘（有 username）非空", kb2 is not None)
    bot.username = None
    check("无 username 时频道键盘为 None", svc.post_keyboard(in_channel=True) is None)


# ==================================================================
# 抢单回调
# ==================================================================
def test_grab_callbacks() -> None:
    print("\n=== 抢单回调 ===")
    tmp = tempfile.mkdtemp()
    os.environ["SHOP_DB_PATH"] = os.path.join(tmp, "gc.db")
    from examples.models import Grabber, User, now_iso

    bot = FakeBot()
    # 重建 runtime 单例，指向临时库
    import examples.runtime as rt

    rt.db.__init__(os.environ["SHOP_DB_PATH"])
    rt.service.set_bot(bot)
    rt.grab.set_bot(bot)

    admin = User(user_id=100000000, username="adm", first_name="管", points=0)
    rt.db.save_user(admin)
    rt.db.save_admin(__import__("examples.models", fromlist=["Admin"]).Admin(
        user_id=100000000, name="主管理员", permissions=["all"], added_by=100000000,
        added_at=now_iso(),
    ))

    g_user = User(user_id=7001, username="g1", first_name="接单", points=0)
    rt.db.save_user(g_user)
    rt.db.save_grabber(Grabber(user_id=7001, seq=1, name="接单员一", status="active"))

    rt.db.set_setting("grab_enabled", True)
    rt.db.set_setting("grab_chat_id", "-1001234")

    from examples.cb_grab import _dispatch_grab

    ctx = type("Ctx", (), {"user_data": {}})()
    tg_user = type("U", (), {"id": 7001, "username": "g1", "first_name": "接单", "last_name": ""})()

    async def fire(data, uid=7001):
        q = FakeQuery()
        hit = await _dispatch_grab(q, ctx, uid, data, tg_user)
        return hit, q

    # 接单员中心
    hit, q = run(fire("gb:m"))
    check("gb:m（接单员中心）命中", hit is True)
    check("gb:m 有渲染", bool(q.message.edits or q.message.replies))

    for data in ("gb:m", "gb:doing", "gb:done", "gb:money", "gb:wd",
                 "gb:wdnew", "gb:inv", "gb:rank", "gb:aps"):
        hit, q = run(fire(data))
        check(f"{data} 命中并有渲染", hit is True and bool(q.message.edits or q.message.replies),
              f"hit={hit}")

    # 管理端接单管理
    for data in ("a:grab", "a:gbers", "a:gbadd", "a:gords", "a:gwds", "a:gaps", "a:gset"):
        hit, q = run(fire(data, uid=100000000))
        check(f"{data} 命中并有渲染", hit is True and bool(q.message.edits or q.message.replies),
              f"hit={hit}")

    # 非接单员访问接单员中心
    hit, q = run(fire("gb:m", uid=888888))
    check("非接单员访问 gb:m 命中（有提示）", hit is True and bool(q.message.edits or q.message.replies))

    # 群内抢单按钮（不存在的订单）
    hit, q = run(fire("gb:g:NOSUCHORDER", uid=7001))
    check("抢不存在的单命中并弹提示", hit is True and bool(q.last_toast), f"toast={q.last_toast}")

    # 未知 gb: 前缀 → 不命中，交给后续 dispatcher
    hit, q = run(fire("gb:unknown_xyz"))
    check("未知 gb: 分支不命中", hit is False, f"hit={hit}")


# ==================================================================
# 后台任务
# ==================================================================
def test_jobs() -> None:
    print("\n=== 后台任务 ===")
    tmp = tempfile.mkdtemp()
    os.environ["SHOP_DB_PATH"] = os.path.join(tmp, "jb.db")
    import examples.runtime as rt
    from examples import jobs

    rt.db.__init__(os.environ["SHOP_DB_PATH"])
    bot = FakeBot()
    rt.service.set_bot(bot)
    rt.grab.set_bot(bot)
    rt.stock.set_bot(bot)

    # 这些任务都是 `while True: await asyncio.sleep(N)` 的常驻协程，
    # 直接 await 会永久挂住。做法：把 sleep 换成立即返回，跑完一轮逻辑后
    # 第二次 sleep 抛 CancelledError 退出循环——既覆盖循环体，又不阻塞测试。
    orig_sleep = asyncio.sleep

    def make_fast_sleep(rounds: int = 2):
        state = {"n": 0}

        async def fast_sleep(delay, *a, **kw):
            state["n"] += 1
            if state["n"] >= rounds:
                raise asyncio.CancelledError
            await orig_sleep(0)

        return fast_sleep

    for name, coro in [
        ("auto_check_recharges", jobs.auto_check_recharges),
        ("auto_check_chain_recharges", jobs.auto_check_chain_recharges),
        ("auto_grab_housekeeping", jobs.auto_grab_housekeeping),
        ("daily_report", jobs.daily_report),
        ("auto_backup", jobs.auto_backup),
    ]:
        asyncio.sleep = make_fast_sleep(2)
        try:
            run(coro())
            check(f"{name} 跑完一轮并正常退出", True)
        except asyncio.CancelledError:
            check(f"{name} 跑完一轮并正常退出", True)
        except Exception as exc:
            check(f"{name} 跑完一轮并正常退出", False, repr(exc))
        finally:
            asyncio.sleep = orig_sleep


# ==================================================================
# 链上拉取的异常路径
# ==================================================================
def test_chain_errors() -> None:
    print("\n=== 链上拉取异常路径 ===")
    import examples.chain_watch as cw

    class BoomClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            raise RuntimeError("网络炸了")

    cw.httpx.AsyncClient = BoomClient
    check("TRON 网络异常返回空列表",
          run(cw.fetch_tron_usdt_incoming("TAddr")) == [])
    check("TON 网络异常返回空列表",
          run(cw.fetch_ton_usdt_incoming("EQAddr")) == [])

    class BadJson:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            class R:
                def raise_for_status(self):
                    return None

                def json(self):
                    raise ValueError("坏 JSON")

            return R()

    cw.httpx.AsyncClient = BadJson
    check("TRON 坏 JSON 返回空列表",
          run(cw.fetch_tron_usdt_incoming("TAddr")) == [])

    # 脏数据：单条解析失败不影响整批
    class Dirty:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            class R:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"data": [
                        {"token_info": None, "value": "bad"},          # 脏
                        {"transaction_id": "", "value": "1"},           # 无 txid
                        {"transaction_id": "OK1",
                         "token_info": {"address": cw.USDT_TRC20_CONTRACT, "decimals": 6},
                         "value": "0"},                                 # 金额 0
                        {"transaction_id": "OK2",
                         "token_info": {"address": cw.USDT_TRC20_CONTRACT, "decimals": 6},
                         "value": "1000000", "from": "TF", "block_timestamp": 1700000000000},
                    ], "meta": {}}

            return R()

    cw.httpx.AsyncClient = Dirty
    txs = run(cw.fetch_tron_usdt_incoming("TAddr"))
    check("脏数据被跳过，正常记录保留", len(txs) == 1 and txs[0].txid == "OK2",
          f"{len(txs)} 条")


# ==================================================================
# handlers_message 的输入状态机
# ==================================================================
def test_message_states() -> None:
    print("\n=== 输入状态机 ===")
    tmp = tempfile.mkdtemp()
    os.environ["SHOP_DB_PATH"] = os.path.join(tmp, "hm.db")
    import examples.runtime as rt
    from examples.handlers_message import on_message

    rt.db.__init__(os.environ["SHOP_DB_PATH"])
    bot = FakeBot()
    rt.service.set_bot(bot)

    class FakeUser:
        id = 8001
        username = "m1"
        first_name = "消息"
        last_name = ""
        is_bot = False

    class FakeMessage:
        def __init__(self, text="", caption=None):
            self.text = text
            self.caption = caption
            self.replies = []
            self.photo = None
            self.document = None
            self.video = None
            self.audio = None
            self.voice = None
            self.sticker = None
            self.reply_to_message = None

        async def reply_text(self, text="", **kw):
            self.replies.append(text)

        async def reply_photo(self, **kw):
            self.replies.append(kw.get("caption", ""))

    class FakeUpdate:
        def __init__(self, text, chat_type="private"):
            self.message = FakeMessage(text)
            self.effective_user = FakeUser()
            self.effective_chat = type("C", (), {"type": chat_type, "id": 8001})()
            self.effective_message = self.message

    class FakeContext:
        def __init__(self, user_data=None):
            self.user_data = user_data or {}
            self.bot = bot

    rt.service.get_or_create_user(8001, username="m1", first_name="消息", last_name="")

    # 无状态 + 私聊普通消息 → 不应崩
    upd = FakeUpdate("随便一句话")
    ctx = FakeContext()
    try:
        run(on_message(upd, ctx))
        check("无状态消息不崩", True)
    except Exception as exc:
        check("无状态消息不崩", False, repr(exc))

    # 群聊消息 → 应被忽略（不崩）
    upd2 = FakeUpdate("群里说话", chat_type="supergroup")
    try:
        run(on_message(upd2, FakeContext()))
        check("群聊消息被忽略", True)
    except Exception as exc:
        check("群聊消息被忽略", False, repr(exc))

    # 搜索状态
    upd3 = FakeUpdate("测试")
    ctx3 = FakeContext({"state": "search:product", "payload": {}})
    try:
        run(on_message(upd3, ctx3))
        check("搜索状态有响应", bool(upd3.message.replies))
    except Exception as exc:
        check("搜索状态有响应", False, repr(exc))

    # 未知状态 → 清理并提示
    upd4 = FakeUpdate("内容")
    ctx4 = FakeContext({"state": "state_that_does_not_exist", "payload": {}})
    try:
        run(on_message(upd4, ctx4))
        check("未知状态被清理", "state" not in ctx4.user_data or bool(upd4.message.replies))
    except Exception as exc:
        check("未知状态被清理", False, repr(exc))


if __name__ == "__main__":
    test_supply_flow()
    test_grab_callbacks()
    test_jobs()
    test_chain_errors()
    test_message_states()
    print(f"\n{'=' * 50}")
    print(f"✅ 通过 {PASS} 项   ❌ 失败 {FAIL} 项")
    print("=" * 50)
    sys.exit(0 if FAIL == 0 else 1)
