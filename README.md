# 智能问答 SaaS 平台

后端位于 `smartqa-platform/backend`，Python 3.10+。当前开发期默认使用 SQLite；生产通过 `DATABASE_URL` 配置 MySQL。**切换生产前请覆盖默认 `JWT_SECRET`，禁用演示种子账号并验证 MySQL 备份/恢复。**

```bash
cd smartqa-platform/backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 新终端（同目录）
python -m pytest tests -q
```

- `/docs`：API 文档；`/health`：数据库、缓存、模型服务状态。未配置 `MODEL_BASE_URL` 时模型是 `unconfigured`，整体返回 HTTP 503（不伪装为健康）。
- `/api/kb/`：知识库与文档；`/api/usage/records`、`/api/usage/summary`：按租户隔离的用量明细与汇总。
- 日志默认输出 JSON，带 `X-Request-Id`、租户、路径、状态码与耗时，绝不记录完整 Key/密码。
- [隔离验证报告](smartqa-platform/docs/隔离验证报告.md) · [数据库设计](smartqa-platform/docs/数据库设计.md) · [运维手册](smartqa-platform/docs/运维手册.md) · [测试报告](smartqa-platform/backend/tests/reports/isolation_report.md)

**当前范围：**已实现知识库、文档和用量流水隔离及可观测性；API Key、知识块、账单导出、配额/限流执行和真实模型服务尚未接入，错误码与预留字段不等于这些模块已完成。
