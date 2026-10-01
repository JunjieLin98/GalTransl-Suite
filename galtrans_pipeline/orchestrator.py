"""Orchestrator:流水线状态机(架构 §3.3)。

幂等步骤执行、断点重跑(--from-step)、能力等级裁剪(L3/L4 终止于 TRANSLATE)。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from . import STEPS
from .errors import PipelineError
from .jobs import JobRegistry
from .profile import EngineProfile
from .project import PatchProject
from .runner import ProcessRunner
from .steps import StepExecutor
from .toolbox import ToolBox


class Pipeline:
    def __init__(
        self,
        project: PatchProject,
        profile: EngineProfile,
        toolbox: ToolBox | None = None,
        runner: ProcessRunner | None = None,
    ) -> None:
        self.project = project
        self.profile = profile
        self.toolbox = toolbox or ToolBox()
        self.runner = runner or ProcessRunner()
        self.registry = JobRegistry(runner=self.runner)

    def executor(self, cancel_event: threading.Event | None = None) -> StepExecutor:
        return StepExecutor(
            self.project,
            self.profile,
            self.toolbox,
            self.runner,
            cancel_event=cancel_event,
            progress=self.registry.progress,
        )

    def run(
        self,
        from_step: str | None = None,
        only: list[str] | None = None,
        api_key: str = "",
        endpoint: str = "",
        model: str = "",
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """执行流水线;from_step 跳过其前步骤;only 指定单步(重跑)。"""
        plan = list(STEPS)
        if from_step:
            if from_step not in STEPS:
                raise PipelineError(
                    "E-INVALID-PROFILE", f"未知步骤: {from_step}(合法: {STEPS})"
                )
            plan = plan[STEPS.index(from_step):]
        if only:
            plan = [step for step in plan if step in only]

        results: dict[str, Any] = {}
        executor = self.executor(cancel_event)
        for step in plan:
            if self.profile.capability in ("L3", "L4") and step in ("INJECT", "PACKAGE"):
                self.registry.progress(
                    step, f"能力等级 {self.profile.capability}:流水线终止于 TRANSLATE"
                )
                break
            if self.profile.capability == "L4" and step in ("UNPACK", "EXTRACT"):
                self.registry.progress(step, f"能力等级 L4:跳过 {step}")
                continue
            if step == "DETECT":
                job = self.registry.register(step)
                outcome = executor.step_detect()
                self.registry.finish(job)
                self.project.mark_step(step, "done", f"候选 {len(outcome)}")
                results[step] = outcome
                continue

            if step == "UNPACK" and not self.profile.unpack_archives():
                self.registry.progress(step, "profile 未声明 unpack.archives,跳过")
                continue

            job = self.registry.register(step)
            try:
                if step == "UNPACK":
                    outcome = executor.step_unpack()
                elif step == "EXTRACT":
                    outcome = executor.step_extract()
                elif step == "TRANSLATE":
                    outcome = executor.step_translate(api_key, endpoint, model)
                elif step == "INJECT":
                    outcome = executor.step_inject()
                elif step == "PACKAGE":
                    outcome = executor.step_package()
                else:  # pragma: no cover
                    continue
            except PipelineError as error:
                self.registry.finish(job, status="failed")
                self.project.mark_step(step, "failed", error.code)
                raise
            self.registry.finish(job)
            self.project.mark_step(step, "done", str(outcome))
            results[step] = outcome
        return results

    def restore(self) -> int:
        """运维操作:backup/ → 游戏目录;要求无活跃 Job(架构 §3.3)。"""
        running = self.registry.running_jobs()
        if running:
            raise PipelineError(
                "E-CACHE-BUSY", f"存在活跃任务 {running[0].id},先取消再 restore"
            )
        self.registry.progress("RESTORE", "开始还原备份")
        count = self.executor().step_restore()
        # 注入/打包产物因还原失效,状态重置
        for step in ("INJECT", "PACKAGE"):
            if self.project.step_status(step):
                self.project.reset_step(step)
        return count
