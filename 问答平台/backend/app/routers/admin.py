from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.core.response import ok
from app.deps import get_db, require_super_admin
from app.models import Role, Tenant, UsageSummary, User
from app.schemas import TenantCreate, TenantInfo, TenantQuotaUpdate
from app.security import hash_password

router = APIRouter(prefix="/api/admin", tags=["平台管理"])


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
    return ok(
        [
            TenantInfo(
                id=t.id,
                code=t.code,
                name=t.name,
                is_active=t.is_active,
                user_count=count,
            )
            for t, count in rows
        ]
    )


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


@router.patch("/tenants/{tenant_id}/quota", summary="配置租户月度配额")
def update_tenant_quota(
    tenant_id: int,
    payload: TenantQuotaUpdate,
    current_user: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise BizError("TENANT_NOT_FOUND", "租户不存在", 404)
    tenant.quota_monthly_tokens = payload.quota_monthly_tokens
    tenant.quota_monthly_requests = payload.quota_monthly_requests
    tenant.overage_policy = payload.overage_policy
    db.commit()
    return ok(message="租户配额已更新")


@router.get("/usage", summary="平台全局用量")
def global_usage(
    current_user: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    row = db.execute(
        select(
            func.coalesce(func.sum(UsageSummary.requests), 0),
            func.coalesce(func.sum(UsageSummary.prompt_tokens), 0),
            func.coalesce(func.sum(UsageSummary.completion_tokens), 0),
        )
    ).one()
    return ok({"total_requests": int(row[0]), "prompt_tokens": int(row[1]), "completion_tokens": int(row[2])})
