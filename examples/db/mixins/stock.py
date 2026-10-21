"""库存池与到货提醒（stock 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import json
import logging
import sqlite3

from ..base import (
    now_iso,
)
from ..schema import (
    StockItem,
)

logger = logging.getLogger(__name__)


class StockMixin:
    """库存池与到货提醒。"""

    def add_stock_item(
        self,
        product_id: str,
        content_type: str,
        content_text: str = "",
        file_ids: list[str] | None = None,
        caption: str = "",
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """入库一件货，返回自增 id（即「编号」）。"""
        with self._maybe_conn(conn) as c:
            cur = c.execute(
                "INSERT INTO stock_items (product_id, content_type, content_text, file_ids, caption, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'available', ?)",
                (
                    product_id,
                    content_type,
                    content_text,
                    json.dumps(file_ids or [], ensure_ascii=False),
                    caption,
                    now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def get_stock_item(self, item_id: int, conn: sqlite3.Connection | None = None) -> StockItem | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM stock_items WHERE id = ?", (item_id,)).fetchone()
            return StockItem.from_dict(dict(row)) if row else None

    def list_stock_items(
        self,
        product_id: str,
        status: str | None = "available",
        limit: int = 50,
        offset: int = 0,
    ) -> list[StockItem]:
        conn = self._get_conn()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM stock_items WHERE product_id = ? AND status = ? ORDER BY id LIMIT ? OFFSET ?",
                    (product_id, status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM stock_items WHERE product_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (product_id, limit, offset),
                ).fetchall()
            return [StockItem.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def count_stock_items(self, product_id: str, status: str | None = "available") -> int:
        conn = self._get_conn()
        try:
            if status:
                row = conn.execute(
                    "SELECT COUNT(*) FROM stock_items WHERE product_id = ? AND status = ?",
                    (product_id, status),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM stock_items WHERE product_id = ?", (product_id,)
                ).fetchone()
            return int(row[0])
        finally:
            conn.close()

    def take_stock_item(
        self, product_id: str, order_id: str, buyer_id: int, conn: sqlite3.Connection
    ) -> StockItem | None:
        """原子取货（必须在 atomic 事务内调用）：锁定池中最早一件可用货并标记售出。

        UPDATE ... WHERE status='available' 自带条件竞争防护——两个并发事务里只有一个
        能改到同一行（另一事务在 BEGIN IMMEDIATE 阶段已被串行化），不会超卖。
        """
        row = conn.execute(
            "SELECT id FROM stock_items WHERE product_id = ? AND status = 'available' ORDER BY id LIMIT 1",
            (product_id,),
        ).fetchone()
        if not row:
            return None
        item_id = int(row[0])
        cur = conn.execute(
            "UPDATE stock_items SET status = 'sold', order_id = ?, buyer_id = ?, sold_at = ? "
            "WHERE id = ? AND status = 'available'",
            (order_id, buyer_id, now_iso(), item_id),
        )
        if cur.rowcount != 1:
            return None
        return self.get_stock_item(item_id, conn=conn)

    def release_stock_item(self, item_id: int, conn: sqlite3.Connection | None = None) -> None:
        """发货失败回滚：货退回池（清掉订单绑定），积分退款由调用方另行处理。"""
        with self._maybe_conn(conn) as c:
            c.execute(
                "UPDATE stock_items SET status = 'available', order_id = '', buyer_id = 0, sold_at = '' WHERE id = ?",
                (item_id,),
            )

    def delete_stock_item(self, item_id: int) -> bool:
        conn = self._get_conn()
        try:
            cur = conn.execute("DELETE FROM stock_items WHERE id = ?", (item_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def clear_sold_stock_items(self, product_id: str) -> int:
        """清空某商品已售记录，返回清理条数。"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "DELETE FROM stock_items WHERE product_id = ? AND status = 'sold'", (product_id,)
            )
            conn.commit()
            return int(cur.rowcount)
        finally:
            conn.close()

    def stock_pool_stats(self) -> dict[str, int]:
        """全站库存池概览（财务页用）：在售件数/已售件数/池模式商品数。"""
        conn = self._get_conn()
        try:
            avail = conn.execute(
                "SELECT COUNT(*) FROM stock_items WHERE status = 'available'"
            ).fetchone()[0]
            sold = conn.execute("SELECT COUNT(*) FROM stock_items WHERE status = 'sold'").fetchone()[0]
            pool_products = conn.execute(
                "SELECT COUNT(*) FROM products WHERE delivery_mode = 'pool'"
            ).fetchone()[0]
            return {"available": int(avail), "sold": int(sold), "pool_products": int(pool_products)}
        finally:
            conn.close()

    # ---------- 到货提醒订阅（v2.4.0） ----------

    def add_restock_sub(self, product_id: str, user_id: int) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO restock_subs (product_id, user_id, created_at) VALUES (?, ?, ?)",
                (product_id, user_id, now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def remove_restock_sub(self, product_id: str, user_id: int) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "DELETE FROM restock_subs WHERE product_id = ? AND user_id = ?",
                (product_id, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def has_restock_sub(self, product_id: str, user_id: int) -> bool:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM restock_subs WHERE product_id = ? AND user_id = ?",
                (product_id, user_id),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def pop_restock_subscribers(self, product_id: str) -> list[int]:
        """取出并清空某商品的全部订阅者（提醒一次即退订，不骚扰）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT user_id FROM restock_subs WHERE product_id = ?", (product_id,)
            ).fetchall()
            conn.execute("DELETE FROM restock_subs WHERE product_id = ?", (product_id,))
            conn.commit()
            return [int(r[0]) for r in rows]
        finally:
            conn.close()

    # ---------- 管理员操作日志（v2.4.0） ----------
