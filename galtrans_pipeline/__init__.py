"""galtrans_pipeline — GalTransl fork 的流水线编排层。

引擎检测 → 解包 → 提取 → 翻译(复用上游)→ 注入 → 打包 → 产出补丁。
设计约束见 docs/architecture.md:翻译算法层零侵入;缓存/存储层受控改造。
"""

__version__ = "1.0.1"

STEPS = ["DETECT", "UNPACK", "EXTRACT", "TRANSLATE", "INJECT", "PACKAGE"]
