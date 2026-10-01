<h1><p align='center'>GalTransl Suite</p></h1>
<div align=center>
  <img src="https://img.shields.io/github/v/release/JunjieLin98/GalTransl-Suite"/>
  <img src="https://img.shields.io/github/license/JunjieLin98/GalTransl-Suite"/>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue"/>
</div>
<p align='center'><b>通用 Galgame LLM 汉化套件</b> —— 拖入游戏目录,自动识别引擎、提取文本、LLM 翻译、回封产出中文补丁</p>

[English](README_EN.md)

GalTransl Suite 是开源项目 [GalTransl](https://github.com/GalTransl/GalTransl)(GPL-3.0)的 fork 增强版:在上游翻译核心(提示工程、实时缓存、GPT 字典、找错系统、桌面 GUI)之上,补齐了**从游戏封包到汉化补丁的完整流水线**,让"做一款 Galgame 的机翻补丁"从多个工具的手工接力变成一次拖拽。

```
游戏目录 ──► 引擎识别 ──► 解包/提取 ──► LLM 翻译 ──► 注入回封 ──► 中文补丁
              │            │            │            │
              └── kirikiri.yaml / yuris.yaml / unity.yaml 引擎适配档案 ──┘
```

## 特性

- **多引擎自动编排**:引擎自动识别,解包/提取/翻译/注入/打包全流程编排,产物落盘可重跑、可恢复(restore)
- **缓存人工锁定 + 双语对照编辑器**:逐条锁定译文不再被重翻、增量重翻、问题状态标记、按文件/问题筛选
- **术语一键提取向导**(集成上游 GenDic):草稿 → 确认 → 自动挂载
- **任务队列 + 系统通知**:批量任务排队,完成/失败桌面通知
- **后端安全加固**:Host 白名单、本地 token 鉴权、CORS 收敛
- **错误码可行动指引**:每个失败都有对应的人话解释与处理步骤
- **上游翻译核心全量保留**:GPT 字典/条件字典、译前译后字典、断点续翻、找错系统、字幕/epub/T++ 等直接翻译格式

## 引擎支持与能力等级(诚实声明)

按引擎成熟度分级,不承诺超出:

| 等级 | 引擎/场景 | 说明 |
|---|---|---|
| **L1 全自动** | Kirikiri(krkr2/krkrz,含 PSB/mdf scn) | 提取→翻译→回封全流程自动;已实测加密 key 自动加载、patch 递增分支 |
| **L1 全自动** | Yu-ris(`pac/*.ypf`) | ypf 解包/回填/回封实测通过 |
| **L2 实验** | Unity(Mono,TextAsset + TypeTree) | 回封前强制备份,支持 restore;Il2Cpp 不支持(引导 XUAT 等外部方案) |
| **L3 半自动** | 其余引擎(SExtractor 可提取) | 自备 [SExtractor](https://github.com/satan53x/SExtractor) 提取 → 套件翻译,回封人工 |
| **L4 仅翻译** | 自备 JSON/字幕等文本 | 上游 GalTransl 原生能力 |

**加密 xp3**:msg-tool 能自动加载部分游戏的加密 key;其余强加密封包解包后零产物时,套件会自动回退调用你自备的 `xp3brute.exe`(放入 `tools/bin/` 即可,见下),或按 `E-UNPACK-ENCRYPTED-XP3` 指引手动解包后从 EXTRACT 步骤续跑。

## 用户自备工具(不随包分发)

以下工具由用户自行获取,使用责任自负,本项目不分发:

- **xp3brute.exe**(强加密 xp3 解密)——放入 `tools/bin/`,解包失败时自动回退调用
- **SExtractor**(GPL-3.0,长尾引擎兜底提取)
- **XUAT 类 Il2Cpp 工具**

随包分发的工具(msg-tool、KirikiriTools 组件等)及其许可见 [THIRD-PARTY-NOTICES](THIRD-PARTY-NOTICES.md)。

## 快速开始

### 桌面端(推荐)

1. 从 [Releases](https://github.com/JunjieLin98/GalTransl-Suite/releases/latest) 下载 `GalTransl.Suite_x64-setup.exe` 安装(自带本地后端,无需安装 Python;安装包未签名,SmartScreen 提示请选"更多信息 → 仍要运行")
2. 新建工程 → 拖入游戏目录 → 引擎自动识别 → 配置 API key → 运行流水线
3. 新手请看 **[用户手册](docs/user-guide.md)**

### CLI

```bash
pip install -e .
galtrans patch "L:\gal\你的游戏"          # 检测+建工程+跑流水线
galtrans run <工程目录> --only UNPACK     # 单步重跑(UNPACK/EXTRACT/TRANSLATE/INJECT/PACKAGE)
galtrans restore <工程目录>               # 恢复游戏目录原状
```

API key 支持任一 OpenAI 兼容端点(GPT/Claude/Deepseek/Sakura/本地 GalTransl-7B/14B 等)。

## 与上游 GalTransl 的关系

本仓库 fork 自 [GalTransl/GalTransl](https://github.com/GalTransl/GalTransl) v7.4.0,整体以 **GPL-3.0** 开源。**衷心感谢上游作者 [XD2333](https://github.com/XD2333) 与全体贡献者**——提示工程、缓存系统、字典体系、找错系统与桌面端基础均来自上游;本项目对上游算法层零侵入,缓存/存储层仅做受控改造,并整理了[上游反馈清单](UPSTREAM.md)。上游的 [使用教程](https://github.com/GalTransl/GalTransl/wiki) 与 [GalTransl-7B/14B 模型](https://huggingface.co/SakuraLLM/GalTransl-7B-v2) 同样适用于本套件。

## 文档

| 文档 | 内容 |
|---|---|
| [用户手册](docs/user-guide.md) | 安装、向导、编辑器、常见错误 |
| [需求说明](docs/requirements.md) | 用户画像、功能分级、验收标准 |
| [架构设计](docs/architecture.md) | 分层、EngineProfile、错误码目录、数据流 |
| [开发计划](docs/development-plan.md) | 里程碑、风险、版本策略 |
| [上游反馈清单](UPSTREAM.md) | 缓存锁定位等 PR 候选 |
| [第三方许可](THIRD-PARTY-NOTICES.md) | 随包组件许可审计 |

## 合规与免责

- 仅用于你**合法持有**的游戏,制作个人使用或社区分享的汉化补丁;不分发游戏资产
- AI 翻译**未经完整人工审校**:在使用本工具翻译并在未做全文校对/润色的前提下发布时,请在最显眼的位置标注"AI 翻译补丁",而不是"个人汉化"
- 产出的补丁内嵌 `TRANSLATION_NOTICE`(AI 翻译声明)与第三方许可文件

## 已知边界

- 真实 LLM API 下的大规模翻译质量回归仍在进行,建议先以小批次试跑
- Unity 真实游戏样例的端到端回归仍在进行(spike 已通过合成样例验证)
- 更新检查 MVP 只提示不自动安装

## 许可证

[GPL-3.0](LICENSE) —— 继承自上游 GalTransl。随包分发的第三方组件许可见 [THIRD-PARTY-NOTICES](THIRD-PARTY-NOTICES.md)。
