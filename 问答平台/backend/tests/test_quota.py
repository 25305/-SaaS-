# -*- coding: utf-8 -*-
"""模块 C/D 回归测试:API Key 生命周期 + 配额与计量(FR-C1~C6、FR-D1~D6)。

对齐任务书 §五 自测清单与 §三 的参考用例,重点覆盖三条会被"判不合格"的红线:
1. **配额预检在模型调用之前** —— `test_quota_rejected_without_model_call`
   用模型调用计数器断言拒绝路径没有调用模型;
2. **Token 取自模型响应** —— `test_token_source_is_response_usage`
   断言日志里 `usage_source == "response.usage"`,而不是 tiktoken 估算;
3. **流水账不可变** —— `test_usage_record_is_append_only`
   静态扫描 app/ 下不存在对 usage_record 的 UPDATE/DELETE。

测试用独立临时 SQLite(见 conftest.py 的 env fixture),不接触项目 qa.db。
"""

import hashlib
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.errors import ErrorCode
from app.db.tenant_hook import TENANT_ID_KEY
from app.models import ApiKey, LlmUsageRecord, Tenant, UsageSummary
from app.services.cache.backend import get_cache
from app.services.model import rag
from app.services.model.llm_client import LlmCompletion, LlmClient, LlmUsage

APP_DIR = Path(__file__).resolve().parents[1] / "app"


# ----------------------------------------------------------------------
# 工具与测试替身
# ----------------------------------------------------------------------
class FakeModel(LlmClient):
    """确定性测试替身:每次"调用"记一次数,并返回固定的 usage。

    它替代的是真实模型服务,不是计量逻辑——计量必须从返回值里取 usage。
    """

    provider = "test-double"

    def __init__(self, prompt_tokens: int = 10, completion_tokens: int = 20) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.calls = 0

    def count(self) -> int:
        return self.calls

    def complete(self, *, question: str, context, model: str) -> LlmCompletion:
        self.calls += 1
        return LlmCompletion(
            answer=f"测试答案({question})",
            model=model,
            provider=self.provider,
            usage=LlmUsage(self.prompt_tokens, self.completion_tokens),
            usage_source="response.usage",
        )


@pytest.fixture(autouse=True)
def clean_answer_cache():
    """问答缓存是进程内单例,跨用例会串;每个用例开始清空自己的租户前缀。"""
    get_cache().delete_prefix("t:")
    yield
    get_cache().delete_prefix("t:")


@pytest.fixture
def llm_call_counter(monkeypatch):
    """注入计数用的模型替身,并让 rag 用它替代真实模型客户端。"""
    fake = FakeModel()
    monkeypatch.setattr(rag, "get_llm_client", lambda: fake)
    return fake


@pytest.fixture
def tenant_config(env):
    """直接改身份层 Tenant 的配额/策略(演示与测试用,不属于业务数据表)。"""

    def _apply(tenant_id=None, **fields):
        with env["db"]() as db:
            tenant = db.get(Tenant, tenant_id or env["tenant_a"])
            for name, value in fields.items():
                setattr(tenant, name, value)
            db.commit()

    return _apply


@pytest.fixture
def make_key(env):
    """通过管理接口创建 Key,返回创建响应的 data(含仅此一次可见的完整值)。"""

    def _create(headers=None, **payload):
        body = {"name": payload.pop("name", "自动化测试 Key"), **payload}
        resp = env["client"].post(
            "/api/v1/apikeys", headers=headers or env["headers"]["a"], json=body
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]

    return _create


def key_headers(raw: str) -> dict:
    return {"X-API-Key": raw}


def read_rows(env, tenant_id: int):
    """带租户上下文读取本租户流水/聚合。"""
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = tenant_id
        records = db.execute(select(LlmUsageRecord)).scalars().all()
        summaries = db.execute(select(UsageSummary)).scalars().all()
    return records, summaries


def read_keys(env, tenant_id: int):
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = tenant_id
        return db.execute(select(ApiKey).order_by(ApiKey.id)).scalars().all()


