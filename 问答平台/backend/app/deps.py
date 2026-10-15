from typing import Generator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.errors import BizError
from app.core.logging import request_id_var
from app.db.session import get_db  # 数据访问层提供,再由本模块统一对外提供依赖
from app.models import User
from app.security import decode_token, is_revoked
from app.db.tenant_hook import TENANT_ID_KEY, bypass_tenant_filter
from app.services.apikey.service import resolve_api_key

_bearer = HTTPBearer(auto_error=False)


def request_id_of(request: Request) -> str:
    """取本次请求的 request_id(中间件已写入 request.state,兜底读 ContextVar)。"""
    return getattr(request.state, "request_id", None) or request_id_var.get()


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


def load_current_user(request: Request, payload: dict, db: Session) -> User:
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
    # app/db/tenant_hook.py 的 ORM 事件从这里读取当前租户（详见该文件 docstring）。
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
    request.state.tenant_id = user.tenant_id
    return user


def get_current_user(
    request: Request,
    payload: dict = Depends(decode_credentials),
    db: Session = Depends(get_db),
) -> User:
    """JWT 登录态鉴权(模块 A)。"""
    return load_current_user(request, payload, db)


def get_current_context(
    request: Request,
    cred: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> RequestContext:
    """统一鉴权出口:支持 `X-API-Key`(模块 C)与 Bearer JWT(模块 A)两种凭据。

    - 带 `X-API-Key` 时优先按 Key 鉴权,反查出租户/用户并写回会话上下文(见
      app/services/apikey/service.py 的"隔离例外"说明);
    - 否则走 JWT;两种路径都归一为 RequestContext,业务代码不再关心凭据类型。

    这里把 tenant_id / key_prefix / api_key_id 写回 request.state,
    供中间件输出访问日志与成员 B 的 HTTP 用量流水使用(日志只记 Key 前缀,不记完整 Key)。
    """
    raw_key = request.headers.get("X-API-Key")
    if raw_key:
        client_ip = request.client.host if request.client else None
        ctx = resolve_api_key(db, raw_key, request_id_of(request), client_ip)
        request.state.tenant_id = ctx.tenant_id
        request.state.key_prefix = ctx.key_prefix
        request.state.api_key_id = ctx.key_id
        return ctx

    user = load_current_user(request, decode_credentials(cred), db)
    return RequestContext(
        request_id=request_id_of(request),
        tenant_id=user.tenant_id,
        user_id=user.id,
        role=user.role_code,
    )


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """租户管理员或平台管理员。"""
    if current_user.role_code not in ("admin", "super_admin"):
        raise BizError("FORBIDDEN", "无权限执行该操作，需要租户管理员权限", 403)
    return current_user


def require_super_admin(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Generator[User, None, None]:
    """仅超级管理员在本请求内临时打开全局业务数据视图。"""
    if current_user.role_code != "super_admin":
        raise BizError("FORBIDDEN", "无权限执行该操作，需要平台管理员权限", 403)
    with bypass_tenant_filter(db):
        yield current_user
