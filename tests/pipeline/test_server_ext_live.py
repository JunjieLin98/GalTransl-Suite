"""M2 后端安全与功能套件(单进程直连 12333;手动运行,依赖本机样例游戏)。

带 __main__ 守卫:pytest 收集本文件只会得到 0 个用例,不会触发真实请求
(此前 import 即自跑并 sys.exit,曾令 CI 收集阶段 INTERNALERROR)。
用法: .venv/Scripts/python.exe tests/pipeline/test_server_ext_live.py
(需先手动启动后端: .venv/Scripts/python.exe run_backend.py)
"""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, r"L:\doc\AI-Project\galTrans")
from galtrans_pipeline.server_ext import (  # noqa: E402
    load_or_create_token,
    issue_sse_ticket,
    consume_sse_ticket,
)

BASE = "http://127.0.0.1:12333"
RESULTS: list[tuple[str, bool, str]] = []


def call(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8") or "{}")
    except Exception as error:  # 连接失败等
        return -1, {"detail": str(error)}


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


def main() -> int:
    token = load_or_create_token()

    # 0. 服务可达
    status, body = call("GET", "/api/version")
    check("backend alive", status == 200, str(body))

    # 1. 恶意 Host → 403(BLK-01 防护)
    status, body = call("GET", "/api/pipeline/profiles", headers={"Host": "evil.com"})
    check("malicious host rejected", status == 403, f"{status} {body}")

    # 2. profiles 正常读取
    status, body = call("GET", "/api/pipeline/profiles")
    check("profiles list", status == 200 and any(p["profile"] == "kirikiri" for p in body.get("profiles", [])), str(body)[:80])

    # 2b. 外部工具检测(工具箱多目录解析)
    status, body = call("GET", "/api/pipeline/tools")
    tools_found = {t["name"] for t in body.get("tools", []) if t.get("found")}
    check("tools endpoint", status == 200 and "msg-tool" in tools_found, str(body)[:120])

    # 3. detect 无 token → 401(FR-G1)
    status, body = call("POST", "/api/pipeline/detect", {"game_dir": "L:/gal"})
    check("detect without token -> 401", status == 401, f"{status}")

    # 4. detect 带 token → 200
    status, body = call(
        "POST",
        "/api/pipeline/detect",
        {"game_dir": "L:/gal/Relirium -レリリウム- 遺跡と出逢いと冒険と"},
        headers={"X-Local-Token": token},
    )
    detected = body.get("results", [{}])[0].get("profile") == "yuris"
    check("detect with token", status == 200 and detected, str(body)[:100])

    # 5. SSE 票据:签发→消费成功→重放失败
    ticket = issue_sse_ticket()
    check("sse ticket issue+consume", consume_sse_ticket(ticket))
    check("sse ticket replay rejected", not consume_sse_ticket(ticket))

    # 6. 无 token 的 sse-ticket 签发 → 401
    status, body = call("POST", "/api/pipeline/sse-ticket", {})
    check("sse-ticket requires token", status == 401, f"{status}")

    # 7. 创建工程 + run(桩:走到 TRANSLATE 会因无 key 停,这里只验证 run 受理)
    proj = r"C:\Users\74994\AppData\Local\Temp\e2e\http_proj"
    status, body = call(
        "POST",
        "/api/pipeline/projects",
        {"game_dir": "L:/gal/とける風花とシロうさぎ", "profile": "kirikiri", "project_dir": proj},
        headers={"X-Local-Token": token},
    )
    check("create project", status == 200, str(body)[:100])

    # 8. status 带 token
    status, body = call(
        "GET", "/api/pipeline/status?project_dir=" + proj.replace("\\", "/"),
        headers={"X-Local-Token": token},
    )
    check("status with token", status == 200 and body.get("profile") == "kirikiri", str(body)[:100])

    # 9. status 无 token → 401
    status, body = call("GET", "/api/pipeline/status?project_dir=" + proj.replace("\\", "/"))
    check("status without token -> 401", status == 401, f"{status}")

    # 10. M3 缓存编辑端点(Relirium 工程,mock 翻译会话已产出真实缓存)
    relirium = r"L:\gal\Relirium -レリリウム- 遺跡と出逢いと冒険と_patch"
    relirium_q = urllib.parse.quote(relirium)
    status, body = call(
        "GET", "/api/pipeline/cache?project=" + relirium_q,
        headers={"X-Local-Token": token},
    )
    cache_files = body.get("files", [])
    check(
        "cache list has files",
        status == 200 and len(cache_files) > 200,
        f"files={len(cache_files)}",
    )

    status, body = call(
        "GET",
        "/api/pipeline/cache/entries?project=" + relirium_q
        + "&file=sc__scenario__00_tr.txt.json",
        headers={"X-Local-Token": token},
    )
    entry_ok = status == 200 and body.get("total", 0) >= 1
    first_index = body["entries"][0]["index"] if entry_ok else None
    check("cache entries page", entry_ok, f"total={body.get('total')}")

    status, body = call(
        "POST", "/api/pipeline/cache/entry",
        {
            "project": relirium,
            "file": "sc__scenario__00_tr.txt.json",
            "index": first_index,
            "locked": True,
        },
        headers={"X-Local-Token": token},
    )
    check("cache entry lock", status == 200 and body.get("locked") is True, str(body)[:120])

    status, body = call("POST", "/api/pipeline/cache/entry", {"project": relirium, "file": "x", "index": 0}, headers={"X-Local-Token": token})
    check("cache entry invalid file -> 400", status == 400, f"{status}")

    # 11. M4 问题状态端点
    status, body = call(
        "POST", "/api/pipeline/cache/problem-status",
        {"project": relirium, "file": "sc__scenario__00_tr.txt.json", "index": 1, "status": "confirmed"},
        headers={"X-Local-Token": token},
    )
    check("problem status set", status == 200 and body.get("status") == "confirmed", str(body)[:120])

    status, body = call(
        "GET",
        "/api/pipeline/cache/entries?project=" + relirium_q + "&file=sc__scenario__00_tr.txt.json&problem=1",
        headers={"X-Local-Token": token},
    )
    statuses = [e.get("problem_status") for e in body.get("entries", [])]
    check("entries carry problem_status", status == 200 and "confirmed" in statuses, str(statuses)[:80])

    # 12. M4 术语草稿端点(空工程草稿读取不炸)
    status, body = call(
        "GET", "/api/pipeline/glossary?project=" + relirium_q,
        headers={"X-Local-Token": token},
    )
    check("glossary state", status == 200 and isinstance(body.get("draft"), list), f"draft={len(body.get('draft', []))}")

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f"套件结果: {len(RESULTS) - len(failed)}/{len(RESULTS)} 通过; 失败: {failed or '无'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
