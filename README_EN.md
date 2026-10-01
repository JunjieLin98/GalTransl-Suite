<h1><p align='center'>GalTransl Suite</p></h1>
<div align=center>
  <img src="https://img.shields.io/github/v/release/JunjieLin98/GalTransl-Suite"/>
  <img src="https://img.shields.io/github/license/JunjieLin98/GalTransl-Suite"/>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue"/>
</div>
<p align='center'><b>An all-in-one LLM localization suite for visual novels</b> — drop in a game folder, and get a translated patch: engine detection, text extraction, LLM translation, and repacking, fully orchestrated.</p>

[中文](README.md)

GalTransl Suite is an enhanced fork of the open-source project [GalTransl](https://github.com/GalTransl/GalTransl) (GPL-3.0). It keeps the upstream translation core — prompt engineering, live caching, GPT dictionaries, proofreading checks, and the desktop GUI — and adds the **complete pipeline from game archives to a ready-to-install patch**.

## Features

- **Multi-engine orchestration**: automatic engine detection; unpack / extract / translate / inject / package pipeline with resumable, on-disk artifacts
- **Manual cache locks + bilingual editor**: pin translations against re-translation, incremental re-runs, per-line problem status
- **One-click glossary extraction** (upstream GenDic integration)
- **Job queue + desktop notifications**
- **Hardened local backend**: host allow-list, local token auth, restricted CORS
- **Actionable error codes**: every failure ships with a plain-language explanation and fix steps
- **Full upstream core retained**: GPT/conditional dictionaries, cache and resume, proofreading, direct translation of subtitles/EPUB/T++ files

## Engine support (honest capability levels)

| Level | Engine / scenario | Notes |
|---|---|---|
| **L1 fully automatic** | Kirikiri (krkr2/krkrz, PSB/mdf scn) | End-to-end; automatic encryption-key loading and patch-increment branches verified |
| **L1 fully automatic** | Yu-ris (`pac/*.ypf`) | ypf unpack / import / pack verified |
| **L2 experimental** | Unity (Mono, TextAsset + TypeTree) | Forced backup before write-back, restore supported; Il2Cpp not supported (guided to XUAT) |
| **L3 semi-automatic** | Others extractable via SExtractor | Bring your own [SExtractor](https://github.com/satan53x/SExtractor); suite translates, manual repack |
| **L4 translation only** | Your own JSON / subtitle files | Native upstream capability |

**Encrypted .xp3**: msg-tool auto-loads keys for some games; for strongly encrypted archives that unpack to zero files, the suite automatically falls back to a user-supplied `xp3brute.exe` placed in `tools/bin/` (not distributed — see below).

## User-supplied tools (not distributed)

Responsibility rests with the user; this project does not distribute:

- **xp3brute.exe** (strongly encrypted xp3) — put it in `tools/bin/`; the pipeline calls it automatically as a fallback
- **SExtractor** (GPL-3.0, long-tail engine extraction)
- **XUAT-family Il2Cpp tools**

Bundled third-party tools and licenses: [THIRD-PARTY-NOTICES](THIRD-PARTY-NOTICES.md).

## Getting started

### Desktop (recommended)

1. Grab `GalTransl.Suite_x64-setup.exe` from [Releases](https://github.com/JunjieLin98/GalTransl-Suite/releases/latest) — bundles the local Python backend; the installer is unsigned, so choose "More info → Run anyway" on SmartScreen
2. New project → drop the game folder → engine auto-detected → set your API key → run the pipeline

### CLI

```bash
pip install -e .
galtrans patch "D:\games\YourGame"        # detect + create project + run pipeline
galtrans run <project-dir> --only UNPACK  # re-run a single step
galtrans restore <project-dir>            # restore the game directory
```

Works with any OpenAI-compatible endpoint (GPT / Claude / Deepseek / Sakura / local GalTransl-7B/14B models).

## Relationship to upstream GalTransl

Forked from [GalTransl/GalTransl](https://github.com/GalTransl/GalTransl) v7.4.0 and released under **GPL-3.0**. Heartfelt thanks to upstream author [XD2333](https://github.com/XD2333) and all contributors — the prompt engineering, caching, dictionaries, proofreading system, and desktop foundation all come from upstream. This fork keeps the algorithm layer untouched, makes only controlled changes to the cache/storage layer, and tracks upstream feedback in [UPSTREAM.md](UPSTREAM.md).

## License

[GPL-3.0](LICENSE) — inherited from upstream GalTransl. Third-party component licenses: [THIRD-PARTY-NOTICES](THIRD-PARTY-NOTICES.md).
