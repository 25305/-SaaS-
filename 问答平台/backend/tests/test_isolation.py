"""成员 B：跨租户访问矩阵、用量隔离与数据层守护测试。"""

from pathlib import Path
import re

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import aliased

from app.core.errors import BizError
from app.models import Base, Document, KnowledgeBase, Role, Tenant, TenantMixin, UsageRecord, User
from app.services.usage import record_request_usage
from app.db.tenant_hook import TENANT_BYPASS_KEY, TENANT_ID_KEY, bypass_tenant_filter


# 对知识库、文档的读/跨父级写/改/删矩阵，均须 404 且与真正不存在一致。
CASES = [
    ("GET", "/api/kb/{kb}", None),
    ("PATCH", "/api/kb/{kb}", {"name": "tampered"}),
    ("DELETE", "/api/kb/{kb}", None),
    ("GET", "/api/kb/{kb}/docs", None),
    ("POST", "/api/kb/{kb}/docs", {"title": "tampered", "content": "x"}),
    ("GET", "/api/kb/{kb}/docs/{doc}", None),
    ("PATCH", "/api/kb/{kb}/docs/{doc}", {"title": "tampered"}),
    ("DELETE", "/api/kb/{kb}/docs/{doc}", None),
]


@pytest.mark.parametrize("method,path,body", CASES)
def test_cross_tenant_resource_returns_indistinguishable_404(env, method, path, body):
    url = path.format(kb=env["kb"], doc=env["doc"])
    missing = path.format(kb=999999999, doc=999999999)
    c = env["client"]
    attack = c.request(method, url, headers=env["headers"]["b"], json=body)
    control = c.request(method, missing, headers=env["headers"]["b"], json=body)
    assert attack.status_code == control.status_code == 404
    assert attack.json() == control.json()
    assert "A-ONLY" not in attack.text
    assert "无权限" not in attack.text


def test_own_resource_access_and_cross_tenant_list(env):
    c = env["client"]
    a, b = env["headers"]["a"], env["headers"]["b"]
    assert c.get("/api/kb/%s" % env["kb"], headers=a).status_code == 200
    assert c.get("/api/kb/%s/docs/%s" % (env["kb"], env["doc"]), headers=a).status_code == 200
    body = c.get("/api/kb/", headers=b).json()
    assert body["code"] == 0 and body["data"] == []


def test_failed_cross_tenant_writes_leave_data_intact(env):
    c = env["client"]
    b = env["headers"]["b"]
    for method, path, body in CASES:
        c.request(method, path.format(kb=env["kb"], doc=env["doc"]), headers=b, json=body)
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = env["tenant_a"]
        assert db.get(KnowledgeBase, env["kb"]).name == "A-ONLY-KB"
        assert db.get(Document, env["doc"]).title == "A-ONLY-DOC"
        assert db.execute(select(func.count(Document.id))).scalar_one() == 1


def test_usage_records_and_summary_are_tenant_scoped(env):
    record_request_usage(
        tenant_id=env["tenant_a"], method="POST", path="/A-ONLY-USAGE",
        status_code=200, latency_ms=100,
    )
    c = env["client"]
    b = env["headers"]["b"]
    # B 的首个请求：尚无本租户流水,不会把 A 的记录算进来。
    summary = c.get("/api/usage/summary", headers=b)
    assert summary.status_code == 200
    assert summary.json()["data"]["total_requests"] == 0
    rows = c.get("/api/usage/records", headers=b)
    assert rows.status_code == 200
    assert "/A-ONLY-USAGE" not in rows.text
    a_rows = c.get("/api/usage/records", headers=env["headers"]["a"])
    assert "/A-ONLY-USAGE" in a_rows.text


def test_aliased_join_and_aggregate_are_filtered(env):
    other = aliased(Document)
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = env["tenant_b"]
        assert db.execute(select(KnowledgeBase).join(Document, Document.kb_id == KnowledgeBase.id)).all() == []
        assert db.execute(select(other).where(other.id == env["doc"])).all() == []
        assert db.execute(select(func.count(Document.id))).scalar_one() == 0
        assert db.get(KnowledgeBase, env["kb"]) is None


def test_bypass_context_is_scoped(env):
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = env["tenant_b"]
        assert db.execute(select(KnowledgeBase)).all() == []
        with bypass_tenant_filter(db):
            assert len(db.execute(select(KnowledgeBase)).all()) == 1
        assert TENANT_BYPASS_KEY not in db.info
        assert db.execute(select(KnowledgeBase)).all() == []


def test_missing_tenant_context_fails_closed(env):
    with env["db"]() as db:
        with pytest.raises(BizError, match="租户上下文缺失"):
            db.execute(select(KnowledgeBase)).all()
        with pytest.raises(BizError, match="租户上下文缺失"):
            db.add(KnowledgeBase(tenant_id=env["tenant_a"], name="invalid"))
            db.flush()
        db.rollback()


def test_object_guard_and_bulk_dml_are_rejected(env):
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = env["tenant_b"]
        db.add(KnowledgeBase(tenant_id=env["tenant_a"], name="invalid"))
        with pytest.raises(BizError):
            db.flush()
        db.rollback()
        with pytest.raises(BizError):
            db.execute(delete(KnowledgeBase))
        db.rollback()


def test_all_business_models_have_tenant_mixin():
    exempt = {Role, Tenant, User}
    missing = [
        mapper.class_.__tablename__ for mapper in Base.registry.mappers
        if mapper.class_ not in exempt and not issubclass(mapper.class_, TenantMixin)
    ]
    assert not missing, "以下业务表未混入 TenantMixin：%s" % missing
    assert UsageRecord in {mapper.class_ for mapper in Base.registry.mappers}


def test_no_raw_sql_in_business_routes():
    """原生 SQL / engine 直连绕过 ORM 钩子，业务接口禁止使用。"""
    root = Path(__file__).resolve().parents[1] / "app" / "routers"
    offenders = []
    for path in root.glob("*.py"):
        if path.name == "health.py":  # 仅依赖探测允许 SELECT 1
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"\btext\s*\(|\bengine\.(?:connect|begin|execute)\s*\(", source):
            offenders.append(path.name)
    assert not offenders, "业务路由存在原生 SQL 绕过点: %s" % offenders


def test_readonly_member_forbidden_within_tenant(env):
    c = env["client"]
    resp = c.post("/api/kb/", headers=env["headers"]["viewer_b"], json={"name": "new"})
    assert resp.status_code == 403  # 同租户角色权限不足，区别于跨租户资源 404
    assert c.get("/api/admin/tenants", headers=env["headers"]["b"]).status_code == 403


def test_request_id_and_structured_usage_log(env, caplog):
    c = env["client"]
    resp = c.get("/api/kb/", headers={**env["headers"]["b"], "X-Request-Id": "case-123"})
    assert resp.headers["X-Request-Id"] == "case-123"
    access = [r for r in caplog.records if r.getMessage() == "http_request"]
    assert any(r.fields["request_id"] == "case-123" and r.fields["tenant_id"] == env["tenant_b"]
               and r.fields["status_code"] == 200 and r.fields["elapsed_ms"] >= 0
               for r in access)
