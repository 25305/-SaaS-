# -*- coding: utf-8 -*-
"""模块 F 回归测试:用量看板与账单(FR-F1~F6)。

重点覆盖三条会被"判不合格"的红线:
1. **看板不扫明细全表(FR-F6)** —— 抓取本次请求实际执行的 SQL,断言标量指标
   只来自 `usage_summary`;凡涉及 `usage_record` 的语句都必须带 `created_at` 区间;
2. **账单可追溯(FR-F5)** —— `verify_bill_traceable()` 返回 `traceable=True`,
   而不是靠人工抽查;
3. **看板/账单按租户隔离** —— 租户 B 看不到租户 A 的用量。

测试用独立临时 SQLite(见 conftest.py 的 env fixture),不接触项目 qa.db。
"""

from datetime import date

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.services.model import rag
from app.services.model.llm_client import LlmCompletion, LlmClient, LlmUsage


class FakeModel(LlmClient):
    """确定性模型替身:返回固定 usage,让计量/看板数字可预期。"""

    provider = "test-double"

    def __init__(self, prompt_tokens: int = 12, completion_tokens: int = 30) -> None:
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


class SqlRecorder:
    """抓取引擎级别实际执行的 SQL 文本,用于编码"无全表扫描"这条要求。"""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self):
        self._engine = Engine

        def _record(conn, cursor, statement, parameters, context, executemany):
            self.statements.append(" ".join(statement.split()))

        self._handler = _record
        event.listen(self._engine, "before_cursor_execute", self._handler)
        return self

    def __exit__(self, *exc):
        event.remove(self._engine, "before_cursor_execute", self._handler)
        return False

    def matching(self, fragment: str) -> list[str]:
        return [s for s in self.statements if fragment in s]


