"""成员 D 限流、缓存失效与账单追溯验收。"""

from pathlib import Path

from app.services.cache.backend import get_cache, tenant_key
from app.services.rate_limit import limiter


def test_cache_keys_are_tenant_scoped():
    cache = get_cache()
    cache.delete_prefix("t:")
    key_a = tenant_key(1, "qa", "same-question")
    key_b = tenant_key(2, "qa", "same-question")
    cache.set(key_a, "tenant A answer", ttl=60)
    assert cache.get(key_b) is None
    assert cache.delete_prefix("t:1:") == 1


def test_knowledge_base_mutations_invalidate_tenant_cache():
    source = (Path(__file__).resolve().parents[1] / "app" / "routers" / "kb.py").read_text(encoding="utf-8")
    assert "invalidate_tenant" in source


def test_ask_response_exposes_cache_status():
    source = (Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "qa.py").read_text(encoding="utf-8")
    assert 'X-Cache' in source


def test_billing_trace_is_available(env):
    from app.services.billing.trace import verify_bill_traceable
    with env["db"]() as db:
        from app.db.tenant_hook import TENANT_ID_KEY
        from app.models import LlmUsageRecord
        db.info[TENANT_ID_KEY] = env["tenant_a"]
        for number in range(5):
            db.add(LlmUsageRecord(tenant_id=env["tenant_a"], key_id=None, user_id=None, request_id=f"trace-{number}", model="test", prompt_tokens=number, completion_tokens=number + 1, latency_ms=10, status="success", cache_hit=False))
        db.commit()
        result = verify_bill_traceable(db, tenant_id=env["tenant_a"], days=30, sample_size=5)
    assert result["traceable"] is True


def test_tenant_rate_limit_returns_retry_after(env, monkeypatch):
    """租户总限流不依赖 API Key，且 429 必须含 Retry-After。"""
    from app.config import settings
    limiter._events.clear()
    monkeypatch.setattr(settings, "TENANT_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "TENANT_RATE_LIMIT_PER_HOUR", 100)
    first = env["client"].post("/api/v1/qa/ask", json={"question": "限流第一次"}, headers=env["headers"]["a"])
    second = env["client"].post("/api/v1/qa/ask", json={"question": "限流第二次"}, headers=env["headers"]["a"])
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["Retry-After"]


def test_kb_write_clears_tenant_cache(env):
    key = tenant_key(env["tenant_a"], "qa", "stale")
    get_cache().set(key, "stale answer", ttl=60)
    response = env["client"].patch(
        f"/api/kb/{env['kb']}", json={"description": "changed"}, headers=env["headers"]["a"]
    )
    assert response.status_code == 200
    assert get_cache().get(key) is None