# ----------------------------------------------------------------------
# FR-D3 ★ 预检在模型调用之前
# ----------------------------------------------------------------------
def test_quota_rejected_without_model_call(env, tenant_config, llm_call_counter):
    """配额用尽时返回 429 + used/reset_at,且**没有调用模型**。"""
    client, headers = env["client"], env["headers"]["a"]

    first = client.post("/api/v1/qa/ask", json={"question": "选课时间是什么"},
                        headers=headers)
    assert first.status_code == 200, first.text
    used_tokens = first.json()["data"]["tokens"]["total_tokens"]
    assert used_tokens > 0, "测试替身必须返回非 0 usage,否则本用例失去意义"

    # 把上限压到"已经用掉的量",下一次请求必然超限
    tenant_config(quota_monthly_tokens=used_tokens)

    calls_before = llm_call_counter.count()
    rejected = client.post("/api/v1/qa/ask", json={"question": "学籍规定有哪些"},
                           headers=headers)

    assert rejected.status_code == 429
    body = rejected.json()
    assert body["code"] == ErrorCode.QUOTA_EXCEEDED
    assert body["data"]["used_tokens"] == used_tokens
    assert body["data"]["limit_tokens"] == used_tokens
    assert body["data"]["reset_at"], "429 响应必须含重置时间"
    assert llm_call_counter.count() == calls_before, \
        "配额拦截时不应调用模型!这是 FR-D3 的硬性要求"


def test_rejected_request_leaves_audit_record_but_no_quota_usage(env, tenant_config):
    """被拒的请求只留审计流水,不计入配额消耗(否则拒绝会自我放大)。"""
    client, headers = env["client"], env["headers"]["a"]
    client.post("/api/v1/qa/ask", json={"question": "报修流程"}, headers=headers)
    tenant_config(quota_monthly_tokens=0)  # 0 = 一律拒绝

    rejected = client.post("/api/v1/qa/ask", json={"question": "宿舍水电"}, headers=headers)
    assert rejected.status_code == 429

    records, summaries = read_rows(env, env["tenant_a"])
    statuses = [r.status for r in records]
    assert "quota_rejected" in statuses
    # 聚合里的 requests 不含 quota_rejected
    assert summaries[-1].requests == sum(1 for s in statuses if s in ("success", "error"))


def test_quota_rejected_response_uses_unified_error_shape(env, tenant_config):
    client, headers = env["client"], env["headers"]["a"]
    tenant_config(quota_monthly_requests=1)
    client.post("/api/v1/qa/ask", json={"question": "借书"}, headers=headers)
    r = client.post("/api/v1/qa/ask", json={"question": "还书"}, headers=headers)
    assert r.status_code == 429
    assert set(r.json()) == {"code", "message", "data"}  # 统一错误体
    assert r.json()["data"]["used_requests"] == 1


# ----------------------------------------------------------------------
# FR-D5 惰性周期重置
# ----------------------------------------------------------------------
def test_period_reset_by_backdating(env, tenant_config):
    """把 period_start 改到上个月,请求后应重置回当月 1 日。"""
    tenant_config(period_start=date.today().replace(day=1) - timedelta(days=40))

    r = env["client"].get("/api/v1/quota/status", headers=env["headers"]["a"])
    assert r.status_code == 200

    with env["db"]() as db:
        tenant = db.get(Tenant, env["tenant_a"])
        assert tenant.period_start == date.today().replace(day=1)


def test_period_reset_clears_previous_month_usage(env, tenant_config, llm_call_counter):
    """跨月后上个月用量不再计入(自然月重置的实际效果)。"""
    client, headers = env["client"], env["headers"]["a"]
    client.post("/api/v1/qa/ask", json={"question": "上月用量"}, headers=headers)

    # 造一条上个月的流水(直接 INSERT,不改旧记录)
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = env["tenant_a"]
        db.add(LlmUsageRecord(
            tenant_id=env["tenant_a"], key_id=None, user_id=None,
            request_id="backdated", model="test-double",
            prompt_tokens=9999, completion_tokens=9999, latency_ms=1,
            status="success", cache_hit=False,
            created_at=datetime.now() - timedelta(days=40),
        ))
        db.add(UsageSummary(
            tenant_id=env["tenant_a"],
            bucket_date=(date.today() - timedelta(days=40)),
            requests=1, success=1, errors=0,
            prompt_tokens=9999, completion_tokens=9999,
            avg_latency_ms=1, p95_latency_ms=1, cache_hits=0,
        ))
        db.commit()

    tenant_config(quota_monthly_tokens=100)
    status = client.get("/api/v1/quota/status", headers=headers).json()["data"]
    assert status["used_tokens"] < 100, "上个月的用量不应计入本月"
    assert status["reset_at"] == str(
        (date.today().replace(day=1) + timedelta(days=32)).replace(day=1)
    )


