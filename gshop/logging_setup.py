"""结构化日志（审计 P2-13）。

原来只有一行 `logging.basicConfig(format=..., level=INFO)`，三个后果：
  1. 全部输出到 stderr，容器/系统没有 collector 时这些日志直接丢失；
  2. 没有轮转，跑上几周日志文件无限增长（如果重定向了的话）；
  3. 纯文本，机器无法按字段过滤——排查「某个订单为什么没到账」只能 grep 关键词。

现在：
  - JSON Lines 输出（默认写 LOG_DIR/app.log，带 RotatingFileHandler 轮转）；
  - 同时保留一份人类可读的 stderr 输出，本地前台跑仍然好读；
  - 关键事件用 `log_event(logger, event, **fields)` 带 order_id / uid / gateway 等字段；
  - 用 contextvars 传播 request_id（每次 update 一个），同一请求的多条日志可串联。
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

from .db.base import LOCAL_TZ

# 当前请求/任务的关联 ID。update 进来时 set，处理链路上所有日志自动带上。
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)) or 10 * 1024 * 1024)
_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5") or 5)

# 这些属性是 LogRecord 自带的，不算业务字段
_STD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName",
}


class JsonFormatter(logging.Formatter):
    """把一条日志打成单行 JSON：固定字段 + extra 里的业务字段。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, LOCAL_TZ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "rid": request_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key in _STD_ATTRS or key.startswith("_") or key in payload:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    """stderr 用的人读格式，带关联 ID 方便和 JSON 日志对照。"""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        rid = request_id_var.get()
        return base if rid == "-" else f"[rid={rid}] {base}"


def setup_logging(log_dir: str | Path = "logs", level: int = logging.INFO) -> Path:
    """装配根 logger：JSON 文件（轮转）+ 人读 stderr。返回日志文件路径。

    LOG_JSON=0 可以退回纯文本文件，方便本地临时排查。
    """
    out_dir = Path(log_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_file = out_dir / "app.log"

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(level)
    if os.getenv("LOG_JSON", "1") == "0":
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    else:
        file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(PlainFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root.addHandler(stream_handler)

    # httpx 每次轮询都打一条 INFO，会把业务日志冲掉
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    return log_file


def new_request_id() -> str:
    """给一次 update / 一次后台任务生成 8 位关联 ID。"""
    return uuid.uuid4().hex[:8]


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    """关键业务事件：event 名 + 结构化字段。

    例：log_event(logger, "order.created", order_id="20261105-001", uid=123, points=50)
    """
    logger.info(event, extra={"event": event, **fields})


__all__ = [
    "JsonFormatter",
    "PlainFormatter",
    "request_id_var",
    "setup_logging",
    "new_request_id",
    "log_event",
]
