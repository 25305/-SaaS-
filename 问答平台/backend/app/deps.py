from typing import Generator

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.db import SessionLocal
from app.models import User
from app.security import decode_token, is_revoked
from app.tenant_scope import TENANT_BYPASS_KEY, TENANT_ID_KEY

_bearer = HTTPBearer(auto_error=False)


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def decode_credentials(cred: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    """校验 Bearer token 的签名/有效期/黑名单，返回 JWT payload。"""
    if cred is None:
        raise BizError("UNAUTHORIZED", "未提供认证信息", 401)
    try:
        payload = decode_token(cred.credentials)
    except PyJWTError:
        raise BizError("INVALID_TOKEN", "登录已过期或无效", 401)
    if not payload.get("jti") or is_revoked(payload["jti"]):
        raise BizError("INVALID_TOKEN", "登录已过期或无效", 401)
    return payload


def get_current_user(
    payload: dict = Depends(decode_credentials),
    db: Session = Depends(get_db),
) -> User:
    """解析 token 并从数据库加载当前登录用户。

    权限判断一律以数据库实时状态为准：改角色/禁用/停租户对已签发 token 立即生效。
    """
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    # fail-closed：token 必须带 tenant_id，否则不设租户上下文会导致静默不过滤
    if not user_id or not tenant_id:
        raise BizError("INVALID_TOKEN", "登录已过期或无效", 401)
    # 关键：加载用户之前，把租户上下文挂到本次请求的 Session 上。
    # FastAPI 依赖缓存保证同一请求内所有依赖/端点共享同一个 Session 对象，
    # tenant_scope.py 的 ORM 事件从这里读取当前租户（详见该文件 docstring）。
    db.info[TENANT_ID_KEY] = int(tenant_id)
    user = db.get(User, int(user_id))
    if user is None:
        raise BizError("INVALID_TOKEN", "登录已过期或无效", 401)
    if user.tenant_id != int(tenant_id):
        # token 与租户绑定：token 里的租户和用户实际租户不一致即拒绝
        raise BizError("INVALID_TOKEN", "登录已过期或无效", 401)
    if not user.is_active:
        raise BizError("ACCOUNT_DISABLED", "账号已被禁用", 403)
    if not user.tenant.is_active:
        raise BizError("TENANT_DISABLED", "租户已停用", 403)
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """租户管理员或平台管理员。"""
    if current_user.role_code not in ("admin", "super_admin"):
        raise BizError("FORBIDDEN", "无权限执行该操作，需要租户管理员权限", 403)
    return current_user


def require_super_admin(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """平台管理员。校验通过后为本请求设置隔离绕过标记(全局统计等场景需要)。"""
    if current_user.role_code != "super_admin":
        raise BizError("FORBIDDEN", "无权限执行该操作，需要平台管理员权限", 403)
    db.info[TENANT_BYPASS_KEY] = True  # 与 get_current_user 拿到的是同一个 Session
    return current_user
