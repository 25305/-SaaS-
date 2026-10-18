# -*- coding: utf-8 -*-
"""性能测试脚本(生成《性能测试记录》,并发 20)。

对问答接口 `/api/v1/qa/ask` 施加并发请求,统计客户端观测延迟与错误率,
产出 `docs/性能测试记录.md`(UTF-8)。

用法(需先启动服务):
    cd backend
    python scripts\\perf_test.py                          # 默认 127.0.0.1:8000,并发 20,共 20 请求
    python scripts\\perf_test.py --concurrency 20 --requests 40
    python scripts\\perf_test.py --base-url http://127.0.0.1:8001

设计说明:
- 纯标准库(urllib + concurrent.futures),零新依赖,与 scripts/test_isolation.py 一致;
- 每个请求使用不同问题,强制缓存 MISS(命中缓存会绕过模型,测不出真实链路延迟);
- 问答链路顺序为:限流 → 配额预检 → 查缓存 → 调模型,故本脚本测的是包含上游模型调用
  的端到端延迟;若上游限流/超时,错误会被如实计入错误率(不因失败而重试掩盖)。
退出码:0 = 全部成功;1 = 存在失败请求或环境错误。
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

try:  # Windows 控制台默认 GBK,强制 UTF-8 输出避免乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 项目根目录下的 docs(script 位于 backend/scripts/,向上两级到「问答平台」)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = PROJECT_ROOT / "docs" / "性能测试记录.md"

DEMO_ACCOUNT = ("academic_admin", "Academic@123")


def http_json(method, url, token=None, body=None, timeout=60):
    """发送请求,返回 (状态码, 解析后的 JSON)。"""
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, _parse(raw)
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print("无法连接服务器 %s (%s)。请先启动服务:uvicorn app.main:app --port 8000"
              % (url, e.reason))
        sys.exit(1)


def _parse(raw):
    try:
        return json.loads(raw)
    except Exception:
        return {}


def login(base_url):
    status, data = http_json(
        "POST", base_url + "/api/auth/login",
        body={"username": DEMO_ACCOUNT[0], "password": DEMO_ACCOUNT[1]},
    )
    if status != 200 or data.get("code") != 0:
        print("登录失败: HTTP %s %s" % (status, data))
        sys.exit(1)
    return data["data"]["access_token"]


def one_request(base_url, token, index):
    """发一次问答请求,返回观测结果 dict。"""
    question = "性能压测问题 %d-%s,请简要作答" % (index, uuid.uuid4().hex[:8])
    start = time.perf_counter()
    status, data = http_json("POST", base_url + "/api/v1/qa/ask",
                             token=token, body={"question": question})
    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    ok = status == 200 and isinstance(data, dict) and data.get("code") == 0
    server_latency = None
    if ok:
        server_latency = (data.get("data") or {}).get("latency_ms")
    return {
        "index": index,
        "status": status,
        "ok": ok,
        "elapsed_ms": elapsed_ms,
        "server_latency_ms": server_latency,
        "error_code": None if ok else (data.get("code") if isinstance(data, dict) else "HTTP_%s" % status),
    }


def percentile(values, ratio):
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(ratio * len(ordered) + 0.5) - 1)  # 最近秩法,与计量模块口径一致
    return ordered[min(rank, len(ordered) - 1)]


def run(base_url, token, concurrency, requests):
    print("=" * 62)
    print("性能测试:%s 并发 %d,共 %d 次问答请求" % (base_url, concurrency, requests))
    print("=" * 62)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(lambda i: one_request(base_url, token, i), range(requests)))
    wall_seconds = round(time.perf_counter() - started, 2)
    return results, wall_seconds


def summarize(results, wall_seconds, concurrency):
    latencies = [r["elapsed_ms"] for r in results]
    server_latencies = [r["server_latency_ms"] for r in results if r["server_latency_ms"] is not None]
    success = sum(1 for r in results if r["ok"])
    errors = len(results) - success
    return {
        "total": len(results),
        "success": success,
        "errors": errors,
        "error_rate": round(errors / len(results), 4) if results else 0.0,
        "min_ms": round(min(latencies), 1) if latencies else 0.0,
        "avg_ms": round(statistics.mean(latencies), 1) if latencies else 0.0,
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "max_ms": round(max(latencies), 1) if latencies else 0.0,
        "server_avg_ms": round(statistics.mean(server_latencies), 1) if server_latencies else 0.0,
        "throughput": round(len(results) / wall_seconds, 2) if wall_seconds else 0.0,
        "wall_seconds": wall_seconds,
        "concurrency": concurrency,
    }


def write_report(stats, results, base_url):
    lines = [
        "# 性能测试记录",
        "",
        "- 生成时间:%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 目标服务:%s" % base_url,
        "- 测试接口:`POST /api/v1/qa/ask`(问答链路:限流 → 配额预检 → 查缓存 → 调模型)",
        "- 演示账号:%s" % DEMO_ACCOUNT[0],
        "- 并发数:%d" % stats["concurrency"],
        "- 请求总数:%d(%s)" % (stats["total"], "每个请求使用不同问题以强制缓存 MISS,测真实链路"),
        "",
        "## 结果汇总",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        "| 成功率 | %d / %d(错误率 %.2f%%) |" % (
            stats["success"], stats["total"], stats["error_rate"] * 100),
        "| 平均延迟(客户端观测) | %.1f ms |" % stats["avg_ms"],
        "| P50 延迟 | %.1f ms |" % stats["p50_ms"],
        "| P95 延迟 | %.1f ms |" % stats["p95_ms"],
        "| 最小 / 最大延迟 | %.1f / %.1f ms |" % (stats["min_ms"], stats["max_ms"]),
        "| 服务端自报平均延迟(latency_ms) | %.1f ms |" % stats["server_avg_ms"],
        "| 吞吐量 | %.2f req/s |" % stats["throughput"],
        "| 总墙钟时间 | %.2f s |" % stats["wall_seconds"],
        "",
        "## 逐请求明细",
        "",
        "| # | HTTP 状态 | 成功 | 客户端观测延迟(ms) | 服务端 latency_ms | 错误码 |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append("| %d | %s | %s | %s | %s | %s |" % (
            r["index"] + 1, r["status"], "✔" if r["ok"] else "✘",
            r["elapsed_ms"], r["server_latency_ms"] if r["server_latency_ms"] is not None else "-",
            r["error_code"] or "-"))
    lines += [
        "",
        "## 说明",
        "",
        "- 延迟为**端到端**观测值(含上游模型调用),故量级受模型服务响应时间主导;",
        "- 服务端自报 `latency_ms` 为问答接口内部计时,与客户端观测值之差含网络与排队开销;",
        "- 错误(4xx/5xx)按实际状态码如实计入,不做失败重试,以反映真实可用性;",
        "- 该记录为课程演示环境的单次抽样,非严格的基准测试(受网络与上游模型波动影响)。",
        "",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print("报告已写入:%s" % REPORT_PATH)


def main():
    parser = argparse.ArgumentParser(description="问答接口并发性能测试")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=20)
    args = parser.parse_args()

    token = login(args.base_url)
    results, wall_seconds = run(args.base_url, token, args.concurrency, args.requests)
    stats = summarize(results, wall_seconds, args.concurrency)

    print("\n成功率 %d/%d,平均 %.1f ms,P95 %.1f ms,吞吐 %.2f req/s"
          % (stats["success"], stats["total"], stats["avg_ms"], stats["p95_ms"], stats["throughput"]))
    write_report(stats, results, args.base_url)

    sys.exit(0 if stats["errors"] == 0 else 1)


if __name__ == "__main__":
    main()
