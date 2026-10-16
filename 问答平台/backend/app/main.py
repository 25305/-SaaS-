from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse

from app import models  # noqa: F401  导入以注册表结构到 Base.metadata
from app.db import tenant_hook  # noqa: F401  导入即注册多租户隔离的 ORM 事件(须在业务查询发生前)
from app.config import settings
from app.core.errors import BizError
from app.core.logging import logger, setup_logging
from app.db.base import Base
from app.db.schema import ensure_schema
from app.db.session import engine
from app.middleware.request_id import RequestContextMiddleware
from app.api.v1 import apikey, health, qa, usage as usage_v1
from app.routers import admin, auth, kb, tenant, usage
from app.seed import run_seed
from app.tasks.summary import scheduler

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_schema()  # 既有库补列(新增表由 create_all 建)
    run_seed()
    scheduler.start()
    yield
    scheduler.stop()


# 官方默认从 cdn.jsdelivr.net 加载 Swagger UI，国内访问会白屏，改用 npmmirror 镜像
_SWAGGER_JS = "https://registry.npmmirror.com/swagger-ui-dist/5.17.14/files/swagger-ui-bundle.js"
_SWAGGER_CSS = "https://registry.npmmirror.com/swagger-ui-dist/5.17.14/files/swagger-ui.css"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="智能问答 SaaS 平台",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)


@app.exception_handler(BizError)
async def biz_error_handler(request: Request, exc: BizError):
    # to_dict 会带上 exc.data(如配额超限的 used/limit/reset_at,FR-D3 要求)
    headers = {}
    if exc.code == "RATE_LIMITED" and exc.data and exc.data.get("retry_after"):
        headers["Retry-After"] = str(exc.data["retry_after"])
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict(), headers=headers)


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        code = detail.get("code", "HTTP_ERROR")
        message = detail.get("message", "请求失败")
    else:
        code, message = "HTTP_ERROR", str(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": code, "message": message, "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "请求参数校验失败",
            # Pydantic 错误默认附带 input,可能含明文密码；只返回字段位置与原因。
            "data": [
                {"loc": list(item.get("loc", ())), "type": item.get("type"), "msg": item.get("msg")}
                for item in exc.errors()
            ],
        },
    )


@app.get("/", summary="服务信息")
def root():
    return {"message": "问答平台API正常"}


@app.get("/docs", include_in_schema=False)
async def swagger_ui():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="智能问答 SaaS 平台 API",
        swagger_js_url=_SWAGGER_JS,
        swagger_css_url=_SWAGGER_CSS,
    )


@app.get("/playground", include_in_schema=False, summary="简易测试页面")
def playground():
    return FileResponse(_STATIC_DIR / "playground.html")


app.include_router(auth.router)
app.include_router(tenant.router)
app.include_router(admin.router)
app.include_router(kb.router)
app.include_router(usage.router)
# 模块 C/D:API Key 生命周期、配额与用量、问答(预检 → 缓存 → 模型)
app.include_router(apikey.router)
app.include_router(usage_v1.router)
app.include_router(qa.router)
app.include_router(health.router)
