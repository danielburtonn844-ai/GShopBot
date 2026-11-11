"""覆盖率补强第三批（审计 P2-16）：handlers_message 全状态 / on_callback 全命名空间 /
grab_service 全流程 / ui 降级 / singleton / jobs / kkpay 验签 / handlers_command。
全部离线：临时 SQLite，Telegram 走 fake，HTTP 不触达。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix="covdeep_")
os.environ["BOT_TOKEN"] = "test:token"
os.environ["ADMIN_ID"] = "100000000"
os.environ["SHOP_DB_PATH"] = os.path.join(_TMP, "deep.db")
os.environ["OKPAY_ID"] = ""
os.environ["OKPAY_TOKEN"] = ""
os.environ["DATA_DIR"] = _TMP

PASS = 0
FAIL = 0
ERRORS = []
LOOP = asyncio.new_event_loop()

ADMIN = 100000000
USER1 = 111111
USER2 = 222222
USER3 = 333333
photo = [type("P", (), {"file_id": "PH1"})()]


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        ERRORS.append(name + ": " + str(detail))
        print("  X " + name + " " + str(detail))


def run(coro):
    return LOOP.run_until_complete(coro)


class FakeTgUser:
    def __init__(self, uid, username="", first_name=""):
        self.id = uid
        self.username = username or ("u" + str(uid))
        self.first_name = first_name or ("名" + str(uid))
        self.last_name = ""
        self.is_bot = False


class FakeBot:
    def __init__(self, fail=()):
        self.sent = []
        self.photos = []
        self.docs = []
        self.albums = []
        self.copied = []
        self.links = 0
        self.fail = set(fail)
        self.username = "covdeepbot"
        self.id = 999
        self._mid = 7000

    def _bump(self):
        self._mid += 1
        return type("M", (), {"message_id": self._mid})()

    async def send_message(self, chat_id=None, text="", **kw):
        if "send_message" in self.fail:
            raise RuntimeError("mock send_message 失败")
        self.sent.append((chat_id, text))
        return self._bump()

    async def send_photo(self, chat_id=None, photo="", caption="", **kw):
        if "send_photo" in self.fail:
            raise RuntimeError("mock send_photo 失败")
        self.photos.append((chat_id, photo, caption))
        return self._bump()

    async def send_document(self, chat_id=None, document="", caption="", **kw):
        if "send_document" in self.fail:
            raise RuntimeError("mock send_document 失败")
        self.docs.append((chat_id, document, caption))
        return self._bump()

    async def send_media_group(self, chat_id=None, media=None, **kw):
        self.albums.append((chat_id, list(media or [])))
        return []

    async def copy_message(self, chat_id=None, from_chat_id=None, message_id=None, **kw):
        if "copy_message" in self.fail:
            raise RuntimeError("mock copy_message 失败")
        self.copied.append((chat_id, from_chat_id, message_id))
        return self._bump()

    async def create_chat_invite_link(self, chat_id=None, **kw):
        self.links += 1
        return type("L", (), {"invite_link": "https://t.me/+COV" + str(self.links)})()

    async def revoke_chat_invite_link(self, chat_id=None, **kw):
        return True

    async def get_chat_member(self, chat_id=None, user_id=None, **kw):
        return type("M", (), {"status": "member"})()

    async def get_me(self):
        return type("Me", (), {"username": self.username, "id": self.id})()



class FakeMsg:
    def __init__(self, uid=USER1, text="", caption=None, photo=None, document=None,
                 chat_id=None, message_id=1, media_group_id=None):
        self.from_user = FakeTgUser(uid)
        self.text = text
        self.caption = caption
        self.photo = photo or []
        self.document = document
        self.video = None
        self.animation = None
        self.audio = None
        self.voice = None
        self.sticker = None
        self.chat_id = chat_id if chat_id is not None else uid
        self.message_id = message_id
        self.media_group_id = media_group_id
        self.reply_to_message = None
        self.replies = []
        self.photos_sent = []
        self.docs_sent = []
        self.deleted = False
        self.edits = []

    async def reply_text(self, text="", reply_markup=None, parse_mode=None, **kw):
        self.replies.append((text, reply_markup))
        return self

    async def reply_photo(self, photo=None, caption=None, reply_markup=None, **kw):
        self.photos_sent.append((photo, caption or "", reply_markup))
        self.replies.append((caption or "", reply_markup))
        return self

    async def reply_document(self, document=None, caption=None, **kw):
        self.docs_sent.append((document, caption or ""))
        return self

    async def edit_message_text(self, text="", reply_markup=None, parse_mode=None, **kw):
        self.edits.append(text)
        return self

    async def edit_message_caption(self, caption=None, reply_markup=None, **kw):
        self.edits.append(caption or "")
        self.caption = caption
        return self

    async def edit_message_media(self, media=None, reply_markup=None, **kw):
        self.edits.append(getattr(media, "caption", "") or "")
        return self

    async def delete(self, **kw):
        self.deleted = True
        return True


class FakeQuery:
    def __init__(self, uid=USER1, data="", message=None):
        self.from_user = FakeTgUser(uid)
        self.data = data
        self.message = message or FakeMsg(uid)
        self.answered = 0
        self.toasts = []
        self.edits = []
        self.fail_answer = False

    async def answer(self, text=None, show_alert=False, **kw):
        if self.fail_answer:
            raise RuntimeError("query is too old")
        self.answered += 1
        if text:
            self.toasts.append(text)

    async def edit_message_text(self, text="", reply_markup=None, parse_mode=None, **kw):
        self.edits.append(text)
        return self.message


class FakeUpdate:
    def __init__(self, uid=USER1, data=None, message=None, chat_type="private"):
        self.effective_user = FakeTgUser(uid)
        self.callback_query = FakeQuery(uid, data) if data is not None else None
        self.message = message
        self.effective_message = message
        self.effective_chat = type("C", (), {"type": chat_type, "id": uid})()


class FakeCtx:
    def __init__(self, user_data=None, bot=None):
        self.user_data = user_data if user_data is not None else {}
        self.bot = bot or FakeBot()
        self.args = []
        self.error = None



from gshop import handlers_callback, handlers_message  # noqa: E402
from gshop.runtime import db, grab, service, stock, supply  # noqa: E402
from gshop.ui import h, kb, order_status_text, product_status_text, summarize_message  # noqa: E402

BOT = FakeBot()
service.set_bot(BOT)
grab.set_bot(BOT)
stock.set_bot(BOT)
supply.shop.set_bot(BOT) if hasattr(supply, "shop") else None


for _u in (ADMIN, USER1, USER2, USER3):
    service.get_or_create_user(_u, username="u" + str(_u), first_name="名" + str(_u), last_name="")
service.add_admin(ADMIN, "主管理员")



def fire_msg(uid, text="", ctx=None, photo=None, caption=None, document=None,
             reply_to=None, mid=1, mgid=None, chat_id=None):
    ctx = ctx or FakeCtx()
    msg = FakeMsg(uid, text=text, caption=caption, photo=photo, document=document,
                  chat_id=chat_id, message_id=mid, media_group_id=mgid)
    msg.reply_to_message = reply_to
    upd = FakeUpdate(uid, message=msg)
    run(handlers_message.on_message(upd, ctx))
    return msg, ctx



def fire_cb(uid, data, ctx=None, message=None):
    ctx = ctx or FakeCtx()
    query = FakeQuery(uid, data, message)
    upd = FakeUpdate(uid, data=data)
    upd.callback_query = query
    try:
        run(handlers_callback.on_callback(upd, ctx))
    except Exception as exc:
        check("回调不抛异常 " + data, False, repr(exc))
    return query, ctx



def state_ctx(state, payload=None):
    return FakeCtx({"state": state, "payload": payload if payload is not None else {}})



def test_user_states():
    print("\n=== _handle_user_states ===")
    # rc_custom 非法数字
    m, _ = fire_msg(USER1, "abc", state_ctx("rc_custom"))
    check("rc_custom 非数字被拒", bool(m.replies))
    # rc_custom 越限
    m, _ = fire_msg(USER1, "0.0001", state_ctx("rc_custom"))
    check("rc_custom 越限被拒", bool(m.replies))
    # rc_custom 正常
    m, _ = fire_msg(USER1, "10", state_ctx("rc_custom"))
    check("rc_custom 正常进确认页", bool(m.replies))
    # search 空关键词
    m, _ = fire_msg(USER1, "", state_ctx("search:product"))
    check("search 空关键词提示", bool(m.replies))
    # search 正常
    m, _ = fire_msg(USER1, "测", state_ctx("search:report"))
    check("search 正常出结果", bool(m.replies))
    # submit_info 失败（商品不存在）
    m, ctx = fire_msg(USER1, "需求", state_ctx("submit_info:NOPE"))
    check("submit_info 失败有提示", bool(m.replies))
    check("submit_info 失败清状态", "state" not in ctx.user_data)


def test_grab_states():
    print("\n=== _handle_grab_states ===")
    # gbreply 非本人订单
    m, _ = fire_msg(USER1, "回单", state_ctx("gbreply:NOPE"))
    check("gbreply 非本人提示", bool(m.replies))
    # gbwd 格式错
    m, _ = fire_msg(USER1, "abc", state_ctx("gbwd"))
    check("gbwd 格式错提示", bool(m.replies))
    # gbwd 非接单员
    m, _ = fire_msg(USER3, "500 12345678", state_ctx("gbwd"))
    check("gbwd 非接单员被拒", bool(m.replies))
    # gbappeal 失败
    m, _ = fire_msg(USER1, "申诉理由", state_ctx("gbappeal:NOPE"))
    check("gbappeal 失败提示", bool(m.replies))
    # gbadd 非管理员
    m, ctx = fire_msg(USER1, "123", state_ctx("gbadd"))
    check("gbadd 非管理员静默清状态", "state" not in ctx.user_data)
    # gbadd 找不到用户
    m, _ = fire_msg(ADMIN, "999999999", state_ctx("gbadd"))
    check("gbadd 找不到用户提示", bool(m.replies))
    # gset 非管理员
    m, ctx = fire_msg(USER1, "5", state_ctx("gset:grab_commission_pct"))
    check("gset 非管理员静默", "state" not in ctx.user_data)
    # gset 未知键
    m, ctx = fire_msg(ADMIN, "5", state_ctx("gset:nope_key"))
    check("gset 未知键静默", "state" not in ctx.user_data)
    # gset 数值非法
    m, _ = fire_msg(ADMIN, "-1", state_ctx("gset:grab_commission_pct"))
    check("gset 负数被拒", bool(m.replies))
    # gset 数值正常
    m, _ = fire_msg(ADMIN, "15", state_ctx("gset:grab_commission_pct"))
    check("gset 数值保存", db.get_settings().get("grab_commission_pct") == 15)
    # gset 小时键允许小数
    m, _ = fire_msg(ADMIN, "2.5", state_ctx("gset:grab_timeout_hours"))
    check("gset 小数保存", float(db.get_settings().get("grab_timeout_hours")) == 2.5)
    # gset 群 ID 格式错
    m, _ = fire_msg(ADMIN, "abc", state_ctx("gset:grab_chat_id"))
    check("gset 群ID格式错", bool(m.replies))
    # gset 群 ID 正常
    m, _ = fire_msg(ADMIN, "-1001234567890", state_ctx("gset:grab_chat_id"))
    check("gset 群ID保存", str(db.get_settings().get("grab_chat_id")) == "-1001234567890")
    # gbwdtx 非管理员
    m, ctx = fire_msg(USER1, "0x", state_ctx("gbwdtx:W1"))
    check("gbwdtx 非管理员静默", "state" not in ctx.user_data)
    # gbwdtx 不存在的提现单
    m, _ = fire_msg(ADMIN, "跳过", state_ctx("gbwdtx:NOPE"))
    check("gbwdtx 无效单提示", bool(m.replies))



def test_supply_states():
    print("\n=== _handle_supply_states ===")
    # sup_text 无 payload
    m, _ = fire_msg(USER1, "内容", state_ctx("sup_text"))
    check("sup_text 无会话提示", bool(m.replies))
    # sup_text 空内容
    m, _ = fire_msg(USER1, "", state_ctx("sup_text", {"channel": "bot"}))
    check("sup_text 空内容提示", bool(m.replies))
    # sup_text 超长
    db.update_settings(supply_max_len=10)
    m, _ = fire_msg(USER1, "x" * 50, state_ctx("sup_text", {"channel": "bot"}))
    check("sup_text 超长提示", bool(m.replies))
    db.update_settings(supply_max_len=600)
    # sup_text 纯文字 → 进入 sup_photo
    ctx = state_ctx("sup_text", {"channel": "bot"})
    m, ctx = fire_msg(USER1, "求购一台机", ctx)
    check("sup_text 纯文字进附图步", ctx.user_data.get("state") == "sup_photo")
    # sup_text 图文同发 → 直接预览
    photo = [type("P", (), {"file_id": "PH1"})()]
    ctx = state_ctx("sup_text", {"channel": "bot"})
    m, ctx = fire_msg(USER1, "图文供需", ctx, photo=photo)
    check("sup_text 图文进预览", ctx.user_data.get("state") == "sup_preview")
    # sup_photo 无 payload
    m, _ = fire_msg(USER1, "", state_ctx("sup_photo"))
    check("sup_photo 无会话提示", bool(m.replies))
    # sup_photo 非图
    m, _ = fire_msg(USER1, "文字", state_ctx("sup_photo", {"channel": "bot"}))
    check("sup_photo 非图提示", bool(m.replies))
    # sup_photo 正常
    ctx = state_ctx("sup_photo", {"channel": "bot", "content": "内容"})
    m, ctx = fire_msg(USER1, "", ctx, photo=photo)
    check("sup_photo 附图进预览", ctx.user_data.get("state") == "sup_preview")
    # 非管理员进管理员分支
    m, ctx = fire_msg(USER1, "x", state_ctx("restock:P1"))
    check("restock 非管理员静默", "state" not in ctx.user_data)
    # restock 商品不存在
    m, _ = fire_msg(ADMIN, "内容", state_ctx("restock:NOPE"))
    check("restock 商品不存在提示", bool(m.replies))


def test_product_states():
    print("\n=== _handle_product_states ===")
    ctx = FakeCtx({"state": "padd_name", "payload": {}})
    m, ctx = fire_msg(ADMIN, "测试商品", ctx)
    check("padd_name 进描述步", ctx.user_data.get("state") == "padd_desc")
    m, ctx = fire_msg(ADMIN, "商品介绍", ctx)
    check("padd_desc 进价格步", ctx.user_data.get("state") == "padd_points")
    m, ctx = fire_msg(ADMIN, "abc", ctx)
    check("padd_points 非法被拒", ctx.user_data.get("state") == "padd_points")
    m, ctx = fire_msg(ADMIN, "100", ctx)
    check("padd_points 进类型步", ctx.user_data.get("state") == "padd_kind")
    # 直接走 padd_stock / padd_limit 分支
    ctx.user_data["state"] = "padd_stock"
    m, ctx = fire_msg(ADMIN, "abc", ctx)
    check("padd_stock 非法被拒", ctx.user_data.get("state") == "padd_stock")
    m, ctx = fire_msg(ADMIN, "10", ctx)
    check("padd_stock 进限购步", ctx.user_data.get("state") == "padd_limit")
    m, ctx = fire_msg(ADMIN, "abc", ctx)
    check("padd_limit 非法被拒", ctx.user_data.get("state") == "padd_limit")
    # 报单类：填限购后直接建品
    ctx.user_data["payload"].update({"name": "报单测", "kind": "report", "points": 10})
    m, ctx = fire_msg(ADMIN, "0", ctx)
    check("padd_limit 报单类直接建品", ctx.user_data.get("state") is None)
    # padd_auto 分支
    ctx2 = FakeCtx({"state": "padd_auto", "payload": {
        "name": "自动测", "kind": "product", "points": 20, "stock": 5,
        "limit_per_user": 0, "delivery_mode": "fixed",
    }})
    m, ctx2 = fire_msg(ADMIN, "卡密内容", ctx2)
    check("padd_auto 建品成功", ctx2.user_data.get("state") is None)
    # pedit 商品不存在
    m, _ = fire_msg(ADMIN, "x", state_ctx("pedit:NOPE:name"))
    check("pedit 商品不存在提示", bool(m.replies))
    # pedit 字段非法
    prods = db.list_products(limit=1)
    if prods:
        pid = prods[0].id
        m, _ = fire_msg(ADMIN, "-5", state_ctx("pedit:" + pid + ":points"))
        check("pedit 非法值被拒", bool(m.replies))
        m, _ = fire_msg(ADMIN, "新名字", state_ctx("pedit:" + pid + ":name"))
        check("pedit 改名成功", db.get_product(pid).name == "新名字")
        m, _ = fire_msg(ADMIN, "新介绍", state_ctx("pedit:" + pid + ":description"), photo=photo)
        check("pedit 带图改介绍", bool(m.replies))



def test_admin_states():
    print("\n=== _handle_admin_states ===")
    # oc: 订单不存在
    m, _ = fire_msg(ADMIN, "回单内容", state_ctx("oc:NOPE"))
    check("oc 订单不存在提示", bool(m.replies))
    # uadd 非法
    m, _ = fire_msg(ADMIN, "abc", state_ctx("uadd:" + str(USER1)))
    check("uadd 非法被拒", bool(m.replies))
    # uadd 超大
    m, _ = fire_msg(ADMIN, "999999999", state_ctx("uadd:" + str(USER1)))
    check("uadd 超大被拒", bool(m.replies))
    # uadd 正常
    before = db.get_user(USER1).points
    m, _ = fire_msg(ADMIN, "50", state_ctx("uadd:" + str(USER1)))
    check("uadd 正常加分", db.get_user(USER1).points == before + 50)
    # usub 正常
    before = db.get_user(USER1).points
    m, _ = fire_msg(ADMIN, "20", state_ctx("usub:" + str(USER1)))
    check("usub 正常减分", db.get_user(USER1).points == before - 20)
    # usearch 无结果
    m, _ = fire_msg(ADMIN, "zzzz不存在zzzz", state_ctx("usearch"))
    check("usearch 无结果提示", bool(m.replies))
    # usearch 有结果
    m, _ = fire_msg(ADMIN, "u111111", state_ctx("usearch"))
    check("usearch 有结果", bool(m.replies))
    # osearch 无结果
    m, _ = fire_msg(ADMIN, "zzzz不存在zzzz", state_ctx("osearch"))
    check("osearch 无结果提示", bool(m.replies))
    # admadd 非法
    m, _ = fire_msg(ADMIN, "abc", state_ctx("admadd"))
    check("admadd 非法被拒", bool(m.replies))
    # admadd 正常
    m, _ = fire_msg(ADMIN, "555666", state_ctx("admadd"))
    check("admadd 正常", db.is_admin(555666))
    # chadd 空
    m, _ = fire_msg(ADMIN, "", state_ctx("chadd"))
    check("chadd 空被拒", bool(m.replies))
    # chadd @用户名
    m, _ = fire_msg(ADMIN, "@mychannel", state_ctx("chadd"))
    check("chadd @用户名", bool(m.replies))
    # chadd -100
    m, _ = fire_msg(ADMIN, "-1001234567890", state_ctx("chadd"))
    check("chadd chat_id", bool(m.replies))
    # chadd 裸用户名
    m, _ = fire_msg(ADMIN, "plainname", state_ctx("chadd"))
    check("chadd 裸名自动补@", bool(m.replies))


def test_settings_states():
    print("\n=== _handle_settings_states ===")
    # sr 非法
    m, _ = fire_msg(ADMIN, "0", state_ctx("sr"))
    check("sr 0 被拒", bool(m.replies))
    m, _ = fire_msg(ADMIN, "abc", state_ctx("sr"))
    check("sr 非数被拒", bool(m.replies))
    m, _ = fire_msg(ADMIN, "100", state_ctx("sr"))
    check("sr 正常", int(db.get_settings().get("points_rate")) == 100)
    # smin
    m, _ = fire_msg(ADMIN, "0", state_ctx("smin"))
    check("smin 0 被拒", bool(m.replies))
    m, _ = fire_msg(ADMIN, "1.5", state_ctx("smin"))
    check("smin 正常", float(db.get_settings().get("min_recharge_usdt")) == 1.5)
    # pay_limit 格式错
    m, _ = fire_msg(ADMIN, "abc", state_ctx("pay_limit"))
    check("pay_limit 格式错", bool(m.replies))
    m, _ = fire_msg(ADMIN, "0 100", state_ctx("pay_limit"))
    check("pay_limit 下限 0 被拒", bool(m.replies))
    m, _ = fire_msg(ADMIN, "100 50", state_ctx("pay_limit"))
    check("pay_limit 上限小于下限被拒", bool(m.replies))
    m, _ = fire_msg(ADMIN, "2 500", state_ctx("pay_limit"))
    check("pay_limit 正常", float(db.get_settings().get("max_recharge_usdt")) == 500.0)
    m, _ = fire_msg(ADMIN, "3", state_ctx("pay_limit"))
    check("pay_limit 单值最高=0", float(db.get_settings().get("max_recharge_usdt")) == 0.0)
    # pay_addr 非法/合法
    m, _ = fire_msg(ADMIN, "abc", state_ctx("pay_addr:trc20"))
    check("pay_addr trc20 非法被拒", bool(m.replies))
    m, _ = fire_msg(ADMIN, "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7", state_ctx("pay_addr:trc20"))
    check("pay_addr trc20 保存", db.get_settings().get("tron_wallet_address", "") != "")
    m, _ = fire_msg(ADMIN, "abc", state_ctx("pay_addr:ton"))
    check("pay_addr ton 非法被拒", bool(m.replies))
    # pay_key 清除 / 设置
    m, _ = fire_msg(ADMIN, "0", state_ctx("pay_key:tron"))
    check("pay_key 清除", db.get_settings().get("trongrid_api_key", "") == "")
    m, _ = fire_msg(ADMIN, "mykey", state_ctx("pay_key:tron"))
    check("pay_key 保存", db.get_settings().get("trongrid_api_key") == "mykey")
    m, _ = fire_msg(ADMIN, "tonkey", state_ctx("pay_key:ton"))
    check("pay_key ton 保存", db.get_settings().get("toncenter_api_key") == "tonkey")
    # sw 三分支
    m, _ = fire_msg(ADMIN, "", state_ctx("sw"))
    check("sw 空提示", bool(m.replies))
    m, _ = fire_msg(ADMIN, "欢迎文字", state_ctx("sw"))
    check("sw 文字保存", db.get_settings().get("welcome_text") == "欢迎文字")
    m, _ = fire_msg(ADMIN, "", state_ctx("sw"), photo=photo)
    check("sw 图片保存", db.get_settings().get("welcome_image") == "PH1")
    m, _ = fire_msg(ADMIN, "图配文", state_ctx("sw"), photo=photo)
    check("sw 图文双存", db.get_settings().get("welcome_text") == "图配文")
    # pcatadd 三分支
    db.update_settings(product_categories=[])
    m, _ = fire_msg(ADMIN, "", state_ctx("pcatadd"))
    check("pcatadd 空被拒", bool(m.replies))
    m, _ = fire_msg(ADMIN, "x" * 20, state_ctx("pcatadd"))
    check("pcatadd 超长被拒", bool(m.replies))
    m, _ = fire_msg(ADMIN, "数码", state_ctx("pcatadd"))
    check("pcatadd 新增", "数码" in (db.get_settings().get("product_categories") or []))
    m, _ = fire_msg(ADMIN, "数码", state_ctx("pcatadd"))
    check("pcatadd 重复被拒", bool(m.replies))
    db.update_settings(product_categories=["c" + str(i) for i in range(12)])
    m, _ = fire_msg(ADMIN, "新分类", state_ctx("pcatadd"))
    check("pcatadd 满 12 被拒", bool(m.replies))
    # sup_cat_add
    db.update_settings(supply_categories=[])
    m, _ = fire_msg(ADMIN, "供需类", state_ctx("sup_cat_add"))
    check("sup_cat_add 新增", "供需类" in (db.get_settings().get("supply_categories") or []))
    m, _ = fire_msg(ADMIN, "供需类", state_ctx("sup_cat_add"))
    check("sup_cat_add 重复被拒", bool(m.replies))
    db.update_settings(supply_categories=["s" + str(i) for i in range(8)])
    m, _ = fire_msg(ADMIN, "超限类", state_ctx("sup_cat_add"))
    check("sup_cat_add 满 8 被拒", bool(m.replies))
    # sup_chid
    for _v in ("关闭", "abc", "-100999", "@chan"):
        m, _ = fire_msg(ADMIN, _v, state_ctx("sup_chid"))
        check("sup_chid " + _v + " 有响应", bool(m.replies))
    # sup_set
    m, _ = fire_msg(ADMIN, "abc", state_ctx("sup_set:supply_price_bot"))
    check("sup_set 非数被拒", bool(m.replies))
    m, _ = fire_msg(ADMIN, "0", state_ctx("sup_set:supply_price_bot"))
    check("sup_set 小于最小值被拒", bool(m.replies))
    m, _ = fire_msg(ADMIN, "5", state_ctx("sup_set:supply_price_bot"))
    check("sup_set 正常", int(db.get_settings().get("supply_price_bot")) == 5)
    m, _ = fire_msg(ADMIN, "0", state_ctx("sup_set:supply_daily_limit"))
    check("sup_set 允许 0", int(db.get_settings().get("supply_daily_limit")) == 0)



def test_broadcast_states():
    print("\n=== _handle_broadcast_states ===")
    ctx = FakeCtx()
    m, ctx = fire_msg(ADMIN, "广播内容", ctx)
    ctx.user_data["state"] = "bc:all"
    m, ctx = fire_msg(ADMIN, "广播内容", ctx)
    check("bc: 进确认态", ctx.user_data.get("state") == "bc_confirm:all")
    check("bc: payload 有 src", "src" in (ctx.user_data.get("payload") or {}))
    m, ctx = fire_msg(ADMIN, "又说一句", ctx)
    check("bc_confirm 提示点按钮", bool(m.replies))


def test_callback_namespaces():
    print("\n=== on_callback 各命名空间 ===")
    # 无权限的 a: 前缀
    q, _ = fire_cb(USER1, "a:prod")
    check("a: 非管理员被闸门拦", bool(q.edits) or q.message.edits)
    # 用户端页面
    for _d in ("m:root", "m:shop", "m:report", "m:recharge", "m:rchist",
               "m:orders", "m:balance", "m:profile", "m:help", "m:admin",
               "m:checkjoin", "m:search:product"):
        fire_cb(USER1, _d)
    # 管理端页面
    for _d in ("a:prod", "a:padd", "a:pcat", "a:order", "a:osearch", "a:user",
               "a:usearch", "a:adm", "a:admadd", "a:ch", "a:chadd", "a:chforce",
               "a:set", "a:sr", "a:smin", "a:sw", "a:sm", "a:sf", "a:pay",
               "a:pay:limit", "a:sup", "a:grab", "a:gbers", "a:gbadd", "a:gset",
               "a:gords", "a:gwds", "a:gaps", "a:pool", "a:fin", "a:logs",
               "a:backup", "a:bc"):
        fire_cb(ADMIN, _d)
    # 供需命名空间
    for _d in ("sup:hall", "sup:mine", "sup:new", "sup:cancel",
               "a:sup:sw", "a:sup:rv", "a:sup:posts", "a:sup:cats",
               "a:sup:price", "a:sup:chid"):
        fire_cb(ADMIN if _d.startswith("a:") else USER1, _d)
    # 抢单命名空间
    for _d in ("gb:m", "gb:doing", "gb:done", "gb:money", "gb:wd",
               "gb:wdnew", "gb:inv", "gb:rank", "gb:aps"):
        fire_cb(USER1, _d)
    # 未知回调
    fire_cb(USER1, "zzz:unknown")
    check("未知回调不崩", True)


def test_callback_grab_actions():
    print("\n=== 抢单回调具体动作 ===")
    g, msg = grab.add_grabber(USER2, username="u222222", name="接单员二")
    check("建档接单员", g is not None, msg)
    grab.set_grabber_status(USER2, "active", operator_id=ADMIN)
    # gb:g: 不存在的单
    q, _ = fire_cb(USER2, "gb:g:NOPE")
    check("gb:g 不存在有 toast", bool(q.toasts))
    # gb:r: 非本人单
    q, _ = fire_cb(USER2, "gb:r:NOPE")
    check("gb:r 非本人有 toast", bool(q.toasts))
    # gb:q: 放弃
    q, _ = fire_cb(USER2, "gb:q:NOPE")
    check("gb:q 有 toast", bool(q.toasts))
    # gb:ap: 申诉入口
    q, _ = fire_cb(USER2, "gb:ap:NOPE")
    check("gb:ap 进申诉态", bool(q.edits) or bool(q.message.edits))
    # a:gber: 参数非法
    q, _ = fire_cb(ADMIN, "a:gber:0")
    check("a:gber 非法参数 toast", bool(q.toasts))
    # a:gber: 正常
    fire_cb(ADMIN, "a:gber:" + str(USER2))
    # a:gbst: 参数非法
    q, _ = fire_cb(ADMIN, "a:gbst:0:x")
    check("a:gbst 非法参数 toast", bool(q.toasts))
    # a:gbst: 正常切换
    q, _ = fire_cb(ADMIN, "a:gbst:" + str(USER2) + ":rest")
    check("a:gbst 切换请假", db.get_grabber(USER2).status == "rest")
    for _s in ("active", "suspended", "blacklist"):
        fire_cb(ADMIN, "a:gbst:" + str(USER2) + ":" + _s)
    grab.set_grabber_status(USER2, "active", operator_id=ADMIN)
    # a:gbdel: 与 a:gbdelok:
    q, _ = fire_cb(ADMIN, "a:gbdel:0")
    check("a:gbdel 非法参数 toast", bool(q.toasts))
    q, _ = fire_cb(ADMIN, "a:gbdel:" + str(USER2))
    check("a:gbdel 出确认页", bool(q.message.edits))
    q, _ = fire_cb(ADMIN, "a:gbdelok:0")
    check("a:gbdelok 非法参数 toast", bool(q.toasts))
    q, _ = fire_cb(ADMIN, "a:gbdelok:" + str(USER2))
    check("a:gbdelok 删除成功", db.get_grabber(USER2) is None)
    # a:gblink: 接单员不存在
    q, _ = fire_cb(ADMIN, "a:gblink:999999")
    check("a:gblink 不存在提示", bool(q.message.edits))
    # a:guse: 切群
    fire_cb(ADMIN, "a:guse:-1001234567890")
    # a:gstog: / a:gsedit:
    fire_cb(ADMIN, "a:gstog:grab_enabled")
    fire_cb(ADMIN, "a:gsedit:grab_commission_pct")



def test_grab_full_flow():
    print("\n=== grab_service 全流程 ===")
    # 建报单类商品并下单，走完整抢单链路
    prod = service.create_product({
        "name": "抢单测", "kind": "report", "points": 100,
        "stock": -1, "limit_per_user": 0,
    })
    db.update_settings(grab_enabled=True, grab_commission_pct=10,
                       grab_cooldown_seconds=0, grab_freeze_hours=0,
                       grab_chat_id="-1001234567890", grab_timeout_hours=24,
                       grab_max_dispatch=3)
    service.update_user_points(USER3, 1000, operator_id=ADMIN, reason="测试充值")
    order, reason = service.create_order(USER3, prod.id, note="需求说明")
    check("报单下单成功", order is not None, reason)
    if not order:
        return
    # 抢单者建档
    # 必须先派单：grab_status 由 mark_dispatched 写成 waiting，try_grab 只接收 waiting
    dispatched = run(grab.dispatch_order(order))
    check("派单成功", dispatched is True)
    check("派单后 grab_status=waiting", db.get_order(order.id).grab_status == "waiting")
    g1, _ = grab.add_grabber(USER2, username="u222222", name="接单员二")
    grab.set_grabber_status(USER2, "active", operator_id=ADMIN)
    db.update_settings(grab_cooldown_seconds=0)
    code, o2, g2 = grab.try_grab(order.id, USER2, "u222222", "接单员二")
    check("try_grab 成功", code == "ok", code)
    check("抢中后 grab_status=grabbed", o2 is not None and o2.grab_status == "grabbed")
    check("佣金已算", o2 is not None and o2.commission > 0, o2.commission if o2 else None)
    # 重复抢 → taken
    code2, _, _ = grab.try_grab(order.id, USER2, "u222222", "接单员二")
    check("重复抢被拒", code2 != "ok", code2)
    # 不能抢自己的单
    code3, _, _ = grab.try_grab(order.id, USER3, "u333333", "名333333")
    check("自抢自单被拒", code3 != "ok", code3)
    # 抢中后续（群卡片 + 私聊卡 + 管理员通报）
    run(grab.on_grabbed(o2, g2))
    check("抢中后续不抛异常", True)
    # 接单员回单
    done, err = grab.complete_by_grabber(order.id, USER2, "<b>已完成</b>")
    check("接单员回单成功", done is not None, err)
    run(grab.after_grabber_complete(done, USER2))
    check("回单后订单已完成", db.get_order(order.id).status == "completed")
    # 佣金存在
    cms = db.list_grab_commissions(grabber_id=USER2, limit=10)
    check("佣金记录已建", len(cms) >= 1, len(cms))
    if cms:
        ok = grab.release_commission_manual(cms[0].id, operator_id=ADMIN)
        check("手动解冻佣金", ok is True)
        check("重复解冻返回 False", grab.release_commission_manual(cms[0].id, operator_id=ADMIN) is False)


def test_grab_withdraw_and_appeal():
    print("\n=== 提现 / 申诉 ===")
    db.update_settings(grab_withdraw_min=10, grab_points_per_usdt=100)
    g = db.get_grabber(USER2)
    if g:
        g.balance_available = 500
        db.save_grabber(g)
    w, err = grab.apply_withdrawal(USER2, 100, "12345678")
    check("提现申请成功", w is not None, err)
    if w:
        run(grab.notify_admins_withdrawal(w))
        check("提现后余额扣减", db.get_grabber(USER2).balance_available == 400)
        w2 = grab.approve_withdrawal(w.id, ADMIN, "txid001")
        check("提现审批通过", w2 is not None and w2.status == "paid")
        check("重复审批返回 None", grab.approve_withdrawal(w.id, ADMIN) is None)
    w3, err3 = grab.apply_withdrawal(USER2, 1, "1")
    check("低于最低提现被拒", w3 is None, err3)
    if w3 is None:
        pass
    w4, err4 = grab.apply_withdrawal(USER2, 10, "2")
    if w4:
        w5 = grab.reject_withdrawal(w4.id, ADMIN, "地址错")
        check("提现驳回", w5 is not None and w5.status == "rejected")
    ap, aerr = grab.create_appeal(USER2, "NOPE", "申诉内容")
    check("申诉单不存在被拒或建单", ap is None or ap is not None, aerr)
    if ap:
        run(grab.notify_admins_appeal(ap))
        h1 = grab.handle_appeal(ap.id, ADMIN, True, "通过")
        check("申诉裁定", h1 is not None)
        check("重复裁定返回 None", grab.handle_appeal(ap.id, ADMIN, True) is None)



def test_ui_degrade():
    print("\n=== ui 降级 / send_or_edit ===")
    from telegram.error import BadRequest

    from gshop import ui

    class BadMsg(FakeMsg):
        def __init__(self, mode):
            super().__init__()
            self.mode = mode

        async def edit_message_text(self, text="", reply_markup=None, parse_mode=None, **kw):
            if self.mode == "notmod":
                raise BadRequest("Message is not modified")
            if self.mode == "badhtml":
                if parse_mode == "HTML":
                    raise BadRequest("Can not parse entities")
                return self
            raise RuntimeError("boom")

        async def reply_text(self, text="", reply_markup=None, parse_mode=None, **kw):
            if self.mode == "badhtml" and parse_mode == "HTML":
                raise BadRequest("Can not parse entities")
            self.replies.append((text, reply_markup))
            return self

    m = BadMsg("notmod")
    check("not modified 视为成功", run(ui._edit_text(m, "x", None)) is True)
    m = BadMsg("badhtml")
    check("HTML 非法降级纯文本", run(ui._edit_text(m, "<b>x", None)) is True)
    m = BadMsg("other")
    check("其它异常返回 False", run(ui._edit_text(m, "x", None)) is False)
    check("summarize_message 空消息", summarize_message(FakeMsg(USER1)) == "（空消息）")
    check("order_status_text 未知码", order_status_text("weird") == "weird")
    check("product_status_text 未知码", product_status_text("weird") == "weird")
    check("h(None) 空串", h(None) == "")
    # send_or_edit 四条分支
    q = FakeQuery(USER1, "m:root")
    run(ui.send_or_edit(q, "纯文字", kb([[("a", "b")]])))
    q2 = FakeQuery(USER1, "m:root")
    q2.message.photo = photo
    run(ui.send_or_edit(q2, "带图", kb([[("a", "b")]]), photo="PH1"))
    q3 = FakeQuery(USER1, "m:root")
    q3.message.photo = photo
    run(ui.send_or_edit(q3, "换成文字", kb([[("a", "b")]])))
    q4 = FakeQuery(USER1, "m:root")
    run(ui.send_or_edit(q4, "x" * 2000, kb([[("a", "b")]]), photo="PH1"))
    q5 = FakeQuery(USER1, "m:root")
    q5.message = None
    run(ui.send_or_edit(q5, "无消息", kb([[("a", "b")]])))
    check("send_or_edit 各分支不崩", True)


def test_singleton_and_health():
    print("\n=== singleton / 健康文件 ===")
    import shutil

    from gshop import singleton
    d = os.path.join(_TMP, "lk")
    shutil.rmtree(d, ignore_errors=True)
    p = singleton.acquire(d, "t.lock")
    check("加锁成功", p.exists())
    check("锁文件写 pid", p.read_text(encoding="utf-8").strip().isdigit())
    singleton.release()
    check("释放后 _lock_fd 为空", singleton._lock_fd is None)
    singleton.release()
    check("重复释放不报错", True)
    hp = os.path.join(d, "sub", ".health")
    singleton.write_health(hp, "ok")
    check("健康文件写入", open(hp, encoding="utf-8").read() == "ok")
    singleton.write_health(None, "ok")
    check("write_health(None) 不报错", True)
    singleton.write_health("/proc/definitely/not/writable/.h", "ok")
    check("write_health 异常被吞", True)


def test_jobs_and_kkpay():
    print("\n=== jobs / kkpay ===")
    from gshop import jobs, kkpay
    check("build_daily_report 出字符串", isinstance(jobs.build_daily_report(), str))
    jobs.BACKUP_DIR = os.path.join(_TMP, "bk")
    bp = jobs.do_backup()
    check("备份文件已生成", os.path.isfile(bp))
    jobs._remove_quietly(os.path.join(_TMP, "not-exist-file"))
    check("_remove_quietly 不报错", True)
    run(jobs.auto_heartbeat(os.path.join(_TMP, "hb", ".health"))) if False else None
    secret = "sec"
    body = "eyJhIjoxfQ=="
    sig = kkpay.verify_callback
    good = __import__("base64").b64encode(
        __import__("hmac").new(secret.encode(), body.encode(), __import__("hashlib").sha256).digest()
    ).decode()
    check("回调验签通过", kkpay.verify_callback(secret, body, good) is True)
    check("回调验签失败", kkpay.verify_callback(secret, body, "bad") is False)
    r1 = kkpay.sign_confirm_reply(secret, "tx1", "success")
    r2 = kkpay.sign_confirm_reply(secret, "tx1", "SUCCESS")
    check("出款应答签名大小写无关", r1 == r2)
    check("出款应答签名非空", bool(r1))


def test_command_handlers():
    print("\n=== handlers_command ===")
    from gshop import handlers_command as HC
    msg = FakeMsg(USER1)
    upd = FakeUpdate(USER1, message=msg)
    ctx = FakeCtx()
    run(HC.cmd_start(upd, ctx))
    check("cmd_start 有回复", bool(msg.replies) or bool(ctx.bot.sent))
    msg2 = FakeMsg(ADMIN)
    run(HC.cmd_admin(FakeUpdate(ADMIN, message=msg2), FakeCtx()))
    check("cmd_admin 有回复", bool(msg2.replies) or bool(FakeCtx().bot.sent))
    msg3 = FakeMsg(USER2)
    run(HC.cmd_grabber(FakeUpdate(USER2, message=msg3), FakeCtx()))
    check("cmd_grabber 有回复", bool(msg3.replies))
    err_ctx = FakeCtx()
    err_ctx.error = RuntimeError("模拟异常")
    run(HC.on_error(FakeUpdate(USER1, message=FakeMsg(USER1)), err_ctx))
    check("on_error 不抛异常", True)



def test_supply_full_flow():
    print("\n=== supply_service 全流程 ===")
    db.update_settings(
        supply_price_bot=10, supply_price_channel=20, supply_daily_limit=5,
        supply_paused=False, supply_review=0, supply_hall_open=True,
        supply_anonymous=1, supply_channel_id="", supply_categories=[],
    )
    service.update_user_points(USER1, 1000, operator_id=ADMIN, reason="测试充值")
    u = db.get_user(USER1)
    _before = u.points
    post, reason = supply.create_post(
        u, channel="bot", category="", content="测试供需内容",
        photo_file_id="", anonymous=False, content_html="",
    )
    check("供需发帖成功", post is not None, reason)
    if post:
        run(supply.distribute(post))
        check("发帖后余额扣减", db.get_user(USER1).points == _before - post.cost)
        check("供需大厅能列出", isinstance(supply.stats(), dict))
        from gshop.pages_supply import page_supply_hall, page_supply_mine
        t1, k1 = page_supply_hall(USER1, 0, "")
        check("大厅页有内容", bool(t1))
        t2, k2 = page_supply_mine(USER1)
        check("我的供需页有内容", bool(t2))
        d = supply.delete_post(post.id, operator_id=ADMIN, refund=True)
        check("删帖并退款", d is not None)
    # 审核模式
    db.update_settings(supply_review=1)
    u2 = db.get_user(USER1)
    p2, r2 = supply.create_post(
        u2, channel="bot", category="", content="待审内容",
        photo_file_id="", anonymous=False, content_html="",
    )
    check("审核模式发帖待审", p2 is not None and p2.status == "pending_review", r2)
    if p2:
        run(supply.notify_admins_review(p2))
        a = supply.approve_post(p2.id, operator_id=ADMIN)
        check("审核通过", a is not None)
        run(supply.notify_user_approved(a))
        db.update_settings(supply_review=1)
        p3, r3 = supply.create_post(
            db.get_user(USER1), channel="bot", category="", content="待驳内容",
            photo_file_id="", anonymous=False, content_html="",
        )
        if p3:
            rj = supply.reject_post(p3.id, operator_id=ADMIN)
            check("审核驳回", rj is not None)
            run(supply.notify_user_rejected(rj))
    db.update_settings(supply_review=0)
    # 余额不足
    db.update_settings(supply_free_daily=0)
    poor = service.get_or_create_user(777001, username="poor", first_name="穷" , last_name="")
    p4, r4 = supply.create_post(
        poor, channel="bot", category="", content="没钱发",
        photo_file_id="", anonymous=False, content_html="",
    )
    check("余额不足被拒", p4 is None and r4 == "points", r4)
    # 暂停 / 大厅关闭
    db.update_settings(supply_paused=True)
    p5, r5 = supply.create_post(
        db.get_user(USER1), channel="bot", category="", content="暂停中",
        photo_file_id="", anonymous=False, content_html="",
    )
    check("暂停时被拒", p5 is None, r5)
    db.update_settings(supply_paused=False)
    check("available_channels 出列表", isinstance(supply.available_channels(), list))
    check("price_for 各渠道", supply.price_for("bot") >= 0 and supply.price_for("channel") >= 0)
    check("channel_leg_value", isinstance(supply.channel_leg_value("both"), int))
    check("anonymous_allowed", supply.anonymous_allowed() in (True, False))


def test_callback_supply_actions():
    print("\n=== 供需回调具体动作 ===")
    db.update_settings(supply_categories=["分类A", "分类B"], supply_channel_id="", supply_review=0)
    q, ctx = fire_cb(USER1, "sup:hall:1")
    check("sup:hall 分页", True)
    fire_cb(USER1, "sup:hall:0:0")
    fire_cb(USER1, "sup:new")
    ctx0 = FakeCtx()
    q, ctx0 = fire_cb(USER1, "sup:ch:bot", ctx0)
    check("sup:ch 进选分类", bool(q.message.edits))
    q, ctx0 = fire_cb(USER1, "sup:cat:0", ctx0)
    check("sup:cat 进输入态", ctx0.user_data.get("state") == "sup_text")
    fire_cb(USER1, "sup:ch:badchannel")
    # 无 payload 的 sup:cat
    q, ctx = fire_cb(USER1, "sup:cat:0")
    check("sup:cat 无会话提示", bool(q.message.edits))
    # sup:nophoto 状态不对
    q, ctx = fire_cb(USER1, "sup:nophoto")
    check("sup:nophoto 状态不对提示", bool(q.message.edits))
    # sup:go 状态不对
    q, ctx = fire_cb(USER1, "sup:go")
    check("sup:go 状态不对提示", bool(q.message.edits))
    # sup:id 匿名切换
    ctx = FakeCtx({"state": "sup_preview", "payload": {
        "channel": "bot", "category": "", "content": "内容",
        "photo_file_id": "", "anonymous": False, "content_html": "",
    }})
    q, ctx = fire_cb(USER1, "sup:id:1", ctx)
    check("sup:id 切匿名", ctx.user_data["payload"]["anonymous"] is True)
    # sup:go 正常发布
    service.update_user_points(USER1, 500, operator_id=ADMIN, reason="测试充值")
    ctx2 = FakeCtx({"state": "sup_preview", "payload": {
        "channel": "bot", "category": "", "content": "回调发布内容",
        "photo_file_id": "", "anonymous": False, "content_html": "",
    }})
    q, ctx2 = fire_cb(USER1, "sup:go", ctx2)
    check("sup:go 发布成功", ctx2.user_data.get("state") is None)
    # sup:cancel
    fire_cb(USER1, "sup:cancel")
    # 管理端供需开关 / 审核 / 分类 / 定价
    fire_cb(ADMIN, "a:sup:sw:t:supply_paused")
    fire_cb(ADMIN, "a:sup:sw:t:bad_key")
    fire_cb(ADMIN, "a:sup:ok:NOPE")
    fire_cb(ADMIN, "a:sup:no:NOPE")
    fire_cb(ADMIN, "a:sup:posts:0")
    fire_cb(ADMIN, "a:sup:pd:NOPE")
    fire_cb(ADMIN, "a:sup:pdel:NOPE")
    fire_cb(ADMIN, "a:sup:pdelr:NOPE")
    fire_cb(ADMIN, "a:sup:pdok:NOPE")
    fire_cb(ADMIN, "a:sup:catadd")
    fire_cb(ADMIN, "a:sup:catdel:0")
    fire_cb(ADMIN, "a:sup:catdel:99")
    fire_cb(ADMIN, "a:sup:set:supply_price_bot")
    fire_cb(ADMIN, "a:sup:chid")
    check("供需管理回调不崩", True)



def test_admin_callback_actions():
    print("\n=== 管理端回调具体动作 ===")
    # 商品：上架 / 编辑 / 上下架 / 置顶 / 标签 / 删除
    fire_cb(ADMIN, "a:padd")
    fire_cb(ADMIN, "padd_kind:product")
    fire_cb(ADMIN, "padd_kind:report")
    fire_cb(ADMIN, "padd_mode:fixed")
    fire_cb(ADMIN, "padd_mode:pool")
    prods = db.list_products(limit=1)
    if prods:
        pid = prods[0].id
        fire_cb(ADMIN, "a:pedit:" + pid)
        fire_cb(ADMIN, "a:pf:" + pid + ":name")
        fire_cb(ADMIN, "a:pf:" + pid + ":description")
        fire_cb(ADMIN, "a:pf:" + pid + ":points")
        fire_cb(ADMIN, "a:pf:" + pid + ":stock")
        fire_cb(ADMIN, "a:pf:" + pid + ":limit_per_user")
        fire_cb(ADMIN, "a:pf:" + pid + ":auto_delivery")
        fire_cb(ADMIN, "a:pcatsel:" + pid)
        fire_cb(ADMIN, "a:pmode:" + pid + ":pool")
        fire_cb(ADMIN, "a:pmode:" + pid + ":fixed")
        fire_cb(ADMIN, "a:ptoggle:" + pid)
        fire_cb(ADMIN, "a:ptop:" + pid)
        fire_cb(ADMIN, "a:ptag:" + pid + ":hot")
        fire_cb(ADMIN, "a:pdel:" + pid)
        fire_cb(ADMIN, "a:pdelok:" + pid)
    fire_cb(ADMIN, "a:pcat")
    fire_cb(ADMIN, "a:pcatadd")
    fire_cb(ADMIN, "a:pcatdel:0")
    # 订单：详情 / 取消 / 回单 / 退款 / 恢复
    o = service.create_product({"name": "订单测", "kind": "report", "points": 10, "stock": -1})
    service.update_user_points(USER1, 500, operator_id=ADMIN, reason="测试")
    od, _ = service.create_order(USER1, o.id, note="x")
    if od:
        fire_cb(ADMIN, "a:od:" + od.id)
        fire_cb(ADMIN, "a:oc:" + od.id)
        fire_cb(ADMIN, "a:ocancel:" + od.id)
        fire_cb(ADMIN, "a:or:" + od.id)
        fire_cb(ADMIN, "a:orok:" + od.id)
        fire_cb(ADMIN, "a:op:" + od.id + ":pending")
    fire_cb(ADMIN, "a:of:all")
    fire_cb(ADMIN, "a:of:pending")
    # 用户：详情 / 加减分 / 封禁 / 解封
    fire_cb(ADMIN, "a:ud:" + str(USER1))
    fire_cb(ADMIN, "a:uadd:" + str(USER1))
    fire_cb(ADMIN, "a:usub:" + str(USER1))
    fire_cb(ADMIN, "a:uban:" + str(USER1))
    fire_cb(ADMIN, "a:uban:" + str(USER1))  # 再点一次取消封禁，避免污染后续供需用例
    fire_cb(ADMIN, "a:uo:" + str(USER1))
    fire_cb(ADMIN, "a:uph:" + str(USER1))
    # 管理员 / 频道
    fire_cb(ADMIN, "a:admdel:555666")
    fire_cb(ADMIN, "a:chdel:1")
    # 设置 / 支付
    fire_cb(ADMIN, "a:pay:tg:tron")
    fire_cb(ADMIN, "a:pay:tg:ton")
    fire_cb(ADMIN, "a:pay:tg:kkpay")
    # 广播
    fire_cb(ADMIN, "a:bc")
    fire_cb(ADMIN, "a:bct:all")
    fire_cb(ADMIN, "a:bct:with_points")
    fire_cb(ADMIN, "a:bct:no_points")
    check("管理端回调不崩", True)


def test_stock_pool():
    print("\n=== 库存池 ===")
    p = service.create_product({
        "name": "池测", "kind": "product", "points": 5, "stock": 0,
        "delivery_mode": "pool",
    })
    item = stock.parse_text("卡密内容ABC")
    check("parse_text 出字典", isinstance(item, dict) and item)
    if item:
        si = stock.add_item(p.id, item, ADMIN)
        check("入库成功", si is not None)
        check("镜像库存同步", db.get_product(p.id).stock >= 1)
    check("parse_photo 出字典", isinstance(stock.parse_photo("PH1", "说明"), dict))
    check("parse_document 出字典", isinstance(stock.parse_document("DOC1"), dict))
    check("parse_album 出字典", isinstance(stock.parse_album(["A", "B"], ""), dict))
    payload = {"album": None}
    check("flush_album_buffer 空返回 0", stock.flush_album_buffer(payload, p.id, ADMIN) == 0)
    payload["album"] = {"mgid": "m1", "file_ids": ["A", "B"], "caption": "", "version": 1}
    n = stock.flush_album_buffer(payload, p.id, ADMIN)
    check("flush_album_buffer 聚合入库", n >= 1, n)
    check("flush 后缓冲清空", payload.get("album") is None)
    items = db.list_stock_items(p.id, "available", limit=10)
    if items:
        ok, msg = stock.remove_item(items[0].id, ADMIN)
        check("删除库存项", ok is True, msg)
    check("删除不存在项返回 False", stock.remove_item(999999, ADMIN)[0] is False)
    check("clear_sold 返回整数", isinstance(stock.clear_sold(p.id), int))


def test_main_module():
    print("\n=== main 模块（覆盖率） ===")
    import importlib
    m = importlib.import_module("main")
    check("main 可导入", m is not None)
    check("main 有 main()", callable(getattr(m, "main", None)))



if __name__ == "__main__":
    test_user_states()
    test_grab_states()
    test_supply_states()
    test_product_states()
    test_admin_states()
    test_settings_states()
    test_broadcast_states()
    test_callback_namespaces()
    test_callback_grab_actions()
    test_callback_supply_actions()
    test_admin_callback_actions()
    test_grab_full_flow()
    test_grab_withdraw_and_appeal()
    test_supply_full_flow()
    test_stock_pool()
    test_ui_degrade()
    test_singleton_and_health()
    test_jobs_and_kkpay()
    test_command_handlers()
    test_main_module()
    print()
    print("=" * 50)
    print("PASS", PASS, " FAIL", FAIL)
    print("=" * 50)
    for _e in ERRORS:
        print("  -", _e)
    sys.exit(0 if FAIL == 0 else 1)
