"""模块 E：租户与 API 请求限流。

开发期使用进程内滑动窗口；生产环境可把后端替换为 Redis 实现。
"""
from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import Request
from app.core.errors import BizError

WINDOW_SECONDS = 60.0
TENANT_LIMIT = 60
PATH_LIMIT = 30
_windows: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def enforce_rate_limit(request: Request, tenant_id: int) -> None:
    """滑动窗口限流；同一租户按全局和接口两个维度限制。"""
    now = monotonic()
    buckets = (f"tenant:{tenant_id}", f"tenant:{tenant_id}:path:{request.url.path}")
    with _lock:
        for bucket, limit in ((buckets[0], TENANT_LIMIT), (buckets[1], PATH_LIMIT)):
            window = _windows[bucket]
            while window and now - window[0] >= WINDOW_SECONDS:
                window.popleft()
            if len(window) >= limit:
                raise BizError("RATE_LIMITED", "请求过于频繁，请稍后重试", 429)
            window.append(now)


def reset_rate_limits() -> None:
    with _lock:
        _windows.clear()
