"""隔离测试：每个用例使用独立临时 SQLite，不接触项目 qa.db。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.deps import get_db
from app.main import app
from app.models import Document, KnowledgeBase, Role, Tenant, User
from app.security import create_access_token
from app.db.tenant_hook import TENANT_BYPASS_KEY
import app.services.usage as usage_service


@pytest.fixture
def env(tmp_path, monkeypatch):
    test_engine = create_engine(
        "sqlite:///" + (tmp_path / "isolation.db").as_posix(),
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(test_engine)
    maker = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    with maker() as db:
        db.info[TENANT_BYPASS_KEY] = True  # 与 seed 一样，仅初始化隔离测试数据
        admin = Role(code="admin", name="管理员")
        viewer = Role(code="readonly", name="只读")
        db.add_all([admin, viewer])
        db.flush()
        a = Tenant(code="alpha", name="A")
        b = Tenant(code="beta", name="B")
        db.add_all([a, b])
        db.flush()
        user_a = User(tenant_id=a.id, role_id=admin.id, username="alpha_admin", password_hash="unused")
        user_b = User(tenant_id=b.id, role_id=admin.id, username="beta_admin", password_hash="unused")
        view_b = User(tenant_id=b.id, role_id=viewer.id, username="beta_viewer", password_hash="unused")
        db.add_all([user_a, user_b, view_b])
        kb_a = KnowledgeBase(tenant_id=a.id, name="A-ONLY-KB", description="secret")
        db.add(kb_a)
        db.flush()
        doc_a = Document(tenant_id=a.id, kb_id=kb_a.id, title="A-ONLY-DOC", content="secret")
        db.add(doc_a)
        db.commit()
        ids = {"tenant_a": a.id, "tenant_b": b.id, "kb": kb_a.id, "doc": doc_a.id}
        headers = {
            "a": {"Authorization": "Bearer " + create_access_token(
                user_id=user_a.id, tenant_id=a.id, role="admin")},
            "b": {"Authorization": "Bearer " + create_access_token(
                user_id=user_b.id, tenant_id=b.id, role="admin")},
            "viewer_b": {"Authorization": "Bearer " + create_access_token(
                user_id=view_b.id, tenant_id=b.id, role="readonly")},
        }

    def test_db():
        db = maker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = test_db
    monkeypatch.setattr(usage_service, "SessionLocal", maker)
    try:
        # 不使用 with TestClient(app): 这会触发生产库上的 lifespan/seed。
        client = TestClient(app, follow_redirects=True)
        yield {"client": client, "db": maker, "headers": headers, **ids}
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()
