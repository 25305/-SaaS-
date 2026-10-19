# 模块 C/D 自动化测试记录：API Key 管理与配额计量

- 执行日期：2026-10-15
- 执行环境：Python 3.11.16 / FastAPI 0.115.0 / SQLAlchemy 2.0.36 / Pydantic 2.14.1 / pytest 8.3.4 / httpx 0.28.1 / 临时 SQLite（`tmp_path`，不接触项目 `qa.db`）
- 命令：

```bash
# 仅本模块
python -m pytest tests/test_quota.py -q --disable-warnings
# 全量回归（含成员 B 的隔离与可观测性用例）
python -m pytest tests -q --disable-warnings
```

- 结果：

```text
tests/test_quota.py ....................  [100%]
20 passed, 1 warning in 3.41s

tests（全量） ...................................................  [100%]
51 passed, 1 warning in 6.19s
```

即 **本模块 20 passed / 0 failed**，**全量 51 passed / 0 failed**（31 条成员 B 既有用例无回归）。唯一 warning 来自第三方库的 `datetime.utcnow()` deprecation，与本次改动无关。

---

## 一、三条生死线的针对性验证

| 红线（错一步即验收不合格） | 覆盖用例 | 断言方式 |
|---|---|---|
| **FR-D3 预检在模型调用之前** | `test_quota_rejected_without_model_call` | 注入计数用模型替身，断言拒绝路径上 `llm_call_counter` **未增长** |
| **FR-D2 Token 必须来自 `response.usage`** | `test_token_source_is_response_usage`、`test_cache_hit_is_metered_with_zero_tokens` | 断言日志 `llm_call_completed.usage_source == "response.usage"`，且 Token 数等于替身返回的 usage，不是估算 |
| **FR-D4 流水不可变** | `test_usage_record_is_append_only`、`test_quota_precheck_reads_summary_not_scanning_records` | 静态扫描 `app/` 无对 `LlmUsageRecord` 的 UPDATE/DELETE；预检数据源必须是 `usage_summary` |

---

## 二、需求覆盖矩阵

| 需求 | 用例 | 结果 |
|---|---|---|
| FR-C1 创建 Key | `test_key_lifecycle_create_list_rotate_revoke` | 通过 |
| FR-C2 完整值仅一次 / 库中只有哈希与前后缀 | `test_key_lifecycle_create_list_rotate_revoke`、`test_no_plaintext_key_anywhere` | 通过 |
| FR-C3 轮换给 24h 窗口 / 吊销立即失效 | `test_key_lifecycle_create_list_rotate_revoke`、`test_expired_key_is_rejected_and_revocation_is_immediate` | 通过 |
| FR-C4 单 Key 配额独立 | `test_key_quota_isolated_within_tenant`、`test_key_level_quota_falls_back_when_null` | 通过 |
| FR-C5 按 Key 反查并分组记用量 | `test_key_quota_isolated_within_tenant`（读 `/api/v1/usage/by-key`） | 通过 |
| FR-C6 记录最后使用时间与来源 IP | `test_key_lifecycle_create_list_rotate_revoke` | 通过 |
| FR-C1/C3 越权与角色校验 | `test_api_key_cannot_access_other_tenant_key`、`test_key_management_requires_admin` | 通过 |
| FR-D1 Token + 请求次数双维度 | `test_quota_rejected_response_uses_unified_error_shape`（请求维度）、`test_quota_rejected_without_model_call`（Token 维度） | 通过 |
| FR-D2 真实 Token，不估算 | `test_token_source_is_response_usage` | 通过 |
| FR-D3 预检在模型调用之前 + 429 含 used/reset_at | `test_quota_rejected_without_model_call`、`test_quota_rejected_response_uses_unified_error_shape` | 通过 |
| FR-D4 不可变流水 / 聚合由明细派生 | `test_usage_record_is_append_only`、`test_usage_summary_is_derived_from_records`、`test_usage_summary_has_unique_bucket_per_tenant` | 通过 |
| FR-D5 自然月惰性重置 | `test_period_reset_by_backdating`、`test_period_reset_clears_previous_month_usage` | 通过 |
| FR-D6 超额行为可配置且实际生效 | `test_soft_overage_policy_allows_request`、`test_reject_policy_actually_blocks` | 通过 |
| 被拒请求只留审计痕迹、不放大配额 | `test_rejected_request_leaves_audit_record_but_no_quota_usage` | 通过 |
| 缓存命中口径（次数照记、Token 记 0） | `test_cache_hit_is_metered_with_zero_tokens` | 通过 |

