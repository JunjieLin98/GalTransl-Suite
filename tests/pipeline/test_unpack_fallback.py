"""UNPACK 空产物回退测试:强加密 xp3 → 用户自备解密工具(需求 FR-B2/加密指引)。

msg-tool 对不支持的强加密 xp3 退出码为 0 但零产物;编排层须识别该形态,
按 profile 的 encrypted_fallback 回退用户自备解密工具,缺失时给出
E-UNPACK-ENCRYPTED-XP3 可行动指引。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from galtrans_pipeline.errors import PipelineError
from galtrans_pipeline.orchestrator import Pipeline
from galtrans_pipeline.profile import EngineProfile
from galtrans_pipeline.project import PatchProject


def _profile(with_fallback: bool = True) -> EngineProfile:
    unpack: dict = {
        "tool": "fake_msg",
        "args": "unpack -t kirikiri-xp3 {archive} {out_dir}",
        "archives": ["*.xp3"],
    }
    if with_fallback:
        unpack["encrypted_fallback"] = {
            "tool": "fake_brute",
            "args": "unpack {archive} {out_dir}",
        }
    return EngineProfile(
        {
            "profile": "fake",
            "capability": "L1",
            "translator_mode": "ForGal-json",
            "detect": {"archives": ["*.xp3"], "min_score": 2},
            "steps": {
                "unpack": unpack,
                "extract": {"tool": "fake", "args": "x", "input_glob": "**/*"},
                "inject": {"tool": "fake", "args": "x"},
                "package": {"strategy": "patch_xp3", "tool": "fake"},
            },
        }
    )


class _FakeToolbox:
    """按名字返回预置工具路径;missing 集合中的名字抛工具缺失。"""

    def __init__(self, paths: dict[str, Path], missing: set[str] | None = None) -> None:
        self.paths = paths
        self.missing = missing or set()

    def locate(self, tool_name: str) -> Path:
        if tool_name in self.missing:
            raise PipelineError(
                "E-EXTRACT-TOOL-MISSING",
                f"找不到 {tool_name}(测试桩)",
            )
        return self.paths[tool_name]


class _FakeRunner:
    """按主/回退工具分别决定是否向 out_dir(argv 末位)写产物。"""

    def __init__(self, primary_writes: bool, fallback_writes: bool = False) -> None:
        self.primary_writes = primary_writes
        self.fallback_writes = fallback_writes
        self.calls: list[list[str]] = []

    def run(self, argv, cancel_event=None, timeout=None, cwd=None):
        self.calls.append(list(argv))
        writes = (
            self.primary_writes
            if Path(argv[0]).name.startswith("fake_msg")
            else self.fallback_writes
        )
        if writes:
            out_dir = Path(argv[-1])
            (out_dir / "scn").mkdir(parents=True, exist_ok=True)
            (out_dir / "scn" / "01.txt.scn").write_bytes(b"PSB")
        return SimpleNamespace(
            returncode=0,
            output_tail="",
            cancelled=False,
        )


def _make_project(tmp_path: Path) -> PatchProject:
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.xp3").write_bytes(b"x")
    return PatchProject.create(tmp_path / "proj", game, "fake")


def _make_tools(tmp_path: Path) -> dict[str, Path]:
    tools = tmp_path / "tools"
    tools.mkdir()
    mapping = {}
    for name in ("fake_msg", "fake_brute"):
        exe = tools / f"{name}.exe"
        exe.write_bytes(b"stub")
        mapping[name] = exe
    return mapping


def test_unpack_success_without_fallback(tmp_path):
    project = _make_project(tmp_path)
    toolbox = _FakeToolbox(_make_tools(tmp_path))
    runner = _FakeRunner(primary_writes=True)
    pipeline = Pipeline(project, _profile(), toolbox=toolbox, runner=runner)
    assert pipeline.executor().step_unpack() == 1
    assert len(runner.calls) == 1  # 回退未被触发


def test_unpack_fallback_invoked_on_empty_output(tmp_path):
    project = _make_project(tmp_path)
    toolbox = _FakeToolbox(_make_tools(tmp_path))
    runner = _FakeRunner(primary_writes=False, fallback_writes=True)
    pipeline = Pipeline(project, _profile(), toolbox=toolbox, runner=runner)
    assert pipeline.executor().step_unpack() == 1
    assert len(runner.calls) == 2  # 主工具 + 回退工具
    assert any(Path(c[0]).name.startswith("fake_brute") for c in runner.calls)


def test_unpack_fallback_missing_tool_raises_encrypted_guidance(tmp_path):
    project = _make_project(tmp_path)
    toolbox = _FakeToolbox(_make_tools(tmp_path), missing={"fake_brute"})
    runner = _FakeRunner(primary_writes=False)
    pipeline = Pipeline(project, _profile(), toolbox=toolbox, runner=runner)
    with pytest.raises(PipelineError) as excinfo:
        pipeline.executor().step_unpack()
    assert excinfo.value.code == "E-UNPACK-ENCRYPTED-XP3"
    assert "fake_brute" in str(excinfo.value)


def test_unpack_fallback_still_empty_raises(tmp_path):
    project = _make_project(tmp_path)
    toolbox = _FakeToolbox(_make_tools(tmp_path))
    runner = _FakeRunner(primary_writes=False, fallback_writes=False)
    pipeline = Pipeline(project, _profile(), toolbox=toolbox, runner=runner)
    with pytest.raises(PipelineError) as excinfo:
        pipeline.executor().step_unpack()
    assert excinfo.value.code == "E-UNPACK-ENCRYPTED-XP3"


def test_unpack_no_archive_matched(tmp_path):
    project = _make_project(tmp_path)
    toolbox = _FakeToolbox(_make_tools(tmp_path))
    runner = _FakeRunner(primary_writes=True)
    profile = _profile()
    profile.data["steps"]["unpack"]["archives"] = ["*.ypf"]  # 无匹配
    pipeline = Pipeline(project, profile, toolbox=toolbox, runner=runner)
    with pytest.raises(PipelineError) as excinfo:
        pipeline.executor().step_unpack()
    assert excinfo.value.code == "E-UNPACK-NO-ARCHIVE"


def test_unpack_preserves_manual_products_on_zero_output(tmp_path):
    """out_dir 已有手动解包产物且本次解包零产物 → 视为成功且不破坏现场。"""
    project = _make_project(tmp_path)
    toolbox = _FakeToolbox(_make_tools(tmp_path))
    runner = _FakeRunner(primary_writes=False)
    pipeline = Pipeline(project, _profile(with_fallback=False), toolbox=toolbox, runner=runner)
    out_dir = project.project_dir / "work" / "unpacked" / "data"
    out_dir.mkdir(parents=True)
    manual = out_dir / "scn" / "manual.txt.scn"
    manual.parent.mkdir(parents=True)
    manual.write_bytes(b"PSB")
    assert pipeline.executor().step_unpack() == 1
    assert manual.is_file()


def test_unpack_without_fallback_config_raises_encrypted(tmp_path):
    project = _make_project(tmp_path)
    toolbox = _FakeToolbox(_make_tools(tmp_path))
    runner = _FakeRunner(primary_writes=False)
    pipeline = Pipeline(project, _profile(with_fallback=False), toolbox=toolbox, runner=runner)
    with pytest.raises(PipelineError) as excinfo:
        pipeline.executor().step_unpack()
    assert excinfo.value.code == "E-UNPACK-ENCRYPTED-XP3"
