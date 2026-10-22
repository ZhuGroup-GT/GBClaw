# GBclaw src 代码导览

本文帮助维护者在修改行为前找到负责它的模块。理解研究流程可以先读[架构与设计](architecture.md)，选择使用或开发指南可以从[文档目录](README.md)进入。下面将设计逻辑对应到当前 `src/` 的实现。

## 设计逻辑与职责边界

论文将任务协调、专业执行和持久研究记忆分开。源码也遵循这一分工：agent 决定需要做什么，领域包执行科研操作，公共服务负责记录和查回结果。接入新的科研流程时，应能够复用这些服务，无需重新实现对话管理或数据存储。

| 模块 | 负责内容 | 需要保持的边界 |
| --- | --- | --- |
| `core/` | 配置加载、模型路由与 provider、执行会话与取消、chat stream 事件 schema、小型 I/O 工具 | 不 import 领域、agent 或工作区代码；将机器、模型选择与科研任务实现分开。 |
| `extensions/` | 领域注册表：声明类型、宿主的 core 声明、加载与校验 | 声明只包含普通数据和 `"module:attr"` 字符串；运行时 hook 在启动完成后才解析。 |
| `project_workspace/` | 项目标识、聊天记录、文件、checkpoint 和执行占用 | 管理项目生命周期，不承担科研算法。 |
| `project_memory/` | 科研事实与 runs、artifact manifest、摘要、发布、提取和检索 | 保留证据来源，区分对话解释与计算测量。 |
| `planning/` | 版本化项目计划、需批准的草稿、计划规则和单轮 planning mode | 只有显式决定才能激活或修订计划，不依据对话中的关键词。 |
| `agents/` | Agent graph、orchestrator 与 planner、公共 worker 生命周期、模型上下文 middleware、面向 agent 的记忆工具和 streaming | 将项目记录转成模型上下文，数据库由记忆和工作区服务管理。 |
| `domains/` | 每个科研领域一个包：声明、worker、delegate、提示词、工具以及模拟或分析引擎 | 通过公共 run 接口发布结果，避免另建一套记忆数据库。 |
| `interfaces/` 与 `web/` | HTTP API、终端 CLI、两者共用的单轮管线和静态 Web UI | API、CLI 与 Web UI 共用后端服务和同一条单轮管线，避免单独实现另一套科研流程。 |

表中各包按从底层到上层排列，import 也沿这一方向：`core ← extensions ← project_workspace ← project_memory ← planning ← agents ← domains ← interfaces`。`agents` 通过注册表中的 `"module:attr"` 获取领域 worker、工具 provider、orchestrator 工具以及路由与规划说明，不直接 import 它们。仍有两处有限的例外：`ProjectStore` 通过惰性 import 打开记忆仓库、artifact store、run 服务和摘要视图写入；`domains/structure/routes.py` 使用 API 的依赖辅助函数。

两个包分别处理不同的上下文。`agents/middleware/` 管理对话如何放入模型窗口，包括 session summary 以及临时 worker 历史的单池压缩（`worker.py`、`worker_policy.py`）；`project_memory/snapshot/` 读取可复用的持久项目记忆快照。后者的 `session.py` 是为调用者加载快照的入口，不负责对话摘要，也不会创建新线程。

### 扩展时从哪里开始

