"""PACKAGE 补丁命名测试:按 Kirikiri 加载序列递增(业务流审查 F1 修复)。

Kirikiri 加载顺序 data < patch<N> < append<N>,越晚加载越优先:
游戏已有 append.xp3(前人补丁)时,patch.xp3 会被压住完全不生效,
必须命名 append2+。per-game override 可用 package.name 强制指定。
"""

from __future__ import annotations

from pathlib import Path

from galtrans_pipeline.orchestrator import Pipeline
from galtrans_pipeline.profile import EngineProfile
from galtrans_pipeline.project import PatchProject
from galtrans_pipeline.toolbox import ToolBox


def _profile() -> EngineProfile:
    return EngineProfile(
        {
            "profile": "fake",
            "capability": "L1",
            "translator_mode": "ForGal-json",
            "detect": {"archives": ["*.xp3"], "min_score": 2},
            "steps": {
                "unpack": {"tool": "fake", "args": "x {archive} {out_dir}",
                           "archives": ["*.xp3"]},
                "extract": {"tool": "fake", "args": "x", "input_glob": "**/*"},
                "inject": {"tool": "fake", "args": "x"},
                "package": {"strategy": "patch_xp3", "tool": "fake"},
            },
        }
    )


def _make(tmp_path: Path, existing: tuple[str, ...] = ()) -> Pipeline:
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.xp3").write_bytes(b"x")
    for name in existing:
        (game / name).write_bytes(b"x")
    project = PatchProject.create(tmp_path / "proj", game, "fake")
    return Pipeline(project, _profile(), toolbox=ToolBox())


def test_no_existing_patch(tmp_path):
    assert _make(tmp_path).executor()._patch_name({}) == "patch"


def test_patch_exists_increments(tmp_path):
    assert _make(tmp_path, ("patch.xp3",)).executor()._patch_name({}) == "patch2"


def test_patch2_exists_increments(tmp_path):
    assert _make(tmp_path, ("patch2.xp3",)).executor()._patch_name({}) == "patch3"


def test_append_family_takes_priority(tmp_path):
    """已有 append.xp3 → 必须命名 append2 才能压住它(核心场景)。"""
    assert _make(tmp_path, ("append.xp3",)).executor()._patch_name({}) == "append2"


def test_append2_exists_increments(tmp_path):
    assert _make(tmp_path, ("append2.xp3",)).executor()._patch_name({}) == "append3"


def test_patch_and_append_coexist(tmp_path):
    assert _make(tmp_path, ("patch.xp3", "append.xp3")).executor()._patch_name({}) == "append2"


def test_override_name_forced(tmp_path):
    pipeline = _make(tmp_path, ("append.xp3",))
    assert pipeline.executor()._patch_name({"name": "unencrypted2"}) == "unencrypted2"
