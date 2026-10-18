# -*- coding: utf-8 -*-
"""模块 A/H 回归测试:平台管理员的租户配额设置(FR-D1/FR-D6)与全局用量概览(FR-A2/FR-F2)。

覆盖点:
1. 配额/超额策略配置接口仅 super_admin 可用(租户管理员 403);
2. PATCH 语义正确:只改传入字段,显式 null 表示该维度不限制,非法策略被拒(422);
3. 租户列表回填配额与超额策略字段(前端管理台直接消费,FR-H2);
4. 全局用量概览跨租户合计,且租户自身看板仍只看得到自己(隔离不被超管接口破坏)。

测试用独立临时 SQLite(见 conftest.py 的 env fixture),不接触项目 qa.db。
"""

import pytest

from app.db.tenant_hook import TENANT_BYPASS_KEY
from app.models import Role, User
from app.security import create_access_token
from app.services.model import rag
from app.services.model.llm_client import LlmClient, LlmCompletion, LlmUsage


class FakeModel(LlmClient):
    """确定性模型替身:返回固定 usage,让全局概览数字可预期。"""

    provider = "test-double"

    def __init__(self, prompt_tokens: int = 7, completion_tokens: int = 11) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    def complete(self, *, question: str, context, model: str) -> LlmCompletion:
        return LlmCompletion(
            answer=f"测试答案({question})",
            model=model,
            provider=self.provider,
            usage=LlmUsage(self.prompt_tokens, self.completion_tokens),
            usage_source="response.usage",
        )


@pytest.fixture
def fake_model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(rag, "get_llm_client", lambda: fake)
    return fake


@pytest.fixture
def super_admin_headers(env):
    """在临时库中补一个 super_admin 角色与账号,返回其 Bearer 鉴权头。"""
    maker = env["db"]
    with maker() as db:
        db.info[TENANT_BYPASS_KEY] = True  # 身份层维护入口
        role = Role(code="super_admin", name="平台管理员")
        db.add(role)
        db.flush()
        user = User(
            tenant_id=env["tenant_a"],
            role_id=role.id,
            username="plat_admin",
            password_hash="unused",
        )
        db.add(user)
        db.commit()
        token = create_access_token(
            user_id=user.id, tenant_id=env["tenant_a"], role="super_admin"
        )
    return {"Authorization": "Bearer " + token}


# ----------------------------------------------------------------------
# 配额设置(FR-D1/FR-D6)
# ----------------------------------------------------------------------
def test_quota_config_requires_super_admin(env, super_admin_headers):
    client, tid = env["client"], env["tenant_b"]
    # 租户管理员无权限
    assert client.patch(
        f"/api/admin/tenants/{tid}/quota",
        json={"quota_monthly_tokens": 100},
        headers=env["headers"]["a"],
    ).status_code == 403
    # 平台管理员放行
    assert client.patch(
        f"/api/admin/tenants/{tid}/quota",
        json={"quota_monthly_tokens": 100},
        headers=super_admin_headers,
    ).status_code == 200


def test_set_tenant_quota_updates_fields_and_list(env, super_admin_headers):
    client, tid = env["client"], env["tenant_b"]
    resp = client.patch(
        f"/api/admin/tenants/{tid}/quota",
        json={"quota_monthly_tokens": 12345, "quota_monthly_requests": 67,
              "overage_policy": "soft_warn"},
        headers=super_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["quota_monthly_tokens"] == 12345
    assert data["quota_monthly_requests"] == 67
    assert data["overage_policy"] == "soft_warn"

    tenants = client.get("/api/admin/tenants", headers=super_admin_headers).json()["data"]
    row = next(t for t in tenants if t["id"] == tid)
    assert row["quota_monthly_tokens"] == 12345
    assert row["quota_monthly_requests"] == 67
    assert row["overage_policy"] == "soft_warn"


def test_patch_quota_partial_and_unlimited(env, super_admin_headers):
    client, tid = env["client"], env["tenant_b"]
    # 只传一个字段:其余维度保持原值(alpha 侧默认 None)
    client.patch(f"/api/admin/tenants/{tid}/quota",
                 json={"overage_policy": "soft_warn"}, headers=super_admin_headers)
    row = next(t for t in client.get("/api/admin/tenants", headers=super_admin_headers).json()["data"]
               if t["id"] == tid)
    assert row["overage_policy"] == "soft_warn"
    assert row["quota_monthly_tokens"] is None

    # 显式传 null 表示该维度不限制
    resp = client.patch(f"/api/admin/tenants/{tid}/quota",
                        json={"quota_monthly_tokens": None}, headers=super_admin_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["quota_monthly_tokens"] is None

    # 非法策略被 Pydantic 拒绝(422)
    assert client.patch(f"/api/admin/tenants/{tid}/quota",
                        json={"overage_policy": "explode"},
                        headers=super_admin_headers).status_code == 422


# ----------------------------------------------------------------------
# 全局用量概览(FR-A2/FR-F2)
# ----------------------------------------------------------------------
def test_global_overview_requires_super_admin(env):
    assert env["client"].get(
        "/api/admin/usage/overview", headers=env["headers"]["a"]
    ).status_code == 403


def test_global_overview_aggregates_across_tenants(env, super_admin_headers, fake_model):
    client = env["client"]
    for headers, question in ((env["headers"]["a"], "A 的独立问题"),
                              (env["headers"]["b"], "B 的独立问题")):
        r = client.post("/api/v1/qa/ask", json={"question": question}, headers=headers)
        assert r.status_code == 200, r.text

    resp = client.get("/api/admin/usage/overview?range=today", headers=super_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    expected_tokens = (fake_model.prompt_tokens + fake_model.completion_tokens) * 2
    assert data["summary"]["total_requests"] == 2
    assert data["summary"]["total_tokens"] == expected_tokens
    assert data["summary"]["tenant_count"] >= 2

    per_tenant = {row["tenant_id"]: row for row in data["by_tenant"]}
    assert per_tenant[env["tenant_a"]]["requests"] == 1
    assert per_tenant[env["tenant_b"]]["requests"] == 1
    assert per_tenant[env["tenant_a"]]["total_tokens"] == expected_tokens // 2

    # 超管接口不破坏租户侧隔离:租户 A 自己的看板仍只看得到自己
    own = client.get("/api/v1/dashboard?range=today", headers=env["headers"]["a"]).json()["data"]
    assert own["summary"]["total_requests"] == 1
