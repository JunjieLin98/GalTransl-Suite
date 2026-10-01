"""ToolBox 多目录解析与 profiles 路径解析测试(2026-09 复审加固项)。"""

import sys
from pathlib import Path

import pytest

from galtrans_pipeline.errors import PipelineError
from galtrans_pipeline.profile import default_profiles_dir, load_profiles
from galtrans_pipeline.toolbox import ToolBox


def _touch(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"stub")
    return path


def test_locate_searches_all_candidate_dirs(tmp_path, monkeypatch):
    """工具分散在用户目录与捆绑目录时都应被找到。"""
    user_dir = tmp_path / "GalTranslSuite" / "tools"
    bundled_dir = tmp_path / "bundled" / "tools"
    _touch(user_dir, "xp3brute.exe")
    _touch(bundled_dir, "unity_tool.py")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("GALTRANS_TOOLS_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    box = ToolBox(bundled_dir)
    assert box.locate("xp3brute").parent == user_dir.resolve()
    assert box.locate("unity_tool").parent == bundled_dir.resolve()


def test_user_dir_overrides_bundled_on_conflict(tmp_path, monkeypatch):
    """冻结态下用户目录先于安装目录(允许用户覆盖随包版本)。"""
    user_dir = tmp_path / "GalTranslSuite" / "tools"
    app_root = tmp_path / "app"
    _touch(user_dir, "xp3brute.exe")
    _touch(app_root / "tools" / "bin", "xp3brute.exe")
    fake_exe = _touch(app_root / "backend", "galtransl_backend.exe")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("GALTRANS_TOOLS_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)

    box = ToolBox()
    assert box.locate("xp3brute").parent == user_dir.resolve()


def test_locate_missing_tool_lists_searched_dirs(tmp_path, monkeypatch):
    monkeypatch.delenv("GALTRANS_TOOLS_DIR", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.chdir(tmp_path)
    box = ToolBox()
    with pytest.raises(PipelineError) as excinfo:
        box.locate("xp3brute")
    assert "xp3brute.exe" in str(excinfo.value)


def test_unknown_tool_name():
    with pytest.raises(PipelineError):
        ToolBox().locate("nonexistent_tool")


def test_default_profiles_dir_env_priority(tmp_path, monkeypatch):
    env_dir = tmp_path / "env_profiles"
    env_dir.mkdir()
    monkeypatch.setenv("GALTRANS_PROFILES_DIR", str(env_dir))
    assert default_profiles_dir() == env_dir
    monkeypatch.delenv("GALTRANS_PROFILES_DIR")
    assert default_profiles_dir().name == "profiles"


def test_load_profiles_missing_dir_raises(tmp_path):
    with pytest.raises(PipelineError) as excinfo:
        load_profiles(tmp_path / "nope")
    assert "profiles 目录不存在" in str(excinfo.value)


def test_run_backend_no_longer_pins_tools_env():
    """冻结引导不再 setdefault 工具目录(交给 ToolBox 多目录解析)。"""
    source = (
        Path(__file__).resolve().parents[2] / "run_backend.py"
    ).read_text(encoding="utf-8")
    assert "GALTRANS_TOOLS_DIR" not in source
