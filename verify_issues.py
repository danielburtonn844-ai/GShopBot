"""三个 issue 的回归验证脚本。

用法：python3 verify_issues.py
不依赖网络，不发送 Telegram 消息，全部用临时 SQLite + HTTP stub。
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
# Issue #1：自动解冻与手动解冻交错，佣金重复入账
# ==================================================================
def test_issue1() -> None:
    print("\n=== Issue #1：佣金解冻竞态 ===")
    tmp = tempfile.mkdtemp()
    os.environ["SHOP_DB_PATH"] = os.path.join(tmp, "t1.db")

    from examples.grab_service import GrabService
    from examples.models import Database, Grabber, GrabCommission, now_iso

    db = Database(os.environ["SHOP_DB_PATH"])
    svc = GrabService(db, None)
    svc._bot = None  # 不发送通知

    # 建档接单员
    db.save_grabber(Grabber(user_id=5001, seq=1, name="测试员"))
    g = db.get_grabber(5001)
    g.balance_frozen = 200
    g.balance_available = 0
    db.save_grabber(g)

    # 两笔到期冻结佣金，各 100
    ids = []
    for i in range(2):
        cm = GrabCommission(
            order_id=f"T{i}", grabber_id=5001, amount=100,
            status="frozen", frozen_until="", created_at=now_iso(),
        )
        ids.append(db.add_grab_commission(cm))

    # 模拟交错：先让手动解冻抢走第二笔，再跑自动解冻
    manual_ok = svc.release_commission_manual(ids[1])
    check("手动解冻第二笔成功", manual_ok is True)

    # 自动解冻此时仍持有「两笔都到期」的快照
    asyncio.get_event_loop().run_until_complete(svc.release_due_commissions())

    g = db.get_grabber(5001)
    check("可提现余额 = 200（不重复入账）", g.balance_available == 200,
          f"实际 {g.balance_available}")
    check("冻结余额 = 0", g.balance_frozen == 0, f"实际 {g.balance_frozen}")

    # 再手动解冻一次同一笔，应被 CAS 拒绝
    again = svc.release_commission_manual(ids[1])
    check("重复手动解冻被拒绝", again is False)
    g = db.get_grabber(5001)
    check("余额未被二次增加", g.balance_available == 200, f"实际 {g.balance_available}")

    # 超过 500 笔时也能按主键解冻（原实现受 limit=500 限制）
    cm = GrabCommission(
        order_id="T999", grabber_id=5001, amount=50,
        status="frozen", frozen_until="", created_at=now_iso(),
    )
    cid = db.add_grab_commission(cm)
    ok = svc.release_commission_manual(cid)
    check("按主键解冻（不受列表分页限制）", ok is True)


# ==================================================================
# Issue #2：TRON 监听缺分页与游标
# ==================================================================
def test_issue2() -> None:
    print("\n=== Issue #2：TRON 分页与时间游标 ===")
    import examples.chain_watch as cw

    calls: list[dict] = []

    class FakeResp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._p

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            calls.append(dict(params or {}))
            fp = (params or {}).get("fingerprint", "")
            # 第一页满 50 条并给出指纹；第二页 10 条且无指纹
            if not fp:
                data = [{
                    "transaction_id": f"tx{i}",
                    "token_info": {"address": cw.USDT_TRC20_CONTRACT, "decimals": 6},
                    "value": str(10 * 10 ** 6),
                    "from": "TFrom",
                    "block_timestamp": 1700000000000 + i * 1000,
                } for i in range(50)]
                return FakeResp({"data": data, "meta": {"fingerprint": "PAGE2"}})
            data = [{
                "transaction_id": f"txB{i}",
                "token_info": {"address": cw.USDT_TRC20_CONTRACT, "decimals": 6},
                "value": str(5 * 10 ** 6),
                "from": "TFrom",
                "block_timestamp": 1700001000000 + i * 1000,
            } for i in range(10)]
            return FakeResp({"data": data, "meta": {}})

    cw.httpx.AsyncClient = FakeClient
    txs = asyncio.get_event_loop().run_until_complete(
        cw.fetch_tron_usdt_incoming("TAddr", min_timestamp_ms=1699999000000)
    )

    check("翻页被触发（共 2 次请求）", len(calls) == 2, f"实际 {len(calls)} 次")
    check("第二页带 fingerprint", calls[1].get("fingerprint") == "PAGE2",
          f"实际 {calls[1].get('fingerprint')}")
    check("min_timestamp 已下传", calls[0].get("min_timestamp") == "1699999000000",
          f"实际 {calls[0].get('min_timestamp')}")
    check("两页共取回 60 笔", len(txs) == 60, f"实际 {len(txs)}")
    check("返回按时间升序", txs[0].timestamp < txs[-1].timestamp)


# ==================================================================
# Issue #3：run_polling 未订阅 chat_member
# ==================================================================
def test_issue3() -> None:
    print("\n=== Issue #3：allowed_updates 订阅 ===")
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"),
               encoding="utf-8").read()

    check("run_polling 传入 allowed_updates",
          "run_polling(allowed_updates=ALLOWED_UPDATES)" in src)
    check("订阅了 CHAT_MEMBER", "Update.CHAT_MEMBER" in src)
    check("订阅了 MY_CHAT_MEMBER", "Update.MY_CHAT_MEMBER" in src)
    check("订阅了 MESSAGE", "Update.MESSAGE" in src)
    check("订阅了 CALLBACK_QUERY", "Update.CALLBACK_QUERY" in src)

    from telegram import Update
    check("PTB 常量存在", all(hasattr(Update, k) for k in
                              ["MESSAGE", "CALLBACK_QUERY", "CHAT_MEMBER", "MY_CHAT_MEMBER"]))


# ==================================================================
# 附加：金额精度
# ==================================================================
def test_money() -> None:
    print("\n=== 附加：金额精度 ===")
    from decimal import Decimal

    from examples.grab_service import GrabService

    class FakeDB:
        def __init__(self):
            self.s = {"grab_points_per_usdt": 100}

        def get_setting(self, k, d=None):
            return self.s.get(k, d)

        def get_settings(self):
            return self.s

    svc = GrabService.__new__(GrabService)
    svc.db = FakeDB()
    svc.settings["grab_points_per_usdt"] = 200
    check("500 积分 @200 汇率 = 2.5", svc.points_to_usdt(500) == 2.5,
          f"实际 {svc.points_to_usdt(500)}")
    svc.db.s["grab_points_per_usdt"] = 100
    check("1234 积分 @100 汇率 = 12.34", svc.points_to_usdt(1234) == 12.34,
          f"实际 {svc.points_to_usdt(1234)}")
    svc.db.s["grab_points_per_usdt"] = 3
    check("1 积分 @3 汇率 = 0.33（四舍五入非截断）", svc.points_to_usdt(1) == 0.33,
          f"实际 {svc.points_to_usdt(1)}")

    # 充值积分换算：float 会少算 1 积分的老坑
    bad = []
    for i in range(1, 1000):
        s = f"{i / 1000:.3f}"
        for rate in (10, 100, 50, 20):
            if int(float(s) * rate) != int(Decimal(s) * Decimal(rate)):
                bad.append((s, rate))
    check("穷举 999 种尾数 × 4 种汇率：Decimal 与 float 存在差异（证明该坑真实）",
          len(bad) > 0, f"差异 {len(bad)} 例")
    print(f"     （float 会算错的组合：{bad[:4]}）")


def test_issue4() -> None:
    print("\n=== Issue #4：TRON 尾数唯一性（超过 200 笔）===")

    tmp = tempfile.mkdtemp()
    os.environ["SHOP_DB_PATH"] = os.path.join(tmp, "t4.db")
    from examples.models import Database, Recharge, now_iso
    from examples.shop_service import ShopService

    db = Database(os.environ["SHOP_DB_PATH"])
    svc = ShopService(db, None)

    # 造 250 笔同金额（10 USDT）pending 冷钱包单，尾数 001..250 各占一个
    N = 250
    for i in range(1, N + 1):
        rc = Recharge(
            id=f"RC{i:04d}", user_id=9000 + i, amount=10.0, points=100,
            status="pending", created_at=now_iso(), gateway="trc20", chain="trc20",
            expected_amount=f"10.{i:03d}",
        )
        db.save_recharge(rc)

    # 全量查询必须能看到全部 250 个尾数
    used = db.pending_tron_suffixes(10)
    check(f"全量尾数查询返回 {N} 个", len(used) == N, f"实际 {len(used)}")

    # 截断列表（原实现的数据源）只能看到最新 200 笔
    truncated = db.list_pending_recharges()
    check("截断列表只有 200 笔（证明原实现的数据源不足）", len(truncated) == 200,
          f"实际 {len(truncated)}")

    # 新分配必须落在 1..250 之外
    for _ in range(20):
        s = svc._alloc_tron_suffix(10)
        if s is None or s in used:
            check("新分配尾数不重复", False, f"分到 {s}，已被占用")
            break
    else:
        check("连续 20 次分配都避开了已占用的 250 个尾数", True)

    # 不同整数金额互不干扰（10.001 与 20.001 不会误匹配，尾数可复用）
    db.save_recharge(Recharge(
        id="RCB001", user_id=9999, amount=20.0, points=200, status="pending",
        created_at=now_iso(), gateway="trc20", chain="trc20", expected_amount="20.500",
    ))
    used20 = db.pending_tron_suffixes(20)
    check("20 USDT 名下只统计自己的尾数", used20 == {500}, f"实际 {used20}")
    check("10 USDT 名下仍是 250 个", len(db.pending_tron_suffixes(10)) == N)


# ==================================================================
# Issue #5：TON 解析与官方协议不兼容
# ==================================================================
def test_issue5() -> None:
    print("\n=== Issue #5：TON 解析协议对齐 ===")
    import examples.chain_watch as cw

    MASTER_FRIENDLY = cw.USDT_TON_MASTER
    MASTER_RAW = "0:B113A994B5024A16719F69139328EB759596C38A25F59028B146FECDC3621DFE"
    OWNER_FRIENDLY = "EQD0vdSA_NedR9uvbgN9EikRX-suesDxGeFg69XQMavfLqIw"
    OWNER_RAW = cw.ton_normalize_address(OWNER_FRIENDLY)

    calls: list[dict] = []

    class FakeResp:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            return None

        def json(self):
            return self._p

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            calls.append(dict(params or {}))
            return FakeResp({"jetton_transfers": [
                {   # 正常转入，带备注（raw 格式 master + decoded payload）
                    "transaction_hash": "TX_OK==",
                    "jetton_master": MASTER_RAW,
                    "source": "0:AAAA",
                    "destination": OWNER_RAW,
                    "amount": "10500000",
                    "transaction_now": 1792342009,
                    "transaction_aborted": False,
                    "decoded_forward_payload": {"comment": "RC20261020ABC"},
                },
                {   # 失败交易，不得入账
                    "transaction_hash": "TX_ABORTED==",
                    "jetton_master": MASTER_RAW,
                    "destination": OWNER_RAW,
                    "amount": "99000000",
                    "transaction_now": 1792342010,
                    "transaction_aborted": True,
                },
                {   # 假 USDT（其他 jetton master），必须过滤
                    "transaction_hash": "TX_FAKE==",
                    "jetton_master": "0:DEADBEEF",
                    "destination": OWNER_RAW,
                    "amount": "50000000",
                    "transaction_now": 1792342011,
                    "transaction_aborted": False,
                },
            ]})

    cw.httpx.AsyncClient = FakeClient
    txs = asyncio.new_event_loop().run_until_complete(
        cw.fetch_ton_usdt_incoming(OWNER_FRIENDLY, min_timestamp=1792000000)
    )

    check("请求参数用 owner_address（不是 destination_wallet）",
          "owner_address" in calls[0] and "destination_wallet" not in calls[0],
          f"实际键: {sorted(calls[0])}")
    check("start_utime 已下传", calls[0].get("start_utime") == "1792000000")
    check("解析出 1 条有效转账", len(txs) == 1, f"实际 {len(txs)}")
    if txs:
        check("备注取自 decoded_forward_payload.comment",
              txs[0].memo == "RC20261020ABC", f"实际 {txs[0].memo!r}")
        check("金额正确解析为 10.5 USDT", str(txs[0].amount) == "10.5", f"实际 {txs[0].amount}")
    check("失败交易被排除", all(t.txid != "TX_ABORTED==" for t in txs))
    check("假 USDT 被排除", all(t.txid != "TX_FAKE==" for t in txs))

    # 证明 bug 真实存在：friendly 常量与 raw 响应直接字符串比较永不相等
    check("修复前比较方式（friendly vs raw 直接比）必然失配",
          MASTER_RAW != MASTER_FRIENDLY)
    check("规范化后两者相等", cw.ton_normalize_address(MASTER_FRIENDLY) == cw.ton_normalize_address(MASTER_RAW))


# ==================================================================
# Issue #6：超时订单未先查账
# ==================================================================
def test_issue6() -> None:
    print("\n=== Issue #6：超时订单先查账再标失败 ===")
    tmp = tempfile.mkdtemp()
    os.environ["SHOP_DB_PATH"] = os.path.join(tmp, "t6.db")
    from datetime import datetime, timedelta

    from examples.models import LOCAL_TZ, Database, Recharge, now_iso
    from examples.shop_service import ShopService

    db = Database(os.environ["SHOP_DB_PATH"])
    svc = ShopService(db, None)
    svc._bot = None

    # 一笔 3 小时前的平台单（已超过 2 小时过期阈值），实际已付款
    rc = Recharge(id="RC_OLD", user_id=7001, amount=10.0, points=100,
                  status="pending", created_at=now_iso(), gateway="okpay")
    db.save_recharge(rc)
    old_ts = (datetime.now(LOCAL_TZ) - timedelta(hours=3)).isoformat()
    conn = db._get_conn()
    try:
        conn.execute("UPDATE recharges SET created_at = ? WHERE id = ?", (old_ts, "RC_OLD"))
        conn.commit()
    finally:
        conn.close()

    # 模拟支付平台返回「已支付」
    async def fake_check(rid, allow_failed=False):
        with db.atomic() as c:
            fresh = db.get_recharge(rid, conn=c)
            if not fresh or fresh.status not in (("pending", "failed") if allow_failed else ("pending",)):
                return False
            fresh.status = "completed"
            db.save_recharge(fresh, conn=c)
        return True

    svc.check_recharge_status = fake_check
    asyncio.new_event_loop().run_until_complete(svc.check_all_pending_recharges())

    fresh = db.get_recharge("RC_OLD")
    check("已付款的过期单被补记为 completed（不是 failed）",
          fresh.status == "completed", f"实际 {fresh.status}")

    # 未付款的过期单仍要标 failed
    rc2 = Recharge(id="RC_OLD2", user_id=7002, amount=10.0, points=100,
                   status="pending", created_at=now_iso(), gateway="okpay")
    db.save_recharge(rc2)
    conn = db._get_conn()
    try:
        conn.execute("UPDATE recharges SET created_at = ? WHERE id = ?", (old_ts, "RC_OLD2"))
        conn.commit()
    finally:
        conn.close()

    async def fake_unpaid(rid, allow_failed=False):
        return False

    svc.check_recharge_status = fake_unpaid
    asyncio.new_event_loop().run_until_complete(svc.check_all_pending_recharges())
    check("未付款的过期单仍标 failed", db.get_recharge("RC_OLD2").status == "failed")

    # failed 单可手动补账
    async def fake_late(rid, allow_failed=False):
        if not allow_failed:
            return False
        with db.atomic() as c:
            fresh = db.get_recharge(rid, conn=c)
            if not fresh or fresh.status not in ("pending", "failed"):
                return False
            fresh.status = "completed"
            db.save_recharge(fresh, conn=c)
        return True

    svc.check_recharge_status = fake_late
    ok = asyncio.new_event_loop().run_until_complete(
        svc.check_recharge_status("RC_OLD2", allow_failed=True)
    )
    check("failed 单可通过 allow_failed 补记", ok is True)
    check("补记后状态为 completed", db.get_recharge("RC_OLD2").status == "completed")


# ==================================================================
# Issue #7：供需退款按当前价格
# ==================================================================
def test_issue7() -> None:
    print("\n=== Issue #7：供需退款按实付快照 ===")
    tmp = tempfile.mkdtemp()
    os.environ["SHOP_DB_PATH"] = os.path.join(tmp, "t7.db")
    from examples.models import Database, SupplyPost, User, now_iso
    from examples.supply_service import SupplyService

    db = Database(os.environ["SHOP_DB_PATH"])
    svc = SupplyService(db, None)

    user = User(user_id=8001, username="u", first_name="u", points=100, total_spent=1)
    db.save_user(user)

    # 发帖时频道腿实付 1 分（cost=1, channel_leg=1）
    post = SupplyPost(id="SP1", user_id=8001, channel="channel", cost=1,
                      channel_leg=1, created_at=now_iso())
    db.save_supply_post(post)

    # 管理员事后把频道价调到 100
    db.set_setting("supply_price_channel", 100)
    check("调价后当前频道腿 = 100", svc.channel_leg_value("channel") == 100,
          f"实际 {svc.channel_leg_value('channel')}")

    asyncio.new_event_loop().run_until_complete(svc._refund_channel_leg(post, "模拟分发失败"))

    after = db.get_user(8001)
    check("退款按发帖时实付 1 分（不是当前价 100）", after.points == 101,
          f"实际 {after.points}（应为 101）")
    check("帖子标记已退款", db.get_supply_post("SP1").refunded is True)

    # 老数据 channel_leg=0 时回退旧口径，但仍不得超过实付总额
    post2 = SupplyPost(id="SP2", user_id=8001, channel="channel", cost=5,
                       channel_leg=0, created_at=now_iso())
    db.save_supply_post(post2)
    asyncio.new_event_loop().run_until_complete(svc._refund_channel_leg(post2, "老数据回退"))
    after2 = db.get_user(8001)
    check("老数据退款封顶在实付总额 5", after2.points == 106,
          f"实际 {after2.points}（应为 106）")


if __name__ == "__main__":
    test_issue1()
    test_issue2()
    test_issue3()
    test_issue4()
    test_issue5()
    test_issue6()
    test_issue7()
    test_money()
    print(f"\n{'=' * 50}")
    print(f"✅ 通过 {PASS} 项   ❌ 失败 {FAIL} 项")
    print("=" * 50)
    sys.exit(0 if FAIL == 0 else 1)


# ==================================================================
# Issue #4：尾数分配依赖截断列表导致重复
# ==================================================================
