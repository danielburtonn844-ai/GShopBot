"""商品（product 领域）。

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
    Product,
)

logger = logging.getLogger(__name__)


class ProductMixin:
    """商品。"""

    def get_product(self, product_id: str, conn: sqlite3.Connection | None = None) -> Product | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if row:
                return Product.from_dict(dict(row))
            return None

    def save_product(self, product: Product, conn: sqlite3.Connection | None = None) -> None:
        product.updated_at = now_iso()
        d = product.to_dict()
        with self._maybe_conn(conn) as c:
            c.execute("""
                INSERT INTO products (id, name, description, points, kind, stock, limit_per_user, status, is_top, tags, auto_delivery, photo_file_id, category, delivery_mode, commission_pct, created_at, updated_at)
                VALUES (:id, :name, :description, :points, :kind, :stock, :limit_per_user, :status, :is_top, :tags, :auto_delivery, :photo_file_id, :category, :delivery_mode, :commission_pct, :created_at, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    points=excluded.points,
                    kind=excluded.kind,
                    stock=excluded.stock,
                    limit_per_user=excluded.limit_per_user,
                    status=excluded.status,
                    is_top=excluded.is_top,
                    tags=excluded.tags,
                    auto_delivery=excluded.auto_delivery,
                    photo_file_id=excluded.photo_file_id,
                    category=excluded.category,
                    delivery_mode=excluded.delivery_mode,
                    commission_pct=excluded.commission_pct,
                    updated_at=excluded.updated_at
            """, d)

    def delete_product(self, product_id: str) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
        finally:
            conn.close()

    def list_products(
        self,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        category: str | None = None,
    ) -> list[Product]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM products WHERE 1=1"
            params: list[Any] = []
            if kind:
                query += " AND kind = ?"
                params.append(kind)
            if status:
                query += " AND status = ?"
                params.append(status)
            if category:
                query += " AND category = ?"
                params.append(category)
            query += " ORDER BY is_top DESC, created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
            return [Product.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def search_products(
        self, keyword: str, kind: str | None = None, status: str | None = None, limit: int = 15
    ) -> list[Product]:
        """商品搜索（v2.4.0）：名称/介绍/分类模糊匹配，用户端传 status='on' 只搜在售。"""
        conn = self._get_conn()
        try:
            query = "SELECT * FROM products WHERE (name LIKE ? OR description LIKE ? OR category LIKE ?)"
            like = f"%{keyword}%"
            params: list[Any] = [like, like, like]
            if kind:
                query += " AND kind = ?"
                params.append(kind)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY is_top DESC, created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [Product.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()