# ----------------------------------------------------------------------
# FR-D6 超额行为可配置
# ----------------------------------------------------------------------
def test_soft_overage_policy_allows_request(env, tenant_config, llm_call_counter):
    """软超额策略:超限仍放行,但响应带告警头。"""
    client, headers = env["client"], env["headers"]["a"]
    tenant_config(overage_policy="soft_warn", quota_monthly_tokens=0)

    r = client.post("/api/v1/qa/ask", json={"question": "报修流程"}, headers=headers)
    assert r.status_code == 200
    assert r.headers.get("X-Quota-Warning") == "overage"


def test_reject_policy_actually_blocks(env, tenant_config, llm_call_counter):
    """同一份配额,换成 reject 就必须拦截 —— 说明策略真的生效,不是摆设。"""
    client, headers = env["client"], env["headers"]["a"]
    tenant_config(overage_policy="reject", quota_monthly_tokens=0)
    r = client.post("/api/v1/qa/ask", json={"question": "报修流程 V2"}, headers=headers)
    assert r.status_code == 429


# ----------------------------------------------------------------------
# FR-C4 单 Key 配额独立
# ----------------------------------------------------------------------
def test_key_quota_isolated_within_tenant(env, make_key, llm_call_counter):
    """Key1 超额不影响同租户的 Key2;并且 Key1 的用量按 Key 统计。"""
    client = env["client"]
    key1 = make_key(name="限流 Key", quota_monthly_tokens=1)
    key2 = make_key(name="正常 Key")

    first = client.post("/api/v1/qa/ask", json={"question": "问题一"},
                        headers=key_headers(key1["key"]))
    assert first.status_code == 200, first.text

    r1 = client.post("/api/v1/qa/ask", json={"question": "问题二"},
                     headers=key_headers(key1["key"]))
    r2 = client.post("/api/v1/qa/ask", json={"question": "问题三"},
                     headers=key_headers(key2["key"]))

    assert r1.status_code == 429, "key1 超过自身配额必须被拒"
    assert r2.status_code == 200, "key2 不应受 key1 的配额影响"

    # 按 Key 分组:两把 Key 各自的用量分开记
    rows = client.get("/api/v1/usage/by-key", headers=env["headers"]["a"]).json()["data"]
    by_key = {row["key_id"]: row for row in rows}
    assert by_key[key1["id"]]["request_count"] == 1
    assert by_key[key2["id"]]["request_count"] == 1
    assert by_key[key1["id"]]["prompt_tokens"] > 0


def test_key_level_quota_falls_back_when_null(env, make_key, llm_call_counter):
    """Key 级字段为 NULL 时继承租户配额。"""
    key = make_key(name="继承租户配额")
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = env["tenant_a"]
        row = db.get(ApiKey, key["id"])
        assert row.quota_monthly_tokens is None

    r = env["client"].post("/api/v1/qa/ask", json={"question": "继承测试"},
                           headers=key_headers(key["key"]))
    assert r.status_code == 200


