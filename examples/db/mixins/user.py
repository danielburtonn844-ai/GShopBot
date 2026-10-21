"""用户（user 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging
import sqlite3

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

    def count_users(self) -> int:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()
