"""用量明细与汇总（FR-B4）；查询的租户条件由 ORM 钩子统一注入。"""

import csv
import io
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.response import ok
from app.deps import get_current_user, get_db, require_admin
from app.models import UsageRecord, User
from app.services.usage import summarize
from app.services.rate_limit import enforce_rate_limit
from app.services.cache.backend import get_cache

router = APIRouter(prefix="/api/usage", tags=["用量"])


def _rows_for_tenant(db: Session, tenant_id: int):
    # tenant_hook 会再次注入租户条件；这里的 tenant_id 仅用于显式上下文设置。
    from app.db.tenant_hook import TENANT_ID_KEY
    db.info[TENANT_ID_KEY] = tenant_id
    return db.execute(select(UsageRecord).order_by(UsageRecord.id.desc())).scalars().all()


@router.get("/records", summary="本租户用量明细")
def records(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_rate_limit(request, current_user.tenant_id)
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
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_rate_limit(request, current_user.tenant_id)
    return ok(summarize(db))


@router.get("/dashboard", summary="看板完整指标")
def dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_rate_limit(request, current_user.tenant_id)
    return ok(summarize(db))


@router.get("/billing.csv", summary="导出 CSV 账单")
def billing_csv(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = _rows_for_tenant(db, current_user.tenant_id)
    output = io.StringIO()
    fields = ["id", "api_key_id", "method", "path", "status_code", "latency_ms", "created_at"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: getattr(row, field) for field in fields})
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=usage-billing.csv"})


@router.get("/billing.xlsx", summary="导出 Excel 账单")
def billing_xlsx(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from openpyxl import Workbook
    rows = _rows_for_tenant(db, current_user.tenant_id)
    fields = ["id", "api_key_id", "method", "path", "status_code", "latency_ms", "created_at"]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "usage-billing"
    sheet.append(fields)
    for row in rows:
        sheet.append([getattr(row, field) for field in fields])
    output = io.BytesIO()
    workbook.save(output)
    return Response(output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=usage-billing.xlsx"})


@router.delete("/cache", summary="清理当前租户缓存")
def clear_cache(current_user: User = Depends(require_admin)):
    removed = get_cache().delete_prefix(f"t:{current_user.tenant_id}:")
    return ok({"removed": removed})


@router.post("/rebuild-summary", status_code=202, summary="提交后台汇总任务")
def rebuild_summary(background_tasks: BackgroundTasks, current_user: User = Depends(require_admin)):
    # 当前统计从不可变流水派生；后台任务入口为后续物化汇总表预留。
    background_tasks.add_task(lambda: None)
    return ok({"status": "accepted"}, "汇总任务已提交")
