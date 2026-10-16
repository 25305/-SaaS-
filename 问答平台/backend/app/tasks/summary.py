"""用量汇总后台任务：每五分钟刷新最近两天的物化汇总。"""

from datetime import date, timedelta
from threading import Event, Thread

from sqlalchemy import select

from app.core.logging import logger
from app.db.session import SessionLocal
from app.db.tenant_hook import TENANT_BYPASS_KEY, TENANT_ID_KEY
from app.models import LlmUsageRecord
from app.services.metering.service import refresh_summary


def refresh_pending_summaries() -> int:
    """刷新有流水的租户/日期组合；失败只记录日志，不影响 HTTP 服务。"""
    db = SessionLocal()
    refreshed = 0
    try:
        db.info[TENANT_BYPASS_KEY] = True
        pairs = db.execute(
            select(LlmUsageRecord.tenant_id, LlmUsageRecord.created_at)
            .where(LlmUsageRecord.created_at >= date.today() - timedelta(days=1))
            .distinct()
        ).all()
        for tenant_id, created_at in pairs:
            db.info.pop(TENANT_BYPASS_KEY, None)
            db.info[TENANT_ID_KEY] = tenant_id
            refresh_summary(db, tenant_id, created_at.date())
            refreshed += 1
            db.info[TENANT_BYPASS_KEY] = True
        return refreshed
    except Exception:
        db.rollback()
        logger.exception("usage_summary_refresh_failed")
        return refreshed
    finally:
        db.close()


class SummaryScheduler:
    """不引入外部队列的轻量调度器；生产可替换为 Celery/cron。"""

    def __init__(self, interval_seconds: int = 300):
        self.interval_seconds = interval_seconds
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = Thread(target=self._run, name="usage-summary-refresh", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            refresh_pending_summaries()
            self._stop.wait(self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


scheduler = SummaryScheduler()
