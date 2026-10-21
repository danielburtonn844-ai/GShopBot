"""订单（order 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from ..base import (
    now_iso,
)
from ..schema import (
    Order,
)

logger = logging.getLogger(__name__)


class OrderMixin:
    """订单。"""

    def get_order(self, order_id: str, conn: sqlite3.Connection | None = None) -> Order | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if row:
                return Order.from_dict(dict(row))
            return None

    def save_order(self, order: Order, conn: sqlite3.Connection | None = None) -> None:
        d = order.to_dict()
        with self._maybe_conn(conn) as c:
            c.execute("""
                INSERT INTO orders (id, user_id, username, product_id, product_name, product_kind, points, status, priority, note, reply_content, reply_admin_id, reply_at, locked_by, stock_item_id, created_at, completed_at, grab_status, grabber_id, grabber_name, commission, grab_at, deadline, dispatch_chat, dispatch_msg, dispatch_count, last_dispatch_at, warn_sent)
                VALUES (:id, :user_id, :username, :product_id, :product_name, :product_kind, :points, :status, :priority, :note, :reply_content, :reply_admin_id, :reply_at, :locked_by, :stock_item_id, :created_at, :completed_at, :grab_status, :grabber_id, :grabber_name, :commission, :grab_at, :deadline, :dispatch_chat, :dispatch_msg, :dispatch_count, :last_dispatch_at, :warn_sent)
                ON CONFLICT(id) DO UPDATE SET
                    user_id=excluded.user_id,
                    username=excluded.username,
                    product_id=excluded.product_id,
                    product_name=excluded.product_name,
                    product_kind=excluded.product_kind,
                    points=excluded.points,
                    status=excluded.status,
                    priority=excluded.priority,
                    note=excluded.note,
                    reply_content=excluded.reply_content,
                    reply_admin_id=excluded.reply_admin_id,
                    reply_at=excluded.reply_at,
                    locked_by=excluded.locked_by,
                    stock_item_id=excluded.stock_item_id,
                    completed_at=excluded.completed_at,
                    grab_status=excluded.grab_status,
                    grabber_id=excluded.grabber_id,
                    grabber_name=excluded.grabber_name,
                    commission=excluded.commission,
                    grab_at=excluded.grab_at,
                    deadline=excluded.deadline,
                    dispatch_chat=excluded.dispatch_chat,
                    dispatch_msg=excluded.dispatch_msg,
                    dispatch_count=excluded.dispatch_count,
                    last_dispatch_at=excluded.last_dispatch_at,
                    warn_sent=excluded.warn_sent
            """, d)

    def search_orders(self, keyword: str, limit: int = 15) -> list[Order]:
        """订单搜索（v2.4.0 管理端）：订单号精确/前缀、用户名模糊、商品名模糊。"""
        conn = self._get_conn()
        try:
            like = f"%{keyword}%"
            rows = conn.execute(
                "SELECT * FROM orders WHERE id LIKE ? OR username LIKE ? OR product_name LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
            return [Order.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    # ---------- 订单 ----------

    def list_orders(
        self,
        user_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Order]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM orders WHERE 1=1"
            params: list[Any] = []
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
            return [Order.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def count_orders(self, status: str | None = None) -> int:
        conn = self._get_conn()
        try:
            if status:
                row = conn.execute("SELECT COUNT(*) FROM orders WHERE status = ?", (status,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM orders").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    # ---------- 充值记录 ----------

    def count_user_orders(self, user_id: int) -> int:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (user_id,)).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def count_user_product_orders(
        self, user_id: int, product_id: str, conn: sqlite3.Connection | None = None
    ) -> int:
        """该用户对某商品的有效购买次数（取消/退款不计），用于限购校验。

        用 SQL 直接 COUNT，无论历史订单多少条都数得准。
        """
        with self._maybe_conn(conn) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM orders WHERE user_id = ? AND product_id = ? AND status NOT IN ('cancelled', 'refunded')",
                (user_id, product_id),
            ).fetchone()
            return row[0] if row else 0

    def save_order_notification(self, chat_id: int, message_id: int, order_id: str) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO order_notifications (chat_id, message_id, order_id, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, message_id, order_id, now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_order_id_by_notification(self, chat_id: int, message_id: int) -> str | None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT order_id FROM order_notifications WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    # ---------- 搜索与统计 ----------