| 想做的改动 | 入口 |
| --- | --- |
| 增加可复用的科研工具 | `domains/` 中对应的领域包：工具模块、`catalog.py` 中的 worker 工具列表，以及 `extension.py` 中的记忆声明和发布契约。 |
| 从其他包为已有 worker 增加工具 | 在该扩展的 `tool_providers` 中声明 `ToolProviderSpec`，其工厂为指定 worker 返回额外工具。 |
| 增加专业 agent | 领域 `extension.py` 中的 `WorkerDefinition` 和记忆策略，以及 worker（`agent.py`）、delegate 工具工厂（`delegate.py`）、提示词与 orchestrator 路由小节（`prompt.py`）。 |
| 增加事实类型、工作流或查询工具 | `extensions/specs.py` 的公开声明，参照[扩展开发指南](extension-guide.md)。 |
| 增加 HTTP 路由、启动或关闭 hook、工具标签或 LLM provider | `DomainExtension` 的 `routers`、`startup`、`shutdown`、`tool_labels` 和 `llm_providers` 字段，由 `interfaces/lifecycle.py` 在 API 和 CLI 中执行。内建 provider 在 `core/llm/providers/`。 |
| 修改 chat stream 事件或斜杠命令 | `core/stream_events.py`，之后用 `python -m core.stream_events` 重新生成 `web/assets/js/stream_events.js`；斜杠命令只在 `interfaces/commands.py` 声明一次。 |
| 修改摘要选择或压缩方式 | 项目知识在 `project_memory/summary/`；对话衔接在 `agents/middleware/`。 |
| 修改项目文件、聊天持久化或占用机制 | `project_workspace/` 下对应服务。 |
| 修改计划规则、草稿或批准流程 | `planning/`；planning worker、计划工具和门禁在 `agents/planner/`。 |
| 为生成的脚本接入本地 Python 环境 | `src/domains/coding/environments.toml`，参照 [Coding agent](coding-agent.md)。 |

增加需要持久保存的科研任务时，复用 `ToolRunSession` 和公共 worker 生命周期。它们提供稳定的 run 引用、产物登记和完成状态跟踪，领域扩展无需另建历史或摘要机制。

## 文档索引

- [architecture.md](architecture.md)：设计目标、研究者参与的工作流，以及各模块边界的原因。
- [getting-started.md](getting-started.md)：安装、启动脚本、CLI 命令和可选科研运行环境。
- [agent-runtime.md](agent-runtime.md)：orchestrator、worker、delegate tool、LangGraph 运行时和 streaming 事件。
- [tools.md](tools.md)：暴露给 agent 的工具、内部服务函数、LAMMPS/CSL/analysis/web search 的边界。
- [dichromatic-map.md](dichromatic-map.md)：晶粒双色图的交互选胞及其结果含义。
- [project-memory.md](project-memory.md)：project workspace、history、summary、checkpoint、recall memory 的管理方式。
- [configuration.md](configuration.md)：`.key`、`.env`、模型路由、thinking、LAMMPS 资源和 prompt 配置。
- [coding-agent.md](coding-agent.md)：临时 Deep Agents worker、沙盒边界和受控持久化。
- [api-and-ui.md](api-and-ui.md)：FastAPI 入口、chat/project/file API，以及静态 Web UI 的代码边界。
- [extension-guide.md](extension-guide.md)：领域声明、worker、工具、宿主 hook，以及不调用模型检查扩展。
- [roadmap.md](roadmap.md)：可选扩展方向及需保持的设计约束。
- [references.md](../references.md)：框架论文、相关研究、软件和势函数。

## 顶层模块

