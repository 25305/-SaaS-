# -*- coding: utf-8 -*-
"""API Key 管理接口(模块 C,D3,FR-C1~C6)。

安全口径:
- **创建/轮换**响应返回完整 Key,仅此一次(FR-C2);
- **列表**只返回掩码 `sk-abc123...xyz9`,任何接口都不再返回完整值;
- 管理操作要求租户管理员(等价于任务书里的 `require_role('tenant_admin')`,
  本项目角色编码为 `admin`,复用既有依赖 `require_admin`);
- 越权访问别人的 Key 走正常隔离路径 → 与"Key 不存在"返回完全相同的 404;
- 日志只记 `key_prefix`,绝不记完整 Key。
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.logging import log_event
from app.core.response import ok
from app.deps import get_db, require_admin, request_id_of
from app.models import User
from app.schemas import KeyCreateIn, KeyInfo, KeyCreatedOut
from app.services.apikey import service

router = APIRouter(prefix="/api/v1/apikeys", tags=["API Key"])

_ONLY_ONCE = "完整 Key 仅显示一次,请立即保存到安全位置;之后只能看到掩码。"


@router.post("", summary="创建 API Key", description="为调用方创建 Key;完整值仅在本次响应返回。")
def create_key(
    payload: KeyCreateIn,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    key, raw = service.create_key(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        name=payload.name,
        quota_monthly_tokens=payload.quota_monthly_tokens,
        quota_monthly_requests=payload.quota_monthly_requests,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        rate_limit_per_hour=payload.rate_limit_per_hour,
    )
    log_event(
        "apikey_created",
        request_id=request_id_of(request),
        tenant_id=current_user.tenant_id,
        key_prefix=key.key_prefix,  # 只记前缀
    )
    return ok(
        KeyCreatedOut(
            id=key.id, name=key.name, key=raw, masked=key.masked, warning=_ONLY_ONCE
        )
    )


@router.get("", summary="API Key 列表", description="列出本租户的 Key,只返回掩码,不含完整值。")
def list_keys(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return ok([KeyInfo(**service.describe(key)) for key in service.list_keys(db)])


@router.post(
    "/{key_id}/rotate",
    summary="轮换 API Key",
    description="签发新 Key,旧 Key 保留 24 小时切换窗口后过期(不是立即失效)。",
)
def rotate_key(
    key_id: int,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    old, new, raw = service.rotate_key(db, key_id)
    log_event(
        "apikey_rotated",
        request_id=request_id_of(request),
        tenant_id=current_user.tenant_id,
        key_prefix=new.key_prefix,
        old_key_prefix=old.key_prefix,
        old_expires_at=str(old.expire_at),
    )
    return ok({
        "id": new.id,
        "name": new.name,
        "key": raw,
        "masked": new.masked,
        "warning": _ONLY_ONCE,
        "old_key": {"id": old.id, "masked": old.masked, "status": old.status,
                    "expire_at": old.expire_at.isoformat()},
    })


@router.delete(
    "/{key_id}",
    summary="吊销 API Key",
    description="状态置为 revoked,该 Key 的调用立即返回 401(API_KEY_INVALID)。",
)
def revoke_key(
    key_id: int,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    key = service.revoke_key(db, key_id)
    log_event(
        "apikey_revoked",
        level="warning",
        request_id=request_id_of(request),
        tenant_id=current_user.tenant_id,
        key_prefix=key.key_prefix,
    )
    return ok({"id": key.id, "status": key.status}, message="API Key 已吊销")
