"""用户（user 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from typing import Any

from ..base import (
    now_iso,
)
from ..schema import (
    User,
)
from .conn import ConnAccessMixin

logger = logging.getLogger(__name__)


class UserMixin(ConnAccessMixin):
    """用户。"""

    def get_user(self, user_id: int, conn: sqlite3.Connection | None = None) -> User | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return User.from_dict(dict(row))
            return None

    USER_COLUMNS = frozenset({
        "username", "first_name", "last_name", "points", "total_recharge",
        "total_spent", "is_banned", "invite_code", "invited_by", "created_at", "last_active",
    })

    def update_user_points(
        self,
        user_id: int,
        delta: int,
        conn: sqlite3.Connection | None = None,
        spent_delta: int | None = None,
        recharge_delta: float | None = None,
    ) -> bool:
        """积分增减走 SQL 自增（`points = points + ?`），绝不读改写。

        审计 N-P1-3：原实现是「读 User → 改 → save_user 全字段覆盖」，「每次按
        按钮都刷新档案」的调用点会把并发期间别人刚扣掉的积分写回去。SQL 自增
        天然并发安全，两个事务串行执行时各自看到的是最新的余额。
        """
        sets = ["points = points + ?", "last_active = ?"]
        params: list[Any] = [int(delta), now_iso()]
        if spent_delta is not None:
            sets.append("total_spent = MAX(0, total_spent + ?)")
            params.append(int(spent_delta))
        if recharge_delta is not None:
            sets.append("total_recharge = MAX(0, total_recharge + ?)")
            params.append(float(recharge_delta))
        sql = f"UPDATE users SET {', '.join(sets)} WHERE user_id = ?"
        params.append(int(user_id))
        with self._maybe_conn(conn) as c:
            cur = c.execute(sql, params)
            return cur.rowcount == 1

    def save_user(self, user: User, conn: sqlite3.Connection | None = None) -> None:
        """保存用户：不存在则 INSERT，存在则只更新**本对象改过**的列。

        v2.8.1（审计 N-P0-1 真修）：判定依据是对象自带的 `_baseline`（读库时的
        原始快照），不是「与数据库当前值比对」。旧实现的基准选错了——

            流程 A 读出 points=100（基线）
            流程 B 把 points 扣成 60 并落库
            流程 A 只改了 last_active，回来 save
            → 旧实现发现 库(60) != 快照(100)，误判成「A 改过 points」，把 100 写回去

        现在只写「当前值 != 基线」的列，本流程没碰过的列一律不进 SQL，
        期间被谁改过都不影响。资金类增减请走 update_user_points 的 SQL 自增，
        那条路径连「读-改-写」都不做。
        """
        d = user.to_dict()
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM users WHERE user_id = ?", (user.user_id,)).fetchone()
            if row is None:
                cols = ", ".join(d.keys())
                marks = ", ".join(f":{k}" for k in d)
                c.execute(f"INSERT INTO users ({cols}) VALUES ({marks})", d)
                user._baseline = dict(d)
                return
            old = dict(row)
            base = getattr(user, "_baseline", None)
            if base is None:
                # 直接 new 出来的对象（不是从库里读的），没有基线可比：
                # 退回「与库比对」，行为与 v2.7.0 一致，不会更差。
                changed = {
                    k: v for k, v in d.items()
                    if k != "user_id" and k in old and old[k] != v
                }
            else:
                changed = {
                    k: v for k, v in d.items()
                    if k != "user_id" and k in old and base.get(k) != v
                }
            if not changed:
                return
            sets = ", ".join(f"{k} = :{k}" for k in changed)
            changed["user_id"] = user.user_id
            c.execute(f"UPDATE users SET {sets} WHERE user_id = :user_id", changed)
            user._baseline = dict(d)

    def list_users(self, limit: int = 50, offset: int = 0) -> list[User]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [User.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def iter_users(self, batch: int = 500) -> Iterator[list[User]]:
        """按批遍历全部用户（广播 / 全量提醒用）。

        原实现直接 `list_users(limit=10000)`：用户超过 1 万后，第 10001 个之后的
        用户永远收不到广播，且没有任何报错。改成按 created_at + user_id 稳定
        分页，保证不漏人，同时每批只占 batch 条内存。
        """
        # 游标分页（keyset）：用 (created_at, user_id) 做续传点，不用 OFFSET。
        # OFFSET n 每次都要扫过前 n 行，全表 10 万人 × 200 批就是 O(n²)，
        # 广播时数据库 CPU 打满；游标版每批都是索引定位，总代价 O(n)。
        last_created = ""
        last_uid = 0
        while True:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM users "
                    "WHERE (COALESCE(created_at, '') > ?) "
                    "   OR (COALESCE(created_at, '') = ? AND user_id > ?) "
                    "ORDER BY COALESCE(created_at, '') ASC, user_id ASC LIMIT ?",
                    (last_created, last_created, last_uid, batch),
                ).fetchall()
            finally:
                conn.close()
            if not rows:
                return
            users = [User.from_dict(dict(r)) for r in rows]
            yield users
            if len(users) < batch:
                return
            last = users[-1]
            last_created = last.created_at or ""
            last_uid = last.user_id

    # ---------- 商品 ----------

    def search_users(self, query: str, limit: int = 20) -> list[User]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM users WHERE CAST(user_id AS TEXT) LIKE ? OR username LIKE ? OR first_name LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            return [User.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def get_user_by_username(self, username: str, conn: sqlite3.Connection | None = None) -> User | None:
        """按 username 精确反查（忽略大小写，去掉前导 @）。

        原实现是「取前 500 个用户再线性比对」，用户超过 500 后新用户永远找不到。
        这里走 SQL 索引查询，与用户总量无关。
        """
        name = (username or "").strip().lstrip("@")
        if not name:
            return None
        with self._maybe_conn(conn) as c:
            row = c.execute(
                "SELECT * FROM users WHERE LOWER(username) = LOWER(?) ORDER BY created_at DESC LIMIT 1",
                (name,),
            ).fetchone()
            return User.from_dict(dict(row)) if row else None

    def count_users(self) -> int:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()
