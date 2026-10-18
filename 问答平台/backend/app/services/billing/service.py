# -*- coding: utf-8 -*-
"""账单构建与导出(模块 F,FR-F4)。

## 数据来源与"不扫全表"的取舍
- **按日汇总行**(`daily_rows`)与**账单合计**(`build_bill`)只读 `usage_summary`
  —— 这是 FR-F6 的硬性要求,汇总表按 `(tenant_id, bucket_date)` 索引命中,耗时恒定。
- **明细行**(`detail_rows`)按需从 `usage_record` 取"有界区间 + limit"的记录,
  用于账单页展示与可追溯导出:FR-F5 要求"抽查 5 行能在流水中查到",
  明细本身就是流水,导出的每一行都带 `request_id`,可直接回查日志。
  该查询带 `created_at` 区间与 `limit`,是有界范围扫描(命中复合索引),不是全表扫描。

所有查询都在调用方的租户上下文内执行,租户条件由 `app/db/tenant_hook.py` 自动注入。
"""

import csv
import io
import re
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.models import LlmUsageRecord, UsageSummary

_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# 明细默认导出上限:即使调用方不传 limit,也必须给一个有界值,避免无界导出
DEFAULT_DETAIL_LIMIT = 5000
MAX_DETAIL_LIMIT = 50000


def parse_period(period: str, today: date | None = None) -> tuple[date, date]:
    """把计费周期解析为 [start, end) 半开区间。

    支持:`today` / `week`(近 7 天)/ `month`(自然月至今)/ `YYYY-MM`(整月)/ `YYYY-MM-DD`(单日)。
    非法写法一律 422,避免静默返回错误区间导致账单数字看起来"对但算错"。
    """
    today = today or date.today()
    key = (period or "month").strip().lower()

    if key == "today":
        return today, today + timedelta(days=1)
    if key == "week":
        return today - timedelta(days=6), today + timedelta(days=1)
    if key == "month":
        return today.replace(day=1), today + timedelta(days=1)

    match = _PERIOD_RE.match(key)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            raise BizError("VALIDATION_ERROR", "计费周期月份非法", 422)
        start = date(year, month, 1)
        end = date(year + (month // 12), month % 12 + 1, 1)
        return start, end

    match = _DAY_RE.match(key)
    if match:
        day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return day, day + timedelta(days=1)

    raise BizError(
        "VALIDATION_ERROR",
        "period 需为 today/week/month/YYYY-MM/YYYY-MM-DD",
        422,
    )


def _day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())


def daily_rows(db: Session, start: date, end: date) -> list[UsageSummary]:
    """按自然日的汇总行(只读 usage_summary,FR-F6)。"""
    return list(
        db.execute(
            select(UsageSummary)
            .select_from(UsageSummary)
            .where(UsageSummary.bucket_date >= start, UsageSummary.bucket_date < end)
            .order_by(UsageSummary.bucket_date)
        ).scalars()
    )


def build_bill(db: Session, start: date, end: date) -> dict:
    """账单合计(只读 usage_summary)。

    口径与 usage_summary 字段定稿一致:
    - requests/success/errors/prompt_tokens/completion_tokens/cache_hits 直接来自汇总;
    - avg_latency_ms 以 requests 为权重对"每日均值"再做加权平均,避免把低流量日的
      均值与高流量日的均值等权平均(那样会被噪声日拉偏);
    - p95_latency_ms 取区间内每日 P95 的最大值(保守口径:日 P95 的日最大值
      不会低估整体 P95,作为对外承诺更安全)。
    """
    rows = daily_rows(db, start, end)
    requests = sum(r.requests for r in rows)
    prompt = sum(r.prompt_tokens for r in rows)
    completion = sum(r.completion_tokens for r in rows)
    cache_hits = sum(r.cache_hits for r in rows)

    weighted = sum(r.avg_latency_ms * r.requests for r in rows)
    avg_latency = round(weighted / requests) if requests else 0
    p95_latency = max((r.p95_latency_ms for r in rows), default=0)

    return {
        "period": {"start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat()},
        "total_requests": requests,
        "success": sum(r.success for r in rows),
        "errors": sum(r.errors for r in rows),
        "total_prompt_tokens": prompt,
        "total_completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cache_hits": cache_hits,
        "avg_latency_ms": avg_latency,
        "p95_latency_ms": p95_latency,
        "days": len(rows),
        "generated_at": datetime.now().isoformat(),
    }


def detail_rows(
    db: Session,
    start: date,
    end: date,
    limit: int = DEFAULT_DETAIL_LIMIT,
) -> list[dict]:
    """账单明细(即流水本身,带 request_id 便于逐行回查,FR-F5)。

    有界查询:带 `created_at` 区间 + `limit`,命中 `ix_llmusage_tenant_created`。
    """
    since, until = _day_bounds(start, end)
    capped = min(max(1, limit), MAX_DETAIL_LIMIT)
    rows = db.execute(
        select(LlmUsageRecord)
        .select_from(LlmUsageRecord)
        .where(LlmUsageRecord.created_at >= since, LlmUsageRecord.created_at < until)
        .order_by(LlmUsageRecord.id.desc())
        .limit(capped)
    ).scalars()
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            "request_id": row.request_id,
            "key_id": row.key_id,
            "user_id": row.user_id,
            "model": row.model,
            "status": row.status,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "total_tokens": row.prompt_tokens + row.completion_tokens,
            "latency_ms": row.latency_ms,
            "cache_hit": row.cache_hit,
        }
        for row in rows
    ]


