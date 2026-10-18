# -*- coding: utf-8 -*-
"""用量看板接口(模块 F,FR-F1~F3、F6)。

## FR-F6:看板接口不做明细全表扫描
所有**标量指标**(请求数/成功失败/错误率/Token 分列/平均与 P95 延迟/缓存命中率)与
**趋势序列**都只读 `usage_summary`。汇总表按 `(tenant_id, bucket_date)` 索引命中,
接口耗时与 `usage_record` 的数据量无关。

两处例外都带**有界条件**,不是全表扫描,原因如下:
1. `by_key`:按 Key 分组的用量。汇总表没有 Key 维度(成员 C 的字段口径定稿),
   只能在区间内 `GROUP BY key_id`(命中 `ix_llmusage_key`);
2. `latency_distribution`:延迟分桶直方图。汇总表只存 avg/p95,存不下分布,
   只能在区间内做条件计数聚合(命中 `ix_llmusage_tenant_created`)。
两者都以 `created_at` 区间收窄,区间由接口参数决定(默认近 30 天)。

响应结构对齐任务书 §2.3 的契约(字段名定稿):
`{summary, trend, by_key, latency_distribution}`。
"""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.errors import BizError
from app.core.response import ok
from app.db.session import get_db
from app.deps import get_current_context
from app.models import ApiKey, LlmUsageRecord, UsageSummary
from app.services.billing.service import daily_rows
from app.services.metering.service import QUOTA_CONSUMING_STATUSES, STATUS_QUOTA_REJECTED

router = APIRouter(prefix="/api/v1/dashboard", tags=["用量看板"])

# 延迟分桶边界(左闭右开),用于 FR-F3 的"延迟分布"图表
LATENCY_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0-500ms", 0, 500),
    ("500-1000ms", 500, 1000),
    ("1000-2000ms", 1000, 2000),
    ("2000-5000ms", 2000, 5000),
    ("5000ms+", 5000, None),
)


def resolve_range(
    range_: str,
    start: date | None,
    end: date | None,
    today: date | None = None,
) -> tuple[date, date]:
    """把时间维度解析成 [start, end) 半开区间。FR-F2 要求支持今日/本周/本月/自定义。"""
    today = today or date.today()
    key = (range_ or "month").strip().lower()
    if key == "today":
        return today, today + timedelta(days=1)
    if key == "week":
        return today - timedelta(days=6), today + timedelta(days=1)
    if key == "month":
        return today.replace(day=1), today + timedelta(days=1)
    if key == "custom":
        if start is None or end is None:
            raise BizError("VALIDATION_ERROR", "自定义区间需同时提供 start 与 end", 422)
        if end < start:
            raise BizError("VALIDATION_ERROR", "end 不能早于 start", 422)
        return start, end + timedelta(days=1)
    raise BizError("VALIDATION_ERROR", "range 需为 today/week/month/custom", 422)


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _summarize(rows: list[UsageSummary]) -> dict:
    """标量指标:全部来自汇总表(FR-F1/F6)。"""
    requests = sum(r.requests for r in rows)
    success = sum(r.success for r in rows)
    errors = sum(r.errors for r in rows)
    prompt = sum(r.prompt_tokens for r in rows)
    completion = sum(r.completion_tokens for r in rows)
    cache_hits = sum(r.cache_hits for r in rows)
    weighted = sum(r.avg_latency_ms * r.requests for r in rows)
    updated = max((r.updated_at for r in rows), default=None)
    return {
        "total_requests": requests,
        "success": success,
        "errors": errors,
        "error_rate": _rate(errors, requests),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "avg_latency_ms": round(weighted / requests) if requests else 0,
        "p95_latency_ms": max((r.p95_latency_ms for r in rows), default=0),
        "cache_hit_rate": _rate(cache_hits, requests),
        "cache_hits": cache_hits,
        # 汇总表由后台任务异步刷新(FR-F6):前端据此提示"数据更新于 X",避免被当作实时值
        "summary_updated_at": updated.isoformat() if updated else None,
    }


def _trend(rows: list[UsageSummary]) -> list[dict]:
    return [
        {
            "date": r.bucket_date.isoformat(),
            "requests": r.requests,
            "success": r.success,
            "errors": r.errors,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "avg_latency_ms": r.avg_latency_ms,
            "p95_latency_ms": r.p95_latency_ms,
            "cache_hit_rate": _rate(r.cache_hits, r.requests),
        }
        for r in rows
    ]


