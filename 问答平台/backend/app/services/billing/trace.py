"""账单导出与不可变用量流水的可追溯性校验。"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tenant_hook import TENANT_BYPASS_KEY, TENANT_ID_KEY
from app.models import LlmUsageRecord


def verify_bill_traceable(
    db: Session, *, tenant_id: int, days: int = 30, sample_size: int = 5
) -> dict:
    """抽样核对账单行是否能按 request_id 回溯到同一条流水。"""
    previous_tenant = db.info.get(TENANT_ID_KEY)
    previous_bypass = db.info.get(TENANT_BYPASS_KEY)
    db.info.pop(TENANT_BYPASS_KEY, None)
    db.info[TENANT_ID_KEY] = tenant_id
    try:
        since = datetime.now() - timedelta(days=days)
        rows = db.execute(
            select(LlmUsageRecord)
            .where(LlmUsageRecord.created_at >= since)
            .order_by(LlmUsageRecord.id)
            .limit(sample_size)
        ).scalars().all()
        if len(rows) < sample_size:
            return {"traceable": False, "checked": [], "reason": f"账期内仅 {len(rows)} 条流水，未达到抽查 {sample_size} 条的验收条件"}
        checked = []
        for row in rows:
            found = db.execute(
                select(LlmUsageRecord).where(
                    LlmUsageRecord.id == row.id,
                    LlmUsageRecord.request_id == row.request_id,
                )
            ).scalar_one_or_none()
            bill_entry = {
                "id": row.id, "request_id": row.request_id, "key_id": row.key_id,
                "prompt_tokens": row.prompt_tokens, "completion_tokens": row.completion_tokens,
                "latency_ms": row.latency_ms, "cache_hit": row.cache_hit,
            }
            matches = found is not None and all(getattr(found, field) == value for field, value in bill_entry.items() if field != "id")
            checked.append({"id": row.id, "request_id": row.request_id, "found": matches})
        return {"traceable": all(item["found"] for item in checked), "checked": checked}
    finally:
        if previous_tenant is None:
            db.info.pop(TENANT_ID_KEY, None)
        else:
            db.info[TENANT_ID_KEY] = previous_tenant
        if previous_bypass is None:
            db.info.pop(TENANT_BYPASS_KEY, None)
        else:
            db.info[TENANT_BYPASS_KEY] = previous_bypass
