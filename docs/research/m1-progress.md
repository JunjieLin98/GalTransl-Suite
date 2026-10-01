# M1 进度报告(编排层 + CLI alpha)

| 项 | 内容 |
|---|---|
| 日期 | 2026-09-12 |
| 交付 | `galtrans_pipeline` 包 + kirikiri/yuris profiles + `galtrans` CLI + 17 项测试 + 三款真实游戏 E2E |

---

## 已交付

| 模块 | 文件 | 实测状态 |
|---|---|---|
| 错误码目录 | galtrans_pipeline/errors.py | 14 个错误码,全部带可行动指引;E2E 中 E-EXTRACT-TOOL-MISSING/E-TRANSLATE-API-AUTH 实际触发并给出正确指引 |
| EngineProfile | galtrans_pipeline/profile.py | 加载/校验/深合并覆盖(override 优先);18 项断言覆盖 |
| 引擎检测 | galtrans_pipeline/detect.py | **三款真实游戏全部正确检出**(マガルミナ→kirikiri、Relirium→yuris、とける風花→kirikiri) |
| ProcessRunner | galtrans_pipeline/runner.py | 子进程树管理;E2E 中发现并修复管道死锁(xp3brute 大量状态输出填满 64KB 缓冲) |
| ToolBox | galtrans_pipeline/toolbox.py | tools/bin 定位 + SHA-256 校验(msg-tool/Xp3Pack 已锁版本) |
| PatchProject | galtrans_pipeline/project.py | schema_version/workspace 目录/步骤快照/游戏目录校验 |
| 步骤实现 | galtrans_pipeline/steps.py | unpack/extract/translate(复用上游 run_job)/inject/package(patch_xp3+repack_archive)/restore |
| Orchestrator | galtrans_pipeline/orchestrator.py | 状态机 + --from-step 幂等重跑 + L3/L4 裁剪;restore 运维语义 |
| CLI | galtrans_pipeline/cli.py | detect/profiles/init/run/patch/restore 六命令 |
| profiles | profiles/kirikiri.yaml、yuris.yaml | 参数全部来自 M0 实测固化 |

## 真实游戏 E2E 结果(非 LLM 步骤全通过)

| 游戏 | 检测 | 解包 | 提取 | 注入+打包 |
|---|---|---|---|---|
| Relirium(Yu-ris) | ✅ yuris | ✅ sc.ypf | ✅ 242 JSON(日文 name-message) | ✅ **dist/sc.ypf 回解字节一致**(DIST_ROUNDTRIP_OK) |
| マガルミナ(krkrz+PSB) | ✅ kirikiri | ✅ data+append | ✅ 452 JSON(242 日文 PSB + 210 补丁中文) | M1.5(PSB 真名恢复) |
| とける風花(强加密) | ✅ kirikiri | ✅ xp3brute override(826 全解) | ✅ 42 scn JSON(760 条日文对话;另有 68 ks 可选) | M1.5(脚本集选择调优) |

TRANSLATE 步骤:代码链路验证完成(上游 run_job 调用、配置生成含全部段落、断点续翻由上游缓存承担);**真实翻译回归待用户提供 API key**(`--api-key` 或 `GALTRANS_API_KEY`)。

## E2E 发现并修复的缺陷(价值记录)

1. **参数模板空格切断**:`_render_args` 先 format 后 split,含空格路径被拆散 → 改为先分词后替换;
2. **管道死锁**:runner 轮询不读 stdout,xp3brute 大量输出填满 64KB 缓冲导致子进程永久阻塞 → 后台线程持续读取;
3. **配置模板降级**:上游导入失败时静默降级 10 行残缺模板(缺 common 段)→ 导入提前 + 显式传入上游模板;
4. **yuris ypf 版本字段**:pack 需 `--yuris-ypf-version`(Relirium=500,读原包头 dword 判定),已写入 profile 并注明 per-game;
5. **空脚本容错**:msg-tool 对无可翻译文本不产输出 → 提取步骤跳过而非报错。

## M1 剩余(下一段)

- 自建 krkrz/KAG 样例游戏 + 用户提供 API key 后的全流程回归(CLI alpha 发布门)
- マガルミナ PSB 真名恢复(利用已有 append.xp3 文件名/msg-tool file-list)+ とける風花脚本集选择调优
- 性能基线记录(NFR-2)

---

# M2 进度:GUI 工作台(后端 API + 前端)

## 已完成

### M2 后端(galtrans_pipeline/server_ext.py,commit 72ee405)
- 扩展上游 12333 后端(不另起服务),新增 10 个 REST 端点:
  - GET profiles / status / events(SSE 票据制)
  - POST sse-ticket / detect / projects / run / cancel / restore