_DETAIL_COLUMNS = [
    "id", "created_at", "request_id", "key_id", "user_id", "model", "status",
    "prompt_tokens", "completion_tokens", "total_tokens", "latency_ms", "cache_hit",
]


def _detail_matrix(db: Session, start: date, end: date, limit: int) -> list[list]:
    return [
        [row[column] for column in _DETAIL_COLUMNS]
        for row in detail_rows(db, start, end, limit=limit)
    ]


def export_bill(
    db: Session,
    start: date,
    end: date,
    fmt: str = "csv",
    limit: int = DEFAULT_DETAIL_LIMIT,
) -> tuple[bytes, str, str]:
    """导出账单,返回 `(字节内容, media_type, 文件名)`。

    - `csv`:UTF-8 BOM 开头,Excel 直接双击不乱码;
    - `xlsx`:依赖 openpyxl(requirements.txt 已固定);缺失时降级为 CSV 并说明,
      而不是抛 500 —— 导出是"可选便利功能",不应因一个可选依赖缺失而不可用。
    """
    matrix = _detail_matrix(db, start, end, limit)
    stamp = f"{start.isoformat()}_{(end - timedelta(days=1)).isoformat()}"

    if fmt == "xlsx":
        try:
            from openpyxl import Workbook
        except ImportError:  # pragma: no cover - 环境缺依赖时的降级分支
            return _to_csv(matrix), "text/csv; charset=utf-8", f"bill_{stamp}.csv"

        book = Workbook()
        sheet = book.active
        sheet.title = "billing"
        sheet.append(["计费区间", start.isoformat(), (end - timedelta(days=1)).isoformat()])
        sheet.append(["生成时间", datetime.now().isoformat()])
        sheet.append([])
        sheet.append(_DETAIL_COLUMNS)
        for row in matrix:
            sheet.append(row)
        buffer = io.BytesIO()
        book.save(buffer)
        return (
            buffer.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            f"bill_{stamp}.xlsx",
        )

    return _to_csv(matrix), "text/csv; charset=utf-8", f"bill_{stamp}.csv"


def _to_csv(matrix: list[list]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(_DETAIL_COLUMNS)
    for row in matrix:
        writer.writerow(row)
    # utf-8-sig:带 BOM,Excel 打开不乱码
    return buffer.getvalue().encode("utf-8-sig")


__all__ = [
    "DEFAULT_DETAIL_LIMIT",
    "MAX_DETAIL_LIMIT",
    "build_bill",
    "daily_rows",
    "detail_rows",
    "export_bill",
    "parse_period",
]
