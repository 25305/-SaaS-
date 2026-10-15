# 智能问答 SaaS 平台

后端位于 `问答平台/backend`，Python 3.10+。当前开发期默认使用 SQLite；生产通过 `DATABASE_URL` 配置 MySQL。**切换生产前请覆盖默认 `JWT_SECRET`，禁用演示种子账号并验证 MySQL 备份/恢复。**

```bash
cd 问答平台/backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 新终端（同目录）
python -m pytest tests -q
```

- `/docs`：API 文档；`/health`：数据库、缓存、模型服务状态。未配置 `MODEL_BASE_URL` 时模型是 `unconfigured`，整体返回 HTTP 503（不伪装为健康）。
- `/api/kb/`：知识库与文档；`/api/usage/records`、`/api/usage/summary`：按租户隔离的用量明细与汇总。
- `/api/v1/apikeys`：API Key 创建/列表/轮换/吊销（完整值仅创建与轮换时返回一次，列表只回掩码）；`/api/v1/quota/status`、`/api/v1/usage/by-key`：配额状态与按 Key 分组用量；`/api/v1/qa/ask`：问答入口，支持 `X-API-Key` 或 Bearer JWT，模型调用前先做配额预检。
- 日志默认输出 JSON，带 `X-Request-Id`、租户、路径、状态码与耗时，绝不记录完整 Key/密码；模型调用日志带 `usage_source`（Token 取自模型响应的 `usage`，不估算）。
- [隔离验证报告](问答平台/docs/隔离验证报告.md) · [数据库设计](问答平台/docs/数据库设计.md) · [运维手册](问答平台/docs/运维手册.md) · [配额与计量说明](问答平台/docs/配额与计量说明.md) · [测试报告](问答平台/backend/tests/reports/isolation_report.md) · [配额计量测试报告](问答平台/backend/tests/reports/quota_report.md)

**当前范围：**已实现知识库、文档与用量流水隔离、可观测性，以及 **API Key 全生命周期**、**配额预检与计量流水（模块 C/D）**；配额拦截、Key 吊销/轮换已可执行并即时生效。**知识块、账单导出、限流执行（属成员 D 的 E 模块）与真实模型服务尚未接入**，错误码与预留字段不等于这些模块已完成；未配置真实上游时问答走内置本地模拟模型（`provider=local-simulation`），其用量如实标注来源、不伪装为真实供应商。
