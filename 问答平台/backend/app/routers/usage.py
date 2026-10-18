"""兼容接口:用量明细、用量汇总与缓存清理。

历史遗留的无界全表扫描接口(/api/usage/dashboard、/api/usage/billing/export[.xlsx])
已移除,前端模块 H 改用有界的 /api/v1/dashboard 与 /api/v1/billing
(见 docs/接口文档.md §9/§10,满足 FR-F6「禁止全表扫描」)。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.response import ok
from app.deps import get_current_user, get_db
from app.models import UsageRecord, User
from app.services.usage import summarize
from app.services.cache.backend import get_cache

router = APIRouter(prefix="/api/usage", tags=["用量"])


@router.get("/records", summary="本租户用量明细")
def records(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(UsageRecord).order_by(UsageRecord.id.desc()).limit(limit)
    ).scalars().all()
    return ok([
        {
            "id": row.id,
            "method": row.method,
            "path": row.path,
            "status_code": row.status_code,
            "latency_ms": row.latency_ms,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ])


@router.get("/summary", summary="本租户用量汇总")
def summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ok(summarize(db))


@router.post('/cache/clear', summary='清理本租户缓存')
def clear_cache(current_user: User = Depends(get_current_user)):
    return ok({'deleted': get_cache().delete_prefix(f't:{current_user.tenant_id}:')})
