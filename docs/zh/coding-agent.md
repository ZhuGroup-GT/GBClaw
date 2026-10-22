# 临时 Coding Agent

Coding agent 用来补充科研流程中的小型需求，例如解析新的输出格式、整理数据、自定义比较表，以及编写可复现的分析脚本或绘图程序。Orchestrator 通过 `delegate_coding` 调用它；如果 Structure、LAMMPS 或 Analysis 已有工具覆盖该任务，就优先使用现成工作流。

本页说明这个 worker 能访问什么、如何选择 Python 环境，以及有用的结果怎样成为持久的项目证据。系统整体的职责划分见[架构与设计逻辑](architecture.md)。

## 设计逻辑

新的科研问题经常需要预先编写的工具尚未覆盖的分析。自定义代码提供这种灵活性，固定工具则让重复计算保持一致。因此，coding agent 接收范围明确的任务、准确的输入引用和预期产物，而不是直接接管整个仓库或研究项目。

Worker 是临时的，证据需要长期保留。每次调用创建新工作区，避免前一次任务留下的文件和状态悄悄影响后一次计算；有用的脚本和产物经过明确的验证、发布，并保留来源关系。这样即使 worker 已经结束，后续 agent 和研究者仍能检查、复用结果。

执行检查可以回答“脚本是否运行成功”“表格是否包含要求的字段”等问题。物理解读还取决于模型、假设、数值方法和研究者审阅。当某种新分析开始被反复使用时，应审阅并整理为正式工具，让同一方法不必在每次任务中重新生成。

## 一次典型任务

例如，研究者希望根据几个已完成的剪切计算生成自定义比较表：

1. Orchestrator 找到准确的源文件引用，调用 `list_coding_environments` 检查合适的已安装环境。
2. 委派具体的数据处理任务，给出输入引用、预期输出文件和计算资源预算；宿主将核验后的输入快照放到 `/inputs`。
3. Worker 在 `/workspace` 编写并测试脚本；需要更多证据时，只能申请委派范围内已授权的输入。
4. `publish_coding_result` 重新运行声明的验证命令，检查选定产物，并登记发布文件及其来源。
5. Orchestrator 收到精简结果和持久引用，随后临时工作区被清理。

