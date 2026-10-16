"""用量明细、物化看板和账单导出。"""

import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.response import ok
from app.deps import get_current_user, get_db
from app.models import ApiKey, LlmUsageRecord, UsageKeySummary, UsageLatencyBucket, UsageRecord, UsageSummary, User
from app.services.billing.trace import verify_bill_traceable
from app.services.cache.backend import invalidate_tenant
from app.services.usage import summarize

router = APIRouter(prefix="/api/usage", tags=["用量"])


@router.get("/records", summary="本租户 HTTP 访问明细")
def records(limit: int = Query(default=50, ge=1, le=200), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(UsageRecord).order_by(UsageRecord.id.desc()).limit(limit)).scalars().all()
    return ok([{"id": row.id, "method": row.method, "path": row.path, "status_code": row.status_code,
                "latency_ms": row.latency_ms, "created_at": row.created_at.isoformat()} for row in rows])


@router.get("/summary", summary="本租户 HTTP 用量汇总")
def summary(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ok(summarize(db))


def _dashboard(db: Session, days: int) -> dict:
    """只读物化汇总表；绝不扫描 usage_record。"""
    since_day = (datetime.now() - timedelta(days=days - 1)).date()
    daily = db.execute(
        select(UsageSummary).where(UsageSummary.bucket_date >= since_day).order_by(UsageSummary.bucket_date)
    ).scalars().all()
    total = sum(item.requests for item in daily)
    errors = sum(item.errors for item in daily)
    prompt = sum(item.prompt_tokens for item in daily)
    completion = sum(item.completion_tokens for item in daily)
    cache_hits = sum(item.cache_hits for item in daily)
    weighted_latency = sum(item.avg_latency_ms * item.requests for item in daily)
    key_rows = db.execute(select(UsageKeySummary).where(UsageKeySummary.bucket_date >= since_day)).scalars().all()
    keys = {key.id: key for key in db.execute(select(ApiKey)).scalars().all()}
    by_key: dict[int, dict] = {}
    for item in key_rows:
        key = keys.get(item.key_id)
        target = by_key.setdefault(item.key_id, {
            "key_id": item.key_id or None,
            "key_prefix": "JWT" if item.key_id == 0 else key.key_prefix if key else "已删除 Key",
            "name": "登录态" if item.key_id == 0 else key.name if key else "",
            "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cache_hits": 0,
        })
        target["requests"] += item.requests
        target["prompt_tokens"] += item.prompt_tokens
        target["completion_tokens"] += item.completion_tokens
        target["total_tokens"] += item.prompt_tokens + item.completion_tokens
        target["cache_hits"] += item.cache_hits
    labels = ("0-500ms", "500ms-1s", "1s-2s", "2s-5s", "5s+")
    distributions = {label: 0 for label in labels}
    for item in db.execute(select(UsageLatencyBucket).where(UsageLatencyBucket.bucket_date >= since_day)).scalars():
        distributions[item.bucket] = distributions.get(item.bucket, 0) + item.count
    total_latency_samples = sum(distributions.values())
    p95_rank = max(1, int(total_latency_samples * .95 + .9999))
    running = 0
    p95_upper_bound = 0
    for label, upper in (("0-500ms", 500), ("500ms-1s", 1000), ("1s-2s", 2000), ("2s-5s", 5000), ("5s+", 5000)):
        running += distributions[label]
        if running >= p95_rank:
            p95_upper_bound = upper
            break
    return {
        "summary": {
            "total_requests": total, "success": sum(item.success for item in daily), "errors": errors,
            "error_rate": round(errors / total * 100, 2) if total else 0,
            "prompt_tokens": prompt, "completion_tokens": completion,
            "avg_latency_ms": round(weighted_latency / total, 2) if total else 0,
            "p95_latency_ms": p95_upper_bound,
            "cache_hit_rate": round(cache_hits / total * 100, 2) if total else 0,
            "updated_at": max((item.updated_at.isoformat() for item in daily), default=None),
        },
        "trend": [{"date": item.bucket_date.isoformat(), "requests": item.requests,
                   "prompt_tokens": item.prompt_tokens, "completion_tokens": item.completion_tokens,
                   "avg_latency_ms": item.avg_latency_ms} for item in daily],
        "by_key": list(by_key.values()),
        "latency_distribution": [{"bucket": label, "count": count} for label, count in distributions.items()],
    }


@router.get("/dashboard", summary="物化看板数据")
def dashboard(days: int = Query(30, ge=1, le=365), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ok(_dashboard(db, days))


def _billing_rows(db: Session, days: int):
    since = datetime.now() - timedelta(days=days)
    return db.execute(select(LlmUsageRecord).where(LlmUsageRecord.created_at >= since).order_by(LlmUsageRecord.id.desc())).scalars().all()


def _bill_dict(row: LlmUsageRecord) -> dict:
    return {"id": row.id, "created_at": row.created_at.isoformat(), "request_id": row.request_id,
            "key_id": row.key_id, "model": row.model, "status": row.status,
            "prompt_tokens": row.prompt_tokens, "completion_tokens": row.completion_tokens,
            "latency_ms": row.latency_ms, "cache_hit": row.cache_hit}


@router.get("/billing/records", summary="账单明细")
def billing_records(days: int = Query(30, ge=1, le=365), limit: int = Query(100, ge=1, le=500), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ok([_bill_dict(row) for row in _billing_rows(db, days)[:limit]])


@router.get("/billing/export", summary="账单 CSV 导出")
def billing_export(days: int = Query(30, ge=1, le=365), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = [_bill_dict(row) for row in _billing_rows(db, days)]
    headers = list(rows[0]) if rows else ["id", "created_at", "request_id", "key_id", "model", "status", "prompt_tokens", "completion_tokens", "latency_ms", "cache_hit"]
    output = io.StringIO(newline=""); writer = csv.DictWriter(output, fieldnames=headers); writer.writeheader()
    for row in rows: writer.writerow(row)
    return StreamingResponse(iter([output.getvalue().encode("utf-8-sig")]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=billing.csv"})


@router.get("/billing/export.xlsx", summary="账单 Excel 导出")
def billing_export_xlsx(days: int = Query(30, ge=1, le=365), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from openpyxl import Workbook
    rows = [_bill_dict(row) for row in _billing_rows(db, days)]
    headers = list(rows[0]) if rows else ["id", "created_at", "request_id", "key_id", "model", "status", "prompt_tokens", "completion_tokens", "latency_ms", "cache_hit"]
    book = Workbook(); sheet = book.active; sheet.title = "billing"; sheet.append(headers)
    for row in rows: sheet.append([row[column] for column in headers])
    data = io.BytesIO(); book.save(data); data.seek(0)
    return StreamingResponse(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=billing.xlsx"})


@router.get("/billing/verify", summary="账单可追溯性抽样校验")
def billing_verify(days: int = Query(30, ge=1, le=365), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ok(verify_bill_traceable(db, tenant_id=current_user.tenant_id, days=days))


@router.post("/cache/clear", summary="清理本租户缓存")
def clear_cache(current_user: User = Depends(get_current_user)):
    return ok({"deleted": invalidate_tenant(current_user.tenant_id)})
