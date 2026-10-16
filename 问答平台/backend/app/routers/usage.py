"""用量明细、看板和账单导出。"""

import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.response import ok
from app.deps import get_current_user, get_db
from app.models import LlmUsageRecord, UsageRecord, User
from app.services.usage import summarize
from app.services.metering.service import usage_by_key
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


def _dashboard(db: Session, days: int) -> dict:
    since = datetime.now() - timedelta(days=days)
    rows = db.execute(select(LlmUsageRecord).where(LlmUsageRecord.created_at >= since).order_by(LlmUsageRecord.created_at)).scalars().all()
    total = len([r for r in rows if r.status in ('success', 'error')])
    latencies = [r.latency_ms for r in rows if r.latency_ms > 0]
    return {
        'summary': {
            'total_requests': total,
            'prompt_tokens': sum(r.prompt_tokens for r in rows),
            'completion_tokens': sum(r.completion_tokens for r in rows),
            'avg_latency_ms': round(sum(latencies) / len(latencies), 2) if latencies else 0,
            'p95_latency_ms': sorted(latencies)[max(0, int(len(latencies) * .95) - 1)] if latencies else 0,
            'cache_hit_rate': round(sum(1 for r in rows if r.cache_hit) / total * 100, 2) if total else 0,
            'errors': sum(1 for r in rows if r.status == 'error'),
            'quota_rejected': sum(1 for r in rows if r.status == 'quota_rejected'),
        },
        'by_key': usage_by_key(db, since),
        'trend': [{'date': r.created_at.date().isoformat(), 'requests': sum(1 for x in rows if x.created_at.date() == r.created_at.date() and x.status in ('success', 'error'))} for r in rows[::max(1, len(rows)//30 or 1)]],
    }


@router.get('/dashboard', summary='看板数据')
def dashboard(days: int = Query(30, ge=1, le=365), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ok(_dashboard(db, days))


@router.get('/billing/export', summary='账单 CSV 导出')
def billing_export(days: int = Query(30, ge=1, le=365), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    since = datetime.now() - timedelta(days=days)
    rows = db.execute(select(LlmUsageRecord).where(LlmUsageRecord.created_at >= since).order_by(LlmUsageRecord.id)).scalars().all()
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['id', 'created_at', 'request_id', 'key_id', 'model', 'status', 'prompt_tokens', 'completion_tokens', 'latency_ms', 'cache_hit'])
    for row in rows:
        writer.writerow([row.id, row.created_at.isoformat(), row.request_id, row.key_id, row.model, row.status, row.prompt_tokens, row.completion_tokens, row.latency_ms, row.cache_hit])
    return StreamingResponse(iter([output.getvalue().encode('utf-8-sig')]), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="billing.csv"'})


@router.get('/billing/export.xlsx', summary='账单 Excel 导出')
def billing_export_xlsx(days: int = Query(30, ge=1, le=365), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        from openpyxl import Workbook
    except ImportError:
        return Response('请先安装 openpyxl', status_code=503, media_type='text/plain')
    since = datetime.now() - timedelta(days=days)
    rows = db.execute(select(LlmUsageRecord).where(LlmUsageRecord.created_at >= since).order_by(LlmUsageRecord.id)).scalars().all()
    book = Workbook(); sheet = book.active; sheet.title = 'billing'
    sheet.append(['id', 'created_at', 'request_id', 'key_id', 'model', 'status', 'prompt_tokens', 'completion_tokens', 'latency_ms', 'cache_hit'])
    for row in rows:
        sheet.append([row.id, row.created_at.isoformat(), row.request_id, row.key_id, row.model, row.status, row.prompt_tokens, row.completion_tokens, row.latency_ms, row.cache_hit])
    data = io.BytesIO(); book.save(data); data.seek(0)
    return StreamingResponse(data, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename="billing.xlsx"'})


@router.post('/cache/clear', summary='清理本租户缓存')
def clear_cache(current_user: User = Depends(get_current_user)):
    return ok({'deleted': get_cache().delete_prefix(f't:{current_user.tenant_id}:')})
