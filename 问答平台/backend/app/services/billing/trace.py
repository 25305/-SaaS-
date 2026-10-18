# -*- coding: utf-8 -*-
"""账单可追溯性自动校验(模块 F,FR-F5)。

验收原文是"抽查 5 行能在流水中查到"。人工抽查既慢又不能回归,所以把这条要求
变成可执行的断言:重算汇总表的账单数字,与 `usage_record` 明细的 SUM/COUNT 逐项比对。

## 为什么能证明"可追溯"
汇总表的每个数字都由明细派生(见 `app/services/metering/service.py::refresh_summary`
与 `tests/test_quota.py::test_usage_summary_is_derived_from_records`)。本函数把该等式
在任意周期上重新验证一遍:
- 一致 → 账单上的每个数字都能在流水中找到来源(`traceable=True`);
- 不一致 → 明确指出是哪一项对不上,便于定位是聚合漏算还是查询口径差异。
"""

from datetime import date, datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.tenant_hook import TENANT_ID_KEY
from app.models import LlmUsageRecord
from app.services.billing.service import build_bill, parse_period
from app.services.metering.service import QUOTA_CONSUMING_STATUSES


def _day_start(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time())


def _ensure_tenant_context(db: Session, tenant_id: int | None) -> None:
    """账单校验可能被脚本直接调用(SessionLocal())而不经请求依赖。

    隔离层 fail-closed:没有租户上下文时查询业务表会被拒绝。这里显式设置上下文
    而不是 bypass —— 后台脚本与请求走完全相同的隔离路径,不给自己开后门。
    """
    if tenant_id is not None:
        db.info[TENANT_ID_KEY] = tenant_id


def verify_bill_traceable(
    db: Session,
    tenant_id: int | None = None,
    period: str = "month",
    today: date | None = None,
) -> dict:
    """校验账单合计能否在流水明细中查到对应记录,返回结构化的比对结果。"""
    _ensure_tenant_context(db, tenant_id)
    start, end = parse_period(period, today=today)
    bill = build_bill(db, start, end)
    since, until = _day_start(start), _day_start(end)

    totals = db.execute(
        select(
            func.count(LlmUsageRecord.id),
            func.coalesce(func.sum(LlmUsageRecord.prompt_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.completion_tokens), 0),
        )
        .select_from(LlmUsageRecord)
        .where(
            LlmUsageRecord.created_at >= since,
            LlmUsageRecord.created_at < until,
            LlmUsageRecord.status.in_(QUOTA_CONSUMING_STATUSES),
        )
    ).one()

    cache_hits = db.execute(
        select(
            func.coalesce(
                func.sum(case((LlmUsageRecord.cache_hit.is_(True), 1), else_=0)), 0
            )
        )
        .select_from(LlmUsageRecord)
        .where(LlmUsageRecord.created_at >= since, LlmUsageRecord.created_at < until)
    ).scalar_one()

    checks = {
        "requests": (bill["total_requests"], int(totals[0])),
        "prompt_tokens": (bill["total_prompt_tokens"], int(totals[1])),
        "completion_tokens": (bill["total_completion_tokens"], int(totals[2])),
        "cache_hits": (bill["cache_hits"], int(cache_hits)),
    }
    mismatches = {
        name: {"bill": pair[0], "detail": pair[1]}
        for name, pair in checks.items()
        if pair[0] != pair[1]
    }
    return {
        "traceable": not mismatches,
        "period": bill["period"],
        "checked": {name: pair[0] for name, pair in checks.items()},
        "mismatches": mismatches,
    }


__all__ = ["verify_bill_traceable"]
