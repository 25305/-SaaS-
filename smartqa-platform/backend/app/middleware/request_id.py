# -*- coding: utf-8 -*-
"""请求上下文中间件(模块 G,FR-G1)。

职责:
1. 为每个请求分配 request_id(优先复用上游传入的 X-Request-Id),写入 ContextVar,
   使该请求内所有日志自动串联;
2. 请求结束后输出一条结构化访问日志,字段:
   request_id / tenant_id / key_prefix / path / method / status_code / elapsed_ms;
3. 把 request_id 回写到响应头 X-Request-Id,方便前端/网关对账;
4. 记录请求用量(仅已认证的 /api/ 请求),供 FR-B4 的用量隔离演示。

安全红线:
- 日志只记录 API Key 的【前缀】(如 sk-abc123),绝不记录完整 Key;
- 不记录请求体与 Authorization 头,避免密码/密钥落日志。
"""

import re
import time
import uuid

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import logger, request_id_var
from app.services.usage import record_request_usage


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming_id = request.headers.get("X-Request-Id", "")
        request_id = incoming_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", incoming_id) else uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        request.state.tenant_id = None      # 由 deps.get_current_user 认证后写入
        request.state.key_prefix = None     # 预留:API Key 鉴权模块写入前缀
        token = request_id_var.set(request_id)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            # 响应不含异常文本（连接串或密钥）；仍携带 request_id 方便运维定位。
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            self._log(request, request_id, 500, elapsed_ms, exc_type=type(exc).__name__)
            request_id_var.reset(token)
            return JSONResponse(
                status_code=500,
                content={"code": "INTERNAL_ERROR", "message": "服务器内部错误", "data": None},
                headers={"X-Request-Id": request_id},
            )
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        self._log(request, request_id, response.status_code, elapsed_ms)
        response.headers["X-Request-Id"] = request_id
        request_id_var.reset(token)

        # 用量记录:仅已认证的 /api/ 请求(失败鉴权时 tenant_id 为空,自然跳过)
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is not None and request.url.path.startswith("/api/"):
            await run_in_threadpool(
                record_request_usage,
                tenant_id=tenant_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                latency_ms=elapsed_ms,
            )
        return response

    @staticmethod
    def _log(request: Request, request_id: str, status_code: int, elapsed_ms: int, exc_type: str = "") -> None:
        fields = {
            "request_id": request_id,
            "tenant_id": getattr(request.state, "tenant_id", None),
            "key_prefix": getattr(request.state, "key_prefix", None),  # 只记前缀
            "path": request.url.path,
            "method": request.method,
            "status_code": status_code,
            "elapsed_ms": elapsed_ms,
            "client_ip": request.client.host if request.client else None,
        }
        if exc_type:
            fields["exception_type"] = exc_type
            logger.error("http_request", extra={"fields": fields})
        else:
            logger.info("http_request", extra={"fields": fields})
