"""成员 D 看板验收：接口读取汇总，不扫描用量流水。"""

from datetime import date, timedelta
from pathlib import Path

from app.db.tenant_hook import TENANT_ID_KEY
from app.models import UsageLatencyBucket, UsageSummary
from app.routers.usage import _dashboard


def test_dashboard_uses_materialized_daily_summary(env):
    """看板须由 usage_summary 组成，并返回三种图表所需字段。"""
    with env["db"]() as db:
        db.info[TENANT_ID_KEY] = env["tenant_a"]
        db.add_all([
            UsageSummary(tenant_id=env["tenant_a"], bucket_date=date.today() - timedelta(days=1),
                         requests=4, success=3, errors=1, prompt_tokens=11, completion_tokens=22,
                         avg_latency_ms=110, p95_latency_ms=200, cache_hits=2),
            UsageSummary(tenant_id=env["tenant_a"], bucket_date=date.today(),
                         requests=6, success=6, errors=0, prompt_tokens=33, completion_tokens=44,
                         avg_latency_ms=210, p95_latency_ms=500, cache_hits=3),
        ])
        db.add_all([
            UsageLatencyBucket(tenant_id=env["tenant_a"], bucket_date=date.today() - timedelta(days=1), bucket="0-500ms", count=4),
            UsageLatencyBucket(tenant_id=env["tenant_a"], bucket_date=date.today(), bucket="0-500ms", count=6),
        ])
        db.commit()
        result = _dashboard(db, 30)

    assert result["summary"]["total_requests"] == 10
    assert result["summary"]["p95_latency_ms"] == 500
    assert result["summary"]["cache_hit_rate"] == 50.0
    assert result["summary"]["error_rate"] == 10.0
    assert len(result["trend"]) == 2
    assert result["latency_distribution"], "前端延迟柱状图需要分布数据"


def test_dashboard_route_does_not_query_usage_record_directly():
    """FR-F6：看板路由不能出现 usage_record 的全表扫描。"""
    source = (Path(__file__).resolve().parents[1] / "app" / "routers" / "usage.py").read_text(encoding="utf-8")
    dashboard_source = source[source.index("def _dashboard"):source.index("def _billing_rows")]
    assert "LlmUsageRecord" not in dashboard_source