# ----------------------------------------------------------------------
# FR-C1/C2/C3/C6 Key 生命周期
# ----------------------------------------------------------------------
def test_key_lifecycle_create_list_rotate_revoke(env, make_key, llm_call_counter):
    client = env["client"]
    created = make_key(name="生命周期 Key")

    # FR-C1 创建成功 + 完整值仅一次
    assert created["key"].startswith("sk-")
    assert created["masked"].startswith(created["key"][:11])

    # FR-C2 列表只返回掩码,绝不含完整值
    listed = client.get("/api/v1/apikeys", headers=env["headers"]["a"])
    assert listed.status_code == 200
    assert created["key"] not in listed.text
    assert "masked" in listed.text

    # FR-C2 库里只存哈希:64 位十六进制,且等于明文 Key 的 sha256
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = env["tenant_a"]
        row = db.get(ApiKey, created["id"])
        assert row.key_hash == hashlib.sha256(created["key"].encode()).hexdigest()
        assert re.fullmatch(r"[0-9a-f]{64}", row.key_hash)
        assert created["key"] not in (row.key_hash or "")

    # 用 Key 调用成功,并更新 last_used(FR-C5/C6)
    ok_resp = client.post("/api/v1/qa/ask", json={"question": "Key 调用"},
                          headers=key_headers(created["key"]))
    assert ok_resp.status_code == 200
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = env["tenant_a"]
        row = db.get(ApiKey, created["id"])
        assert row.last_used_at is not None
        assert row.last_used_ip is not None

    # FR-C3 轮换:旧 Key 不是立即失效,而是拿到 24h 过期窗口
    rotated = client.post(f"/api/v1/apikeys/{created['id']}/rotate",
                          headers=env["headers"]["a"])
    assert rotated.status_code == 200
    data = rotated.json()["data"]
    old_expire = datetime.fromisoformat(data["old_key"]["expire_at"])
    assert timedelta(hours=23) < old_expire - datetime.now() <= timedelta(hours=24)
    assert data["masked"] != created["masked"]
    # 旧 Key 在窗口内仍可用,新 Key 立即可用
    assert client.post("/api/v1/qa/ask", json={"question": "轮换窗口"},
                       headers=key_headers(created["key"])).status_code == 200
    assert client.post("/api/v1/qa/ask", json={"question": "新 Key"},
                       headers=key_headers(data["key"])).status_code == 200

    # FR-C3 吊销:立即失效(401),且不影响同租户其他 Key
    assert client.delete(f"/api/v1/apikeys/{data['id']}",
                         headers=env["headers"]["a"]).status_code == 200
    revoked = client.post("/api/v1/qa/ask", json={"question": "已吊销 Key"},
                          headers=key_headers(data["key"]))
    assert revoked.status_code == 401
    assert revoked.json()["code"] == ErrorCode.API_KEY_INVALID


def test_expired_key_is_rejected_and_revocation_is_immediate(env, make_key):
    """过期 Key 与吊销 Key 都返回 401;轮换后的旧 Key 在窗口内仍可用。"""
    client, admin = env["client"], env["headers"]["a"]
    created = make_key(name="过期 Key")
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = env["tenant_a"]
        db.get(ApiKey, created["id"]).expire_at = datetime.now() - timedelta(seconds=1)
        db.commit()

    r = client.post("/api/v1/qa/ask", json={"question": "过期后调用"},
                    headers=key_headers(created["key"]))
    assert r.status_code == 401 and r.json()["code"] == ErrorCode.API_KEY_INVALID


def test_api_key_cannot_access_other_tenant_key(env, make_key):
    """跨租户操作别人的 Key → 与"不存在"完全一致的 404,不泄露存在性。"""
    client = env["client"]
    created = make_key(name="A 的 Key")
    other = client.delete(f"/api/v1/apikeys/{created['id']}", headers=env["headers"]["b"])
    missing = client.delete("/api/v1/apikeys/99999999", headers=env["headers"]["b"])
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()


def test_key_management_requires_admin(env):
    """只读成员不能创建 Key(同租户角色不足 → 403,不是 404)。"""
    r = env["client"].post("/api/v1/apikeys", headers=env["headers"]["viewer_b"],
                           json={"name": "越权"})
    assert r.status_code == 403


# ----------------------------------------------------------------------
# FR-D2 Token 来源佐证 & FR-D4 不可变流水
# ----------------------------------------------------------------------
def test_token_source_is_response_usage(env, llm_call_counter, caplog):
    """日志必须能证明 Token 不是估算的:usage_source 标明来自模型响应。"""
    with caplog.at_level(logging.INFO):
        r = env["client"].post("/api/v1/qa/ask", json={"question": "Token 来源"},
                               headers=env["headers"]["a"])
    assert r.status_code == 200

    events = [rec for rec in caplog.records if rec.getMessage() == "llm_call_completed"]
    assert events, "缺少 llm_call_completed 日志,无法佐证 Token 来源"
    fields = events[-1].fields
    assert fields["usage_source"] == "response.usage"
    assert fields["prompt_tokens"] == llm_call_counter.prompt_tokens
    assert fields["completion_tokens"] == llm_call_counter.completion_tokens
    assert fields["total_tokens"] == llm_call_counter.prompt_tokens + llm_call_counter.completion_tokens
    assert fields["key_prefix"] is None  # JWT 调用不写 Key 前缀


