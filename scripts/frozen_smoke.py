"""冻结后端真实管线冒烟(发布门禁;手动运行,依赖本机样例游戏)。

对 release/app/backend/galtransl_backend.exe:
  1. 体积门禁:后端 exe ≥ 30MB(系统 Python 误打曾产出 16MB 缺依赖坏包);
  2. 启动冻结 exe → API 建工程 → 跑 UNPACK(+EXTRACT 可选);
  3. 校验产物非空。

用法:
  .venv/Scripts/python.exe scripts/frozen_smoke.py            # UNPACK,EXTRACT
  .venv/Scripts/python.exe scripts/frozen_smoke.py --only UNPACK
  可用参数:--game/--project-dir/--port/--timeout-min
注意:构建安装包前必须用装好 requirements.txt 的解释器跑 build_windows.py
(build_windows.py 已有依赖预检防呆)。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BACKEND = REPO / "release" / "app" / "backend" / "galtransl_backend.exe"
MIN_BACKEND_BYTES = 30_000_000
DEFAULT_GAME = r"L:\gal\マガルミナ ゲーム本編データ・サントラデータ"


def call(base: str, token: str, method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(base + path, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Local-Token", token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8") or "{}")
    except Exception as error:
        return -1, {"detail": str(error)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", default=DEFAULT_GAME)
    parser.add_argument("--project-dir", default="")
    parser.add_argument("--port", type=int, default=12402)
    parser.add_argument("--only", default="UNPACK,EXTRACT")
    parser.add_argument("--timeout-min", type=int, default=20)
    parser.add_argument("--skip-extract-check", action="store_true",
                        help="不校验 EXTRACT 产物数(仅要求 > 0)")
    args = parser.parse_args()

    from galtrans_pipeline.server_ext import load_or_create_token

    if not BACKEND.is_file():
        print(f"[FAIL] 后端不存在: {BACKEND}(先跑 build_windows.py)")
        return 1
    size = BACKEND.stat().st_size
    print(f"[{'OK' if size >= MIN_BACKEND_BYTES else 'FAIL'}] backend size {size:,}B")
    if size < MIN_BACKEND_BYTES:
        print("       体积过小:疑似缺依赖坏包(检查构建解释器是否装好 requirements)")
        return 1

    project_dir = Path(args.project_dir) if args.project_dir else Path(
        Path(os.environ.get("TEMP", ".")) / "gt_frozen_smoke_proj"
    )
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)

    base = f"http://127.0.0.1:{args.port}"
    token = load_or_create_token()
    proc = subprocess.Popen(
        [str(BACKEND), "--host", "127.0.0.1", "--port", str(args.port)],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        for _ in range(45):
            time.sleep(2)
            status, body = call(base, token, "GET", "/api/version")
            if status == 200:
                print(f"[OK] backend alive {body}")
                break
        else:
            print("[FAIL] backend did not start")
            return 1

        status, body = call(base, token, "GET", "/api/pipeline/profiles")
        ok = status == 200 and len(body.get("profiles", [])) >= 3
        print(f"[{'OK' if ok else 'FAIL'}] profiles {len(body.get('profiles', []))}")
        if not ok:
            return 1

        status, body = call(
            base, token, "POST", "/api/pipeline/projects",
            {"project_dir": str(project_dir), "game_dir": args.game, "profile": "kirikiri"},
        )
        ok = status == 200
        print(f"[{'OK' if ok else 'FAIL'}] create project {status} {str(body)[:100]}")
        if not ok:
            return 1

        status, body = call(
            base, token, "POST", "/api/pipeline/run",
            {"project_dir": str(project_dir), "only": args.only},
        )
        print(f"[{'OK' if status == 200 else 'FAIL'}] run started {status} {str(body)[:80]}")
        if status != 200:
            return 1

        deadline = time.monotonic() + args.timeout_min * 60
        while time.monotonic() < deadline:
            time.sleep(10)
            status, body = call(
                base, token,
                "GET", "/api/pipeline/status?project_dir=" + urllib.parse.quote(str(project_dir)),
            )
            steps = body.get("steps", {}) if isinstance(body, dict) else {}
            wanted = [s.strip().upper() for s in args.only.split(",") if s.strip()]
            if wanted and all(
                steps.get(s, {}).get("status") == "done" for s in wanted
            ):
                break
        else:
            print("[FAIL] steps did not finish in time")
            return 1

        for step in [s.strip().upper() for s in args.only.split(",") if s.strip()]:
            note = steps.get(step, {}).get("note", "")
            print(f"[{'OK' if steps.get(step, {}).get('status') == 'done' else 'FAIL'}] {step}: {note}")
        if "EXTRACT" in args.only.upper():
            extracted = project_dir / "work" / "extracted"
            n = len(list(extracted.glob("*.json"))) if extracted.exists() else 0
            floor = 1 if args.skip_extract_check else 200
            ok = n >= floor
            print(f"[{'OK' if ok else 'FAIL'}] extracted JSON files: {n}(门槛 {floor})")
            if not ok:
                return 1
        print("[OK] 冻结后端管线冒烟通过")
        return 0
    finally:
        proc.kill()


if __name__ == "__main__":
    sys.exit(main())
