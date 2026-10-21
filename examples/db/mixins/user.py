"""用户（user 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator

from ..schema import (
    User,
)

logger = logging.getLogger(__name__)


class UserMixin:
    """用户。"""

    def get_user(self, user_id: int, conn: sqlite3.Connection | None = None) -> User | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return User.from_dict(dict(row))
            return None

    def save_user(self, user: User, conn: sqlite3.Connection | None = None) -> None:
        d = user.to_dict()
        with self._maybe_conn(conn) as c:
            c.execute("""
                INSERT INTO users (user_id, username, first_name, last_name, points, total_recharge, total_spent, is_banned, invite_code, invited_by, created_at, last_active)
                VALUES (:user_id, :username, :first_name, :last_name, :points, :total_recharge, :total_spent, :is_banned, :invite_code, :invited_by, :created_at, :last_active)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    points=excluded.points,
                    total_recharge=excluded.total_recharge,
                    total_spent=excluded.total_spent,
                    is_banned=excluded.is_banned,
                    invite_code=excluded.invite_code,
                    invited_by=excluded.invited_by,
                    created_at=excluded.created_at,
                    last_active=excluded.last_active
            """, d)

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
        offset = 0
        while True:
            rows = self.list_users(limit=batch, offset=offset)
            if not rows:
                return
            yield rows
            if len(rows) < batch:
                return
            offset += batch

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
