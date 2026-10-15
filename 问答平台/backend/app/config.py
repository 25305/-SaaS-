import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # ---------------- 认证 ----------------
    # 生产环境必须通过环境变量覆盖 JWT_SECRET
    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-secret-change-me")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "120"))

    # ---------------- 数据库 ----------------
    # 开发期默认 SQLite(单文件,零依赖);生产用环境变量切 MySQL:
    #   DATABASE_URL=mysql+pymysql://user:pwd@host:3306/smartqa_saas?charset=utf8mb4
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "sqlite:///" + (BACKEND_DIR / "qa.db").as_posix()
    )
    # 打开后打印每条 SQL,用于核对隔离钩子是否注入 tenant_id(模块 B 自测用)
    DB_ECHO: bool = _env_bool("DB_ECHO", False)

    # ---------------- 可观测性(模块 G) ----------------
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    # 结构化 JSON 日志开关;置 false 时输出便于人读的单行文本
    LOG_JSON: bool = _env_bool("LOG_JSON", True)
    APP_VERSION: str = os.getenv("APP_VERSION", "0.2.0")
    # 单次依赖探测的超时(秒):依赖挂掉时健康检查不能跟着挂(FR-G2)
    HEALTH_TIMEOUT_SECONDS: float = float(os.getenv("HEALTH_TIMEOUT_SECONDS", "3"))

    # ---------------- 模型服务 ----------------
    # 留空表示使用内置本地桩(local-stub),不发起外部请求
    MODEL_BASE_URL: str = os.getenv("MODEL_BASE_URL", "")
    MODEL_API_KEY: str = os.getenv("MODEL_API_KEY", "")
    MODEL_TIMEOUT_SECONDS: float = float(os.getenv("MODEL_TIMEOUT_SECONDS", "3"))
    # 计入计量的模型名(本地模拟模型用此名;接入真实上游时填真实模型 ID)
    MODEL_NAME: str = os.getenv("MODEL_NAME", "local-simulation")

    # ---------------- 问答缓存(模块 D) ----------------
    QA_CACHE_TTL_SECONDS: int = int(os.getenv("QA_CACHE_TTL_SECONDS", "300"))


settings = Settings()