---

## 三、任务书 §五 自测清单逐项核对

| # | 自测项 | 证据 | 结论 |
|---|---|---|---|
| 1 | `api_key` 表无明文，`key_hash` 是 64 位十六进制 | `test_no_plaintext_key_anywhere`；端到端演示第五节 | ✅ |
| 2 | 创建接口返回完整 Key，列表接口只返回 `masked` | `test_key_lifecycle_create_list_rotate_revoke`（断言列表响应体不含完整值） | ✅ |
| 3 | 吊销后立即 401 | `test_key_lifecycle_create_list_rotate_revoke`、演示第四节 | ✅ |
| 4 | 轮换后旧 Key 有 24h 过期时间，不是立即失效 | 断言 `23h < expire_at - now <= 24h`，且窗口内旧 Key 仍可用 | ✅ |
| 5 | 单 Key 配额独立，不影响同租户其他 Key | `test_key_quota_isolated_within_tenant`；演示第三节（受限 Key 二次 429 / 另一把 Key 200） | ✅ |
| 6 | Token 取自 `response.usage`，日志有 `usage_source` | `test_token_source_is_response_usage` | ✅ |
| 7 | 配额预检在模型调用之前，`llm_call_counter` 未增长 | `test_quota_rejected_without_model_call`；演示第一节（拒绝路径新增调用数 = 0） | ✅ |
| 8 | 429 响应含 `used` 与 `reset_at` | 断言 `data.used_tokens/limit_tokens/used_requests/limit_requests/reset_at` 齐全 | ✅ |
| 9 | 流水表无任何 UPDATE 语句 | `test_usage_record_is_append_only`（静态扫描 `app/`） | ✅ |
| 10 | 周期重置可通过改 `period_start` 模拟验证 | `test_period_reset_by_backdating`（回拨 40 天后自动回到当月 1 日） | ✅ |
| 11 | 超额行为 `reject` / `soft_warn` 都实际生效 | `test_reject_policy_actually_blocks` + `test_soft_overage_policy_allows_request`（同一份配额、仅换策略，结果不同） | ✅ |

---

## 四、端到端演示证据（2026-10-15，临时 SQLite，真实 HTTP 调用）

在隔离副本库上启动真实应用（`TestClient` + `get_db` 覆盖），用真实 `X-API-Key` 与 JWT 调用 `/api/v1/qa/ask`，输出如下（换行已整理）：

```text
=== A. reject:429 + 未调用模型 ===
请求1: 200 tokens= 34
请求2: 429 {"used_tokens": 34, "limit_tokens": 1, "used_requests": 1, "limit_requests": 100000, "reset_at": "2026-11-01"}
拒绝路径新增模型调用数 = 0

=== B. soft_warn:200 + 告警头 ===
status= 200 X-Quota-Warning= overage

=== C. 租户配额放开,单 Key 配额独立 ===
创建返回完整值一次,列表只回掩码: sk-uj-bwqqw…pXeI | sk-uj-bwqqw...pXeI
受限 Key 首次=200 二次=429 | 同租户另一把 Key=200

=== D. 吊销立即失效 ===
DELETE= 200
吊销后: 401 API_KEY_INVALID

=== E. 明文 Key 检查 ===
按列值扫描命中: 无
完整 Key 出现在库文件: 无
api_key 表: [('sk-uj-bwqqw', 64, 'active'), ('sk-t8d3yKfS', 64, 'revoked')]

=== F. 按 Key 用量 / 配额状态 ===
[{"key_id": null, "request_count": 2, ..., "quota_rejected": 1, "masked": "jwt(登录态)"},
 {"key_id": 1, "request_count": 1, ..., "quota_rejected": 0, "masked": "sk-uj-bwqqw...pXeI"},
 {"key_id": 2, "request_count": 1, ..., "quota_rejected": 1, "masked": "sk-t8d3yKfS...BaYo"}]
{"allowed": true, "used_tokens": 130, "limit_tokens": null, "used_requests": 4, "limit_requests": null,
 "reset_at": "2026-11-01", "policy": "reject", "key_id": null}
```