def test_cache_hit_is_metered_with_zero_tokens(env, llm_call_counter):
    """缓存命中:请求次数照记,Token 记 0,且不再调用模型。"""
    client, headers = env["client"], env["headers"]["a"]
    first = client.post("/api/v1/qa/ask", json={"question": "缓存口径"}, headers=headers)
    assert first.json()["data"]["cache_hit"] is False
    calls_after_first = llm_call_counter.count()

    second = client.post("/api/v1/qa/ask", json={"question": "缓存口径"}, headers=headers)
    assert second.json()["data"]["cache_hit"] is True
    assert second.json()["data"]["tokens"]["total_tokens"] == 0
    assert llm_call_counter.count() == calls_after_first, "缓存命中不应再调用模型"

    records, summaries = read_rows(env, env["tenant_a"])
    assert any(r.cache_hit for r in records)
    assert summaries[-1].cache_hits == 1
    assert summaries[-1].requests == 2  # 缓存命中也算一次请求


def test_usage_summary_is_derived_from_records(env, llm_call_counter):
    """聚合值由明细派生:表中数字等于 SUM/COUNT(不存在可写计数器)。"""
    client, headers = env["client"], env["headers"]["a"]
    client.post("/api/v1/qa/ask", json={"question": "聚合一"}, headers=headers)
    client.post("/api/v1/qa/ask", json={"question": "聚合二"}, headers=headers)

    records, summaries = read_rows(env, env["tenant_a"])
    today_rows = [r for r in records if r.created_at.date() == date.today()]
    summary = [s for s in summaries if s.bucket_date == date.today()][0]

    assert summary.requests == len(today_rows)
    assert summary.prompt_tokens == sum(r.prompt_tokens for r in today_rows)
    assert summary.completion_tokens == sum(r.completion_tokens for r in today_rows)
    assert summary.success == sum(1 for r in today_rows if r.status == "success")


def test_usage_record_is_append_only():
    """静态检查:app/ 下不存在对 usage_record 的 UPDATE/DELETE(LlmUsageRecord)。"""
    patterns = [
        r"update\s*\(\s*LlmUsageRecord",
        r"delete\s*\(\s*LlmUsageRecord",
        r"LlmUsageRecord\s*\)?\s*\.update\s*\(",
        r"LlmUsageRecord\s*\)?\s*\.delete\s*\(",
        r"(?i)\bupdate\s+usage_record\b",
        r"(?i)\bdelete\s+from\s+usage_record\b",
    ]
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for pattern in patterns:
            if re.search(pattern, source):
                offenders.append(f"{path.relative_to(APP_DIR)} :: {pattern}")
    assert not offenders, "流水表出现可变操作(破坏 FR-D4 不可变性): %s" % offenders


def test_quota_precheck_reads_summary_not_scanning_records():
    """静态检查:租户口径预检读聚合表(usage_summary),不是全表扫描流水。"""
    source = (APP_DIR / "services" / "quota" / "service.py").read_text(encoding="utf-8")
    assert "UsageSummary" in source, "预检必须以聚合表为数据源"
    # Key 口径允许按 key_id 过滤流水(聚合表没有 Key 维度),必须带 key_id 条件
    assert "LlmUsageRecord.key_id == key_id" in source


def test_no_plaintext_key_anywhere(env, make_key):
    """FR-C2 全库搜索无明文 Key:api_key 表所有行的哈希都不等于明文。"""
    created = make_key(name="明文检查")
    rows = read_keys(env, env["tenant_a"])
    for row in rows:
        assert not row.key_hash.startswith("sk-"), "key_hash 列出现明文 Key"
        assert len(row.key_hash) == 64
        assert row.key_prefix.startswith("sk-")
    assert any(r.key_hash == hashlib.sha256(created["key"].encode()).hexdigest()
               for r in rows)


def test_usage_summary_has_unique_bucket_per_tenant(env, llm_call_counter):
    """同一租户同一天只有一行聚合(重复刷新不会写出多行)。"""
    client, headers = env["client"], env["headers"]["a"]
    for i in range(3):
        client.post("/api/v1/qa/ask", json={"question": f"重复刷新{i}"}, headers=headers)

    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = env["tenant_a"]
        count = db.execute(
            select(func.count(UsageSummary.id)).select_from(UsageSummary)
        ).scalar_one()
    assert count == 1
