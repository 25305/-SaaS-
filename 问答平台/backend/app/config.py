import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent

# 读取 backend/.env(不存在则静默跳过)。已存在的进程环境变量优先,不被 .env 覆盖。
# 键位清单见 backend/.env.example,部署说明见 docs/部署说明.md。
load_dotenv(BACKEND_DIR / ".env")


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
    # 优先用 MODEL_API_KEY;未设置时回退到系统环境变量 DASHSCOPE_API_KEY(阿里云百炼),
    # 这样密钥留在机器环境变量里,不必抄进 .env 文件。
    MODEL_API_KEY: str = os.getenv("MODEL_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
    MODEL_TIMEOUT_SECONDS: float = float(os.getenv("MODEL_TIMEOUT_SECONDS", "3"))
    # 计入计量的模型名(本地模拟模型用此名;接入真实上游时填真实模型 ID)
    MODEL_NAME: str = os.getenv("MODEL_NAME", "local-simulation")

    # ---------------- 问答缓存(模块 D) ----------------
    QA_CACHE_TTL_SECONDS: int = int(os.getenv("QA_CACHE_TTL_SECONDS", "300"))

    # ---------------- 限流(模块 E,FR-E1) ----------------
    # 租户维度的兜底阈值:API Key 自带 rate_limit_* 时以 Key 为准;JWT 登录态调用走这里。
    TENANT_RATE_LIMIT_PER_MINUTE: int = int(os.getenv("TENANT_RATE_LIMIT_PER_MINUTE", "600"))
    TENANT_RATE_LIMIT_PER_HOUR: int = int(os.getenv("TENANT_RATE_LIMIT_PER_HOUR", "20000"))

    # ---------------- 用量汇总任务(模块 F,FR-F6) ----------------
    # 后台定时把 usage_record 汇总进 usage_summary;<=0 表示不启动(仅手动刷新)。
    # 默认 300s:看板数据最多有 5 分钟延迟,换取接口耗时恒定(见 docs/架构说明.md)。
    SUMMARY_REFRESH_INTERVAL_SECONDS: int = int(os.getenv("SUMMARY_REFRESH_INTERVAL_SECONDS", "300"))
    # 每次刷新回看的自然日窗口(含今日),用于补偿调度抖动/延迟落库
    SUMMARY_REFRESH_LOOKBACK_DAYS: int = int(os.getenv("SUMMARY_REFRESH_LOOKBACK_DAYS", "2"))


settings = Settings()
