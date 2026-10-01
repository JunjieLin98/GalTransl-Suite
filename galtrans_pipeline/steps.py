"""流水线各步骤实现(架构 §3.3 状态机的执行体)。

每步幂等:产物落盘 workspace,重跑覆盖自身产物;步骤前置条件不满足时抛 PipelineError。
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

from .errors import PipelineError
from .jobs import Job
from .profile import EngineProfile
from .project import PatchProject
from .runner import ProcessRunner
from .toolbox import ToolBox

TRANSLATION_NOTICE = """====================================
 AI 翻译补丁 / AI-Generated Translation
====================================
本补丁文本由大语言模型(LLM)自动翻译生成,未经完整人工审校。
可能存在误译、漏译与风格不一致,请酌情使用。

This patch was machine-translated by an LLM and has NOT been
fully human-reviewed. Use at your own discretion.
"""

THIRD_PARTY_NOTICE = """THIRD-PARTY LICENSES / 第三方组件许可
====================================
本补丁若包含以下组件,其版权与许可归属原作者,随补丁一并分发:

* version.dll (KirikiriTools - KirikiriUnencryptedArchive)
  Copyright (c) arcusmaximus — MIT License
  https://github.com/arcusmaximus/KirikiriTools
  本组件仅用于让 Kirikiri 引擎加载未加密补丁归档,不含任何游戏资源。

使用本补丁即表示你已知晓上述组件随附分发。若原作者要求停止分发,请停止传播本补丁。
"""

INSTALL_README = """安装说明 / How to install
====================================
1. 将本目录中的补丁文件(patch*.xp3 等)复制到游戏根目录(与游戏 exe 同级)。
2. 若本补丁包含 version.dll:一并复制到游戏根目录;若杀毒软件报警,
   请将游戏目录加入白名单(该文件为 KirikiriTools 免封包加载组件,见
   LICENSE-THIRD-PARTY.txt)。
3. 已有同名补丁(如 patch.xp3)时,本补丁按 patch2/patch3… 递增命名,
   引擎会全部加载,无需删除旧补丁。
4. 卸载:删除本补丁文件即可,不影响游戏本体。