[运行环境注册表](#运行环境注册表)包含可选的 `ase` 别名。选择别名不会安装 ASE；对应 Conda 环境和需要的包必须预先存在于宿主上。

## 生命周期与安全边界

每次调用创建新的 Deep Agents 执行图和内存 checkpointer，后者只保存本次调用的执行状态。不会启用 Deep Agents 的 `memory`/`AGENTS.md`、skills 或嵌套通用子 agent。执行图在调用结束后销毁；需要长期保留的信息交给下文介绍的 GBclaw 项目记忆系统。

相关代码位于 `src/domains/coding/`：`agent.py` 构建 Deep Agents 执行图，`middleware.py` 是它的上下文压缩和步数预算中间件，`delegate.py` 定义 `delegate_coding`，`run.py` 中的 `CodingRunSession` 负责 run 记录、输入快照、验证和发布，`sandbox/` 包含沙盒契约及其 Bubblewrap 实现（后者同时施加单进程 `prlimit` 限制），`limits/` 包含进程树限制（采样监测或委派的 cgroup v2）和可信的 cgroup launcher，`environments.py` 和 `environments.toml` 是运行环境注册表，`orchestrator.py` 构建 `list_coding_environments` 并与 delegate 共享注册表、配置和 slot 资源池，`resources.py` 是 compute slot 资源池，`budget.py` 是任务截止时间，`prompt.py` 保存 worker 提示词和 orchestrator 路由指引，`extension.py` 声明该领域（workflow、`publish_coding_result` memory spec、worker、工具标签），`settings.py` 读取 `.env` 中的限制。

执行图使用 `BubblewrapSandboxBackend`，而不是 `LocalShellBackend`：

- `/inputs`：由 host 校验并复制的 completed-run artifact，只读；
- `/workspace`：唯一可写工作区；
- `/tmp`：临时编辑与执行缓存；
- `/opt/gbclaw`：只读挂载当前 Python 环境；
- `/opt/runtime`：只读挂载 host 选定的 allowlisted Conda 环境，并置于 `PATH` 首位；
- 仓库、项目数据库、manifest、published 目录、用户 Home 和 host 进程均不可见；
- 外部网络始终禁止；串行沙盒由 seccomp 拒绝 socket，显式获批的本机 MPI 只在全新的 Bubblewrap network namespace 内使用 socket。host 无法创建 namespace 时会 fail closed；
- command 启动前会丢弃全部 Linux capabilities；
- 命令受 wall time、单进程 CPU/地址空间/文件大小/open-file 限制，以及按 UID 计数的进程数限制、输出和工作区配额约束；必须存在 `prlimit`。进程树限制使用可配置的 cgroup v2，或明确标识的采样监测后备方案。
- Deep Agents graph 另有独立 recursion limit（`GBCLAW_CODING_RECURSION_LIMIT`，默认 200）。剩余约 8 个工具轮次时，每次模型请求都会附带“立即发布”的提示，因为到达上限时未发布的工作会全部丢弃。

`CodingRunSession` 只使用 `src/domains/coding/sandbox/base.py` 中的 `CodingSandbox` 契约（未提供 factory 时构建 `BubblewrapSandboxBackend`）。它是 Deep Agents 的 `SandboxBackendProtocol`，shell 和文件操作只看到 `/inputs`（只读）、`/workspace` 和 `/tmp`，并额外提供：

- `publication_guard()`：追加输入以及验证、发布产物期间，排除并行的 shell/文件修改；
- `seal()`：持久发布后拒绝之后的一切修改；
- `shutdown()`：停止运行中的命令并释放资源，重复调用不产生效果；
- `resource_record()`：实际执行的资源限制，写入 run 记录；
- `command_records`：按顺序记录每条已执行命令；
- `session_key` 与 `task_budget` 属性，分别用于取消和任务截止时间。

内置实现是 `BubblewrapSandboxBackend`（`src/domains/coding/sandbox/bubblewrap.py`）。`CodingRunSession.prepare(sandbox_factory=...)` 与 `make_delegate_coding_tool(sandbox_factory=...)` 可接收另一个 `CodingSandboxFactory`；调用时以关键字参数传入该 run 的 sandbox ID、输入/工作区/临时目录、coding 配置、选定环境、资源分配、会话 key 和任务预算。为 orchestrator 组装的 `delegate_coding` 使用默认的 Bubblewrap 实现。上面列出的挂载、网络策略和资源限制都属于该 backend；替换的 backend 必须自行提供隔离。

取消与具体的执行及其所属 API worker 绑定。即使 `/chat/stop` 到达另一个 worker，共享的取消信号也会让正在执行的 worker 终止其命令；资源等待、模型/工具调用和沙盒命令都检查同一信号。活跃 run 先标记为 cancelled，backend 在 worker 的 `finally` 中关闭后再删除工作区。较早一次执行发出的取消不能作用于之后的轮次。

整个任务共用一份按经过时间计算的预算，从等待资源之前开始，覆盖准备、等待模型、命令执行、编辑和发布。超时监测会在预算耗尽时关闭沙盒，因此迟到的模型调用不能再发布结果。阻塞的模型请求可能仍在后台线程中等待传输超时，但它的工具访问权限已被撤销。已经发布的产物不会因为最后一条模型回复超时而被删除。

## 按需补充输入

`delegate_coding.input_grants` 在委派时声明当前项目的精确 completed-run 输入范围：

```json
[{"run_id": "exact-completed-run", "roles": ["stress_strain_data"]}]
```

可用 `artifact_ids` 限定具体文件；同时提供两项时必须同时匹配。拒绝空范围和通配符。不声明 grants 时，只允许最初 staged 的文件。Worker 调用 `request_input_artifacts(references, purpose)` 后，host 核对源文件身份/hash、累计文件数和 bytes，追加只读快照；重复申请复用已有快照。每次追加保存来源关系、请求证据，并更新 run fingerprint。最终发布会再次核验输入快照的 hash。

范围外请求返回 `input_scope_required`，准确引用写入 run progress 并回交主 Agent。工作区在当前 invocation 内保留，结束时仍清理；跨 invocation 的草稿导出/续作是独立能力。追加输入发生部分登记失败时禁止该 run 完成，避免证据关系不完整的结果进入记忆。

## 进程树资源限制

`GBCLAW_CODING_RESOURCE_MODE` 支持 `auto`（默认）、`monitor`、`cgroup`：

- `auto`：有可用的 `GBCLAW_CODING_CGROUP_ROOT` 时使用 cgroup，否则记录原因并使用 `proc_monitor`。
- `monitor`：每 0.25 秒采样整个进程树的 RSS、累计 CPU 时间和进程/线程数，发现超限即终止。共享页可能重复计数，短命进程可能漏采；该模式不提供内核 CPU 带宽上限。
- `cgroup`：必须具有可用委派；限制无法执行时，每个 coding 沙盒都无法启动（委派失败），没有监测后备方案。

Host 提供 `/sys/fs/cgroup` 内的绝对委派目录，其 `cgroup.subtree_control` 需启用 `cpu`、 `memory`、`pids`。每个任务创建独立子组，由可信 launcher 在执行任何 worker 命令前加入， fork 的后代继承该组。`memory.max`、`memory.swap.max=0`、`pids.max`、`cpu.max` 约束内存、 swap、进程/线程数量和 CPU 带宽；CPU 配额跟随 compute slots，累计 CPU 时间跨命令计费。清理使用 `cgroup.kill` 终止整组。接口语义依据 [Linux cgroup v2 文档](https://docs.kernel.org/admin-guide/cgroup-v2.html)。

请求上下文和成功/失败 run 都记录实际 mode、是否具备内核限制、观察到的峰值和后备原因。资源准入池仍局限于单个服务进程，本批不引入多服务器统一资源池。

## 运行环境注册表

运行环境在宿主维护的 [`src/domains/coding/environments.toml`](../../src/domains/coding/environments.toml) 中配置。可通过 `GBCLAW_CODING_ENVIRONMENTS_FILE` 指定自定义注册表；相对路径以仓库根目录为基准解析。Orchestrator 选择 `current`、`ase` 等已注册别名，不能传入任意宿主路径。`list_coding_environments` 返回环境是否可用、允许的能力以及常用已安装包的版本；还根据 Conda history 和已安装包的元数据生成环境指纹，因此 pip 包变化也会改变指纹。返回值不暴露宿主路径。

| Alias | 用途 | Capability |
| --- | --- | --- |
| `current` | 服务当前使用的 Python 环境，用于绘图、后处理 | 无 |
| `lammps` | 小型自定义串行或受限本机 MPI LAMMPS 验证 | `lammps`, `mpi` |
| `ase` | 使用 Atomic Simulation Environment 的小型原子模拟脚本与后处理 | `ase` |

这些是稳定别名，不是 Conda 环境名。默认别名 `current` 跟随运行服务的 Python 环境；`lammps` 从 `GBCLAW_LAMMPS_CONDA_ENV` 读取实际环境名，默认是 `GBclaw-lammps`。`ase` 从 `GBCLAW_ASE_CONDA_ENV` 读取实际环境名，默认是 `GBclaw-ase`。实际环境改名无需修改别名，重启服务后生效。

ASE 作为可选 coding 运行环境接入，不新增 specialist 工作流。宿主安装对应 Conda 环境和 ASE 后，orchestrator 先调用 `list_coding_environments`，确认 `available=true` 且 `packages` 包含 `ase` 版本，再调用 `delegate_coding(environment="ase", ...)`。Worker 直接使用 `/opt/runtime` 对应的 `python`，不需要在沙盒内执行 `conda activate`。未安装的环境仍在目录中显示为不可用，不影响 GBclaw 启动；缺包时报告依赖缺失，不自动安装或切换运行环境。 ASE 不授予 LAMMPS/MPI 权限，具体 calculator 可能还需要额外安装软件或提供数据。

只有 delegate 显式请求 `mpi_ranks > 1`、环境具备 `mpi` capability 且 ranks 不超过 compute allocation 时才开放 MPI。构型生成、minimization、shear、NEB 和正式 MPI 作业仍必须走 `delegate_lammps`，以保留领域 provenance。未来增加环境时添加一个 TOML table 并重启 GBclaw 即可。

```toml
[environments.fenicsx]
conda_env = "fenicsx"
description = "Finite-element post-processing environment."
capabilities = []
```

也可以在宿主配置中使用 `prefix = "/absolute/read-only/prefix"`，但这个路径不会成为 `delegate_coding` 可传入的参数。注册环境只负责让范围明确的自定义任务使用已安装依赖；新增经常运行的科研工作流，还需要接入工具、验证和发布规则，详见[扩展开发指南](extension-guide.md)。

## 总资源准入

同一 GBclaw 进程内的所有 orchestrator graph 共用一个按权重计费的 coding resource pool。单任务默认申请 4 个 compute slots，总预算默认 36，分别由独立环境变量 `GBCLAW_CODING_AGENT_COMPUTE_SLOTS` 和 `GBCLAW_CODING_TOTAL_COMPUTE_SLOTS` 控制，不复用 LAMMPS agent 的变量。多个无依赖 coding delegation 可以并发；申请量之和装不进剩余预算的调用会等待，成功、失败和取消都会归还 lease。

Orchestrator 可调用 `delegate_coding(compute_slots=..., mpi_ranks=...)` 分配单沙盒资源。 `mpi_ranks` 默认 1 且不能超过 `compute_slots`；只传 `mpi_ranks` 时 compute allocation 与之相同。MPI launcher 必须写明不超过 host allocation 的字面量 `-n`/`-np`。 `list_coding_environments` 会同时返回环境 capabilities 和静态资源上限。

## 项目持久化

```text
projects/{project_id}/artifacts/code/{run_id}/
├── manifest.json              # 由 host 控制
├── input.json                 # 任务、精确输入引用、预期输出、输入授权、沙盒策略、运行环境、资源分配
├── input_request-*.json       # 每次 request_input_artifacts 追加一条（目的 + 已暂存的输入）
├── inputs/                    # 输入证据快照
├── published/                 # 通过验证并显式发布的脚本/数据/图/报告
├── workspace/                 # worker 存活期间才存在
└── .sandbox-tmp/              # worker 存活期间才存在
```

`publish_coding_result` 接受一个或多个输出文件以及 1–5 条验证命令。GBclaw 在同一沙盒中重新运行这些命令，拒绝失败验证、路径穿越、符号链接、目录、重复路径或超额文件，把选中的普通文件经 SHA-256 校验的原子替换复制进 `published/`，写入 manifest/SQLite artifact index，并完成 `code` run。输入快照同样会在沙盒启动前按源 hash 校验。无论成功、失败或取消，`workspace` 和 `.sandbox-tmp` 都会在 `finally` 中删除。worker 未发布、且 host salvage 也未按声明的 `expected_outputs` 保存的工作区文件，不会进入项目记忆。

如果 worker 没有成功发布就停止（正常退出、撞到步数上限或出错），并且任务没有被取消、超时或在等待更多输入授权，host 会在删除工作区之前检查声明的 `expected_outputs`。所有必需输出都存在且通过约定检查时，host 只发布这些声明过的文件，以 `publication.mode="host_salvage"`、`validation.worker_validation_run=false` 完成 run，并把这次委托报告为 `partial`：文件已持久保存，但 worker 从未验证或总结过它们。

`delegate_coding.expected_outputs` 可使用确切工作区文件名，或结构化契约：

```json
[
  {"path": "data.csv", "role": "data", "columns": ["strain", "stress"], "min_bytes": 1},
  {"path": "report.json", "role": "report", "required_keys": ["summary"]},
  {"path": "plot.png", "role": "plot"}
]
```

`required` 默认 true；`/workspace/` 路径规范为工作区相对路径。发布前拒绝路径穿越、重复、必需文件缺失、角色错误和空的永久产物。Host 独立检查 JSON 语法及非有限常量、CSV 表头及行宽、PNG/JPEG 编码和尺寸、Python 语法；`columns` 和 `required_keys` 增加 CSV/JSON 字段要求；required key 可以是顶层 key，也可以是穿过嵌套对象的点路径（`pairs.P1`），拒绝时会列出所有缺失的列或 key。发布被拒绝后可在同一 run 内修正并重试。 Worker 验证命令仍需通过，但命令成功和格式有效并不单独证明科学正确性。发布记录分别保存 `output_contract_passed`、`format_checks` 和 `scientific_checks_applied`。

## 长期记忆

与其他 worker 一样，coding worker 获得有大小限制、针对当前任务整理的已完成 run 与 fact 信息，以及只读语义查询和搜索工具。文件内容只能通过明确提供的输入快照访问。

持久记录沿用项目记忆的层级：

1. `runs`：`code` 执行记录保存任务、来源、环境指纹、计算资源与 MPI 分配、状态、验证及结果摘要。
2. `artifacts`：`input.json`、来源快照、输入请求记录以及已发布产物都会写入 manifest 并索引。
3. `facts`：`code` run 完成时（无论由 worker 发布还是 host salvage），会确定性写入 `scientific_conclusion` fact `run:<run_id>:main_conclusion`（摘要、任务、预期输出、已发布产物、验证记录）。记录的验证未通过时，builder 会拒绝该 run。经 salvage 的 run 其记录保留 `worker_validation_run=false`。
4. `memory/summary.md`（`semantic_summary` 记录的视图）：增量摘要按相关性保留 run ID、来源关系、验证和产物用途。

Agent 调用结束后，能够复现和审阅其结果的证据仍保留在项目中。
