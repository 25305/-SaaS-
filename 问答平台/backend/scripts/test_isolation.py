# -*- coding: utf-8 -*-
"""越权测试脚本(模块 B,FR-B5)。

自动用租户 B 的凭证遍历租户 A 的全部资源 ID,验证每一笔跨租户访问都被拒绝,
并把结果写入 scripts/越权测试报告.md。

用法(需先启动服务):
    cd backend
    .venv\\Scripts\\python.exe scripts\\test_isolation.py            # 默认连 127.0.0.1:8000
    .venv\\Scripts\\python.exe scripts\\test_isolation.py http://127.0.0.1:8001   # 指定地址

退出码:0 = 全部拦截成功;1 = 存在失败项或环境错误。
纯标准库实现(urllib),零新依赖。
"""

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime

try:  # Windows 控制台默认 GBK,强制 UTF-8 输出避免乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
REPORT_PATH = "scripts/越权测试报告.md"

# 攻击方(租户 B)与受害方(租户 A)——均使用 seed 演示账号
A = ("academic_admin", "Academic@123")
B = ("physics_admin", "Physics@123")


def http(method, path, token=None, body=None):
    """发送请求,返回 (状态码, 解析后的 JSON, 原始响应文本)。"""
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, _parse(raw), raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        return e.code, _parse(raw), raw
    except urllib.error.URLError as e:
        print("无法连接服务器 %s (%s)。请先启动服务:uvicorn app.main:app --port 8000" % (BASE, e.reason))
        sys.exit(1)


def _parse(raw):
    try:
        return json.loads(raw)
    except Exception:
        return {}


def login(username, password):
    status, data, _ = http("POST", "/api/auth/login", body={"username": username, "password": password})
    if status != 200 or data.get("code") != 0:
        print("登录失败 %s: HTTP %s %s" % (username, status, data))
        sys.exit(1)
    return data["data"]["access_token"]


def main():
    print("=" * 62)
    print("越权测试:租户 B(%s) 攻击 租户 A(%s) 的资源" % (B[0], A[0]))
    print("=" * 62)

    token_a = login(*A)
    token_b = login(*B)

    # ---------- 1. 租户 A 枚举自己的全部资源 ID ----------
    status, data, _ = http("GET", "/api/kb/", token=token_a)
    assert status == 200 and data.get("code") == 0, "A 无法获取自己的知识库列表"
    kbs_a = data["data"]  # [{id, name, description, doc_count, created_at}]
    docs_a = []  # [(kb_id, kb_name, doc_id, doc_title)]
    for kb in kbs_a:
        _status, _data, _ = http("GET", "/api/kb/%s/docs" % kb["id"], token=token_a)
        assert _status == 200 and _data.get("code") == 0, "A 无法获取知识库 %s 的文档" % kb["id"]
        for doc in _data["data"]:
            docs_a.append((kb["id"], kb["name"], doc["id"], doc["title"]))
    print("租户 A 资源清单:%d 个知识库、%d 篇文档" % (len(kbs_a), len(docs_a)))
    for kb in kbs_a:
        print("  KB #%s %s" % (kb["id"], kb["name"]))

    results = []  # 每条攻击一行记录

    def record(attack, resource, method_path, expect, status, raw, secret):
        """记录一条攻击结果。secret: 若出现在响应文本中即视为泄露。"""
        leaked = secret and secret in raw
        passed = (status == expect) and not leaked
        results.append({
            "attack": attack, "resource": resource, "req": method_path,
            "expect": expect, "status": status, "passed": passed, "leaked": leaked,
        })
        return passed

    # ---------- 2. 阴性对照:A 访问自己的资源必须全部 200 ----------
    print("\n[阴性对照] A 访问自己的资源(应全部 200)……")
    control_ok = True
    for kb in kbs_a:
        status, _, _ = http("GET", "/api/kb/%s" % kb["id"], token=token_a)
        if status != 200:
            print("  ! 对照失败:GET /api/kb/%s -> %s" % (kb["id"], status))
            control_ok = False
    for kb_id, _kb_name, doc_id, _title in docs_a:
        status, _, _ = http("GET", "/api/kb/%s/docs/%s" % (kb_id, doc_id), token=token_a)
        if status != 200:
            print("  ! 对照失败:GET /api/kb/%s/docs/%s -> %s" % (kb_id, doc_id, status))
            control_ok = False
    if not control_ok:
        print("  阴性对照未通过:资源本身异常,后续结果不可信。")
        sys.exit(1)
    print("  通过:%d 个资源 A 自己都能正常访问" % (len(kbs_a) + len(docs_a)))

    # ---------- 3. 攻击:租户 B 重放 A 的全部资源 ID ----------
    print("\n[攻击] 租户 B 用 A 的资源 ID 逐项重放(应全部 404 且不泄露)……")
    # 知识库级攻击
    for kb in kbs_a:
        kb_id, kb_name = kb["id"], kb["name"]
        status, _, raw = http("GET", "/api/kb/%s" % kb_id, token=token_b)
        record("B", "KB #%s %s" % (kb_id, kb_name), "GET /api/kb/%s" % kb_id, 404, status, raw, kb_name)
        status, _, raw = http("PATCH", "/api/kb/%s" % kb_id, token=token_b, body={"name": "越权改名"})
        record("B", "KB #%s %s" % (kb_id, kb_name), "PATCH /api/kb/%s" % kb_id, 404, status, raw, kb_name)
        status, _, raw = http("GET", "/api/kb/%s/docs" % kb_id, token=token_b)
        record("B", "KB #%s %s" % (kb_id, kb_name), "GET /api/kb/%s/docs" % kb_id, 404, status, raw, kb_name)
        status, _, raw = http("POST", "/api/kb/%s/docs" % kb_id, token=token_b, body={"title": "越权文档", "content": "attack"})
        record("B", "KB #%s %s" % (kb_id, kb_name), "POST /api/kb/%s/docs" % kb_id, 404, status, raw, kb_name)
        status, _, raw = http("DELETE", "/api/kb/%s" % kb_id, token=token_b)
        record("B", "KB #%s %s" % (kb_id, kb_name), "DELETE /api/kb/%s" % kb_id, 404, status, raw, kb_name)
    # 文档级攻击
    for kb_id, kb_name, doc_id, doc_title in docs_a:
        status, _, raw = http("GET", "/api/kb/%s/docs/%s" % (kb_id, doc_id), token=token_b)
        record("B", "DOC #%s %s" % (doc_id, doc_title), "GET /api/kb/%s/docs/%s" % (kb_id, doc_id), 404, status, raw, doc_title)
        status, _, raw = http("PATCH", "/api/kb/%s/docs/%s" % (kb_id, doc_id), token=token_b, body={"title": "越权改标题"})
        record("B", "DOC #%s %s" % (doc_id, doc_title), "PATCH /api/kb/%s/docs/%s" % (kb_id, doc_id), 404, status, raw, doc_title)
        status, _, raw = http("DELETE", "/api/kb/%s/docs/%s" % (kb_id, doc_id), token=token_b)
        record("B", "DOC #%s %s" % (doc_id, doc_title), "DELETE /api/kb/%s/docs/%s" % (kb_id, doc_id), 404, status, raw, doc_title)

    # ---------- 4. 列表隔离检查 ----------
    print("\n[列表隔离] 检查租户 B 的列表里不出现 A 的任何资源名……")
    list_ok = True
    status, data, raw = http("GET", "/api/kb/", token=token_b)
    if status != 200 or data.get("code") != 0:
        print("  ! B 获取自己的列表失败")
        list_ok = False
    else:
        b_names = [kb["name"] for kb in data["data"]]
        for kb in kbs_a:
            leaked = kb["name"] in raw
            record("B", "列表", "GET /api/kb/ 中查 %s" % kb["name"], 200, status, raw, kb["name"])
            if leaked:
                list_ok = False
        if not b_names:
            print("  ! B 自己的列表为空(种子数据缺失?)")
            list_ok = False
    # 反向对照:B 访问自己的资源应正常
    status, data, _ = http("GET", "/api/kb/", token=token_b)
    b_own_ok = status == 200 and data.get("code") == 0 and len(data["data"]) > 0
    print("  反向对照:B 访问自己的列表 -> %s" % ("正常" if b_own_ok else "异常!"))

    # ---------- 5. 汇总 ----------
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    leaked_count = sum(1 for r in results if r["leaked"])

    print("\n" + "=" * 62)
    print("测试汇总:共 %d 项攻击,拦截成功 %d,失败 %d,信息泄露 %d" % (total, passed, failed, leaked_count))
    print("结论:%s" % ("全部跨租户访问被拒绝 ✔" if failed == 0 and list_ok else "存在未拦截的访问 ✘"))
    print("=" * 62)

    _write_report(results, total, passed, failed, leaked_count, list_ok, kbs_a, docs_a)

    if failed == 0 and list_ok:
        sys.exit(0)
    sys.exit(1)