```text
src/
├── core/                   # 最底层：配置、LLM、执行会话、stream 事件、I/O
│   ├── paths.py            # SRC_ROOT 与 REPO_ROOT
│   ├── identifiers.py      # 各层共用的 run ID 格式
│   ├── stream_events.py    # chat stream 事件类型与字段；生成 Web UI 模块
│   ├── settings/           # .env/.key 加载、LangSmith、上下文预算、web search、执行模式
│   ├── llm/                # 模型路由配置、build_llm 工厂、thinking、reasoning、截断判断
│   │   └── providers/      # ProviderAdapter 契约；DeepSeek、OpenAI、Anthropic 适配器；tokens.py + vocab/（离线 token 计数）
│   ├── execution/          # 执行会话与取消、SlotPool、受监管的子进程、进程池
│   └── util/               # 哈希与原子写入（仅 stdlib）、LangGraph run 标识
├── extensions/             # 领域注册表：specs、loader、validation、宿主声明（core.py）
├── project_workspace/      # ProjectStore（store.py）、项目路径与 metadata
│   ├── chat/               # 永久 Chat history、trace、请求准入、Markdown transcript
│   ├── artifacts/          # 项目文件安全访问与 UI 图片引用
│   ├── execution/          # 按项目的 checkpointer、执行占用、checkpoint 修复
│   └── persistence/        # 跨进程锁与 SQLite 辅助函数
├── project_memory/         # 语义记忆持久化、发布与召回
│   ├── storage/            # SQLite facts/runs/search、结论、task state、tool output 归档
│   ├── publication/        # RunService、ToolRunSession、fact 构造、validator 与原子写入
│   ├── run_artifacts/      # 按 run 的 artifact 目录、manifest、中断 run 收尾
│   ├── extraction/         # 对话提取、key policy 与持久重试
│   ├── snapshot/           # 只读项目记忆快照及其数据契约
│   ├── summary/            # Project summary 策略、生成、归档、查询与视图
│   └── prompts/            # 对话 fact 提取提示词
├── planning/               # 版本化计划、需批准的草稿、规则、视图、planning mode
├── agents/                 # graph.py agent 工厂；invoke.py run config 与回复解析
│   ├── orchestrator/       # 面向用户的 graph、注册表拼装的提示词、领域工具、web search 工具
│   ├── planner/            # planning worker、delegate_plan、计划工具与门禁、domain_knowledge.md
│   ├── workers/            # WorkerSpec、公共 delegate 生命周期、交付物校验、结果、outcome、计划跟踪
│   ├── middleware/         # 每个 thread 的 Session summary、上下文编辑、恢复、取消与 worker 历史压缩
│   ├── memory/             # worker 召回、记忆 handoff、结论与结果投影
│   │   └── tools/          # 面向模型的记忆、artifact、结论、output 与 task-state 工具
│   ├── streaming/          # 由 LangGraph stream 生成 chat 事件、工具标签、trace 分段
│   └── prompts/            # 公共结果交接提示词
├── domains/                # 每个科研领域一个包（结构见下文）
│   ├── structure/          # csl/、dichromatic/、routes.py（双色图 HTTP API）、memory_query.py
│   ├── lammps/             # common/、orientation.py，以及每个 LAMMPS 工作流一个子包
│   ├── analysis/           # stress_strain/（tool.py、analysis.py、model_view.py）、neb_mep/、rendering/、
│   │                       #   periodicity/、periodicity_tools.py、common.py（artifact 解析）、
│   │                       #   periodicity_artifacts.py（与子进程共用的文件名）
│   └── coding/             # agent.py、middleware.py（压缩与步数预算）、run.py、sandbox/、limits/、
│                           #   budget.py、resources.py、settings.py、orchestrator.py、environments.py + .toml
└── interfaces/             # commands.py、turn.py（公共单轮管线）、recording.py、lifecycle.py
    ├── api/                # FastAPI app、deps、schemas、ownership、routers/（chat、projects、registry）
    └── cli/                # 终端 CLI（python -m interfaces.cli）：对话循环、输入、渲染
```

`src/` 之外，仓库根目录存放 Web UI 和内置势函数：

```text
web/                        # 由 API 提供的静态 Web UI：index.html 与 assets/
└── assets/js/              # registry.js 读取 GET /api/registry（delegate 标签、命令、stream 策略）；
                            # stream_events.js 由 core/stream_events.py 生成
data/potentials/            # 原子间势函数文件与 potentials.json 目录
```

