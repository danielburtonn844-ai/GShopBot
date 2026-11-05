"""订单（order 领域）。

由 models.py 拆分而来。v2.7.0 起 save_order 改为「差量更新」：

历史缺陷（审计报告 N-P0-1，全仓最严重）：save_order 原本是 INSERT ...
ON CONFLICT(id) DO UPDATE SET 全 28 列的 upsert。任何「读出 Order → await 一下
→ 再 save」的代码都会拿旧快照把这段时间里别人改的所有列覆盖回去，实测造成：

  - 管理员已回单 → 被派单协程整单回滚，回单内容被清空（内容丢失）
  - 接单员刚抢中 → 被重派协程抹掉，grabber_id 归 0（抢单丢失）

The fix: 落库前先读当前行，只 UPDATE 与内存对象不同的列。内存对象没碰过的列
永远不可能被写回去，跨 await 持有旧快照就再也不会丢更新。同时提供
update_order_fields() 做条件更新（CAS），给派单/抢单/退款这些状态机路径用。
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
from .conn import ConnAccessMixin

logger = logging.getLogger(__name__)

# 允许被 update_order_fields 更新的列白名单（防止拼 SQL 时把非法列名带进去）
ORDER_COLUMNS = frozenset({
    "user_id", "username", "product_id", "product_name", "product_kind", "points",
    "status", "priority", "note", "reply_content", "reply_admin_id", "reply_at",
    "locked_by", "stock_item_id", "created_at", "completed_at", "grab_status",
    "grabber_id", "grabber_name", "commission", "grab_at", "deadline",
    "dispatch_chat", "dispatch_msg", "dispatch_count", "last_dispatch_at", "warn_sent",
})


class OrderMixin(ConnAccessMixin):
    """订单。"""

    def get_order(self, order_id: str, conn: sqlite3.Connection | None = None) -> Order | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if row:
                return Order.from_dict(dict(row))
            return None

    def _insert_order(self, order: Order, conn: sqlite3.Connection | None = None) -> None:
        """新建订单：全字段 INSERT（已存在则报错，不允许静默覆盖）。"""
        d = order.to_dict()
        with self._maybe_conn(conn) as c:
            cols = ", ".join(d.keys())
            marks = ", ".join(f":{k}" for k in d)
            c.execute(f"INSERT INTO orders ({cols}) VALUES ({marks})", d)

    def insert_order(self, order: Order, conn: sqlite3.Connection | None = None) -> None:
        self._insert_order(order, conn=conn)

    def update_order_fields(
        self,
        order_id: str,
        conn: sqlite3.Connection | None = None,
        where: str = "",
        where_params: tuple = (),
        **fields: Any,
    ) -> bool:
        """条件更新（CAS）：只写传入的列，可附加 WHERE 前置状态。

        SQL 里只会出现 fields 里给的列名，其余列一律不动。返回是否命中 1 行；
        返回 False 说明 WHERE 条件没满足（例如订单已被别人抢走 / 已终态），
        调用方必须按「本次操作放弃」处理，不得继续写别的字段。
        """
        clean = {k: v for k, v in fields.items() if k in ORDER_COLUMNS}
        if not clean:
            return False
        sets = ", ".join(f"{k} = ?" for k in clean)
        sql = f"UPDATE orders SET {sets} WHERE id = ?"
        params: list[Any] = list(clean.values()) + [order_id]
        if where:
            sql += f" AND ({where})"
            params.extend(where_params)
        with self._maybe_conn(conn) as c:
            cur = c.execute(sql, params)
            return cur.rowcount == 1

    def save_order(self, order: Order, conn: sqlite3.Connection | None = None) -> None:
        """保存订单：不存在则 INSERT，存在则**只更新真正变化的列**。

        跨 await 拿着旧快照回来 save 时，没被本流程改过的列（管理员回单内容、
        别人的抢单结果、派单计数……）都不会被覆盖，从根上消灭 N-P0-1 的丢更新。
        """
        d = order.to_dict()
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM orders WHERE id = ?", (order.id,)).fetchone()
            if row is None:
                cols = ", ".join(d.keys())
                marks = ", ".join(f":{k}" for k in d)
                c.execute(f"INSERT INTO orders ({cols}) VALUES ({marks})", d)
                return
            old = dict(row)
            changed = {
                k: v for k, v in d.items()
                if k != "id" and k in old and old[k] != v
            }
            if not changed:
                return
            sets = ", ".join(f"{k} = :{k}" for k in changed)
            changed["id"] = order.id
            c.execute(f"UPDATE orders SET {sets} WHERE id = :id", changed)

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
        grab_status: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> list[Order]:
        """订单列表：筛选条件全部下推到 SQL，配真分页（管理端不再「先取 200 条再 Python 过滤」）。"""
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
            if grab_status is not None:
                query += " AND grab_status = ?"
                params.append(grab_status)
            if created_after:
                query += " AND created_at >= ?"
                params.append(created_after)
            if created_before:
                query += " AND created_at <= ?"
                params.append(created_before)
            query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
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

    def count_orders_by_status(self) -> dict[str, int]:
        """一次 SQL 出全部状态计数（管理端分类统计，不再把全表读进 Python）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall()
            return {str(r[0] or ""): int(r[1]) for r in rows}
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
        """该用户对某商品的**有效**购买次数，用于限购校验。

        退款/取消的订单不占额度（审计 P2-11：原实现把已退款订单也算进限购额度，
        退款后用户被永久锁死）。
        """
        with self._maybe_conn(conn) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM orders WHERE user_id = ? AND product_id = ?"
                " AND status NOT IN ('cancelled', 'refunded')",
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

    def next_order_seq(self, date_prefix: str, conn: sqlite3.Connection | None = None) -> int:
        """自增订单序号（替代 COUNT(*)+1）。

        原实现按 `COUNT(*) WHERE id LIKE 'YYYYMMDD-%'` 生成序号：订单被任何脚本
        物理删除一次，计数就回退，下一单立刻撞主键；多进程下也会撞号。这里用
        独立的 order_seq 表按日期维护严格递增计数，只增不减。
        """
        with self._maybe_conn(conn) as c:
            c.execute(
                "INSERT INTO order_seq (date, seq) VALUES (?, 0) ON CONFLICT(date) DO NOTHING",
                (date_prefix,),
            )
            c.execute("UPDATE order_seq SET seq = seq + 1 WHERE date = ?", (date_prefix,))
            row = c.execute("SELECT seq FROM order_seq WHERE date = ?", (date_prefix,)).fetchone()
            return int(row[0]) if row else 1

    # ---------- 搜索与统计 ----------
