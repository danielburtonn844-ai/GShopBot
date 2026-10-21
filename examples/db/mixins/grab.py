"""抢单派单（接单员/佣金/提现/申诉）（grab 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from ..base import (
    now_iso,
    now_str,
)
from ..schema import (
    GrabAppeal,
    Grabber,
    GrabCommission,
    Order,
    Withdrawal,
)

logger = logging.getLogger(__name__)


class GrabMixin:
    """抢单派单（接单员/佣金/提现/申诉）。"""

    def save_grabber(self, g: Grabber, conn: sqlite3.Connection | None = None) -> None:
        g.updated_at = now_iso()
        if not g.created_at:
            g.created_at = g.updated_at
        with self._maybe_conn(conn) as c:
            c.execute("""
                INSERT INTO grabbers (user_id, seq, username, name, status, balance_available, balance_frozen,
                                      total_commission, completed_orders, timeout_orders, giveup_orders,
                                      cooldown_until, last_grab_at, invite_link, joined_group,
                                      created_at, updated_at)
                VALUES (:user_id, :seq, :username, :name, :status, :balance_available, :balance_frozen,
                        :total_commission, :completed_orders, :timeout_orders, :giveup_orders,
                        :cooldown_until, :last_grab_at, :invite_link, :joined_group,
                        :created_at, :updated_at)
                ON CONFLICT(user_id) DO UPDATE SET
                    seq=excluded.seq,
                    username=excluded.username,
                    name=excluded.name,
                    status=excluded.status,
                    balance_available=excluded.balance_available,
                    balance_frozen=excluded.balance_frozen,
                    total_commission=excluded.total_commission,
                    completed_orders=excluded.completed_orders,
                    timeout_orders=excluded.timeout_orders,
                    giveup_orders=excluded.giveup_orders,
                    cooldown_until=excluded.cooldown_until,
                    last_grab_at=excluded.last_grab_at,
                    invite_link=excluded.invite_link,
                    joined_group=excluded.joined_group,
                    updated_at=excluded.updated_at
            """, {
                "user_id": g.user_id, "seq": g.seq, "username": g.username, "name": g.name,
                "status": g.status, "balance_available": g.balance_available, "balance_frozen": g.balance_frozen,
                "total_commission": g.total_commission, "completed_orders": g.completed_orders,
                "timeout_orders": g.timeout_orders, "giveup_orders": g.giveup_orders,
                "cooldown_until": g.cooldown_until, "last_grab_at": g.last_grab_at,
                "invite_link": g.invite_link, "joined_group": g.joined_group,
                "created_at": g.created_at, "updated_at": g.updated_at,
            })

    def get_grabber(self, user_id: int, conn: sqlite3.Connection | None = None) -> Grabber | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM grabbers WHERE user_id = ?", (user_id,)).fetchone()
            return Grabber.from_dict(dict(row)) if row else None

    def get_grabber_by_invite(self, invite_link: str, conn: sqlite3.Connection | None = None) -> Grabber | None:
        """按专属入群链接反查接单员（进群归属判定用）。"""
        if not invite_link:
            return None
        with self._maybe_conn(conn) as c:
            row = c.execute(
                "SELECT * FROM grabbers WHERE invite_link = ?", (invite_link,)
            ).fetchone()
            return Grabber.from_dict(dict(row)) if row else None

    def next_grabber_seq(self, conn: sqlite3.Connection | None = None) -> int:
        """下一个接单员编号（#0001 起）。与建档放同一事务里用，防并发撞号。"""
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT MAX(seq) FROM grabbers").fetchone()
            return int(row[0] or 0) + 1

    def list_grabbers(self, status: str = "", limit: int = 30, offset: int = 0) -> list[Grabber]:
        conn = self._get_conn()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM grabbers WHERE status = ? ORDER BY seq ASC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM grabbers ORDER BY seq ASC LIMIT ? OFFSET ?", (limit, offset)
                ).fetchall()
            return [Grabber.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def count_grabbers(self, status: str = "") -> int:
        conn = self._get_conn()
        try:
            if status:
                return int(conn.execute("SELECT COUNT(*) FROM grabbers WHERE status = ?", (status,)).fetchone()[0])
            return int(conn.execute("SELECT COUNT(*) FROM grabbers").fetchone()[0])
        finally:
            conn.close()

    def delete_grabber(self, user_id: int) -> bool:
        conn = self._get_conn()
        try:
            cur = conn.execute("DELETE FROM grabbers WHERE user_id = ?", (user_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def is_grabber_active(self, user_id: int) -> bool:
        """快捷判断：在岗接单员（抢单按钮回调的高频路径用）。"""
        g = self.get_grabber(user_id)
        return bool(g and g.status == "active")

    # ---------- 佣金流水 ----------

    def add_grab_commission(self, cm: GrabCommission, conn: sqlite3.Connection | None = None) -> int:
        with self._maybe_conn(conn) as c:
            cur = c.execute(
                "INSERT INTO grab_commissions (order_id, grabber_id, amount, status, frozen_until, released_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cm.order_id, cm.grabber_id, cm.amount, cm.status, cm.frozen_until, cm.released_at, now_iso()),
            )
            return int(cur.lastrowid or 0)

    def get_commission_by_order(self, order_id: str, conn: sqlite3.Connection | None = None) -> GrabCommission | None:
        with self._maybe_conn(conn) as c:
            row = c.execute(
                "SELECT * FROM grab_commissions WHERE order_id = ? ORDER BY id DESC LIMIT 1", (order_id,)
            ).fetchone()
            return GrabCommission.from_dict(dict(row)) if row else None

    def list_grab_commissions(
        self, grabber_id: int = 0, status: str = "", limit: int = 20, offset: int = 0
    ) -> list[GrabCommission]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM grab_commissions WHERE 1=1"
            params: list[Any] = []
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            return [GrabCommission.from_dict(dict(r)) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

    def get_grab_commission(
        self, cid: int, conn: sqlite3.Connection | None = None
    ) -> GrabCommission | None:
        """按主键取单笔佣金（手动解冻用，不依赖分页列表）。"""
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM grab_commissions WHERE id = ?", (cid,)).fetchone()
            return GrabCommission.from_dict(dict(row)) if row else None

    def count_grab_commissions(self, grabber_id: int = 0, status: str = "") -> int:
        conn = self._get_conn()
        try:
            query = "SELECT COUNT(*) FROM grab_commissions WHERE 1=1"
            params: list[Any] = []
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            return int(conn.execute(query, params).fetchone()[0])
        finally:
            conn.close()

    def due_commissions(self, now: str, limit: int = 200) -> list[GrabCommission]:
        """到期该解冻的冻结佣金（frozen_until 空串视为立即到期）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM grab_commissions WHERE status = 'frozen' AND (frozen_until = '' OR frozen_until <= ?)"
                " ORDER BY id ASC LIMIT ?",
                (now, limit),
            ).fetchall()
            return [GrabCommission.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def set_commission_status(
        self,
        cid: int,
        status: str,
        released_at: str = "",
        conn: sqlite3.Connection | None = None,
        expect: str | None = None,
    ) -> bool:
        """更新佣金状态。

        expect 非空时做 CAS（compare-and-swap）：只有库中当前状态等于 expect 才更新，
        返回是否命中。资金记账必须用 CAS 形式——自动解冻与手动解冻可能交错执行，
        无条件的 UPDATE 会让同一笔佣金被两次计入可提现余额。
        """
        with self._maybe_conn(conn) as c:
            if expect is None:
                c.execute(
                    "UPDATE grab_commissions SET status = ?, released_at = ? WHERE id = ?",
                    (status, released_at, cid),
                )
                return True
            cur = c.execute(
                "UPDATE grab_commissions SET status = ?, released_at = ? WHERE id = ? AND status = ?",
                (status, released_at, cid, expect),
            )
            return cur.rowcount > 0

    # ---------- 提现单 ----------

    def create_withdrawal(self, w: Withdrawal, conn: sqlite3.Connection | None = None) -> None:
        with self._maybe_conn(conn) as c:
            c.execute(
                "INSERT INTO withdrawals (id, grabber_id, amount, amount_usdt, address, status, txid, note, processed_by, processed_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (w.id, w.grabber_id, w.amount, w.amount_usdt, w.address, w.status, w.txid, w.note,
                 w.processed_by, w.processed_at, now_iso()),
            )

    def get_withdrawal(self, wid: str, conn: sqlite3.Connection | None = None) -> Withdrawal | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM withdrawals WHERE id = ?", (wid,)).fetchone()
            return Withdrawal.from_dict(dict(row)) if row else None

    def save_withdrawal(self, w: Withdrawal, conn: sqlite3.Connection | None = None) -> None:
        with self._maybe_conn(conn) as c:
            c.execute("""
                UPDATE withdrawals SET status = :status, txid = :txid, note = :note,
                       processed_by = :processed_by, processed_at = :processed_at
                WHERE id = :id
            """, {
                "id": w.id, "status": w.status, "txid": w.txid, "note": w.note,
                "processed_by": w.processed_by, "processed_at": w.processed_at,
            })

    def list_withdrawals(
        self, status: str = "", grabber_id: int = 0, limit: int = 20, offset: int = 0
    ) -> list[Withdrawal]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM withdrawals WHERE 1=1"
            params: list[Any] = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            return [Withdrawal.from_dict(dict(r)) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

    def count_withdrawals(self, status: str = "", grabber_id: int = 0) -> int:
        conn = self._get_conn()
        try:
            query = "SELECT COUNT(*) FROM withdrawals WHERE 1=1"
            params: list[Any] = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            return int(conn.execute(query, params).fetchone()[0])
        finally:
            conn.close()

    def has_pending_withdrawal(self, grabber_id: int, conn: sqlite3.Connection | None = None) -> bool:
        """是否已有待审提现单（同一时间只许一单在审，防重复申请）。"""
        with self._maybe_conn(conn) as c:
            row = c.execute(
                "SELECT 1 FROM withdrawals WHERE grabber_id = ? AND status = 'pending' LIMIT 1",
                (grabber_id,),
            ).fetchone()
            return row is not None

    # ---------- 申诉单 ----------

    def create_grab_appeal(self, a: GrabAppeal, conn: sqlite3.Connection | None = None) -> int:
        with self._maybe_conn(conn) as c:
            cur = c.execute(
                "INSERT INTO grab_appeals (grabber_id, order_id, content, status, reply, handled_by, handled_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (a.grabber_id, a.order_id, a.content, a.status, a.reply, a.handled_by, a.handled_at, now_iso()),
            )
            return int(cur.lastrowid or 0)

    def get_grab_appeal(self, aid: int, conn: sqlite3.Connection | None = None) -> GrabAppeal | None:
        with self._maybe_conn(conn) as c:
            row = c.execute("SELECT * FROM grab_appeals WHERE id = ?", (aid,)).fetchone()
            return GrabAppeal.from_dict(dict(row)) if row else None

    def save_grab_appeal(self, a: GrabAppeal, conn: sqlite3.Connection | None = None) -> None:
        with self._maybe_conn(conn) as c:
            c.execute(
                "UPDATE grab_appeals SET status = ?, reply = ?, handled_by = ?, handled_at = ? WHERE id = ?",
                (a.status, a.reply, a.handled_by, a.handled_at, a.id),
            )

    def list_grab_appeals(
        self, status: str = "", grabber_id: int = 0, limit: int = 20, offset: int = 0
    ) -> list[GrabAppeal]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM grab_appeals WHERE 1=1"
            params: list[Any] = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            query += " ORDER BY id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            return [GrabAppeal.from_dict(dict(r)) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

    def count_grab_appeals(self, status: str = "") -> int:
        conn = self._get_conn()
        try:
            if status:
                return int(conn.execute("SELECT COUNT(*) FROM grab_appeals WHERE status = ?", (status,)).fetchone()[0])
            return int(conn.execute("SELECT COUNT(*) FROM grab_appeals").fetchone()[0])
        finally:
            conn.close()

    # ---------- 派单/抢单订单查询 ----------

    def grab_order_atomic(
        self,
        order_id: str,
        grabber_id: int,
        grabber_name: str,
        commission: int,
        grab_at: str,
        deadline: str,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        """原子抢单：只有 still waiting 的单才抢得到，rowcount=0 即手慢无。

        并发安全靠单条 UPDATE 的 WHERE 条件保证——SQLite 写锁串行化，
        两个接单员同时点也只有一个人的 rowcount 是 1。
        """
        with self._maybe_conn(conn) as c:
            cur = c.execute(
                "UPDATE orders SET grab_status = 'grabbed', grabber_id = ?, grabber_name = ?,"
                " commission = ?, grab_at = ?, deadline = ?"
                " WHERE id = ? AND grab_status = 'waiting'",
                (grabber_id, grabber_name, commission, grab_at, deadline, order_id),
            )
            return cur.rowcount == 1

    def release_grab_order(
        self, order_id: str, conn: sqlite3.Connection | None = None
    ) -> None:
        """释放回待抢池：清接单员/时限/预警标记，保留 dispatch_count 计数。"""
        with self._maybe_conn(conn) as c:
            c.execute(
                "UPDATE orders SET grab_status = 'waiting', grabber_id = 0, grabber_name = '',"
                " grab_at = '', deadline = '', warn_sent = 0"
                " WHERE id = ?",
                (order_id,),
            )

    def list_grab_orders(
        self, grab_status: str = "", grabber_id: int = 0, limit: int = 20, offset: int = 0
    ) -> list[Order]:
        """派单列表查询：grab_status 传 'grabbed' 等精确值；传 '' 表示全部派单（grab_status != ''）。"""
        conn = self._get_conn()
        try:
            query = "SELECT * FROM orders WHERE grab_status != ''"
            params: list[Any] = []
            if grab_status:
                query = "SELECT * FROM orders WHERE grab_status = ?"
                params.append(grab_status)
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            query += " ORDER BY id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            return [Order.from_dict(dict(r)) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

    def count_grab_orders(self, grab_status: str = "", grabber_id: int = 0) -> int:
        conn = self._get_conn()
        try:
            if grab_status:
                query = "SELECT COUNT(*) FROM orders WHERE grab_status = ?"
                params: list[Any] = [grab_status]
            else:
                query = "SELECT COUNT(*) FROM orders WHERE grab_status != ''"
                params = []
            if grabber_id:
                query += " AND grabber_id = ?"
                params.append(grabber_id)
            return int(conn.execute(query, params).fetchone()[0])
        finally:
            conn.close()

    def timeout_grab_orders(self, now: str, limit: int = 100) -> list[Order]:
        """已过最晚回单时限仍在进行中的单（定时任务扫描用）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM orders WHERE grab_status = 'grabbed' AND deadline != '' AND deadline <= ?"
                " ORDER BY deadline ASC LIMIT ?",
                (now, limit),
            ).fetchall()
            return [Order.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def warn_due_grab_orders(self, warn_before: str, now: str, limit: int = 100) -> list[Order]:
        """进入预警窗口（now < deadline <= warn_before）且还没发过预警的单。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM orders WHERE grab_status = 'grabbed' AND warn_sent = 0"
                " AND deadline != '' AND deadline > ? AND deadline <= ? LIMIT ?",
                (now, warn_before, limit),
            ).fetchall()
            return [Order.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    def mark_grab_warned(self, order_id: int | str, conn: sqlite3.Connection | None = None) -> None:
        with self._maybe_conn(conn) as c:
            c.execute("UPDATE orders SET warn_sent = 1 WHERE id = ?", (str(order_id),))

    def grab_stats(self) -> dict[str, int]:
        """管理面板抢单概览：待抢/进行中/今日完单/接单员数/待审提现/待审申诉。"""
        conn = self._get_conn()
        try:
            waiting = conn.execute("SELECT COUNT(*) FROM orders WHERE grab_status = 'waiting'").fetchone()[0]
            grabbed = conn.execute("SELECT COUNT(*) FROM orders WHERE grab_status = 'grabbed'").fetchone()[0]
            today = now_str()[:10]
            done_today = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE grab_status = 'done' AND substr(completed_at, 1, 10) = ?",
                (today,),
            ).fetchone()[0]
            grabbers = conn.execute("SELECT COUNT(*) FROM grabbers WHERE status = 'active'").fetchone()[0]
            wd_pending = conn.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'").fetchone()[0]
            appeal_pending = conn.execute("SELECT COUNT(*) FROM grab_appeals WHERE status = 'pending'").fetchone()[0]
            return {
                "waiting": int(waiting), "grabbed": int(grabbed), "done_today": int(done_today),
                "grabbers": int(grabbers), "wd_pending": int(wd_pending), "appeal_pending": int(appeal_pending),
            }
        finally:
            conn.close()
