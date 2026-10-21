"""管理员（admin 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging

from ..schema import (
    Admin,
)

logger = logging.getLogger(__name__)


class AdminMixin:
    """管理员。"""

    def get_admin(self, user_id: int) -> Admin | None:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM admins WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return Admin.from_dict(dict(row))
            return None
        finally:
            conn.close()

    def save_admin(self, admin: Admin) -> None:
        conn = self._get_conn()
        try:
            d = admin.to_dict()
            conn.execute("""
                INSERT INTO admins (user_id, name, permissions, added_by, added_at)
                VALUES (:user_id, :name, :permissions, :added_by, :added_at)
                ON CONFLICT(user_id) DO UPDATE SET
                    name=excluded.name,
                    permissions=excluded.permissions,
                    added_by=excluded.added_by,
                    added_at=excluded.added_at
            """, d)
            conn.commit()
        finally:
            conn.close()

    def delete_admin(self, user_id: int) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()

    def list_admins(self) -> list[Admin]:
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM admins ORDER BY added_at").fetchall()
            return [Admin.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def is_admin(self, user_id: int) -> bool:
        return self.get_admin(user_id) is not None

    # ---------- 频道 ----------
