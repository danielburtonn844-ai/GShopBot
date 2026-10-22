"""并发压测：验证资金路径在真实竞争下是否原子。

与单元测试的区别
----------------
单元测试是「一个协程里顺序执行」，验证不了竞争。这里用**多线程 + 独立连接**
打同一个 SQLite 文件，制造真实的写锁竞争。

测五件事
--------
1. 100 人同时抢同一单，只能 1 人成功
2. 自动解冻与手动解冻并发，同一笔佣金不重复入账
3. 200 笔并发下单，TRON 尾数不重复（同金额）
4. 提现「通过」与「拒绝」并发，只生效一个
5. 吞吐：统计写操作用时与 database is locked 次数

用法：python3 bench_concurrency.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_ID", "100000000")

PASS = 0
FAIL = 0
LOCK_ERRORS = 0
_lock = threading.Lock()


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    with _lock:
        if cond:
            PASS += 1
            print(f"  ✅ {name}")
        else:
            FAIL += 1
            print(f"  ❌ {name}  {detail}")


def fresh_db(tag: str):
    from examples.db import Database

    tmp = tempfile.mkdtemp(prefix=f"bench_{tag}_")
    path = os.path.join(tmp, "shop.db")
    return Database(path), path


# ==================================================================
# 1. 抢单：100 线程抢同一单
# ==================================================================
def bench_grab() -> None:
    print("\n=== 1. 抢单原子性：100 线程抢同一单 ===")
    db, path = fresh_db("grab")
    from examples.models import Grabber, Order, now_iso

    for i in range(1, 101):
        db.save_grabber(Grabber(user_id=8000 + i, seq=i, name=f"接单员{i}", status="active"))
    db.save_order(Order(id="RACE-001", user_id=6001, product_id="P1", product_name="抢单测试",
                        points=100, status="pending", grab_status="waiting",
                        created_at=now_iso()))

    winners: list[int] = []
    lock = threading.Lock()

    def worker(gid: int) -> None:
        d = db  # 复用同一实例：连接按线程自动隔离
        try:
            ok = d.grab_order_atomic("RACE-001", gid, f"接单员{gid}", 20,
                                     now_iso(), now_iso())
            if ok:
                with lock:
                    winners.append(gid)
        finally:
            d2 = getattr(d, "path", None)
            _ = d2

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=100) as pool:
        list(pool.map(worker, range(8001, 8101)))
    elapsed = time.time() - t0

    check(f"100 线程抢单：唯一成功者 = {winners}", len(winners) == 1,
          f"实际成功 {len(winners)} 人：{winners[:5]}")
    row = db.get_order("RACE-001")
    check("库中 grab_status = grabbed", row.grab_status == "grabbed", row.grab_status)
    check("库中 grabber_id 就是那个赢家",
          winners and row.grabber_id == winners[0], f"{row.grabber_id} vs {winners}")
    print(f"     耗时 {elapsed * 1000:.0f} ms，平均 {elapsed * 1000 / 100:.1f} ms/次")


# ==================================================================
# 2. 佣金解冻：自动与手动并发
# ==================================================================
def bench_commission() -> None:
    print("\n=== 2. 佣金解冻：自动 vs 手动并发 ===")
    db, path = fresh_db("cm")
    from examples.grab_service import GrabService
    from examples.models import Grabber, GrabCommission, now_iso

    db.save_grabber(Grabber(user_id=5001, seq=1, name="测试员", status="active",
                            balance_frozen=1000, balance_available=0))
    ids = []
    for i in range(50):
        cm = GrabCommission(order_id=f"C{i}", grabber_id=5001, amount=20,
                            status="frozen", frozen_until="", created_at=now_iso())
        ids.append(db.add_grab_commission(cm))

    def auto_worker(seq):
        d = db
        svc = GrabService(d, None)
        svc._bot = None
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(svc.release_due_commissions())
        finally:
            loop.close()

    def manual_worker(i):
        d = db
        svc = GrabService(d, None)
        svc._bot = None
        svc.release_commission_manual(ids[i % len(ids)])

    with ThreadPoolExecutor(max_workers=20) as pool:
        futs = [pool.submit(auto_worker, i) for i in range(4)]
        futs += [pool.submit(manual_worker, i) for i in range(50)]
        for f in futs:
            f.result()

    g = db.get_grabber(5001)
    released = db.count_grab_commissions(grabber_id=5001, status="released")
    expected_total = released * 20
    check(f"解冻笔数 {released}，可提现余额 {g.balance_available} = {expected_total}",
          g.balance_available == expected_total,
          f"余额 {g.balance_available} 与已解冻总额 {expected_total} 不符（重复入账）")
    check("冻结余额归零", g.balance_frozen == 0, str(g.balance_frozen))


# ==================================================================
# 3. 尾数分配：200 并发同金额下单
# ==================================================================
def bench_suffix() -> None:
    print("\n=== 3. TRON 尾数并发分配（同金额 10 USDT）===")
    db, path = fresh_db("sfx")
    from examples.models import User
    from examples.shop_service import ShopService

    db.set_setting("pay_tron_enabled", True)
    db.set_setting("tron_wallet_address", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    db.set_setting("min_recharge_usdt", 1)
    db.set_setting("max_recharge_usdt", 1000)
    db.set_setting("points_rate", 100)
    for i in range(1, 201):
        db.save_user(User(user_id=9000 + i, username=f"u{i}", first_name=f"用户{i}", points=0))

    created: list[str] = []
    lock = threading.Lock()

    def worker(uid: int) -> None:
        d = db
        svc = ShopService(d, None)
        try:
            rc = svc.create_chain_recharge(uid, 10, "trc20")
            if rc:
                with lock:
                    created.append(rc.expected_amount)
        except sqlite3.OperationalError:
            global LOCK_ERRORS
            with lock:
                LOCK_ERRORS += 1

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(worker, range(9001, 9201)))
    elapsed = time.time() - t0

    uniq = len(set(created))
    check(f"成功建单 {len(created)} 笔，尾数唯一 {uniq} 个",
          uniq == len(created), f"有 {len(created) - uniq} 笔尾数重复！")
    check("全部落在 10.001~10.999 区间",
          all(s.startswith("10.") for s in created), str(created[:3]))
    print(f"     耗时 {elapsed * 1000:.0f} ms，平均 {elapsed * 1000 / max(len(created), 1):.1f} ms/单")


# ==================================================================
# 4. 提现审核：通过 vs 拒绝并发
# ==================================================================
def bench_withdrawal() -> None:
    print("\n=== 4. 提现审核：通过 vs 拒绝并发 ===")
    db, path = fresh_db("wd")
    from examples.grab_service import GrabService
    from examples.models import Grabber, Withdrawal, now_iso

    db.save_grabber(Grabber(user_id=4001, seq=1, name="提现员", status="active",
                            balance_available=1000, balance_frozen=0))
    results = {"paid": 0, "rejected": 0, "both": 0}

    for round_no in range(20):
        wid = f"W_TEST_{round_no:03d}"
        db.create_withdrawal(Withdrawal(id=wid, grabber_id=4001, amount=100,
                                        amount_usdt=1.0, address="88665544",
                                        status="pending", created_at=now_iso()))

        def do_approve(wid=wid, round_no=round_no):
            d = db
            svc = GrabService(d, None)
            return svc.approve_withdrawal(wid, 100000000, txid=f"tx{round_no}")

        def do_reject(wid=wid):
            d = db
            svc = GrabService(d, None)
            return svc.reject_withdrawal(wid, 100000000, note="测试拒绝")

        with ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(do_approve)
            fr = pool.submit(do_reject)
            ra, rr = fa.result(), fr.result()

        final = db.get_withdrawal(wid)
        if ra and rr:
            results["both"] += 1
        elif ra:
            results["paid"] += 1
        elif rr:
            results["rejected"] += 1
        if final.status not in ("paid", "rejected"):
            check(f"第 {round_no} 轮终态异常", False, final.status)

    check("20 轮并发：没有任何一轮两个操作都成功", results["both"] == 0,
          f"{results['both']} 轮出现双重生效")
    print(f"     通过 {results['paid']} 次 / 拒绝 {results['rejected']} 次 / 双生效 {results['both']} 次")


# ==================================================================
# 5. 吞吐
# ==================================================================
def bench_throughput() -> None:
    print("\n=== 5. 并发读写吞吐 ===")
    db, path = fresh_db("tput")
    from examples.models import User

    N = 1000
    for i in range(N):
        db.save_user(User(user_id=i + 1, username=f"u{i}", first_name=f"U{i}", points=0))

    def worker(i: int) -> bool:
        d = db
        try:
            u = d.get_user(i + 1)
            if not u:
                return False
            u.points += 1
            d.save_user(u)
            return True
        except sqlite3.OperationalError:
            global LOCK_ERRORS
            with _lock:
                LOCK_ERRORS += 1
            return False

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(worker, range(N)))
    elapsed = time.time() - t0
    ok = sum(1 for r in results if r)

    print(f"     {N} 次读+写，20 线程：{elapsed:.2f}s → {N / elapsed:.0f} 次/秒，成功 {ok}")
    check("吞吐 > 200 次/秒（业务只需 30/秒，Telegram 限流才是真瓶颈）", N / elapsed > 200, f"实测 {N / elapsed:.0f}")
    check("无 SQLite 锁错误", LOCK_ERRORS == 0, f"{LOCK_ERRORS} 次 database is locked")


if __name__ == "__main__":
    print("=" * 60)
    print("并发压测（多线程 + 独立连接，真实写锁竞争）")
    print("=" * 60)
    bench_grab()
    bench_commission()
    bench_suffix()
    bench_withdrawal()
    bench_throughput()
    print(f"\n{'=' * 60}")
    print(f"✅ 通过 {PASS} 项   ❌ 失败 {FAIL} 项")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)
