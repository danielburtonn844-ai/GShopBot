"""充值单与链上防重（recharge 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any

from ..base import (
    now_iso,
)
from ..schema import (
    Recharge,
)
from .conn import ConnAccessMixin

logger = logging.getLogger(__name__)


class RechargeMixin(ConnAccessMixin):
    """充值单与链上防重。"""

    def get_recharge(self, recharge_id: str, conn: sqlite3.Connection | None = None) -> Recharge | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM recharges WHERE id = ?", (recharge_id,)).fetchone()
            if row:
                return Recharge.from_dict(dict(row))
            return None

    def save_recharge(self, recharge: Recharge, conn: sqlite3.Connection | None = None) -> None:
        d = recharge.to_dict()
        with self._maybe_conn(conn) as c:
            c.execute("""
                INSERT INTO recharges (id, user_id, username, amount, points, status, pay_url, okpay_order_id, gateway, created_at, paid_at, chain, expected_amount, txid, memo)
                VALUES (:id, :user_id, :username, :amount, :points, :status, :pay_url, :okpay_order_id, :gateway, :created_at, :paid_at, :chain, :expected_amount, :txid, :memo)
                ON CONFLICT(id) DO UPDATE SET
                    user_id=excluded.user_id,
                    username=excluded.username,
                    amount=excluded.amount,
                    points=excluded.points,
                    status=excluded.status,
                    pay_url=excluded.pay_url,
                    okpay_order_id=excluded.okpay_order_id,
                    gateway=excluded.gateway,
                    paid_at=excluded.paid_at,
                    chain=excluded.chain,
                    expected_amount=excluded.expected_amount,
                    txid=excluded.txid,
                    memo=excluded.memo
            """, d)

    def list_recharges(self, user_id: int | None = None, status: str | None = None, limit: int = 50) -> list[Recharge]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM recharges WHERE 1=1"
            params: list[Any] = []
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [Recharge.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    # ---------- 管理员 ----------

    def list_pending_recharges(self, limit: int = 200) -> list[Recharge]:
        return self.list_recharges(status="pending", limit=limit)

    # ---------- v2.8.1 补单入口（审计 N-P0-2） ----------

    OPEN_RECHARGE_STATES = ("pending", "failed", "expired")

    def count_recharges_multi(self, statuses: tuple[str, ...]) -> int:
        """按多状态计数（补单页的分页总数）。"""
        if not statuses:
            return 0
        marks = ", ".join("?" for _ in statuses)
        conn = self._get_conn()
        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM recharges WHERE status IN ({marks})",
                tuple(statuses),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def list_recharges_multi(
        self, statuses: tuple[str, ...], limit: int = 50, offset: int = 0
    ) -> list[Recharge]:
        """按多状态列充值单（v2.8.1）。

        补单页面要同时看 pending / failed / expired 三态：pending 是正常待支付、
        failed 是下单失败或超时标失败、expired 是冷钱包超过宽限期仍未匹配到。
        后两种都可能已经真的收到钱，必须能被管理员翻出来补账。

        单独开一个方法、不改 list_recharges 的签名，是为了让现有的
        pages_user / jobs / shop_service 调用点行为完全不变。
        """
        if not statuses:
            return []
        marks = ", ".join("?" for _ in statuses)
        params: list[Any] = list(statuses)
        params.append(max(1, int(limit)))
        params.append(max(0, int(offset)))
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM recharges WHERE status IN ({marks})"
                " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            return [Recharge.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def pending_tron_suffixes(
        self, base_amount: int, conn: sqlite3.Connection | None = None
    ) -> set[int]:
        """某个整数金额下，已被 pending 冷钱包单占用的 TRON 识别尾数（全量，不截断）。

        尾数分配必须看全量：list_pending_recharges 有 limit=200，待支付单超过
        200 笔时更早的单不再出现在列表里，尾数会被重复分配，导致两笔不同订单的
        应付金额完全相同、付款记到别人账上。这里直接按金额分组全表扫描。
        """
        with self._maybe_conn(conn) as c:
            rows = c.execute(
                "SELECT expected_amount FROM recharges"
                " WHERE status = 'pending' AND chain = 'trc20' AND expected_amount != ''"
            ).fetchall()
        used: set[int] = set()
        for row in rows:
            raw = str(row[0] or "")
            try:
                d = Decimal(raw)
            except (InvalidOperation, ValueError):
                continue
            if int(d) != int(base_amount):
                continue  # 整数金额不同 → 完整应付金额不同，不会互相误匹配
            used.add(int((d % 1) * 1000))
        return used

    # ---------- 链上交易防重（v2.5.0 冷钱包：同一 txid 绝不二次入账） ----------

    def chain_txid_seen(self, txid: str) -> bool:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT 1 FROM chain_txids WHERE txid = ?", (txid,)).fetchone()
            return row is not None
        finally:
            conn.close()

    def add_chain_txid(self, txid: str, recharge_id: str, chain: str,
                       conn: sqlite3.Connection | None = None) -> None:
        """记录已入账的链上交易哈希；INSERT OR IGNORE 保证并发/重放下不炸不重复。"""
        with self._maybe_conn(conn) as c:
            c.execute(
                "INSERT OR IGNORE INTO chain_txids (txid, recharge_id, chain, created_at) VALUES (?, ?, ?, ?)",
                (txid, recharge_id, chain, now_iso()),
            )

    # ---------- 积分流水 ----------
