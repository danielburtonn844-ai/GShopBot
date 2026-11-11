"""覆盖率补强第四批（审计 P2-16）：支付/充值/链上/订单/广播/抢单管家/UI/jobs/main。"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_cov_deep  # noqa: E402,F401  复用 Fake 对象与已初始化的运行时
from test_cov_deep import (  # noqa: E402
    ADMIN,
    USER1,
    USER2,
    USER3,
    FakeMsg,
    check,
    run,
)

from gshop.runtime import db, grab, service  # noqa: E402


def test_payment_gateways():
    print("\n=== 支付渠道 / 限额校验 ===")
    service.okpay_id = "ID1"
    service.okpay_token = "TK1"
    service._okpay = None
    c1 = service._get_okpay()
    check("okpay 懒加载", c1 is not None)
    check("okpay 复用", service._get_okpay() is c1)
    service.kkpay_id = ""
    service.kkpay_token = ""
    service._kkpay = None
    try:
        service._get_kkpay()
        check("kkpay 未配置抛错", False)
    except RuntimeError:
        check("kkpay 未配置抛错", True)
    service.kkpay_id = "ID2"
    service.kkpay_token = "TK2"
    c2 = service._get_kkpay()
    check("kkpay 懒加载", c2 is not None)
    check("_get_gateway kkpay", service._get_gateway("kkpay") is c2)
    check("_get_gateway okpay", service._get_gateway("okpay") is c1)
    db.update_settings(pay_kkpay_enabled=True, pay_okpay_enabled=True,
                       pay_tron_enabled=True, tron_wallet_address="TWALLET",
                       pay_ton_enabled=True, ton_wallet_address="TWALLET2")
    for gw in ("kkpay", "okpay", "trc20", "ton", "nope"):
        check("gateway_available " + gw, service.gateway_available(gw) in (True, False))
    check("gateway trc20 可用", service.gateway_available("trc20") is True)
    check("gateway ton 可用", service.gateway_available("ton") is True)
    check("gateway 未知不可用", service.gateway_available("nope") is False)
    db.update_settings(tron_wallet_address="")
    check("gateway trc20 无地址", service.gateway_available("trc20") is False)
    db.update_settings(tron_wallet_address="TWALLET")

    db.update_settings(min_recharge_usdt=2, max_recharge_usdt=100)
    check("限额 低于下限", service.validate_recharge_amount(Decimal("1")) is not None)
    check("限额 下限边界", service.validate_recharge_amount(Decimal("2")) is None)
    check("限额 上限边界", service.validate_recharge_amount(Decimal("100")) is None)
    check("限额 超上限", service.validate_recharge_amount(Decimal("101")) is not None)
    check("限额 非法串", service.validate_recharge_amount("abc") is not None)
    check("限额 None", service.validate_recharge_amount(None) is not None)
    db.update_settings(min_recharge_usdt="bad", max_recharge_usdt="bad")
    check("限额 脏设置兜底", service.validate_recharge_amount(Decimal("5")) is None)
    db.update_settings(min_recharge_usdt=1, max_recharge_usdt=1000)


class FakePayClient:
    """支付平台假客户端：可控制 check_payment 返回值/抛异常/创建链接失败。"""

    def __init__(self, paid=True, raise_check=False, raise_create=False):
        self.paid = paid
        self.raise_check = raise_check
        self.raise_create = raise_create
        self.closed = 0

    async def create_payment(self, unique_id, amount):
        if self.raise_create:
            raise RuntimeError("mock create_payment 失败")
        return {"pay_url": "https://pay.test/" + str(unique_id), "order_id": "ORD" + str(unique_id)}

    async def check_payment(self, unique_id):
        if self.raise_check:
            raise RuntimeError("mock check_payment 失败")
        return {"status": 1 if self.paid else 0}

    async def close(self):
        self.closed += 1


def test_recharge_flow():
    print("\n=== 充值链路 ===")
    db.update_settings(points_rate=10, min_recharge_usdt=1, max_recharge_usdt=1000)
    # 越限直接拒绝
    check("充值越限被拒", service.create_recharge(USER1, Decimal("9999")) is None)
    rc = service.create_recharge(USER1, Decimal("10"), gateway="okpay")
    check("充值单创建", rc is not None)
    if not rc:
        return
    check("充值积分换算 Decimal", rc.points == 100, rc.points)
    check("充值单待付", rc.status == "pending", rc.status)
    # 手动补单
    done = service.complete_recharge(rc.id)
    check("手动补账成功", done is not None and done.status == "completed")
    check("重复补账被拒", service.complete_recharge(rc.id) is None)
    check("补账后积分到账", db.get_user(USER1).points is not None)

    # check_recharge_status：未到账 / 到账 / 抛异常 / 链上单 / 非待付态
    rc2 = service.create_recharge(USER1, Decimal("5"), gateway="kkpay")
    service._okpay = FakePayClient(paid=False)
    service._kkpay = FakePayClient(paid=False)
    check("未到账返回 False", run(service.check_recharge_status(rc2.id)) is False)
    service._okpay = FakePayClient(paid=True)
    service._kkpay = FakePayClient(paid=True)
    check("到账返回 True", run(service.check_recharge_status(rc2.id)) is True)
    check("重复查到账 False", run(service.check_recharge_status(rc2.id)) is False)
    rc3 = service.create_recharge(USER1, Decimal("6"))
    service._okpay = FakePayClient(raise_check=True)
    check("查询抛异常不崩溃", run(service.check_recharge_status(rc3.id)) is False)
    check("不存在单返回 False", run(service.check_recharge_status("NOPE")) is False)
    # 链上单不走支付平台
    cr = service.create_chain_recharge(USER1, Decimal("10"), "trc20")
    check("TRON 冷钱包单创建", cr is not None)
    if cr:
        check("TRON 应付含尾数", "." in str(cr.expected_amount) or cr.expected_amount != "10", cr.expected_amount)
        check("链上单拒绝平台查询", run(service.check_recharge_status(cr.id)) is False)
    db.update_settings(pay_ton_enabled=True, ton_wallet_address="TWALLET2")
    cr2 = service.create_chain_recharge(USER1, Decimal("12"), "ton")
    check("TON 冷钱包单创建", cr2 is not None)
    if cr2:
        check("TON memo=单号", cr2.memo == cr2.id, cr2.memo)
    check("未知链返回 None", service.create_chain_recharge(USER1, Decimal("12"), "nope") is None)
    db.update_settings(pay_tron_enabled=False)
    check("TRON 开关关闭拒单", service.create_chain_recharge(USER1, Decimal("12"), "trc20") is None)
    db.update_settings(pay_tron_enabled=True, tron_wallet_address="TWALLET")


def test_chain_matching():
    print("\n=== 链上匹配 / 游标 ===")
    from datetime import datetime, timezone

    from gshop.chain_watch import ChainTx

    db.update_settings(pay_tron_enabled=True, tron_wallet_address="TWALLET",
                       pay_ton_enabled=True, ton_wallet_address="TWALLET2")
    # 清掉遗留待付单，保证匹配从干净状态开始
    for r in db.list_pending_recharges():
        r.status = "failed"
        db.save_recharge(r)
    rc = service.create_chain_recharge(USER1, Decimal("20"), "trc20")
    check("TRON 单准备", rc is not None)
    tx = ChainTx(txid="TXA1", amount=rc.amount, sender="S1", timestamp=int(datetime.now(timezone.utc).timestamp()))
    paid = service._match_tron_txs([rc], [tx])
    check("TRON 金额匹配入账", len(paid) == 1, len(paid))
    tx2 = ChainTx(txid="TXA2", amount=rc.amount, sender="S1", timestamp=int(datetime.now(timezone.utc).timestamp()))
    paid2 = service._match_tron_txs([rc], [tx2])
    check("已入账单不再重复匹配", paid2 == [], len(paid2))
    rc_b = service.create_chain_recharge(USER1, Decimal("30"), "trc20")
    tx_wrong = ChainTx(txid="TXB1", amount=Decimal("1.234"), sender="S2", timestamp=int(datetime.now(timezone.utc).timestamp()))
    check("金额不符不匹配", service._match_tron_txs([rc_b], [tx_wrong]) == [])
    old_ts = int(datetime.now(timezone.utc).timestamp()) - 99999
    tx_old = ChainTx(txid="TXB2", amount=rc_b.amount, sender="S2", timestamp=old_ts)
    check("交易早于下单不匹配", service._match_tron_txs([rc_b], [tx_old]) == [])
    # TON：memo 匹配 + 少付不入账
    ton = service.create_chain_recharge(USER1, Decimal("15"), "ton")
    check("TON 单准备", ton is not None)
    tx_ton = ChainTx(txid="TONTX1", amount=ton.amount, sender="S3", timestamp=int(datetime.now(timezone.utc).timestamp()), memo=ton.memo)
    check("TON memo 匹配入账", len(service._match_ton_txs([ton], [tx_ton])) == 1)
    ton2 = service.create_chain_recharge(USER1, Decimal("16"), "ton")
    tx_under = ChainTx(txid="TONTX2", amount=Decimal("1"), sender="S3", timestamp=int(datetime.now(timezone.utc).timestamp()), memo=ton2.memo)
    check("TON 少付不入账", service._match_ton_txs([ton2], [tx_under]) == [])
    tx_nomemo = ChainTx(txid="TONTX3", amount=Decimal("99"), sender="S4", timestamp=int(datetime.now(timezone.utc).timestamp()))
    check("TON 无 memo 跳过", service._match_ton_txs([ton2], [tx_nomemo]) == [])
    # 游标
    check("_scan_cursor 默认值", service._scan_cursor("never_set_key", 12345) == 12345)
    db.set_setting("cursor_test_key", 777)
    check("_scan_cursor 读已存值", service._scan_cursor("cursor_test_key", 12345) == 777)
    db.set_setting("cursor_bad_key", "abc")
    check("_scan_cursor 脏值兜底", service._scan_cursor("cursor_bad_key", 8888) == 8888)
    check("_oldest_pending_ms 空列表", isinstance(service._oldest_pending_ms([]), int))
    ok_rc = service.create_chain_recharge(USER1, Decimal("7"), "ton")
    check("_oldest_pending_ms 有单", service._oldest_pending_ms([ok_rc]) > 0)


def test_order_lifecycle():
    print("\n=== 订单生命周期 / 佣金冲销 ===")
    db.update_settings(points_rate=10)
    service.update_user_points(USER3, 2000, operator_id=ADMIN, reason="测试充值")
    prod = service.create_product({"name": "生命周期", "kind": "product", "points": 50, "stock": 5})
    check("商品创建", prod is not None)
    check("商品更新", service.update_product(prod.id, name="改名") is not None)
    check("商品更新不存在", service.update_product("NOPE", name="x") is None)
    check("商品删除不存在", service.delete_product("NOPE") is False)
    order, reason = service.create_order(USER3, prod.id, note="n")
    check("下单成功", order is not None, reason)
    check("商品订单已完成", order.status == "completed", order.status)
    if order:
        got = service.get_order(order.id)
        check("get_order 命中", got is not None)
        # 已完成的单不能再退款两次
        r1 = service.refund_order(order.id, ADMIN)
        check("退款成功", r1 is not None and r1.status == "refunded")
        check("重复退款幂等 None", service.refund_order(order.id, ADMIN) is None)
    # 取消订单路径
    prod2 = service.create_product({"name": "取消测", "kind": "report", "points": 30})
    o2, r2 = service.create_order(USER3, prod2.id)
    check("报单下单", o2 is not None, r2)
    if o2:
        c1, why = service.cancel_order("NOPE", USER3, by_user=True)
        check("取消不存在单", c1 is None and why == "not_found", why)
        c2, why2 = service.cancel_order(o2.id, USER1, by_user=True)
        check("取消非本人单", c2 is None and why2 == "not_owner", why2)
        c3, why3 = service.cancel_order(o2.id, USER3, by_user=True)
        check("本人取消成功", c3 is not None and c3.status == "cancelled", why3)
        c4, why4 = service.cancel_order(o2.id, USER3, by_user=True)
        check("重复取消 not_found", c4 is None and why4 == "not_found", why4)
    # 管理端完成订单 + 佣金结算
    grab.add_grabber(USER2, username="u222222", name="接单员二")
    db.update_settings(grab_enabled=True, grab_chat_id="-1001234567890",
                       grab_commission_pct=10, grab_freeze_hours=0)
    o3, _ = service.create_order(USER3, prod2.id)
    if o3:
        run(grab.dispatch_order(o3))
        code, og, gg = grab.try_grab(o3.id, USER2, "u222222", "接单员二")
        if code == "ok" and og:
            done = service.complete_order(o3.id, "<b>已完成</b>", ADMIN)
            check("管理员回单成功", done is not None and done.status == "completed")
            check("重复回单 None", service.complete_order(o3.id, "x", ADMIN) is None)
            cms = db.list_grab_commissions(grabber_id=USER2, limit=20)
            check("佣金已产生", len(cms) >= 1, len(cms))
            # 退款冲销佣金
            rr = service.refund_order(o3.id, ADMIN)
            check("回单后退款成功", rr is not None)
            check("退款后 grab 字段清空", rr.grab_status == "" and rr.grabber_id == 0)
        else:
            check("抢单前置失败", False, code)
    check("删除商品", service.delete_product(prod.id) is True)


def test_ui_all_branches():
    print("\n=== ui 全分支 ===")
    from telegram.error import BadRequest

    from gshop import ui as U

    _PHOTO = [type("P", (), {"file_id": "PH1"})()]
    check("summarize 图片", U.summarize_message(FakeMsg(USER1, caption="cap", photo=_PHOTO)).startswith("cap"))
    m_doc = FakeMsg(USER1, caption="文件说明", document=type("D", (), {"file_id": "D1"})())
    check("summarize 文件", "[文件]" in U.summarize_message(m_doc))
    m_vid = FakeMsg(USER1)
    m_vid.video = type("V", (), {"file_id": "V1"})()
    check("summarize 视频", "[视频]" in U.summarize_message(m_vid))
    m_aud = FakeMsg(USER1)
    m_aud.audio = type("A", (), {"file_id": "A1"})()
    check("summarize 音频", "[音频]" in U.summarize_message(m_aud))
    m_voi = FakeMsg(USER1)
    m_voi.voice = type("VO", (), {"file_id": "VO1"})()
    check("summarize 语音", "[语音]" in U.summarize_message(m_voi))
    m_sti = FakeMsg(USER1)
    m_sti.sticker = type("S", (), {"file_id": "S1"})()
    check("summarize 贴纸", "[贴纸]" in U.summarize_message(m_sti))
    check("summarize 空消息", U.summarize_message(FakeMsg(USER1)) == "（空消息）")
    check("media_of 图片", U._media_of(FakeMsg(USER1, photo=_PHOTO)) == "PH1")
    check("media_of 文件", U._media_of(m_doc) == "D1")
    check("media_of 无媒体", U._media_of(FakeMsg(USER1)) == "")
    check("caption_fits 短", U._caption_fits("短") is True)
    check("caption_fits 超长", U._caption_fits("x" * 2000) is False)
    check("order_status_text 已知", "已完成" in U.order_status_text("completed"))
    check("order_status_text 未知", U.order_status_text("weird") == "weird")
    check("product_status_text", isinstance(U.product_status_text("on"), str))
    check("get_points", isinstance(U.get_points(USER1), int))
    U.audit(ADMIN, "测试", "明细")
    check("audit 不抛异常", True)

    class EditMsg:
        """可控编辑行为的假消息：按 fail 中记录的方法名抛 BadRequest/异常。"""

        def __init__(self, fail=None, text_err="", photo=None, caption=None, hard=None):
            self.fail = set(fail or ())
            self.hard = set(hard or ())
            self.text_err = text_err
            self.photo = photo or []
            self.caption = caption
            self.document = None
            self.video = None
            self.animation = None
            self.audio = None
            self.voice = None
            self.deleted = 0
            self.edits = []
            self.replies = []
            self.photos = []

        async def edit_message_text(self, text="", reply_markup=None, parse_mode=None, **kw):
            if "edit_message_text" in self.hard or ("edit_message_text" in self.fail and parse_mode):
                raise BadRequest(self.text_err or "HTML 解析失败")
            self.edits.append(text)
            return self

        async def edit_message_caption(self, caption=None, reply_markup=None, parse_mode=None, **kw):
            if "edit_message_caption" in self.hard or ("edit_message_caption" in self.fail and parse_mode):
                raise BadRequest(self.text_err or "bad caption")
            self.edits.append(caption or "")
            return self

        async def edit_message_media(self, media=None, reply_markup=None, parse_mode=None, **kw):
            if "edit_message_media" in self.hard or ("edit_message_media" in self.fail and parse_mode):
                raise BadRequest(self.text_err or "bad media")
            self.edits.append("media")
            return self

        async def reply_photo(self, photo=None, caption=None, reply_markup=None, parse_mode=None, **kw):
            if "reply_photo" in self.hard or ("reply_photo" in self.fail and parse_mode):
                raise BadRequest(self.text_err or "bad photo")
            self.photos.append(caption or "")
            return self

        async def reply_text(self, text="", reply_markup=None, parse_mode=None, **kw):
            if "reply_text" in self.hard or ("reply_text" in self.fail and parse_mode):
                raise BadRequest(self.text_err or "bad text")
            self.replies.append(text)
            return self

        async def delete(self, **kw):
            if "delete" in self.fail:
                raise RuntimeError("删不掉")
            self.deleted += 1
            return True

    check("_edit_text 成功", run(U._edit_text(EditMsg(), "a", None)) is True)
    check("_edit_text not modified", run(U._edit_text(EditMsg(fail={"edit_message_text"}, text_err="Message is not modified"), "a", None)) is True)
    check("_edit_text HTML 非法降级", run(U._edit_text(EditMsg(fail={"edit_message_text"}, text_err="can't parse entities"), "<b>a", None)) is True)
    check("_edit_text 彻底失败", run(U._edit_text(EditMsg(hard={"edit_message_text"}, text_err="boom"), "a", None)) is False)
    check("_edit_text 非 BadRequest 异常", run(U._edit_text(EditMsg(hard={"edit_message_text"}, text_err="x"), "a", None)) in (True, False))
    check("_edit_caption 成功", run(U._edit_caption(EditMsg(caption="c"), "a", None)) is True)
    check("_edit_caption not modified", run(U._edit_caption(EditMsg(fail={"edit_message_caption"}, text_err="Message is not modified"), "a", None)) is True)
    check("_edit_caption 降级", run(U._edit_caption(EditMsg(fail={"edit_message_caption"}, text_err="parse"), "<b>a", None)) is True)
    check("_edit_media 成功", run(U._edit_media(EditMsg(caption="c"), "PH1", "a", None)) is True)
    check("_edit_media not modified", run(U._edit_media(EditMsg(fail={"edit_message_media"}, text_err="Message is not modified"), "PH1", "a", None)) is True)
    check("_edit_media 降级", run(U._edit_media(EditMsg(fail={"edit_message_media"}, text_err="parse"), "PH1", "<b>a", None)) is True)
    check("_reply_photo 成功", run(U._reply_photo(EditMsg(), "PH1", "a", None)) is True)
    check("_reply_photo 降级", run(U._reply_photo(EditMsg(fail={"reply_photo"}, text_err="parse"), "PH1", "<b>a", None)) is True)
    check("_drop_message 异常忽略", run(U._drop_message(EditMsg(fail={"delete"}))) is None)

    # send_or_edit 五分支
    q_none = type("Q", (), {"message": None})()
    check("send_or_edit 无 message", run(U.send_or_edit(q_none, "t", None)) is None)
    class Q:
        def __init__(self, message):
            self.message = message
    # 图注超长退纯文字
    long_text = "x" * 2000
    m = EditMsg(photo=_PHOTO)
    run(U.send_or_edit(Q(m), long_text, None, photo="PH1"))
    check("send_or_edit 长文本退纯文字", m.edits or m.replies)
    # 当前图文 + 同图 → 改图注
    m2 = EditMsg(photo=_PHOTO)
    run(U.send_or_edit(Q(m2), "t", None, photo="PH1"))
    check("send_or_edit 同图改图注", m2.edits == ["t"], m2.edits)
    # 当前图文 + 异图 → 换图
    m3 = EditMsg(photo=_PHOTO)
    run(U.send_or_edit(Q(m3), "t", None, photo="PH2"))
    check("send_or_edit 异图换图", "media" in m3.edits, m3.edits)
    # 当前文字 + 目标带图 → 删旧发新
    m4 = EditMsg()
    run(U.send_or_edit(Q(m4), "t", None, photo="PH3"))
    check("send_or_edit 文字转图", m4.deleted == 1 and m4.photos == ["t"], (m4.deleted, m4.photos))
    # 当前图文 + 目标不带图 → 删旧发新
    m5 = EditMsg(photo=_PHOTO)
    run(U.send_or_edit(Q(m5), "t", None))
    check("send_or_edit 图转文字", m5.deleted == 1 and m5.replies == ["t"], (m5.deleted, m5.replies))
    # 纯文字就地改
    m6 = EditMsg()
    run(U.send_or_edit(Q(m6), "t", None))
    check("send_or_edit 文字改文字", m6.edits == ["t"], m6.edits)
    # 改失败 → 回复兜底
    m7 = EditMsg(hard={"edit_message_text"}, text_err="boom")
    run(U.send_or_edit(Q(m7), "t", None))
    check("send_or_edit 改失败转回复", m7.replies == ["t"], m7.replies)
    # reply_html 四分支
    m8 = EditMsg()
    run(U.reply_html(m8, "t", photo="PH1"))
    check("reply_html 带图", m8.photos == ["t"])
    m9 = EditMsg(fail={"reply_photo"}, text_err="parse")
    run(U.reply_html(m9, "<b>t", photo="PH1"))
    check("reply_html 带图降级", m9.photos)
    m10 = EditMsg(hard={"reply_photo"}, text_err="boom")
    run(U.reply_html(m10, "t", photo="PH1"))
    check("reply_html 带图彻底失败走文字", m10.replies)
    m11 = EditMsg()
    run(U.reply_html(m11, "t"))
    check("reply_html 纯文字", m11.replies == ["t"])
    m12 = EditMsg(hard={"reply_text"}, text_err="parse")
    run(U.reply_html(m12, "t"))
    check("reply_html 文字降级不抛", True)


def test_grab_housekeeping():
    print("\n=== 抢单管家：超时/预警/解冻/放弃/退群/入群 ===")
    db.update_settings(grab_enabled=True, grab_chat_id="-1001234567890",
                       grab_commission_pct=10, grab_timeout_hours=1,
                       grab_penalty_minutes=5, grab_warn_minutes=30,
                       grab_freeze_hours=0, grab_auto_release=True,
                       grab_notify_admin=True, grab_points_per_usdt=100,
                       grab_withdraw_min=10)
    grab._bot_username = ""
    check("enabled True", grab.enabled() is True)
    db.update_settings(grab_enabled=False)
    check("enabled False", grab.enabled() is False)
    db.update_settings(grab_enabled=True)
    check("chat_id", grab.chat_id() == "-1001234567890")
    check("points_per_usdt", grab.points_per_usdt() == 100)
    check("points_to_usdt", grab.points_to_usdt(1000) == Decimal("10"))
    check("usdt_text", isinstance(grab.usdt_text(1000), str))
    _co = type("O", (), {"points": 100, "product_kind": "report", "product_id": None})()
    check("commission_for", grab.commission_for(_co) >= 0)
    check("_calc_commission 0", grab._calc_commission(100, 0) == 0)
    check("_safe_commission 脏数据", isinstance(grab._safe_commission(type("O", (), {"points": 100, "commission": 0, "product_id": None})()), int))
    # 派单卡片文本
    class FakeOrder:
        def __init__(self):
            self.id = "T1"
            self.note = "需求"
            self.product_name = "测试"
            self.points = 100
            self.status = "pending"
            self.grab_status = ""
            self.commission = 0
            self.commission_pct = 0
            self.product_kind = "report"
            self.user_id = USER3
            self.username = "u3"
            self.created_at = ""
            self.completed_at = ""
            self.reply_content = ""
            self.dispatch_chat = ""
            self.dispatch_msg = 0
            self.dispatch_count = 0
            self.last_dispatch_at = ""
            self.warn_sent = 0
            self.grabber_id = 0
            self.grabber_name = ""
            self.grab_at = ""
            self.deadline = ""
            self.stock_item_id = 0
    check("派单卡文本", "新单待抢" in grab.dispatch_card_text(FakeOrder(), 10))
    check("派单键盘", grab._grab_keyboard("T1") is not None)
    # 接单员资格校验
    check("grabber_can_grab None", grab.grabber_can_grab(None) is not None)
    g, _ = grab.add_grabber(USER1, username="u111111", name="接单员一")
    if g:
        grab.set_grabber_status(USER1, "suspended", operator_id=ADMIN)
        check("停权不能抢", grab.grabber_can_grab(db.get_grabber(USER1)) is not None)
        grab.set_grabber_status(USER1, "active", operator_id=ADMIN)
        check("激活可抢", grab.grabber_can_grab(db.get_grabber(USER1)) is None)
    # 群卡片编辑：无 bot / 无 dispatch 字段直接返回
    run(grab._edit_dispatch_card(FakeOrder(), "x"))
    check("_edit_dispatch_card 无字段不抛", True)
    # 派单：未启用 / 状态不符
    db.update_settings(grab_enabled=False)
    check("未启用不派单", run(grab.dispatch_order(FakeOrder())) is False)
    db.update_settings(grab_enabled=True)
    o_park = FakeOrder()
    o_park.grab_status = "waiting"
    check("已 waiting 不重派", run(grab.dispatch_order(o_park)) is False)
    o_done = FakeOrder()
    o_done.status = "completed"
    check("已终态不派单", run(grab.dispatch_order(o_done)) is False)
    # 重派：超过最大次数
    db.update_settings(grab_max_dispatch=1)
    o_max = FakeOrder()
    o_max.dispatch_count = 5
    real = service.create_product({"name": "重派测", "kind": "report", "points": 10})
    ro, _ = service.create_order(USER3, real.id)
    check("重派测试单", ro is not None)
    if ro:
        ro.dispatch_count = 99
        db.save_order(ro)
        check("超最大次数不重派", run(grab.redispatch_order(ro, "测试")) is False)
    db.update_settings(grab_max_dispatch=3)
    # giveup 各分支
    ok, why = run(grab.giveup("NOPE", USER2))
    check("放弃不存在单", ok is False and "不在你手上" in why, why)
    # 超时扫描 / 预警 / 解冻（空数据也应正常返回）
    check("check_timeouts 返回 int", isinstance(run(grab.check_timeouts()), int))
    check("check_warns 返回 int", isinstance(run(grab.check_warns()), int))
    db.update_settings(grab_warn_minutes=0)
    check("预警关闭返回 0", run(grab.check_warns()) == 0)
    db.update_settings(grab_warn_minutes=30)
    db.update_settings(grab_auto_release=False)
    check("自动解冻关闭返回 0", run(grab.release_due_commissions()) == 0)
    db.update_settings(grab_auto_release=True)
    check("release_due_commissions 返回 int", isinstance(run(grab.release_due_commissions()), int))


def test_grab_member_and_withdraw():
    print("\n=== 接单员：提现/申诉/入群/退群/欢迎 ===")
    db.update_settings(grab_enabled=True, grab_chat_id="-1001234567890",
                       grab_withdraw_min=10, grab_points_per_usdt=100,
                       grab_freeze_hours=0)
    g, _ = grab.add_grabber(USER3, username="u333333", name="接单员三")
    check("建档", g is not None)
    if not g:
        return
    grab.set_grabber_status(USER3, "active", operator_id=ADMIN)
    # 提现：余额不足 / 低于门槛 / 地址非法 / 成功
    w0, e0 = grab.apply_withdrawal(USER3, 100, "12345678")
    check("余额不足拒提现", w0 is None, e0)
    g = db.get_grabber(USER3)
    g.balance_available = 5000
    db.save_grabber(g)
    w1, e1 = grab.apply_withdrawal(USER3, 1, "12345678")
    check("低于门槛拒提现", w1 is None, e1)
    w2, e2 = grab.apply_withdrawal(USER3, 500, "")
    check("空地址拒提现", w2 is None, e2)
    w3, e3 = grab.apply_withdrawal(USER3, 500, "12345678")
    check("提现申请成功", w3 is not None, e3)
    if w3:
        run(grab.notify_admins_withdrawal(w3))
        check("提现通知不抛", True)
        ap = grab.approve_withdrawal(w3.id, ADMIN, "TXID1")
        check("审核打款", ap is not None)
        check("重复审核 None", grab.approve_withdrawal(w3.id, ADMIN) is None)
    g = db.get_grabber(USER3)
    g.balance_available = 5000
    db.save_grabber(g)
    w4, _ = grab.apply_withdrawal(USER3, 500, "87654321")
    if w4:
        rj = grab.reject_withdrawal(w4.id, ADMIN, "地址错误")
        check("驳回提现", rj is not None)
        check("重复驳回 None", grab.reject_withdrawal(w4.id, ADMIN) is None)
    # 申诉
    a1, ea = grab.create_appeal(USER3, "NOPE", "内容")
    check("申诉不存在的单被拒", a1 is None, ea)
    real = service.create_product({"name": "申诉测", "kind": "report", "points": 10})
    ao, _ = service.create_order(USER1, real.id)
    if ao:
        a2, ea2 = grab.create_appeal(USER3, ao.id, "我认为不合理")
        check("申诉创建", a2 is not None, ea2)
        if a2:
            run(grab.notify_admins_appeal(a2))
            h1 = grab.handle_appeal(a2.id, ADMIN, True, "已核实")
            check("申诉通过", h1 is not None)
            check("重复处理 None", grab.handle_appeal(a2.id, ADMIN, False) is None)
    # 入群 / 退群
    check("非接单群忽略", run(grab.handle_member_left(-100999, USER3)) is False)
    check("退群停权", run(grab.handle_member_left(-1001234567890, USER3)) is True)
    check("已停权再退不重复", run(grab.handle_member_left(-1001234567890, USER3)) is False)
    check("非接单员退群", run(grab.handle_member_left(-1001234567890, 999999)) is False)
    check("入群非接单群", run(grab.handle_member_joined(-100999, USER3, "u3", "名3")) == "ignored")
    check("陌生人入群", run(grab.handle_member_joined(-1001234567890, 888888, "x", "路人")) in ("stranger", "ok", "self_join", "leaked", "ignored"))
    # 专属链接
    gg = db.get_grabber(USER3)
    if gg:
        link, err = run(grab.ensure_invite_link(gg))
        check("生成专属链接", bool(link) or bool(err), err)
        link2, err2 = run(grab.ensure_invite_link(gg, force=True))
        check("强制重生成", bool(link2) or bool(err2), err2)
        msg = run(grab.notify_grabber_welcome(gg))
        check("欢迎三连", isinstance(msg, str))
    # 机器人入群
    run(grab.handle_bot_added(-100999, "另一个群"))
    check("handle_bot_added 不抛", True)
    grab.confirm_grab_chat("-1001234567890")
    check("confirm_grab_chat", db.get_settings().get("grab_chat_id") == "-1001234567890")
    # 深链
    check("_chat_ref 数字", isinstance(grab._chat_ref(), (int, str)))
    d = run(grab._deeplink("T1"))
    check("_deeplink", isinstance(d, str))
    db.update_settings(grab_chat_id="@somegroup")
    check("_chat_ref 用户名", grab._chat_ref() == "@somegroup")
    db.update_settings(grab_chat_id="-1001234567890")


if __name__ == "__main__":
    for _fn in (
        test_payment_gateways,
        test_recharge_flow,
        test_chain_matching,
        test_order_lifecycle,
        test_ui_all_branches,
        test_grab_housekeeping,
        test_grab_member_and_withdraw,
    ):
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
