# -*- coding: utf-8 -*-
"""账单接口(模块 F,FR-F4、FR-F5)。

- `GET /api/v1/billing/summary` 账单合计(按日汇总,只读 usage_summary);
- `GET /api/v1/billing/daily`   按日明细行(只读 usage_summary);
- `GET /api/v1/billing/detail`  流水明细(有界区间,用于账单页"明细表");
- `GET /api/v1/billing/export`  导出 CSV / Excel(导出的每一行都带 request_id,可逐行回查);
- `GET /api/v1/billing/verify`  自动化可追溯校验(FR-F5),无需人工抽查。

租户条件由 `app/db/tenant_hook.py` 自动注入,本文件的查询都不手写 tenant_id。
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.response import ok
from app.db.session import get_db
from app.deps import get_current_context
from app.services.billing.service import (
    DEFAULT_DETAIL_LIMIT,
    MAX_DETAIL_LIMIT,
    build_bill,
    daily_rows,
    detail_rows,
    export_bill,
    parse_period,
)
from app.services.billing.trace import verify_bill_traceable

router = APIRouter(prefix="/api/v1/billing", tags=["账单"])


def _period_bounds(period: str) -> tuple[date, date]:
    return parse_period(period)


@router.get("/summary", summary="账单合计")
def billing_summary(
    period: str = Query(default="month", description="today/week/month/YYYY-MM/YYYY-MM-DD"),
    ctx: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    start, end = _period_bounds(period)
    return ok(build_bill(db, start, end))


@router.get("/daily", summary="账单按日汇总")
def billing_daily(
    period: str = Query(default="month"),
    ctx: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    start, end = _period_bounds(period)
    rows = daily_rows(db, start, end)
    return ok(
        [
            {
                "date": row.bucket_date.isoformat(),
                "requests": row.requests,
                "success": row.success,
                "errors": row.errors,
                "prompt_tokens": row.prompt_tokens,
                "completion_tokens": row.completion_tokens,
                "total_tokens": row.prompt_tokens + row.completion_tokens,
                "avg_latency_ms": row.avg_latency_ms,
                "p95_latency_ms": row.p95_latency_ms,
                "cache_hits": row.cache_hits,
            }
            for row in rows
        ]
    )


@router.get("/detail", summary="账单流水明细(有界)")
def billing_detail(
    period: str = Query(default="month"),
    limit: int = Query(default=DEFAULT_DETAIL_LIMIT, ge=1, le=MAX_DETAIL_LIMIT),
    ctx: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    start, end = _period_bounds(period)
    return ok(detail_rows(db, start, end, limit=limit))


@router.get("/verify", summary="账单可追溯校验(FR-F5,自动化)")
def billing_verify(
    period: str = Query(default="month"),
    ctx: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    # 抽查变成断言:账单每个数字都能在 usage_record 中找到对应记录
    return ok(verify_bill_traceable(db, tenant_id=ctx.tenant_id, period=period))


@router.get("/export", summary="导出账单 CSV/Excel")
def billing_export(
    period: str = Query(default="month"),
    fmt: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    limit: int = Query(default=DEFAULT_DETAIL_LIMIT, ge=1, le=MAX_DETAIL_LIMIT),
    ctx: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    start, end = _period_bounds(period)
    content, media_type, filename = export_bill(db, start, end, fmt=fmt, limit=limit)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