def ask(client, headers, question: str):
    resp = client.post("/api/v1/qa/ask", json={"question": question}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ----------------------------------------------------------------------
# FR-F1 / F2 / F3 指标齐全、时间维度正确
# ----------------------------------------------------------------------
def test_dashboard_contract_and_metrics(env, fake_model):
    client, headers = env["client"], env["headers"]["a"]
    first = ask(client, headers, "选课什么时候开始")
    second = ask(client, headers, "图书馆能借几本")

    r = client.get("/api/v1/dashboard?range=today", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    # 契约字段齐全(前端图表直接消费)
    assert set(data) >= {"summary", "trend", "by_key", "latency_distribution", "range"}

    summary = data["summary"]
    # 两条非缓存请求:请求数、成功数、错误率、Token 分列、平均/P95、命中率
    assert summary["total_requests"] == 2
    assert summary["success"] == 2
    assert summary["errors"] == 0
    assert summary["error_rate"] == 0
    assert summary["prompt_tokens"] == fake_model.prompt_tokens * 2
    assert summary["completion_tokens"] == fake_model.completion_tokens * 2
    assert summary["avg_latency_ms"] >= 0
    assert summary["p95_latency_ms"] >= 0
    assert summary["cache_hit_rate"] == 0
    assert summary["quota_rejected"] == 0
    # 异步汇总的延迟提示(FR-F6 的取舍:数据非实时,需让前端标注更新时间)
    assert summary["summary_updated_at"]

    # 趋势按自然日分桶
    assert data["trend"][0]["date"] == date.today().isoformat()
    assert data["trend"][0]["requests"] == 2

    # Key 维度:JWT 登录态调用归到 jwt 分组(不返回完整 Key)
    jwt_group = [row for row in data["by_key"] if row["key_prefix"] == "jwt"]
    assert jwt_group and jwt_group[0]["requests"] == 2
    assert jwt_group[0]["total_tokens"] == (fake_model.prompt_tokens + fake_model.completion_tokens) * 2

    # 延迟分布直方图:五个桶且计数合计等于区间内流水条数
    buckets = data["latency_distribution"]
    assert [b["bucket"] for b in buckets] == [
        "0-500ms", "500-1000ms", "1000-2000ms", "2000-5000ms", "5000ms+",
    ]
    assert sum(b["count"] for b in buckets) == 2

    # 时间维度可切换(range=week 覆盖 today)
    week = client.get("/api/v1/dashboard?range=week", headers=headers).json()["data"]
    assert week["summary"]["total_requests"] == 2
    assert week["range"]["days"] == 7

    # 自定义区间校验
    assert client.get("/api/v1/dashboard?range=custom", headers=headers).status_code == 422


def test_dashboard_sql_only_reads_summary_for_scalars(env, fake_model):
    """FR-F6:标量指标来自 usage_summary;涉及 usage_record 的语句必须有区间约束。"""
    client, headers = env["client"], env["headers"]["a"]
    ask(client, headers, "SQL 走查")

    with SqlRecorder() as recorder:
        resp = client.get("/api/v1/dashboard?range=today", headers=headers)
    assert resp.status_code == 200

    assert recorder.matching("FROM usage_summary"), "看板必须读取汇总表"
    for statement in recorder.matching("FROM usage_record"):
        # 明细侧只允许有界区间聚合(不允许无条件全表扫描)
        assert "created_at >=" in statement and "created_at <" in statement, (
            "看板出现无区间约束的 usage_record 查询(违反 FR-F6): %s" % statement
        )


# ----------------------------------------------------------------------
# 租户隔离
# ----------------------------------------------------------------------
def test_dashboard_and_billing_are_tenant_isolated(env, fake_model):
    client = env["client"]
    ask(client, env["headers"]["a"], "A 的问题一")
    ask(client, env["headers"]["a"], "A 的问题二")
    ask(client, env["headers"]["b"], "B 的问题一")

    a = client.get("/api/v1/dashboard?range=today", headers=env["headers"]["a"]).json()["data"]
    b = client.get("/api/v1/dashboard?range=today", headers=env["headers"]["b"]).json()["data"]
    assert a["summary"]["total_requests"] == 2
    assert b["summary"]["total_requests"] == 1

    a_bill = client.get("/api/v1/billing/summary?period=today",
                        headers=env["headers"]["a"]).json()["data"]
    assert a_bill["total_requests"] == 2
    assert a_bill["total_tokens"] == (fake_model.prompt_tokens + fake_model.completion_tokens) * 2


# ----------------------------------------------------------------------
# FR-F4 / F5 账单导出与可追溯
# ----------------------------------------------------------------------
def test_bill_is_traceable_to_raw_records(env, fake_model):
    """账单每项数字都能在 usage_record 中找到对应记录(自动化断言,不靠人工抽查)。"""
    client, headers = env["client"], env["headers"]["a"]
    ask(client, headers, "账单问题一")
    ask(client, headers, "账单问题二")

    r = client.get("/api/v1/billing/verify?period=today", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["traceable"] is True, data["mismatches"]
    assert data["mismatches"] == {}
    assert data["checked"]["requests"] == 2


def test_bill_export_csv_and_xlsx_are_complete(env, fake_model):
    client, headers = env["client"], env["headers"]["a"]
    data = ask(client, headers, "导出行")
    assert data["cache_hit"] is False

    csv_resp = client.get("/api/v1/billing/export?period=today&fmt=csv", headers=headers)
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "attachment" in csv_resp.headers["content-disposition"]
    text = csv_resp.content.decode("utf-8-sig")
    # 导出的是流水本身:每行都带 request_id,可直接回查(FR-F5)
    assert "request_id" in text
    assert "prompt_tokens" in text
    assert text.strip().count("\n") >= 1

    xlsx_resp = client.get("/api/v1/billing/export?period=today&fmt=xlsx", headers=headers)
    assert xlsx_resp.status_code == 200
    assert xlsx_resp.content[:2] == b"PK", "xlsx 应为合法 zip 包"


def test_bill_detail_is_bounded_and_limited(env, fake_model):
    client, headers = env["client"], env["headers"]["a"]
    for i in range(3):
        ask(client, headers, f"明细问题 {i}")

    rows = client.get("/api/v1/billing/detail?period=today&limit=2",
                      headers=headers).json()["data"]
    assert len(rows) == 2, "detail 必须按 limit 有界返回"
    assert {"request_id", "prompt_tokens", "status"} <= set(rows[0])

    daily = client.get("/api/v1/billing/daily?period=today", headers=headers).json()["data"]
    assert daily[0]["requests"] == 3
    assert daily[0]["total_tokens"] > 0
