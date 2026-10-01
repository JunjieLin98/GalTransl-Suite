"""JobRegistry:统一注册流水线步骤任务,承载取消事件与进度回调(架构 §3.5)。"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Job:
    id: str
    step: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    status: str = "running"

    def request_cancel(self) -> None:
        self.cancel_event.set()


ProgressCallback = Callable[[str, str], None]  # (step, message)


class JobRegistry:
    """CLI 形态的轻量注册表;GUI 形态(M2)在同一接口上接 SSE 广播。"""

    def __init__(self, runner=None) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.runner = runner
        self._progress_callbacks: list[ProgressCallback] = []

    def add_progress_callback(self, callback: ProgressCallback) -> None:
        self._progress_callbacks.append(callback)

    def progress(self, step: str, message: str) -> None:
        for callback in self._progress_callbacks:
            try:
                callback(step, message)
            except Exception:
                pass

    def register(self, step: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], step=step)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def finish(self, job: Job, status: str = "done") -> None:
        job.status = status

    def running_jobs(self) -> list[Job]:
        with self._lock:
            return [job for job in self._jobs.values() if job.status == "running"]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return False
        job.request_cancel()
        if self.runner is not None:
            self.runner.cancel_all()
        return True

    def cancel_active(self) -> int:
        with self._lock:
            jobs = list(self._jobs.values())
        count = 0
        for job in jobs:
            if job.status == "running":
                job.request_cancel()
                count += 1
        if count and self.runner is not None:
            self.runner.cancel_all()
        return count
