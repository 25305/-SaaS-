# -*- coding: utf-8 -*-
"""最小增量迁移:给既有库补建新增列(只加列,不改动任何列的语义)。

为什么需要:`Base.metadata.create_all` 只会新建缺失的**表**,不会给已存在的表加列。
开发库 `backend/qa.db` 是模块 A/B 阶段建的,升级到模块 C 后 `tenants` 表缺少配额列,
启动时查询 `Tenant` 会直接报 `no such column`。生产切 MySQL 后同理。

已登记的 Core SQL 豁免:`app/api/v1/health.py` 的 `SELECT 1` 之外,本文件是第二个
允许使用 `text()` 的入口——它只做 DDL,不读写任何租户业务数据行,因此不构成隔离绕过。
业务路由仍禁止原生 SQL,由 `tests/test_isolation.py::test_no_raw_sql_in_business_routes` 约束。

列定义在 SQLite / MySQL 下语法一致,可两边复用。
"""
from sqlalchemy import inspect, text

from app.core.logging import logger
from app.db.session import engine

# 表名 -> {列名: 列定义}
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "tenants": {
        "period_start": "DATE NULL",
        "quota_monthly_tokens": "BIGINT NULL",
        "quota_monthly_requests": "INT NULL",
        "overage_policy": "VARCHAR(20) NOT NULL DEFAULT 'reject'",
    },
}


def ensure_schema() -> None:
    """幂等:缺列才补,已有则跳过。启动时调用(Alembic 之前的最小过渡方案)。"""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    patches: dict[str, dict[str, str]] = {}
    for table, columns in _ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue  # 表尚未建立,create_all 会一次性建全,无需补列
        present = {c["name"] for c in inspector.get_columns(table)}
        missing = {name: ddl for name, ddl in columns.items() if name not in present}
        if missing:
            patches[table] = missing
    if not patches:
        return

    with engine.begin() as conn:
        for table, columns in patches.items():
            for name, ddl in columns.items():
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    logger.info(
        "schema_patched",
        extra={"fields": {"tables": ",".join(sorted(patches))}},
    )
