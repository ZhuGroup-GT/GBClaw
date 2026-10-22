# Project 存储、历史与语义记忆

[English](../project-memory.md) · [架构与设计](architecture.md) · [Agent 运行机制](agent-runtime.md) · [扩展开发指南](extension-guide.md)

GBClaw 把下一步决策需要的信息放在容易读取的位置，同时保留核查结果来源所需的完整记录。聊天历史、科研记录、执行状态和科学文件承担不同职责。本指南先解释这些职责，再介绍各层的存储和查询接口。

记忆公共实现集中在 `src/project_memory/`。领域包通过 `DomainExtension` 声明 workflow、fact、 tool、worker 与访问策略，`src/extensions/` 中的注册表将这些声明组合为经过校验的不可变词表。新增工具或 subagent 的标准接入方式见[扩展开发指南](extension-guide.md)。

## 设计逻辑：为什么记忆需要分层

一次长期研究会积累大量候选结构、计算参数、轨迹、分析结果，以及关于下一步该做什么的讨论。每次请求都把这些内容全部交给模型，会淹没当前决策所需的信息，也容易混淆建议与计算证据。但如果只保存一份摘要，又会失去复现结果或质疑结论所需的细节。

论文提出的五层 agent 记忆就是为了解决这一矛盾。当前实现将它们对应到提示词、有选择的项目摘要、对话状态、可查询的科研记录，以及实际文件：

| 层次 | 回答的问题 | 信息来源 |
| --- | --- | --- |
| 领域知识 | 这类任务应该怎样完成？ | 系统提示词、工作流规则和科研工具。 |
| 项目摘要 | 现在最值得关注的研究进展是什么？ | `memory/index.sqlite` 中有大小上限的工作视图，保留当前目标、约束、未完成事项和关键发现。 |
| 对话状态 | 我们正在讨论什么，还有什么需要回应？ | 当前线程 checkpoint 中的近期消息和精简 session summary。 |
| 科研记录 | 哪项结果、决定或运行支持这个说法？ | `memory/index.sqlite` 中的事实、运行、来源关系和产物索引。 |
| 科学产物 | 实际使用了哪些输入，又得到了哪些输出？ | 已登记的配置、结构、轨迹、日志、脚本和分析文件。 |

完整聊天和工具轨迹另行归档，供人类回看。完成轮次中的目标、偏好和解释可以按策略提取，但始终保留 `conversation` 来源。保存了一句话，或保存了一份 worker 报告，并不等于得到了一项计算结果。

论文说明的是概念层次；当前代码用 SQLite 保证记录的事务一致性，用文件保存科学产物和可读导出。因此，Markdown 摘要是数据库内容的视图，不是第二份数据库。两个 `session.py` 也由此分工：`agents/middleware/session.py` 维护线程内的对话连续性，`project_memory/snapshot/session.py` 为当前请求读取项目记忆快照。

来源信息使结果可以追溯，但不自动证明物理正确性。复用结果前，agent 和研究者仍需核对模型、计算条件、收敛情况，以及它是否适用于新问题。

## 摘要里没有的细节，应该去哪里找

例如，某次旧剪切计算不再出现在当前摘要中，并不意味着它被删除，也不意味着系统没有计算过。

1. 根据晶界、计算类型或科研问题搜索项目记忆，得到候选记录和稳定 ID。搜索排名用于发现记录，不代表对科学正确性的判定。
2. 精确读取对应 run 或 fact，确认状态、来源类型、输入和关联记录。需要工具发布的计算事实时，使用 `source_type="tool_result"`。
3. 列出该 run 的产物，读取相关报告或元数据。结构和轨迹由下游科研工具按 run ID 和 artifact role 解析，不必把文件全文放进模型提示词。
4. 如果要了解某个历史阶段如何概括研究进展，可按历史摘要 ID 读取，或搜索摘要归档。采取行动前，再对照当前记录核实旧说法。

