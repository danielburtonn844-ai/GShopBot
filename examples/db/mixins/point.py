"""积分流水与操作日志（point 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging
import sqlite3

from ..base import (
    now_iso,
)
from ..schema import (
    AuditLog,
    PointRecord,
)

logger = logging.getLogger(__name__)


class PointMixin:
    """积分流水与操作日志。"""

    def add_point_record(
        self,
        user_id: int,
        change: int,
        balance_after: int,
        reason: str,
        operator_id: int = 0,
        ref: str = "",
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """记一笔积分变动流水。必须与实际积分变动处于同一事务。"""
        with self._maybe_conn(conn) as c:
            c.execute(
                "INSERT INTO point_history (user_id, change, balance_after, reason, operator_id, ref, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, change, balance_after, reason, operator_id, ref, now_iso()),
            )

    def list_point_records(self, user_id: int, limit: int = 20) -> list[PointRecord]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM point_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [PointRecord.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    # ---------- 付费供需 ----------

    def add_audit_log(self, admin_id: int, action: str, detail: str = "") -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO audit_logs (admin_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
                (admin_id, action, detail[:500], now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def list_audit_logs(self, limit: int = 15, offset: int = 0) -> list[AuditLog]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
            return [AuditLog.from_row(r) for r in rows]
        finally:
            conn.close()

    def count_audit_logs(self) -> int:
        conn = self._get_conn()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0])
        finally:
            conn.close()

    # ==================================================================
    # 抢单模块（v2.6.0）：接单员档案 / 佣金流水 / 提现单 / 申诉单 / 派单查询
    # ==================================================================

    # ---------- 接单员档案 ----------
