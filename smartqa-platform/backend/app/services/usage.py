# -*- coding: utf-8 -*-
"""用量记录与统计(模块 B,FR-B4)。

- record_request_usage:请求中间件在请求结束后调用,写入一条用量流水;
  这里使用独立 Session,从已校验的 request.state 获取 tenant_id 并设置 Session 上下文,
  让写入同样经过数据层租户守卫。
- summary:按租户聚合,依赖 app/db/tenant_hook.py 的 ORM 事件自动注入租户条件,
  端点代码不手写 where tenant_id。
"""

from typing import Optional

from sqlalchemy import case, func, select

from app.core.logging import logger
from app.db.session import SessionLocal
from app.models import UsageRecord
from app.db.tenant_hook import TENANT_ID_KEY


def record_request_usage(
    *,
    tenant_id: Optional[int],
    method: str,
    path: str,
    status_code: int,
    latency_ms: int,
    api_key_id: Optional[int] = None,
) -> None:
    """写入一条用量流水。任何异常都被吞掉,绝不影响正常请求。"""
    if tenant_id is None:
        return
    db = SessionLocal()
    db.info[TENANT_ID_KEY] = tenant_id
    try:
        db.add(
            UsageRecord(
                tenant_id=tenant_id,  # 显式指定所属租户
                api_key_id=api_key_id,
                method=method,
                path=path,
                status_code=status_code,
                latency_ms=latency_ms,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.error("usage_record_failed", extra={"fields": {"tenant_id": tenant_id}})
    finally:
        db.close()


def summarize(db) -> dict:
    """统计当前租户的用量汇总。db 必须已带租户上下文(隔离条件自动注入)。"""
    row = db.execute(
        select(
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.latency_ms), 0),
            func.coalesce(func.avg(UsageRecord.latency_ms), 0.0),
            func.coalesce(func.sum(case((UsageRecord.status_code >= 400, 1), else_=0)), 0),
        )
    ).one()
    total, total_latency, avg_latency, error_count = row
    return {
        "total_requests": int(total or 0),
        "error_requests": int(error_count or 0),
        "total_latency_ms": int(total_latency or 0),
        "avg_latency_ms": round(float(avg_latency or 0.0), 2),
    }
