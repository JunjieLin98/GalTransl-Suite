"""galtrans CLI(CLI alpha 形态;GUI M2 复用同一执行体)。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .detect import detect_engine
from .errors import PipelineError
from .orchestrator import Pipeline
from .profile import load_profiles
from .project import PatchProject
from .toolbox import ToolBox

REPO_ROOT = Path(__file__).resolve().parent.parent


def _profiles_dir(args_profiles: str | None) -> Path:
    if args_profiles:
        return Path(args_profiles)
    from .profile import default_profiles_dir

    return default_profiles_dir()


def _print_results(results: list[dict]) -> None:
    if not results:
        print("未识别出引擎。")
        return
    print("检测结果(按置信度排序):")
    for item in results:
        print(
            f"  - {item['profile']:<12} 能力等级 {item['capability']}"
            f"  得分 {item['score']}  依据: {', '.join(item['matched']) or '-'}"
        )


def cmd_detect(args: argparse.Namespace) -> int:
    profiles = load_profiles(_profiles_dir(args.profiles))
    results = detect_engine(Path(args.game_dir).resolve(), profiles)
    _print_results(results)
    if not results:
        raise PipelineError("E-DETECT-UNKNOWN-ENGINE", Path(args.game_dir).name)
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    profiles = load_profiles(_profiles_dir(args.profiles))
    print("可用引擎 profile:")
    for name, profile in profiles.items():
        print(f"  - {name:<12} 能力等级 {profile.capability}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    profiles = load_profiles(_profiles_dir(args.profiles))
    game_dir = Path(args.game_dir).resolve()
    profile_name = args.profile
    if not profile_name:
        results = detect_engine(game_dir, profiles)
        if not results:
            raise PipelineError("E-DETECT-UNKNOWN-ENGINE", game_dir.name)
        profile_name = results[0]["profile"]
        _print_results(results)
    if profile_name not in profiles:
        raise PipelineError("E-INVALID-PROFILE", f"profile 不存在: {profile_name}")
    project_dir = (
        Path(args.project_dir).resolve()
        if args.project_dir
        else Path.cwd() / f"{game_dir.name}_patch"
    )
    overrides = {}
    if args.override:
        import yaml

        overrides = yaml.safe_load(args.override) or {}
    project = PatchProject.create(project_dir, game_dir, profile_name, overrides)
    print(f"工程已创建: {project.project_dir}")
    print(f"引擎: {profile_name}  能力等级: {profiles[profile_name].capability}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    project = PatchProject.load(Path(args.project_dir).resolve())
    profiles = load_profiles(_profiles_dir(args.profiles))
    profile = profiles.get(project.profile_name)
    if profile is None:
        raise PipelineError("E-INVALID-PROFILE", f"profile 不存在: {project.profile_name}")
    profile = profile.apply_override(project.overrides)
    pipeline = Pipeline(project, profile, toolbox=ToolBox(Path(args.tools_dir) if args.tools_dir else None))
    results = pipeline.run(
        from_step=args.from_step,
        only=args.only.split(",") if args.only else None,
        api_key=args.api_key or "",
        endpoint=args.endpoint or "",
        model=args.model or "",
    )
    print("执行完成:")
    for step, outcome in results.items():
        detail = outcome if isinstance(outcome, int) else f"{len(outcome)} 候选"
        print(f"  - {step}: {detail}")
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    """init + run 一条龙(CLI alpha 主入口)。"""
    cmd_init(args)
    project_dir = (
        Path(args.project_dir).resolve()
        if args.project_dir
        else Path.cwd() / f"{Path(args.game_dir).resolve().name}_patch"
    )
    args.project_dir = str(project_dir)
    return cmd_run(args)


def cmd_restore(args: argparse.Namespace) -> int:
    project = PatchProject.load(Path(args.project_dir).resolve())
    profiles = load_profiles(_profiles_dir(args.profiles))
    profile = profiles[project.profile_name].apply_override(project.overrides)
    pipeline = Pipeline(project, profile, toolbox=ToolBox())
    count = pipeline.restore()
    print(f"已还原 {count} 个文件到游戏目录")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="galtrans",
        description="galTrans — Galgame LLM 汉化流水线(CLI alpha)",
    )
    parser.add_argument("--version", action="version", version=f"galtrans {__version__}")
    parser.add_argument("--profiles", help="profile 目录(默认仓库 profiles/)")
    parser.add_argument("--tools-dir", help="外部工具目录(默认仓库 tools/bin)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_detect = sub.add_parser("detect", help="引擎检测")
    p_detect.add_argument("game_dir")

    p_profiles = sub.add_parser("profiles", help="列出可用 profile")

    p_init = sub.add_parser("init", help="创建补丁工程")
    p_init.add_argument("game_dir")
    p_init.add_argument("--profile", help="手动指定引擎(默认自动检测)")
    p_init.add_argument("--project-dir", help="工程目录(默认 <游戏名>_patch)")
    p_init.add_argument("--override", help="per-game 覆盖(YAML 字符串)")

    p_run = sub.add_parser("run", help="执行流水线")
    p_run.add_argument("project_dir")
    p_run.add_argument("--from-step", help="从指定步骤开始(幂等重跑)")
    p_run.add_argument("--only", help="仅执行指定步骤(逗号分隔)")
    p_run.add_argument("--api-key", help="翻译 API key(或环境变量 GALTRANS_API_KEY)")
    p_run.add_argument("--endpoint", help="OpenAI 兼容 endpoint")
    p_run.add_argument("--model", help="模型名")

    p_patch = sub.add_parser("patch", help="检测+创建工程+执行 全流程")
    p_patch.add_argument("game_dir")
    p_patch.add_argument("--profile", help="手动指定引擎")
    p_patch.add_argument("--project-dir", help="工程目录")
    p_patch.add_argument("--override", help="per-game 覆盖(YAML 字符串)")
    p_patch.add_argument("--from-step", help="从指定步骤开始")
    p_patch.add_argument("--only", help="仅执行指定步骤")
    p_patch.add_argument("--api-key", help="翻译 API key")
    p_patch.add_argument("--endpoint", help="OpenAI 兼容 endpoint")
    p_patch.add_argument("--model", help="模型名")

    p_restore = sub.add_parser("restore", help="从 backup/ 还原游戏文件")
    p_restore.add_argument("project_dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "detect": cmd_detect,
        "profiles": cmd_profiles,
        "init": cmd_init,
        "run": cmd_run,
        "patch": cmd_patch,
        "restore": cmd_restore,
    }
    try:
        return handlers[args.command](args)
    except PipelineError as error:
        print(f"[错误] {error}", file=sys.stderr)
        return 1
