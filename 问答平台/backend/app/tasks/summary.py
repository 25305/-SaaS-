# -*- coding: utf-8 -*-
"""用量汇总任务(模块 F,FR-F6 异步化)。

## 为什么必须异步
`usage_record` 随调用量线性增长,若在看板接口里对它做 `SUM()` 聚合,接口耗时会随
数据量一起恶化。本任务在后台把明细预聚合进 `usage_summary`,看板/账单只查汇总表,
接口耗时与数据量无关(恒定)。

## 与 metering 服务的关系
聚合算法不在本文件重复实现:`app/services/metering/service.py::refresh_summary()`
既是请求路径上的实时聚合入口(写入流水后立刻刷新当天),也是本定时任务复用的
唯一实现。这样"实时"与"异步"两条路径的口径不会漂移。

## 调度器为什么不用 apscheduler
项目 requirements.txt 未引入 apscheduler;汇总任务只是"每 N 秒跑一次"的简单循环,
用标准库 `threading.Thread` + `Event` 即可,零新增依赖。若部署到多进程/多机环境,
应改为独立 worker(或以分布式锁保证单实例),本文件已把入口收敛为 `refresh_all()`。
"""

from datetime import date, timedelta
from threading import Event, Thread

from sqlalchemy import select

from app.config import settings
from app.core.logging import logger
from app.db.session import SessionLocal
from app.db.tenant_hook import TENANT_ID_KEY
from app.models import Tenant
from app.services.metering.service import refresh_summary

_stop = Event()


def refresh_all(lookback_days: int | None = None) -> int:
    """刷新所有租户最近 N 天(含今日)的聚合并返回刷新的 (租户×天) 行数。

    隔离说明:Tenant 属身份层表,不受租户过滤约束,可跨租户列出;而每条聚合计
    算**必须在目标租户的上下文内执行**(显式设置 `session.info[TENANT_ID_KEY]`),
    不使用 bypass —— 后台任务同样受隔离层约束,避免"定时任务绕过隔离"的口子。
    """
    days = lookback_days or settings.SUMMARY_REFRESH_LOOKBACK_DAYS
    start = date.today() - timedelta(days=max(1, days) - 1)

    with SessionLocal() as db:
        tenant_ids = list(db.execute(select(Tenant.id).order_by(Tenant.id)).scalars())

    refreshed = 0
    for tenant_id in tenant_ids:
        with SessionLocal() as db:
            db.info[TENANT_ID_KEY] = tenant_id
            day = start
            while day <= date.today():
                refresh_summary(db, tenant_id, day)
                refreshed += 1
                day += timedelta(days=1)
    return refreshed


def run_once() -> int:
    """同步刷新一次(供手动触发/验收脚本调用),失败时抛出,便于调用方感知。"""
    return refresh_all()


def _loop(interval: int) -> None:
    while not _stop.wait(interval):
        try:
            rows = refresh_all()
            logger.info("usage_summary_refreshed", extra={"fields": {"rows": rows}})
        except Exception:
            # 常见坑:汇总任务静默失败会让看板数据停滞。这里显式记录并继续下一轮。
            logger.exception("usage_summary_refresh_failed")


def start_scheduler(interval: int | None = None) -> Thread | None:
    """启动后台汇总线程;interval<=0 时不启动(便于测试与本地按需刷新)。"""
    seconds = settings.SUMMARY_REFRESH_INTERVAL_SECONDS if interval is None else interval
    if seconds <= 0:
        return None
    _stop.clear()
    thread = Thread(target=_loop, args=(seconds,), name="usage-summary-refresh", daemon=True)
    thread.start()
    logger.info("usage_summary_scheduler_started", extra={"fields": {"interval_seconds": seconds}})
    return thread


def stop_scheduler() -> None:
    """通知后台线程退出(应用关闭时调用)。"""
    _stop.set()
