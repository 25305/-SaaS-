from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse

from app import models  # noqa: F401  导入以注册表结构到 Base.metadata
from app import tenant_scope  # noqa: F401  导入即注册多租户隔离的 ORM 事件(须在业务查询发生前)
from app.core.errors import BizError
from app.db import Base, engine
from app.routers import admin, auth, kb, tenant
from app.seed import run_seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    run_seed()
    yield


# 官方默认从 cdn.jsdelivr.net 加载 Swagger UI，国内访问会白屏，改用 npmmirror 镜像
_SWAGGER_JS = "https://registry.npmmirror.com/swagger-ui-dist/5.17.14/files/swagger-ui-bundle.js"
_SWAGGER_CSS = "https://registry.npmmirror.com/swagger-ui-dist/5.17.14/files/swagger-ui.css"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="智能问答 SaaS 平台",
    version="0.1.0",
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


@app.exception_handler(BizError)
async def biz_error_handler(request: Request, exc: BizError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


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
            "data": jsonable_encoder(exc.errors()),
        },
    )


@app.get("/", summary="服务信息")
def root():
    return {"message": "问答平台API正常"}


@app.get("/health", summary="健康检查")
def health():
    return {"code": 0, "message": "ok", "data": {"service": "up"}}


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
