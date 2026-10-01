"""Toolbox:外部工具的定位/版本校验(E-EXTRACT-TOOL-MISSING 指引路径)。

查找顺序:GALTRANS_TOOLS_DIR 环境变量 → 仓库 tools/bin → PATH。
SHA-256 校验值已知的工具(见 docs/architecture.md §6)强制校验。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import PipelineError


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ToolSpec:
    name: str
    exe: str
    sha256: str | None = None  # 已知官方构建的锁定校验值;None 表示本地构建/未核实


# 版本锁定基准(2026-09-12 实测,来源见 docs/research/toolchain-verification.md)
TOOL_SPECS: dict[str, ToolSpec] = {
    "msg-tool": ToolSpec(
        "msg-tool",
        "msg_tool.exe",
        sha256="62fe396780468200cb50f287fe213e697a903f0a85a91153fa3692598ae8ab5d",
    ),
    "xp3pack": ToolSpec(
        "xp3pack",
        "Xp3Pack.exe",
        sha256="c6f4a6f4d74cd293777cd321cefdeae88ff4dd344c4f2a0bcbc88840aa717cb6",
    ),
    "xp3brute": ToolSpec("xp3brute", "xp3brute.exe", sha256=None),
    "sextractor": ToolSpec("sextractor", "run.py", sha256=None),
    # 本仓库自有 Python 工具(M5);由 _run_tool 以当前解释器驱动,无需校验和
    "unity_tool": ToolSpec("unity_tool", "unity_tool.py", sha256=None),
}


class ToolBox:
    """工具定位:按优先级搜索多个目录,而非单一目录。

    目录优先级:显式传入(--tools-dir)→ GALTRANS_TOOLS_DIR 环境变量 →
    用户级目录 %APPDATA%/GalTranslSuite/tools(安装版无需管理员即可放
    xp3brute 等自备工具)→ 冻结态安装根 tools/bin → 仓库 tools/bin(开发态)。
    同名冲突时靠前目录获胜(用户目录可覆盖随包版本);全部目录逐个搜索,
    因此工具分散在多个目录也能各自找到。
    """

    USER_TOOLS_DIRNAME = ("GalTranslSuite", "tools")

    def __init__(self, tools_dir: Path | None = None) -> None:
        self.explicit_dir = tools_dir

    def candidate_dirs(self) -> list[Path]:
        dirs: list[Path] = []

        def _add(path: Path) -> None:
            resolved = path.resolve()
            if resolved not in dirs:
                dirs.append(resolved)

        if self.explicit_dir is not None:
            _add(self.explicit_dir)
        env_dir = os.environ.get("GALTRANS_TOOLS_DIR")
        if env_dir:
            _add(Path(env_dir))
        appdata = os.environ.get("APPDATA")
        if appdata:
            _add(Path(appdata).joinpath(*self.USER_TOOLS_DIRNAME))
        if getattr(sys, "frozen", False):
            _add(Path(sys.executable).resolve().parent.parent / "tools" / "bin")
        probe = Path.cwd().resolve()
        for candidate in [probe, *probe.parents]:
            _add(candidate / "tools" / "bin")
        return [d for d in dirs if d.is_dir()]

    def locate(self, tool_name: str) -> Path:
        spec = TOOL_SPECS.get(tool_name)
        if spec is None:
            raise PipelineError("E-EXTRACT-TOOL-MISSING", f"未知工具: {tool_name}")
        candidates: list[Path] = []
        for directory in self.candidate_dirs():
            candidates.append(directory / spec.exe)
        path_env = shutil.which(spec.exe)
        if path_env:
            candidates.append(Path(path_env))
        for candidate in candidates:
            if candidate.is_file():
                self._verify(spec, candidate)
                return candidate
        searched = [str(d) for d in self.candidate_dirs()] or ["(无可用目录)"]
        raise PipelineError(
            "E-EXTRACT-TOOL-MISSING",
            f"找不到 {tool_name}({spec.exe});已搜索目录: {'; '.join(searched)}"
            f"。可将工具放入以上任一目录(推荐用户目录 %APPDATA%\\GalTranslSuite\\tools)",
        )

    @staticmethod
    def _verify(spec: ToolSpec, path: Path) -> None:
        if spec.sha256 is None:
            return
        actual = _sha256(path)
        if actual != spec.sha256:
            raise PipelineError(
                "E-EXTRACT-TOOL-MISSING",
                f"{spec.exe} SHA-256 不匹配(期望 {spec.sha256[:16]}…,实际 {actual[:16]}…);"
                "请使用托管表锁定的版本",
            )
