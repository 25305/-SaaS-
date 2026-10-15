from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    # SQLite 默认不强制外键，显式开启以保证 role_id / tenant_id 引用完整性
    dbapi_conn.execute("PRAGMA foreign_keys=ON")


class Base(DeclarativeBase):
    pass