def _by_key(db: Session, start: date, end: date) -> list[dict]:
    """按 Key 分组(有界区间聚合,汇总表无 Key 维度)。"""
    since, until = _day_bounds(start, end)
    rows = db.execute(
        select(
            LlmUsageRecord.key_id,
            ApiKey.name,
            ApiKey.key_prefix,
            func.coalesce(
                func.sum(
                    case((LlmUsageRecord.status.in_(QUOTA_CONSUMING_STATUSES), 1), else_=0)
                ),
                0,
            ),
            func.coalesce(func.sum(LlmUsageRecord.prompt_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.completion_tokens), 0),
            func.coalesce(
                func.sum(case((LlmUsageRecord.cache_hit.is_(True), 1), else_=0)), 0
            ),
        )
        .select_from(LlmUsageRecord)
        .outerjoin(ApiKey, ApiKey.id == LlmUsageRecord.key_id)
        .where(LlmUsageRecord.created_at >= since, LlmUsageRecord.created_at < until)
        .group_by(LlmUsageRecord.key_id, ApiKey.name, ApiKey.key_prefix)
        .order_by(LlmUsageRecord.key_id)
    ).all()
    return [
        {
            "key_id": key_id,
            # 绝不返回完整 Key:JWT 调用没有 Key,归到独立的 jwt 分组
            "key_prefix": key_prefix or "jwt",
            "name": name or "JWT 登录态",
            "requests": int(requests),
            "total_tokens": int(prompt) + int(completion),
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "cache_hits": int(hits),
        }
        for key_id, name, key_prefix, requests, prompt, completion, hits in rows
    ]


def _latency_distribution(db: Session, start: date, end: date) -> list[dict]:
    """延迟分桶直方图(有界区间条件计数聚合)。"""
    since, until = _day_bounds(start, end)
    columns = []
    for _label, low, high in LATENCY_BUCKETS:
        condition = LlmUsageRecord.latency_ms >= low
        if high is not None:
            condition = condition & (LlmUsageRecord.latency_ms < high)
        columns.append(func.coalesce(func.sum(case((condition, 1), else_=0)), 0))
    counts = db.execute(
        select(*columns)
        .select_from(LlmUsageRecord)
        .where(LlmUsageRecord.created_at >= since, LlmUsageRecord.created_at < until)
    ).one()
    return [
        {"bucket": label, "count": int(count)}
        for (label, _low, _high), count in zip(LATENCY_BUCKETS, counts)
    ]


def _quota_rejected(db: Session, start: date, end: date) -> int:
    """被配额拦下的次数(审计口径,不计入 quota-consuming 的 requests)。"""
    since, until = _day_bounds(start, end)
    return int(
        db.execute(
            select(func.count(LlmUsageRecord.id))
            .select_from(LlmUsageRecord)
            .where(
                LlmUsageRecord.created_at >= since,
                LlmUsageRecord.created_at < until,
                LlmUsageRecord.status == STATUS_QUOTA_REJECTED,
            )
        ).scalar_one()
    )


def _day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(start, datetime.min.time()),
        datetime.combine(end, datetime.min.time()),
    )


@router.get("", summary="用量看板数据")
def dashboard(
    range_: str = Query(default="month", alias="range", description="today/week/month/custom"),
    start: date | None = Query(default=None, description="自定义区间起始日(range=custom 时必填)"),
    end: date | None = Query(default=None, description="自定义区间截止日(range=custom 时必填)"),
    ctx: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    start_day, end_day = resolve_range(range_, start, end)
    rows = daily_rows(db, start_day, end_day)  # 只读汇总表(FR-F6)
    summary = _summarize(rows)
    summary["quota_rejected"] = _quota_rejected(db, start_day, end_day)
    return ok(
        {
            "range": {
                "kind": range_,
                "start": start_day.isoformat(),
                "end": (end_day - timedelta(days=1)).isoformat(),
                "days": (end_day - start_day).days,
                "tenant_id": ctx.tenant_id,
            },
            "summary": summary,
            "trend": _trend(rows),
            "by_key": _by_key(db, start_day, end_day),
            "latency_distribution": _latency_distribution(db, start_day, end_day),
            "generated_at": datetime.now().isoformat(),
        }
    )
