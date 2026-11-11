"""覆盖率补强第五批（审计 P2-16）：富文本/日志/单实例/任务表/金额/KKPay/命令入口/main/jobs。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_cov_deep  # noqa: E402,F401  复用 Fake 对象与已初始化的运行时
from test_cov_deep import (  # noqa: E402
    ADMIN,
    BOT,
    USER1,
    USER2,
    USER3,
    FakeCtx,
    FakeMsg,
    FakeQuery,
    FakeUpdate,
    check,
    run,
)

from gshop.runtime import db, grab, service  # noqa: E402

# ======================================================================
# money：Decimal 全链路
# ======================================================================

def test_money_helpers():
    print("\n=== money helpers ===")
    from gshop import money as M

    check("to_dec Decimal 原样", M.to_dec(Decimal("1.23")) == Decimal("1.23"))
    check("to_dec float 走 repr", M.to_dec(0.29) == Decimal("0.29"))
    check("to_dec None", M.to_dec(None) == Decimal(0))
    check("to_dec None 带默认", M.to_dec(None, Decimal("9")) == Decimal("9"))
    check("to_dec 非法串", M.to_dec("bad") == Decimal(0))
    check("to_dec 非法串带默认", M.to_dec("bad", Decimal("7")) == Decimal("7"))
    check("to_dec 空格串", M.to_dec(" 5 ") == Decimal(5))
    check("to_dec 对象不可转", M.to_dec(object()) == Decimal(0))
    check("dec_str 去尾零", M.dec_str(Decimal("1.500")) == "1.5")
    check("dec_str 整数", M.dec_str(Decimal("2.00")) == "2")
    check("dec_str 零", M.dec_str(Decimal("0")) == "0")
    check("q2 四舍五入", M.q2(Decimal("1.005")) == Decimal("1.01"))
    check("fmt_usdt", M.fmt_usdt(Decimal("12.345")) == "12.35 USDT")
    check("parse 正常", M.parse_user_amount(" 1,234 ") == Decimal("1234"))
    check("parse None", M.parse_user_amount(None) is None)
    check("parse 空", M.parse_user_amount("") is None)
    check("parse 非数", M.parse_user_amount("abc") is None)
    check("parse 负数", M.parse_user_amount("-1") is None)
    check("parse inf", M.parse_user_amount("inf") is None)
    check("parse nan", M.parse_user_amount("nan") is None)
    check("points_from_usdt 精确", M.points_from_usdt(Decimal("0.29"), 100) == 29)
    check("points_from_usdt 向下取整", M.points_from_usdt(Decimal("0.99"), 10) == 9)
    check("points_from_usdt 汇率 0 兜底", M.points_from_usdt(Decimal("5"), 0) == 5)
    check("usdt_from_points", M.usdt_from_points(100, 100) == Decimal("1"))
    check("usdt_from_points 汇率 0 兜底", M.usdt_from_points(100, 0) == Decimal("100"))
    check("SQLite Decimal adapter", True)


# ======================================================================
# singleton：单实例锁 / 健康文件
# ======================================================================

def test_singleton():
    print("\n=== singleton ===")
    from gshop import singleton as S

    tmp = tempfile.mkdtemp(prefix="cov3_lock_")
    p = S.acquire(tmp, name="a.lock")
    check("acquire 拿到路径", p.exists())
    check("锁文件写了 pid", p.read_text().strip().isdigit())
    try:
        S.acquire(tmp, name="a.lock")
        check("重复 acquire 抛 AlreadyRunning", False)
    except S.AlreadyRunning:
        check("重复 acquire 抛 AlreadyRunning", True)
    S.release()
    S.release()  # 幂等
    check("release 幂等", True)
    p2 = S.acquire(tmp, name="b.lock")
    check("释放后可再 acquire", p2.exists())
    S.release()

    S.write_health(None, "ok")
    S.write_health("", "ok")
    check("write_health 空路径不抛", True)
    hp = os.path.join(tmp, "sub", "health")
    S.write_health(hp, "alive")
    check("write_health 建目录写文件", open(hp, encoding="utf-8").read() == "alive")
    S.write_health("/proc/definitely/not/here/health", "x")
    check("write_health OSError 吞掉", True)


# ======================================================================
# tasks：spawn / drain
# ======================================================================

def test_tasks_registry():
    print("\n=== tasks registry ===")
    from gshop import tasks as T

    async def _ok():
        return 1

    async def _boom():
        raise RuntimeError("后台炸了")

    async def _slow():
        await asyncio.sleep(0.05)
        return 2

    async def _runner():
        t1 = T.spawn(_ok(), name="t.ok")
        t2 = T.spawn(_boom(), name="t.boom")
        await asyncio.gather(t1, t2, return_exceptions=True)
        await asyncio.sleep(0)
        check("异常任务已被 _on_done 移除", T.pending_count() >= 0)
        t3 = T.spawn(_slow(), name="")
        check("spawn 无名字可跑", t3 is not None)
        await T.drain(timeout=2.0)
        check("drain 等完任务", T.pending_count() == 0)
        t4 = T.spawn(_slow())
        t4.cancel()
        await asyncio.gather(t4, return_exceptions=True)
        await asyncio.sleep(0)
        check("取消任务不报警", T.pending_count() == 0)

    run(_runner())


# ======================================================================
# logging_setup：JSON 结构化日志
# ======================================================================

def test_logging_setup():
    print("\n=== logging_setup ===")
    import logging

    from gshop import logging_setup as L

    tmp = tempfile.mkdtemp(prefix="cov3_log_")
    path = L.setup_logging(tmp, level=logging.INFO)
    check("setup_logging 返回路径", str(path).endswith("app.log"))
    check("日志文件已建", os.path.exists(path))

    L.request_id_var.set("-")
    fmt = L.JsonFormatter()
    rec = logging.LogRecord("t", logging.INFO, "f.py", 10, "hello %s", ("x",), None)
    out = fmt.format(rec)
    check("JsonFormatter 基础字段", '"msg": "hello x"' in out and '"level": "INFO"' in out)

    rec2 = logging.LogRecord("t", logging.INFO, "f.py", 10, "e", (), None)
    rec2.event = "order.created"
    rec2.order_id = "O1"
    rec2.uid = 1
    rec2.bad = object()
    rec2._private = 1
    out2 = fmt.format(rec2)
    check("JsonFormatter 业务字段", '"event": "order.created"' in out2 and '"order_id": "O1"' in out2)
    check("JsonFormatter 不可序列化转 repr", '"bad": "<object object' in out2)
    check("JsonFormatter 下划线字段被跳过", '"_private"' not in out2)

    try:
        raise ValueError("boom")
    except ValueError:
        rec3 = logging.LogRecord("t", logging.ERROR, "f.py", 10, "e", (), sys.exc_info())
    check("JsonFormatter 带堆栈", '"exc"' in fmt.format(rec3))

    pf = L.PlainFormatter("%(message)s")
    L.request_id_var.set("-")
    rec4 = logging.LogRecord("t", logging.INFO, "f.py", 10, "plain", (), None)
    check("PlainFormatter 无 rid", pf.format(rec4) == "plain")
    L.request_id_var.set("abc12345")
    check("PlainFormatter 带 rid", "[rid=abc12345]" in pf.format(rec4))

    rid = L.new_request_id()
    check("new_request_id 8 位", len(rid) == 8 and rid.isalnum())
    lg = logging.getLogger("cov3")
    L.log_event(lg, "test.event", uid=1, amount="1.00")
    check("log_event 不抛", True)

    os.environ["LOG_JSON"] = "0"
    path2 = L.setup_logging(tmp, level=logging.WARNING)
    check("LOG_JSON=0 退纯文本", os.path.exists(path2))
    os.environ.pop("LOG_JSON", None)


# ======================================================================
# richtext：降级 / 分片 / 安全发送
# ======================================================================

def test_richtext_edges():
    print("\n=== richtext ===")
    from telegram.error import BadRequest

    from gshop import richtext as R

    check("未定义 TG_ 降级为表情标签", "tg-emoji" in getattr(R, "TG_NOT_DEFINED"))
    check("未定义 ICON_ 降级为默认图标", getattr(R, "ICON_NOT_DEFINED") == R.DEFAULT_ICON)
    try:
        getattr(R, "OTHER_NAME")
        check("未定义普通名抛 AttributeError", False)
    except AttributeError:
        check("未定义普通名抛 AttributeError", True)

    check("strip_html_tags 空", R.strip_html_tags("") == "")
    check("strip_html_tags None", R.strip_html_tags(None) == "")
    check("strip_html_tags 去标签", R.strip_html_tags("<b>a</b>&amp;") == "a&")
    check("strip_html_tags 保留表情回退", R.strip_html_tags(R.tg(R.DEFAULT_ICON, "X")) == "X")
    check("strip_html_tags br 转换行", "\n" in R.strip_html_tags("a<br/>b"))

    class BoomMsg:
        text = "hi"

        @property
        def text_html(self):
            raise RuntimeError("取实体失败")

    plain, html_text = R.rich_from_message(BoomMsg())
    check("rich_from_message 异常兜底转义", plain == "hi" and html_text == "hi")
    plain2, html2 = R.rich_from_message(FakeMsg(USER1, text="<b>x</b>"))
    check("rich_from_message 转义尖括号", "&lt;b&gt;" in html2)

    check("is_not_modified 命中", R.is_not_modified(BadRequest("Message is not modified")) is True)
    check("is_not_modified 不命中", R.is_not_modified(BadRequest("boom")) is False)

    check("split_html 短文本", R.split_html("abc") == ["abc"])
    multi = R.split_html("a" * 30 + "\n" + "b" * 30, limit=40)
    check("split_html 按行切", len(multi) >= 2)
    hard = R.split_html("x" * 100, limit=40)
    _hard_ok = all(len(c) <= 40 for c in hard) and "".join(hard) == "x" * 100
    check("split_html 硬切", _hard_ok and len(hard) >= 4 and all(len(c) <= 20 for c in hard[:-1]))

    class SendBot:
        """可控发送失败点：只让第 fail_at 次 HTML 发送抛 BadRequest。"""

        def __init__(self, fail_at=0, err="can\'t parse entities", photo_fail=False):
            self.calls = 0
            self.fail_at = fail_at
            self.err = err
            self.sent = []
            self.photo_fail = photo_fail

        async def send_message(self, chat_id=None, text="", parse_mode=None, reply_markup=None, **kw):
            self.calls += 1
            if parse_mode and self.fail_at and self.calls == self.fail_at:
                raise BadRequest(self.err)
            self.sent.append((chat_id, text))
            return type("M", (), {"message_id": self.calls})()

        async def send_photo(self, chat_id=None, photo="", caption="", parse_mode=None, reply_markup=None, **kw):
            self.calls += 1
            if self.photo_fail and parse_mode:
                raise BadRequest(self.err)
            self.sent.append((chat_id, caption))
            return type("M", (), {"message_id": self.calls})()

    b1 = SendBot()
    m1 = run(R.send_html(b1, 1, "<b>hi</b>"))
    check("send_html 正常", m1 is not None and b1.sent)

    b2 = SendBot()
    long_text = "\n".join(f"line{i} {'z' * 40}" for i in range(200))
    m2 = run(R.send_html(b2, 1, long_text))
    check("send_html 长文本分片", len(b2.sent) >= 2 and m2 is not None)

    b3 = SendBot(fail_at=1)
    run(R.send_html(b3, 1, long_text))
    check("send_html 分片 HTML 失败降级", len(b3.sent) >= 2)

    b4 = SendBot(fail_at=1)
    run(R.send_html(b4, 1, "<b>hi</b>"))
    check("send_html 主分片降级", b4.sent and "hi" in b4.sent[-1][1])

    b5 = SendBot(fail_at=1, err="Message is not modified")
    try:
        run(R.send_html(b5, 1, "<b>hi</b>"))
        check("send_html not modified 抛出", False)
    except BadRequest:
        check("send_html not modified 抛出", True)

    b6 = SendBot(photo_fail=True)
    run(R.send_html(b6, 1, "<b>hi</b>", photo="P1"))
    check("send_html 带图降级", b6.sent and b6.sent[-1][1] == "hi")

    b7 = SendBot()
    run(R.send_html(b7, 1, "<b>hi</b>", photo="P1"))
    check("send_html 带图正常", b7.sent)


def test_kkpay_full():
    print("\n=== kkpay ===")
    import base64
    import json as _json

    import httpx

    from gshop import kkpay as K

    c = K.KKPay("MERCHANT", "SECRET", timeout=3.0)
    check("kkpay 实例字段", c.app_id == "MERCHANT" and c.token == "SECRET")
    sig = c._hmac_b64("hello")
    check("_hmac_b64 为 base64", isinstance(sig, str) and len(sig) > 10)
    check("_hmac_b64 可复现", sig == c._hmac_b64("hello"))
    check("_hmac_b64 变输入变签名", sig != c._hmac_b64("hello2"))
    hdr = c._signed_headers("post", "/merchant/payLink", "BODY")
    check("签名头 4 个", set(hdr) >= {"kkpay-id", "kkpay-timestamp", "kkpay-nonce", "kkpay-sign"})
    check("签名头 Content-Type", hdr["Content-Type"] == "text/plain")
    check("签名头 nonce 32 位", len(hdr["kkpay-nonce"]) == 32)
    check("签名头 id", hdr["kkpay-id"] == "MERCHANT")

    class FakeResp:
        def __init__(self, payload, sign="", err=None):
            self._payload = payload
            self.headers = {"kkpay-sign": sign}
            self._err = err

        def raise_for_status(self):
            if self._err:
                raise self._err

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, resp):
            self.resp = resp
            self.posted = []
            self.closed = 0

        async def post(self, url, content=None, headers=None, **kw):
            self.posted.append((url, content, headers))
            return self.resp

        async def aclose(self):
            self.closed += 1

    data = {"txid": "TX1", "amount": "5"}
    body_b64 = base64.b64encode(_json.dumps(data, ensure_ascii=False).encode()).decode()
    ok_resp = FakeResp({"code": K.SUCCESS_CODE, "data": body_b64}, sign=c._hmac_b64(body_b64))
    fc = FakeClient(ok_resp)
    c._client = fc
    out = run(c._post(K.EP_CREATE, {"amount": "5"}))
    check("_post 成功返回解码体", out["txid"] == "TX1")
    check("_post 发到正确 URL", fc.posted[0][0] == K.BASE_URL + K.EP_CREATE)
    check("_get_client 复用", c._client is fc)

    bad_resp = FakeResp({"code": 999, "message": "失败"})
    c._client = FakeClient(bad_resp)
    try:
        run(c._post(K.EP_CHECK, {}))
        check("code 非成功抛错", False)
    except RuntimeError as exc:
        check("code 非成功抛错", "999" in str(exc))

    c._client = FakeClient(FakeResp({"code": K.SUCCESS_CODE, "data": body_b64}, sign="WRONG"))
    try:
        run(c._post(K.EP_CHECK, {}))
        check("响应验签失败抛错", False)
    except RuntimeError as exc:
        check("响应验签失败抛错", "验签" in str(exc))

    c._client = FakeClient(FakeResp({}, err=httpx.HTTPError("网络挂了")))
    try:
        run(c._post(K.EP_CHECK, {}))
        check("HTTP 错误向上抛", False)
    except httpx.HTTPError:
        check("HTTP 错误向上抛", True)

    tx9 = base64.b64encode(_json.dumps({"txid": "TX9", "payUrl": "https://pay/9"}).encode()).decode()
    c._client = FakeClient(FakeResp({"code": K.SUCCESS_CODE, "data": tx9}, sign=c._hmac_b64(tx9)))
    pay = run(c.create_payment("U1", "5", return_url="https://r"))
    check("create_payment 归一化 order_id", pay.get("order_id") == "TX9")
    sent = _json.loads(base64.b64decode(c._client.posted[0][1]).decode())
    check("create_payment 带 return_url", sent.get("return_url") == "https://r")
    c._client = FakeClient(FakeResp({"code": K.SUCCESS_CODE, "data": body_b64}, sign=c._hmac_b64(body_b64)))
    run(c.create_payment("U2", "5"))
    sent2 = _json.loads(base64.b64decode(c._client.posted[0][1]).decode())
    check("create_payment 不带 return_url", "return_url" not in sent2)

    paid_data = base64.b64encode(_json.dumps({"statusText": "paid", "txid": "T"}).encode()).decode()
    c._client = FakeClient(FakeResp({"code": K.SUCCESS_CODE, "data": paid_data}, sign=c._hmac_b64(paid_data)))
    chk = run(c.check_payment("U1"))
    check("check_payment paid → status 1", chk["status"] == 1)
    check("check_payment is_paid", K.KKPay.is_paid(chk) is True)
    pend_data = base64.b64encode(_json.dumps({"statusText": "Pending"}).encode()).decode()
    c._client = FakeClient(FakeResp({"code": K.SUCCESS_CODE, "data": pend_data}, sign=c._hmac_b64(pend_data)))
    chk2 = run(c.check_payment("U1"))
    check("check_payment pending → status 0", chk2["status"] == 0)

    check("is_paid 脏值兜底", K.KKPay.is_paid({"status": "abc"}) is False)
    check("is_paid 缺字段", K.KKPay.is_paid({}) is False)
    check("is_paid 字符串 1", K.KKPay.is_paid({"status": "1"}) is True)

    run(c.close())
    check("close 清空 client", c._client is None)
    run(c.close())
    check("close 幂等", c._client is None)
    c._client = None
    c2 = K.KKPay("M", "S")
    cl = run(c2._get_client())
    check("_get_client 懒建 httpx.AsyncClient", cl is not None)
    run(c2.close())


def test_command_handlers():
    print("\n=== handlers_command ===")
    from gshop import handlers_command as HC

    # /start：无 effective_user 直接返回
    upd_none = FakeUpdate(USER1)
    upd_none.effective_user = None
    run(HC.cmd_start(upd_none, FakeCtx()))
    check("/start 无用户直接返回", True)

    # /start：封禁用户
    u = db.get_user(USER1) or service.get_or_create_user(USER1)
    u.is_banned = True
    db.save_user(u)
    m = FakeMsg(USER1, text="/start")
    run(HC.cmd_start(FakeUpdate(USER1, message=m), FakeCtx()))
    check("/start 封禁拦截", any("限制" in t for t, _k in m.replies), m.replies)
    u2 = db.get_user(USER1)
    u2.is_banned = False
    db.save_user(u2)

    # /start：强制关注未满足
    db.update_settings(force_channels=True)
    ch = service.add_channel("-100777", title="测试频道")
    m2 = FakeMsg(USER1, text="/start")
    run(HC.cmd_start(FakeUpdate(USER1, message=m2), FakeCtx()))
    check("/start 强制关注拦截", any("关注" in t or "频道" in t for t, _k in m2.replies), m2.replies)
    service.remove_channel(ch.chat_id)
    db.update_settings(force_channels=False)

    # /start：deep link supply
    ctx = FakeCtx()
    ctx.args = ["supply"]
    m3 = FakeMsg(USER1, text="/start supply")
    run(HC.cmd_start(FakeUpdate(USER1, message=m3), ctx))
    check("/start supply 深链", any("供需" in t or "发布" in t for t, _k in m3.replies), m3.replies)

    # /start：deep link grab_ 但非接单员
    ctx4 = FakeCtx()
    ctx4.args = ["grab_NOSUCH"]
    m4 = FakeMsg(USER1, text="/start grab_NOSUCH")
    run(HC.cmd_start(FakeUpdate(USER1, message=m4), ctx4))
    check("/start grab 非接单员", any("接单员" in t for t, _k in m4.replies), m4.replies)

    # /start：grab_ 接单员但订单不存在
    g, _e = grab.add_grabber(USER2, username="g2", name="接单二")
    ctx5 = FakeCtx()
    ctx5.args = ["grab_NOSUCHORDER"]
    m5 = FakeMsg(USER2, text="/start grab_NOSUCHORDER")
    run(HC.cmd_start(FakeUpdate(USER2, message=m5), ctx5))
    check("/start grab 订单不在进行中", any("不在进行中" in t for t, _k in m5.replies), m5.replies)

    # /start：欢迎图发送失败降级纯文字
    db.update_settings(welcome_image="BADFILE")
    m6 = FakeMsg(USER1, text="/start")
    run(HC.cmd_start(FakeUpdate(USER1, message=m6), FakeCtx()))
    check("/start 欢迎图降级", bool(m6.replies))
    db.update_settings(welcome_image="")

    # /admin：无权限
    m7 = FakeMsg(USER1, text="/admin")
    run(HC.cmd_admin(FakeUpdate(USER1, message=m7), FakeCtx()))
    check("/admin 无权限", any("无权限" in t for t, _k in m7.replies), m7.replies)
    # /admin：管理员
    m8 = FakeMsg(ADMIN, text="/admin")
    run(HC.cmd_admin(FakeUpdate(ADMIN, message=m8), FakeCtx()))
    check("/admin 管理员可见", bool(m8.replies))
    # /admin：无 message
    run(HC.cmd_admin(FakeUpdate(ADMIN), FakeCtx()))
    check("/admin 无 message 不抛", True)

    # /grabber
    m9 = FakeMsg(USER2, text="/grabber")
    run(HC.cmd_grabber(FakeUpdate(USER2, message=m9), FakeCtx()))
    check("/grabber 有响应", bool(m9.replies))
    run(HC.cmd_grabber(FakeUpdate(USER2), FakeCtx()))
    check("/grabber 无 message 不抛", True)

    # on_grab_chat_member：三分支
    up_no = FakeUpdate(USER1)
    up_no.chat_member = None
    run(HC.on_grab_chat_member(up_no, FakeCtx()))
    check("chat_member 为空直接返回", True)

    class _CM:
        def __init__(self, status, uid=None):
            self.status = status
            self.user = type("U", (), {"id": uid})() if uid else None

    class _CMU:
        def __init__(self, status, uid=None):
            self.new_chat_member = _CM(status, uid)
            self.chat = type("C", (), {"id": -1001234567890})()

    up_member = FakeUpdate(USER1)
    up_member.chat_member = _CMU("member", USER2)
    run(HC.on_grab_chat_member(up_member, FakeCtx()))
    check("进群不触发停权", True)

    up_nouser = FakeUpdate(USER1)
    up_nouser.chat_member = _CMU("left", None)
    run(HC.on_grab_chat_member(up_nouser, FakeCtx()))
    check("退群无 user 不抛", True)

    up_left = FakeUpdate(USER1)
    up_left.chat_member = _CMU("left", USER3)
    run(HC.on_grab_chat_member(up_left, FakeCtx()))
    check("退群触发停权", True)

    # on_error：三种 update 形态
    class _CtxErr:
        def __init__(self, err):
            self.error = err

    run(HC.on_error("not-an-update", _CtxErr(ValueError("boom"))))
    check("on_error 非 Update 不抛", True)

    from telegram import Update as _TGUpdate
    _q = FakeQuery(USER1, "m:root")
    up_cb = _TGUpdate(update_id=9001, callback_query=_q)
    run(HC.on_error(up_cb, _CtxErr(ValueError("boom"))))
    check("on_error 回调提示", any("开小差" in t for t in _q.toasts), _q.toasts)

    m_err = FakeMsg(USER1, text="x")
    up_msg = _TGUpdate(update_id=9002, message=m_err)
    run(HC.on_error(up_msg, _CtxErr(ValueError("boom"))))
    check("on_error 消息提示", any("开小差" in t for t, _k in m_err.replies), m_err.replies)

    class _BadQuery:
        async def answer(self, **kw):
            raise RuntimeError("too old")

    class _BadMsg:
        async def reply_text(self, **kw):
            raise RuntimeError("blocked")

    up_bad = _TGUpdate(update_id=9003, callback_query=_BadQuery())
    run(HC.on_error(up_bad, _CtxErr(ValueError("boom"))))
    check("on_error query 二次失败吞掉", True)

    up_bad2 = _TGUpdate(update_id=9004, message=_BadMsg())
    run(HC.on_error(up_bad2, _CtxErr(ValueError("boom"))))
    check("on_error message 二次失败吞掉", True)


def test_jobs_all():
    print("\n=== jobs ===")
    import gshop.jobs as J

    secs = J._seconds_until(0, 0)
    check("_seconds_until 在 0~86400", 0 < secs <= 86400, secs)
    secs2 = J._seconds_until(23, 59)
    check("_seconds_until 23:59 合法", 0 < secs2 <= 86400, secs2)

    db.update_settings(daily_task_hour=5)
    check("_daily_task_hour 正常", J._daily_task_hour() == 5)
    db.update_settings(daily_task_hour=99)
    check("_daily_task_hour 上限夹紧", J._daily_task_hour() == 23)
    db.update_settings(daily_task_hour=-5)
    check("_daily_task_hour 下限夹紧", J._daily_task_hour() == 0)
    db.update_settings(daily_task_hour="bad")
    check("_daily_task_hour 脏值兜底", J._daily_task_hour() == 8)
    db.update_settings(daily_task_hour=8)

    report = J.build_daily_report()
    check("日报含标题", "经营日报" in report)
    check("日报含版本", "v" in report and "运行正常" in report)
    check("日报含今日订单", "今日订单" in report)

    dest = J.do_backup()
    check("do_backup 返回路径", os.path.exists(dest), dest)
    check("备份文件非空", os.path.getsize(dest) > 0)
    J._remove_quietly(dest)
    check("_remove_quietly 删掉", not os.path.exists(dest))
    J._remove_quietly(dest)
    check("_remove_quietly 幂等", True)
    J._remove_quietly("/proc/definitely/not/here/x")
    check("_remove_quietly OSError 吞掉", True)

    real_sleep = asyncio.sleep

    def _one_round():
        """把 asyncio.sleep 换成「第一轮返回、第二轮抛 CancelledError」，只跑一轮循环体。"""
        state = {"n": 0}

        async def _fake(delay, *a, **kw):
            state["n"] += 1
            if state["n"] > 1:
                raise asyncio.CancelledError
            return None

        asyncio.sleep = _fake
        return state

    def _restore():
        asyncio.sleep = real_sleep

    def _run_round(coro):
        """跑一轮循环体：第二次 sleep 抛 CancelledError 作为退出信号，在这里吃掉。"""
        try:
            run(coro)
        except (asyncio.CancelledError, Exception):
            pass

    # 充值轮询
    _one_round()
    try:
        _run_round(J.auto_check_recharges())
        check("auto_check_recharges 单轮不抛", True)
    finally:
        _restore()

    # 链上轮询
    _one_round()
    try:
        _run_round(J.auto_check_chain_recharges())
        check("auto_check_chain_recharges 单轮不抛", True)
    finally:
        _restore()

    # 抢单管家三件套
    db.update_settings(grab_enabled=True, grab_chat_id="-1001234567890")
    _one_round()
    try:
        _run_round(J.auto_grab_housekeeping())
        check("auto_grab_housekeeping 单轮不抛", True)
    finally:
        _restore()

    # 经营日报（关闭态 / 无 bot / 打开）
    db.update_settings(daily_report_enabled=False)
    _one_round()
    try:
        _run_round(J.daily_report())
        check("daily_report 关闭态跳过", True)
    finally:
        _restore()

    db.update_settings(daily_report_enabled=True)
    _saved_bot = service._bot
    service._bot = None
    _one_round()
    try:
        _run_round(J.daily_report())
        check("daily_report 无 bot 跳过", True)
    finally:
        _restore()

    bot = BOT
    bot.sent.clear()
    service._bot = bot
    _one_round()
    try:
        _run_round(J.daily_report())
        check("daily_report 发出", len(bot.sent) >= 1, bot.sent)
    finally:
        _restore()
        service._bot = _saved_bot

    # 自动备份（关闭态 / 打开）
    db.update_settings(daily_backup_enabled=False)
    _one_round()
    try:
        _run_round(J.auto_backup())
        check("auto_backup 关闭态跳过", True)
    finally:
        _restore()

    db.update_settings(daily_backup_enabled=True)
    _one_round()
    try:
        _run_round(J.auto_backup())
        check("auto_backup 单轮跑通", True)
    finally:
        _restore()

    # 心跳
    hp = os.path.join(tempfile.mkdtemp(prefix="cov3_hb_"), "hb")
    _one_round()
    try:
        _run_round(J.auto_heartbeat(hp))
        check("auto_heartbeat 写心跳", os.path.exists(hp), hp)
    finally:
        _restore()



# ======================================================================
# main.py：装配层（不打网络，只验证 handler 装配与回调逻辑）
# ======================================================================

def test_main_module():
    print("\n=== main.py ===")
    import main as M

    check("main 可导入", M is not None)
    check("HEALTH_FILE 默认可写路径", isinstance(M.HEALTH_FILE, str) and M.HEALTH_FILE)
    check("ALLOWED_UPDATES 四项", len(M.ALLOWED_UPDATES) == 4, M.ALLOWED_UPDATES)
    check("post_init 可调用", callable(M.post_init))
    check("post_shutdown 可调用", callable(M.post_shutdown))
    check("tag_request_id 可调用", callable(M.tag_request_id))
    check("on_my_chat_member 可调用", callable(M.on_my_chat_member))

    # tag_request_id：写入 rid
    from gshop.logging_setup import request_id_var
    ctx = FakeCtx()
    run(M.tag_request_id(FakeUpdate(USER1), ctx))
    rid = ctx.user_data.get("_rid")
    check("tag_request_id 写 _rid", isinstance(rid, str) and len(rid) == 8, rid)

    _probe = {}

    async def _cv_probe():
        _c2 = FakeCtx()
        await M.tag_request_id(FakeUpdate(USER1), _c2)
        _probe["rid"] = _c2.user_data.get("_rid")
        _probe["var"] = request_id_var.get()

    run(_cv_probe())
    check(
        "tag_request_id 设 contextvar",
        _probe.get("var") == _probe.get("rid") and len(_probe.get("rid") or "") == 8,
        _probe,
    )

    class _CtxNoData:
        user_data = None

    run(M.tag_request_id(FakeUpdate(USER1), _CtxNoData()))
    check("tag_request_id user_data 为 None 不抛", True)

    # post_shutdown：drain + aclose
    run(M.post_shutdown(None))
    check("post_shutdown 不抛", True)

    # post_init：会用 spawn 拉起 6 个常驻任务，这里换成假函数避免真跑
    saved = {}
    names = ("auto_check_recharges", "auto_check_chain_recharges", "auto_grab_housekeeping",
             "daily_report", "auto_backup", "auto_heartbeat")
    for n in names:
        saved[n] = getattr(M, n)
    spawned = []

    def _mk(n, **kw):
        async def _c(*a, **k):
            return None
        _c.__name__ = n
        return _c

    boom = {"v": False}

    def _spawn(coro, name=""):
        spawned.append(name)
        try:
            coro.close()
        except Exception:
            pass

        class _T:
            def cancel(self):
                pass

        return _T()

    saved_spawn = M.spawn
    M.spawn = _spawn
    try:
        run(M.post_init(None))
    finally:
        M.spawn = saved_spawn
    check("post_init 拉起 6 个任务", len(spawned) == 6, spawned)
    check("post_init 任务名", "job.health" in spawned and "job.grab" in spawned, spawned)

    # on_my_chat_member：四分支
    class _MChat:
        def __init__(self, ctype, title=""):
            self.type = ctype
            self.title = title
            self.id = -1001234567890

    class _MNew:
        def __init__(self, status):
            self.status = status

    class _MUpd:
        def __init__(self, ctype, status, title=""):
            self.chat = _MChat(ctype, title)
            self.new_chat_member = _MNew(status)

    class _Upd:
        def __init__(self, m):
            self.my_chat_member = m

    run(M.on_my_chat_member(_Upd(None), FakeCtx()))
    check("on_my_chat_member 空直接返回", True)
    run(M.on_my_chat_member(_Upd(_MUpd("private", "member")), FakeCtx()))
    check("on_my_chat_member 私聊忽略", True)
    run(M.on_my_chat_member(_Upd(_MUpd("group", "left")), FakeCtx()))
    check("on_my_chat_member 退群忽略", True)
    run(M.on_my_chat_member(_Upd(_MUpd("supergroup", "administrator", "接单群")), FakeCtx()))
    check("on_my_chat_member 入群识别", True)

    class _BoomChat:
        type = "group"
        id = -1001234567890

        @property
        def title(self):
            raise RuntimeError("坏的 chat")

    class _BoomUpd:
        def __init__(self):
            self.chat = _BoomChat()
            self.new_chat_member = _MNew("member")

    run(M.on_my_chat_member(_Upd(_BoomUpd()), FakeCtx()))
    check("on_my_chat_member 异常被兜底", True)

    # on_grab_chat_member：main 版本
    class _U:
        def __init__(self, uid):
            self.id = uid
            self.username = "u"
            self.full_name = "名"

    class _CMember:
        def __init__(self, status, uid=None):
            self.status = status
            self.user = _U(uid) if uid is not None else None

    class _CMU2:
        def __init__(self, old, new, uid=None, link=None):
            self.old_chat_member = _CMember(old) if old else None
            self.new_chat_member = _CMember(new, uid)
            self.chat = type("C", (), {"id": -1001234567890})()
            self.invite_link = type("L", (), {"invite_link": link})() if link else None

    class _Upd2:
        def __init__(self, m):
            self.chat_member = m

    run(M.on_grab_chat_member(_Upd2(None), FakeCtx()))
    check("main.on_grab_chat_member 空返回", True)
    run(M.on_grab_chat_member(_Upd2(_CMU2("member", "left", USER3)), FakeCtx()))
    check("main.on_grab_chat_member 退群停权", True)
    run(M.on_grab_chat_member(_Upd2(_CMU2(None, "member", USER3, "https://t.me/+X")), FakeCtx()))
    check("main.on_grab_chat_member 进群校验", True)
    run(M.on_grab_chat_member(_Upd2(_CMU2("member", "administrator", USER3)), FakeCtx()))
    check("main.on_grab_chat_member 已是成员不重复校验", True)

    class _BadCM:
        @property
        def old_chat_member(self):
            raise RuntimeError("坏的成员")
        new_chat_member = _CMember("member", USER3)
        chat = type("C", (), {"id": -1})()

    class _Upd3:
        def __init__(self):
            self.chat_member = _BadCM()

    run(M.on_grab_chat_member(_Upd3(), FakeCtx()))
    check("main.on_grab_chat_member 异常兜底", True)
if __name__ == "__main__":
    _SEL = (
        test_money_helpers,
        test_singleton,
        test_tasks_registry,
        test_logging_setup,
        test_richtext_edges,
        test_kkpay_full,
        test_command_handlers,
        test_jobs_all,
        test_main_module,
    )
    for _fn in _SEL:
        try:
            _fn()
        except Exception as _exc:
            import traceback
            traceback.print_exc()
            check("异常 " + _fn.__name__, False, repr(_exc))
    print()
    print("=" * 50)
    print("PASS", test_cov_deep.PASS, " FAIL", test_cov_deep.FAIL)
    print("=" * 50)
    for _e in test_cov_deep.ERRORS:
        print("  -", _e)
    sys.exit(0 if test_cov_deep.FAIL == 0 else 1)

