import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings:
    # 生产环境必须通过环境变量覆盖 JWT_SECRET
    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-secret-change-me")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "120"))
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "sqlite:///" + (BACKEND_DIR / "qa.db").as_posix()
    )


settings = Settings()
