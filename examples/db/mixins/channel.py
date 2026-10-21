"""频道（channel 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging

from ..schema import (
    Channel,
)

logger = logging.getLogger(__name__)


class ChannelMixin:
    """频道。"""

    def get_channel(self, channel_id: str) -> Channel | None:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
            if row:
                return Channel.from_dict(dict(row))
            return None
        finally:
            conn.close()

    def save_channel(self, channel: Channel) -> None:
        conn = self._get_conn()
        try:
            d = channel.to_dict()
            conn.execute("""
                INSERT INTO channels (id, chat_id, title, username, added_by, added_at)
                VALUES (:id, :chat_id, :title, :username, :added_by, :added_at)
                ON CONFLICT(id) DO UPDATE SET
                    chat_id=excluded.chat_id,
                    title=excluded.title,
                    username=excluded.username,
                    added_by=excluded.added_by,
                    added_at=excluded.added_at
            """, d)
            conn.commit()
        finally:
            conn.close()

    def delete_channel(self, channel_id: str) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
            conn.commit()
        finally:
            conn.close()

    def list_channels(self) -> list[Channel]:
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM channels ORDER BY added_at").fetchall()
            return [Channel.from_dict(dict(r)) for r in rows]
        finally:
            conn.close()

    # ---------- 系统设置 ----------
