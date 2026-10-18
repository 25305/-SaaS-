# -*- coding: utf-8 -*-
"""计量服务(模块 C/D,FR-D2/D4)。

## 不可变流水账
`usage_record` 只允许 INSERT:本模块没有任何 update/delete/upsert 该表的代码,
`usage_summary` 的所有数值都由它 SUM/COUNT 派生(`refresh_summary`)。
为什么不用"可写计数器"(tenant.used_tokens += n):
1. 无法审计——账单数字对不上时查不出是哪次调用错了;
2. 并发不安全——多个请求同时 `+=` 会丢更新,加行锁又成为性能瓶颈;
3. 无法追溯——验收要求账单每行数字都能在流水里查到对应记录。

## 失败处理策略(与成员 B 的 HTTP 访问流水的差异)
成员 B 的 `usage_records` 是访问审计的辅助数据,写失败可以吞掉;
本模块的计量流水是**计费依据**,写失败一律显式抛出(500)+ 记 `usage_record_failed`
——静默漏记会让配额永不推进,等于配额失效。
"""

from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.logging import logger
from app.models import LlmUsageRecord, UsageKeySummary, UsageLatencyBucket, UsageSummary

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_QUOTA_REJECTED = "quota_rejected"
# 计入配额消耗的状态;quota_rejected 只审计,不累加(否则拒绝会自我放大)
QUOTA_CONSUMING_STATUSES = (STATUS_SUCCESS, STATUS_ERROR)


# ----------------------------------------------------------------------
# 写入流水(只 INSERT)
# ----------------------------------------------------------------------
def _insert(
    db: Session,
    ctx: RequestContext,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    status: str,
    cache_hit: bool,
) -> None:
    record = LlmUsageRecord(
        tenant_id=ctx.tenant_id,
        key_id=ctx.key_id,
        user_id=ctx.user_id,
        request_id=ctx.request_id,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        status=status,
        cache_hit=cache_hit,
    )
    try:
        db.add(record)
        db.flush()
        # 请求路径只做 O(1) 增量写入，完整重算由后台任务异步完成。
        # 这样问答延迟不会随当日 usage_record 数量线性增长。
        apply_record_delta(db, record)
        db.commit()
    except Exception:
        db.rollback()
        logger.error(
            "usage_record_failed",
            extra={"fields": {"request_id": ctx.request_id, "tenant_id": ctx.tenant_id,
                              "status": status}},
        )
        raise


def record_success(
    db: Session,
    ctx: RequestContext,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
) -> None:
    """模型调用成功。Token 数必须来自模型响应的 usage(FR-D2)。"""
    _insert(db, ctx, model=model, prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens, latency_ms=latency_ms,
            status=STATUS_SUCCESS, cache_hit=False)


def record_error(
    db: Session,
    ctx: RequestContext,
    *,
    model: str,
    latency_ms: int,
) -> None:
    """模型调用失败(上游异常/超时)。Token 记 0,请求次数照记。"""
    _insert(db, ctx, model=model, prompt_tokens=0, completion_tokens=0,
            latency_ms=latency_ms, status=STATUS_ERROR, cache_hit=False)


def record_cache_hit(db: Session, ctx: RequestContext, *, model: str, latency_ms: int = 0) -> None:
    """缓存命中(FR:命中率口径)。

    记一条 `cache_hit=1, tokens=0` 的流水:
    - 请求次数包含缓存命中(确实发生了一次请求);
    - Token 记 0(没有调用模型,没有 Token 成本),这样"缓存省了多少成本"能算出来。
    """
    _insert(db, ctx, model=model, prompt_tokens=0, completion_tokens=0,
            latency_ms=latency_ms, status=STATUS_SUCCESS, cache_hit=True)


def record_quota_rejected(db: Session, ctx: RequestContext, *, model: str) -> None:
    """配额拦截(FR-D3)。此时**尚未调用模型**,故 latency/tokens 全为 0。

    写这条流水的意义:拒绝也有审计痕迹,且按状态分组能统计"被拦了多少次"。
    """
    _insert(db, ctx, model=model, prompt_tokens=0, completion_tokens=0,
            latency_ms=0, status=STATUS_QUOTA_REJECTED, cache_hit=False)


# ----------------------------------------------------------------------
# 聚合派生(唯一写 usage_summary 的入口,成员 D 的异步任务可复用)
# ----------------------------------------------------------------------
def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


def _percentile(values: list[int], ratio: float) -> int:
    """最近秩法算分位数(SQLite 无 PERCENTILE 函数,量级小,在 Python 侧算)。

    成员 D 的看板若改为异步刷新,可换成 MySQL 的窗口函数/近似分位,口径不变。
    """
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, round(ratio * len(ordered) + 0.5) - 1)
    return int(ordered[min(rank, len(ordered) - 1)])


