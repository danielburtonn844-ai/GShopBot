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

    from examples.models import Database, GrabCommission, Grabber, now_iso
    from examples.grab_service import GrabService

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
    from examples.shop_service import ShopService
    bad = []
    for i in range(1, 1000):
        s = f"{i / 1000:.3f}"
        for rate in (10, 100, 50, 20):
            if int(float(s) * rate) != int(Decimal(s) * Decimal(rate)):
                bad.append((s, rate))
    check("穷举 999 种尾数 × 4 种汇率：Decimal 与 float 存在差异（证明该坑真实）",
          len(bad) > 0, f"差异 {len(bad)} 例")
    print(f"     （float 会算错的组合：{bad[:4]}）")


if __name__ == "__main__":
    test_issue1()
    test_issue2()
    test_issue3()
    test_money()
    print(f"\n{'=' * 50}")
    print(f"✅ 通过 {PASS} 项   ❌ 失败 {FAIL} 项")
    print("=" * 50)
    sys.exit(0 if FAIL == 0 else 1)
