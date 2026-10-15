# -*- coding: utf-8 -*-
"""结构化日志(模块 G,FR-G1)。

设计要点:
1. 每条日志是一行 JSON,便于日志系统按字段检索与串联:
   `request_id` 把同一请求内的所有日志串起来排查;
2. `request_id` 通过 ContextVar 传递,请求中间件写入后,
   该请求内任何模块打印的日志都自动带上,无需层层传参;
3. 业务字段统一通过 `log_event(..., **fields)` 或 `extra={"fields": {...}}` 传入,
   格式化器会把它展开进 JSON 顶层,避免污染 LogRecord 内置属性;
4. 【安全红线】绝不打印完整 API Key 或密码:Key 只允许记录前缀(如 sk-abc123),
   见 app/middleware/request_id.py 与 docs/运维手册.md。
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from app.config import settings

# 请求级上下文:同一请求内所有日志自动携带 request_id
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# LogRecord 的内置属性,序列化时排除,避免把日志框架内部字段写进 JSON
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "fields", "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    """把一条日志渲染成单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", None)
        payload: dict = {}
        if isinstance(fields, dict):
            # 业务字段先展开(tenant_id / key_prefix / path / status_code ...)
            payload.update(fields)

        # 框架字段最后写入,保证不被业务字段覆盖
        payload["ts"] = datetime.fromtimestamp(
            record.created, timezone.utc
        ).astimezone().isoformat(timespec="milliseconds")
        payload["level"] = record.levelname
        payload["logger"] = record.name
        payload["event"] = record.getMessage()

        # request_id:优先取显式传入的,否则取上下文中的
        if not payload.get("request_id"):
            payload["request_id"] = getattr(record, "request_id", None) or request_id_var.get()

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class PlainFormatter(logging.Formatter):
    """LOG_JSON=false 时的可读文本格式,字段以 k=v 追加在行尾。"""

    def format(self, record: logging.LogRecord) -> str:
        base = "%s %-5s %s | %s" % (
            datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            record.levelname,
            record.name,
            record.getMessage(),
        )
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            base += " | " + " ".join("%s=%s" % (k, v) for k, v in fields.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


logger = logging.getLogger("app")

_configured = False


def setup_logging() -> None:
    """配置根日志与 uvicorn 日志,幂等(多次调用只生效一次)。"""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.LOG_JSON else PlainFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.LOG_LEVEL)

    # uvicorn 的日志也统一走同一 handler,避免格式不一致
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False

    # 请求访问日志由 RequestContextMiddleware 统一输出(字段更全),
    # 关掉 uvicorn 自带的 access 日志,避免重复打印
    logging.getLogger("uvicorn.access").disabled = True

    _configured = True


def log_event(event: str, level: str = "info", **fields) -> None:
    """打印一条结构化日志。字段值请勿包含完整密钥。"""
    getattr(logger, level)(event, extra={"fields": fields})
