"""server_ext — 上游 12333 后端的流水线扩展(M2)。

挂载点:GalTransl/server.py 的 do_GET/do_POST 入口(受控改造,`# galTrans:` 注记,可上游化):
    if path.startswith("/api/pipeline"):
        from galtrans_pipeline.server_ext import handle_pipeline_get / handle_pipeline_post

安全(架构 §4.1):
    1. Host 白名单(防 DNS Rebinding,实测上游无校验);
    2. 写操作 + 敏感读强制 X-Local-Token(DPAPI 加密落盘);
    3. SSE 用一次性短时票据换取(EventSource 不支持自定义头)。
"""

from __future__ import annotations

import ctypes
import hmac
import json
import os
import queue
import secrets
import shutil
import sys
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .errors import PipelineError
from .orchestrator import Pipeline
from .profile import load_profiles
from .project import PatchProject
from .runner import ProcessRunner
from .toolbox import ToolBox

# ---------------------------------------------------------------- 仓库定位
def _app_root() -> Path:
    """仓库根(开发态)/ 发布包根(冻结态:backend/galtransl_backend.exe 的上级)。

    发布布局(见 scripts/build_windows.py):
      app/{GalTransl Suite.exe, backend/, plugins/, profiles/, tools/bin/, res/}
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


REPO_ROOT = _app_root()


def _profiles_dir() -> Path:
    """env 优先的 profiles 目录(与 toolbox 多目录策略同源的单一事实)。"""
    from .profile import default_profiles_dir

    return default_profiles_dir()

# ---------------------------------------------------------------- Host 白名单
ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
# GUI 自身来源(Tauri v2: dev=127.0.0.1:1420, 打包态=tauri.localhost);
# token 下发仅允许这些 Origin(外来网页 Origin 为自身域名或 null → 拒绝)
ALLOWED_ORIGINS = {
    "http://127.0.0.1:1420",
    "http://localhost:1420",
    "http://tauri.localhost",
    "https://tauri.localhost",
}


def origin_allowed(handler) -> bool:
    origin = (handler.headers.get("Origin") or "").strip()
    if not origin:
        return True  # 非浏览器客户端(curl 等);仍受 Host 校验与 token 保护
    return origin in ALLOWED_ORIGINS


def host_allowed(handler) -> bool:
    """严格校验 Host 头(修复 BLK-01:上游实测恶意 Host 照常返回 200)。"""
    host = (handler.headers.get("Host") or "").strip().lower()
    if not host:
        return False
    if ":" in host:
        host_part, _, port_part = host.rpartition(":")
        if not port_part.isdigit():
            return False
    else:
        host_part = host
    return host_part in ALLOWED_HOSTS


def _reject_host(handler) -> None:
    handler._gt_cors_origin = ""  # 拒绝响应不携带任何 CORS 头
    body = json.dumps({"error": "E-AUTH-UNAUTHORIZED", "detail": "invalid host"}).encode()
    handler.send_response(HTTPStatus.FORBIDDEN)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


# ---------------------------------------------------------------- Token(DPAPI)
_TOKEN_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "galtrans"
_TOKEN_FILE = _TOKEN_DIR / "local_token.bin"


def _dpapi_protect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.c_void_p))
    blob_out = Blob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(blob_out.pbData))


def _dpapi_unprotect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.c_void_p))
    blob_out = Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(blob_out.pbData))


def load_or_create_token() -> str:
    """本地鉴权 token:首启生成,DPAPI 加密落盘;解密失败引导重新生成。"""
    import os

    try:
        if _TOKEN_FILE.is_file():
            return _dpapi_unprotect(_TOKEN_FILE.read_bytes()).decode("utf-8")
    except OSError:
        pass  # 跨用户/提权导致解密失败 → 重新生成(平滑降级,architecture §4.1)
    token = secrets.token_urlsafe(32)
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _TOKEN_FILE.write_bytes(_dpapi_protect(token.encode("utf-8")))
    except OSError:
        # DPAPI 不可用(非 Windows 等):降级明文 + 仅本机目录权限
        _TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


_TOKEN = load_or_create_token()


def token_ok(handler) -> bool:
    supplied = handler.headers.get("X-Local-Token", "")
    return hmac.compare_digest(supplied, _TOKEN)


def _require_token(handler) -> bool:
    if token_ok(handler):
        return True
    _send_json(
        handler,
        {"error": "E-AUTH-UNAUTHORIZED", "detail": "missing or invalid X-Local-Token"},
        status=HTTPStatus.UNAUTHORIZED,
    )
    return False


# ---------------------------------------------------------------- SSE 票据
_SSE_TICKETS: dict[str, float] = {}
_TICKET_TTL = 30.0


def issue_sse_ticket() -> str:
    ticket = secrets.token_urlsafe(16)
    _SSE_TICKETS[ticket] = time.monotonic()
    return ticket


def consume_sse_ticket(ticket: str) -> bool:
    issued = _SSE_TICKETS.pop(ticket, None)
    return issued is not None and (time.monotonic() - issued) <= _TICKET_TTL


# ---------------------------------------------------------------- 流水线管理器
class PipelineManager:
    """进程内单例:活跃流水线 + SSE 事件广播(架构 §3.5 JobRegistry 的服务端形态)。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._subscribers: list[queue.Queue] = []
        self._run_queue: list[dict[str, Any]] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def emit(self, event: str, data: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait({"event": event, "data": data})
            except queue.Full:
                pass

    def start_run(
        self,
        project_dir: str,
        from_step: str = "",
        only: str = "",
        api_key: str = "",
        endpoint: str = "",
        model: str = "",
        allow_queue: bool = False,
    ) -> dict[str, Any]:
        if self.has_active():
            if allow_queue:
                with self._lock:
                    self._run_queue.append(
                        {
                            "project_dir": project_dir,
                            "from_step": from_step,
                            "only": only,
                            "api_key": api_key,
                            "endpoint": endpoint,
                            "model": model,
                        }
                    )
                    position = len(self._run_queue)
                self.emit("job_queued", {"project_dir": project_dir, "position": position})
                self.emit("queue_updated", {"length": position})
                return {"queued": True, "queue_position": position}
            raise PipelineError(
                "E-CACHE-BUSY", "已有流水线任务在运行,请等待完成或取消后再试。"
            )
        job_id = secrets.token_urlsafe(8)
        self._launch_run(job_id, project_dir, from_step, only, api_key, endpoint, model)
        return {"queued": False, "job_id": job_id}

    def _launch_run(
        self,
        job_id: str,
        project_dir: str,
        from_step: str = "",
        only: str = "",
        api_key: str = "",
        endpoint: str = "",
        model: str = "",
    ) -> None:
        project = PatchProject.load(Path(project_dir))
        profiles = load_profiles(_profiles_dir())
        profile = profiles[project.profile_name].apply_override(project.overrides)
        job_id = secrets.token_urlsafe(8)
        runner = ProcessRunner()
        pipeline = Pipeline(project, profile, toolbox=ToolBox(), runner=runner)
        cancel_event = threading.Event()

        def on_progress(step: str, message: str) -> None:
            self.emit("log", {"job_id": job_id, "step": step, "message": message})

        pipeline.registry.add_progress_callback(on_progress)

        def worker() -> None:
            self.emit("job_started", {"job_id": job_id, "project_dir": project_dir})
            try:
                results = pipeline.run(
                    from_step=from_step or None,
                    only=[s.strip() for s in only.split(",")] if only else None,
                    api_key=api_key,
                    endpoint=endpoint,
                    model=model,
                    cancel_event=cancel_event,
                )
                summary = {
                    key: (value if isinstance(value, int) else len(value))
                    for key, value in results.items()
                }
                self.emit("job_done", {"job_id": job_id, "results": summary})
            except PipelineError as error:
                self.emit(
                    "job_failed",
                    {"job_id": job_id, "code": error.code, "message": str(error)},
                )
            except Exception as error:  # pragma: no cover
                self.emit("job_failed", {"job_id": job_id, "code": "unknown", "message": str(error)})
            finally:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job:
                        job["status"] = "done"
                self._drain_queue()

        thread = threading.Thread(target=worker, daemon=True)
        with self._lock:
            self._jobs[job_id] = {
                "thread": thread,
                "pipeline": pipeline,
                "cancel_event": cancel_event,
                "status": "running",
            }
        thread.start()
        return job_id

    def _drain_queue(self) -> None:
        """任务完成后出队下一个排队流水线(FR-F7 顺序执行)。

        在 worker 线程内调用;直接同步启动下一任务,避免与其他入队竞态。
        """
        with self._lock:
            item = self._run_queue.pop(0) if self._run_queue else None
            length = len(self._run_queue)
        if item is None:
            return
        self.emit("queue_updated", {"length": length})
        self.emit(
            "log",
            {
                "job_id": "queue",
                "step": "QUEUE",
                "message": f"开始执行排队任务:{item['project_dir']}",
            },
        )
        try:
            self._launch_run(
                secrets.token_urlsafe(8),
                item["project_dir"],
                from_step=item["from_step"],
                only=item["only"],
                api_key=item["api_key"],
                endpoint=item["endpoint"],
                model=item["model"],
            )
        except PipelineError as error:
            self.emit(
                "job_failed",
                {"job_id": "queue", "code": error.code, "message": str(error)},
            )
            self._drain_queue()

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return False
        job["cancel_event"].set()
        job["pipeline"].registry.cancel_active()
        return True

    def has_active(self) -> bool:
        with self._lock:
            return any(job["status"] == "running" for job in self._jobs.values())

    def start_rebuild(self, project_dir: str, compact: bool = False) -> str:
        """后台重建输出(rebuildr 模式,不调 API);可选先合并 append 日志。

        与 start_run 共享单活跃约束:重建期间缓存不可编辑(写端点被
        has_active 拦下),保证单写者原则。
        """
        from . import cache_editor

        if self.has_active():
            raise PipelineError(
                "E-CACHE-BUSY", "已有任务在运行,请等待完成或取消后再重建输出。"
            )
        project = PatchProject.load(Path(project_dir))
        job_id = secrets.token_urlsafe(8)

        def worker() -> None:
            self.emit(
                "job_started", {"job_id": job_id, "project_dir": project_dir, "mode": "rebuild"}
            )
            try:
                if compact:
                    merged = cache_editor.compact_append_logs(project)
                    self.emit(
                        "log",
                        {"job_id": job_id, "step": "CACHE", "message": f"合并了 {merged} 个 append 日志"},
                    )
                count = cache_editor.rebuild_output(project)
                self.emit(
                    "log",
                    {"job_id": job_id, "step": "CACHE", "message": f"从缓存重建输出 {count} 个文件"},
                )
                self.emit("job_done", {"job_id": job_id, "results": {"rebuilt": count}})
            except PipelineError as error:
                self.emit(
                    "job_failed", {"job_id": job_id, "code": error.code, "message": str(error)}
                )
            except Exception as error:  # pragma: no cover
                self.emit("job_failed", {"job_id": job_id, "code": "unknown", "message": str(error)})
            finally:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job:
                        job["status"] = "done"

        thread = threading.Thread(target=worker, daemon=True)
        with self._lock:
            self._jobs[job_id] = {"thread": thread, "cancel_event": threading.Event(), "status": "running"}
        thread.start()
        return job_id

    def start_glossary_extract(self, project_dir: str) -> str:
        """后台跑上游 GenDic,从 gt_input 抽取高频专名生成术语字典草稿。

        草稿写 <project>/项目GPT字典-生成.txt(上游默认 gpt.dict 引用之一);
        用户确认后由 glossary/confirm 写入正式 项目GPT字典.txt。
        """
        from . import glossary

        if self.has_active():
            raise PipelineError(
                "E-CACHE-BUSY", "已有任务在运行,请等待完成或取消后再提取术语。"
            )
        project = PatchProject.load(Path(project_dir))
        gt_input = project.subdir("work/gt_project") / "gt_input"
        if not gt_input.is_dir() or not any(gt_input.glob("*.json")):
            raise PipelineError(
                "E-CACHE-EDIT-INVALID",
                "gt_input 为空,请先在补丁工作台跑到「文本提取」步再提取术语。",
            )
        job_id = secrets.token_urlsafe(8)

        def worker() -> None:
            self.emit(
                "job_started",
                {"job_id": job_id, "project_dir": project_dir, "mode": "glossary"},
            )
            try:
                count = glossary.run_gendic(project)
                self.emit(
                    "log",
                    {"job_id": job_id, "step": "GLOSSARY", "message": f"术语草稿生成:{count} 条候选"},
                )
                self.emit("job_done", {"job_id": job_id, "results": {"draft": count}})
            except PipelineError as error:
                self.emit(
                    "job_failed", {"job_id": job_id, "code": error.code, "message": str(error)}
                )
            except Exception as error:  # pragma: no cover
                self.emit("job_failed", {"job_id": job_id, "code": "unknown", "message": str(error)})
            finally:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job:
                        job["status"] = "done"

        thread = threading.Thread(target=worker, daemon=True)
        with self._lock:
            self._jobs[job_id] = {"thread": thread, "cancel_event": threading.Event(), "status": "running"}
        thread.start()
        return job_id


MANAGER = PipelineManager()


# ---------------------------------------------------------------- HTTP 工具
def _apply_cors(handler) -> None:
    """标记响应的 CORS 形态:GUI 白名单 Origin 回显;外来 Origin 抑制(浏览器拒读)。

    头部注入由上游 end_headers 统一按 handler._gt_cors_origin 处理(server.py 受控改造)。
    """
    origin = (handler.headers.get("Origin") or "").strip()
    if origin and origin_allowed(handler):
        handler._gt_cors_origin = origin
    else:
        handler._gt_cors_origin = ""


def _send_json(handler, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    _apply_cors(handler)
    handler.end_headers()
    handler.wfile.write(body)


def handle_pipeline_options(handler) -> None:
    """预检:CORS 头由 end_headers 按 handler._gt_cors_origin 统一注入。"""
    if not host_allowed(handler):
        _reject_host(handler)
        return
    origin = (handler.headers.get("Origin") or "").strip()
    handler._gt_cors_origin = origin if origin and origin_allowed(handler) else ""
    handler.send_response(HTTPStatus.NO_CONTENT)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def _read_body_json(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8")) or {}


# ---------------------------------------------------------------- 路由处理
def handle_pipeline_get(handler, registry) -> None:
    if not host_allowed(handler):
        _reject_host(handler)
        return
    import urllib.parse

    parsed = urllib.parse.urlparse(handler.path)
    path = parsed.path
    query = urllib.parse.parse_qs(parsed.query)

    if path == "/api/pipeline/token":
        # GUI 专用:仅放行 Host 白名单 + GUI Origin;外来网页既过不了 Origin,
        # 也读不到响应(CORS 收敛);本地恶意进程本就与用户同权,不构成增量风险
        if not origin_allowed(handler):
            _reject_host(handler)
            return
        _send_json(handler, {"token": _TOKEN})
        return

    if path == "/api/pipeline/profiles":
        from .detect import detect_engine

        profiles = load_profiles(_profiles_dir())
        payload = []
        for name, profile in profiles.items():
            payload.append(
                {
                    "profile": name,
                    "capability": profile.capability,
                    "translator_mode": profile.translator_mode,
                }
            )
        _send_json(handler, {"profiles": payload})
        return

    if path == "/api/pipeline/tools":
        from .toolbox import TOOL_SPECS, ToolBox

        box = ToolBox()
        dirs = box.candidate_dirs()
        tools = []
        for name, spec in TOOL_SPECS.items():
            found = ""
            for directory in dirs:
                candidate = directory / spec.exe
                if candidate.is_file():
                    found = str(candidate)
                    break
            if not found and shutil.which(spec.exe):
                found = shutil.which(spec.exe) or ""
            tools.append(
                {
                    "name": name,
                    "file": spec.exe,
                    "found": bool(found),
                    "path": found,
                    "bundled": name in ("msg-tool", "xp3pack", "unity_tool"),
                }
            )
        _send_json(handler, {"dirs": [str(d) for d in dirs], "tools": tools})
        return

    if path == "/api/pipeline/events":
        ticket = (query.get("ticket") or [""])[0]
        if not consume_sse_ticket(ticket):
            _send_json(
                handler,
                {"error": "E-AUTH-UNAUTHORIZED", "detail": "invalid or expired sse ticket"},
                status=HTTPStatus.UNAUTHORIZED,
            )
            return
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        _apply_cors(handler)
        handler.end_headers()
        q = MANAGER.subscribe()
        try:
            handler.wfile.write(b"event: hello\ndata: {}\n\n")
            handler.wfile.flush()
            while True:
                try:
                    item = q.get(timeout=15)
                except queue.Empty:
                    handler.wfile.write(b": keepalive\n\n")
                    handler.wfile.flush()
                    continue
                frame = f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
                handler.wfile.write(frame.encode("utf-8"))
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            MANAGER.unsubscribe(q)
        return

    if path == "/api/pipeline/status":
        if not _require_token(handler):
            return
        project_dir = (query.get("project_dir") or [""])[0]
        try:
            project = PatchProject.load(Path(project_dir))
        except (FileNotFoundError, PipelineError) as error:
            _send_json(handler, {"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        _send_json(
            handler,
            {
                "project_dir": str(project.project_dir),
                "game_dir": str(project.game_dir),
                "profile": project.profile_name,
                "schema_version": project.data.get("schema_version"),
                "steps": project.data.get("steps", {}),
            },
        )
        return

    if path == "/api/pipeline/cache":
        from . import cache_editor

        project_dir = (query.get("project") or [""])[0]
        try:
            project = PatchProject.load(Path(project_dir))
        except (FileNotFoundError, PipelineError) as error:
            _send_json(handler, {"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        files = cache_editor.list_cache_files(project)
        _send_json(handler, {"files": files, "editable": not MANAGER.has_active()})
        return

    if path == "/api/pipeline/cache/entries":
        from . import cache_editor

        project_dir = (query.get("project") or [""])[0]
        try:
            project = PatchProject.load(Path(project_dir))
        except (FileNotFoundError, PipelineError) as error:
            _send_json(handler, {"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return

        def _opt_bool(key: str):
            raw = (query.get(key) or [""])[0]
            return raw == "1" if raw in ("0", "1") else None

        try:
            payload = cache_editor.load_entries(
                project,
                name=(query.get("file") or [""])[0],
                query=(query.get("q") or [""])[0],
                locked=_opt_bool("locked"),
                problem=_opt_bool("problem"),
                untranslated=_opt_bool("untranslated"),
                page=int((query.get("page") or ["1"])[0]),
                page_size=int((query.get("page_size") or ["200"])[0]),
            )
        except PipelineError as error:
            _send_json(
                handler, {"error": error.code, "detail": str(error)}, status=HTTPStatus.CONFLICT
            )
            return
        _send_json(handler, payload)
        return

    if path == "/api/pipeline/glossary":
        from . import glossary

        project_dir = (query.get("project") or [""])[0]
        try:
            project = PatchProject.load(Path(project_dir))
        except (FileNotFoundError, PipelineError) as error:
            _send_json(handler, {"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        _send_json(
            handler,
            {
                "draft": glossary.read_draft(project),
                "confirmed": glossary.read_confirmed(project),
                "draft_exists": glossary.draft_path(project).exists(),
            },
        )
        return

    _send_json(handler, {"error": "not found"}, status=HTTPStatus.NOT_FOUND)


def handle_pipeline_post(handler, registry) -> None:
    if not host_allowed(handler):
        _reject_host(handler)
        return
    import urllib.parse

    path = urllib.parse.urlparse(handler.path).path

    # 全部 POST 路由强制 token(FR-G1);SSE 票据 = 持 token 换短时票据
    if not _require_token(handler):
        return

    body = _read_body_json(handler)

    if path == "/api/pipeline/sse-ticket":
        _send_json(handler, {"ticket": issue_sse_ticket()})
        return

    if path == "/api/pipeline/detect":
        from .detect import detect_engine

        game_dir = Path(str(body.get("game_dir", "")))
        if not game_dir.is_dir():
            _send_json(
                handler,
                {"error": "E-PROJECT-GAME-MISSING", "detail": str(game_dir)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        profiles = load_profiles(_profiles_dir())
        _send_json(handler, {"results": detect_engine(game_dir, profiles)})
        return

    if path == "/api/pipeline/projects":
        game_dir = Path(str(body.get("game_dir", "")))
        profile_name = str(body.get("profile", ""))
        overrides = body.get("overrides") or {}
        project_dir = Path(str(body.get("project_dir", "")))
        if not project_dir:
            _send_json(
                handler, {"error": "project_dir required"}, status=HTTPStatus.BAD_REQUEST
            )
            return
        try:
            if (project_dir / "project.yaml").exists():
                # 已有工程 = 幂等打开,不覆盖步骤状态
                project = PatchProject.load(project_dir)
            else:
                project = PatchProject.create(project_dir, game_dir, profile_name, overrides)
        except PipelineError as error:
            _send_json(
                handler, {"error": error.code, "detail": str(error)}, status=HTTPStatus.BAD_REQUEST
            )
            return
        _send_json(
            handler,
            {"project_dir": str(project.project_dir), "profile": project.profile_name},
        )
        return

    if path == "/api/pipeline/run":
        project_dir = str(body.get("project_dir", ""))
        try:
            result = MANAGER.start_run(
                project_dir,
                from_step=str(body.get("from_step", "")),
                only=str(body.get("only", "")),
                api_key=str(body.get("api_key", "")),
                endpoint=str(body.get("endpoint", "")),
                model=str(body.get("model", "")),
                allow_queue=bool(body.get("queue", False)),
            )
        except FileNotFoundError as error:
            _send_json(handler, {"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        except PipelineError as error:
            _send_json(
                handler, {"error": error.code, "detail": str(error)}, status=HTTPStatus.BAD_REQUEST
            )
            return
        _send_json(handler, result)
        return

    if path == "/api/pipeline/cancel":
        ok = MANAGER.cancel(str(body.get("job_id", "")))
        _send_json(handler, {"success": ok})
        return

    if path == "/api/pipeline/cache/entry":
        from . import cache_editor

        if MANAGER.has_active():
            _send_json(
                handler,
                {"error": "E-CACHE-BUSY", "detail": "任务运行中,缓存暂不可写"},
                status=HTTPStatus.CONFLICT,
            )
            return
        project_dir = Path(str(body.get("project", "")))
        try:
            project = PatchProject.load(project_dir)
            result = cache_editor.update_entry(
                project,
                name=str(body.get("file", "")),
                index=int(body.get("index", -1)),
                pre_src=body.get("pre_src"),
                pre_dst=body.get("pre_dst"),
                locked=body.get("locked"),
            )
        except PipelineError as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code == "E-CACHE-BUSY"
                else HTTPStatus.BAD_REQUEST
            )
            _send_json(handler, {"error": error.code, "detail": str(error)}, status=status)
            return
        _send_json(handler, result)
        return

    if path == "/api/pipeline/cache/compact":
        from . import cache_editor

        if MANAGER.has_active():
            _send_json(
                handler,
                {"error": "E-CACHE-BUSY", "detail": "任务运行中,暂不能合并日志"},
                status=HTTPStatus.CONFLICT,
            )
            return
        try:
            project = PatchProject.load(Path(str(body.get("project", ""))))
            merged = cache_editor.compact_append_logs(project)
        except PipelineError as error:
            _send_json(handler, {"error": error.code, "detail": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        _send_json(handler, {"merged": merged})
        return

    if path == "/api/pipeline/cache/problem-status":
        from . import cache_editor

        if MANAGER.has_active():
            _send_json(
                handler,
                {"error": "E-CACHE-BUSY", "detail": "任务运行中,暂不能修改问题状态"},
                status=HTTPStatus.CONFLICT,
            )
            return
        try:
            project = PatchProject.load(Path(str(body.get("project", ""))))
            result = cache_editor.set_problem_status(
                project,
                name=str(body.get("file", "")),
                index=int(body.get("index", -1)),
                status=str(body.get("status", "")),
            )
        except PipelineError as error:
            _send_json(handler, {"error": error.code, "detail": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        _send_json(handler, result)
        return

    if path == "/api/pipeline/cache/rebuild":
        project_dir = str(body.get("project", ""))
        try:
            job_id = MANAGER.start_rebuild(project_dir, compact=bool(body.get("compact")))
        except PipelineError as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code == "E-CACHE-BUSY"
                else HTTPStatus.BAD_REQUEST
            )
            _send_json(handler, {"error": error.code, "detail": str(error)}, status=status)
            return
        _send_json(handler, {"job_id": job_id})
        return

    if path == "/api/pipeline/glossary/extract":
        project_dir = str(body.get("project", ""))
        try:
            job_id = MANAGER.start_glossary_extract(project_dir)
        except PipelineError as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code == "E-CACHE-BUSY"
                else HTTPStatus.BAD_REQUEST
            )
            _send_json(handler, {"error": error.code, "detail": str(error)}, status=status)
            return
        _send_json(handler, {"job_id": job_id})
        return

    if path == "/api/pipeline/glossary/confirm":
        from . import glossary

        if MANAGER.has_active():
            _send_json(
                handler,
                {"error": "E-CACHE-BUSY", "detail": "任务运行中,暂不能确认术语"},
                status=HTTPStatus.CONFLICT,
            )
            return
        try:
            project = PatchProject.load(Path(str(body.get("project", ""))))
            count = glossary.confirm_entries(project, body.get("entries") or [])
        except PipelineError as error:
            _send_json(handler, {"error": error.code, "detail": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        _send_json(handler, {"confirmed": count})
        return

    if path == "/api/pipeline/restore":
        project_dir = Path(str(body.get("project_dir", "")))
        try:
            project = PatchProject.load(project_dir)
            profiles = load_profiles(_profiles_dir())
            profile = profiles[project.profile_name].apply_override(project.overrides)
            pipeline = Pipeline(project, profile, toolbox=ToolBox())
            count = pipeline.restore()
        except PipelineError as error:
            _send_json(
                handler, {"error": error.code, "detail": str(error)}, status=HTTPStatus.BAD_REQUEST
            )
            return
        _send_json(handler, {"restored": count})
        return

    _send_json(handler, {"error": "not found"}, status=HTTPStatus.NOT_FOUND)
