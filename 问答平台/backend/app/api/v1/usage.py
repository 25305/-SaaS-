# -*- coding: utf-8 -*-
"""配额状态与用量分组接口(模块 D,D7 + FR-C5)。

- `GET /api/v1/quota/status`:给前端展示"本周期用到了多少、什么时候重置";
- `GET /api/v1/usage/by-key`:用量明细按 Key 分组(FR-C5 验收点)。

两者都接受 `X-API-Key` 或 Bearer JWT 两种凭据(统一走 get_current_context),
租户条件由 ORM 钩子注入,不手写 tenant_id。
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.response import ok
from app.deps import get_current_context, get_db
from app.schemas import QuotaStatusOut
from app.services.apikey import service as apikey_service
from app.services.metering import service as metering_service
from app.services.quota import service as quota_service

router = APIRouter(prefix="/api/v1", tags=["配额与用量"])


@router.get("/quota/status", summary="当前配额状态")
def quota_status(
    ctx: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    # 读取本身会触发惰性周期重置(FR-D5),因此本接口可用来验证周期重置
    status = quota_service.check(db, ctx)
    return ok(QuotaStatusOut(**status.as_dict()))


@router.get("/usage/by-key", summary="用量按 Key 分组")
def usage_by_key(
    days: Optional[int] = Query(
        default=None, ge=1, le=90, description="统计最近 N 天;不填为全部历史"
    ),
    ctx: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    since = datetime.now() - timedelta(days=days) if days is not None else None
    rows = metering_service.usage_by_key(db, since)
    # 附上掩码,便于前端展示"哪个 Key 被用了多少";绝不返回完整 Key
    masked = {key.id: key.masked for key in apikey_service.list_keys(db)}
    for row in rows:
        row["masked"] = masked.get(row["key_id"]) if row["key_id"] else "jwt(登录态)"
    return ok(rows)
