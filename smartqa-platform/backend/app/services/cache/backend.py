# -*- coding: utf-8 -*-
"""缓存后端(模块 G,FR-G2 依赖探测)。

开发期用进程内内存缓存;生产可替换为 Redis(保持同一接口)。
关键约定:缓存 key 必须带租户前缀(tenant_key),否则会出现
"租户 B 读到租户 A 的答案"的跨租户串数据故障(见 docs/运维手册.md 场景 5)。
"""

import threading
import time
from typing import Optional


class InMemoryCache:
    """线程安全的进程内 TTL 缓存。"""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, Optional[float]]] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        expires_at = time.time() + ttl if ttl else None
        with self._lock:
            self._data[key] = (value, expires_at)

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, expires_at = item
            if expires_at is not None and expires_at < time.time():
                self._data.pop(key, None)
                return None
            return value

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        """按前缀清理(用于清空某租户的全部缓存)。返回删除条数。"""
        with self._lock:
            keys = [k for k in self._data if k.startswith(prefix)]
            for k in keys:
                self._data.pop(k, None)
            return len(keys)

    def ping(self) -> bool:
        return True


_cache = InMemoryCache()


def get_cache() -> InMemoryCache:
    return _cache


def tenant_key(tenant_id: int, *parts: str) -> str:
    """构造带租户前缀的缓存 key,从源头杜绝跨租户串数据。"""
    return "t:%s:%s" % (tenant_id, ":".join(parts))
