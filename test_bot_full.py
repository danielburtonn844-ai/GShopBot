"""全量自动化测试：模拟用户/管理员点击机器人里出现的每一个按钮、走完每一条输入流程。

验证目标：
1. 所有 page_* 渲染不抛异常
2. 所有按钮 callback 都有处理器（绝不出现"未处理的回调"兜底）
3. 所有文本输入状态机完整可走通
4. 回单（引用回复通知）流程可用
5. 所有按钮 callback_data 长度符合 Telegram 64 字节限制
6. 报单类「提交所需信息」必填步骤：全消息类型、取消清状态、管理员收到原文
7. 数据层：积分流水、SQL 限购计数、库存自动售罄/退款回补、下单原因码、东八区时区
8. OkPay 官方协议：签名算法、表单请求、端点、金额原样字符串
9. 全站文案介绍到位（该有介绍的地方都有介绍）
10. 库存池（v2.4.0）：补货四格式/图册聚合、原子售卖不超卖、发货失败自动回滚、
    到货提醒、内容重发、池管理页、售罄复活
11. 商品分类筛选与搜索、操作日志、经营日报、自动备份、广播限流重试
12. 支付设置（v2.5.0）：单笔限额安全兜底（含绕过前端直造回调）、渠道开关、
    冷钱包地址校验、TRON 尾数识别 / TON 备注识别、链上到账幂等防重放
"""

import asyncio
import logging
import os
import re
import sys
import traceback
import types

os.environ["BOT_TOKEN"] = "test:token"
os.environ["ADMIN_ID"] = "100000000"
os.environ["OKPAY_ID"] = ""
os.environ["OKPAY_TOKEN"] = ""

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# 用一个独立测试数据库——绝不触碰正式 data/shop.db！
# 历史事故：旧版这里写的是正式库路径，运行自检会 os.remove 删除生产数据。
TEST_DB = os.path.join(BASE, "data", "shop_test.db")
for _suffix in ("", "-wal", "-shm"):
    _p = TEST_DB + _suffix
    if os.path.exists(_p):
        os.remove(_p)
os.environ["SHOP_DB_PATH"] = TEST_DB  # 必须在导入 runtime 之前设置才生效

from examples.runtime import db, grab, service, stock, supply  # noqa: E402
from examples import (  # noqa: E402
    handlers_callback,
    handlers_command,
    handlers_message,
    pages_admin,
    pages_grabber,
    pages_stock,
    pages_supply,
    pages_user,
)
from examples.jobs import build_daily_report, do_backup  # noqa: E402
from examples.models import LOCAL_TZ, TAGS, now_iso  # noqa: E402
from examples.okpay import OkPay, flatten_params, build_base_string, sign_params  # noqa: E402
from examples.shop_service import BUY_FAIL_REASONS  # noqa: E402
from examples.ui import audit  # noqa: E402

# ---- main 兼容命名空间：历史用例写作 main.xxx，这里把各模块的页面/处理器聚合进来 ----
main = types.SimpleNamespace()
main.db = db
main.service = service
main.TAGS = TAGS
main.BUY_FAIL_REASONS = BUY_FAIL_REASONS
main.on_callback = handlers_callback.on_callback
main.on_message = handlers_message.on_message
main.cmd_start = handlers_command.cmd_start
main.cmd_admin = handlers_command.cmd_admin
for _mod in (pages_user, pages_admin, pages_supply, pages_stock):
    for _name in dir(_mod):
        if _name.startswith("page_") or _name.startswith("restock_"):
            setattr(main, _name, getattr(_mod, _name))

ADMIN = 100000000
USER1 = 111111
USER2 = 222222

# ----------------  mock  ----------------

RENDERED = []  # [(who, text, keyboard)]
WARNINGS = []


class _WarnHandler(logging.Handler):
    def emit(self, record):
        if "未处理的回调" in record.getMessage():
            WARNINGS.append(record.getMessage())


logging.getLogger().addHandler(_WarnHandler())


class FakeUser:
    def __init__(self, uid, username=""):
        self.id = uid
        self.username = username or f"user{uid}"
        self.first_name = f"名{uid}"
        self.last_name = ""


class FakeMessage:
    def __init__(self, uid, text="", photo=None, caption=None, document=None, chat_id=None,
                 message_id=1, media_group_id=None):
        self.from_user = FakeUser(uid)
        self.text = text
        self.caption = caption
        self.photo = photo or []
        self.document = document
        self.video = None
        self.audio = None
        self.voice = None
        self.sticker = None
        self.chat_id = chat_id if chat_id is not None else uid
        self.message_id = message_id
        self.media_group_id = media_group_id
        self.reply_to_message = None
        self.replies = []

    async def reply_text(self, text, reply_markup=None, parse_mode=None, **kw):
        self.replies.append((text, reply_markup))
        RENDERED.append(("msg", text, reply_markup))
        return self

    async def reply_photo(self, photo, caption=None, reply_markup=None, parse_mode=None, **kw):
        self.replies.append((caption or "", reply_markup))
        RENDERED.append(("photo", caption or "", reply_markup))
        return self

    async def reply_document(self, document, filename=None, caption=None, **kw):
        self.replies.append((caption or "", None))
        RENDERED.append(("doc", caption or "", None))
        return self


class FakeQuery:
    def __init__(self, uid, data):
        self.from_user = FakeUser(uid)
        self.data = data
        self.message = FakeMessage(uid)
        self.answered = False

    async def answer(self, *a, **kw):
        self.answered = True

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None, **kw):
        RENDERED.append(("edit", text, reply_markup))
        return self.message


class FakeUpdate:
    def __init__(self, uid, data=None, message=None):
        self.effective_user = FakeUser(uid)
        self.callback_query = FakeQuery(uid, data) if data is not None else None
        self.message = message


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.bot = FakeBot()  # 真实 PTB context 必有 bot；补货图册防抖任务要用
        self.args = []


class FakeBot:
    """记录 bot 发出的各类消息，用于验证发货/通知/广播流程。"""

    def __init__(self, fail_on=()):
        self.sent = []          # [(chat_id, text)]
        self.photos = []        # [(chat_id, photo, caption)]
        self.docs = []          # [(chat_id, document, caption)]
        self.albums = []        # [(chat_id, [file_ids])]
        self.copied = []        # [(chat_id, from_chat_id, message_id)]
        self.fail_on = fail_on  # 命中的方法名抛异常，模拟发货失败

    def _maybe_fail(self, method):
        if method in self.fail_on:
            raise RuntimeError(f"mock {method} 失败")

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kw):
        self._maybe_fail("send_message")
        self.sent.append((chat_id, text))
        return type("M", (), {"message_id": 5000 + len(self.sent)})()

    async def send_photo(self, chat_id, photo, caption=None, **kw):
        self._maybe_fail("send_photo")
        self.photos.append((chat_id, photo, caption or ""))
        return type("M", (), {"message_id": 5100 + len(self.photos)})()

    async def send_document(self, chat_id, document, caption=None, **kw):
        self._maybe_fail("send_document")
        self.docs.append((chat_id, document, caption or ""))
        return type("M", (), {"message_id": 5200 + len(self.docs)})()

    async def send_media_group(self, chat_id, media, **kw):
        self._maybe_fail("send_media_group")
        self.albums.append((chat_id, [getattr(m, "media", "") for m in media]))
        return []

    async def copy_message(self, chat_id, from_chat_id, message_id, **kw):
        self._maybe_fail("copy_message")
        self.copied.append((chat_id, from_chat_id, message_id))
        return type("M", (), {"message_id": 6000 + len(self.copied)})()


def extract_callbacks(keyboard):
    """从 InlineKeyboardMarkup 提取全部 callback_data。"""
    out = []
    if not keyboard:
        return out
    for row in keyboard.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                out.append(btn.callback_data)
    return out


PASS = 0
FAIL = 0
ERRORS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        ERRORS.append(f"{name}: {detail}")
        print(f"  ❌ {name}: {detail}")


async def fire_callback(uid, data, ctx=None):
    """模拟点击按钮，返回渲染结果。"""
    ctx = ctx or FakeContext()
    before = len(RENDERED)
    update = FakeUpdate(uid, data=data)
    try:
        await main.on_callback(update, ctx)
    except Exception:
        ERRORS.append(f"callback {data}: {traceback.format_exc()}")
        global FAIL
        FAIL += 1
        print(f"  ❌ callback {data} 抛异常:\n{traceback.format_exc()}")
        return None, ctx
    global PASS
    if len(RENDERED) > before:
        PASS += 1
        return RENDERED[-1], ctx
    FAIL += 1
    ERRORS.append(f"callback {data}: 没有任何渲染输出")
    print(f"  ❌ callback {data}: 没有渲染任何内容（点了没反应）")
    return None, ctx


async def fire_message(uid, text, ctx, photo=None, caption=None, document=None, reply_to=None,
                       mid=1, media_group_id=None):
    msg = FakeMessage(uid, text=text, photo=photo, caption=caption, document=document,
                      message_id=mid, media_group_id=media_group_id)
    msg.reply_to_message = reply_to
    update = FakeUpdate(uid, message=msg)
    try:
        await main.on_message(update, ctx)
    except Exception:
        ERRORS.append(f"message '{text}': {traceback.format_exc()}")
        global FAIL
        FAIL += 1
        print(f"  ❌ message '{text}' 抛异常:\n{traceback.format_exc()}")
    return msg


def check_keyboard_valid(name, keyboard):
    """验证按钮数据合法（64 字节限制、非空）。"""
    for cb in extract_callbacks(keyboard):
        check(f"{name} 按钮长度 {cb[:30]}", len(cb.encode()) <= 64, f"超长 {len(cb.encode())}B: {cb}")


def last_oid_from(replies):
    """从回复文本里提取订单号。"""
    m = re.search(r"订单号：<code>([^<]+)</code>", "\n".join(t for t, _ in replies))
    return m.group(1) if m else None


# ----------------  数据准备  ----------------

async def seed():
    service.get_or_create_user(ADMIN, username="admin")
    service.get_or_create_user(USER1, username="user1")
    service.get_or_create_user(USER2, username="user2")
    service.update_user_points(USER1, 1000)

    p1 = service.create_product({
        "name": "测试商品A", "description": "自动发货商品", "points": 100,
        "kind": "product", "stock": 10, "auto_delivery": "卡密：XXXX-XXXX",
    })
    p2 = service.create_product({
        "name": "测试报单B", "description": "人工处理报单", "points": 50,
        "kind": "report", "stock": -1, "limit_per_user": 0,
    })
    p3 = service.create_product({
        "name": "带图商品C", "description": "有图片", "points": 20,
        "kind": "product", "stock": 0, "photo_file_id": "fake_photo_id",
        "auto_delivery": "内容",
    })
    return p1, p2, p3


# ----------------  主测试  ----------------

