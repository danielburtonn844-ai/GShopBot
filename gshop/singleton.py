"""Single instance lock (audit P2-14).

Telegram long polling allows exactly one getUpdates consumer per token. A
second process steals updates from the first, and both then credit the same
recharge twice. flock is used instead of a pid file: the kernel drops the lock
when the process dies, so a crash never leaves a stale lock.

Also provides write_health() for the Docker HEALTHCHECK.
"""
from __future__ import annotations

import atexit
import fcntl
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_lock_fd: int | None = None


class AlreadyRunning(RuntimeError):
    pass


def acquire(data_dir: str = "data", name: str = "gshop.lock") -> Path:
    global _lock_fd
    path = Path(data_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise AlreadyRunning(str(path)) from None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode("ascii"))
    os.fsync(fd)
    _lock_fd = fd
    atexit.register(release)
    logger.info("单实例锁已获取：%s（pid=%s）", path, os.getpid())
    return path


def release() -> None:
    global _lock_fd
    if _lock_fd is None:
        return
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(_lock_fd)
    except OSError:
        pass
    _lock_fd = None


def write_health(path: str | None, payload: str) -> None:
    if not path:
        return
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(payload, encoding="utf-8")
    except OSError as exc:
        logger.warning("写健康文件失败：%s", exc)


__all__ = ["AlreadyRunning", "acquire", "release", "write_health"]
