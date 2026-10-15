# -*- coding: utf-8 -*-
"""API Key 服务(模块 C):创建 / 列表 / 轮换 / 吊销 / 鉴权反查。

## 隔离例外(必须由成员 B 在评审时确认)

`resolve_api_key` 在**鉴权阶段**执行:此时还没有 `tenant_id` 上下文(上下文正是它要
反查出来的东西),无法走 `TenantMixin` 的自动隔离条件。做法是:

1. 用 `bypass_tenant_filter` 打开一个**极短窗口**,只跑一条
   `WHERE key_hash = :hash` 的查询(该列在库里是 UNIQUE,等价于按唯一键取单行);
2. 立刻退出窗口,把反查出的 `tenant_id` 写回 Session 上下文,
   之后本请求的所有业务查询/写入都回到普通隔离路径;
3. 窗口内不读任何其他业务表,拿到租户后所有后续访问仍受隔离约束。

风险与结论:`key_hash` 全局唯一 + 只有完整 Key 的持有者能算出哈希 → 该查询不构成
越权,也不泄露其他租户的数据(查询条件本身不含他人可控的标识)。这是模块 C 唯一的
隔离例外,除此之外本模块所有 Key 管理接口都通过正常隔离路径(越权访问返回 404)。
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.errors import ApiKeyInvalidError, BizError
from app.core.logging import logger
from app.db.tenant_hook import TENANT_ID_KEY, bypass_tenant_filter
from app.models import ApiKey, User
from app.services.apikey.generator import generate_api_key, hash_key, mask

# 轮换宽限期:旧 Key 不立即失效,给调用方灰度切换窗口(FR-C3)
ROTATION_GRACE_HOURS = 24

STATUS_ACTIVE = "active"
STATUS_EXPIRING = "expiring"
STATUS_REVOKED = "revoked"


# ----------------------------------------------------------------------
# 生命周期
# ----------------------------------------------------------------------
def get_own_key(db: Session, key_id: int) -> ApiKey:
    """取本租户的 Key。`Session.get` 同样被 ORM 钩子注入租户条件:
    别人的 Key 查不到 → 与"Key 不存在"返回完全相同的 404,不泄露存在性。"""
    key = db.get(ApiKey, key_id)
    if key is None:
        raise BizError("API_KEY_NOT_FOUND", "API Key 不存在", 404)
    return key


def create_key(
    db: Session,
    *,
    tenant_id: int,
    user_id: int,
    name: str,
    quota_monthly_tokens: Optional[int] = None,
    quota_monthly_requests: Optional[int] = None,
    rate_limit_per_minute: Optional[int] = None,
    rate_limit_per_hour: Optional[int] = None,
) -> tuple[ApiKey, str]:
    """创建 Key。返回 (对象, 完整值);完整值仅此一次可用(FR-C2)。"""
    raw, key_hash, prefix, suffix = generate_api_key()
    key = ApiKey(
        tenant_id=tenant_id,
        user_id=user_id,
        name=name,
        key_hash=key_hash,
        key_prefix=prefix,
        key_suffix=suffix,
        status=STATUS_ACTIVE,
        quota_monthly_tokens=quota_monthly_tokens,
        quota_monthly_requests=quota_monthly_requests,
        rate_limit_per_minute=rate_limit_per_minute,
        rate_limit_per_hour=rate_limit_per_hour,
    )
    db.add(key)
    db.commit()
    return key, raw


def list_keys(db: Session) -> list[ApiKey]:
    """本租户全部 Key(隔离条件由 ORM 钩子注入)。"""
    return list(
        db.execute(select(ApiKey).order_by(ApiKey.id.desc())).scalars().all()
    )


def rotate_key(db: Session, key_id: int) -> tuple[ApiKey, ApiKey, str]:
    """轮换:旧 Key 设 24h 过期窗口 + 状态 expiring,同时签发新 Key。

    返回 (旧 Key, 新 Key, 新 Key 完整值)。
    新 Key 继承旧 Key 的配额与限流配置:否则轮换会把 FR-C4 的"单 Key 独立配额"
    悄悄丢掉,等于换个 Key 就绕过限流。
    """
    old = get_own_key(db, key_id)
    if old.status == STATUS_REVOKED:
        raise BizError("API_KEY_REVOKED", "已吊销的 Key 不能轮换,请重新创建", 409)

    old.expire_at = datetime.now() + timedelta(hours=ROTATION_GRACE_HOURS)
    old.status = STATUS_EXPIRING

    raw, key_hash, prefix, suffix = generate_api_key()
    new = ApiKey(
        tenant_id=old.tenant_id,
        user_id=old.user_id,
        name=f"{old.name} (rotated)",
        key_hash=key_hash,
        key_prefix=prefix,
        key_suffix=suffix,
        status=STATUS_ACTIVE,
        quota_monthly_tokens=old.quota_monthly_tokens,
        quota_monthly_requests=old.quota_monthly_requests,
        rate_limit_per_minute=old.rate_limit_per_minute,
        rate_limit_per_hour=old.rate_limit_per_hour,
    )
    db.add(new)
    db.commit()
    return old, new, raw


def revoke_key(db: Session, key_id: int) -> ApiKey:
    """吊销:状态置 revoked,下一次调用立即 401(FR-C3)。"""
    key = get_own_key(db, key_id)
    key.status = STATUS_REVOKED
    db.commit()
    return key


# ----------------------------------------------------------------------
# 鉴权反查(供 app/deps.py 的 get_current_context 调用)
# ----------------------------------------------------------------------
def resolve_api_key(
    db: Session,
    raw_key: str,
    request_id: str,
    client_ip: Optional[str] = None,
) -> RequestContext:
    """按完整 Key 反查租户/用户,返回 RequestContext;失败抛 ApiKeyInvalidError。

    client_ip 用于记录来源 IP(FR-C6,泄漏排查时看"谁在用")。
    """
    key_hash = hash_key(raw_key)
    with bypass_tenant_filter(db):
        row = db.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        ).scalar_one_or_none()
    if row is None:
        raise ApiKeyInvalidError("API Key 无效")

    if row.status == STATUS_REVOKED:
        raise ApiKeyInvalidError("API Key 已失效")
    now = datetime.now()
    if row.expire_at is not None and row.expire_at < now:
        raise ApiKeyInvalidError("API Key 已过期")

    # 反查出的租户写回 Session 上下文:本请求后续查询/写入回到正常隔离路径
    db.info[TENANT_ID_KEY] = row.tenant_id

    key_id, tenant_id, user_id, key_prefix = row.id, row.tenant_id, row.user_id, row.key_prefix
    user = db.get(User, user_id)
    role = user.role_code if user is not None else "readonly"

    # 记录最后使用信息(FR-C6);只记前缀,绝不记完整 Key
    row.last_used_at = now
    row.last_used_ip = client_ip
    db.commit()

    logger.info(
        "apikey_resolved",
        extra={"fields": {"request_id": request_id, "tenant_id": tenant_id,
                          "key_prefix": key_prefix}},
    )
    return RequestContext(
        request_id=request_id,
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        key_id=key_id,
        key_prefix=key_prefix,
    )


def describe(key: ApiKey) -> dict:
    """Key 的对外表示:只含掩码,绝不含完整值(FR-C2)。"""
    return {
        "id": key.id,
        "name": key.name,
        "masked": mask(key.key_prefix, key.key_suffix),
        "status": key.status,
        "expire_at": key.expire_at.isoformat() if key.expire_at else None,
        "quota_monthly_tokens": key.quota_monthly_tokens,
        "quota_monthly_requests": key.quota_monthly_requests,
        "rate_limit_per_minute": key.rate_limit_per_minute,
        "rate_limit_per_hour": key.rate_limit_per_hour,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "last_used_ip": key.last_used_ip,
        "created_at": key.created_at.isoformat(),
    }
