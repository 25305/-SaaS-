# -*- coding: utf-8 -*-
"""配额预检服务(模块 D,FR-D1/D3/D5/D6)。

## 顺序红线
预检必须发生在**模型调用之前**(见 app/api/v1/qa.py 的三步顺序)。本模块只负责
"看用量、判是否超限",不做任何模型调用,因此天然满足"拒绝时日志中无模型调用记录"。

## 数据源取舍(写进文档)
- 租户口径的用量读 `usage_summary`(聚合表):流水表会随请求量线性增长,
  预检每次 `SUM(usage_record)` 会随数据量变慢,违反性能要求;
- Key 口径的用量读 `usage_record`(流水表)但**按 key_id 过滤**:
  聚合表只有"租户 + 日"一个维度,没有 Key 维度,无法支撑 FR-C4
  "每个 Key 单独设配额且互不影响",该查询走 ix_llmusage_key 索引 +
  当月时间范围,扫描量被限制在单个 Key 的当月记录内。

## 惰性重置(FR-D5)
不在任何定时任务里重置,而是在每次预检/查询配额状态时做一次幂等判断:
只要"有人用",当月就一定会被对齐。定时任务一旦漏跑无人察觉,惰性判断不会漏。
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.logging import logger
from app.models import ApiKey, LlmUsageRecord, Tenant, UsageSummary

# 只有这两种超额行为,取值不合法时按最严格的 reject 处理
POLICY_REJECT = "reject"
POLICY_SOFT_WARN = "soft_warn"
VALID_POLICIES = (POLICY_REJECT, POLICY_SOFT_WARN)
# 计入"消耗配额"的流水状态;quota_rejected 只审计、不累加用量,避免拒绝雪崩
QUOTA_CONSUMING_STATUSES = ("success", "error")


@dataclass
class QuotaStatus:
    """一次预检的结果。allowed=False 时由调用方决定拒绝还是告警。"""

    allowed: bool
    used_tokens: int
    limit_tokens: Optional[int]
    used_requests: int
    limit_requests: Optional[int]
    reset_at: date
    policy: str
    key_id: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "used_tokens": self.used_tokens,
            "limit_tokens": self.limit_tokens,
            "used_requests": self.used_requests,
            "limit_requests": self.limit_requests,
            "reset_at": self.reset_at.isoformat(),
            "policy": self.policy,
            "key_id": self.key_id,
        }


# ----------------------------------------------------------------------
# 周期工具
# ----------------------------------------------------------------------
def period_start_of(day: date) -> date:
    return day.replace(day=1)


def next_period_start(period_start: date) -> date:
    """周期重置时间点 = 当前周期起始日的下一个月 1 日。"""
    if period_start.month == 12:
        return date(period_start.year + 1, 1, 1)
    return date(period_start.year, period_start.month + 1, 1)


def _day_floor(day: date) -> datetime:
    return datetime.combine(day, time.min)


def ensure_current_period(db: Session, tenant_id: int) -> Tenant:
    """惰性周期重置:把"本月是否已重置"变成请求路径上的幂等检查。

    并发安全:仅在**确实需要重置**时才用 `SELECT ... FOR UPDATE` 把并发事务串行化,
    后到者读到前者的结果,不会重复重置(仅 MySQL 生效;SQLite 无行锁,但开发库的
    写事务本身串行,故跳过该子句)。

    性能取舍:行锁会一直持有到请求结尾的提交(即模型调用**之后**),
    若在"当月无需重置"的常见路径上也加锁,会把同一租户的并发请求串行化,
    吞吐随并发线性下降。因此先用无锁读走快速路径,只有跨月需重置时才加锁。
    """
    tenant = db.get(Tenant, tenant_id)
    today = date.today()
    stored = tenant.period_start if tenant is not None else None
    if tenant is not None and stored is not None and (
        (stored.year, stored.month) == (today.year, today.month)
    ):
        return tenant  # 当月已对齐:无锁快速路径,不阻塞同租户并发

    # 需要重置(或租户不存在):此时才加行锁;不存在则 scalar_one 仍抛 NoResultFound
    stmt = select(Tenant).where(Tenant.id == tenant_id)
    if db.get_bind().dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    tenant = db.execute(stmt).scalar_one()

    today = date.today()
    stored = tenant.period_start
    if stored is None or (stored.year, stored.month) != (today.year, today.month):
        tenant.period_start = period_start_of(today)
        db.commit()
        logger.info(
            "quota_period_reset",
            extra={"fields": {"tenant_id": tenant.id,
                              "previous_period_start": str(stored),
                              "new_period_start": str(tenant.period_start)}},
        )
    return tenant


# ----------------------------------------------------------------------
# 配额解析:Key 级 > 租户级
# ----------------------------------------------------------------------
def effective_quota(key: Optional[ApiKey], tenant: Tenant) -> tuple[Optional[int], Optional[int]]:
    """返回 (月度 Token 上限, 月度请求次数上限);None 表示不限制。

    Key 级字段为 NULL 时继承租户级(FR-C4)。判断用 `is not None` 而非真值判断,
    否则把 `quota_monthly_tokens = 0`(彻底禁止)误当成"未配置"。
    """
    if key is not None and key.quota_monthly_tokens is not None:
        tokens = key.quota_monthly_tokens
    else:
        tokens = tenant.quota_monthly_tokens
    if key is not None and key.quota_monthly_requests is not None:
        requests = key.quota_monthly_requests
    else:
        requests = tenant.quota_monthly_requests
    return tokens, requests


def _within(used: int, limit: Optional[int]) -> bool:
    if limit is None:
        return True  # 该维度不限制
    return used < limit


# ----------------------------------------------------------------------
# 用量读取
# ----------------------------------------------------------------------
def _used_from_summary(db: Session, period_start: date) -> tuple[int, int]:
    """租户口径用量:读聚合表,租户条件由 ORM 钩子注入。"""
    row = db.execute(
        select(
            func.coalesce(func.sum(UsageSummary.prompt_tokens), 0),
            func.coalesce(func.sum(UsageSummary.completion_tokens), 0),
            func.coalesce(func.sum(UsageSummary.requests), 0),
        )
        .select_from(UsageSummary)
        .where(UsageSummary.bucket_date >= period_start)
    ).one()
    return int(row[0]) + int(row[1]), int(row[2])


def _used_from_records(db: Session, period_start: date, key_id: int) -> tuple[int, int]:
    """Key 口径用量:按 key_id 过滤流水(走 ix_llmusage_key,扫描量受限)。"""
    row = db.execute(
        select(
            func.coalesce(func.sum(LlmUsageRecord.prompt_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.completion_tokens), 0),
            func.coalesce(
                func.sum(case((LlmUsageRecord.status.in_(QUOTA_CONSUMING_STATUSES), 1), else_=0)),
                0,
            ),
        )
        .select_from(LlmUsageRecord)
        .where(
            LlmUsageRecord.key_id == key_id,
            LlmUsageRecord.created_at >= _day_floor(period_start),
        )
    ).one()
    return int(row[0]) + int(row[1]), int(row[2])


def check(db: Session, ctx: RequestContext) -> QuotaStatus:
    """配额预检(FR-D3)。调用方必须在模型调用之前执行它。"""
    tenant = ensure_current_period(db, ctx.tenant_id)
    key = db.get(ApiKey, ctx.key_id) if ctx.key_id is not None else None

    policy = tenant.overage_policy if tenant.overage_policy in VALID_POLICIES else POLICY_REJECT
    limit_tokens, limit_requests = effective_quota(key, tenant)

    used_tokens, used_requests = _used_from_summary(db, tenant.period_start)

    # Key 级配额生效的维度单独按 Key 统计,保证"Key1 超限不影响 Key2"(FR-C4)
    if key is not None and (
        key.quota_monthly_tokens is not None or key.quota_monthly_requests is not None
    ):
        key_tokens, key_requests = _used_from_records(db, tenant.period_start, key.id)
        if key.quota_monthly_tokens is not None:
            used_tokens = key_tokens
        if key.quota_monthly_requests is not None:
            used_requests = key_requests

    allowed = _within(used_tokens, limit_tokens) and _within(used_requests, limit_requests)
    return QuotaStatus(
        allowed=allowed,
        used_tokens=used_tokens,
        limit_tokens=limit_tokens,
        used_requests=used_requests,
        limit_requests=limit_requests,
        reset_at=next_period_start(tenant.period_start),
        policy=policy,
        key_id=ctx.key_id,
    )


def status_of(db: Session, ctx: RequestContext) -> QuotaStatus:
    """给前端展示的配额状态(D7)。与预检同一口径,但永远不抛异常。"""
    return check(db, ctx)