要点：

- 超额请求返回 **429 `QUOTA_EXCEEDED`**，`data` 含已用量与 `reset_at`，且**该 request_id 下没有 `llm_call_completed` 日志**——预检确实在模型调用之前（FR-D3 的现场证据）。
- 同一份超限用量，`overage_policy=soft_warn` 时放行并回写 `X-Quota-Warning: overage`。
- 单 Key 配额独立：受限 Key 二次调用 429，同租户未设 Key 级配额的 Key 仍 200。
- 全文扫描无明文 Key；`key_hash` 全部为 64 位十六进制。

> **关于"全库搜索明文"的一个本地化说明**：对 SQLite **原始文件字节**直接做 `sk-[A-Za-z0-9_-]{20,}` 扫描会出现**假阳性**——SQLite 行内各列紧密相邻，`key_prefix`（`sk-`+8 位）会与紧随其后的 `key_suffix`（4 位）、`status`（`active`）、`created_at` 连成一串，凑够 20 字符以上。上表第四节改用两条更严格的判据：**逐列值扫描**（等价于 `mysqldump`/`.dump` 的带引号输出，列间有分隔符，无假阳性）与**完整明文 Key 的子串查找**，两者均为 0 命中。该项**不影响**上表第 1 项自测结论（`key_hash` 是 64 位十六进制）。

---

## 五、已知边界与遗留说明（不夸大）

| 项 | 现状 | 影响 / 后续 |
|---|---|---|
| `FOR UPDATE` 行锁 | 仅 MySQL 生效；SQLite 端按方言跳过（`app/services/quota/service.py::ensure_current_period`） | 开发期并发跨月重置无锁保护；生产切 MySQL 后生效，需在 MySQL 上补一次并发演练 |
| 预检数据源精度 | 租户口径读 `usage_summary` 聚合表 | 存在刷新延迟，临界点可能"多用"一点，见《配额与计量说明.md》取舍说明 |
| 本地模型与真实模型 | 未配置 `MODEL_BASE_URL` 时用 `LocalSimulationClient`（`provider=local-simulation`） | 其 usage 由内置分词器统计并**如实标注来源**；`usage_source` 统一为 `"response.usage"`，无需改计量代码。真实供应商（阿里云百炼 OpenAI 兼容端点）**已由后续批次接入并实测**，Token 取自 `response.usage` 的计量口径保持成立，见《运维手册》第 1 节 |
| 隔离例外 | `resolve_api_key` 在无租户上下文时按全局唯一 `key_hash` 反查 | 已在代码与文档标注，待**成员 B 评审确认**（等价于按唯一键取单行，不构成越权） |
| 限流 | `rate_limit_per_minute/per_hour` 字段由本批次定稿并随 Key 暴露 | 拦截执行**已由后续批次（成员 D 的 E 模块）补齐**：滑动窗口限流（Key 与租户双维度）在模型调用前执行，超限返回 429 `RATE_LIMITED` 并附 `retry_after`，见 `tests/test_rate_limit_cache.py`；本批次自身只提供配置与契约 |
| 覆盖范围 | 未验证多进程并发拒绝竞态；真实供应商 usage 解析已由后续批次实测（MySQL 建表与冒烟已于 2026-10-18 完成，见《运维手册》第 3 节） | 仍需在独立测试库补 MySQL 并发演练 |

---

## 六、结论

模块 C/D 交付范围内 **20 条自动化用例全部通过**，成员 B 的 31 条既有用例无回归（全量 51 passed）。任务书 §五 自测清单 **11 项全部满足**，三条生死线（预检顺序、Token 来源、流水不可变）均有可复现的断言与现场日志佐证。剩余项均为已声明的边界（MySQL 行锁并发演练、多进程限流需切 Redis 共享计数），不影响本模块验收结论；限流执行与真实供应商接入已由后续批次补齐。
