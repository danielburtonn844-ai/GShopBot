"""后台任务注册表（v2.7.1）。

问题：旧代码 13 处 `asyncio.create_task(...)` 全部不保存引用。事件循环只持
弱引用，任务对象随时可能被 GC 回收，回收时还会抛 `Task was destroyed but it is
pending!`，导致“图册防抖提交”“派单通知”“群发”这类后台动作静默丢失。

做法：统一走 `spawn()` 拿到引用，放进模块级 set 持留，任务结束后从 set 移除。
退出时 `drain()` 等待在跑的任务完成，避免关进程时任务被斩。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# 持留引用：模块级 set 存活到进程结束，任务不会被 GC
_PENDING: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str = "") -> asyncio.Task:
    """把协程排入事件循环并持留引用，返回 Task。"""
    task = asyncio.get_running_loop().create_task(coro)
    if name:
        try:
            task.set_name(name)
        except AttributeError:
            pass
    _PENDING.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task) -> None:
    _PENDING.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            "后台任务 %s 异常退出: %s: %s",
            task.get_name(),
            type(exc).__name__,
            exc,
        )


def pending_count() -> int:
    """当前持留中的任务数（自检/测试用）。"""
    return len(_PENDING)


async def drain(timeout: float = 10.0) -> None:
    """等待当前在跑的后台任务结束（超时放弃），退出前调用。"""
    if not _PENDING:
        return
    tasks = list(_PENDING)
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning("后台任务 drain 超时，仍有 %d 个未完成", pending_count())
