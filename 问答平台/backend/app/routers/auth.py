from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import BizError
from app.core.response import ok
from app.deps import decode_credentials, get_current_user, get_db
from app.models import Role, Tenant, User
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserInfoResponse
from app.seed import RESERVED_TENANT_CODES
from app.security import (
    create_access_token,
    hash_password,
    revoke_jti,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/register", summary="注册租户与管理员账号", description="用租户编码创建新租户，并把请求者注册为该租户的管理员。租户编码或用户名重复时返回 409。")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if req.tenant_code in RESERVED_TENANT_CODES:
        raise BizError("TENANT_CODE_RESERVED", "该租户编码为系统保留，请换一个", 409)
    if db.execute(select(Tenant.id).where(Tenant.code == req.tenant_code)).first():
        raise BizError("TENANT_EXISTS", "租户编码已存在", 409)
    if db.execute(select(User.id).where(User.username == req.username)).first():
        raise BizError("USERNAME_EXISTS", "用户名已存在", 409)

    admin_role = db.execute(select(Role).where(Role.code == "admin")).scalar_one()
    tenant = Tenant(code=req.tenant_code, name=req.tenant_code)
    db.add(tenant)
    db.flush()
    db.add(
        User(
            tenant_id=tenant.id,
            username=req.username,
            password_hash=hash_password(req.password),
            email=req.email,
            role_id=admin_role.id,
            is_active=True,
        )
    )
    db.commit()
    return ok(message="注册成功")


@router.post("/login", summary="登录", description="校验用户名和密码，成功返回 JWT access_token。用户名全局唯一，tenant_code 可不填。")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    stmt = select(User).where(User.username == req.username)
    if req.tenant_code:
        stmt = stmt.join(Tenant, User.tenant_id == Tenant.id).where(
            Tenant.code == req.tenant_code
        )
    user = db.execute(stmt).scalar_one_or_none()
    if user is None or not verify_password(req.password, user.password_hash):
        raise BizError("INVALID_CREDENTIALS", "用户名或密码错误", 401)
    if not user.is_active:
        raise BizError("ACCOUNT_DISABLED", "账号已被禁用", 403)
    if not user.tenant.is_active:
        raise BizError("TENANT_DISABLED", "租户已停用", 403)

    role = user.role_code
    token = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=role,
        is_super_admin=(role == "super_admin"),
    )
    return ok(
        TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in=settings.JWT_EXPIRE_MINUTES * 60,
            username=user.username,
            role=role,
            tenant_code=user.tenant.code,
            home="/admin" if role == "super_admin" else "/workbench",
        )
    )


@router.get("/me", summary="获取当前用户", description="根据 Bearer token 返回当前登录用户的信息及所属租户。")
def me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ok(
        UserInfoResponse(
            id=current_user.id,
            username=current_user.username,
            email=current_user.email,
            role=current_user.role_code,
            is_super_admin=(current_user.role_code == "super_admin"),
            tenant_id=current_user.tenant_id,
            tenant_code=current_user.tenant.code,
        )
    )


@router.post("/logout", summary="退出登录", description="使当前 token 失效（加入黑名单），之后再用该 token 请求会返回 401。")
def logout(payload: dict = Depends(decode_credentials)):
    revoke_jti(payload["jti"])
    return ok(message="退出成功")
