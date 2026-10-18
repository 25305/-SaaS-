# -*- coding: utf-8 -*-
"""模型服务探测(模块 G,FR-G2 依赖探测)。

- 未配置 MODEL_BASE_URL 时使用内置 local-stub(始终可用),便于本地开发;
- 配置后对上游做一次极短超时的真实探测;
- 探测本身必须可被超时中断,避免健康检查被拖死。
"""

import asyncio

import httpx

from app.config import settings


async def probe_model() -> dict:
    """返回 {"status": "up"/"down", ...}。异常由调用方捕获。"""
    if not settings.MODEL_BASE_URL:
        # 尚未接入真实模型；不可将桩服务谎报为真实依赖正常。
        return {"status": "unconfigured", "provider": "none"}

    headers = {}
    if settings.MODEL_API_KEY:
        headers["Authorization"] = "Bearer " + settings.MODEL_API_KEY
    async with httpx.AsyncClient(timeout=settings.MODEL_TIMEOUT_SECONDS) as client:
        # 探针走 OpenAI 兼容面里的 /models(与 /chat/completions 同一协议族):
        # 真实上游(如阿里云百炼)没有 /health,而 /models 在兼容实现里普遍存在。
        resp = await client.get(settings.MODEL_BASE_URL.rstrip("/") + "/models", headers=headers)
        # 只有 2xx 能证明探针可用；401/404/5xx 均为不可用。
        if not 200 <= resp.status_code < 300:
            return {"status": "down", "http_status": resp.status_code}
        return {"status": "up", "http_status": resp.status_code}
