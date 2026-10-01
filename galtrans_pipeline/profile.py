"""EngineProfile:声明式引擎适配的加载/校验/覆盖合并。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

from .errors import PipelineError

VALID_CAPABILITIES = {"L1", "L2", "L3", "L4"}
REQUIRED_STEPS = ("unpack", "extract", "inject")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并;override 优先(需求 FR-A4:per-game 覆盖优先级最高)。"""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class EngineProfile:
    """一个引擎的声明式适配(YAML 加载,结构见 profiles/kirikiri.yaml)。"""

    def __init__(self, data: dict[str, Any], source: str = "") -> None:
        self.data = data
        self.source = source
        self._validate()

    # -- 基础属性 -------------------------------------------------------
    @property
    def name(self) -> str:
        return str(self.data.get("profile", ""))

    @property
    def capability(self) -> str:
        return str(self.data.get("capability", "L4"))

    @property
    def translator_mode(self) -> str:
        return str(self.data.get("translator_mode", "ForGal-json"))

    @property
    def detect(self) -> dict[str, Any]:
        return self.data.get("detect", {}) or {}

    @property
    def constraints(self) -> dict[str, Any]:
        return self.data.get("constraints", {}) or {}

    def step(self, name: str) -> dict[str, Any]:
        return (self.data.get("steps", {}) or {}).get(name, {}) or {}

    def unpack_archives(self) -> list[str]:
        return list(self.step("unpack").get("archives", []))

    def apply_override(self, override: dict[str, Any]) -> "EngineProfile":
        """per-game 覆盖(优先级:override > profile,FR-A4)。"""
        return EngineProfile(deep_merge(self.data, override), self.source)

    # -- 校验 -----------------------------------------------------------
    def _validate(self) -> None:
        if not self.name:
            raise PipelineError("E-INVALID-PROFILE", "缺少 profile 字段")
        cap = self.capability
        if cap not in VALID_CAPABILITIES:
            raise PipelineError("E-INVALID-PROFILE", f"capability 非法: {cap}")
        steps = self.data.get("steps", {}) or {}
        if cap in ("L1", "L2"):
            for key in REQUIRED_STEPS:
                if not steps.get(key):
                    raise PipelineError("E-INVALID-PROFILE", f"L1/L2 缺少 steps.{key}")
            for key in REQUIRED_STEPS:
                if not (steps.get(key) or {}).get("tool"):
                    raise PipelineError("E-INVALID-PROFILE", f"steps.{key} 缺少 tool")


def default_profiles_dir() -> Path:
    """profiles 目录解析:显式环境变量 → 冻结态安装根 → 仓库根。

    冻结态不依赖 __file__(PyInstaller 临时解包目录)与 cwd,杜绝
    run_backend chdir 失败时 profiles 静默变空的路径脆弱性。
    """
    env_dir = os.environ.get("GALTRANS_PROFILES_DIR")
    if env_dir:
        return Path(env_dir)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "profiles"
    return Path(__file__).resolve().parent.parent / "profiles"


def load_profiles(profiles_dir: Path) -> dict[str, EngineProfile]:
    """加载目录下全部 *.yaml profile,按 profile 字段索引。

    目录缺失直接报错:静默返回空字典会把环境问题伪装成"未识别引擎"。
    """
    profiles: dict[str, EngineProfile] = {}
    if not profiles_dir.is_dir():
        raise PipelineError(
            "E-INVALID-PROFILE",
            f"profiles 目录不存在: {profiles_dir}(可用 GALTRANS_PROFILES_DIR 显式指定)",
        )
    for path in sorted(profiles_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or not data.get("profile"):
            continue
        profile = EngineProfile(data, source=str(path))
        profiles[profile.name] = profile
    return profiles