每个领域包遵循同一结构。`extension.py` 以 `MEMORY_EXTENSION` 声明该领域的 `DomainExtension`；`agent.py` 构建 worker；`delegate.py` 为 orchestrator 提供 delegate 工具工厂；`prompt.py` 包含 worker 提示词，以及被 orchestrator 提示词收录的 `ORCHESTRATOR_ROUTING` 小节；`catalog.py` 列出 worker 工具，并提供 `ToolProviderSpec` 指向的工厂。coding 领域没有 `catalog.py`：它的 Deep Agents worker 使用沙盒 backend 的文件与命令工具，以及 run session（`run.py`）的输入与发布工具；`sandbox/base.py` 定义 `CodingSandbox` 契约，由 `sandbox/bubblewrap.py` 实现。`lammps/` 中每个工作流子包（`minimize/`、`structure_minimize/`、`energy/`、`shear/`、`neb/`）包含 agent 适配器 `tool.py`、配置 schema `config.py`，以及在 LAMMPS conda 环境中运行的 `engine.py`；`cfg_convert/` 用 `converter.py` 代替 engine，`common/` 存放共用的启动器、MPI rank 准入、配置、势函数目录、fact 构造和几何工具。

`domains/analysis/` 中，`stress_strain/`、`neb_mep/`、`rendering/` 各自的 `tool.py` 提供 agent adapter，并记录带来源关系的 analysis run。 `periodicity_tools.py` 为 `periodicity/` 包承担同样职责，并以子进程方式运行该包，使服务进程不导入其数值计算与 OVITO 依赖。 `rendering/` 中，`ovito_rendering.py` 负责通用结构渲染、可选的参考帧位移箭头及 JSON 记录； `gb_plan_view.py` 是独立的 OVITO subprocess 入口，负责沿 Y 法向的平面晶界定位、两侧相邻原子层选择、XZ 俯视渲染，以及诊断 JSON 和 Y 向分布图。两种渲染都将已索引产物写入 `artifacts/analysis/render/{run_id}/`。通用 top view 根据已显示原子的 XY 位移投影缩放箭头，并沿 +Z 偏移；晶界 plan view 根据 slab 的 XZ 投影缩放，并沿 +Y 偏移。`stress_strain/model_view.py` 为 `stress_strain_analysis` 与 `plot_stress_strain_comparison` 生成附在 `RunToolResult` 上的有界 `model_view`（每个事件一行、每条曲线一行，最多 15,000 字符），完整结果保留在 run 的 JSON artifact 和归档回复中。

扩展通过 [extensions/specs.py](../../src/extensions/specs.py) 声明。 [extensions/loader.py](../../src/extensions/loader.py) 收集内建声明（`extensions.core` 与各 `domains.<d>.extension`）和 `GBCLAW_EXTENSIONS` 中列出的受信任模块（仍兼容旧名 `GBCLAW_MEMORY_EXTENSIONS`），校验引用关系，并在首次使用时冻结注册表，然后才构造存储 schema 和工具。记忆公共实现集中在 `project_memory/`，面向 agent 的记忆工具在 `agents/memory/`；领域计算与专属 fact builder 仍保留在对应领域包。接入方式见[扩展开发指南](extension-guide.md)。

## 当前架构一眼看懂

Orchestrator 协调研究任务，将范围明确的子任务交给专业 worker。每个 worker 接收相关证据、调用领域工具，并返回带有可查询引用的结果。完整调用过程见 [Agent 运行时](agent-runtime.md)。

当前实现保持以下约定：

