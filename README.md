# 智能问答 SaaS 平台

多租户智能问答 SaaS：租户与账号体系、数据隔离、API Key 与配额计量、限流与缓存、用量看板与账单导出，前后端一体。

后端位于 `问答平台/backend`，Python 3.10+。数据库由 `DATABASE_URL` 决定：本机已在 MySQL 8.0 上建库 `smartqa_saas` 并实测通过（配置见 `问答平台/backend/.env`，键位见 `.env.example`）；不写 `.env` 时回退到零依赖的 SQLite 单文件 `qa.db`。**切换生产前请覆盖默认 `JWT_SECRET`，禁用演示种子账号并验证 MySQL 备份/恢复。**

## 快速开始

**Windows 一键启动**：双击仓库根目录的 `start.cmd` —— 自动检查环境、按需安装依赖、启动后端与前端并打开浏览器；关闭弹出的两个窗口即停止服务。

也可手动分两步启动。

后端：

```powershell
cd 问答平台/backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端（另开终端，仓库根目录）：

```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173，已配置 /api 代理到 127.0.0.1:8000
```

跑测试（工作目录 `问答平台/backend`）：

```powershell
python -m pytest tests -q      # 期望 63 passed
```

## 演示账号

启动时由 `app/seed.py` 幂等写入（每次启动重置密码/角色，保证演示可用）：

| 租户编码 | 用户名 | 密码 | 角色 | 说明 |
|---|---|---|---|---|
| `platform` | `platform_admin` | `Platform@123` | `super_admin` | 平台管理员：租户管理、全局用量 |
| `academic` | `academic_admin` | `Academic@123` | `admin` | 学术问答租户管理员 |
| `academic` | `academic_viewer` | `Viewer@123` | `readonly` | 只读成员（无 Key 管理权限） |
| `physics` | `physics_admin` | `Physics@123` | `admin` | 物理问答租户管理员 |

登录页点选演示账号即可一键填充。演示知识库（图书馆借阅规则、教务处选课须知、牛顿力学基础、物理学史）同时写入，详见 [数据来源清单](问答平台/docs/数据来源清单.md)。

## 接口与能力

- `/docs`：API 文档；`/health`：数据库、缓存、模型服务状态。未配置 `MODEL_BASE_URL` 时模型是 `unconfigured`，整体返回 HTTP 503（不伪装为健康）。
- `/api/auth/`：注册/登录/`me`/登出（jti 黑名单，登出后旧 Token 立即失效）。
- `/api/kb/`：知识库与文档；`/api/usage/records`、`/api/usage/summary`：按租户隔离的用量明细与汇总。
- `/api/v1/apikeys`：API Key 创建/列表/轮换/吊销（完整值仅创建与轮换时返回一次，列表只回掩码）。
- `/api/v1/quota/status`、`/api/v1/usage/by-key`：配额状态与按 Key 分组用量。
- `/api/v1/qa/ask`：问答入口，支持 `X-API-Key` 或 Bearer JWT；链路为**限流 → 配额预检 → 查缓存 → 调模型**，配额拦截时不会调用模型。
- `/api/v1/dashboard`：用量看板（10 项指标 + 趋势 + Key 占比 + 延迟分布 + 配额拦截次数）。
- `/api/v1/billing/`：账单合计/按日/明细/导出（CSV、Excel）与**可追溯校验**（账单每个数字都能在流水中找到来源）。
- 日志默认输出 JSON，带 `X-Request-Id`、租户、路径、状态码与耗时，绝不记录完整 Key/密码；模型调用日志带 `usage_source`（Token 取自模型响应的 `usage`，不估算）。

完整接口清单、鉴权方式与错误码表见 [接口文档](问答平台/docs/接口文档.md)；
分层架构与请求数据流见 [架构说明](问答平台/docs/架构说明.md)。

## 目录结构

```
（仓库根）
├── 问答平台/                     主交付目录
│   ├── backend/                  FastAPI + SQLAlchemy 主后端（唯一）
│   │   ├── app/api/v1/           apikey / usage / qa / health / dashboard / billing（`/api/v1/*`）
│   │   ├── app/routers/          auth / tenant / admin / kb / usage（`/api/*`，无版本前缀）
│   │   ├── app/db/               TenantMixin 与租户隔离钩子
│   │   ├── app/services/         apikey / quota / metering / model / cache / rate_limit / billing
│   │   ├── app/tasks/            用量汇总后台线程
│   │   ├── tests/                隔离 / 可观测性 / API Key 与配额 / 限流缓存 / 看板账单
│   │   └── .env.example          环境变量键位清单（复制为 .env 使用）
│   └── docs/                     接口文档 / 架构说明 / 部署说明 / 数据库设计 / 运维手册 / 配额与计量说明
└── frontend/                     Vue3 + vue-router4 + ECharts 管理台
    └── src/                      api 封装 / 路由守卫 / 三态组件 / 6 个页面
```

> **唯一后端**：仓库根目录下曾存在 `backend/` 与 `docs/` 两份历史遗留的部分副本
> （缺少模块 C/D/E/F：`qa.py`、`apikey/`、`billing/`、`metering/`、`tasks/`），
> 易误判"哪份代码在跑"，现已移除。**唯一后端与文档分别位于 `问答平台/backend/` 与 `问答平台/docs/`。**

## 文档索引

[接口文档](问答平台/docs/接口文档.md) · [架构说明](问答平台/docs/架构说明.md) · [部署说明](问答平台/docs/部署说明.md) · [隔离验证报告](问答平台/docs/隔离验证报告.md) · [数据库设计](问答平台/docs/数据库设计.md) · [运维手册](问答平台/docs/运维手册.md) · [配额与计量说明](问答平台/docs/配额与计量说明.md) · [数据来源清单](问答平台/docs/数据来源清单.md) · [测试报告](问答平台/backend/tests/reports/isolation_report.md) · [配额计量测试报告](问答平台/backend/tests/reports/quota_report.md)

**当前范围：**知识库与文档隔离、可观测性、API Key 全生命周期、配额预检与不可变计量流水、滑动窗口限流（Key + 租户双维度）、问答缓存（租户前缀隔离）、用量看板与账单导出/可追溯校验，以及前端管理台 6 个页面均已接入。

**尚未接入：**知识块（chunk）级切分与向量检索。**真实模型上游已接入并跑通**：阿里云百炼（DashScope）的 OpenAI 兼容端点，密钥取自机器环境变量 `DASHSCOPE_API_KEY`（不写入 `.env`），模型 `qwen-flash`——问答返回真实答案，Token 取自模型响应的 `usage`（不估算）并落库。注意 `qwen-turbo` 等旧模型会被限流（429 `limit_requests`，属**速率限流**而非余额不足），换 `qwen-flash` / `qwen-plus` 即可。把 `MODEL_BASE_URL` 留空即回退内置本地模拟模型（`provider=local-simulation`，用量如实标注来源、不伪装为真实供应商）。限流与登出黑名单为进程内实现，多 worker 部署须切 Redis。错误码与预留字段不等于这些模块已完成。