def _write_report(results, total, passed, failed, leaked_count, list_ok, kbs_a, docs_a):
    """把结果写入 scripts/越权测试报告.md(UTF-8)。"""
    lines = [
        "# 越权测试报告(模块 B)",
        "",
        "- 生成时间:%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 目标服务:%s" % BASE,
        "- 受害方(租户 A):%s(%d 个知识库、%d 篇文档)" % (A[0], len(kbs_a), len(docs_a)),
        "- 攻击方(租户 B):%s" % B[0],
        "- 测试方式:用租户 B 的凭证遍历租户 A 的全部资源 ID,重放查询/修改/删除请求",
        "",
        "## 汇总",
        "",
        "| 攻击总数 | 拦截成功 | 失败 | 信息泄露 | 列表隔离 |",
        "|---|---|---|---|---|",
        "| %d | %d | %d | %d | %s |" % (
            total, passed, failed, leaked_count, "通过" if list_ok else "未通过"),
        "",
        "**最终结论:%s**" % (
            "全部跨租户访问被拒绝,隔离有效。" if failed == 0 and list_ok else "存在未拦截的跨租户访问,需修复!"),
        "",
        "## 明细",
        "",
        "| # | 攻击方 | 目标资源 | 请求 | 预期 | 实际状态码 | 是否通过 | 是否泄露信息 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        lines.append(
            "| %d | %s | %s | %s | %d | %d | %s | %s |" % (
                i, r["attack"], r["resource"], r["req"], r["expect"], r["status"],
                "✔" if r["passed"] else "✘", "✘ 泄露" if r["leaked"] else "无"))
    lines += [
        "",
        "## 判定标准",
        "",
        "- 跨租户访问一律返回 404,且响应体中不出现受害方资源的名称(不泄露存在性);",
        "- 受害方访问自己的资源正常返回 200(阴性对照);",
        "- 攻击方访问自己的资源正常返回 200(反向对照)。",
        "",
    ]
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("报告已写入:%s" % REPORT_PATH)


if __name__ == "__main__":
    main()