- 安全层:
  - **Host 白名单**(localhost/127.0.0.1)——修复上游 DNS Rebinding 隐患(实测恶意 Host 返回 200+数据)
  - **本地 token 鉴权**:GUI 启动换取,hmac.compare_digest 校验,全部 POST 强制
  - **DPAPI 存储 API key**(ctypes,修复 c_char_p 堆损坏 0xc0000374 → create_string_buffer+cast)
- PipelineManager:后台线程跑 Pipeline,SSE 广播 job_started/log/job_done/job_failed
- 在线测试套件 tests/pipeline/test_server_ext_live.py:11 项全过

### CORS 精确收敛(commit 7d4f810)
- 上游 `send_header("Access-Control-Allow-Origin","*")` 全局放行与具体 Origin 并存时浏览器拒绝 →
  受控改造 end_headers:`handler._gt_cors_origin` 属性模式(白名单回显/外来置空抑制/None=上游原行为)
- server_ext 补 OPTIONS 预检放行 X-Local-Token;_reject_host 同样抑制 CORS
- npm run build 通过;live 套件 11/11 回归

### M2 前端(commit 7d4f810)
- desktop/src/lib/pipeline.ts:API 客户端(启动换 token 存 sessionStorage、EventSource+ticket 订阅)
- PatchWorkbenchPage:①选择游戏(文本框+浏览+检测+profile 下拉)②执行流水线
  (步骤徽章/运行全部/从翻译重跑/取消/还原 + SSE 日志区)+ 能力等级说明
- 路由 /patch + 侧边栏 🎮 入口 + patch.css

## 浏览器 E2E 冒烟(通过,2026-09-12)

Relirium 全流程在浏览器中验证:
1. 填入游戏目录 → 检测引擎 → yuris 自动选中(依据 yscfg.dat),profile 下拉同步
2. 创建工程 → 工程目录 `*_patch` 创建成功
3. ▶ 运行全部 → SSE 实时日志:DETECT(1 候选)→ UNPACK(sc.ypf,显示完整 msg-tool 命令行)
   → EXTRACT(242 文件逐个导出,空脚本"跳过(无可翻译文本)"容错正常)
4. TRANSLATE 步无 API key → **预期错误完整呈现**:`[E-TRANSLATE-API-AUTH]` + 可行动指引
   (提示 --api-key / 环境变量 / 直接编辑 config.yaml 三种途径)

## M2 剩余(按开发计划属 v0.1 范围,当前为功能子集)

