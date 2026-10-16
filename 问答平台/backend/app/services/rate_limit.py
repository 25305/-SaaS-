"""租户/API Key 滑动窗口限流。开发环境使用进程内存，生产可替换为 Redis。"""

from collections import defaultdict, deque
from threading import Lock
from time import monotonic


class SlidingWindowLimiter:
    def __init__(self):
        self._events = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, limit: int | None, window: int) -> tuple[bool, int]:
        if not limit or limit <= 0:
            return True, 0
        now = monotonic()
        with self._lock:
            bucket = self._events[key]
            while bucket and now - bucket[0] >= window:
                bucket.popleft()
            if len(bucket) >= limit:
                retry = max(1, int(window - (now - bucket[0])))
                return False, retry
            bucket.append(now)
            return True, 0


limiter = SlidingWindowLimiter()


def enforce(
    tenant_id: int,
    key_id: int | None,
    per_minute: int | None,
    per_hour: int | None,
    *,
    tenant_per_minute: int | None,
    tenant_per_hour: int | None,
):
    """先限制租户总量，再限制单 Key；JWT 调用也受租户总量保护。"""
    tenant_identity = f"tenant:{tenant_id}"
    ok, retry = limiter.check(tenant_identity + ':minute', tenant_per_minute, 60)
    if not ok:
        return False, retry, 'tenant-minute'
    ok, retry = limiter.check(tenant_identity + ':hour', tenant_per_hour, 3600)
    if not ok:
        return False, retry, 'tenant-hour'
    if key_id is None:
        return True, 0, None
    identity = f"tenant:{tenant_id}:key:{key_id}"
    ok, retry = limiter.check(identity + ':minute', per_minute, 60)
    if not ok:
        return False, retry, 'key-minute'
    ok, retry = limiter.check(identity + ':hour', per_hour, 3600)
    if not ok:
        return False, retry, 'key-hour'
    return True, 0, None