当前摘要会明确说明内容经过选择，并附上小型历史索引，包含归档 ID、主题提要和查询方法。索引同样有长度限制，较早记录仍可搜索。具体调用见 [Agent 查询工具](#agent-查询工具)，保留与压缩规则见 [Semantic Summary 与短期 Context](#semantic-summary-与短期-context)。

项目和对话线程是两个不同范围。即使隔了很久，重新打开同一线程仍可恢复它保存的对话状态；时间间隔本身不意味着必须新建线程。同一项目的不同线程共享科研记忆，但各自保留独立的对话 checkpoint。

## 源码组织与扩展边界

除给出 `src/` 路径的条目外，下列子包都位于 `src/project_memory/`。

| 子包 | 职责 | 主要入口 |
| --- | --- | --- |
| `src/extensions/` | 声明类型、宿主词表、加载、引用校验与冻结 | [specs.py](../../src/extensions/specs.py)、[loader.py](../../src/extensions/loader.py)、[core.py](../../src/extensions/core.py) |
| `storage/` | semantic runs/facts/artifacts、搜索、结论、task cursor 与 tool-output 归档；`MemoryRepository` facade 按表组合 mixin（schema、changes、runs、facts、retry queue、artifact index、search），并加入 `summary/` 的摘要存储 | [repository.py](../../src/project_memory/storage/repository.py) |
| `run_artifacts/` | 每个 run 的产物目录与 manifest；所属聊天轮次被取消时清理对应 run | [store.py](../../src/project_memory/run_artifacts/store.py)、[cleanup.py](../../src/project_memory/run_artifacts/cleanup.py) |
| `publication/` | 绑定工具运行、产物验证、fact 发布、受信任 validator 与恢复 | [service.py](../../src/project_memory/publication/service.py)、[session.py](../../src/project_memory/publication/session.py) |
| `extraction/` | 对话提取、规范化 fact key、证据策略与持久重试 | [extractor.py](../../src/project_memory/extraction/extractor.py)、[policy.py](../../src/project_memory/extraction/policy.py) |
| `snapshot/` | 只读项目记忆快照与 preview contract | [session.py](../../src/project_memory/snapshot/session.py) |
| `summary/` | 项目摘要策略、分批、生成、验证、存储、历史归档与 Markdown 视图 | [包说明](../../src/project_memory/summary/README.md)、[service.py](../../src/project_memory/summary/service.py) |
| `src/agents/memory/` | worker 召回、记忆工具装配、报告持久化与结果投影；终态位于 `src/agents/workers/` | [worker.py](../../src/agents/memory/worker.py)、[outcome.py](../../src/agents/workers/outcome.py) |
| `src/agents/memory/tools/` | 面向模型的 query/search、artifact、结论、tool-output 与 task-state 工具 | [query.py](../../src/agents/memory/tools/query.py) |
| `prompts/` | 对话事实提取提示词构造 | [extraction.py](../../src/project_memory/prompts/extraction.py) |

`project_workspace/` 负责 workspace metadata、永久 Chat、checkpoint 与文件访问，按职责分为 `chat/`、`artifacts/`、`execution/` 和 `persistence/`。 Project plan 归 `src/planning/`：计划模型、需审批的草案与计划服务、派生计划视图、规划模式和决策授权。聊天和产物模型归各自子包，记忆专属模型在 `project_memory/models.py`；详见 [工作区源码说明](../../src/project_workspace/README.md)。科研算法和领域 fact builder 留在对应工具模块。

Loader 收集 `extensions/core.py` 的宿主词表、各内建领域的 `domains/<domain>/extension.py`，以及 `GBCLAW_EXTENSIONS` 中列出的受信任模块（仍读取旧名 `GBCLAW_MEMORY_EXTENSIONS`），再校验重复名称、writer 归属和引用关系，最后冻结注册表。注册表在首次使用时构建，最迟在存储模型导入并派生 workflow 与 fact 词表时完成。扩展模块从 `extensions.specs` 导入声明类型，并导出 `MEMORY_EXTENSION`。修改启用模块后需要重启进程，不能通过运行中修改注册表来更新已经生成的 schema。

`WorkerDefinition`、`AgentMemorySpec` 和 `ToolCapabilitySpec` 统一描述路由、召回与 artifact 权限、执行性质和规划行为。[create_worker_delegate() 公共生命周期](../../src/agents/workers/lifecycle.py) 负责绑定项目与 invocation identity、准备记忆工具及 handoff、执行领域 worker、验收完成结果、保存结论与 task cursor。Orchestrator 和计划门禁读取同一份声明。新 worker 只需通过扩展提供自身 factory、tool provider 和策略，无需修改 repository、公共查询工具或增加独立的 agent 名称名单。

## Workspace 目录

`src/project_workspace/store.py` 中的 `ProjectStore` 管理 `<repo>/projects`（可用 `GBCLAW_PROJECTS_ROOT` 改变位置）。一个已有内容的项目目录如下（`create()`/`initialize()` 只创建 `project.json`、`chat/history.sqlite`、`memory/index.sqlite`、`memory/summary.md` 以及空的 `artifacts/` 和 `runtime/`）：

```text
projects/{project_id}/
├── project.json
├── plan-draft.md           # 仅在等待批准时生成
├── plan.md                 # active plan 的生成视图
├── chat/
│   ├── history.sqlite
│   └── transcript.md       # 每轮结束后或浏览文件时导出
├── memory/
│   ├── index.sqlite
│   ├── summary.md
│   ├── summary_archive/    # 替换非空摘要后生成
│   │   ├── index.md
│   │   └── {archive_id}.md
│   ├── conclusions/
│   │   ├── {conclusion_id}.md
│   │   └── {conclusion_id}.json
│   └── plans/
│       ├── drafts/{draft_id}.json
│       └── {plan_id}/
│           ├── current.json
│           └── revisions/revision-NNNN.json
├── runtime/
│   ├── checkpoints.sqlite
│   └── tool_outputs/{output_id}.json
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

`projects/` 是 git-ignored 的科研数据；只有配置或启动 workflow 时才创建对应 run 目录。

`ProjectStore.get()` 和 `list_projects()` 只读 metadata，不再重写 `project.json` 或初始化数据库。新建项目由 `create()` 初始化；已有工作区的迁移和缺失 metadata 的创建使用显式 `initialize(project_id)`。损坏的 metadata 报错并保留原文件，不重置项目身份。元数据更新采用原子替换与项目外的跨进程锁，删除使用同一把锁，避免写入与删除竞争。

Structure run 保持 workflow 标识 `csl`，产物写入 `artifacts/structure/{run_id}/`。

## 永久 Chat 历史

这份归档用于回顾说过什么、任务如何展开。需要确认项目当前记录的结果时，应读取语义记忆和科学产物。两者并存，既保留研究过程，也避免把每句对话都作为日常 agent 召回的依据。

`ChatRepository` 将完整记录存入 `chat/history.sqlite`：

- `turns`：按顺序保存 user/assistant content、assistant reasoning、status 和时间戳。
- `turn_order`：跨 thread 的不可变项目级入场顺序，旧 turn 按 rowid 顺序一次性回填。对话事实的新旧比较使用这些位置，而不是时间戳。历史 API 按 `sequence` 排列单个 thread 的 turn；`chat/transcript.md` 按 `created_at`、thread 和 sequence 列出 turn。
- `chat_requests`：API 请求指纹、执行 owner、绑定的 assistant turn、终态结果和提前取消记录；不属于 semantic facts。
- `trace_events`：按顺序保存 reasoning/tool event、嵌套关系以及完整 tool input/output；当供应商报告了未公开的 reasoning 时，还会保存 `reasoning_withheld` 记录（`input_json` 中为 `reasoning_tokens`）。这类记录立即写入，不会并入该轮的 `reasoning`，在 transcript 中显示为 “Reasoning withheld”。

聊天、reasoning 和 trace 在独立归档中永久保留，不会把整个历史归档直接装入 agent context 或 `summary.md`。已完成轮次的最终 user/assistant 正文可以支持经策略校验的对话事实，但查询、召回和摘要必须保留其对话来源。隐藏 reasoning 与原始 trace 不参与事实提取。

`ChatTraceRecorder` 不会把每个 reasoning delta 单独写入数据库，而是按 parent/label 分别维护内存 segment，因此并行 worker 可以交错输出而不会互相切碎。匹配的 tool boundary 只封闭该 parent 的 segment；final answer/error 再封闭其余 segment。因此网页流式输出频率与数据库写入频率相互独立。

`trace_events.tool_call_id` 保存运行时 tool-call identity；worker trace row 以对应的 tool-start event 作为 `parent_event_id`，worker 名字只用于显示。已有数据库会增量添加这个 nullable column；旧 row 则继续通过按名字匹配的读取 fallback 渲染。

`chat/history.sqlite` 是权威数据源，聊天和 trace 实时入库，并在同一事务推进导出 revision。 `chat/transcript.md` 是人类可读 Markdown 视图，包含 turns 和可折叠 reasoning/tool trace。turn recorder（`interfaces/recording.py`）在把某轮记录为完成、失败、中断或暂停之后刷新它；浏览 chat 目录，或对 transcript 执行 stat、预览、下载或 raw 读取时也会刷新。由 recorder 之外的路径收尾的轮次（`/chat/stop` 接口，或下次请求时的孤儿恢复）会在下一次导出时出现。导出失败只记录日志，不会使该轮失败。系统把待处理变更合并为一次导出；无变更时复用文件。直接从磁盘读取的程序可先调用 `ChatRepository.ensure_transcript()`，或用 `rebuild_transcript()` 强制重建。手工修改会在下次重建时被覆盖，历史 API 始终直接读取 SQLite。

网页端按需加载：

```text
GET /api/projects/{project_id}/history?offset=0&limit=200
GET /api/projects/{project_id}/history/{turn_id}
```

分页接口接受可选的 `&thread_id=`（默认为项目当前 thread），只返回轻量 turn；只有用户展开某次历史执行时才读取 reasoning/trace detail。该详情响应中的 tool input/output 是有界 preview；权威 SQLite row 和生成的 Markdown transcript 保留完整值。停止请求会把当前 assistant turn 标为 `interrupted`，不会额外写 semantic record。

## Semantic Memory

这里的“语义记忆”指结构化、可搜索的项目知识。它同时记录计算结果和上下文解释，并明确区分来源；`fact` 是记录类型名称，不意味着每条内容都是经过实验验证的真理。

`memory/index.sqlite` 是 agent 可检索科研状态的 source of truth，包含：

- `runs`：CSL/structure、dichromatic map、CFG conversion、GB/structure minimization、GB energy、shear、NEB、stress--strain/NEB/GB-periodicity analysis、render，以及通过验证的通用 coding run 的稳定生命周期记录。
- `run_sources`：以 `(run_id, relation, ordinal)` 为键的规范化有序来源关系。
- `facts`：带稳定 lookup key、active/retracted 状态、乐观版本、source turn/run 和 evidence 分类的当前事实。
- `artifacts`：带 role、media type、size、metadata 和 provenance 的 manifest 索引。
- `semantic_summary`：正文、source revision/digest、format version、生成时间和 fact/run 计数的 singleton 权威记录。
- `semantic_summary_archives`：不可变的完整旧摘要与来源 metadata（UPDATE/DELETE 触发器会中止写入）。替换非空摘要时以 `archive_reason='replaced'` 原子归档；重置过时格式以便重建时以 `'format_reset'` 归档。
- `semantic_revision` 与 `semantic_changes`：monotonic revision counter 和按实体合并的 fact/run 变更队列，用于增量更新 working summary。
- `memory_search`：覆盖全部 fact value、run state/result 和 artifact metadata 的派生 FTS5 trigram 索引；`memory_search_state` 记录可重建索引的版本。
- `memory_retry_jobs`：持久保存 turn 后 extract/apply/summarize 的失败阶段和重试状态。
- `memory_schema`：schema 版本 4。

### 事实来源

返回的每条 `FactRecord` 都带有计算字段 `source_type`，由宿主根据已保存的证据和来源推导，调用者不能把对话解释自行改标为工具结果。`evidence_kind`、`source_turn_id` 和 `source_run_id` 仍保留，方便进一步核实。

| `source_type` | 含义 |
| --- | --- |
| `tool_result` | 科研工具发布的结果，保留对应 run 与 artifact 证据。 |
| `conversation` | 从对话提取的目标、选择、偏好及模型解释。 |
| `agent_report` | worker 报告或解释，带调用终态和支持性引用。 |
| `workflow_state` | 计划、审批状态和任务执行游标。 |

对话事实中的 `user_explicit` 表示用户说过，不代表经过独立测量；`completed_run` 表示解释有已核验的完成运行支持，不等于该解释由工具计算或发布。报告引用与计划批准也不能证明科研成功。复用前仍应核对准确的 run/artifact；completed run 也可能带有收敛警告。

模型不能直接写数据库。成功的 assistant 轮次结束后，系统先准备一组有大小上限的核对材料：项目固定信息槽位、与本轮已验证 run 关联的事实，以及用每条消息前 250 个字符通过 FTS5 找到的最多 10 条相关有效事实。候选总数最多 30 条（每个已验证 run 最多 20 条）。计划、agent 结论和任务状态事实被排除，超过 4,000 字符的值标记为省略。这些搜索结果供提取器识别已有内容、更新和冲突，不是事实可信度的排名。提取器只读取最终用户消息和回答、经过验证的精简运行记录，以及这些候选事实；不读取模型思考或工具轨迹。

模型只提出 `upsert`、`retract` 或 `noop` mutation。随后由确定性 policy 校验 canonical key、value 大小/深度、显式 user evidence、completed-run provenance 和 expected version，整批 mutation 再与 FTS 索引和 semantic revision 一起原子提交。同一来源的相同写入是 noop；较新来源的同值确认仍推进来源标记和版本。旧来源不能覆盖或撤回新事实，纠错前和存储事务内都检查来源顺序；同批中其他没有过时的合法事实仍可写入。Retract 只写 tombstone，不物理删除；普通 query、搜索、context 和 summary 默认只读 active facts。

Fact 保存当前值和用于并发检查的版本号，并不为每个旧值另建历史表；科学运行和产物证据仍保持不可变。Schema 或策略校验失败时，系统最多进行两次纠正提取，并提供有大小上限的错误反馈及最新事实版本。持续不合法的输入进入 `dead_letter`；存储冲突和模型传输错误仍可按计划重试，传输错误不消耗本地纠正次数。已保存的 apply 阶段任务也使用相同纠正流程。

Fact 词表由本进程启动时加载的声明确定。内建类别包括 `project_goal`、`user_decision`、 `loading_preference`、`csl_orientation`、`gb_crystallography`、`gb_identity`、`material_selection`、 `potential_selection`、`scientific_conclusion`、`project_plan`、`agent_conclusion`、`task_state`。扩展可声明新的类别及其 writer、证据和 key policy；聊天提取只能写 policy 允许的子集。例如 CSL orientation 仍由确定性 workflow 独占，project plan 由确定性 planning service 独占。Run ID 从结构化 tool input/output 收集，包括来源、参考、context 和批量 run ID，并在提供给 extractor 前通过项目 run index 验证。非法 kind、未提供的 run、failed run conclusion、把 assistant 建议当成 user decision，以及基于过期 fact version 的写入都会被拒绝。

CSL 在领域 recorder 中构造规范化 orientation fact，通过公共发布接口提交；稳定身份包含精确的 `(Sigma, rotation_m, rotation_n)` 分支、axis、basis 和 GB plane，因此相同 Sigma 的不同旋转不会互相覆盖。科研 workflow 也直接维护自己的 run 状态和结果，不依赖聊天提取。 CSL orientation 与晶界分类事实在所属 run 完成后提交；同一任务分析多个晶界时，按各自几何身份保存事实。工具返回的普通错误字符串不能注册为成功结果或进入结果缓存。

现有科研工具的完成路径均使用注册策略，支持一次提交一条结论或多条专属事实。CFG 转换和批量 structure minimize 为每个成功项分别保存来源帧、用途、约束和输出引用。整批完成时原子发布事实；失败批次保留已有逐项文件和结果，但不将其提升为 completed-run 证据。

`RunService` 在完成 run 前将确切事实保存为永久 artifact，再由 [publication/writes.py](../../src/project_memory/publication/writes.py) 的注册 writer 提交。Planning 与聊天提取的最终提交入口也使用该公共层，同时保留审批、用户证据与版本校验。发布重试只恢复已保存的事实，不调用科研计算或 LLM，也不覆盖更新或已撤回的内容。聊天提取不能抢占待发布工具事实的记忆位置。主 agent 和全部已注册 worker 共用注册驱动的记忆工具工厂，主 agent 的 summary 注入仍由现有 context middleware 负责。

Tool 与 workflow 声明受信任的 publication validator；同一策略覆盖 staging、恢复、直接确定性写入以及聊天提取引用的 completed-run 证据。Coding 必须记录 `validation.passed=true`，该要求由领域声明并在 [publication/validators.py](../../src/project_memory/publication/validators.py) 实现。公共 writer 同时检查绑定工具允许的 fact kind、来源身份、证据和 lookup-key 范围。

### 版本化 Project Plan

成功的 planning delegate 只会写等待批准的 proposal，不会直接创建 active plan：

- `plan_draft:<draft_id>` 保存完整拟议计划、create/revise action、base plan/revision、用户原始要求、decision 状态/反馈和最终批准结果。
- `project:pending_plan` 是当前待用户决定草案的唯一稳定 pointer。

只有用户显式批准后才会创建或推进可执行 fact：

- `plan:<plan_id>` 保存完整 objective、evidence/assumptions、有序 steps、dependencies、 capabilities、validation、状态、attempts、时间戳、结果摘要和关联 scientific run IDs。
- `project:active_plan` 是紧凑稳定指针，保存 active plan ID、整体状态、step counts，以及精确的 running/next/blocked step IDs。
- `plan:<plan_id>:revision:<n>` 是不可变的结构修订快照。结构 `revision` 与 fact 的 `memory_version` 分开计数；普通进度更新只推进后者。

active plan/revision、approved draft 和被 retract 的 pending pointer 使用乐观 fact version 在同一事务中提交。若 base structural revision 已变化，旧 revision draft 会被拒绝；reject 只关闭 pending pointer 并保留审计草案，不改变 active plan。FTS document 与 semantic revision 也在同一事务刷新，因此新计划和进度会立刻可搜索。Step 状态为 `pending`、`running`、 `completed`、`failed`、`blocked` 或 `skipped`，整体状态由精确 step/dependency 状态派生。新计划可以 supersede 没有 running step 的未完成旧计划，但不能静默替换仍在运行的计划。科研 delegate wrapper 会在 worker 边界更新计划步骤并关联发现的 run IDs。跨 turn 执行会先解析 active pointer 再读完整 fact，不依赖 Chat trace，也不把有界 working summary 当作权威进度。

用户要求修改现有计划时，orchestrator 会把完整修改要求和 existing plan ID 重新交给 planning worker。planning-only `revise_project_plan_draft` 会先读取精确计划与相关 memory，再提出带审计信息的修订。title/objective/evidence 字段以及 pending/blocked step 可以修改；running、completed、failed 或 skipped step 不能删除，也不能改写其定义，attempts、result、timestamp 和 run ID 都会保留。如果科研目标发生实质变化，应创建 new-plan draft。所有修改都必须再次批准；批准后才可能 supersede 没有 running step 的旧 plan。批准后的 plan 会记录修订理由、请求者、变更字段、之前的 memory version、source draft 和时间戳。

pending draft 会原子刷新根目录 `plan-draft.md` 和 `memory/plans/drafts/<draft_id>.json`。批准/拒绝后根目录 pending view 消失，但审计 JSON 保留。批准后的计划刷新 `plan.md`、current JSON 和不可变 revision JSON。SQLite 始终是唯一事实源；手工修改的文件会被覆盖。

pending pointer 在 Runtime Context 中优先级最高，其次是 active pointer 和 working summary。 dynamic prompt 每次 model call 前都会重读两者；独立 tool-call middleware 也会重读 pending 状态，在未决定时阻止 replacement planning、科研 delegate 和进度推进。只有最新 user message 明确授权、且没有指向别的 draft，approve/reject 才会放行。完整数据通过 `get_project_plan_draft` / `get_project_plan` 精确读取。

`run_sources` 允许同一 relation 关联同一父运行的多个帧，以 `(run_id, relation, ordinal)` 唯一标识各个输入，记录精确的 CFG 帧→转换→`structure_minimize` 来源链。每次普通最小化的必填 `purpose`、直接输入和原始帧都会进入可搜索记忆与 worker handoff。Memory schema V2 和 V3 数据库会自动迁移到 V4，并保留全部记录和来源关系。V2 之前或未知版本会被拒绝并返回明确错误，但不会自动删除数据库。

## 结构化 Runs

一个 run 对应一次可明确识别的科研操作。记录它的生命周期和来源，下游任务才能区分可复用的已完成结果、尚未执行的配置、失败尝试和正在进行的计算。这是把 agent 的任务指令连接到可复现科学证据的主要接口。

所有 run 使用同一生命周期：

```text
configured -> running -> completed | failed | cancelled
```

工具返回机器可读的 JSON（`publication/results.py` 中的 `RunToolResult`），包含 `run_id`、workflow、status、manifest、artifact records、compact result、error，以及工具提供时的有界 `model_view`；模型边界显示该视图而不是完整结果。配置与执行更新同一个稳定 run row。

`RunService.tool_run(memory_tool, parameters=...)` 返回绑定项目、工具和 run 的 `ToolRunSession`。工具在 `session.artifact_directory` 写入产物，通过 `session.register_artifact(...)` 登记，再调用 `session.complete(result=..., conclusion=...)` 或传入 fact batch；已注册 fact builder 也可从科研结果构造事实。[rendering/tool.py](../../src/domains/analysis/rendering/tool.py) 的 `render_atomic_structure` 已使用这套接口。

默认 `mode="facts"` 要求非空事实批次或结论；只有工具策略既不要求主结论、也没有 fact builder 时，才允许显式 `mode="artifacts_only"`。退出 context 时漏掉 completion 会将 run 标记为 failed；取消会记录 cancelled，并保留已登记产物。`session.result_ref` 返回项目/run/tool 身份、manifest、artifact 与 fact 引用、协议和完整结果的存储位置，模型摘要不替代原始科研结果。

需要先配置后执行的 workflow 保留 configure/start/complete 生命周期。创建时通过显式参数或 parameters 中的 `memory_tool` 绑定工具。所有已绑定入口在创建 run 产物前预检 fact builder、 model-result builder 和 publication validator 的导入、同步 callable 与签名，坏 hook 在计算前报错。完成时统一验证必需 artifact role，并先将确切事实保存为永久 artifact，再提交 completed；fact 索引失败不会撤销成功计算，待发布指针支持后续恢复。

每次待发布 payload 与 pointer 都保存 `contract_version` 和 `contract_sha256`。指纹涵盖 fact 归属、必需产物和 validator 路径等声明策略。恢复要求两者与当前工具策略精确一致；缺失版本、版本不同或策略漂移都会返回明确的兼容错误，不提供旧协议适配，也不静默套用新策略。恢复原始策略后可显式重试，但不会重跑 builder、科研计算或 LLM；更新或已撤回的事实仍受版本检查保护。Hook 语义变化时，领域作者必须推进声明版本；指纹针对声明内容，不对 Python 源码进行 hash。

Configured CFG conversion、GB minimization、structure minimization、GB energy、GB shear 和 GB NEB run 都会在 run parameters 中保存一份可搜索的 `canonical_config` 镜像。primary `input.json` artifact 仍是权威 config；该镜像只用于让所有精确字段进入 run 搜索和 working-summary 更新。Shear 的 source 文件、material、 boundary condition 和 potential 从关联的 completed minimization 或 completed shear 的最终状态继承，loading、minimization、output 则来自 writer 的具名类型参数。建立或刷新 search index 时，系统会从 project 内的 `input.json` 为受支持的科研 workflow 回填缺失的 config 镜像。

GB-energy run 复用 minimization 的 canonical 双晶构型字段，并要求 x-z 界面连续周期、y 方向带真空。它记录 relaxed bicrystal 以及 top/bottom 各自的 bulk/surface reference；紧凑 run result 包含 J/m² 与 eV/Å² 两套 GB energy、surface/bulk energy、界面面积和 convergence warning。成功后还会确定性写入 `run:<gb_energy_run_id>:main_conclusion` 这一 `scientific_conclusion` fact；完整 reference 状态保存在已索引的 `run_metadata` artifact。

完成的 `cfg_convert` run 会记录精确的源 CFG 路径与 SHA-256 fingerprint、primary 源文件快照、derived `converted_lmp` artifact 路径、原子/类型数、cell metadata 以及源到输出的映射。由于 CFG 缺少 Cartesian box-origin 字段，conversion 还会记录复用的兼容 parent-LMP origin reference/fingerprint；如果只能把盒中心置于原点，则记录明确 warning。之后的 LAMMPS 任务可直接用 run ID 和 artifact role 找到并复用转换结果，无需扫描 project 目录。剪切最终 CFG/LMP 与中间帧均保存准确的 step、应变和应力 metadata；旧最终文件可从原运行 metadata 的 `current` 恢复帧身份，不会把终态套用于中间帧。单个和批量普通最小化还通过 `simulation_context` 关系记录实际提供材料、势函数和边界配置的运行，支持反向查询。

应力应变分析会对本次读取的剪切 metadata 原始字节计算指纹，并保存永久 `source_metadata_snapshot`。分析结果、来源关系和结论 fact 指向同一份快照，即使 `allow_partial` 对应的剪切运行之后继续推进，也能重现当时分析的数据。

每个 run 的 provenance 在 `run_sources(run_id, source_run_id, relation, ordinal, ...)` 中规范化存储。因此 comparison 等派生 run 可以直接关联每个输入，无需只把 ID 藏在 parameters JSON 中。 `source_run_id` 保留为单一 primary source 的便捷字段，并与 normalized relation 在同一 transaction 写入；反向查询以 `run_sources` 为准。

执行 shear 时会把父运行的最终 LMP 和两份 boundary-ID 文件复制到本运行的 `inputs/` 目录并注册，同时记录父 workflow、父 run ID、source fingerprint 和初始累计应变。运行中 metadata 持续记录 progress、当前 COM、spacing、相对与累计 strain、convergence、经过真空修正的上下剪切应力和六分量应力、 summary peak、每个已完成 loading point 以及可渲染 frames。逐点历史不受 `dump_every` 影响；该参数只决定 frame index 中包含哪些 CFG 路径。

终态 semantic result 直接由这些 metadata 构建，只保留紧凑的修正后索引，例如 top shear stress 绝对峰值 MPa（spring force/area 或 displacement 模式的 `sigma_xy`）、peak step/strain、应力来源、当前应力状态、收敛计数、初始/当前上下边界 COM。

运行期间，semantic progress 索引最新的完整修正应力状态、当前间距和运行中 peak summary。完整 `points` 历史保留在已注册的 metadata artifact 中供后续分析，不重复塞入可搜索 run row。Monitor 会跳过未变化的 metadata，并且只注册新发现的 CFG frames。

供人查看的 shear curve 保留在 run 目录但不注册进 Project Memory。Structure、log、 run metadata 和 frame 始终放在 artifacts。Delegate 不另外创建科学 run 包装或复制科学输出；最终结论按下述方式独立保存，对话 trace 留在仅供人读取的 Chat history 中。

## 子 agent 结论与任务连续性

[WorkerOutcome](../../src/agents/workers/outcome.py) 明确最终报告的 schema version、status、 summary、带类型的 run/fact/artifact 引用和 next action。支持 `completed`、`reused`、`partial`、 `failed`、`awaiting_user`、`cancelled`、`interrupted`；未知或非终态状态会被拒绝。工具声称的成功仍需通过宿主的来源和交付物验收，不能仅凭 outcome 获得科研证据资格。

每次已注册 worker 的项目委派都有独立的 `conclusion-<id>`，由调用身份确定；同一身份重放不会覆盖不同的结论。 `memory/conclusions/<id>.md` 保存完整最终结论，相邻 metadata 记录任务目的、结果状态、来源与内容哈希，不包含对话 trace。数据库 `agent_conclusion` fact 仅保存最多 800 字符的短摘要（由关闭 thinking 的 memory endpoint 副本生成，回答上限为 2,048 token 或更低的 endpoint `max_tokens`；无法停止 reasoning 的模型在 endpoint 上限内额外获得 4,096 token 余量）、来源和全文引用，证据类别为 `assistant_report`，不会冒充已验证的科学事实。成功、失败、部分完成和仅复用已有结果的调用均可发布结论。摘要生成失败时，全文与 `summary_pending` 短索引仍可查询；补摘要不需要重新计算。

通过 `query_project_memory` / `search_project_memory` 找到索引，再用 `read_agent_conclusion(conclusion_id, offset_chars=0, max_chars=12000)` 分页读取全文。 `section="metadata"` 可以读取完整来源列表。

`task_state` fact 保存每个 thread 和每次调用的执行游标：目标、状态、活跃调用、结论引用、阻塞原因和下一步。`get_task_state()` 读取当前状态，传入精确 lookup key 可读取旧调用。 CAS 更新防止并行调用互相覆盖，动态 prompt 每次调用模型前刷新有界状态。已有 approved project plan 仍是权威计划；未规划任务不会因此新建审批计划。这使长期任务不必把所有回复都留在 working 中，也能知道已经做过什么。

## Artifact Manifest 与 Provenance

每个 run 目录都有 `manifest.json`；配置型 workflow 只包含一个权威 `input.json`，并且只以 `config` role 注册一次。GB shear 还会写入一份 runtime `config_used.json`，其中的路径指向本运行目录内的 structure 和 boundary-ID 快照。文件通过 artifact role 选择，不依赖文件名。provenance 决定保留策略：

- `primary`：原始科研输入与输出，永久保留。
- `derived`：stress--strain 数据/曲线、frame、render 等科研结果，永久保留。
- `debug`：仅诊断用途，可由内部维护策略清理。

Agent 没有删除工具。读取必须同时通过数据库索引和 run manifest 校验；路径越出 project/run 目录、索引损坏或 manifest 不一致都会被拒绝。

## Agent 查询工具

`query_project_memory` 接收 `entity=runs`、`entity=facts`、`entity=summary` 或 `entity=summary_archive` 并返回 JSON。 Run filter 包括 `workflow`、`status`、`source_fingerprint`、`source_run_id`、 `source_relation`、`run_id`； fact filter 包括 `fact_kind`、`lookup_key`、`source_type` 和仅用于审计 tombstone 的 `include_retracted`。不同 entity 的 filter 不可混用。

面向模型的工具调用参数示例：

```text
query_project_memory(entity="facts", source_type="tool_result")
query_project_memory(entity="facts", source_type="conversation")
query_project_memory(entity="summary_archive", query="NEB")
query_project_memory(entity="summary_archive", archive_id="summary-...")
search_project_memory(query="晶界能", source_type="tool_result")
```

Search 设置 `source_type` 且未指定 `entity_types` 时，只搜索 facts。来源过滤不能与显式 run/artifact entity 类型混用，否则报错。不设置来源过滤时仍可查询混合来源；返回的 fact 和 search reference 继续携带来源类别。

记录分页使用 `has_more`、`next_cursor`、`source_revision`，续页保持原筛选条件； revision 改变时返回 `query_snapshot_changed`，需要重新查询。大结果的字符续读必须提供 `snapshot_id` 和 `offset_chars`，或通过 `read_tool_output` 读取不可变快照。 Summary 与主 Agent 上下文明确显示 `summary_stale`、提取重试和发布健康状态；关键决策仍需精确读取 facts/runs。新登记永久产物保存 SHA-256，读取、Analysis 输入、Coding staging 和完成验收共用 manifest、路径、角色及内容核验。没有原始 hash 的旧文件需要另行迁移，不能把当前文件的 hash 当作历史正确性的证明。

`search_project_memory(query, entity_types=None, limit=10, offset=0, include_noncompleted_runs=False, include_task_states=False, source_type=None)` 用于 working summary 信息不足、或还不知道精确 ID 时搜索完整的 fact/run/artifact 索引。返回内容是有界的候选 snippet、稳定 reference 和分页状态，不会直接返回完整 artifact 文件。`entity_types` 可限制为 `fact`、`run`、`artifact`，`limit` 最大为 25。

长度至少为 3 的关键词使用 FTS5 trigram，包括没有分词的中文。包含 `Fe`、`Σ5` 等短标识时按匹配内容排序，较长关键词权重更高；短化学符号按词匹配，避免 `Fe` 误中 `manifest`，同时支持紧邻中文的符号和中文短词子串。

默认在分页之前排除未完成运行、其产物及计算事实；用户明确决定不因关联运行失败而失效。诊断任务可设置 `include_noncompleted_runs=true`。执行游标也默认排除；执行游标审计可设置 `include_task_states=true`，恢复任务进度则用 `get_task_state`。

搜索结果用于发现候选，并不替代权威 structured row 或 artifact 内容。run 结果和 run search reference 都包含有序 `sources` 列表。关键结果应继续通过 `query_project_memory`、`list_run_artifacts` 或 `read_run_artifact` 精确读取。索引更新与 source write 位于同一 transaction；它可以随时从 `runs`、`run_sources`、`facts`、`artifacts` 重建，index version 变化时已有 project 会在首次打开时自动重建。

`fact_kind` 是启动注册表 fact vocabulary 中的类别，例如 `user_decision`、`project_plan` 或 `scientific_conclusion`；`lookup_key` 才是某条具体事实的稳定标识，例如 `project:active_material`。未知 `fact_kind` 会返回明确的字段使用错误，而不是静默返回空列表，从而避免把 lookup key 当成类别。

Artifact 工具同样采用结构化输入：

- `list_run_artifacts(run_id, role=None, offset=0, limit=20, offset_chars=0, max_chars=12000, snapshot_id=None)`：分页列出经过 manifest 校验的记录，每页最多 100 条（`next_offset`）。大页面以无损 JSON 文本分页返回，用 `snapshot_id` 续读。
- `read_run_artifact(run_id, role, artifact_id=None, max_chars=12000, offset_chars=0)`：读取唯一匹配、适合语言模型理解的 text artifact。默认每页 12,000 字符、单次最多 24,000 字符；更大的文件会给出精确的下一 offset，可通过连续分块完整读取。`.lmp`、`.cfg`、`.lammpstrj` 等原子结构和轨迹仍是永久机器 artifact，但模型读取工具会拒绝它们；下游 LAMMPS/analysis 工具通过 run ID 和 role 解析这些文件。
- `report_artifact_usage(workflow=None)`：按 provenance 报告索引文件大小。

公共模型边界（`agents/memory/projection.py`）对不超过 4,000 字符的工具返回保持原样，除非它带有 `model_view` 或 worker trace。更大的返回归档到 `runtime/tool_outputs/`，并以最多 16,000 字符的投影呈现，附带 `full_output` 引用。自带 `model_view` 的回复（例如 `RunToolResult.model_view`），或超过 4,000 字符且工具通过 `ToolCapabilitySpec.model_view` 声明了视图的回复，以该视图呈现；超过 15,400 字符的视图本身也会被投影。纯文本在 15,400 字符以内完整显示，更长的显示前 15,400 字符，若最后五分之一内有换行则回退到该换行处。读取细节的记忆工具永不投影。归档的原始科学证据可通过 `read_tool_output(output_id, offset_chars=0, max_chars=12000, json_pointer=None)` 按需读取。旧 delegate 存档只提供最终结论和科学引用，不允许通过此工具读取 chat history 或嵌套 trace。这属于 runtime 调用记录，不重复登记科学事实；run、artifact、source 和 conclusion 的身份保持不变。较大的精确 memory 查询结果同样使用有界、无损的分页。

记忆与 artifact 权限由各自注册的 `AgentMemorySpec.artifact_access` 决定：`"read"`（默认）或 `"metadata"`。`read` agent 获得 `list_run_artifacts` 和 `read_run_artifact`；`metadata` agent（目前只有 Coding）两者都没有，其 handoff 把 artifact 引用标记为仅元数据，其 `read_tool_output` 只能读取本次调用中归档的输出。Coding 输入仍需显式授权的快照。`report_artifact_usage` 由单独的 `report_artifact_usage=True` 标志控制，只对 orchestrator 启用。

每次已注册 worker delegation 之前，系统都会先从权威 SQLite 确定性选择一份有界证据预览。`task_state` 在搜索排序和数量限制前就被排除，避免 worker 把刚创建的自身游标误认为另一项已经运行的科学分析。普通 search 也默认排除游标，显式执行审计可设置 `include_task_states=true`，连续性查询仍使用 `get_task_state`。声称科学计算已完成或正在运行必须核对真实 run，不能只依赖执行游标或 assistant report。

该 handoff 最多 12,000 字符，包含相关 active facts、completed runs 和稳定 artifact references。面向 orchestrator 的 `gb_crystallography` fact 不会自动注入 worker，仍可通过 project summary 和精确 memory query 读取。选择发生在新 scientific run 注册之前，因此 worker 不会把自己的 in-flight task 当作历史证据。只有 completed run 及其产物会作为可复用证据注入；configured、running、failed、cancelled run 及与之绑定的计算事实都被排除，用户的明确选择保留。Agent 结论仍可能出现，并可能描述失败、部分完成或中断的工作；它们标记为 `agent_report`，不是证据。CSL artifact 还会保存规范化工具输入和 input fingerprint，使完全相同的确定性调用可以跨 turn 直接复用。

Handoff 优先解析完整任务中的精确 run/artifact ID，并查找来源已有的派生运行；关键词查询最多 500 字符，同时搜索 artifact metadata。最新候选覆盖相关 workflow 后再统一排序。材料、势函数、加载偏好、目标和用户决定优先保留。

记录按条装入：最多 4 个 completed run 和 8 条 active fact。从任务文本中最多解析 24 个显式 run、artifact 或 conclusion 引用，关键词搜索（查询最多 500 字符）取 12 条命中。每条 fact 的值预览公平分享剩余空间（100–1,400 字符）；run 输入预览最多 900 字符，结果摘要预览最多 700 字符；每个 run 最多列出 10 个 artifact 引用。返回的 ID 仅对应实际展示内容；字段 preview 明确标记截断，并引导 worker 精确读取。12,000 字符（`DEFAULT_HANDOFF_MAX_CHARS`）是 handoff 自身上限，不随主上下文的 1M 开关变化。Coding 的文件访问仍限于显式授权并复制的输入。

CSL 的精确输入缓存查询全部已完成历史，不受最近运行数限制；读取前核对 manifest、路径、有效科研输出以及已记录的内容 hash。进程内缓存按 project 和 worker thread 隔离。其他科学 workflow 的候选仍需 agent 核对，尚无统一的自动复用入口。结果复用和证据追踪的可选扩展方向见[后续设计](roadmap.md)。

## Semantic Summary 与短期 Context

Project summary 和 session summary 解决不同问题：前者帮助整个项目确定下一步科研决策，后者帮助模型接续当前对话。两者都应保持简短，允许省略细节。开展具体科研工作时，仍需通过查询接口读取准确的研究记录和文件。

`memory/index.sqlite` 的 `semantic_summary` singleton row 是有界 project working summary 的 source of truth，并与 source revision/digest、生成时间、fact/run 数量和 format version 一起事务写入。`memory/summary.md` 只是从该 row 原子重建的人类可读 Markdown view；agent、 API 和 memory query 都不会读取 Markdown。写入前会通过索引查询校验所有 ``(fact: `lookup_key`)`` 引用确实存在于当前 SQLite facts，且属于对应来源章节。

### 哪些信息留在当前上下文

Project summary 是有选择的工作索引：优先保存当前目标、长期偏好和约束、待批准事项、未完成任务、阻塞原因以及关键证据引用。需要压缩时，先移除已经完成的执行过程、参数清单、重复报告，以及与下一步决策无关的旧结果；仍然相关的已完成发现保留为简短结论和精确引用。领域声明的 `context_priority` 与固定 lookup key 参与宿主选择；活跃状态记录在分页之前查询，避免大量较新的已完成历史挤掉旧阻塞项。

### 历史摘要与查询入口

每次替换非空摘要，都会把上一版完整正文和来源 metadata 保存到 `semantic_summary_archives`，与写入新摘要、确认已处理变更位于同一事务。CAS 校验会在归档和清理之前拒绝过时的并发写入。摘要压缩不删除或改写任何 fact、run、artifact 或 conclusion，只清理已处理的派生变更通知。已有数据库在初始化时获得归档表；历史从采用此实现后的首次摘要替换开始积累。

当前摘要还包含程序生成的历史导航：最多列出 3 个真实归档 ID、source revision 和简短主题提要，优先包含刚被替换的上一版。导航明确说明“摘要和索引都不完整”，未提及某项不代表数据库中没有该记录，并给出 `archive_id` 精确读取、`query` 搜索和 `next_cursor` 翻页方法。导航最多占 1,400 字符，计入摘要总预算；小预算会进一步缩减。历史提要是按来源摘取的片段，不作为当前事实校验，也不作为批准依据。旧导航不再送给摘要模型，避免递归增长；归档 ID 由程序生成并随同归档事务提交，模型不能用编造的索引替代它。旧摘要在下一次刷新时补齐导航。

当前准确证据使用精确 fact/run query，未知 ID 的旧记录用 `search_project_memory` 查找。旧摘要使用 `query_project_memory(entity="summary_archive", query="NEB")` 搜索预览并分页，再传入 `archive_id="summary-..."` 读取完整正文；大结果仍可通过 tool-output 无损续读。历史摘要可能含有已被取代的决定或已撤回的解释，行动前需要核对当前数据库记录；历史全文不会重新自动注入日常上下文。`memory/summary_archive/{archive_id}.md` 是可重建视图， `index.md` 只列最近 20 份，较早记录仍可从 SQLite 查询。普通刷新修复近期视图； `save_archive_views(project_root, memory, rebuild=True)` 可在不调用模型的情况下重建全部历史文档。

### 代码职责与证据来源

项目摘要实现集中于 [`src/project_memory/summary/`](../../src/project_memory/summary/README.md)： `service.py` 协调刷新，`policy.py`、`batch.py`、`prompt.py`、`generation.py`、`validation.py` 负责选择和验证内容，`navigation.py` 负责历史入口，`storage.py` 与 `views.py` 负责存储和派生文档。 `project_memory/snapshot/session.py` 只读项目快照；`agents/middleware/session.py` 管理另一层独立的线程对话摘要。

摘要格式 4 固定保留四个章节：`Tool results`、`Conversation-derived context`、 `Agent reports`、`Workflow state`。输入分别为 `changed_facts`（工具结果）、 `conversation_facts`、`agent_reports`、`workflow_state` 和 `changed_runs`。即使引用了 completed run，对话解释仍放在对话来源章节；worker 的解释留在 `Agent reports`。把 fact 引用放进错误来源章节会被拒绝：草稿得到一次纠正，提示该记录应放在哪个章节；纠正后仍有引用来源错误时，保留已提交的摘要，来源变更留待之后刷新。旧的混合摘要从当前持久记录重新生成，旧正文以 `archive_reason='format_reset'` 归档；不删除 facts，也不把对话主张升级成计算结果。

Facts 和 runs 仍是完整 durable source of truth。每次新增、有效更新或 retract 都会递增 monotonic semantic revision，并把变更实体 upsert 到 `semantic_changes`。同一个 run 的多次 progress 更新会合并成一个待处理项；内容不变的 fact/progress 写入不会增加 revision。 Retracted fact 会作为明确 tombstone 交给 summarizer，要求移除旧 statement 和 citation； fact count 只统计 active facts。Working summary 成功提交后，已处理的 change 会被清理。

### 更新、长度限制与失败恢复

Summary 生成明确移出 request preparation 的关键路径。assistant turn 完成后，semantic post-processing 依次执行 extract、policy/apply 和 `maybe_refresh_summary()`。Extractor 和 summarizer 都通过 `core.llm.factory.auxiliary_endpoint` 复制 memory endpoint，并设置 `thinking="disabled"`、`reasoning_effort=None`。供应商仍可能发送其最低 reasoning effort；无法停止 reasoning 的模型在回答上限之上额外获得 4,096 token 余量，但不超过 endpoint 的输出上限。Extraction 的回答上限为 8,192 token（endpoint 上限更低时取后者）；其请求容量最多 64K token，先扣除输出上限和 1,024 token 余量，再装入源对话、run 卡片和候选事实。新 project summary 目标为 6,000 字符，硬上限为 12,000 字符；token 预算较小时还会扣除 framing 预留并进一步缩小字符上限。字符限制不随 1M 模式扩大，也不等于供应商 token 数。项目摘要生成和草稿校验使用 min(`project_summary_max_tokens`, endpoint 输出上限, 24,000) token，即 12,000 字符上限按每字符两个 token 计算。`[runtime_context] project_summary_max_tokens` 继续作为动态注入和上下文预留的上限，默认 32768，1M 模式为 131072。原 endpoint 不会被修改。

首次生成未通过大小、完整性或当前关键引用检查时，最多再纠正一次，使用有界原始输入，不把过大的拒稿继续追加到请求。仍未通过这些检查则使用宿主确定性构建的有界数据库检索索引，不编造科研解释。模型服务或网络错误、证据来源引用不合法时仍保留当前摘要。流式 API 在此后处理期间发送 `finalizing` 状态；只有请求结果落库且执行租约释放之后才发送终态 `done`，网页发送按钮在此之前保持禁用。

如果 extraction、apply/storage 或 summary refresh 失败，已完成聊天仍保持 completed，并用确定性 job ID 持久记录失败阶段。Extraction 已成功时会保存 structured JSON，因此 apply/summary retry 不需要重复调用 extractor。Web 后台循环会事务 claim ready jobs，Web/CLI 每轮完成后也会顺带处理一个 ready job。重试使用有界退避（5 秒、30 秒、2 分钟、10 分钟，然后 1 小时）；后台重试失败 5 次（`MAX_RETRY_ATTEMPTS`）后进入 `dead_letter`，policy/schema、payload、source-context 或摘要预算等永久错误则直接进入 dead letter。处于 `processing` 状态且闲置 10 分钟的任务会被回收。Project-memory preview API 会返回 pending 和 dead-letter 数量。历史 `dead_letter` 任务不会自动重新排队。`project_memory.extraction.retry` 提供两个显式辅助函数，默认均为 dry-run，且都不调用模型：`requeue_legacy_extraction_budget_failure(store, project_id, assistant_turn_id, dry_run=True)` 保留尝试计数；`requeue_dead_letter_extraction_failures(store, project_id, error_type=..., dry_run=True)` 重新排队所有记录的 `error_type` 已被宿主修复解决的 extract 阶段 dead letter，并重置其尝试计数。

公共结论登记使用独立的 `memory_publication` 状态。每次项目请求最多恢复 8 个 pending 项；包括首次尝试在内失败 3 次后转为 blocked。恢复不调用 LLM 或重跑科学工具，preview 使用 `publication_pending_count` / `publication_blocked_count` 报告。恢复先校验协议版本和声明指纹，再验证快照与来源，并保护较新或已撤回的事实；详见[公共发布接口](extension-guide.md)。

每次生成发送上一版 working summary、当前优先事项、数据库总数，以及下一批按 revision 排序的变更 facts/runs，不读取全库作为模型输入。超过 6,000 字符目标的旧摘要只在生成输入中替换为有界当前数据库索引；完整旧正文仍在数据库中，直到原子替换时进入历史归档。整个请求受 `project_summary_working_set_max_tokens`（默认 65536， 1M 模式为 262144）限制，每次最多扫描 512 个待处理实体。单条记录过大时只保留 ID 和省略说明，不复制大块 payload；若仍有变更，则在后续完成的 turn 继续消费。批次里没有出现某条记录不代表删除，精确历史细节通过全量搜索后再进行结构化 memory query 或 artifact read 获取。该预算用于独立的摘要更新请求，不占用 orchestrator 的近期对话 working 预算。

### 向模型请求提供上下文

`interfaces.turn.prepare_project_session()` 在执行租约内协调遗留 run 取消、checkpoint 修复、旧聊天中断和 publication 恢复，然后调用只读的 `project_memory.snapshot.session.read_project_memory_snapshot()`，再通过 `ProjectRuntimeContext.from_memory_snapshot()` 适配为 request-scoped 上下文。每次 model call 通过 dynamic system prompt 注入：

```xml
<gbclaw_memory_context>
<pending_project_plan>
...存在时的紧凑 pending pointer...
</pending_project_plan>
<active_project_plan>
...存在时的紧凑 active pointer...
</active_project_plan>
</gbclaw_memory_context>
<gbclaw_project_summary>
<project_memory>
...semantic summary...
</project_memory>
</gbclaw_project_summary>
```

project summary 不会保存为 Human/AI checkpoint message，summary 更新也不会清空最近对话。永久 Chat 仍只供历史回放，不会暗中转成模型 context。

每次 model request 前还会使用当前 orchestrator tokenizer 按同一限制检查摘要。意外残留的超限旧数据或外部修改内容不会被截断成可能误导的科研摘要，而是停止注入；agent 仍可查询结构化 project memory。

默认 256K 运行时预算分别限制项目摘要（32K）、压缩对话（32K）、近期未压缩对话（64K）和实时计划/检索记忆（64K），这里 K 表示 1024 tokens。主请求为生成输出和安全余量预留至少 32K，剩余 32K 用于 system prompt 和 tool schema。Pending/active plan pointer 与记忆 tool 返回值共用实时记忆预算，独立于项目摘要。请求中的记忆限额保留完整 tool result 或检索指针，不会从 JSON 值中间截断，也不会截短数据库或 checkpoint 里的原始记录。 `GBCLAW_CONTEXT_1M=1` 将这些运行时 token 预算统一乘以 4，但仍受 endpoint 配置的输入上限限制。保持数值预算不定义，即可使用随开关自动缩放的默认值；显式定义会覆盖默认值。只有清楚覆盖影响时才设置，运行时覆盖值与固定 endpoint 上限的区别参见 [配置说明](configuration.md#runtime-context-与-working-summary)。

### 压缩对话状态

Orchestrator 的 approval 与短期 state 由 middleware 管理：

- `TurnStartCleanupMiddleware` 在每条请求开始时运行一次，从 checkpoint 删除之前各轮的推理，以及没有得到回答的相同请求副本；两者都仍保存在聊天记录里。
- `PlanApprovalGateMiddleware` 在 tool-call 边界强制执行当前 pending decision。
- `ContextPartitionMiddleware` 在模型请求中将可恢复的旧工具详情替换成引用卡片，保留状态以及 run/artifact/conclusion 标识。不删除文件，也不改写原始工具返回；没有安全引用或仍需用户选择的内容保留。
- `FailSafeSummarizationMiddleware` 默认在 48K 开始压缩，目标保留 24K 的近期完整消息块， working 硬上限仍为 64K。最新请求前的那条回答原样保留；只有它本身放不下时，才一起纳入摘要。检索记忆使用独立预算。摘要失败或无效时不更新 checkpoint；若引用卡片已足够缩小请求，仍可继续。确实无法安全放入窗口时，API/CLI 返回可恢复的暂停和后续处理提示，不自动重跑科学计算。

成功的 session 压缩会替换当前 checkpoint 中的旧消息列表，不删除永久聊天记录、结论文件、科学产物或工具输出存档。Working 限制的是跨多轮保留下来、当前要发给模型的内容，既不是整个项目累计历史的上限，也不是每个任务独立重新分配的额度。

Session summary 目标为 2,000 字符，上限 4,000 字符；四个章节只保留当前话题、有效约束、未决事项和检索引用。详细工具结果和已完成工作清单保存在持久项目记忆中，Session summary 不会写回 project semantic memory。

`GET /api/projects/{project_id}/memory` 返回项目摘要、`semantic_digest`、`source_revision`/`latest_revision`、`changes_pending`、fact/run 计数、`generated_at`、`retry_pending_count`、`retry_dead_letter_count`、`publication_pending_count`、`publication_blocked_count`，以及精确的 `active_plan` / `pending_plan` envelope，供 UI 恢复审批面板。

## Runtime Checkpoint

`ProjectCheckpointRouter` 按 `project_id` 将 LangGraph state 路由到 `runtime/checkpoints.sqlite`。Checkpoint 是可重建执行状态，不是 Chat 或 semantic memory 的 source of truth。错误或停止请求之后，`project_workspace/execution/recovery.py` 会移除不完整 tool call 并修复消息序列。

一个 project 的全部 thread 共用该 SQLite 文件，由 LangGraph 的 `thread_id` 和 `checkpoint_ns` 进行逻辑分区。不为每个 thread 建子目录：单库事务和连接复用更简单，而消息压缩及后续 checkpoint pruning 才是控制增长的有效手段。

## Web 文件访问

`project_workspace/artifacts/files.py` 为网页端提供 project-root-bounded 的 list、stat、text preview、download 和 raw 文件访问。文件浏览属于 UI 能力，不是 agent recall；agent 始终通过稳定 run ID 和 artifact role 访问。
