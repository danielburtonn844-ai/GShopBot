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

logger = logging.getLogger(__name__)


class RechargeMixin:
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
