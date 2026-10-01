"""后端服务入口(PyInstaller 冻结入口 / 开发态均可)。

冻结态布局(scripts/build_windows.py 产出):
  app/
    backend/galtransl_backend.exe   ← 本文件
    plugins/  profiles/  tools/bin/  res/
启动时 chdir 到发布包根,使上游相对路径(plugins/、res/)生效。
profiles/tools 目录由 profile.default_profiles_dir / ToolBox 按
sys.frozen 显式解析(含用户级 %APPDATA% 工具目录),不再依赖环境变量。
"""

import os
import sys


def _freeze_bootstrap() -> None:
    if not getattr(sys, "frozen", False):
        return
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    try:
        os.chdir(app_root)
    except OSError:
        pass


if __name__ == "__main__":
    _freeze_bootstrap()
    from GalTransl.server import main

    main()
else:
    _freeze_bootstrap()
