from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import AuthContext, get_auth_context, get_current_user
from app.models import Tenant, User, UserTenantRole, Role
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserInfo
from app.security import (
    hash_password, verify_password, create_access_token, revoke_jti,
)
from app.core.response import ok

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/register")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """注册：默认绑定到指定租户，角色为只读成员。"""
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(400, detail={"code": "USER_EXISTS", "message": "用户名已存在"})

    tenant = db.scalar(select(Tenant).where(Tenant.code == body.tenant_code))
    if not tenant or tenant.status != 1:
        raise HTTPException(404, detail={"code": "TENANT_NOT_FOUND", "message": "租户不存在或已停用"})

    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.flush()

    role = db.scalar(select(Role).where(Role.code == "viewer"))
    db.add(UserTenantRole(user_id=user.id, tenant_id=tenant.id, role_id=role.id))
    db.commit()
    return ok({"user_id": user.id}, "注册成功")

@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_BAD_CREDENTIALS", "message": "用户名或密码错误"},
        )
    if user.status != 1:
        raise HTTPException(403, detail={"code": "USER_DISABLED", "message": "账号已停用"})

    tenant_id, role_code = None, None

    if not user.is_super_admin:
        bindings = db.execute(
            select(UserTenantRole, Role.code)
            .join(Role, Role.id == UserTenantRole.role_id)
            .where(UserTenantRole.user_id == user.id)
        ).all()

        if not bindings:
            raise HTTPException(403, detail={"code": "NO_TENANT", "message": "账号未绑定任何租户"})

        if body.tenant_code:
            tenant = db.scalar(select(Tenant).where(Tenant.code == body.tenant_code))
            if not tenant:
                raise HTTPException(404, detail={"code": "TENANT_NOT_FOUND", "message": "租户不存在"})
            match = [(utr, rc) for utr, rc in bindings if utr.tenant_id == tenant.id]
            if not match:
                raise HTTPException(403, detail={"code": "NO_TENANT_ACCESS", "message": "无权访问该租户"})
            tenant_id, role_code = match[0][0].tenant_id, match[0][1]
        elif len(bindings) == 1:
            tenant_id, role_code = bindings[0][0].tenant_id, bindings[0][1]
        else:
            raise HTTPException(400, detail={
                "code": "TENANT_REQUIRED",
                "message": "该账号属于多个租户，请指定 tenant_code",
            })

    token = create_access_token(
        user_id=user.id,
        tenant_id=tenant_id,
        role=role_code,
        is_super_admin=bool(user.is_super_admin),
    )
    user.last_login_at = datetime.now()
    db.commit()

    return ok({
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 7200,
        "tenant_id": tenant_id,
        "role": role_code,
        "is_super_admin": bool(user.is_super_admin),
    })

@router.post("/logout")
def logout(ctx: AuthContext = Depends(get_auth_context)):
    revoke_jti(ctx.jti)
    return ok(None, "已登出")

@router.get("/me")
def me(
    user: User = Depends(get_current_user),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    bindings = db.execute(
        select(Tenant.id, Tenant.code, Tenant.name, Role.code)
        .join(UserTenantRole, UserTenantRole.tenant_id == Tenant.id)
        .join(Role, Role.id == UserTenantRole.role_id)
        .where(UserTenantRole.user_id == user.id)
    ).all()

    return ok({
        "user_id": user.id,
        "username": user.username,
        "is_super_admin": bool(user.is_super_admin),
        "current_tenant_id": ctx.tenant_id,
        "current_role": ctx.role,
        "tenants": [
            {"id": t_id, "code": t_code, "name": t_name, "role": r_code}
            for t_id, t_code, t_name, r_code in bindings
        ],
    })