- 首启向导/API 自检页、人性化错误聚合、只读问题列表(v0.1 完整门)
- Tauri 打包态(http://tauri.localhost)验证——dev 态已验证,打包态待 M6
- TRANSLATE 真实回归仍待 API key

---

# M3 进度:缓存锁定 + 双语对照编辑器

## 已完成

### 缓存锁定位(上游受控改造,PR 形态,~15 行)
- `GalTransl/CSentense.py`:CTrans 新增 `locked` 属性
- `GalTransl/Cache.py` 三处:
  - `_build_cache_obj`:locked 透传回缓存 JSON(post_save 全量快照不丢字段)
  - `get_transCache_from_json`:locked 条目**强制命中**——post_src 变化/重试失败/
    retran_key/校对模式均不再送回翻译(人工终稿语义);命中后回填 tran.locked
- 全部标注 `# galTrans:`,可整体作为上游 PR(候选记录在 UPSTREAM.md)

### 增量重翻
- 无需额外指纹:上游三联键(prev+now+next 的 name+pre_src)天然覆盖
  "原文变化→重翻";locked 是唯一新增语义

### cache_editor.py(编排层新模块)
- list_cache_files / load_entries(筛选+分页)/ update_entry(index+pre_src 双校验,
  tmp+os.replace 原子写;锁定空译文拒绝)/ compact_append_logs(调上游公开函数)/
  rebuild_output(rebuildr 模式,复制到 work/translated)
- 单写者原则:append 日志存在 → 读写均拒绝(E-CACHE-BUSY,先合并);
  活跃任务期间缓存写端点拒绝
- 新错误码 E-CACHE-EDIT-INVALID

### server_ext.py 新端点(共 6 个)
- GET cache(文件列表+统计+editable)、cache/entries(筛选分页)
- POST cache/entry(写回)、cache/compact(合并日志)、cache/rebuild(后台 rebuildr)
- **修复 M2 bug**:start_run 完成后 job status 永远 running → has_active 永真;
  现在统一 finally 收尾 + start_run/start_rebuild 前置单活跃检查

### 双语对照编辑器(/editor 页)
- 工程选择(浏览/手动输入+加载)→ 缓存文件列表(统计+has_append 提示)
- 条目网格:角色/原文/译文(textarea)/锁定 checkbox/保存状态;问题 ⚠ 标记
- 筛选:搜索、只看锁定、只看问题、只看未翻译;分页 200/页
- 交互:改译文失焦自动保存+自动锁定;锁定切换即存;合并日志/重建输出按钮
- 后台任务日志区(SSE)

### mock LLM(tools/mock_llm.py)——重要基础设施
- OpenAI 兼容(流式+非流式),理解 ForGal-json jsonline 协议(sig|{"id","dst"})
- 只处理当前批次输入行(含 src),跳过历史响应行(含 dst)避免串行
- **无真实 API key 驱动完整翻译会话**:真实缓存(润色 post_src/三联键/分块)
  由上游亲自写出;mock 驱动下 Relirium 全链路 TRANSLATE→INJECT→PACKAGE 打通,
  47266 条全翻成功,dist/sc.ypf 产出
- 后续 CI 无 key 全链路回归的基础

## 浏览器 E2E(M3 闭环,全部通过)
1. 编辑器加载 Relirium 工程 → 242 个缓存文件列出(统计正确)
2. 打开文件 → 条目网格渲染;编辑译文 → 自动锁定("已保存 ✓",磁盘验证 locked:true)
3. 点"重建输出" → rebuildr 从缓存刷写 gt_output + work/translated
4. **人工定稿译文出现在 gt_output 与 translated 的输出 JSON 中** ✓
5. live 套件 15/15(含 4 项新增 cache 端点测试);全量 30/30;前端 build 通过

## 发现并修复
- `_cache_file` 中 `path += ".json"` 对 Path 对象 TypeError → with_name
- 上游无害告警:CRebuildTranslate 关闭时报 `_shutdown_done` 属性缺失(记录 UPSTREAM.md)
- E2E 排障教训:netstat head -1 会抓到 ESTABLISHED 行杀错进程;合成事件的
  React 18 批处理时序要求 blur 前等重渲染(trusted 事件无此问题)

## M3 剩余(按开发计划)
- 术语向导(M3 计划内第三项)
- 编辑器增强:虚拟滚动(当前分页)、问题列跳转上游找错面板(M4 集成后)

---

# M4 进度:智能化集成(问题状态/术语向导/任务队列+通知)

## 已完成

### FR-D6 问题状态管理
- `problem_status.json`(work/gt_project 下,独立文件,零上游侵入):
  {缓存文件名: {index: confirmed|ignored}}
- cache_editor: load/save/set_problem_status;load_entries 行内带 problem_status
- 端点 POST /api/pipeline/cache/problem-status(活跃任务时 409)
- 编辑器 UI:问题 ⚠ 变为可点按钮,循环切换 确认→忽略→清除,
  样式区分(confirmed 橙底/ignored 灰+删除线),title 显示问题原文

### FR-D7 术语一键提取向导
- glossary.py:run_gendic(调上游 GenDic,translator 模式)/read_draft/read_confirmed/
  confirm_entries(TSV 写正式字典)
- **上游原生集成,零 config 修改**:GenDic 草稿「项目GPT字典-生成.txt」与确认后
  「项目GPT字典.txt」均在上游默认 config 的 dictionary.gpt.dict 引用链中,
  下次翻译自动作为人设/代词约束生效(上游原生入口同样可用 → DoD)
- mock_llm.py 支持 GenDic 协议:解析人名 hint(note=人名,与上游
  _build_final_list 的单票保留规则对齐)+ 片假名抽取
- 工作台 ③ 术语向导面板:提取(SSE 进度)→ 草稿表(可编辑/删除,前 100 条)
  → 一键确认生效
- **E2E:Relirium 47266 句 → GenDic 真实分词(vaporetto)+mock LLM → 草稿 811 条
  (含トワ/リゼット等角色名)→ 确认 100 条 → 正式字典落盘 ✓**

### FR-F7 任务队列与完成通知
- PipelineManager:allow_queue 入队(_run_queue),job 完成 finally 中
  _drain_queue 自动启动下一个;SSE 事件 job_queued/queue_updated
- POST run 响应 {queued, queue_position} / {job_id}
- 前端:run 时自动带 queue=true,排队时日志显示队列位置;
  job_done/job_failed 触发系统通知(Web Notification API,
  Tauri WebView2 原生支持;@tauri-apps/plugin-notification 插件留 M6 打包时统一接)
- **E2E:两个 PACKAGE 任务,第一个直接执行、第二个排队并在第一个完成后
  自动执行 ✓**(queued:true, queue_position:1)

### 附带修复
- POST /projects 幂等:工程目录已有 project.yaml 时改为打开而非覆盖
  (原实现会清空步骤状态,工作台刷新后无法恢复工程)
- PatchWorkbenchPage:工程打开时加载术语草稿;refreshGlossary 声明顺序修正

## 测试
- 单元 test_glossary.py 7 项(问题状态 roundtrip/非法值/条目透传;
  术语确认去重/TSV 解析/缺 config 错误指引)
- live 套件扩至 18 项(problem-status 设置/entries 透传/glossary 状态)全部通过
- 全量 37/37;前端 build 通过

## M4 诚实边界
- 问题状态目前是"标注+筛选"层;与上游 retran_key-by-problem 重翻联动的
  深度整合(忽略的问题不再触发重翻)留 v1.x
- 通知为 Web Notification;Tauri 原生通知插件在 M6 打包时接入
- GenDic 对同一草稿的重复提取有上游去重保护(重复点击提取幂等)

---

# M5 进度:Unity 最小版(spike 定案 + TextAsset 工具链)

## Spike 定案(commit 见 git log)

**选型 UnityPy 1.25.3,AssetsTools.NET 不采用。** 详见 docs/research/unity-spike.md:
- 真实 bundle(UnityPy 测试样本 char_118_yuki.ab)实测:读→改字段→
  **容器级 lz4 写回→重载修改持久化 PASS**(704,951→704,974 字节)
- 1.25 API 形态与旧教程差异大(env.save(pack,out_path) 而非 env.file.save())已归档
- AssetsTools.NET 不做 A/B 实测,依据(调研+架构成本)已诚实记录

## 交付

- **tools/unity_tool.py**:export(TextAsset→GalTransl JSON,_asset 分组标记)/
  import(按 _asset 重组写回 m_Script,容器级保存)/ copy(UNPACK 只读复制)
  - 内容格式支持 JSON 数组 [{name,message}] 与 TSV;其它 E-UNITY-FORMAT 指引
  - 输出 JSON 的 _asset 字段经上游 update_json_with_transList 原位保留
    (CSerialize.py 已核实)——单 bundle 多 TextAsset 场景成立
- **profiles/unity.yaml**(L2 实验性):detect(*/resources.assets 等 glob,
  detect.py files 增强为 glob 语义、向后兼容)/ copy / export / import +
  **package strategy: deploy**
- **强制备份+restore**:_package_deploy 替换游戏目录文件前强制复制原件到
  backup/(保留相对路径),同名多匹配拒绝部署(E-UNITY-DEPLOY-AMBIGUOUS);
  restore 走 M1 通用步骤
- **_run_tool 增强**:.py 工具以 sys.executable 驱动(unity_tool/sextractor 通用)
- 新错误码:E-UNITY-FORMAT、E-UNITY-DEPLOY-AMBIGUOUS
- 依赖声明:requirements.txt / pyproject.toml + UnityPy
- Il2Cpp 引导:docs/research/unity-il2cpp.md(TextAsset 不受 Il2Cpp 影响;
  代码内嵌文本→XUAT 路线,工具自备政策与 xp3-brute 一致)

## 测试

- 单元 test_unity_tool.py:TSV/JSON 解析、重组序列化(未知字段原位保留)、
  CLI 契约;全量 43/43;前端 build 通过
- CLI 实测:copy ✓;export 对无 TextAsset bundle 正确报 E-EXTRACT-NO-SCRIPT ✓

## 诚实边界

- 真实 Unity 游戏全链路回归待样例(三款样例游戏均为 krkr/Yu-ris);
  TextAsset 内容格式的真实多样性是 L2"受限"的边界声明
- data.unity3d 容器型文件需 per-game override 扩 input_glob(profile 注释已写)

---

# M6 进度:打包发布(进行中)

## 已完成

### 发布包布局与后端打包(实测通过)
- **scripts/build_windows.py**:PyInstaller onefile(galtransl_backend.exe,
  collect GalTransl/galtrans_pipeline 全部子模块)+ 组装发布布局:
  ```
  release/app/
    backend/galtransl_backend.exe   ← Tauri 上游候选路径自动对齐
    plugins/  profiles/  tools/bin/  res/
  ```
- 冻结态定位:run_backend.py 启动 chdir 到包根 + GALTRANS_TOOLS_DIR;
  server_ext._app_root() 冻结分支取 exe 上级(plugins/res 相对路径、
  profiles/tools 目录全部可达)
- **冻结后端实测**:`/api/version` ✓;`/api/pipeline/profiles` 三个 profile
  (kirikiri/unity/yuris)全部可读 ✓

### Tauri 集成
- tauri.conf.json:bundle.resources 把 release/app 各目录映射进安装包
  (backend/plugins/profiles/tools/res),targets 收敛为 nsis
- 上游 main.rs 的 backend_executable_candidates() 按 <exe>/backend/ 查找,
  与我们的布局天然对齐,无需改 Rust 代码

### 许可审计与文档
- **THIRD-PARTY-NOTICES.md**:随包分发的 Python 库/外部工具/前端框架许可
  汇编;msg-tool GPL 镜像附源码要求;xp3-brute/SExtractor/XUAT 用户自备声明;
  PyInstaller 特殊例外说明;DumpInjector 许可澄清记录
- **docs/user-guide.md**:新手手册(准备→连接→后端配置→流水线→编辑器→
  术语向导→排队→FAQ→合规)
- README 顶部插入分支说明(保留上游内容)

### CI
- 新增 package-backend job:Windows runner 上构建发布布局 + 冻结后端
  冒烟(version API + profiles)+ 上传 artifact

## M6 剩余(发布就绪后的收尾)
- Tauri NSIS 安装包本地/CI 产出(cargo 构建进行中)
- 自动更新:Tauri updater 插件 + minisign 密钥(GitHub Releases 源)
- 桌面端通知升级为 Tauri plugin-notification
- GalTransl-7B 等上游资源的 THIRD-PARTY 补充核对
- v1.0 tag/GitHub Release(创建 release 是外向动作,待确认)

## M6 补充:安装包实测(2026-09-12)

- **NSIS 安装包产出**:GalTransl Desktop_0.1.0_x64-setup.exe(~80MB,含
  backend exe/plugins/profiles/tools/res 全部 resources)
- **静默安装实测 PASS**(/S /D=临时目录):目录结构完整;
  **安装版后端冒烟 PASS**(version API + 三 profile 可读)
- **发布卫生修正**:build_windows.py 排除 *.pdb/*.zip/**xp3brute.exe**
  (许可未核实,政策为用户自备)后重打验证,tools/bin 只余
  Xp3Pack.exe/msg_tool.exe/version.dll
- 桌面端 Rust release 编译 PASS(1m53s,产物 galtransl-desktop.exe 11.7MB)

## M6 补充:自动更新与原生通知(2026-09-12)

- **插件落地**:tauri-plugin-notification / updater / process(Rust+npm 双侧),
  capabilities 加 notification:default / updater:default / process:allow-restart;
  Cargo.toml 补 serde_json(updater 配置使 generate_context! 需要)
- **更新签名**:minisign 密钥对生成于 .tauri/galtransl.key(.gitignore,不入库);
  公钥内联 tauri.conf.json plugins.updater;endpoints 指向
  `github.com/JunjieLin98/GalTransl/releases/latest/download/latest.json`
- **带签名重打 PASS**:产出 setup.exe + **.sig 签名文件**(updater 工作流闭环)
- **发布流程要点**(v1.0 Release 时用):
  1. `export TAURI_SIGNING_PRIVATE_KEY=$(cat .tauri/galtransl.key)`(2.0.6 的
     tauri-cli 不支持 _PATH 变体;installMode 字段勿写,那是 MSI 枚举)
  2. `npm run tauri:build` → 上传 setup.exe + .sig
  3. latest.json:{version, notes, pub_date, platforms["windows-x86_64"]=
     {signature: .sig 内容, url: setup.exe 的 release 直链}}
- 前端:lib/desktop.ts 封装 notifySystem(Tauri 插件优先/Web fallback)与
  启动静默更新检查(App.tsx 挂载,dev 态跳过)
- 已知边界:minisign 私钥空口令(本机生成,建议发布前轮换并加口令);
  检查更新只提示不自动安装(MVP)

---

# GUI 对齐上游设计语言(2026-09-12)

## 动机

新增的补丁工作台/双语编辑器最初用了自造样式(自造网格、自造错误条),
与上游原生页面(项目缓存页 1888 行的成熟设计)存在视觉与交互断层。

## 改造内容

### 双语编辑器 → 上游 ProjectCachePage 风格(重构)
- **直接复用上游样式类**(styles 自动生效):cache-card 卡片、cache-card__pill
  徽章体系(speaker 配色 pill 经 speakerStyle() 来自上游 lib/speaker)、
  cache-layout 侧栏、cache-sidebar-tab 页签、cache-file-item 文件列表、
  cache-search 搜索输入、search-highlight 高亮
- **page-state 三件套**:InlineFeedback(带图标的 toast,替代自造错误 div)、
  EmptyState(替代自造提示段落)、LoadingState
- **侧栏页签**:文件 / 问题(全工程问题计数徽标,点击跳转并自动开启问题筛选)
- 控制字符转义(\r\n 显示)与上游一致
- 保留全部 M3 业务逻辑:锁定写回/问题状态循环/合并日志/重建输出/分页

### 补丁工作台 → 上游组件对齐
- profile 下拉换上游 CustomSelect;错误/成功提示换 InlineFeedback

### 样式
- patch.css 删除自造网格样式,改为基于上游设计 token
  (--color-surface-strong/--radius-card/--space-*)的少量补充类

## 验证
- npm build 通过;浏览器实测:编辑器页侧栏页签/文件列表/卡片徽章/
  toast 提示全部按上游风格渲染,工作台 CustomSelect 正常

---

# v1.0.0 发布(2026-09-12)

## 发布前置修复(发布过程中发现并当场修复)

1. **CI backend-tests 三连红(自 live E2E 脚本加入起)**:`tests/pipeline/test_server_ext_live.py`
   是无 `__main__` 守卫的手动 live E2E 脚本(import 即自跑全部 18 项并 `sys.exit`),
   pytest 收集时触发 INTERNALERROR。修复:ci.yml 加
   `--ignore=tests/pipeline/test_server_ext_live.py` 并注明仅手动运行。
   本机实跑该脚本:**18/18 通过**(backend alive 7.4.0/Host 白名单/token/SSE ticket/
   工程创建/缓存锁定/问题状态/术语,真实样例工程)。
2. **THIRD-PARTY-NOTICES 两处来源地址失实**(全库排查 + GitHub API 实证):
   - msg-tool 真实仓库 `lifegpc/msg-tool`(原记录 dnanchev/msgTool 不存在);
     tag v0.4.0-alpha.3 存在,GPL-3.0
   - Xp3Pack.exe/version.dll 实为 **KirikiriTools v1.7 构建产物**
     (`arcusmaximus/KirikiriTools`,MIT,已归档;release 1.7 资产名实证吻合;
     原记录 icecr4ck/Xp3Pack 不存在)
3. **工具溯源闭环(GPL §6)**:随包三工具 SHA256 与官方 release 资产逐一比对一致
   (msg_tool.exe=62fe3967…/Xp3Pack.exe=c6f4a6f4…/version.dll=52745037…),
   msg-tool v0.4.0-alpha.3 源码包(1.1MB)随 Release 资产附上。
   README 中的 XD2333 链接为上游原文,GitHub 重定向有效,不改。

## 构建与签名

- **版本号 1.0.0**:tauri.conf.json / package.json / package-lock.json(两处)/
  Cargo.toml(Cargo.lock 构建时自动同步)
- **坑:tauri build 在签名口令提示处挂死**。空口令 minisign 密钥必须显式
  `export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""`,否则(后台/管道 stdin 场景)
  CLI 在 "Finished 1 bundle" 之后的 updater 签名口令交互处永久挂起——
  现象是产物已出但 `.sig` 缺失、进程无 CPU 无输出。显式空串后正常:
  `Finished 1 updater signature`。**此坑补记入发布流程要点。**
- 带签名重打 PASS:`GalTransl Desktop_1.0.0_x64-setup.exe`(55.8MB)+ `.sig`(432B)
- 旧 0.1.0 产物清理

## 发布门禁(全绿)

- pytest(CI 同命令,含两处 --ignore + 一处 deselect):**69 passed, 1 deselected**
- pipeline 套件 43/43;live E2E 18/18
- npm build(tsc+vite)随 tauri build 通过

## 发布资产(release/dist_out/)

setup.exe + .sig + latest.json(updater 清单,platforms.windows-x86_64,
signature=.sig 内容,url=release 直链 %20 转义)+ SHA256SUMS.txt +
msg-tool 源码包。发布说明 release/NOTES-v1.0.0.md:
**明确 fork 自上游 GalTransl v7.4.0(GPL-3.0)并致谢**、能力等级诚实声明
(L1/L2 实验/L3/L4)、SmartScreen 无签名公示、用户自备工具声明、
合规声明(合法持有/TRANSLATION_NOTICE)、已知边界。

## 发布动作(实际执行)

- 提交 release prep → push develop → CI 复验:
  **又暴露一真因——backend-tests 从未真正跑过**(安装步骤漏装 pytest,
  此前三连红皆此因;live 脚本排除也一并落地)。修复后 CI 全绿
  (run 34701758955,conclusion success)
- tag **v1.0.0**(annotated)打在 develop 发布提交 29c2c5e 并推送
- **main 未 ff**:origin/main 已被同步到上游新 main(98ee841,含上游
  PR #212 等 7 个新提交,基于 v7.4.0 fork 点之上),ff 推送被拒;
  不强推,发布以 tag 为准。本地 main 恢复跟踪 origin/main
- `gh release create v1.0.0 -R JunjieLin98/GalTransl --latest` 上传 5 资产:
  https://github.com/JunjieLin98/GalTransl/releases/tag/v1.0.0
- **坑:GitHub 自动把资产名空格转为点**(`GalTransl.Desktop_....exe`),
  latest.json 里按空格 %20 写的下载 URL 变 404——改用点号名重传
  latest.json/SHA256SUMS 后修正
- **端到端验证**:releases/latest/download/latest.json 返回 1.0.0 清单
  (CDN 缓存 ~90s 刷新),安装包直链 200
- 发布说明链接一律用 blob/v1.0.0/(远端 main 无我们的文档,blob/main 会 404)
- **发布目标仓库为个人项目 JunjieLin98/GalTransl,不动上游 GalTransl/GalTransl**

---

# 修复:强加密 xp3 静默零产物 + xp3brute 回退接入(v1.0.0 后,2026-09-12)

## 用户报告

本地运行报错,归因 xp3-brute 缺失。复现定位(样例 とける風花,data.xp3 强加密):

1. msg-tool 对不支持的强加密 xp3 **退出码 0 但 work/unpacked/data/ 零产物**
   (826 文件全部解密失败不视为进程错误)→ UNPACK 误报成功;
2. 计划 Kirikiri 链第一环"用户自备 xp3brute 解包"(sample-games.md M1 方案)
   **从未接入编排**——runner.py 已预留其输出死锁对策,但无调用点;
3. 旧 total==0 分支把"无封包匹配"与"解包失败"混为 E-UNPACK-ENCRYPTED-XP3 误指加密。

## 修复(PR 形态,~70 行)

- **steps.py**:UNPACK 逐封包检查产物数,零产物 → 按 profile
  `encrypted_fallback`(tool+args)自动回退;回退工具缺失(E-EXTRACT-TOOL-MISSING)
  转译为 E-UNPACK-ENCRYPTED-XP3 全上下文指引;回退后仍零产物同样报加密。
  工作目录已有手动解包产物(>0 文件)视为成功,不覆盖。
  "无封包匹配"拆分为新错误码 **E-UNPACK-NO-ARCHIVE**。
- **errors.py**:E-UNPACK-ENCRYPTED-XP3 指引重写(①xp3brute.exe 放 tools/bin
  重跑 UNPACK 自动回退 ②手动解包放 work/unpacked/<封包名>/ 从 EXTRACT 续跑
  ③责任自负);新增 E-UNPACK-NO-ARCHIVE。
- **profiles/kirikiri.yaml**:unpack 步骤声明
  `encrypted_fallback: {tool: xp3brute, args: "unpack {archive} {out_dir}"}`。
- **architecture.md §3.6** 错误目录同步。

## 验证

- 新增 tests/pipeline/test_unpack_fallback.py **6 场景全过**
  (成功无回退/回退触发/回退缺失→加密指引/回退仍空/无封包匹配/未配置回退)
- **真实强加密游戏端到端**:とける風花 data.xp3 → msg-tool 零产物 →
  xp3brute 回退 70s 解出 **827 文件**(826 资源+xp3-meta.yaml),真名目录
  (scn/AppConfig.tjs/bgm/font)完整;unencrypted.xp3 照常 397 文件
- CI 同命令 pytest 全绿(75 passed, 1 deselected)
- 发布包不受影响:DIST_EXCLUDE 继续排除 xp3brute.exe(不随包分发,
  仅编排调用用户自备副本)

---

# 品牌定名 GalTransl Suite + README 重写(2026-09-12)

## 动机

README 此前沿用上游原文(上游徽章/前言/教程链接),与"通用汉化套件"的
实际定位不匹配;产品名 "GalTransl Desktop" 只覆盖桌面端,不覆盖 CLI+编排。

## 改名面(GalTransl Desktop → GalTransl Suite)

- tauri.conf.json:productName / identifier(com.galtransl.suite)/ 窗口标题
- 桌面端:ConnectionContext 窗口标题×2、index.html title、styles.css/motion.ts 注释
- 包名:package.json + lock(galtransl-suite)、Cargo.toml + lock;
  pyproject description;desktop/README.md
- 版本 1.0.0 → 1.0.1(随 v1.0.1 发布生效;已装 1.0.0 不受影响)
- **内部保留**:GalTransl 上游核心包名、`galtrans` CLI 命令、
  galtrans_pipeline 编排包名、仓库地址(均不改,避免无谓 churn)
- 历史文档中的旧安装包名(m1-progress)按史实保留

## README 重写(中英双份)

- 定位:"通用 Galgame LLM 汉化套件"+流程示意;去掉上游徽章/logo/前言/教程正文
- 能力等级诚实表(L1 Kirikiri/Yu-ris、L2 Unity 实验、L3/L4)+加密 xp3 自动回退说明
- 用户自备工具声明(xp3brute/SExtractor/XUAT)
- 快速开始:桌面安装包 + CLI 示例
- **上游关系章节:fork 致谢 XD2333、GPL-3.0 继承、UPSTREAM.md、上游教程/模型仍适用**
- 合规(AI 翻译标注要求沿用上游规范)、已知边界、文档索引

---

# v1.0.1 发布(2026-09-13)

## 发布内容

- **fix(unpack)**:强加密 xp3 零产物自动回退用户自备 xp3brute(前次会话)
- **brand**:定名 GalTransl Suite,README 中英重写,版本 1.0.1

## 发布过程拦截的坏包(重要教训)

- 用**系统 Python**(C:\Python313)跑 build_windows.py,PyInstaller 产出缺
  orjson 的 16.3MB 坏后端(正确 39.9MB,差 23MB)——直接启动即
  ModuleNotFoundError。**v1.0.0 的 39.9MB 是 venv 打的**;构建解释器必须
  与安装依赖的环境一致。
- 防呆:build_windows.py 新增 `_assert_runtime_deps()`(orjson/yaml/UnityPy/
  openai/PyInstaller 缺一即快速失败)
- 拦截手段:**冻结后端真实管线冒烟**(启动冻结 exe → API 建工程 →
  跑 UNPACK+EXTRACT)——UNPACK 3s 完成(マガルミナ data.xp3 加密 key
  自动加载,1584 文件 424MB),EXTRACT done 226 JSON(append 真名 scn 链;
  data/ 哈希名无扩展名需 per-game override,与打包无关)
- 安装包体积回归检查:34.8MB(坏)→ 58.5MB(好)

## 发布动作

- tag **v1.0.1**(develop 53b9417)→ push →
  `gh release create v1.0.1 -R JunjieLin98/GalTransl --latest`
- 资产 5 件:GalTransl.Suite_1.0.1_x64-setup.exe(58.5MB,签名)+ .sig +
  latest.json(点号 URL)+ SHA256SUMS.txt + msg-tool 源码包
- **验证**:updater 通道 `releases/latest/download/latest.json` 已下发
  version=1.0.1 + 点号直链 + minisign 签名;安装包直链 200
- v1.0.0 客户端(更新只提示)将收到 1.0.1 提示——更新通道首次真实闭环

---

# 业务流全链路审查与优化(2026-09-13)

## 审查范围

orchestrator.run 状态机 → steps.py 五步骤 → restore → toolbox/profile 定位。
逐条核对幂等语义、错误路径、与引擎真实行为的契合度。

## 发现并修复

1. **F1 补丁命名会产出无效补丁(严重,业务流真 bug)**:原 `_patch_name`
   只对 patch 家族递增;Kirikiri 实际加载序 data < patch<N> < append<N>
   (越晚越优先),样例 マガルミナ 已有 append.xp3(前人补丁),产出
   patch.xp3 会被压住**完全不生效**。修复:扫描游戏目录 patch/append
   家族取最大序号+1(append 家族优先),per-game override 可用
   `package.name` 强制(unencrypted 场景)。真机验证:マガルミナ→append2。
2. **F7 UNPACK 非原子**:解包直接写 out_dir,失败残留半成品、重跑不覆盖
   已删封包的旧产物。修复:先解临时目录,有产物才原子替换;out_dir 已有
   手动解包产物时零产物视为成功不破坏现场。**坑:msg-tool 会截断目录名
   后缀**(.data.unpacking→.data,产物散落重复提取),临时目录名必须无点号。
3. **F6/F5 重跑残留重复翻译**:EXTRACT 不清 extracted/、TRANSLATE 不清
   gt_input/,改 override 缩小范围后残留旧文件被重复翻译浪费 token。
   修复:两步骤开始时清理旧 *.json(缓存不受影响,断点续翻语义不变)。
4. F9 restore 只重置 INJECT 不重置 PACKAGE;F10 registry 私有字段访问
   → 新增 running_jobs()。F11 EXTRACT 死代码删除。
5. msg-tool 后缀截断坑记入 _tmp_unpack_dir 注释(后续换工具/加步骤时避雷)。

## 新增测试(8 项)

- test_patch_naming.py 7 场景(无补丁/patch 递增/patch2 递增/append 优先/
  append2 递增/并存/override 强制)
- test_unpack_fallback + 手动产物保护场景
- 全量 106 passed,1 deselected;真机 とける風花 UNPACK+EXTRACT 复验
  (827 文件回退解包 + 42 JSON 提取 + 零临时残留)

## 复审整改(同日,阶段一)

- ToolBox 多目录解析(--tools-dir > env > 用户目录 %APPDATA%/GalTranslSuite/tools
  > 冻结安装目录 > 仓库);/api/pipeline/tools + 设置页"外部工具"卡片
- profile.default_profiles_dir + load_profiles 缺目录报错(消除静默空表)
- live E2E 加 __main__ 守卫,CI 移除 ignore;版本对齐 1.0.1
- scripts/frozen_smoke.py 固化(体积门禁+真实管线冒烟)
- user-guide:外部工具放法/旧版升级双安装提示
- minisign 轮换约束记录:换钥匙即换内嵌公钥,v1.0.0/1.0.1 客户端将拒收
  后续更新——只能在下个大版本一并规划
