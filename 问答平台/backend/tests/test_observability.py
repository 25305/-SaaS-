"""模块 G：健康探针、请求日志与五类错误码的回归测试。"""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.errors import BizError, ERROR_SPEC, ErrorCode
from app.core.logging import JsonFormatter
from app.main import app
from app.api.v1 import health
from app.services.cache.backend import get_cache, tenant_key


def test_health_unconfigured_model_returns_degraded():
    c = TestClient(app)
    response = c.get("/health")
    assert response.status_code == 503
    deps = response.json()["dependencies"]
    assert deps["database"]["status"] == "up"
    assert deps["cache"]["status"] == "up"
    assert deps["model_service"]["status"] == "unconfigured"
    assert response.headers["X-Request-Id"]


def test_health_all_up(monkeypatch):
    async def up():
        return {"status": "up"}
    monkeypatch.setattr(health, "probe_model", up)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert all(v["status"] == "up" for v in response.json()["dependencies"].values())


def test_health_probe_timeout(monkeypatch):
    async def slow():
        await asyncio.sleep(0.05)
        return {"status": "up"}
    monkeypatch.setattr(health, "probe_model", slow)
    monkeypatch.setattr(health.settings, "HEALTH_TIMEOUT_SECONDS", 0.005)
    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert response.json()["dependencies"]["model_service"] == {"status": "down", "reason": "timeout"}


@pytest.mark.parametrize("code,http", [
    (ErrorCode.QUOTA_EXCEEDED, 429),
    (ErrorCode.RATE_LIMITED, 429),
    (ErrorCode.UNAUTHORIZED, 401),
    (ErrorCode.API_KEY_INVALID, 401),
    (ErrorCode.MODEL_SERVICE_ERROR, 502),
])
def test_five_error_families(code, http):
    err = BizError(code)
    assert err.code == code and err.status_code == http
    assert err.message == ERROR_SPEC[code][1]
    assert set(err.to_dict()) == {"code", "message", "data"}


def test_authentication_error_has_request_id(env):
    response = env["client"].get("/api/kb/", headers={"X-Request-Id": "auth-001"})
    assert response.status_code == 401
    assert response.json()["code"] == ErrorCode.UNAUTHORIZED
    assert response.headers["X-Request-Id"] == "auth-001"


def test_request_id_rejects_invalid_value():
    response = TestClient(app).get("/", headers={"X-Request-Id": "x" * 200})
    assert response.status_code == 200
    assert len(response.headers["X-Request-Id"]) == 16


def test_json_log_contains_required_fields_without_secret():
    rec = logging.LogRecord("app", logging.INFO, __file__, 1, "http_request", (), None)
    rec.fields = {"request_id": "abc", "tenant_id": 1, "key_prefix": "sk-abc123", "path": "/api/kb/", "method": "GET", "status_code": 200, "elapsed_ms": 2}
    output = JsonFormatter().format(rec)
    assert '"request_id": "abc"' in output
    assert '"tenant_id": 1' in output
    assert '"key_prefix": "sk-abc123"' in output
    assert '"elapsed_ms": 2' in output


def test_cache_keys_are_tenant_specific():
    a, b = tenant_key(1, "answer", "same-question"), tenant_key(2, "answer", "same-question")
    cache = get_cache()
    cache.set(a, "secret", ttl=5)
    assert cache.get(b) is None
    cache.delete(a)
