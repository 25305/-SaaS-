# 隔离与可观测性自动化测试记录

- 执行日期：2026-10-15
- 命令：`PYTHONPATH=<backend> python -m pytest <backend>/tests -q --disable-warnings`
- 环境：Python 3.13.12 / SQLAlchemy 2.0.36 / FastAPI 0.115.0 / 临时 SQLite
- 结果：**31 passed，0 failed，0 skipped**；161 条第三方 deprecation warnings（SQLAlchemy 中 `datetime.utcnow()`、Starlette/AnyIO 别名），不影响通过状态，但后续应升级处理。

## 资源矩阵（只统计已实现资源）

| 资源/能力 | 覆盖点 | 结果 |
|---|---|---|
| 知识库 | 跨租户 GET/PATCH/DELETE | 3/3 被拒绝，404 与不存在 ID 的响应完全一致 |
| 文档归属知识库 | 跨租户 GET 列表/POST 新增 | 2/2 被拒绝，404 与不存在 ID 的响应完全一致 |
| 文档 | 跨租户 GET/PATCH/DELETE | 3/3 被拒绝，404 与不存在 ID 的响应完全一致 |
| 知识库列表 | B 看不到 A、A 可正常读取 | 通过 |
| 用量明细/汇总 | A 的用量 B 看不到、A 可见 | 通过 |
| ORM 别名、join、聚合、Session.get | 查询注入隔离条件 | 通过 |
| 缺上下文、越权对象写、批量 DML | fail-closed；数据未被篡改 | 通过 |
| 业务模型混入/业务路由原生 SQL 检查 | 守护测试 | 通过 |
| 日志、请求 ID、五类错误码 | 字段完整、响应回写、状态正确 | 通过 |
| 健康检查 | 缺配置→503；全 up→200；超时→503 | 通过 |

> **现场重放**：在隔离副本数据库上启动真实服务，再运行 `scripts/test_isolation.py`，结果 27/27 项跨租户攻击被拒绝，0 失败、0 泄露；7 项归属资源阴性对照正常，见 `docs/越权测试报告.md`。注意 8 条参数化用例与 27 项现场攻击覆盖有重叠，不合并计数。本报告批次执行时，成员 B 任务书示例中的 `api_key`、`knowledge_chunk`、账单导出尚未实现，故不虚构其越权测试；其中 `api_key` 与账单导出**已由后续批次实现**，其隔离性由各模块自带用例覆盖（`tests/test_admin_quota_overview.py`、`tests/test_dashboard_billing.py`），`knowledge_chunk`（知识块）仍未实现。
