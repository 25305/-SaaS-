"""数据库、模型服务及缓存的有界健康检查（FR-G2）。"""

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.db.session import engine
from app.services.cache.backend import get_cache
from app.services.model.backend import probe_model

router = APIRouter(tags=["运维"])


def _probe_db():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "up"}


def _probe_cache():
    cache = get_cache()
    cache.set("health:probe", "1", ttl=5)
    if cache.get("health:probe") != "1":
        return {"status": "degraded", "backend": "local-memory"}
    return {"status": "up", "backend": "local-memory"}


async def _bounded(name, probe):
    try:
        result = await asyncio.wait_for(probe, timeout=settings.HEALTH_TIMEOUT_SECONDS)
        return result
    except asyncio.TimeoutError:
        return {"status": "down", "reason": "timeout"}
    except Exception:
        # 不向匿名健康检查调用者暴露异常文本（连接串、凭证等）。
        return {"status": "down", "reason": name + "_unavailable"}


@router.get("/health", summary="依赖健康检查")
async def health():
    database, model, cache = await asyncio.gather(
        _bounded("database", asyncio.to_thread(_probe_db)),
        _bounded("model_service", probe_model()),
        _bounded("cache", asyncio.to_thread(_probe_cache)),
    )
    deps = {"database": database, "model_service": model, "cache": cache}
    healthy = all(dep["status"] == "up" for dep in deps.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "dependencies": deps},
    )
