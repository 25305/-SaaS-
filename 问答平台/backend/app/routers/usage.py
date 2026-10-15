"""用量明细与汇总（FR-B4）；查询的租户条件由 ORM 钩子统一注入。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.response import ok
from app.deps import get_current_user, get_db
from app.models import UsageRecord, User
from app.services.usage import summarize

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