def _latency_bucket(value: int) -> str:
    if value < 500: return "0-500ms"
    if value < 1000: return "500ms-1s"
    if value < 2000: return "1s-2s"
    if value < 5000: return "2s-5s"
    return "5s+"


def apply_record_delta(db: Session, record: LlmUsageRecord) -> None:
    """把单条新流水增量写入物化表；不读取整日流水。"""
    day = record.created_at.date()
    summary = db.execute(select(UsageSummary).where(UsageSummary.bucket_date == day)).scalar_one_or_none()
    if summary is None:
        summary = UsageSummary(
            tenant_id=record.tenant_id, bucket_date=day, requests=0, success=0, errors=0,
            prompt_tokens=0, completion_tokens=0, avg_latency_ms=0, p95_latency_ms=0, cache_hits=0,
        )
        db.add(summary); db.flush()
    consumes = record.status in QUOTA_CONSUMING_STATUSES
    previous_requests = summary.requests
    if consumes:
        summary.requests += 1
    if record.status == STATUS_SUCCESS: summary.success += 1
    if record.status == STATUS_ERROR: summary.errors += 1
    summary.prompt_tokens += record.prompt_tokens
    summary.completion_tokens += record.completion_tokens
    if record.cache_hit: summary.cache_hits += 1
    if record.latency_ms > 0:
        denominator = max(1, previous_requests + (1 if consumes else 0))
        summary.avg_latency_ms = round((summary.avg_latency_ms * max(0, denominator - 1) + record.latency_ms) / denominator)
        summary.p95_latency_ms = max(summary.p95_latency_ms, record.latency_ms)
    key_id = record.key_id or 0
    key_summary = db.execute(select(UsageKeySummary).where(UsageKeySummary.bucket_date == day, UsageKeySummary.key_id == key_id)).scalar_one_or_none()
    if key_summary is None:
        key_summary = UsageKeySummary(
            tenant_id=record.tenant_id, bucket_date=day, key_id=key_id,
            requests=0, prompt_tokens=0, completion_tokens=0, cache_hits=0,
        )
        db.add(key_summary)
    if consumes: key_summary.requests += 1
    key_summary.prompt_tokens += record.prompt_tokens
    key_summary.completion_tokens += record.completion_tokens
    if record.cache_hit: key_summary.cache_hits += 1
    label = _latency_bucket(record.latency_ms)
    bucket = db.execute(select(UsageLatencyBucket).where(UsageLatencyBucket.bucket_date == day, UsageLatencyBucket.bucket == label)).scalar_one_or_none()
    if bucket is None:
        bucket = UsageLatencyBucket(tenant_id=record.tenant_id, bucket_date=day, bucket=label, count=0)
        db.add(bucket)
    bucket.count += 1


