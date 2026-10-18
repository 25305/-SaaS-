# -*- coding: utf-8 -*-
"""模块 E 回归测试:限流与缓存(FR-E1~E5)。

覆盖:
1. **Key 维度限流** —— 阈值调小后第 N 次返回 429,且响应带 `Retry-After`;
2. **租户维度限流** —— JWT 登录态调用(无 Key)同样受租户兜底阈值约束;
3. **跨租户缓存隔离(FR-E3)** —— 租户 B 问同样的问题不命中租户 A 的缓存;
4. **缓存 TTL / 主动清除(FR-E4)** —— 清除入口调用后同租户不再命中。

测试用独立临时 SQLite(见 conftest.py 的 env fixture),不接触项目 qa.db。
"""

import pytest

from app.config import settings
from app.services.cache.backend import get_cache, tenant_key
from app.services.model import rag
from app.services.model.llm_client import LlmCompletion, LlmClient, LlmUsage
from app.services.rate_limit import limiter


class FakeModel(LlmClient):
    provider = "test-double"

    def complete(self, *, question: str, context, model: str) -> LlmCompletion:
        return LlmCompletion(
            answer=f"测试答案({question})",
            model=model,
            provider=self.provider,
            usage=LlmUsage(10, 20),
            usage_source="response.usage",
        )


@pytest.fixture(autouse=True)
def reset_limiter_and_cache():
    """限流器是进程内单例,缓存同理:跨用例会串,逐个清空保证用例独立。"""
    limiter._events.clear()
    get_cache().delete_prefix("t:")
    yield
    limiter._events.clear()
    get_cache().delete_prefix("t:")


@pytest.fixture
def fake_model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(rag, "get_llm_client", lambda: fake)
    return fake


def make_key(client, headers, **payload):
    body = {"name": "限流测试 Key", **payload}
    resp = client.post("/api/v1/apikeys", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def ask(client, headers, question: str):
    return client.post("/api/v1/qa/ask", json={"question": question}, headers=headers)


# ----------------------------------------------------------------------
# FR-E1 限流:Key 维度
# ----------------------------------------------------------------------
def test_key_rate_limit_returns_429_with_retry_after(env, fake_model):
    client = env["client"]
    key = make_key(client, env["headers"]["a"], rate_limit_per_minute=2)
    headers = {"X-API-Key": key["key"]}

    assert ask(client, headers, "限流问题一").status_code == 200
    assert ask(client, headers, "限流问题二").status_code == 200

    blocked = ask(client, headers, "限流问题三")
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["code"] == "RATE_LIMITED"
    # FR-E1:超限返回 429 + 重试建议(响应头 + 响应体双给出)
    assert blocked.headers.get("Retry-After")
    assert body["data"]["retry_after"] > 0


def test_tenant_dimension_limit_applies_to_jwt_calls(env, fake_model, monkeypatch):
    """JWT 调用没有 Key,应回落到租户兜底阈值(双维度,不是只限 Key)。"""
    monkeypatch.setattr(settings, "TENANT_RATE_LIMIT_PER_MINUTE", 1)
    client, headers = env["client"], env["headers"]["a"]

    assert ask(client, headers, "租户限流一").status_code == 200
    blocked = ask(client, headers, "租户限流二")
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "RATE_LIMITED"
    assert blocked.headers.get("Retry-After")


def test_rate_limit_is_isolated_per_tenant(env, fake_model, monkeypatch):
    """一个租户被限流不应影响另一个租户。"""
    monkeypatch.setattr(settings, "TENANT_RATE_LIMIT_PER_MINUTE", 1)
    client = env["client"]

    assert ask(client, env["headers"]["a"], "A 限流一").status_code == 200
    assert ask(client, env["headers"]["a"], "A 限流二").status_code == 429
    assert ask(client, env["headers"]["b"], "B 限流一").status_code == 200


# ----------------------------------------------------------------------
# FR-E3 跨租户缓存隔离(验收必查)
# ----------------------------------------------------------------------
def test_cache_not_shared_across_tenants(env, fake_model):
    client = env["client"]
    question = "选课什么时候开始"

    first = ask(client, env["headers"]["a"], question)
    assert first.status_code == 200
    assert first.headers.get("X-Cache") == "MISS"
    assert first.json()["data"]["cache_hit"] is False

    again = ask(client, env["headers"]["a"], question)
    assert again.headers.get("X-Cache") == "HIT", "同租户重复提问应命中缓存"
    assert again.json()["data"]["tokens"]["total_tokens"] == 0

    other = ask(client, env["headers"]["b"], question)
    assert other.headers.get("X-Cache") != "HIT", "租户 B 命中了租户 A 的缓存!"
    assert other.json()["data"]["cache_hit"] is False


def test_cache_key_has_tenant_prefix_in_front(env):
    """FR-E3 的根因防护:租户前缀必须在最前,才能按租户安全批量清理。"""
    key_a = tenant_key(1, "qa", "同一个问题")
    key_b = tenant_key(2, "qa", "同一个问题")
    assert key_a.startswith("t:1:")
    assert key_b.startswith("t:2:")
    assert not key_a.startswith(key_b)


# ----------------------------------------------------------------------
# FR-E4 缓存可清除(知识库更新后的数据一致性入口)
# ----------------------------------------------------------------------
def test_cache_can_be_cleared_by_tenant(env, fake_model):
    client, headers = env["client"], env["headers"]["a"]
    question = "知识库更新前的问题"

    ask(client, headers, question)
    assert ask(client, headers, question).headers.get("X-Cache") == "HIT"

    cleared = client.post("/api/usage/cache/clear", headers=headers)
    assert cleared.status_code == 200
    assert cleared.json()["data"]["deleted"] >= 1

    assert ask(client, headers, question).headers.get("X-Cache") == "MISS", \
        "清除后同租户不应再命中旧缓存"
