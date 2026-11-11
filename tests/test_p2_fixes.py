"""v2.7.1 回归：二次审计 P2 批次的 9 项修复。

每一项都对应当前代码里的一个具体缺陷，改动回退就会红。
全部离线：数据库走临时 SQLite，不连 Telegram、不连支付平台。
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_ID", "100000000")
_TMP = tempfile.mkdtemp(prefix="p2fix_")
os.environ["SHOP_DB_PATH"] = os.path.join(_TMP, "shop.db")

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


# --------------------------------------------------------------- N-P2-2
def test_safe_int():
    print("\n=== N-P2-2  cb_grab 回调参数安全解析 ===")
    from gshop.cb_grab import _safe_int

    check("正常数字", _safe_int("123") == 123)
    check("带空格", _safe_int(" 42 ") == 42)
    check("空串返回默认值", _safe_int("") == 0)
    check("非数字返回默认值", _safe_int("abc") == 0)
    check("负数原样返回", _safe_int("-7") == -7)
    check("None 不抛异常", _safe_int(None) == 0)
    check("自定义默认值", _safe_int("x", default=99) == 99)
    check("超长串不崩", _safe_int("9" * 500) > 0)


# --------------------------------------------------------------- N-P2-5/6/7
def test_task_registry():
    print("\n=== N-P2-5/6/7  后台任务注册表 ===")
    import gshop.tasks as T

    async def body():
        async def work(x):
            await asyncio.sleep(0)
            return x * 2

        before = T.pending_count()
        t1 = T.spawn(work(1), name="t1")
        t2 = T.spawn(work(2), name="t2")
        check("spawn 后计入持留集", T.pending_count() == before + 2)
        check("任务带名字", t1.get_name() == "t1")
        r1 = await t1
        r2 = await t2
        check("任务正常执行", r1 == 2 and r2 == 4)
        await asyncio.sleep(0)
        check("结束后从持留集移除", T.pending_count() == before)

        async def boom():
            raise ValueError("expected")

        T.spawn(boom(), name="boom")
        await asyncio.sleep(0.05)
        check("异常任务不挂死循环", T.pending_count() == before)

    run(body())


# --------------------------------------------------------------- N-P2-8
def test_iter_users_cursor():
    print("\n=== N-P2-8  iter_users 游标分页 ===")
    from gshop.models import User, now_iso
    from gshop.runtime import db

    base = now_iso()
    for i in range(1, 31):
        ts = base if i <= 4 else f"2026-02-01T00:00:{i:02d}"
        db.save_user(User(user_id=7000 + i, username=f"c{i}", first_name=f"C{i}", created_at=ts))

    seen = []
    for batch in db.iter_users(batch=6):
        check_batch = len(batch) <= 6
        if not check_batch:
            check("每批不超过 batch", False, str(len(batch)))
        seen.extend(u.user_id for u in batch)

    check("每批不超过 batch", True)
    check("不漏人", set(range(7001, 7031)).issubset(set(seen)))
    check("不重人", len(seen) == len(set(seen)))

    # created_at 为 NULL 的老数据也不能漏
    conn = sqlite3.connect(str(db.path))
    conn.execute(
        "INSERT INTO users(user_id, username, first_name, created_at) VALUES (7777, 'n', 'N', NULL)"
    )
    conn.commit()
    conn.close()
    seen2 = []
    for batch in db.iter_users(batch=6):
        seen2.extend(u.user_id for u in batch)
    check("created_at 为 NULL 也不漏", 7777 in seen2)


# --------------------------------------------------------------- N-P2-9
def test_backup():
    print("\n=== N-P2-9  do_backup 一致性快照 + integrity_check ===")
    import gshop.jobs as J
    from gshop.models import User
    from gshop.runtime import db

    bk = os.path.join(_TMP, "backups")
    J.BACKUP_DIR = bk
    db.save_user(User(user_id=8888, username="bk", first_name="BK"))

    p1 = J.do_backup()
    p2 = J.do_backup()
    check("备份文件存在", os.path.isfile(p1) and os.path.isfile(p2))
    check("毫秒级文件名不冲突", p1 != p2)
    # 文件名形如 shop_20261105_174414_049.db：日期_时分秒_毫秒
    stem = os.path.basename(p1).rsplit(".", 1)[0]
    check("文件名含毫秒段", len(stem.split("_")[-1]) == 3, stem)

    c = sqlite3.connect(p2)
    try:
        rows = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        c.close()
    check("备份内容可读", rows > 0, str(rows))
    check("integrity_check = ok", ok == "ok", ok)
    jsrc = open(os.path.join(ROOT, "gshop", "jobs.py"), encoding="utf-8").read()
    check("不再 import shutil", "import shutil" not in jsrc)
    check("改用 SQLite backup API", ".backup(" in jsrc)


# --------------------------------------------------------------- N-P2-10
def test_admin_id_parse():
    print("\n=== N-P2-10  ADMIN_ID 解析 ===")
    from gshop.runtime import _parse_admin_id

    check("正常 ID", _parse_admin_id("100000000") == 100000000)
    check("空串→0", _parse_admin_id("") == 0)
    check("None→0", _parse_admin_id(None) == 0)
    check("非法串→0", _parse_admin_id("abc") == 0)
    check("负数→0", _parse_admin_id("-5") == 0)
    check("零→0", _parse_admin_id("0") == 0)
    check("带空格可解析", _parse_admin_id(" 123 ") == 123)

    src = open(os.path.join(ROOT, "gshop", "runtime.py"), encoding="utf-8").read()
    check("0 号管理员不再无条件建档", "if ADMIN_ID > 0:" in src)


# --------------------------------------------------------------- N-P2-12
def test_safe_float():
    print("\n=== N-P2-12  safe_float 拒绝 inf/nan ===")
    from gshop.models import safe_float

    check("inf → 默认值", safe_float("inf", 24, 0, 8760) == 24)
    check("-inf → 默认值", safe_float("-inf", 24, 0, 8760) == 24)
    check("nan → 默认值", safe_float("nan", 24, 0, 8760) == 24)
    check("1e400 → 默认值", safe_float("1e400", 24, 0, 8760) == 24)
    check("非法串 → 默认值", safe_float("abc", 24, 0, 8760) == 24)
    check("正常小数", safe_float("12.5", 24, 0, 8760) == 12.5)
    check("0 合法", safe_float("0", 24, 0, 8760) == 0)
    check("负数越界 → 默认值", safe_float("-3", 24, 0, 8760) == 24)
    check("超大越界 → 默认值", safe_float("999999", 24, 0, 8760) == 24)

    # 关键：timedelta 不再被 inf 炸
    from datetime import datetime, timedelta

    from gshop.models import LOCAL_TZ

    try:
        datetime.now(LOCAL_TZ) + timedelta(hours=safe_float("inf", 24, 0, 8760))
        check("timedelta 不被 inf 炸", True)
    except OverflowError as exc:
        check("timedelta 不被 inf 炸", False, repr(exc))


# --------------------------------------------------------------- N-P2-13
def test_recharge_id_prefix():
    print("\n=== N-P2-13  充值单号分域前缀 ===")
    from gshop.models import gen_recharge_id

    ids = {
        "default": gen_recharge_id(),
        "okpay": gen_recharge_id("okpay"),
        "kkpay": gen_recharge_id("kkpay"),
        "trc20": gen_recharge_id("trc20"),
        "ton": gen_recharge_id("ton"),
    }
    for k, v in ids.items():
        check(f"{k} 前缀为 RC_", v.startswith("RC_"), v)
    check("okpay 标 OP", ids["okpay"].startswith("RC_OP_"))
    check("kkpay 标 KK", ids["kkpay"].startswith("RC_KK_"))
    check("trc20 标 TRC20", ids["trc20"].startswith("RC_TRC20_"))
    check("ton 标 TON", ids["ton"].startswith("RC_TON_"))
    check("不再以 ORDER_ 开头", not any(v.startswith("ORDER_") for v in ids.values()))
    check("全部唯一", len(set(ids.values())) == 5)


# --------------------------------------------------------------- N-P2-4
def test_aclose():
    print("\n=== N-P2-4  ShopService.aclose 释放连接池 ===")
    from gshop.runtime import service

    async def body():
        # 未创建过客户端时 aclose 不报错
        try:
            await service.aclose()
            check("无客户端时 aclose 不报错", True)
        except Exception as exc:
            check("无客户端时 aclose 不报错", False, repr(exc))

        class Dummy:
            closed = False

            async def close(self):
                Dummy.closed = True

        service._okpay = Dummy()
        service._kkpay = Dummy()
        await service.aclose()
        check("aclose 调用客户端 close", Dummy.closed)
        check("aclose 后引用置空", service._okpay is None and service._kkpay is None)

        class Bad:
            async def close(self):
                raise RuntimeError("boom")

        service._okpay = Bad()
        try:
            await service.aclose()
            check("close 抛错不影响停机", service._okpay is None)
        except Exception as exc:
            check("close 抛错不影响停机", False, repr(exc))

    run(body())
    check("post_shutdown 挂到 builder", ".post_shutdown(post_shutdown)" in open(os.path.join(ROOT, "main.py"), encoding="utf-8").read())


# --------------------------------------------------------------- N-P2-1/3
def test_appeal_and_answer():
    print("\n=== N-P2-1/N-P2-3  申诉归属校验 / 回调应答兜底 ===")
    src = open(os.path.join(ROOT, "gshop", "grab_service.py"), encoding="utf-8").read()
    check("申诉含订单归属校验", "该订单不属于你" in src)
    check("申诉含重复提交拦截", "已有待裁定申诉" in src)

    hc = open(os.path.join(ROOT, "gshop", "handlers_callback.py"), encoding="utf-8").read()
    check("query.answer 有 try 兜底", "回调应答失败" in hc)


if __name__ == "__main__":
    test_safe_int()
    test_task_registry()
    test_iter_users_cursor()
    test_backup()
    test_admin_id_parse()
    test_safe_float()
    test_recharge_id_prefix()
    test_aclose()
    test_appeal_and_answer()
    print(f"\n{'=' * 50}")
    print(f"✅ 通过 {PASS} 项   ❌ 失败 {FAIL} 项")
    print("=" * 50)
sys.exit(0 if FAIL == 0 else 1)