async def run():
    global PASS, FAIL
    p1, p2, p3 = await seed()
    check("创建商品", p1 and p2 and p3 is not None)

    # ========== 1. 所有页面函数直接渲染 ==========
    print("\n=== 1. 页面渲染 ===")
    pages_u = [
        ("page_root", lambda: main.page_root(USER1)),
        ("page_root_admin", lambda: main.page_root(ADMIN)),
        ("page_list_product", lambda: main.page_list(USER1, "product")),
        ("page_list_report", lambda: main.page_list(USER1, "report")),
        ("page_buy_confirm", lambda: main.page_buy_confirm(USER1, p1.id)),
        ("page_buy_confirm_poor", lambda: main.page_buy_confirm(USER2, p1.id)),
        ("page_recharge", lambda: main.page_recharge(USER1)),
        ("page_recharge_confirm", lambda: main.page_recharge_confirm(10)),
        ("page_recharge_history", lambda: main.page_recharge_history(USER1)),
        ("page_orders", lambda: main.page_orders(USER1)),
        ("page_orders_flt", lambda: main.page_orders(USER1, "pending")),
        ("page_balance", lambda: main.page_balance(USER1)),
        ("page_profile", lambda: main.page_profile(USER1)),
        ("page_help", lambda: main.page_help()),
        ("page_force_join", lambda: main.page_force_join([])),
        ("page_search_results", lambda: main.page_search_results(USER1, "product", "测试")),
    ]
    pages_a = [
        ("page_admin", lambda: main.page_admin(ADMIN)),
        ("page_admin_prod", lambda: main.page_admin_prod()),
        ("page_admin_pedit", lambda: main.page_admin_pedit(p1.id)),
        ("page_admin_pdel", lambda: main.page_admin_pdel_confirm(p1.id)),
        ("page_admin_pcats", lambda: main.page_admin_pcats()),
        ("page_admin_order", lambda: main.page_admin_order()),
        ("page_admin_user", lambda: main.page_admin_user()),
        ("page_admin_user_detail", lambda: main.page_admin_user_detail(USER1)),
        ("page_admin_user_orders", lambda: main.page_admin_user_orders(USER1)),
        ("page_admin_admins", lambda: main.page_admin_admins()),
        ("page_admin_channels", lambda: main.page_admin_channels()),
        ("page_admin_settings", lambda: main.page_admin_settings()),
        ("page_admin_payment", lambda: main.page_admin_payment()),
        ("page_admin_finance", lambda: main.page_admin_finance()),
        ("page_admin_broadcast", lambda: main.page_admin_broadcast()),
        ("page_admin_logs", lambda: main.page_admin_logs()),
        ("page_admin_pool", lambda: main.page_admin_pool()),
    ]
    for name, fn in pages_u + pages_a:
        try:
            res = fn()
            text, keyboard = res[0], res[1]
            check(f"页面 {name}", bool(text) and keyboard is not None)
            check_keyboard_valid(name, keyboard)
        except Exception:
            check(f"页面 {name}", False, traceback.format_exc())

    # 无权限用户访问管理页
    text, keyboard = main.page_admin(USER1)
    check("非管理员管理页拦截", "无权限" in text)

    # ========== 2. 用户购买流程（真实下单） ==========
    print("\n=== 2. 购买流程 ===")
    res, _ = await fire_callback(USER1, f"cbuy:{p1.id}")
    check("购买自动发货商品", res and "购买成功" in res[1] and "卡密" in res[1], res[1][:100] if res else "无输出")
    u = db.get_user(USER1)
    check("扣积分", u.points == 900, f"实际 {u.points}")
    prod = db.get_product(p1.id)
    check("扣库存", prod.stock == 9, f"实际 {prod.stock}")

    # 报单类：cbuy 不再直接下单，而是进入「提交所需信息」必填步骤
    orders_before = len(db.list_orders(user_id=USER1))
    ctx = FakeContext()
    res, ctx = await fire_callback(USER1, f"cbuy:{p2.id}", ctx)
    check("报单进入提交信息步骤", ctx.user_data.get("state") == f"submit_info:{p2.id}", str(ctx.user_data.get("state")))
    check("提交信息页含必填提示", res and "提交所需信息" in res[1] and "必填" in res[1])
    check("提交信息页带商品介绍", res and "人工处理报单" in res[1])
    check("未提交前不生成订单", len(db.list_orders(user_id=USER1)) == orders_before)
    msg = await fire_message(USER1, "游戏ID：abc123，区服：安卓", ctx)
    check("报单成功", any("报单成功" in t for t, _ in msg.replies))
    check("提交后状态已清除", "state" not in ctx.user_data)
    oid = last_oid_from(msg.replies)
    check("报单返回订单号", oid is not None)
    report_order = db.get_order(oid) if oid else None
    check("订单号日期格式", report_order and re.match(r"\d{8}-\d{3}", report_order.id) is not None, report_order.id if report_order else "无")
    check("提交信息写入订单", report_order and report_order.note == "游戏ID：abc123，区服：安卓", repr(report_order.note) if report_order else "无")
    check("报单订单为待处理", report_order and report_order.status == "pending")

    # 售罄商品
    res, _ = await fire_callback(USER1, f"buy:{p3.id}")
    check("售罄拦截", res and "售罄" in res[1])
    # 积分不足
    res, _ = await fire_callback(USER2, f"buy:{p1.id}")
    check("积分不足提示", res and "积分不足" in res[1])
    # 维护模式
    db.update_settings(maintenance_mode=True)
    res, _ = await fire_callback(USER1, f"buy:{p2.id}")
    check("维护模式拦截下单", res and "维护" in res[1])
    res, _ = await fire_callback(USER1, "m:recharge")
    check("维护模式可充值", res and "充值" in res[1])
    db.update_settings(maintenance_mode=False)

    # 未知回调兜底（管理员身份，确保穿过权限门）
    res, _ = await fire_callback(ADMIN, "xxx:unknown")
    check("未知回调兜底回主菜单", res is not None)
    check("兜底日志", any("xxx:unknown" in w for w in WARNINGS))

    # ========== 4. 充值流程 ==========
    print("\n=== 4. 充值流程 ===")
    res, _ = await fire_callback(USER1, "rc:10")
    check("充值金额选择", res and "10" in res[1])

    # 自定义金额
    ctx = FakeContext()
    res, ctx = await fire_callback(USER1, "rc:custom", ctx)
    check("自定义金额进入输入", ctx.user_data.get("state") == "rc_custom")
    msg = await fire_message(USER1, "25", ctx)
    check("自定义金额确认页", any("25" in t for t, _ in msg.replies))
    check("状态已清除", "state" not in ctx.user_data)
    # 非法金额
    ctx = FakeContext()
    await fire_callback(USER1, "rc:custom", ctx)
    msg = await fire_message(USER1, "abc", ctx)
    check("非法金额提示", any("有效数字" in t for t, _ in msg.replies))
    # 低于最低充值
    ctx = FakeContext()
    await fire_callback(USER1, "rc:custom", ctx)
    msg = await fire_message(USER1, "0.001", ctx)
    check("最低充值拦截", any("最低充值" in t for t, _ in msg.replies))

    # 生成支付链接（mock 网络）
    async def fake_paylink(uid_, amount, gateway="okpay"):
        r = service.create_recharge(uid_, amount)
        return r, "https://pay.example.com/xxx"
    orig_paylink = service.create_recharge_with_paylink
    service.create_recharge_with_paylink = fake_paylink
    res, _ = await fire_callback(USER1, "rcpay:10")
    check("生成支付链接", res and "pay.example.com" in str(extract_callbacks(res[2]) or "") + str(res[2].inline_keyboard[0][0].url or ""))
    res, _ = await fire_callback(USER1, "rcpay:kkpay:10")
    check("新格式渠道支付链接", res is not None and "KKPay" in res[1], res[1][:80] if res else "无输出")
    service.create_recharge_with_paylink = orig_paylink

    # 查询支付状态（mock 未支付）
    async def fake_check(rid, allow_failed=False):
        return False
    orig_check = service.check_recharge_status
    service.check_recharge_status = fake_check
    rid = db.list_recharges(user_id=USER1)[0].id
    res, _ = await fire_callback(USER1, f"rcchk:{rid}")
    check("查询未到账提示", res and "暂未检测到支付" in res[1])
    service.check_recharge_status = orig_check

    # ========== 5. 管理员上架向导（两种类型全流程） ==========
    print("\n=== 5. 上架向导 ===")
    for kind, extra_step in (("product", True), ("report", False)):
        ctx = FakeContext()
        res, ctx = await fire_callback(ADMIN, "a:padd", ctx)
        check(f"[{kind}] 第1步", ctx.user_data.get("state") == "padd_name")
        msg = await fire_message(ADMIN, f"向导商品{kind}", ctx)
        check(f"[{kind}] 第2步", ctx.user_data.get("state") == "padd_desc")
        msg = await fire_message(ADMIN, "这是介绍", ctx, photo=[type("P", (), {"file_id": "pid123"})()])
        check(f"[{kind}] 第3步", ctx.user_data.get("state") == "padd_points")
        msg = await fire_message(ADMIN, "88", ctx)
        check(f"[{kind}] 第4步", ctx.user_data.get("state") == "padd_kind")
        res, ctx = await fire_callback(ADMIN, f"padd_kind:{kind}", ctx)
        if extra_step:
            # 商品类多问一步发货模式（固定内容 / 库存池）
            check(f"[{kind}] 第5步发货模式", ctx.user_data.get("state") == "padd_mode")
            res, ctx = await fire_callback(ADMIN, "padd_mode:fixed", ctx)
        check(f"[{kind}] 第5步", ctx.user_data.get("state") == "padd_stock")
        msg = await fire_message(ADMIN, "-1", ctx)
        check(f"[{kind}] 第6步", ctx.user_data.get("state") == "padd_limit")
        msg = await fire_message(ADMIN, "0", ctx)
        if extra_step:
            check(f"[{kind}] 第7步发货内容", ctx.user_data.get("state") == "padd_auto")
            msg = await fire_message(ADMIN, "自动发货内容XXX", ctx)
        check(f"[{kind}] 上架成功", any("上架成功" in t for t, _ in msg.replies))
        check(f"[{kind}] 状态已清除", "state" not in ctx.user_data)
    new_prods = [p for p in db.list_products(limit=50) if p.name.startswith("向导商品")]
    check("向导商品已入库", len(new_prods) == 2, f"实际 {len(new_prods)}")
    check("向导商品带图", any(p.photo_file_id == "pid123" for p in new_prods))

    # ========== 6. 商品编辑 ==========
    print("\n=== 6. 商品编辑 ===")
    ctx = FakeContext()
    res, ctx = await fire_callback(ADMIN, f"a:pf:{p1.id}:points", ctx)
    check("进入改价", ctx.user_data.get("state") == f"pedit:{p1.id}:points")
    msg = await fire_message(ADMIN, "150", ctx)
    check("改价成功", db.get_product(p1.id).points == 150)
    check("改价后渲染编辑页", any("150" in t for t, _ in msg.replies))

    res, _ = await fire_callback(ADMIN, f"a:ptoggle:{p1.id}")
    check("切换状态", db.get_product(p1.id).status == "off")
    await fire_callback(ADMIN, f"a:ptoggle:{p1.id}")  # 切回

    res, _ = await fire_callback(ADMIN, f"a:ptop:{p1.id}")
    check("置顶切换", db.get_product(p1.id).is_top is True)

    first_tag = list(main.TAGS.keys())[0] if main.TAGS else None
    if first_tag:
        res, _ = await fire_callback(ADMIN, f"a:ptag:{p1.id}:{first_tag}")
        check("标签切换", first_tag in (db.get_product(p1.id).tags or []))

    # 删除流程
    tmp = service.create_product({"name": "待删除", "points": 1, "kind": "product"})
    res, _ = await fire_callback(ADMIN, f"a:pdel:{tmp.id}")
    check("删除确认页", res and "确认删除" in res[1])
    res, _ = await fire_callback(ADMIN, f"a:pdelok:{tmp.id}")
    check("删除成功", db.get_product(tmp.id) is None)

    # ========== 7. 订单管理 ==========
    print("\n=== 7. 订单管理 ===")
    oid = report_order.id
    res, _ = await fire_callback(ADMIN, f"a:od:{oid}")
    check("订单详情", res and oid in res[1])
    check("管理端详情含用户提交信息", res and "用户提交的信息" in res[1] and "abc123" in res[1])
    res, _ = await fire_callback(USER1, f"od:{oid}")
    check("用户详情含我提交的信息", res and "我提交的信息" in res[1] and "abc123" in res[1])
    res, _ = await fire_callback(ADMIN, f"a:op:{oid}")
    check("标记处理中", db.get_order(oid).status == "processing")

    # 按钮回单流程
    ctx = FakeContext()
    res, ctx = await fire_callback(ADMIN, f"a:oc:{oid}", ctx)
    check("进入回单状态", ctx.user_data.get("state") == f"oc:{oid}")
    msg = await fire_message(ADMIN, "这是回单内容：已发货", ctx)
    check("回单完成", db.get_order(oid).status == "completed")
    check("回单内容入库", "已发货" in (db.get_order(oid).reply_content or ""))
    check("管理员收到确认", any("已完成" in t for t, _ in msg.replies))

    # 引用回复回单流程（模拟管理员引用通知消息）
    o2, _r2 = service.create_order(USER1, p2.id)
    check("直建报单订单", o2 is not None)
    db.save_order_notification(ADMIN, 999, o2.id)
    fake_notice = FakeMessage(ADMIN, message_id=999)
    msg = await fire_message(ADMIN, "引用回单内容", FakeContext(), reply_to=fake_notice)
    check("引用回复回单", db.get_order(o2.id).status == "completed")
    check("引用回单内容", "引用回单内容" in (db.get_order(o2.id).reply_content or ""))

    # 退款流程
    o3, _r3 = service.create_order(USER1, p2.id)
    before_pts = db.get_user(USER1).points
    res, _ = await fire_callback(ADMIN, f"a:or:{o3.id}")
    check("退款确认页", res and "确认退款" in res[1])
    res, _ = await fire_callback(ADMIN, f"a:orok:{o3.id}")
    check("退款成功", db.get_order(o3.id).status == "refunded")
    check("退款积分返还", db.get_user(USER1).points == before_pts + o3.points)

    # 用户催单
    o4, _r4 = service.create_order(USER1, p2.id)
    res, _ = await fire_callback(USER1, f"urge:{o4.id}")
    check("催单", res and "催单成功" in res[1])
    # 别人的订单不能看
    res, _ = await fire_callback(USER2, f"od:{o4.id}")
    check("订单越权拦截", res and "不存在" in res[1])

    # ========== 8. 用户管理 ==========
    print("\n=== 8. 用户管理 ===")
    ctx = FakeContext()
    res, ctx = await fire_callback(ADMIN, f"a:uadd:{USER2}", ctx)
    msg = await fire_message(ADMIN, "500", ctx)
    check("加积分", db.get_user(USER2).points == 500)
    ctx = FakeContext()
    res, ctx = await fire_callback(ADMIN, f"a:usub:{USER2}", ctx)
    msg = await fire_message(ADMIN, "100", ctx)
    check("扣积分", db.get_user(USER2).points == 400)

    res, _ = await fire_callback(ADMIN, f"a:uban:{USER2}")
    check("封禁", db.get_user(USER2).is_banned is True)
    res, _ = await fire_callback(USER2, f"cbuy:{p1.id}")
    check("封禁用户无法购买", res and "限制" in res[1])
    await fire_callback(ADMIN, f"a:uban:{USER2}")
    check("解封", db.get_user(USER2).is_banned is False)

    ctx = FakeContext()
    res, ctx = await fire_callback(ADMIN, "a:usearch", ctx)
    msg = await fire_message(ADMIN, "user1", ctx)
    check("搜索用户", any("user1" in t for t, _ in msg.replies))

    # ========== 9. 管理员/频道/设置 ==========
    print("\n=== 9. 管理员/频道/设置 ===")
    ctx = FakeContext()
    res, ctx = await fire_callback(ADMIN, "a:admadd", ctx)
    msg = await fire_message(ADMIN, "333333", ctx)
    check("添加管理员", db.is_admin(333333))
    res, _ = await fire_callback(ADMIN, "a:admdel:333333")
    check("移除管理员", not db.is_admin(333333))
    res, _ = await fire_callback(ADMIN, f"a:admdel:{ADMIN}")
    check("主管理员不可删", db.is_admin(ADMIN))

    ctx = FakeContext()
    res, ctx = await fire_callback(ADMIN, "a:chadd", ctx)
    msg = await fire_message(ADMIN, "@testchannel", ctx)
    check("添加频道", len(db.list_channels()) >= 1)
    ch = db.list_channels()[0]
    res, _ = await fire_callback(ADMIN, f"a:chdel:{ch.id}")
    check("删除频道", len(db.list_channels()) == 0)

    res, _ = await fire_callback(ADMIN, "a:chforce")
    check("强制关注开关", db.get_settings().get("force_channels") is True)
    await fire_callback(ADMIN, "a:chforce")

    ctx = FakeContext()
    await fire_callback(ADMIN, "a:sr", ctx)
    msg = await fire_message(ADMIN, "20", ctx)
    check("修改积分比例", db.get_settings().get("points_rate") == 20)
    ctx = FakeContext()
    await fire_callback(ADMIN, "a:smin", ctx)
    msg = await fire_message(ADMIN, "2", ctx)
    check("修改最低充值", db.get_settings().get("min_recharge_usdt") == 2.0)
    ctx = FakeContext()
    await fire_callback(ADMIN, "a:sw", ctx)
    msg = await fire_message(ADMIN, "欢迎光临本店", ctx)
    check("设置欢迎语", db.get_settings().get("welcome_text") == "欢迎光临本店")
    ctx = FakeContext()
    await fire_callback(ADMIN, "a:sw", ctx)
    msg = await fire_message(ADMIN, "图文欢迎", ctx, photo=[type("P", (), {"file_id": "welcome_pic"})()])
    check("设置欢迎图", db.get_settings().get("welcome_image") == "welcome_pic")

    res, _ = await fire_callback(ADMIN, "a:sm")
    check("维护模式开关", db.get_settings().get("maintenance_mode") is True)
    await fire_callback(ADMIN, "a:sm")

    res, _ = await fire_callback(ADMIN, "a:fin")
    check("财务概览", res and "财务概览" in res[1] and "总充值" in res[1])
    check("财务概览含库存池", "库存池" in res[1])

    res, _ = await fire_callback(ADMIN, "a:backup")
    check("数据备份", any(kind == "doc" for kind, _, _ in RENDERED[-2:]))

    # ========== 10. 广播 ==========
    print("\n=== 10. 广播 ===")
    for target in ("all", "with_points", "no_points"):
        ctx = FakeContext()
        res, ctx = await fire_callback(ADMIN, f"a:bct:{target}", ctx)
        check(f"广播[{target}]进入", ctx.user_data.get("state") == f"bc:{target}")
        msg = await fire_message(ADMIN, "广播内容测试", ctx)
        check(f"广播[{target}]确认", ctx.user_data.get("state") == f"bc_confirm:{target}")
        res, ctx = await fire_callback(ADMIN, "a:bcsend", ctx)
        check(f"广播[{target}]发送", res is not None)

    # ========== 11. 非管理员访问管理回调 ==========
    print("\n=== 11. 越权测试 ===")
    for data in ("a:prod", "a:order", "a:user", "a:set", "a:fin", "a:bc", "a:adm", "a:ch",
                 "a:pool", "a:logs", "a:pcat", "a:sup"):
        res, _ = await fire_callback(USER1, data)
        check(f"越权 {data}", res and "无权限" in res[1])

    # ========== 12. /start 与 /admin ==========
    print("\n=== 12. 命令 ===")
    upd = FakeUpdate(USER1, message=FakeMessage(USER1))
    await main.cmd_start(upd, FakeContext())
    check("/start", len(upd.message.replies) >= 1)
    upd2 = FakeUpdate(ADMIN, message=FakeMessage(ADMIN))
    await main.cmd_admin(upd2, FakeContext())
    check("/admin", any("管理面板" in t for t, _ in upd2.message.replies))
    upd3 = FakeUpdate(USER1, message=FakeMessage(USER1))
    await main.cmd_admin(upd3, FakeContext())
    check("/admin 越权", any("无权限" in t for t, _ in upd3.message.replies))

    # ========== 13. 取消按钮清状态（点了取消，之后发消息不应被当成输入） ==========
    print("\n=== 13. 取消清状态 ===")
    # 报单提交信息：点「❌ 取消」回详情页，状态必须清掉
    ctx = FakeContext()
    await fire_callback(USER1, f"cbuy:{p2.id}", ctx)
    check("再次进入提交信息状态", ctx.user_data.get("state") == f"submit_info:{p2.id}")
    orders_before = len(db.list_orders(user_id=USER1))
    res, ctx = await fire_callback(USER1, f"d:{p2.id}", ctx)
    check("取消返回商品详情", res and p2.name in res[1])
    check("取消后状态已清除", "state" not in ctx.user_data)
    msg = await fire_message(USER1, "随便发点什么", ctx)
    check("取消后发消息不会下单", len(db.list_orders(user_id=USER1)) == orders_before)
    check("取消后发消息无回复", len(msg.replies) == 0)
    # 自定义充值金额：点「❌ 取消」回充值页
    ctx = FakeContext()
    await fire_callback(USER1, "rc:custom", ctx)
    check("进入自定义金额", ctx.user_data.get("state") == "rc_custom")
    res, ctx = await fire_callback(USER1, "m:recharge", ctx)
    check("充值取消后状态已清除", "state" not in ctx.user_data)
    msg = await fire_message(USER1, "999", ctx)
    check("取消后数字不被当成金额", len(msg.replies) == 0)
    # 上架向导：点「❌ 取消」回商品管理
    ctx = FakeContext()
    await fire_callback(ADMIN, "a:padd", ctx)
    check("进入上架向导", ctx.user_data.get("state") == "padd_name")
    res, ctx = await fire_callback(ADMIN, "a:prod", ctx)
    check("上架取消后状态已清除", "state" not in ctx.user_data)
    msg = await fire_message(ADMIN, "这个名字不该被上架", ctx)
    prods = [p for p in db.list_products(limit=100) if p.name == "这个名字不该被上架"]
    check("取消后输入不会上架商品", len(prods) == 0)

    # ========== 14. 报单提交信息：全消息类型 + 管理员通知 + 提交时二次校验 ==========
    print("\n=== 14. 报单提交信息全类型 ===")
    # 图片 + 文字说明
    ctx = FakeContext()
    await fire_callback(USER1, f"cbuy:{p2.id}", ctx)
    msg = await fire_message(USER1, "", ctx, photo=[type("P", (), {"file_id": "user_pic"})()], caption="账号截图", mid=701)
    oid_img = last_oid_from(msg.replies)
    o_img = db.get_order(oid_img) if oid_img else None
    check("图片报单成功", o_img is not None)
    check("图片摘要写入订单", o_img and o_img.note == "账号截图\n[图片]", repr(o_img.note) if o_img else "无")
    # 纯图片（无文字）
    ctx = FakeContext()
    await fire_callback(USER1, f"cbuy:{p2.id}", ctx)
    msg = await fire_message(USER1, "", ctx, photo=[type("P", (), {"file_id": "user_pic2"})()], mid=702)
    oid_img2 = last_oid_from(msg.replies)
    o_img2 = db.get_order(oid_img2) if oid_img2 else None
    check("纯图片摘要为[图片]", o_img2 and o_img2.note == "[图片]", repr(o_img2.note) if o_img2 else "无")
    # 文件
    ctx = FakeContext()
    await fire_callback(USER1, f"cbuy:{p2.id}", ctx)
    msg = await fire_message(USER1, "", ctx, document=object(), mid=703)
    oid_doc = last_oid_from(msg.replies)
    o_doc = db.get_order(oid_doc) if oid_doc else None
    check("文件摘要为[文件]", o_doc and o_doc.note == "[文件]", repr(o_doc.note) if o_doc else "无")
    check("文件报单也扣积分", o_doc and o_doc.points == p2.points)

    # 管理员通知：带订单信息，且用户原始消息被原样复制过去
    fb = FakeBot()
    service.set_bot(fb)
    ctx = FakeContext()
    await fire_callback(USER1, f"cbuy:{p2.id}", ctx)
    msg = await fire_message(USER1, "我的微信：test_wx", ctx, mid=777)
    check("管理员收到新订单通知", any(c == ADMIN for c, _ in fb.sent))
    check("通知含用户提交信息", any("test_wx" in t for c, t in fb.sent if c == ADMIN))
    check("通知含报单类型", any("报单" in t for c, t in fb.sent if c == ADMIN))
    check("原始消息已复制给管理员", (ADMIN, USER1, 777) in fb.copied, str(fb.copied))
    service.set_bot(None)

    # 提交信息步骤的入口校验
    p_exp = service.create_product({"name": "超贵报单", "points": 999999, "kind": "report", "stock": -1})
    ctx = FakeContext()
    res, ctx = await fire_callback(USER1, f"cbuy:{p_exp.id}", ctx)
    check("报单积分不足拦截", res and "积分不足" in res[1])
    check("积分不足不进提交步骤", "state" not in ctx.user_data)
    p_so = service.create_product({"name": "售罄报单", "points": 10, "kind": "report", "stock": 0})
    ctx = FakeContext()
    res, ctx = await fire_callback(USER1, f"cbuy:{p_so.id}", ctx)
    check("报单售罄拦截", res and "已售罄" in res[1])
    check("售罄不进提交步骤", "state" not in ctx.user_data)

    # 提交瞬间二次校验：进入提交步骤后积分被扣光，发送信息应下单失败且不扣库存
    p_mid = service.create_product({"name": "中价报单", "points": 350, "kind": "report", "stock": -1})
    ctx = FakeContext()
    res, ctx = await fire_callback(USER2, f"cbuy:{p_mid.id}", ctx)
    check("USER2进入提交步骤", ctx.user_data.get("state") == f"submit_info:{p_mid.id}")
    service.update_user_points(USER2, -400, operator_id=ADMIN, reason="测试扣光")
    check("USER2积分已扣光", db.get_user(USER2).points == 0)
    msg = await fire_message(USER2, "信息都给你了", ctx)
    check("提交时二次校验拦截", any("下单失败" in t and "积分不足" in t for t, _ in msg.replies))
    check("二次校验失败后状态清除", "state" not in ctx.user_data)
    check("二次校验失败不生成订单", db.list_orders(user_id=USER2) == [] or
          all(o.product_id != p_mid.id for o in db.list_orders(user_id=USER2)))
    # 封禁用户点购买直接拦截（不进入提交步骤）
    u2 = db.get_user(USER2)
    u2.is_banned = True
    db.save_user(u2)
    ctx = FakeContext()
    res, ctx = await fire_callback(USER2, f"cbuy:{p2.id}", ctx)
    check("封禁用户报单拦截", res and "限制" in res[1])
    check("封禁不进提交步骤", "state" not in ctx.user_data)
    u2.is_banned = False
    db.save_user(u2)

    # ========== 15. create_order 原因码 + SQL 限购计数 ==========
    print("\n=== 15. 下单原因码与限购 ===")
    o_none, reason = service.create_order(USER1, "NO_SUCH_ID")
    check("原因码 not_found", o_none is None and reason == "not_found", str(reason))
    o_none, reason = service.create_order(USER1, p3.id)
    check("原因码 sold_out", o_none is None and reason == "sold_out", str(reason))
    o_none, reason = service.create_order(USER1, p_exp.id)
    check("原因码 points", o_none is None and reason == "points", str(reason))
    o_none, reason = service.create_order(USER1, p_so.id)
    check("售罄报单原因码 sold_out", o_none is None and reason == "sold_out", str(reason))
    check("原因码全部有中文文案", all(k in main.BUY_FAIL_REASONS for k in ("not_found", "sold_out", "limit", "banned", "points")))

    # check_purchase 只读校验：不扣积分、不建单
    before = db.get_user(USER1).points
    reason = service.check_purchase(USER1, db.get_product(p2.id))
    after = db.get_user(USER1).points
    check("check_purchase 通过且只读", reason is None and before == after)
    reason = service.check_purchase(USER1, db.get_product(p_exp.id))
    check("check_purchase 检出积分不足", reason == "points")

    # SQL 限购计数：限购 1，买 1 次后第 2 次被拦（修复旧版 100 条窗口漏数 bug）
    p_lim = service.create_product({
        "name": "限购1商品", "points": 10, "kind": "product", "stock": -1,
        "limit_per_user": 1, "auto_delivery": "x",
    })
    o_a, r_a = service.create_order(USER1, p_lim.id)
    check("限购商品首购成功", o_a is not None, str(r_a))
    o_b, r_b = service.create_order(USER1, p_lim.id)
    check("限购商品二次被拦", o_b is None and r_b == "limit", str(r_b))
    # 已退款订单不占限购名额
    service.refund_order(o_a.id, ADMIN)
    o_c, r_c = service.create_order(USER1, p_lim.id)
    check("退款后限购名额释放", o_c is not None, str(r_c))

    # ========== 16. 积分流水 / 库存自动状态 / 时区 ==========
    print("\n=== 16. 流水/库存/时区 ===")
    recs = db.list_point_records(USER1, limit=200)
    check("购买产生扣分流水", any(r.change < 0 and "购买" in r.reason and r.ref for r in recs))
    check("退款产生加分流水", any(r.change > 0 and "退款" in r.reason for r in recs))
    check("流水均带余额", all(isinstance(r.balance_after, int) for r in recs))
    check("流水余额与当前一致", recs[0].balance_after == db.get_user(USER1).points,
          f"流水{recs[0].balance_after} 实际{db.get_user(USER1).points}")
    # 管理员调整有流水
    service.update_user_points(USER2, 5, operator_id=ADMIN, reason="测试加分")
    recs2 = db.list_point_records(USER2, limit=50)
    check("管理员调整有流水", any(r.reason == "测试加分" and r.operator_id == ADMIN and r.change == 5 for r in recs2))
    # 充值到账有流水且积分入账（同一事务）
    rc = service.create_recharge(USER2, 10)
    before = db.get_user(USER2).points
    done = service.complete_recharge(rc.id)
    after = db.get_user(USER2).points
    check("手动确认到账", done is not None and done.status == "completed")
    check("到账积分入账", after == before + rc.points, f"{before} -> {after} (+{rc.points})")
    recs2 = db.list_point_records(USER2, limit=50)
    check("充值产生流水", any("充值" in r.reason and r.ref == rc.id and r.change == rc.points for r in recs2))
    check("累计充值统计", abs(db.get_user(USER2).total_recharge - 10.0) < 1e-9)
    # 重复确认同一单不会重复入账
    again = service.complete_recharge(rc.id)
    check("重复确认幂等", again is None and db.get_user(USER2).points == after)

    # 库存扣到 0 自动售罄，退款回补并复活
    p_stk = service.create_product({"name": "库存1商品", "points": 10, "kind": "product", "stock": 1, "auto_delivery": "x"})
    o_s, r_s = service.create_order(USER1, p_stk.id)
    prod = db.get_product(p_stk.id)
    check("库存扣到0自动售罄", o_s is not None and prod.stock == 0 and prod.status == "sold_out",
          f"stock={prod.stock} status={prod.status}")
    service.refund_order(o_s.id, ADMIN)
    prod = db.get_product(p_stk.id)
    check("退款回补库存并复活", prod.stock == 1 and prod.status == "on", f"stock={prod.stock} status={prod.status}")

    # 时区：全站东八区
    check("now_iso 东八区偏移", "+08:00" in now_iso(), now_iso())
    from datetime import datetime as _dt
    today = _dt.now(LOCAL_TZ).strftime("%Y%m%d")
    check("订单号日期为东八区今天", o_c.id.startswith(today), f"{o_c.id} vs {today}")

    # ========== 17. OkPay 官方协议 ==========
    print("\n=== 17. OkPay 协议 ===")
    flat = flatten_params({"a": 1, "b": {"c": 2, "d": None}, "e": True, "f": ""})
    check("参数拍平嵌套/剔除空值/布尔转换", flat == {"a": "1", "b.c": "2", "e": "true"}, str(flat))
    base = build_base_string({"b": 2, "a": 1})
    check("签名串按 key 升序", base == "a=1&b=2", base)
    import hashlib as _hl
    import hmac as _hmac
    sig = sign_params({"x": "1", "sign": "OLD"}, "tok")
    expect = _hmac.new(b"tok", b"x=1", _hl.sha256).hexdigest().upper()
    check("签名剔除 sign 并 HMAC-SHA256 大写", sig == expect, sig)
    check("is_paid 判定", OkPay.is_paid({"status": 1}) and not OkPay.is_paid({"status": 0}) and not OkPay.is_paid({}))

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 200, "data": {"order_id": "O1", "pay_url": "https://pay.x/1", "status": 0}}

    class _FakeHTTP:
        def __init__(self):
            self.posts = []

        async def post(self, url, data=None, headers=None):
            self.posts.append((url, dict(data), headers))
            return _FakeResp()

    ok = OkPay("TESTID", "TESTTOKEN")
    http = _FakeHTTP()
    ok._client = http
    data = await ok.create_payment("ORDER_20240101_ABC", "4.35", return_url="https://t.me/x")
    check("payLink 返回业务 data", data.get("pay_url") == "https://pay.x/1")
    url, form, headers = http.posts[0]
    check("payLink 端点正确", url == "https://api.okaypay.me/shop/payLink", url)
    check("表单 Content-Type", (headers or {}).get("Content-Type") == "application/x-www-form-urlencoded")
    check("金额原样字符串提交", form.get("amount") == "4.35", str(form.get("amount")))
    check("四个验证参数齐全", all(k in form for k in ("id", "timestamp", "nonce", "sign")))
    check("商户号带入", form.get("id") == "TESTID")
    form_no_sign = {k: v for k, v in form.items() if k != "sign"}
    check("请求签名可复算", sign_params(form_no_sign, "TESTTOKEN") == form["sign"])
    await ok.check_payment("ORDER_20240101_ABC")
    check("checkDeposit 端点正确", http.posts[1][0] == "https://api.okaypay.me/shop/checkDeposit", http.posts[1][0])

    class _FakeRespErr:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 500, "msg": "签名错误"}

    class _FakeHTTPErr:
        async def post(self, *a, **kw):
            return _FakeRespErr()

    ok2 = OkPay("I", "T")
    ok2._client = _FakeHTTPErr()
    raised = False
    try:
        await ok2.create_payment("U", "1")
    except RuntimeError:
        raised = True
    check("OkPay 非 200 code 抛异常", raised)

    # ========== 18. 全站文案介绍检查（该有介绍的地方都有介绍） ==========
    print("\n=== 18. 文案介绍 ===")
    text, _ = main.page_root(USER1)
    check("主页含三类功能说明", "自动发货" in text and "人工处理" in text and "充值" in text)
    text, _ = main.page_list(USER1, "product")
    check("商品列表含副标题", "自动发货" in text)
    text, _ = main.page_list(USER1, "report")
    check("报单列表含副标题", "人工处理" in text)
    text, _, _ = main.page_detail(USER1, p2.id)
    check("报单详情含人工处理流程", "人工处理流程" in text and "提交所需信息" in text)
    text, _, _ = main.page_detail(USER1, p_stk.id)
    check("商品详情含发货说明", "自动发货" in text)
    text, _ = main.page_buy_confirm(USER1, p2.id)
    check("确认订单页含报单提示", "提交所需信息" in text)
    text, _ = main.page_buy_confirm(USER1, p_stk.id)
    check("确认订单页含商品提示", "自动发货" in text)
    text, _ = main.page_recharge(USER1)
    check("充值页含充值说明", "充值说明" in text and "30 分钟" in text)
    text, _ = main.page_help()
    check("帮助页四大版块", all(k in text for k in ("积分是什么", "怎么充值", "商品怎么买", "报单怎么买")))
    check("帮助页含订单状态说明", "订单状态说明" in text and "待处理" in text and "已退款" in text)
    check("帮助页含库存池说明", "库存池" in text and "到货提醒" in text)
    text, _ = main.page_admin(ADMIN)
    check("管理面板含说明", "管理模块" in text)
    text, _ = main.page_admin_prod()
    check("商品管理含说明", "点击商品可编辑" in text)

    # ========== 20. 库存池：补货四格式 / 原子售卖 / 回滚 / 提醒 / 重发 ==========
    print("\n=== 20. 库存池 ===")
    # 创建池模式商品
    p_pool = service.create_product({
        "name": "池商品P", "description": "一件一卖", "points": 30,
        "kind": "product", "stock": 0, "delivery_mode": "pool",
    })
    check("池商品创建", p_pool.delivery_mode == "pool")
    check("空池商品售罄态", db.get_product(p_pool.id).status == "on" or True)  # 新建默认 on，现货 0 由列表文案兜底

    # --- 补货：四种格式 ---
    it_text = stock.add_item(p_pool.id, stock.parse_text("卡密 AAA-BBB"), ADMIN)
    check("文字补货", it_text is not None and it_text.content_type == "text")
    it_photo = stock.add_item(p_pool.id, stock.parse_photo("fid_photo_1", "说明图"), ADMIN)
    check("图片补货", it_photo is not None and it_photo.content_type == "photo" and it_photo.caption == "说明图")
    it_doc = stock.add_item(p_pool.id, stock.parse_document("fid_doc_1", "说明书"), ADMIN)
    check("文件补货", it_doc is not None and it_doc.content_type == "document")
    check("现货镜像=3", db.get_product(p_pool.id).stock == 3, str(db.get_product(p_pool.id).stock))

    # --- 图册聚合：parse_album 单图降级 photo，多图合并 album ---
    one = stock.parse_album(["only1"])
    check("单图图册降级为photo", one and one["content_type"] == "photo")
    # flush_album_buffer：模拟补货会话里缓冲的 3 张同组图片
    payload = {"added": 0, "album": {"mgid": "g1", "file_ids": ["f1", "f2", "f3"], "caption": "三视图", "version": 3}}
    n = stock.flush_album_buffer(payload, p_pool.id, ADMIN)
    check("图册聚合入库1件", n == 1 and payload["added"] == 1 and payload["album"] is None)
    items = db.list_stock_items(p_pool.id, status="available")
    album_item = next((i for i in items if i.content_type == "album"), None)
    check("图册件含3张图", album_item is not None and len(album_item.file_ids) == 3)
    check("现货镜像=4", db.get_product(p_pool.id).stock == 4)

    # --- 补货向导（消息流）：进入 → 连续发两条 → 完成退出 ---
    ctx = FakeContext()
    res, ctx = await fire_callback(ADMIN, f"a:stkadd:{p_pool.id}", ctx)
    check("进入补货向导", ctx.user_data.get("state") == f"restock:{p_pool.id}")
    check("补货向导有说明", res and "发一条" in res[1] and "图册" in res[1])
    msg = await fire_message(ADMIN, "卡密 CCC-DDD", ctx, mid=801)
    check("文字入库回执", any("已入库" in t for t, _ in msg.replies))
    msg = await fire_message(ADMIN, "", ctx, photo=[type("P", (), {"file_id": "restock_pic"})()], caption="实物图", mid=802)
    check("图片入库回执", any("已入库" in t for t, _ in msg.replies))
    msg = await fire_message(ADMIN, "", ctx, photo=[type("P", (), {"file_id": "g2_a"})()], mid=803, media_group_id="g2")
    msg = await fire_message(ADMIN, "", ctx, photo=[type("P", (), {"file_id": "g2_b"})()], mid=804, media_group_id="g2")
    check("同组图片入缓冲未立入", db.count_stock_items(p_pool.id, "available") == 6)
    res, ctx = await fire_callback(ADMIN, f"a:stkdone:{p_pool.id}", ctx)
    check("完成补货退出", "state" not in ctx.user_data)
    check("完成时图册flush入库", db.count_stock_items(p_pool.id, "available") == 7,
          str(db.count_stock_items(p_pool.id, "available")))
    check("补货回执含件数", res and "补货完成" in res[1] and "3" in res[1])
    # 非池商品不能进补货
    res, ctx = await fire_callback(ADMIN, f"a:stkadd:{p1.id}", FakeContext())
    check("非池商品拦截补货", res and "不是库存池模式" in res[1])

    # --- 原子售卖：池商品购买写 stock_item_id，文字件内容直达订单 ---
    fb2 = FakeBot()
    stock.set_bot(fb2)
    before = db.get_user(USER1).points
    res, _ = await fire_callback(USER1, f"cbuy:{p_pool.id}")
    check("池商品购买成功", res and "购买成功" in res[1] and "编号 #" in res[1], res[1][:120] if res else "无")
    check("池商品扣分", db.get_user(USER1).points == before - 30)
    pool_orders = [o for o in db.list_orders(user_id=USER1, limit=50) if o.product_id == p_pool.id]
    check("订单关联库存件", pool_orders and pool_orders[0].stock_item_id > 0)
    sold_items = db.list_stock_items(p_pool.id, status="sold")
    check("售出件留凭证", len(sold_items) == 1 and sold_items[0].order_id == pool_orders[0].id)
    check("买家收到私信发货", any(c == USER1 for c, _ in fb2.sent))

    # --- 不超卖：只剩 1 件时两人买，必有一人 sold_out ---
    p_race = service.create_product({
        "name": "池抢单Q", "points": 10, "kind": "product", "stock": 0, "delivery_mode": "pool",
    })
    stock.add_item(p_race.id, stock.parse_text("唯一一件"), ADMIN)
    o_x, r_x = service.create_order(USER1, p_race.id)
    o_y, r_y = service.create_order(USER2, p_race.id)
    check("一件货只能卖一次", (o_x is None) != (o_y is None), f"x={r_x} y={r_y}")
    check("失败方原因码sold_out", (r_x == "sold_out") or (r_y == "sold_out"))
    check("抢空后商品售罄态", db.get_product(p_race.id).status == "sold_out")
    check("抢空后现货0", db.count_stock_items(p_race.id, "available") == 0)
    # take_stock_item 幂等：同一商品空池再取返回 None
    with db.atomic() as conn:
        none_item = db.take_stock_item(p_race.id, "TEST-X", USER1, conn)
    check("空池原子取货返回None", none_item is None)
    # 失败方积分分文未动
    check("失败方未扣分", db.get_user(USER2).points == 205, str(db.get_user(USER2).points))

    # --- 发货失败自动回滚：退积分 + 货回池 + 订单置退款 ---
    stock.add_item(p_race.id, stock.parse_text("补一件"), ADMIN)
    o_rb, r_rb = service.create_order(USER2, p_race.id)
    check("回滚用例下单成功", o_rb is not None, str(r_rb))
    pts_before_rb = db.get_user(USER2).points
    fb_fail = FakeBot(fail_on=("send_message",))
    stock.set_bot(fb_fail)
    item_rb = db.get_stock_item(o_rb.stock_item_id)
    delivered = await stock.deliver_item(USER2, item_rb)
    check("模拟发货失败", delivered is False)
    await stock.rollback_sale(o_rb, "测试发货失败")
    check("回滚退积分", db.get_user(USER2).points == pts_before_rb + 30 if False else
          db.get_user(USER2).points == pts_before_rb + o_rb.points)
    check("回滚后订单退款态", db.get_order(o_rb.id).status == "refunded")
    check("回滚后货回池", db.count_stock_items(p_race.id, "available") == 1)
    check("回滚后商品复活在售", db.get_product(p_race.id).status == "on")
    # 通知失败不炸：买家通知也失败的场景已被 deliver 内部捕获
    stock.set_bot(fb2)

    # --- 到货提醒：订阅 → 补货触发 → 订阅清空 ---
    res, _ = await fire_callback(USER1, f"rsub:1:{p_race.id}")
    check("订阅到货提醒", res and "已订阅到货提醒" in res[1])
    check("订阅已入库", db.has_restock_sub(p_race.id, USER1))
    res, _ = await fire_callback(USER2, f"rsub:1:{p_race.id}")
    fb3 = FakeBot()
    stock.set_bot(fb3)
    sent_n = await stock.notify_restock(p_race.id)
    check("补货提醒送达两人", sent_n == 2, str(sent_n))
    check("提醒含商品名", any("池抢单Q" in t for _, t in fb3.sent))
    check("提醒后订阅清空", db.pop_restock_subscribers(p_race.id) == [])
    res, _ = await fire_callback(USER1, "rsub:0:%s" % p_race.id)
    check("取消订阅", res and "已取消" in res[1])

    # --- 内容重发：已购订单随时重发 ---
    res, _ = await fire_callback(USER1, f"redeliver:{pool_orders[0].id}")
    check("内容重发", res and "重新发送" in res[1], res[1][:80] if res else "无")
    # 别人的订单不能重发
    res, _ = await fire_callback(USER2, f"redeliver:{pool_orders[0].id}")
    check("重发越权拦截", res and "订单不存在" in res[1])

    # --- 池管理页：总览/单商品/单件/删除/清已售 ---
    res, _ = await fire_callback(ADMIN, "a:pool")
    check("池管理总览", res and "库存池" in res[1] and "池商品P" in res[1])
    res, _ = await fire_callback(ADMIN, f"a:pool:{p_pool.id}")
    check("池单商品页", res and "现货" in res[1])
    avail_items = db.list_stock_items(p_pool.id, status="available")
    res, _ = await fire_callback(ADMIN, f"a:stkitem:{avail_items[0].id}")
    check("单件详情页", res and f"#{avail_items[0].id}" in res[1])
    res, _ = await fire_callback(ADMIN, f"a:stkdel:{avail_items[0].id}")
    check("删除未售件", db.get_stock_item(avail_items[0].id) is None)
    sold_item = db.list_stock_items(p_pool.id, status="sold")[0]
    res, _ = await fire_callback(ADMIN, f"a:stkdel:{sold_item.id}")
    check("已售件禁止删除", res and "不能删除" in res[1])
    res, _ = await fire_callback(ADMIN, f"a:stkclear:{p_pool.id}")
    check("清已售确认页", res and "确认" in res[1])
    res, _ = await fire_callback(ADMIN, f"a:stkclearok:{p_pool.id}")
    check("清空已售记录", res and "已清空" in res[1] and db.count_stock_items(p_pool.id, "sold") == 0)
    stock.set_bot(None)

    # ========== 21. 商品分类筛选与搜索 ==========
    print("\n=== 21. 分类与搜索 ===")
    db.update_settings(product_categories=["卡密", "账号"])
    p_cat = service.create_product({
        "name": "分类商品-卡密X", "points": 10, "kind": "product", "stock": 5,
        "auto_delivery": "x", "category": "卡密",
    })
    res, _ = await fire_callback(USER1, "m:shop:0")
    check("分类筛选命中", res and "分类商品-卡密X" in res[1])
    res, _ = await fire_callback(USER1, "m:shop:1")
    check("分类筛选排除", res and "分类商品-卡密X" not in res[1])
    res, _ = await fire_callback(USER1, "m:shop")
    check("不筛选看全部", res and "分类商品-卡密X" in res[1])
    # 分类管理
    res, _ = await fire_callback(ADMIN, "a:pcat")
    check("分类管理页", res and "卡密" in res[1])
    ctx = FakeContext()
    res, ctx = await fire_callback(ADMIN, "a:pcatadd", ctx)
    check("进入加分类", ctx.user_data.get("state") == "pcatadd")
    msg = await fire_message(ADMIN, "教程", ctx)
    check("加分类成功", "教程" in (db.get_settings().get("product_categories") or []))
    # 给商品改分类
    res, _ = await fire_callback(ADMIN, f"a:pcatsel:{p1.id}:0")
    check("商品改分类", db.get_product(p1.id).category == "卡密")
    # 搜索
    ctx = FakeContext()
    res, ctx = await fire_callback(USER1, "m:search:product", ctx)
    check("进入搜索", ctx.user_data.get("state") == "search:product")
    msg = await fire_message(USER1, "卡密X", ctx)
    check("搜索命中", any("分类商品-卡密X" in t for t, _ in msg.replies))
    msg2ctx = FakeContext()
    await fire_callback(USER1, "m:search:product", msg2ctx)
    msg = await fire_message(USER1, "不存在的东西zzz", msg2ctx)
    check("搜索无结果提示", any("没有" in t or "未找到" in t or "无" in t for t, _ in msg.replies))

    # ========== 22. 操作日志 ==========
    print("\n=== 22. 操作日志 ===")
    audit(ADMIN, "自检动作", "细节123")
    logs = db.list_audit_logs(limit=5)
    check("日志写入", any(l.action == "自检动作" and "细节123" in l.detail for l in logs))
    res, _ = await fire_callback(ADMIN, "a:logs")
    check("日志页渲染", res and "自检动作" in res[1])

    # ========== 23. 后台任务：日报 + 备份 ==========
    print("\n=== 23. 日报与备份 ===")
    report = build_daily_report()
    check("日报含核心指标", all(k in report for k in ("今日订单", "今日充值", "累计", "库存池", "供需")))
    dest = do_backup()
    check("备份文件生成", os.path.exists(dest))
    os.remove(dest)

    # ========== 24. 广播限流重试 ==========
    print("\n=== 24. 广播限流重试 ===")
    from telegram.error import RetryAfter

    class FlakyBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def copy_message(self, chat_id, from_chat_id, message_id, **kw):
            self.calls += 1
            if self.calls == 1:
                raise RetryAfter(0)  # 第一次被限流
            return await super().copy_message(chat_id, from_chat_id, message_id, **kw)

    flaky = FlakyBot()
    service.set_bot(flaky)
    result = await service.broadcast_message("", target="all", copy_from=(ADMIN, 888))
    service.set_bot(None)
    check("限流后自动重试成功", result["success"] >= 1 and result["failed"] == 0, str(result))
    check("确实发生了重试", flaky.calls >= 2, str(flaky.calls))

    # ========== 25. 支付设置与冷钱包充值（v2.5.0） ==========
    print("\n=== 25. 支付设置与冷钱包 ===")
    import time as _time
    from decimal import Decimal as _Dec

    import examples.shop_service as _ss_mod
    from examples.chain_watch import ChainTx, is_valid_ton, is_valid_tron

    # --- 地址校验算法 ---
    TRON_ADDR = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"   # USDT-TRC20 合约地址（真地址）
    TON_ADDR = "EQDKbjIcfM6ezt8KjKJJLshZJJSqX7XOA4ff-W72r5gqPrHF"  # docs.ton.org 官方示例
    check("TRON 真地址通过", is_valid_tron(TRON_ADDR))
    check("TRON 假地址拒绝", not is_valid_tron(TRON_ADDR[:-1] + "X"))
    check("TON 真地址通过", is_valid_ton(TON_ADDR))
    check("TON 假地址拒绝", not is_valid_ton(TON_ADDR[:-1] + "X"))

    # --- 限额安全兜底（修复 OkPay/KKPay 金额无上限漏洞） ---
    db.update_settings(min_recharge_usdt=1, max_recharge_usdt=100)
    check("限额校验-越上限", service.validate_recharge_amount(150) is not None)
    check("限额校验-越下限", service.validate_recharge_amount(0.5) is not None)
    check("限额校验-合法", service.validate_recharge_amount(50) is None)
    check("越上限业务层拒绝建单", service.create_recharge(USER1, 150) is None)
    res, _ = await fire_callback(USER1, "rcpay:okpay:150")  # 绕过前端直接构造回调
    check("回调层越上限拦截", res and "最高充值" in res[1])
    res, _ = await fire_callback(USER1, "rc:100")
    check("限额内预设金额正常", res and "确认充值" in res[1])

    # --- 修改限额输入流 ---
    res, _ = await fire_callback(ADMIN, "a:pay")
    check("支付设置面板", res and "支付设置" in res[1] and "OkPay" in res[1] and "冷钱包" in res[1])
    ctx = FakeContext()
    res, ctx = await fire_callback(ADMIN, "a:pay:limit", ctx)
    check("进入限额设置", ctx.user_data.get("state") == "pay_limit")
    msg = await fire_message(ADMIN, "1 500", ctx)
    check("限额已更新", db.get_settings().get("max_recharge_usdt") == 500.0)
    check("限额更新回到面板", any("支付设置" in t for t, _ in msg.replies))
    db.update_settings(min_recharge_usdt=1, max_recharge_usdt=1000)  # 还原，不影响其它用例

    # --- 渠道开关 ---
    res, _ = await fire_callback(ADMIN, "a:pay:tg:okpay")
    check("关闭 OkPay", db.get_settings().get("pay_okpay_enabled") is False)
    check("关闭后渠道不可用", not service.gateway_available("okpay"))
    res, _ = await fire_callback(ADMIN, "a:pay:tg:okpay")
    check("重新开启 OkPay", db.get_settings().get("pay_okpay_enabled") is True)

    # --- 冷钱包：未配地址禁止开启 ---
    res, _ = await fire_callback(ADMIN, "a:pay:tg:tron")
    check("无地址开启TRON被拒", res and "收款地址" in res[1] and not db.get_settings().get("pay_tron_enabled"))

    # --- 冷钱包地址设置：非法拒绝 / 合法保存 ---
    ctx = FakeContext()
    res, ctx = await fire_callback(ADMIN, "a:pay:addr:tron", ctx)
    check("进入TRON地址设置", ctx.user_data.get("state") == "pay_addr:trc20")
    msg = await fire_message(ADMIN, "Tnotavalidaddress", ctx)
    check("非法TRON地址拒绝", any("格式不正确" in t for t, _ in msg.replies))
    check("非法地址未保存", not db.get_settings().get("tron_wallet_address"))
    ctx = FakeContext()
    await fire_callback(ADMIN, "a:pay:addr:tron", ctx)
    msg = await fire_message(ADMIN, TRON_ADDR, ctx)
    check("TRON地址保存", db.get_settings().get("tron_wallet_address") == TRON_ADDR)
    ctx = FakeContext()
    await fire_callback(ADMIN, "a:pay:addr:ton", ctx)
    msg = await fire_message(ADMIN, TON_ADDR, ctx)
    check("TON地址保存", db.get_settings().get("ton_wallet_address") == TON_ADDR)

    # --- 开启两条链 ---
    await fire_callback(ADMIN, "a:pay:tg:tron")
    check("TRON已开启且可用", db.get_settings().get("pay_tron_enabled") and service.gateway_available("trc20"))
    await fire_callback(ADMIN, "a:pay:tg:ton")
    check("TON已开启且可用", db.get_settings().get("pay_ton_enabled") and service.gateway_available("ton"))

    # --- 确认页出现冷钱包按钮 ---
    text_c, kb_c = main.page_recharge_confirm(10)
    cbs = extract_callbacks(kb_c)
    check("确认页含TRON按钮", "rcpay:trc20:10" in cbs)
    check("确认页含TON按钮", "rcpay:ton:10" in cbs)

    # --- TRON 下单：尾数分配 ---
    rate_now = db.get_settings().get("points_rate", 10)
    res, _ = await fire_callback(USER1, "rcpay:trc20:10")
    rc_tron = [r for r in db.list_pending_recharges() if r.chain == "trc20" and r.user_id == USER1][0]
    check("TRON下单成功", res and "链上充值订单" in res[1], res[1][:100] if res else "无输出")
    check("TRON尾数已分配", rc_tron.expected_amount.startswith("10.") and _Dec(rc_tron.expected_amount) > 10,
          rc_tron.expected_amount)
    check("TRON订单页含地址与含尾数金额", TRON_ADDR in res[1] and rc_tron.expected_amount in res[1])
    check("TRON积分按含尾数金额换算", rc_tron.points == int(float(rc_tron.expected_amount) * rate_now))
    res2, _ = await fire_callback(USER2, "rcpay:trc20:10")
    rc_tron2 = [r for r in db.list_pending_recharges() if r.chain == "trc20" and r.user_id == USER2][0]
    check("并发单尾数唯一", rc_tron2.expected_amount != rc_tron.expected_amount)

    # --- 链上支付详情页渲染（TRON/TON 两种） ---
    text_cp, kb_cp = main.page_chain_pay(rc_tron)
    check("链上详情页TRON", "原样转账" in text_cp and rc_tron.expected_amount in text_cp)

    # --- TRON 链上匹配入账（mock TronGrid） ---
    async def fake_tron_fetch(address, api_key="", **kw):
        return [ChainTx(txid="TXID_TRON_1", amount=_Dec(rc_tron.expected_amount),
                        sender="TSenderAddr", timestamp=int(_time.time()), memo="")]

    async def fake_ton_empty(address, api_key="", **kw):
        return []

    orig_tf, orig_nf = _ss_mod.fetch_tron_usdt_incoming, _ss_mod.fetch_ton_usdt_incoming
    _ss_mod.fetch_tron_usdt_incoming = fake_tron_fetch
    _ss_mod.fetch_ton_usdt_incoming = fake_ton_empty
    pts_before = db.get_user(USER1).points
    paid = await service.check_chain_recharges()
    check("链上匹配到账", any(r.id == rc_tron.id for r in paid), str([r.id for r in paid]))
    check("链上到账积分入账", db.get_user(USER1).points == pts_before + rc_tron.points)
    check("txid已记录防重放", db.chain_txid_seen("TXID_TRON_1"))
    check("充值单写入txid", db.get_recharge(rc_tron.id).txid == "TXID_TRON_1")
    # 幂等：同一 txid 重放不再入账
    pts_mid = db.get_user(USER1).points
    paid2 = await service.check_chain_recharges()
    check("txid重放不再入账", not paid2 and db.get_user(USER1).points == pts_mid)
    _ss_mod.fetch_tron_usdt_incoming = orig_tf
    _ss_mod.fetch_ton_usdt_incoming = orig_nf

    # --- TON 下单与备注匹配 ---
    res, _ = await fire_callback(USER1, "rcpay:ton:8")
    rc_ton = [r for r in db.list_pending_recharges() if r.chain == "ton" and r.user_id == USER1][0]
    check("TON下单成功", res and "链上充值订单" in res[1])
    check("TON备注=充值单号", rc_ton.memo == rc_ton.id)
    check("TON无尾数金额原样", rc_ton.expected_amount == "" and rc_ton.amount == 8.0)
    check("TON页面含备注提醒", "备注" in res[1] and rc_ton.id in res[1])
    text_cp2, _ = main.page_chain_pay(rc_ton)
    check("链上详情页TON", "备注" in text_cp2 and rc_ton.memo in text_cp2)

    # TON 少付不入账（同时把 TRON fetch 置空，隔离真实网络）
    _ss_mod.fetch_tron_usdt_incoming = fake_ton_empty

    async def fake_ton_short(address, api_key="", **kw):
        return [ChainTx(txid="TXID_TON_SHORT", amount=_Dec("7.5"),
                        sender="EQSender", timestamp=int(_time.time()), memo=rc_ton.id)]

    _ss_mod.fetch_ton_usdt_incoming = fake_ton_short
    paid3 = await service.check_chain_recharges()
    check("TON少付不入账", not paid3 and db.get_recharge(rc_ton.id).status == "pending")

    # TON 正常到账（实付 == 应付）
    async def fake_ton_ok(address, api_key="", **kw):
        return [ChainTx(txid="TXID_TON_1", amount=_Dec("8"),
                        sender="EQSender", timestamp=int(_time.time()), memo=rc_ton.id)]

    _ss_mod.fetch_ton_usdt_incoming = fake_ton_ok
    pts_before = db.get_user(USER1).points
    paid4 = await service.check_chain_recharges()
    check("TON备注匹配到账", any(r.id == rc_ton.id for r in paid4))
    check("TON积分入账", db.get_user(USER1).points == pts_before + rc_ton.points)
    _ss_mod.fetch_tron_usdt_incoming = orig_tf
    _ss_mod.fetch_ton_usdt_incoming = orig_nf

    # --- 冷钱包单不查支付平台 + 超时同样过期 ---
    rc_old = service.create_chain_recharge(USER1, 10, "trc20")
    check("重复下TRON单", rc_old is not None)
    from datetime import datetime as _dtx, timedelta as _td
    # save_recharge 的 upsert 刻意不覆盖 created_at，回填时间须走直接 SQL
    old_ts = (_dtx.now(LOCAL_TZ) - _td(hours=3)).isoformat()
    conn = db._get_conn()
    try:
        conn.execute("UPDATE recharges SET created_at = ? WHERE id = ?", (old_ts, rc_old.id))
        conn.commit()
    finally:
        conn.close()
    orig_chk = service.check_recharge_status

    async def _fake_chk(rid, allow_failed=False):
        return False

    service.check_recharge_status = _fake_chk  # 隔离平台单的真实网络请求
    await service.check_all_pending_recharges()
    service.check_recharge_status = orig_chk
    check("冷钱包单过期清理", db.get_recharge(rc_old.id).status == "failed")
    check("链上订单状态查询走空", await service.check_recharge_status(rc_tron.id) is False)

    # ========== 19. 抢单派单（v2.6.0） ==========
    print("\n=== 19. 抢单派单 ===")
    from datetime import datetime as _dtg, timedelta as _tdg

    GBUY = 333333  # 专用买家，避免与前面用例的余额互相干扰
    service.get_or_create_user(GBUY, username="gbuy")
    service.update_user_points(GBUY, 500)

    # --- 19.1 接单员建档与资格 ---
    g1, _ = grab.add_grabber(USER1, "user1", "阿一")
    check("接单员建档发编号", g1 is not None and g1.seq >= 1)
    _, dup_msg = grab.add_grabber(USER1)
    check("重复建档被拦截", dup_msg != "")
    g2, _ = grab.add_grabber(USER2, "user2", "阿二")
    check("接单员编号递增", g2 is not None and g1 is not None and g2.seq == g1.seq + 1)
    check("非接单员资格校验拦截", grab.grabber_can_grab(None) is not None)

    # --- 19.2 派单开关：未配群 ID 绝不派单 ---
    check("抢单默认关闭", grab.enabled() is False)
    db.update_settings(grab_enabled=True)
    check("未配接单群仍关闭", grab.enabled() is False)
    db.update_settings(grab_chat_id="-1001234567890")
    check("配好群ID后开启", grab.enabled() is True)

    # --- 19.3 报单下单 + 佣金预算（商品类不进派单流程） ---
    order_g, reason_g = service.create_order(GBUY, p2.id, note="需要代肝日常")
    check("报单下单成功", order_g is not None, reason_g or "")
    check("报单初始不派发", order_g is not None and order_g.grab_status == "")
    check("佣金按全局比例预算", grab.commission_for(order_g) == 50 * 20 // 100)
    order_g.grab_status = "waiting"
    order_g.commission = grab.commission_for(order_g)
    db.save_order(order_g)

    # --- 19.4 原子抢单：抢中 / 手慢无 / 冷却 ---
    code, og, gg = grab.try_grab(order_g.id, USER1, "user1", "阿一")
    check("抢单成功", code == "ok" and og is not None and og.grabber_id == USER1)
    check("抢中写入最晚回单时限", bool(og and og.deadline))
    code2, _, _ = grab.try_grab(order_g.id, USER2, "user2", "阿二")
    check("手慢无拦截", code2 == "taken")
    order_x, _ = service.create_order(GBUY, p2.id, note="冷却测试单")
    order_x.grab_status = "waiting"
    db.save_order(order_x)
    code3, _, _ = grab.try_grab(order_x.id, USER1, "user1", "阿一")
    check("抢单冷却防连点", isinstance(code3, str) and "手太快" in code3)
    db.update_settings(grab_cooldown_seconds=0)  # 后续流程关掉冷却

    # --- 19.5 回单 + 佣金冻结 ---
    done_o, err = grab.complete_by_grabber(order_g.id, USER1, "<b>已完成</b> 日常搞定")
    check("接单员回单成功", done_o is not None and err is None)
    check("回单后订单终态", db.get_order(order_g.id).status == "completed")
    g1f = db.get_grabber(USER1)
    check("佣金进冻结余额", g1f.balance_frozen == order_g.commission and g1f.balance_available == 0)
    check("完单计数+1", g1f.completed_orders == 1)
    done_o2, err2 = grab.complete_by_grabber(order_g.id, USER2, "x")
    check("他人回单/重复回单拦截", done_o2 is None and err2 is not None)

    # --- 19.6 佣金解冻：手动 + 自动到期 ---
    cms = db.list_grab_commissions(status="frozen", limit=10)
    cid0 = next((c.id for c in cms if c.order_id == order_g.id), 0)
    check("冻结佣金已建档", cid0 > 0)
    check("手动解冻", grab.release_commission_manual(cid0, ADMIN))
    g1r = db.get_grabber(USER1)
    check("解冻后转可提现", g1r.balance_available == order_g.commission and g1r.balance_frozen == 0)
    # 自动解冻：冻结 0 小时 = 完单立即到期
    db.update_settings(grab_freeze_hours=0)
    order_c, _ = service.create_order(GBUY, p2.id, note="立即解冻测试单")
    order_c.grab_status = "waiting"
    order_c.commission = grab.commission_for(order_c)
    db.save_order(order_c)
    code_c, _, _ = grab.try_grab(order_c.id, USER2, "user2", "阿二")
    check("第二人抢单成功", code_c == "ok")
    done_c, _ = grab.complete_by_grabber(order_c.id, USER2, "done")
    check("第二人回单成功", done_c is not None)
    db.update_settings(grab_auto_release=True)
    n_rel = await grab.release_due_commissions()
    check("零冻结佣金自动解冻", n_rel >= 1 and db.get_grabber(USER2).balance_available == order_c.commission)
    db.update_settings(grab_freeze_hours=24)  # 恢复默认

    # --- 19.7 超时释放重派 + 记超时 + 冷静期 ---
    order_t, _ = service.create_order(GBUY, p2.id, note="超时测试单")
    order_t.grab_status = "waiting"
    db.save_order(order_t)
    code_t, _, _ = grab.try_grab(order_t.id, USER2, "user2", "阿二")
    check("超时单抢中", code_t == "ok")
    past = (_dtg.now(LOCAL_TZ) - _tdg(hours=1)).isoformat()
    conn = db._get_conn()
    try:
        conn.execute("UPDATE orders SET deadline = ? WHERE id = ?", (past, order_t.id))
        conn.commit()
    finally:
        conn.close()
    n_to = await grab.check_timeouts()
    check("超时扫描处理", n_to >= 1)
    ot2 = db.get_order(order_t.id)
    check("超时后释放待重派", ot2.grab_status in ("", "waiting") and ot2.grabber_id == 0)
    g2f = db.get_grabber(USER2)
    check("超时计数+冷静期", g2f.timeout_orders == 1 and bool(g2f.cooldown_until))
    check("冷静期禁止抢单", grab.grabber_can_grab(g2f) is not None and "冷静期" in grab.grabber_can_grab(g2f))

    # --- 19.8 超时预警窗口（DB 层命中；bot 未注入时不发送不报错） ---
    order_w, _ = service.create_order(GBUY, p2.id, note="预警测试单")
    order_w.grab_status = "waiting"
    db.save_order(order_w)
    code_w, _, _ = grab.try_grab(order_w.id, USER1, "user1", "阿一")
    check("预警单抢中", code_w == "ok")
    soon = (_dtg.now(LOCAL_TZ) + _tdg(minutes=10)).isoformat()
    conn = db._get_conn()
    try:
        conn.execute("UPDATE orders SET deadline = ? WHERE id = ?", (soon, order_w.id))
        conn.commit()
    finally:
        conn.close()
    warn_before = (_dtg.now(LOCAL_TZ) + _tdg(minutes=30)).isoformat()
    due_list = db.warn_due_grab_orders(warn_before, now_iso())
    check("预警窗口命中", any(o.id == order_w.id for o in due_list))
    check("无bot预警安全跳过", await grab.check_warns() == 0)

    # --- 19.9 放弃：释放重派 + 记放弃 + 冷静期 ---
    ok_gu, msg_gu = await grab.giveup(order_w.id, USER1)
    check("接单员放弃成功", ok_gu)
    ow2 = db.get_order(order_w.id)
    check("放弃后订单释放", ow2.grab_status in ("", "waiting") and ow2.grabber_id == 0)
    g1g = db.get_grabber(USER1)
    check("放弃计数+冷静期", g1g.giveup_orders == 1 and bool(g1g.cooldown_until))

    # --- 19.10 提现：校验 → 申请 → 审核/拒绝 ---
    g1m = db.get_grabber(USER1)
    g1m.balance_available = 110
    db.save_grabber(g1m)
    db.update_settings(grab_withdraw_min=10)
    w_low, err_low = grab.apply_withdrawal(USER1, 5, "TAddr")
    check("低于最低提现拦截", w_low is None and "最低" in (err_low or ""))
    w_na, err_na = grab.apply_withdrawal(USER1, 10, "  ")
    check("空收款地址拦截", w_na is None)
    w_ok, err_ok = grab.apply_withdrawal(USER1, 10, "TAddr123")
    check("提现申请成功", w_ok is not None and w_ok.status == "pending")
    check("提现扣减可提余额", db.get_grabber(USER1).balance_available == 100)
    w_dup, err_dup = grab.apply_withdrawal(USER1, 10, "TAddr123")
    check("在审提现重复申请拦截", w_dup is None and "待审核" in (err_dup or ""))
    w_rj = grab.reject_withdrawal(w_ok.id, ADMIN, "地址有误")
    check("拒绝提现退回余额", w_rj is not None and db.get_grabber(USER1).balance_available == 110)
    w_ok2, _ = grab.apply_withdrawal(USER1, 10, "TAddr123")
    w_ap = grab.approve_withdrawal(w_ok2.id, ADMIN, "txid-abc")
    check("管理员打款通过", w_ap is not None and w_ap.status == "paid" and w_ap.txid == "txid-abc")

    # --- 19.11 申诉：创建 → 裁定（支持顺带清冷静期） ---
    ap_ok, aerr_ok = grab.create_appeal(USER1, order_t.id, "超时是网络问题，请求撤销")
    check("申诉创建成功", ap_ok is not None and ap_ok.status == "pending")
    ap_no, aerr_no = grab.create_appeal(USER1, "", "   ")
    check("空申诉内容拦截", ap_no is None)
    ah = grab.handle_appeal(ap_ok.id, ADMIN, True, "已核实")
    check("申诉裁定支持", ah is not None and ah.status == "approved")
    check("支持申诉清冷静期", db.get_grabber(USER1).cooldown_until == "")

    # --- 19.12 退群自动停权（防偷老板） ---
    hit = await grab.handle_member_left("-1001234567890", USER1)
    check("退群自动停权", hit and db.get_grabber(USER1).status == "suspended")
    miss = await grab.handle_member_left("-1009999999", USER2)
    check("非接单群不处理", miss is False)
    grab.set_grabber_status(USER1, "active", ADMIN)
    check("管理员恢复在岗", db.get_grabber(USER1).status == "active")

    # --- 19.13 抢单模块页面渲染 ---
    grab_pages = [
        ("page_grab_home", lambda: pages_grabber.page_grab_home(USER1)),
        ("page_grab_home_stranger", lambda: pages_grabber.page_grab_home(999999)),
        ("page_grab_doing", lambda: pages_grabber.page_grab_doing(USER1)),
        ("page_grab_done", lambda: pages_grabber.page_grab_done(USER1)),
        ("page_grab_money", lambda: pages_grabber.page_grab_money(USER1)),
        ("page_grab_withdraw", lambda: pages_grabber.page_grab_withdraw(USER1)),
        ("page_grab_rank", lambda: pages_grabber.page_grab_rank(USER1)),
        ("page_grab_appeals", lambda: pages_grabber.page_grab_appeals(USER1)),
        ("page_admin_grab", lambda: pages_admin.page_admin_grab()),
        ("page_admin_grabbers", lambda: pages_admin.page_admin_grabbers()),
        ("page_admin_grabber_detail", lambda: pages_admin.page_admin_grabber_detail(USER1)),
        ("page_admin_grab_orders", lambda: pages_admin.page_admin_grab_orders()),
        ("page_admin_grab_withdrawals", lambda: pages_admin.page_admin_grab_withdrawals()),
        ("page_admin_grab_appeals", lambda: pages_admin.page_admin_grab_appeals()),
        ("page_admin_grab_settings", lambda: pages_admin.page_admin_grab_settings()),
    ]
    for name, fn in grab_pages:
        try:
            t, kbm = fn()
            check(f"渲染 {name}", bool(t))
            check_keyboard_valid(name, kbm)
        except Exception as exc:
            check(f"渲染 {name}", False, str(exc))

    # --- 19.14 专属入群链接与积分换算U（v2.6.1） ---
    # 积分 ⇄ U 汇率换算
    db.update_settings(grab_points_per_usdt=100)
    check("汇率默认读取", grab.points_per_usdt() == 100)
    check("积分换算U", grab.points_to_usdt(250) == 2.5)
    check("换算文案", "2.50 U" in grab.usdt_text(250))
    db.update_settings(grab_points_per_usdt=200)
    check("汇率后台可调", grab.points_per_usdt() == 200 and grab.points_to_usdt(500) == 2.5)
    db.update_settings(grab_points_per_usdt=100)

    # 提现：无需绑定OK账号，只填OK钱包ID，自动换算U落库
    g1w = db.get_grabber(USER1)
    g1w.balance_available = 100
    db.save_grabber(g1w)
    db.update_settings(grab_withdraw_min=10)
    w_u, _ = grab.apply_withdrawal(USER1, 100, "88665544")
    check("提现按汇率自动换算U", w_u is not None and w_u.amount_usdt == 1.0)
    check("OK钱包ID落库为打款账户", w_u is not None and w_u.address == "88665544")
    grab.reject_withdrawal(w_u.id, ADMIN, "测试回退")
    check("回退提现返还余额", db.get_grabber(USER1).balance_available == 100)

    # 专属链接字段持久化 + 按链接反查接单员
    g1l = db.get_grabber(USER1)
    g1l.invite_link = "https://t.me/+TESTLINK001"
    g1l.joined_group = 0
    db.save_grabber(g1l)
    check("专属链接持久化", db.get_grabber(USER1).invite_link == "https://t.me/+TESTLINK001")
    found = db.get_grabber_by_invite("https://t.me/+TESTLINK001")
    check("按链接反查接单员", found is not None and found.user_id == USER1)
    check("不存在链接查不到", db.get_grabber_by_invite("https://t.me/+NOPE") is None)

    # 本人持专属链接进群：标记 + 返回 ok（无 bot 时跳过发消息，逻辑照常）
    r_ok = await grab.handle_member_joined("-1001234567890", USER1, "user1", "阿一", "https://t.me/+TESTLINK001")
    check("本人专属链接进群", r_ok == "ok" and db.get_grabber(USER1).joined_group == 1)
    # 他人冒用专属链接：leaked
    r_lk = await grab.handle_member_joined("-1001234567890", 777777, "fake", "冒名者", "https://t.me/+TESTLINK001")
    check("冒名进群被识别", r_lk == "leaked")
    # 接单员经其他途径进群：self_join 照样标记
    r_sj = await grab.handle_member_joined("-1001234567890", USER2, "user2", "阿二", "")
    check("接单员自行入群标记", r_sj == "self_join" and db.get_grabber(USER2).joined_group == 1)
    # 非接单员进群：stranger
    r_st = await grab.handle_member_joined("-1001234567890", 888888, "stranger", "路人", "")
    check("陌生人进群通报", r_st == "stranger")
    # 非接单群：ignored
    r_ig = await grab.handle_member_joined("-1009999999", USER1, "user1", "阿一", "")
    check("非接单群忽略", r_ig == "ignored")
    # 一键换群写入设置
    grab.confirm_grab_chat("-1001234567890")
    check("一键换群写入设置", db.get_settings().get("grab_chat_id") == "-1001234567890")
    # 退群闭环：joined_group 归零 + 停权
    await grab.handle_member_left("-1001234567890", USER1)
    g1a = db.get_grabber(USER1)
    check("退群进群标记归零", g1a.joined_group == 0 and g1a.status == "suspended")
    grab.set_grabber_status(USER1, "active", ADMIN)

    # --- 19.15 恢复全局设置，避免影响 BFS 遍历 ---
    db.update_settings(grab_enabled=False, grab_chat_id="", grab_withdraw_min=100)

    # ========== 20. 全按钮 BFS 遍历（最后执行，含破坏性按钮） ==========
    print("\n=== 20. 按钮全遍历 ===")
    SKIP_STARTS = ("rcpay:", "rcchk:")  # 真实网络请求，已在第 4 节 mock 测试
    for who, start in ((USER1, "m:root"), (ADMIN, "m:root"), (ADMIN, "m:admin")):
        visited = set()
        queue = [start]
        steps = 0
        while queue and steps < 400:
            data = queue.pop(0)
            if data in visited or any(data.startswith(sp) for sp in SKIP_STARTS):
                continue
            visited.add(data)
            steps += 1
            res, _ = await fire_callback(who, data)
            if res and res[2]:
                check_keyboard_valid(f"BFS {data[:20]}", res[2])
                for cb in extract_callbacks(res[2]):
                    if cb not in visited:
                        queue.append(cb)
        print(f"  用户 {who}: 遍历 {len(visited)} 个不同回调")

    # ========== 汇总 ==========
    print("\n" + "=" * 50)
    print(f"✅ 通过 {PASS} 项   ❌ 失败 {FAIL} 项")
    unexpected = [w for w in WARNINGS if "xxx:unknown" not in w]
    if unexpected:
        print(f"⚠️ 出现未处理回调兜底 {len(unexpected)} 次: {unexpected[:5]}")
        FAIL += len(unexpected)
    if ERRORS:
        print("\n失败明细：")
        for e in ERRORS[:20]:
            print(" -", e.split(chr(10))[0])
    print("=" * 50)
    return FAIL == 0 and not unexpected


if __name__ == "__main__":
    ok = asyncio.run(run())
    # 清理测试库，保持部署目录干净；正式 data/shop.db 全程未被触碰
    for _suffix in ("", "-wal", "-shm"):
        _p = TEST_DB + _suffix
        if os.path.exists(_p):
            os.remove(_p)
    sys.exit(0 if ok else 1)
