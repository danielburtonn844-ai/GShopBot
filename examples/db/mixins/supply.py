"""付费供需（supply 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from ..schema import (
    SupplyPost,
)

logger = logging.getLogger(__name__)


class SupplyMixin:
    """付费供需。"""

    def save_supply_post(self, post: SupplyPost, conn: sqlite3.Connection | None = None) -> None:
        d = post.to_dict()
        d["anonymous"] = int(post.anonymous)
        d["bot_ok"] = int(post.bot_ok)
        d["channel_ok"] = int(post.channel_ok)
        d["refunded"] = int(post.refunded)
        with self._maybe_conn(conn) as c:
            c.execute(
                "INSERT OR REPLACE INTO supply_posts (id, user_id, username, display_name, category, content, "
                "content_html, photo_file_id, channel, cost, anonymous, status, bot_ok, channel_ok, channel_msg, refunded, created_at) "
                "VALUES (:id, :user_id, :username, :display_name, :category, :content, :content_html, :photo_file_id, :channel, "
                ":cost, :anonymous, :status, :bot_ok, :channel_ok, :channel_msg, :refunded, :created_at)",
                d,
            )

    def get_supply_post(self, post_id: str, conn: sqlite3.Connection | None = None) -> SupplyPost | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM supply_posts WHERE id = ?", (post_id,)).fetchone()
            return SupplyPost.from_dict(dict(row)) if row else None

    def list_supply_posts(
        self,
        status: str | None = "active",
        category: str | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> list[SupplyPost]:
        sql = "SELECT * FROM supply_posts WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        conn = self._get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [SupplyPost.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def count_supply_posts(self, status: str | None = "active", category: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM supply_posts WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if category:
            sql += " AND category = ?"
            params.append(category)
        conn = self._get_conn()
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def count_user_supply_posts(self, user_id: int, since_iso: str | None = None) -> int:
        """用户累计发帖数；传 since_iso 则只算该时间之后（用于每日上限）。"""
        sql = "SELECT COUNT(*) FROM supply_posts WHERE user_id = ?"
        params: list[Any] = [user_id]
        if since_iso:
            sql += " AND created_at >= ?"
            params.append(since_iso)
        conn = self._get_conn()
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def supply_stats(self, today_prefix: str) -> dict[str, Any]:
        """供需统计：今日/累计的发帖数与积分收入、渠道分布。today_prefix 如 '2026-09-07'。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt, COALESCE(SUM(cost), 0) AS income FROM supply_posts "
                "WHERE created_at >= ?",
                (today_prefix,),
            ).fetchone()
            today_cnt, today_income = row[0], row[1]
            row = conn.execute(
                "SELECT COUNT(*) AS cnt, COALESCE(SUM(cost), 0) AS income FROM supply_posts"
            ).fetchone()
            total_cnt, total_income = row[0], row[1]
            channels = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT channel, COUNT(*) FROM supply_posts GROUP BY channel"
                ).fetchall()
            }
            return {
                "today_cnt": today_cnt,
                "today_income": today_income,
                "total_cnt": total_cnt,
                "total_income": total_income,
                "channels": channels,
            }
        finally:
            conn.close()

    # ---------- 库存池（v2.4.0） ----------