- 用户只直接接触 orchestrator。
- orchestrator 使用结构化 semantic/artifact access、可选 web search、领域为其声明的工具（`orchestrator_tools`：coding 环境目录与势函数目录），以及从 `WorkerDefinition` 声明装配的 delegate。其能力路由小节按注册表顺序由各 worker 的 `routing_guidance` 拼装。
- 内建 delegate 为 `delegate_plan`、`delegate_csl`、`delegate_lammps`、`delegate_analysis`、`delegate_coding`。[workers/lifecycle.py](../../src/agents/workers/lifecycle.py) 统一提供 invocation identity、召回、记忆工具、结论持久化与 task-state 生命周期；planner 位于 `agents/planner/`，各领域包负责业务工具与 worker。
- 路由、计划门禁、工具分类，以及 Web UI 的 delegate 标签和斜杠命令读取同一份 worker/capability 声明，扩展领域不再增加中央 agent 名称名单。[WorkerOutcome](../../src/agents/workers/outcome.py) 校验终态与证据引用，未知状态不能被当成成功。
- chat stream 事件只在 [core/stream_events.py](../../src/core/stream_events.py) 定义一份 schema，API、CLI、chat trace 和 Web UI 共用。转发到 orchestrator stream 的 worker 事件带有 `parent_id` 与 `parent_name`。
- Planning 先持久写 approval-required draft 和 pending pointer；用户显式批准后才原子地 activate/revise 版本化 plan。之后计划内科研 delegate 自动更新 step 边界，精确进度可跨 turn 搜索。
- worker trace 只在永久 Chat history 保留一份；科研工具直接写稳定 runs 和 manifest artifacts。
- semantic memory 与永久 Chat 分离；LangGraph checkpoint 是可重建 runtime state。worker graph 按任务临时创建。
- facts 通过 `source_type` 保留工具结果、对话来源、agent 报告或工作流状态的区别。对话提取继续保留，查询过滤、worker handoff 和分来源摘要防止将解释混同为计算结果。
- 科研工具在计算前绑定记忆策略。[ToolRunSession](../../src/project_memory/publication/session.py) 提供 artifact 登记、显式发布与完整结果引用；[RunService](../../src/project_memory/publication/service.py) 预检受信任 hooks，保存协议版本与策略指纹。恢复要求策略精确匹配，不重跑科研计算。

核心请求流：

```text
FastAPI / CLI
  -> interfaces.turn.run_turn()（两个客户端共用的单轮管线）
  -> prepare_project_session()（恢复执行状态，再只读构造记忆上下文）
  -> dynamic project-memory prompt + context editing / session summarization
  -> orchestrator LangGraph
  -> 注册表装配 delegate -> 公共 worker 生命周期 -> 领域工具
  -> ToolRunSession / RunService -> manifest artifacts 与原子 fact 发布
  -> 完成永久 Chat history（interfaces.recording）
  -> 完成后提取 facts，并按策略刷新 SQLite semantic summary 和 Markdown view
```

## 运行时数据位置

源码在 `src/`，运行数据在仓库根目录的 `projects/`（可用 `GBCLAW_PROJECTS_ROOT` 覆盖）：

```text
projects/{project_id}/
├── project.json
├── plan.md                  # 文件浏览器可见的 active-plan 生成视图
├── chat/
│   ├── history.sqlite      # 权威 Chat history 与 trace
│   └── transcript.md       # 原子生成的人类可读视图
├── memory/
│   ├── index.sqlite         # semantic records、搜索索引与权威 project summary
│   ├── summary.md           # 生成的人类可读视图
│   ├── summary_archive/     # 历史摘要的生成视图，权威记录保存在 SQLite
│   └── plans/{plan_id}/     # 派生的 current 与不可变 revision JSON 视图（草稿在 plans/drafts/）
├── runtime/
│   ├── checkpoints.sqlite    # 全部 thread，按 thread_id 分区
│   ├── tool_outputs/         # read_tool_output 读取的完整工具回复归档（含 worker 压缩溢出）
│   └── dichromatic_requests/ # 未完成的 DichromaticMap 交互选胞请求
└── artifacts/
    ├── structure/{run_id}/
    ├── structure/dichromatic_map/{run_id}/
    ├── lammps/cfg_convert/{run_id}/
    ├── lammps/minimize/{run_id}/
    ├── lammps/structure_minimize/{run_id}/
    ├── lammps/energy/{run_id}/
    ├── lammps/shear/{run_id}/
    ├── lammps/neb/{run_id}/
    ├── analysis/stress_strain/{run_id}/
    ├── analysis/neb/{run_id}/
    ├── analysis/gb_periodicity/{run_id}/
    ├── analysis/render/{run_id}/
    └── code/{run_id}/
```

各工作流的 artifact 目录在对应领域的 `extension.py` 中声明。`projects/` 是独立于 `src` 的 runtime workspace。Chat 用于历史回放；agent 从结构化 runs/facts 和 manifest artifacts 检索科研状态。