本补丁由 AI 翻译生成,未经完整人工审校,详见 TRANSLATION_NOTICE。
"""


def _render_args(template: str, mapping: dict[str, Any]) -> list[str]:
    """参数模板展开:先按空格分词、再逐词替换占位符——含空格的值不会被切断。"""
    return [token.format(**mapping) for token in template.split()]


class StepExecutor:
    """步骤执行器:持有工程/profile/工具箱/运行器,方法即步骤。"""

    def __init__(
        self,
        project: PatchProject,
        profile: EngineProfile,
        toolbox: ToolBox,
        runner: ProcessRunner,
        cancel_event: threading.Event | None = None,
        progress=None,
    ) -> None:
        self.project = project
        self.profile = profile
        self.toolbox = toolbox
        self.runner = runner
        self.cancel_event = cancel_event or threading.Event()
        self.progress = progress or (lambda step, message: None)

    # ------------------------------------------------------------------
    def _tool_path(self, name: str) -> Path:
        # 允许直接传已定位的工具路径(如 package 步骤先定位再渲染参数)
        candidate = Path(name)
        if candidate.is_file():
            return candidate
        return self.toolbox.locate(name)

    def _run_tool(self, step_name: str, tool_name: str, args: list[str]):
        tool = self._tool_path(tool_name)
        # Python 脚本工具(unity_tool/sextractor)用当前解释器驱动
        argv = ([sys.executable, str(tool)] if tool.suffix == ".py" else [str(tool)]) + args
        self.progress(step_name, "$ " + " ".join(argv))
        result = self.runner.run(argv, cancel_event=self.cancel_event)
        if getattr(result, "cancelled", False):
            raise PipelineError("E-TOOL-PROCESS-CRASH", f"{step_name} 被用户取消")
        if result.returncode != 0:
            raise PipelineError(
                "E-TOOL-PROCESS-CRASH",
                f"{step_name} 退出码 {result.returncode}\n{result.output_tail}",
            )
        return result

    # ------------------------------------------------------------------
    # DETECT
    # ------------------------------------------------------------------
    def step_detect(self) -> list[dict[str, Any]]:
        from .detect import detect_engine
        from .profile import default_profiles_dir, load_profiles

        profiles = load_profiles(self._profiles_dir())
        results = detect_engine(self.project.game_dir, profiles)
        self.project.data["detect"] = results
        self.project.save()
        self.progress("DETECT", f"检出 {len(results)} 个候选")
        return results

    def _profiles_dir(self) -> Path:
        from .profile import default_profiles_dir

        override = self.project.data.get("profiles_dir")
        return Path(override) if override else default_profiles_dir()

    # ------------------------------------------------------------------
    # UNPACK
    # ------------------------------------------------------------------
    @staticmethod
    def _count_files(directory: Path) -> int:
        return sum(1 for item in directory.rglob("*") if item.is_file())

    def _unpack_encrypted_fallback(self, spec: dict[str, Any], archive: Path, out_dir: Path) -> int:
        """解包工具退出码 0 但无产物(强加密封包的典型形态)→ 回退用户自备解密工具。"""
        self.progress("UNPACK", f"{archive.name} 解包后无产物,疑似强加密封包")
        fallback = spec.get("encrypted_fallback") or {}
        tool = str(fallback.get("tool", "")).strip()
        args_tmpl = str(fallback.get("args", "")).strip()
        if not tool or not args_tmpl:
            raise PipelineError(
                "E-UNPACK-ENCRYPTED-XP3",
                f"{archive.name} 解包后无产物(疑似强加密),且 profile 未配置 encrypted_fallback",
            )
        args = _render_args(args_tmpl, {"archive": str(archive), "out_dir": str(out_dir)})
        try:
            self._run_tool("UNPACK", tool, args)
        except PipelineError as error:
            if error.code == "E-EXTRACT-TOOL-MISSING":
                raise PipelineError(
                    "E-UNPACK-ENCRYPTED-XP3",
                    f"{archive.name} 疑似强加密(msg-tool 无产物),回退解密工具不可用:{error}",
                ) from error
            raise
        count = self._count_files(out_dir)
        if count == 0:
            raise PipelineError(
                "E-UNPACK-ENCRYPTED-XP3",
                f"{archive.name} 回退解密后仍无产物",
            )
        self.progress("UNPACK", f"{archive.name} 回退解密成功({count} 文件)")
        return 1

    def step_unpack(self) -> int:
        spec = self.profile.step("unpack")
        tool = spec.get("tool", "msg-tool")
        unpacked = self.project.subdir("work/unpacked")
        total = 0
        matched_any = False
        for pattern in self.profile.unpack_archives():
            archives = sorted(self.project.game_dir.glob(pattern))
            if not archives:
                continue
            matched_any = True
            for archive in archives:
                stem = archive.stem or archive.name
                out_dir = unpacked / stem
                out_dir.mkdir(parents=True, exist_ok=True)
                self.progress("UNPACK", f"{archive.name} → {out_dir.name}/")
                args = _render_args(
                    spec["args"], {"archive": str(archive), "out_dir": str(out_dir)}
                )
                self._run_tool("UNPACK", tool, args)
                if self._count_files(out_dir) > 0:
                    total += 1
                else:
                    # 退出码 0 但零产物:加密封包下 msg-tool 的行为;工作目录里
                    # 若已有手动解包产物则视为成功,不覆盖
                    total += self._unpack_encrypted_fallback(spec, archive, out_dir)
        if total == 0:
            if not matched_any:
                raise PipelineError(
                    "E-UNPACK-NO-ARCHIVE",
                    f"游戏目录中未找到匹配 unpack.archives 的封包: {self.profile.unpack_archives()}",
                )
            raise PipelineError(
                "E-UNPACK-ENCRYPTED-XP3",
                "全部封包解包后均无产物,回退解密(若已配置)后仍失败",
            )
        return total

    # ------------------------------------------------------------------
    # EXTRACT
    # ------------------------------------------------------------------
    @staticmethod
    def _flat_name(relative: Path) -> str:
        parts = [part for part in relative.parts if part not in (";",)]
        return "__".join(parts) + ".json"

    @staticmethod
    def _magic_ok(path: Path, magic_filter: str) -> bool:
        if not magic_filter:
            return True
        with open(path, "rb") as handle:
            head = handle.read(8)
        return magic_filter.encode("ascii", errors="ignore") in head

    def step_extract(self) -> int:
        spec = self.profile.step("extract")
        tool = spec.get("tool", "msg-tool")
        unpacked = self.project.subdir("work/unpacked")
        extracted = self.project.subdir("work/extracted")
        input_glob = spec.get("input_glob", "**/*")
        magic_filter = str(spec.get("magic_filter", "") or "")
        encoding_arg = spec.get("encoding", "")
        manifest: dict[str, str] = {}
        count = 0
        for source in sorted(unpacked.glob(input_glob)):
            if not source.is_file():
                continue
            if not self._magic_ok(source, magic_filter):
                continue
            relative = source.relative_to(unpacked)
            json_name = self._flat_name(relative)
            out_file = extracted / json_name
            args = _render_args(
                spec["args"], {"input": str(source), "out_file": str(out_file)}
            )
            if encoding_arg:
                args = [arg for arg in args]  # 编码旗标已在模板中
            self.progress("EXTRACT", f"{relative}")
            self._run_tool("EXTRACT", tool, args)
            if not out_file.is_file():
                # 工具成功但无输出 = 该脚本无可翻译文本(如场景选择占位),跳过
                self.progress("EXTRACT", f"跳过(无可翻译文本): {relative}")
                continue
            manifest[json_name] = relative.as_posix()
            count += 1
        if count == 0:
            raise PipelineError(
                "E-EXTRACT-NO-SCRIPT",
                f"input_glob={input_glob!r} 未命中任何脚本文件",
            )
        (extracted / "_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return count

    # ------------------------------------------------------------------
    # TRANSLATE(复用上游翻译核心,算法层零侵入)
    # ------------------------------------------------------------------
    def step_translate(self, api_key: str = "", endpoint: str = "", model: str = "") -> int:
        # 先导入上游(带 sys.path 回退):配置模板必须来自上游本体
        try:
            from GalTransl.DefaultProjectConfig import DEFAULT_PROJECT_CONFIG_YAML
            from GalTransl.Service import JobSpec, run_job
        except ImportError:
            # fork 仓库形态:上游包在仓库根,安装形态下需手动补 sys.path
            import sys as _sys

            repo_root = str(Path(__file__).resolve().parent.parent)
            if repo_root not in _sys.path:
                _sys.path.insert(0, repo_root)
            try:
                from GalTransl.DefaultProjectConfig import (  # noqa: F811
                    DEFAULT_PROJECT_CONFIG_YAML,
                )
                from GalTransl.Service import JobSpec, run_job  # noqa: F811
            except ImportError as error:  # pragma: no cover - 打包形态兜底
                raise PipelineError(
                    "E-TOOL-PROCESS-CRASH", f"上游 GalTransl 不可导入: {error}"
                )

        gt_dir = self.project.subdir("work/gt_project")
        gt_input = gt_dir / "gt_input"
        gt_output = gt_dir / "gt_output"
        gt_input.mkdir(parents=True, exist_ok=True)
        gt_output.mkdir(exist_ok=True)
        extracted = self.project.subdir("work/extracted")
        sources = sorted(
            path for path in extracted.glob("*.json") if path.name != "_manifest.json"
        )
        if not sources:
            raise PipelineError("E-EXTRACT-NO-SCRIPT", "没有可翻译的提取产物")
        for source in sources:
            shutil.copy2(source, gt_input / source.name)

        self._write_gt_config(
            gt_dir / "config.yaml",
            api_key,
            endpoint,
            model,
            template=DEFAULT_PROJECT_CONFIG_YAML,
        )

        self.progress(
            "TRANSLATE",
            f"调用上游 GalTransl({self.profile.translator_mode}),"
            f"{len(sources)} 个文件;断点续翻由上游缓存兜底",
        )
        spec = JobSpec(
            project_dir=str(gt_dir), translator=self.profile.translator_mode
        )
        state = run_job(spec, stop_event=self.cancel_event)
        outputs = [
            path for path in gt_output.glob("*.json") if path.stat().st_size > 2
        ]
        if len(outputs) < len(sources):
            missing = {p.name for p in sources} - {p.name for p in outputs}
            raise PipelineError(
                "E-TRANSLATE-OUTPUT-MISSING",
                f"缺少 {len(missing)} 个输出: {sorted(missing)[:5]}…"
                f"(state.error={state.error!r})",
            )
        translated = self.project.subdir("work/translated")
        for path in outputs:
            shutil.copy2(path, translated / path.name)
        return len(outputs)

    def _write_gt_config(
        self,
        config_path: Path,
        api_key: str,
        endpoint: str,
        model: str,
        template: str = "",
    ) -> None:
        import os

        api_key = api_key or os.environ.get("GALTRANS_API_KEY", "")
        endpoint = endpoint or os.environ.get("GALTRANS_ENDPOINT", "")
        model = model or os.environ.get("GALTRANS_MODEL", "")

        fresh = not config_path.exists()
        if fresh:
            text = template or (
                "backendSpecific:\n"
                "  OpenAI-Compatible:\n"
                "    tokens:\n"
                "      - token: sk-placeholder\n"
                "        endpoint: https://api.deepseek.com\n"
                "        modelName: deepseek-chat\n"
                "    tokenStrategy: \"random\"\n"
                "    checkAvailable: true\n"
                "plugin:\n"
                "  filePlugin: file_galtransl_json\n"
            )
        else:
            # 保留用户手工编辑过的配置,仅做占位符替换
            text = config_path.read_text(encoding="utf-8")

        if api_key:
            for placeholder in ("sk-example-key1", "sk-example-key2", "sk-placeholder"):
                text = text.replace(placeholder, api_key)
            if fresh and endpoint:
                text = text.replace("      - token: " + api_key,
                                    f"      - token: {api_key}\n"
                                    f"        endpoint: {endpoint}"
                                    + (f"\n        modelName: {model}" if model else ""))
        if "sk-example" in text or "sk-placeholder" in text:
            # 先落盘模板,让用户可以直接编辑(错误指引指向该文件)
            config_path.write_text(text, encoding="utf-8")
            raise PipelineError(
                "E-TRANSLATE-API-AUTH",
                "未提供 API key(用 --api-key 或环境变量 GALTRANS_API_KEY,"
                f"或直接编辑 {config_path})",
            )
        config_path.write_text(text, encoding="utf-8")

    # ------------------------------------------------------------------
    # INJECT
    # ------------------------------------------------------------------
    def step_inject(self) -> int:
        spec = self.profile.step("inject")
        tool = spec.get("tool", "msg-tool")
        unpacked = self.project.subdir("work/unpacked")
        translated = self.project.subdir("work/translated")
        injected = self.project.subdir("work/injected")
        manifest_path = self.project.subdir("work/extracted") / "_manifest.json"
        if not manifest_path.is_file():
            raise PipelineError("E-INJECT-IMPORT-FAIL", "缺少提取清单 _manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        count = 0
        for json_name, original_rel in sorted(manifest.items()):
            translated_file = translated / json_name
            if not translated_file.is_file():
                continue  # 翻译缺失的文件在 TRANSLATE 步已报错;此处容错跳过
            original = unpacked / original_rel
            patched = injected / original_rel
            patched.parent.mkdir(parents=True, exist_ok=True)
            args = _render_args(
                spec["args"],
                {
                    "input": str(original),
                    "trans_file": str(translated_file),
                    "patched": str(patched),
                },
            )
            self.progress("INJECT", f"{original_rel}")
            self._run_tool("INJECT", tool, args)
            count += 1
        if count == 0:
            raise PipelineError("E-INJECT-IMPORT-FAIL", "没有可注入的译文")
        return count

    # ------------------------------------------------------------------
    # PACKAGE
    # ------------------------------------------------------------------
    def step_package(self) -> int:
        spec = self.profile.step("package")
        strategy = spec.get("strategy", "patch_xp3")
        dist = self.project.subdir("dist")
        injected = self.project.subdir("work/injected")
        if strategy == "patch_xp3":
            return self._package_patch_xp3(spec, dist, injected)
        if strategy == "repack_archive":
            return self._package_repack_archive(spec, dist, injected)
        if strategy == "deploy":
            return self._package_deploy(spec)
        raise PipelineError(
            "E-INVALID-PROFILE", f"未知 package.strategy: {strategy}"
        )

    def _package_deploy(self, spec: dict[str, Any]) -> int:
        """Unity deploy 策略(FR-C7,实验性):强制备份后替换游戏目录文件。

        injected/ 下每个文件按文件名在游戏目录递归定位唯一原件:
        复制到 backup/(保留相对路径,供 restore 通用步骤还原)后再替换。
        找不到或多处同名 → 报错不落盘(避免误写)。
        """
        game_dir = self.project.check_game_dir()
        backup = self.project.project_dir / "backup"
        injected = self.project.subdir("work/injected")
        files = [p for p in injected.rglob("*") if p.is_file()]
        if not files:
            raise PipelineError(
                "E-TRANSLATE-OUTPUT-MISSING", "injected/ 为空,没有可部署的产物"
            )
        count = 0
        for src in files:
            matches = [p for p in game_dir.rglob(src.name) if p.is_file()]
            if len(matches) != 1:
                raise PipelineError(
                    "E-UNITY-DEPLOY-AMBIGUOUS",
                    f"{src.name} 在游戏目录匹配到 {len(matches)} 个文件"
                    f"(需恰好 1 个);请在 per-game override 中用完整相对路径声明 archive",
                )
            target = matches[0]
            rel = target.relative_to(game_dir)
            backup_path = backup / rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if not backup_path.exists():
                shutil.copy2(target, backup_path)
            shutil.copy2(src, target)
            self.progress("PACKAGE", f"已部署 {rel}(原文件已备份)")
            count += 1
        self._write_dist_docs(self.project.subdir("dist"))
        return count

    def _patch_name(self, base: str = "patch") -> str:
        """游戏已有同名补丁时递增命名(FR-C4:patch2 分支)。"""
        name = base
        index = 1
        while (self.project.game_dir / f"{name}.xp3").is_file():
            index += 1
            name = f"{base}{index}"
        return name

    def _write_dist_docs(self, target_dir: Path) -> None:
        (target_dir / "TRANSLATION_NOTICE").write_text(
            TRANSLATION_NOTICE, encoding="utf-8"
        )
        (target_dir / "LICENSE-THIRD-PARTY.txt").write_text(
            THIRD_PARTY_NOTICE, encoding="utf-8"
        )
        (target_dir / "安装说明.txt").write_text(INSTALL_README, encoding="utf-8")

    def _package_patch_xp3(self, spec: dict[str, Any], dist: Path, injected: Path) -> int:
        files = [path for path in injected.rglob("*") if path.is_file()]
        if not files:
            raise PipelineError("E-INJECT-IMPORT-FAIL", "没有可打包的注入产物")
        package_dir = self.project.subdir("work/package")
        name = self._patch_name("patch")
        patch_folder = package_dir / name
        if patch_folder.exists():
            shutil.rmtree(patch_folder)
        patch_folder.mkdir(parents=True)
        for source in files:
            target = patch_folder / source.relative_to(injected)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        self._write_dist_docs(patch_folder)
        tool = self._tool_path(spec.get("tool", "xp3pack"))
        self.progress("PACKAGE", f"Xp3Pack {name}/ → {name}.xp3")
        result = self.runner.run([str(tool), name], cwd=str(package_dir))
        if result.returncode != 0:
            raise PipelineError(
                "E-TOOL-PROCESS-CRASH",
                f"Xp3Pack 失败({result.returncode})\n{result.output_tail}",
            )
        produced = package_dir / f"{name}.xp3"
        if not produced.is_file():
            raise PipelineError(
                "E-TOOL-PROCESS-CRASH", f"Xp3Pack 未产出 {name}.xp3"
            )
        shutil.move(str(produced), dist / f"{name}.xp3")
        self._write_dist_docs(dist)
        self.progress("PACKAGE", f"产物 → dist/{name}.xp3")
        return 1

    def _package_repack_archive(
        self, spec: dict[str, Any], dist: Path, injected: Path
    ) -> int:
        archive_name = str(spec.get("archive", "patch.ypf"))
        tool = self._tool_path(spec.get("tool", "msg-tool"))
        staging = self.project.subdir("work/package") / "archive_src"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        count = 0
        for source in injected.rglob("*"):
            if not source.is_file():
                continue
            target = staging / source.relative_to(injected)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            count += 1
        if count == 0:
            raise PipelineError("E-INJECT-IMPORT-FAIL", "没有可打包的注入产物")
        args = _render_args(
            spec.get("pack_args", "pack -t yuris-ypf {src_dir} {out_xp3}"),
            {"src_dir": str(staging), "out_xp3": str(dist / archive_name)},
        )
        self.progress("PACKAGE", f"repack → {archive_name}")
        self._run_tool("PACKAGE", str(tool), args)
        if not (dist / archive_name).is_file():
            raise PipelineError(
                "E-TOOL-PROCESS-CRASH", f"打包失败: {archive_name} 未产出"
            )
        self._write_dist_docs(dist)
        return 1

    # ------------------------------------------------------------------
    # RESTORE(运维操作,非流水线步骤;架构 §3.3)
    # ------------------------------------------------------------------
    def step_restore(self) -> int:
        backup = self.project.project_dir / "backup"
        if not backup.is_dir() or not any(backup.rglob("*")):
            raise PipelineError("E-RESTORE-NO-BACKUP", f"{backup} 为空")
        game_dir = self.project.check_game_dir()
        count = 0
        for source in backup.rglob("*"):
            if not source.is_file():
                continue
            target = game_dir / source.relative_to(backup)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            count += 1
        self.progress("RESTORE", f"已还原 {count} 个文件")
        return count