def refresh_summary(db: Session, tenant_id: int, bucket_date: date) -> UsageSummary:
    """重算某租户某日的聚合并 UPSERT。调用方 Session 必须已带租户上下文。"""
    start, end = _day_bounds(bucket_date)
    in_day = (LlmUsageRecord.created_at >= start, LlmUsageRecord.created_at < end)

    counters = db.execute(
        select(
            func.coalesce(func.sum(case((LlmUsageRecord.status.in_(QUOTA_CONSUMING_STATUSES), 1), else_=0)), 0),
            func.coalesce(func.sum(case((LlmUsageRecord.status == STATUS_SUCCESS, 1), else_=0)), 0),
            func.coalesce(func.sum(case((LlmUsageRecord.status == STATUS_ERROR, 1), else_=0)), 0),
            func.coalesce(func.sum(LlmUsageRecord.prompt_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.completion_tokens), 0),
            func.coalesce(func.avg(LlmUsageRecord.latency_ms), 0.0),
            func.coalesce(func.sum(case((LlmUsageRecord.cache_hit.is_(True), 1), else_=0)), 0),
        )
        .select_from(LlmUsageRecord)
        .where(*in_day)
    ).one()

    latencies = [
        int(value)
        for value in db.execute(
            select(LlmUsageRecord.latency_ms)
            .select_from(LlmUsageRecord)
            .where(*in_day, LlmUsageRecord.latency_ms > 0)
        ).scalars()
    ]

    row = db.execute(
        select(UsageSummary)
        .select_from(UsageSummary)
        .where(UsageSummary.bucket_date == bucket_date)
    ).scalar_one_or_none()
    if row is None:
        row = UsageSummary(tenant_id=tenant_id, bucket_date=bucket_date)
        db.add(row)

    row.requests = int(counters[0])
    row.success = int(counters[1])
    row.errors = int(counters[2])
    row.prompt_tokens = int(counters[3])
    row.completion_tokens = int(counters[4])
    row.avg_latency_ms = int(round(float(counters[5])))
    row.p95_latency_ms = _percentile(latencies, 0.95)
    row.cache_hits = int(counters[6])

    # Key 图表物化汇总：看板请求不再扫描不可变流水。
    key_rows = db.execute(
        select(
            LlmUsageRecord.key_id,
            func.coalesce(func.sum(case((LlmUsageRecord.status.in_(QUOTA_CONSUMING_STATUSES), 1), else_=0)), 0),
            func.coalesce(func.sum(LlmUsageRecord.prompt_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.completion_tokens), 0),
            func.coalesce(func.sum(case((LlmUsageRecord.cache_hit.is_(True), 1), else_=0)), 0),
        ).where(*in_day).group_by(LlmUsageRecord.key_id)
    ).all()
    existing_key_rows = db.execute(
        select(UsageKeySummary).where(UsageKeySummary.bucket_date == bucket_date)
    ).scalars().all()
    existing_by_key = {item.key_id: item for item in existing_key_rows}
    seen_keys: set[int] = set()
    for key_id, requests, prompt, completion, cache_hits in key_rows:
        normalized_key_id = int(key_id or 0)
        seen_keys.add(normalized_key_id)
        key_row = existing_by_key.get(normalized_key_id)
        if key_row is None:
            key_row = UsageKeySummary(
                tenant_id=tenant_id, bucket_date=bucket_date, key_id=normalized_key_id
            )
            db.add(key_row)
        key_row.requests = int(requests)
        key_row.prompt_tokens = int(prompt)
        key_row.completion_tokens = int(completion)
        key_row.cache_hits = int(cache_hits)
    for stale in existing_key_rows:
        if stale.key_id not in seen_keys:
            db.delete(stale)

    # 延迟直方图同样物化，供 ECharts 柱状图直接读取。
    bucket_definitions = (("0-500ms", 0, 500), ("500ms-1s", 500, 1000),
                          ("1s-2s", 1000, 2000), ("2s-5s", 2000, 5000),
                          ("5s+", 5000, None))
    existing_buckets = {
        item.bucket: item
        for item in db.execute(
            select(UsageLatencyBucket).where(UsageLatencyBucket.bucket_date == bucket_date)
        ).scalars().all()
    }
    for label, lower, upper in bucket_definitions:
        predicates = [*in_day, LlmUsageRecord.latency_ms >= lower]
        if upper is not None:
            predicates.append(LlmUsageRecord.latency_ms < upper)
        count = int(db.execute(select(func.count(LlmUsageRecord.id)).where(*predicates)).scalar_one())
        bucket = existing_buckets.get(label)
        if bucket is None:
            bucket = UsageLatencyBucket(tenant_id=tenant_id, bucket_date=bucket_date, bucket=label)
            db.add(bucket)
        bucket.count = count
    db.commit()
    return row


# ----------------------------------------------------------------------
# 查询辅助(成员 D 的看板 / 接口层复用)
# ----------------------------------------------------------------------
def usage_by_key(db: Session, since: Optional[datetime] = None) -> list[dict]:
    """按 Key 分组的用量明细(FR-C5:用量明细能按 Key 分组)。

    分组维度为 key_id;NULL 表示用 JWT 登录态调用,统一归到 "jwt" 分组。

    `request_count` 与 `usage_summary.requests` 同口径,只统计真正消耗配额的
    状态(success/error,含缓存命中);`quota_rejected` 单独返回,避免"被拦次数"
    混进用量,与预检侧的计数口径保持一致。
    """
    conditions = []
    if since is not None:
        conditions.append(LlmUsageRecord.created_at >= since)
    rows = db.execute(
        select(
            LlmUsageRecord.key_id,
            func.coalesce(
                func.sum(case((LlmUsageRecord.status.in_(QUOTA_CONSUMING_STATUSES), 1), else_=0)), 0
            ),
            func.coalesce(func.sum(LlmUsageRecord.prompt_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.completion_tokens), 0),
            func.coalesce(func.sum(case((LlmUsageRecord.cache_hit.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(case((LlmUsageRecord.status == STATUS_QUOTA_REJECTED, 1), else_=0)), 0),
        )
        .select_from(LlmUsageRecord)
        .where(*conditions)
        .group_by(LlmUsageRecord.key_id)
        .order_by(LlmUsageRecord.key_id)
    ).all()
    return [
        {
            "key_id": key_id,
            "request_count": int(count),
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "total_tokens": int(prompt) + int(completion),
            "cache_hits": int(hits),
            "quota_rejected": int(rejected),
        }
        for key_id, count, prompt, completion, hits, rejected in rows
    ]
