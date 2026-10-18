from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.dashboard import resolve_range
from app.core.errors import BizError
from app.core.response import ok
from app.deps import get_db, require_super_admin
from app.models import Role, Tenant, UsageSummary, User
from app.schemas import TenantCreate, TenantInfo, TenantQuotaUpdate
from app.security import hash_password

router = APIRouter(prefix="/api/admin", tags=["平台管理"])


def _tenant_info(tenant: Tenant, user_count: int) -> TenantInfo:
    return TenantInfo(
        id=tenant.id,
        code=tenant.code,
        name=tenant.name,
        is_active=tenant.is_active,
        user_count=user_count,
        quota_monthly_tokens=tenant.quota_monthly_tokens,
        quota_monthly_requests=tenant.quota_monthly_requests,
        overage_policy=tenant.overage_policy,
    )


@router.get("/tenants", summary="租户列表", description="列出全部租户及成员数量（平台管理员操作）。")
def list_tenants(
    current_user: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Tenant, func.count(User.id))
        .outerjoin(User, User.tenant_id == Tenant.id)
        .group_by(Tenant.id)
        .order_by(Tenant.id)
    ).all()
    return ok([_tenant_info(t, count) for t, count in rows])


@router.post("/tenants", summary="创建租户", description="创建租户并同时创建其首个管理员（平台管理员操作）。")
def create_tenant(
    req: TenantCreate,
    current_user: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    if db.execute(select(Tenant.id).where(Tenant.code == req.code)).first():
        raise BizError("TENANT_EXISTS", "租户编码已存在", 409)
    if db.execute(select(User.id).where(User.username == req.admin_username)).first():
        raise BizError("USERNAME_EXISTS", "用户名已存在", 409)

    admin_role = db.execute(select(Role).where(Role.code == "admin")).scalar_one()
    tenant = Tenant(code=req.code, name=req.name)
    db.add(tenant)
    db.flush()
    db.add(
        User(
            tenant_id=tenant.id,
            username=req.admin_username,
            password_hash=hash_password(req.admin_password),
            email=req.admin_email,
            role_id=admin_role.id,
            is_active=True,
        )
    )
    db.commit()
    return ok({"tenant_id": tenant.id}, message="租户创建成功")


@router.patch(
    "/tenants/{tenant_id}/quota",
    summary="设置租户配额与超额策略",
    description="设置租户的月度 Token/请求次数上限与超额行为（FR-D1/FR-D6，平台管理员操作）。",
)
def set_tenant_quota(
    tenant_id: int,
    req: TenantQuotaUpdate,
    current_user: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise BizError("TENANT_NOT_FOUND", "租户不存在", 404)

    # PATCH 语义:只更新显式传入的字段;显式传 null 表示"该维度不限制"。
    # overage_policy 的合法取值已由 TenantQuotaUpdate 的 Literal 约束。
    updates = req.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(tenant, field, value)
    db.commit()
    return ok(
        {
            "tenant_id": tenant.id,
            "code": tenant.code,
            "quota_monthly_tokens": tenant.quota_monthly_tokens,
            "quota_monthly_requests": tenant.quota_monthly_requests,
            "overage_policy": tenant.overage_policy,
        },
        message="租户配额已更新",
    )


def _set_active(tenant_id: int, active: bool, db: Session):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise BizError("TENANT_NOT_FOUND", "租户不存在", 404)
    if tenant.code == "platform":
        # 平台租户停用会把唯一的超管锁在门外，禁止操作
        raise BizError("FORBIDDEN", "平台租户不允许停用或启用", 403)
    tenant.is_active = active
    db.commit()
    return ok(message="租户已停用" if not active else "租户已启用")


@router.post("/tenants/{tenant_id}/disable", summary="停用租户", description="停用后该租户所有成员的登录与请求立即失效（平台管理员操作）。")
def disable_tenant(
    tenant_id: int,
    current_user: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    return _set_active(tenant_id, False, db)


@router.post("/tenants/{tenant_id}/enable", summary="启用租户", description="重新启用已停用的租户（平台管理员操作）。")
def enable_tenant(
    tenant_id: int,
    current_user: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    return _set_active(tenant_id, True, db)


@router.get(
    "/usage/overview",
    summary="全局用量概览",
    description="平台管理员视角的跨租户用量合计与按租户明细（FR-A2/FR-F2）。标量指标只读 usage_summary。",
)
def usage_overview(
    range_: str = Query(default="month", alias="range", description="today/week/month/custom"),
    start: date | None = Query(default=None, description="自定义区间起始日(range=custom 时必填)"),
    end: date | None = Query(default=None, description="自定义区间截止日(range=custom 时必填)"),
    current_user: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    start_day, end_day = resolve_range(range_, start, end)

    # 跨租户读用量:require_super_admin 已在本请求内开启 bypass,故不注入租户条件。
    # 只读汇总表 usage_summary(FR-F6),按 tenant_id 分组,接口耗时不随流水量增长。
    aggregates = db.execute(
        select(
            UsageSummary.tenant_id,
            func.coalesce(func.sum(UsageSummary.requests), 0),
            func.coalesce(func.sum(UsageSummary.success), 0),
            func.coalesce(func.sum(UsageSummary.errors), 0),
            func.coalesce(func.sum(UsageSummary.prompt_tokens), 0),
            func.coalesce(func.sum(UsageSummary.completion_tokens), 0),
            func.coalesce(func.sum(UsageSummary.cache_hits), 0),
            func.coalesce(func.sum(UsageSummary.avg_latency_ms * UsageSummary.requests), 0),
            func.coalesce(func.max(UsageSummary.p95_latency_ms), 0),
        )
        .select_from(UsageSummary)
        .where(UsageSummary.bucket_date >= start_day, UsageSummary.bucket_date < end_day)
        .group_by(UsageSummary.tenant_id)
    ).all()
    usage_by_tenant = {row[0]: row[1:] for row in aggregates}

    tenants = db.execute(select(Tenant).order_by(Tenant.id)).scalars().all()

    by_tenant = []
    totals = {
        "total_requests": 0,
        "success": 0,
        "errors": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_hits": 0,
        "weighted_latency": 0,
        "p95_latency_ms": 0,
    }
    for tenant in tenants:
        row = usage_by_tenant.get(tenant.id)
        requests, success, errors, prompt, completion, cache_hits, weighted, p95 = (
            row if row is not None else (0, 0, 0, 0, 0, 0, 0, 0)
        )
        requests = int(requests)
        prompt = int(prompt)
        completion = int(completion)
        totals["total_requests"] += requests
        totals["success"] += int(success)
        totals["errors"] += int(errors)
        totals["prompt_tokens"] += prompt
        totals["completion_tokens"] += completion
        totals["total_tokens"] += prompt + completion
        totals["cache_hits"] += int(cache_hits)
        totals["weighted_latency"] += int(weighted)
        totals["p95_latency_ms"] = max(totals["p95_latency_ms"], int(p95))

        limit_tokens = tenant.quota_monthly_tokens
        by_tenant.append(
            {
                "tenant_id": tenant.id,
                "code": tenant.code,
                "name": tenant.name,
                "is_active": tenant.is_active,
                "requests": requests,
                "success": int(success),
                "errors": int(errors),
                "error_rate": round(int(errors) / requests, 4) if requests else 0.0,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "cache_hits": int(cache_hits),
                "avg_latency_ms": round(int(weighted) / requests) if requests else 0,
                "p95_latency_ms": int(p95),
                "quota_monthly_tokens": limit_tokens,
                "quota_monthly_requests": tenant.quota_monthly_requests,
                "overage_policy": tenant.overage_policy,
                # 用量占比便于看板直观展示;配额为 null(不限制)时该值为 null
                "token_usage_ratio": (
                    round((prompt + completion) / limit_tokens, 4) if limit_tokens else None
                ),
            }
        )

    total_requests = totals["total_requests"]
    return ok(
        {
            "range": {
                "kind": range_,
                "start": start_day.isoformat(),
                "end": (end_day - timedelta(days=1)).isoformat(),
                "days": (end_day - start_day).days,
            },
            "summary": {
                "tenant_count": len(tenants),
                "total_requests": total_requests,
                "success": totals["success"],
                "errors": totals["errors"],
                "error_rate": round(totals["errors"] / total_requests, 4) if total_requests else 0.0,
                "prompt_tokens": totals["prompt_tokens"],
                "completion_tokens": totals["completion_tokens"],
                "total_tokens": totals["total_tokens"],
                "cache_hits": totals["cache_hits"],
                "cache_hit_rate": round(totals["cache_hits"] / total_requests, 4) if total_requests else 0.0,
                "avg_latency_ms": round(totals["weighted_latency"] / total_requests) if total_requests else 0,
                "p95_latency_ms": totals["p95_latency_ms"],
            },
            "by_tenant": by_tenant,
        }
    )
