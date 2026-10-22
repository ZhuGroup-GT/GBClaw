# Agent Runtime

[English](../agent-runtime.md) · [架构与设计](architecture.md) · [项目记忆](project-memory.md) · [扩展开发指南](extension-guide.md)

GBClaw 由一个主 agent 与研究者持续交流，再把具体科研任务交给专业 agent。本指南先解释请求如何流转、各类 agent 的职责以及结果如何带着证据返回，再介绍 `src/agents/` 中的实现。

## 设计逻辑：为什么把协调与执行分开

晶界研究通常不能用一份固定脚本覆盖全过程。结构筛选可能发现意外构型，剪切计算可能引出新的分析，研究者也可能提出另一种假设。主 agent（orchestrator）负责把握科研问题、确定下一步；专业 agent（worker）只接收完成当前任务所需的输入、工具和已有证据。

这样，每个 worker 的上下文更集中，彼此独立的案例也可以在配置的资源额度内并发推进。但任务依赖仍然存在：轨迹分析必须等待轨迹生成，NEB 计算需要明确的端点结构。因此，委派解决的是职责分工，并不意味着所有任务都能并行。

Worker 按任务临时创建，科研输出则长期保存。后续 worker 通过稳定 ID 查询运行记录和产物，而不是继承前一个 worker 的全部对话。这要求每次交接明确说明做了什么、证据存在哪里、还有哪些问题没有解决；仅凭 worker 在文字中声称成功，不能把科研任务标记为成功完成。

Planning agent 用于支持研究者审阅方案。拟议草案与已批准计划分开保存，运行时在执行计划内科研步骤前检查批准状态。研究方向的调整因此有明确的决策节点，而范围清晰的直接任务仍可通过 `/noplan` 执行。

## 跟随一个科研任务

以“比较这两个已弛豫晶界的剪切响应”为例，典型流程是：

1. 主 agent 读取当前目标、计划状态和相关项目记忆，确认用户指的是哪两份已完成的结构，而不是从聊天描述中重新猜测结构。
2. 需要计划时，planning agent 提出计算和分析步骤；主 agent 展示草案，等待研究者决定。
3. 每个 worker 接收明确任务，以及有大小上限的相关事实、run 和 artifact 引用；细节不足时继续调用记忆工具查询。
4. 科研工具完成计算并保存输入、输出和运行状态；公共 worker 生命周期检查返回证据，记录结论和下一步。
5. 主 agent 对比发现、说明局限和未决问题，再向研究者回复。轮次完成后的记忆处理更新项目的精简工作摘要。

初次使用可先读[上手指南](getting-started.md)。新增专业 agent 时，按[扩展开发指南](extension-guide.md)接入下文使用的统一注册接口。

## 关键文件

- `src/agents/orchestrator/graph.py`: 创建与用户直接交流的主 agent。
- `src/agents/orchestrator/prompt.py`: `build_orchestrator_prompt()`，组合 orchestrator 的全局规则与各已注册 worker 的路由小节。
- `src/agents/graph.py`: 所有 agent 共用的 `create_agent_graph()` 工厂。
- `src/agents/invoke.py`: 同步调用、运行配置构建和最终回复提取。
- `src/agents/middleware/`: orchestrator 的上下文分区与 session summary 压缩、临时 worker 的单池压缩（`WorkerCompactionMiddleware`）、project summary 与计划状态注入、轮次开始时的 checkpoint 清理，以及取消检查；预算在 `src/core/settings/runtime_context.py` 中解析。
- `src/agents/streaming/events.py`: 把 LangGraph 流转换为网页和 CLI 使用的聊天流事件。
- `src/core/stream_events.py`: 所有事件生产方和消费方共用的流事件 schema。
- `src/interfaces/turn.py`: API 与 CLI 共用的轮次流程。
- `src/interfaces/recording.py`: API 与 CLI 共用的轮次记录流程。
- `src/interfaces/api/routers/chat.py`: API 流式与非流式请求的执行生命周期。
- `src/project_workspace/chat/trace.py`: 在内存中拼接执行轨迹，并在指定边界保存。
- `src/agents/workers/spec.py`: `WorkerSpec`，声明式的单任务 worker graph 及其运行流程。
- `src/agents/workers/result.py`: worker 返回给 orchestrator 的结构化结果格式。
- `src/agents/workers/lifecycle.py`: 统一处理已注册 worker 的委派、记忆交接、计划跟踪和结论保存。
- `src/agents/planner/`: planning worker、它的 `delegate_plan` 工具、计划工具和计划 gate。
- `src/domains/<domain>/agent.py` 与 `delegate.py`: Structure、LAMMPS、analysis 与临时 coding worker，以及 orchestrator 可调用的对应 delegate 工具。
- `src/core/llm/factory.py`: 通过已注册的 provider adapter，从 `LLMEndpoint` 构建 LangChain chat model。
- `src/core/execution/`: 执行会话、取消、资源额度和受监管的子进程。

## Agent graph 创建

`create_agent_graph()` 是统一入口：

- 调用 `build_llm(endpoint)` 创建底层模型。
- 若传入 `projects_root`，使用 `ProjectCheckpointRouter`，把 checkpoint 写入对应 project 的 SQLite。
- 若没有 project root，也没有显式 checkpointer，则使用内存态 `MemorySaver`。
- 在已注册 worker 的调用内，加入该 worker 注册的记忆工具，并把它的 memory handoff 追加到 system prompt。
- 安装 `ExecutionCancellationMiddleware` 与 `ModelToolOutputMiddleware`，随后加入调用方传入的 `middleware_factory`（orchestrator 的分区上下文 middleware），或默认的 `build_worker_context_middleware()`（它加入 `WorkerCompactionMiddleware`）。
- 调用 LangChain `create_agent()`，传入 tools、system prompt、middleware 和 checkpointer。

这意味着 orchestrator 在 API 模式下是持久化 checkpoint；worker 默认是临时 graph，除非外部额外传入持久化 checkpointer。

## Orchestrator

`create_orchestrator_agent()` 做几件事：

1. 校验每个已注册扩展 hook 的调用签名（发布校验器、fact 与 model-view 构造器、tool/query provider、orchestrator 工具与 worker 工厂、路由指引、startup/shutdown 与 LLM provider hook），并通过 `ModelRouter.for_role()` 解析 `orchestrator`、`literature` 和 `memory` endpoint；各 worker 在委派工具组装时解析自己声明的模型角色。
2. 根据 `literature` endpoint 判断是否启用 web search。
3. 组装领域为其声明的工具（`DomainExtension.orchestrator_tools`：coding 环境查询排在宿主工具之前，势函数查询排在其后）、可选搜索以及已注册的 worker 委派工具（`build_registered_worker_tools()`）；有 `ProjectStore` 时，额外注入注册策略指定的记忆工具和项目计划工具。内建工具包括：
   - `list_coding_environments`
   - `query_project_memory`
   - `search_project_memory`
   - `read_agent_conclusion`
   - `get_task_state`
   - `read_tool_output`
   - `list_run_artifacts`
   - `read_run_artifact`
   - `report_artifact_usage`
   - `get_project_plan`
   - `get_project_plan_draft`
   - `approve_project_plan_draft`
   - `reject_project_plan_draft`
   - `update_project_plan_step`
   - 可选 `search_web`
   - `list_available_potentials`
   - `delegate_plan`
   - `delegate_csl`
   - `delegate_lammps`
   - `delegate_analysis`
   - `delegate_coding`
4. 用 `build_orchestrator_prompt()` 构建 system prompt，如果 web search 可用则追加 `WEB_SEARCH_PROMPT`。其中 capability routing 小节按注册表顺序拼接各已注册 worker 的 `WorkerDefinition.routing_guidance` 文本；内置领域把这段文本保存为 `src/domains/<domain>/prompt.py` 中的 `ORCHESTRATOR_ROUTING`。

orchestrator 是唯一应直接对用户说话的 agent。领域计算和长任务通过 delegate 工具交给 worker。

## Worker agents

各 worker 都按单次任务创建。每个专业 agent 声明一个 `WorkerSpec`（`src/agents/workers/spec.py`）：agent 名称、label、状态文案和 system prompt。prompt 可以是 callable，因此 planner 每次任务都会重新加载领域知识。除非调用方显式传入工具或 spec 自己声明了 `tools` callable，worker 的业务工具来自注册表中该 agent 的 tool provider（`DomainExtension.tool_providers`），所以扩展也能给内置 worker 增加工具。`create_*_agent()` 就是该 spec 的 `create_graph()`，`invoke_*_agent()` 就是它的 `invoke()`；其中：

- `create_plan_agent()`（`src/agents/planner/agent.py`）: 动态加载领域知识，只注入只读 semantic/artifact 工具，不提供科研执行工具。
- `create_structure_agent()`（`src/domains/structure/agent.py`）: CSL/GB 领域工具 + semantic search/query 与 artifact list/read。
- `create_lammps_agent()`（`src/domains/lammps/agent.py`）: 领域工具 + semantic search/query 与 artifact list/read。
- `create_analysis_agent()`（`src/domains/analysis/agent.py`）: 领域工具 + semantic search/query 与 artifact list/read。
- `create_coding_agent()`（`src/domains/coding/agent.py`）: Deep Agents 文件/执行工具 + 隔离的临时沙盒工作区、只读 semantic 工具、用于申请 `input_grants` 授权文件的 `request_input_artifacts`，以及 host 控制的 `publish_coding_result` 发布工具，不直接读取任意 project artifact 内容。coding worker 保留自己的调用函数，受任务截止时间和 `GBCLAW_CODING_RECURSION_LIMIT` 步数上限（默认 200）约束，但复用相同的事件与回复处理。剩余约 8 轮工具调用时，`StepBudgetMiddleware` 只在当次请求中加入提醒；worker 未发布就停止、且未被取消或等待更多输入授权时，host 保存通过约定检查的声明 `expected_outputs`（`publication.mode="host_salvage"`），并把委托报告为 `partial`，详见 [Coding agent](coding-agent.md)。

对应的 `invoke_*_agent()` 会：

1. 校验 task 不为空。
2. 创建 worker graph。
3. 生成临时 `thread_id`。
4. 把 `project_id` 和可选 `chat_thread_id` 放入 config metadata/configurable。
5. 如果提供 `on_event`，走 `iter_worker_tool_events()`，把子工具事件流回 UI。
6. 从最终 messages 提取 reply。
7. 用 `extract_tool_trace_from_messages()` 提取工具轨迹。
8. 返回 `WorkerResult.ok(...)` 或 `WorkerResult.fail(...)`。

`WorkerResult` 字段：

- `status`: `ok` 或 `error`
- `summary`: worker 给 orchestrator 的文字摘要
- `data`: 附加结构化数据
- `artifacts`: artifact 路径
- `trace`: 内部跟踪用的工具调用轨迹，不进入面向模型的交接
- `error`: 错误文本
- `worker_outcome`: 可选的、经校验的 `WorkerOutcome`（`completed`、`reused`、`partial`、`failed`、`awaiting_user`、`cancelled` 或 `interrupted`，附证据引用和下一步）；序列化的交接始终带有一个，缺失时由结果推导

所有 delegate 使用统一的精简交接，只传结论、必要指标、去重后的 run/status/manifest 引用，以及必须保留的后续任务和用户决定。重复的任务原文和工具调用历史不会再返回给 orchestrator。计划跟踪先读取完整 worker result，再进行投影；完整科学结果仍保存在 run memory 和 artifacts。

graph 抛出异常（上下文上限、步数上限或 provider 错误）时，`WorkerSpec.invoke()` 返回 `WorkerResult.fail(...)`，并从 worker 的内存 checkpoint 读回工具轨迹；已完成的 run 因此仍能通过验证，结果为 `partial` 而不是 `failed`。上下文超限（无论来自 GBclaw 自身的检查、`ContextOverflowError`，还是指明上下文窗口的 provider 400/413）会在 `data.context_failure` 中写入错误码、host 生成的说明、安全的诊断信息（绝不包含 provider 返回的原文）、`recoverable=true`、`checkpoint_preserved=false`，以及“复用已完成 run ID、重新委派更窄任务”的 action，并同时写入 `data.next_action`。worker 对话本身无法恢复；只有 orchestrator 的聊天 thread 保留持久 checkpoint。coding worker 的调用函数不读回轨迹：run 已发布时仍返回成功并附 `post_publication_warning`；否则 host 先以 `partial` 保存声明的输出，再报告带同样 `context_failure` 的失败。

Worker 不使用 orchestrator 的分区上下文预算。`WorkerCompactionMiddleware` 为每个临时 worker 提供一个池子（`resolve_worker_context_budget`）：取 endpoint 窗口（`.key` 的 `max_input_tokens`、模型 profile 或默认值）与 `[runtime_context] total_context_tokens`（256K，`GBCLAW_CONTEXT_1M=1` 时乘以 4）中的较小者，减去 endpoint 输出上限与按比例缩放的 `safety_margin_tokens` 中的较大者，再减去静态 system prompt 和 tool schema。池子小于 16,384 token 时委派失败；`[runtime_context] enabled = false` 会关闭 worker 压缩。对话超过池子的 `worker_compact_fraction`（默认 0.8，范围 0.3–0.95，不随 1M 模式缩放）后，任务消息和最新的完整工具调用块（约池子的四分之一）原样保留，其余内容由 worker 自己的 endpoint 在关闭 thinking 的情况下写成一份包含 `COMPLETED`、`IN PROGRESS`、`OPEN ITEMS`、`LOOK-BACK REFERENCES` 四节的摘要：已完成的工作只保留结果和引用，进行中的工作保留参数、最后观察到的状态和下一步。留在上下文中的摘要字符数上限等于池子 token 数的六分之一（至少 4,096），摘要模型最多可写两倍：摘要过长时，完整的 COMPLETED 一节归档为 `compacted_worker_results` 工具输出，上下文只保留其开头几行，其余由一条 `read_tool_output` 指针替代；其余三节必须自行放得下，没有活动 project 时无法归档，过长的摘要会被拒绝。不合格的摘要有一次纠正机会；若摘要仍失败而请求仍在池内，worker 不压缩继续运行，并在下一步再试。被压掉的工具结果会先归档，可用 `read_tool_output` 读回；它们的 trace 记录随摘要消息（`compacted_tool_trace`）保存，completion check 仍能看到每个已完成调用。计数使用 GBclaw 的分词器，每个内联媒体块按 2,000 token 计。provider 上下文超限（`ContextOverflowError`，或指明上下文窗口的 400/413）会就地压缩请求并重试一次；无法再缩小的单个块以 `worker_block_exceeds_context` 使委派失败，重试后仍超限则以 `worker_context_exhausted` 失败。coding worker 保留 Deep Agents 的 summarizer 及其写入沙盒 backend 的历史卸载（`src/domains/coding/middleware.py`），使用相同的触发阈值、保留目标、计数器、摘要 prompt 和 COMPLETED 溢出归档；此外它从触发阈值的一半起截短旧工具调用参数，摘要格式不合格时保留 Deep Agents 原文而不纠正，runtime context 关闭时回退到 Deep Agents 的 profile 默认值。

公共 agent factory 和独立的 coding worker 都在模型请求前投影工具结果，覆盖工作流 JSON、普通文本工具及旧 checkpoint 中的结果。省略详情之前，先在项目 runtime 中归档完整工具输出，返回的 `full_output.output_id` 可通过 `read_tool_output` 分页读取。显式 memory/artifact 读取也使用有界分页。投影不改写原始工具事件或科学报告；新增工具自动获得这一边界，特殊结论字段可在统一 memory registry 中声明。

## Delegate tools

`delegate_plan`、`delegate_csl`、`delegate_lammps`、`delegate_analysis`、`delegate_coding` 都是 orchestrator 的工具，而不是用户 API。

每个 `WorkerDefinition` 指定一个 delegate factory（`src/domains/<domain>/delegate.py`；planning 为 `src/agents/planner/delegate.py`）。`build_registered_worker_tools()` 按注册表顺序构建这些工具，并用 `src/agents/workers/lifecycle.py` 中的 `create_worker_delegate()` 包装每一个。领域 delegate 函数定义科研任务并调用相应 worker；公共生命周期与领域资源管理共同负责外围流程：

- 从 LangGraph run config 里读取 `project_id`。
- 在创建 worker/run 之前生成有界、与当前任务相关的 memory handoff；其中只包含 active facts、相关 completed runs 和稳定 artifact IDs，并排除非终态 run。
- 给领域 worker 注入结构化、只读的 semantic 与 artifact 工具；coding 只获得 semantic 工具，以及调用参数 `source_artifacts` 中精确指定、由 host 校验并快照到 `/inputs` 的 artifact 文件；`input_grants` 授权的额外文件需由 worker 通过 `request_input_artifacts` 申请。
- 创建沙盒前从进程级 coding resource pool 预留该次调用的 `compute_slots`/`mpi_ranks`，并在所有终态路径归还 lease。
- 把 `WORKER_FORWARDED_EVENTS` 列出的 worker 事件（`status`、`thinking`、`thinking_withheld`、`tool_start`、`tool_end`、`agent_result_delta`、`agent_result_reset`）通过 `get_stream_writer()` 写回上层 stream，并携带 delegate tool-call `parent_id` 以及仅用于显示的 `parent_name`（delegate 工具名）。
- 调用对应 `invoke_*_agent()`。
- 接收结构化的 `WorkerResult`、`RunToolResult` 或 `WorkerOutcome`，检查完成证据、保存结果，再把精简序列化交接返回给 orchestrator。

### 任务上下文与证据

Delegate reasoning 和 tool events 永久保存在 Chat history。公共 worker 生命周期另行保存带报告来源的 agent conclusion 和 task cursor，不把完整 trace 复制进 semantic memory。领域工具通过自身注册的发布契约保存 scientific runs、artifacts 和结果 facts。

每条召回 fact 都携带 `source_type`、`evidence_kind` 和来源 turn/run 引用。Handoff 区分工具发布结果（`tool_result`）、对话提取上下文（`conversation`）、worker 报告（`agent_report`）与计划/任务游标（`workflow_state`）。`completed_run` 支持的解释仍属于对话来源；报告中的引用是支持性入口，不能独立证明科研成功。需要计算证据时，worker 可按 `source_type="tool_result"` 查询。

Handoff 上限为 12,000 字符，作为不可信 project data 追加到 worker system prompt。它让临时、无跨 turn 状态的 worker 在第一次模型调用前就知道哪些 completed evidence 已存在，以及后续读取应使用哪些精确 ID。面向 orchestrator 的 `gb_crystallography` 结论不会自动注入 worker； orchestrator 通过 project summary 或精确 memory query 读取它，再决定是否需要把相关内容写进具体 delegated task。没有匹配证据时 worker 仍可使用普通 memory search/query。

CSL result artifact 还会记录规范化工具输入和 input fingerprint；以后遇到完全相同的 CSL 调用会直接返回 completed artifact，不再计算。新 run 会记录所属 chat thread；该 turn 被 stop/disconnect 时，匹配的 running row 会转成 `cancelled`。

### 计划、批准与修订

`delegate_plan` 只在需要更深入的方法选择或依赖分析时调用。它向 orchestrator 返回建议，实际科研步骤仍由 orchestrator 调度。每次创建临时 planning graph 时都会读取 `src/agents/planner/domain_knowledge.md`。新计划请求会获得 `create_project_plan_draft`；带 `existing_plan_id` 的调用还会获得精确读取和 `revise_project_plan_draft`。共享的 request-scoped guard 只允许一次成功 draft write。这些工具写入 `plan_draft:<draft_id>` 和 `project:pending_plan`，不会激活或执行。delegate factory 的 `extra_tools` 是为将来的外部或 server-side search 预留的显式注入点，当前默认不注入搜索工具。

delegate 返回后，orchestrator 必须总结草案、请求用户 approve/reject/change，并结束当前 turn。 `PlanApprovalGateMiddleware` 在每个 tool call 前重读 pending pointer；未决定时阻止科研 delegate、 step transition 和 replacement planning，并验证 approve/reject 是否由最新 user message 明确授权。批准会把 active plan/revision、draft decision 与 pending-pointer retract 放在同一个 SQLite transaction；拒绝只关闭草案。用户提出修改后会生成新的、仍需再次批准的 draft。

计划内的 CSL/LAMMPS/analysis/coding 调用会携带 `plan_id` 和 `step_id`。delegate wrapper 会校验 capability 与依赖，在 worker 开始前写 `running`，结束后写 `completed` 或 `failed`，同时记录时间、attempts、结果摘要和发现的 run IDs；worker 等待用户决定或 near-CSL cell 选择尚未完成时则写 `blocked` 并附原因。完整计划和 active pointer 使用 fact version 在同一 SQLite transaction 中更新。orchestrator/memory/user-input/search 步骤使用 `update_project_plan_step`；跨 turn 恢复必须调用 `get_project_plan`，不能从 Chat 或可能滞后的 summary 猜测进度。

用户要求改计划时，orchestrator 先精确读取原计划，再把完整修改要求和 `existing_plan_id` 交给 `delegate_plan`。planning worker 会获得只读 memory/artifact 工具、精确 `get_project_plan`、 planning-only `revise_project_plan_draft` 和 `create_project_plan_draft`，并且只能成功执行一次 draft write。同一科研目标下的变更提出结构 revision；目标发生实质变化时提出 new-plan draft。两者在批准前都不影响 active plan。orchestrator 从不直接获得结构性 draft 写入工具。结构 revision 与普通的进度驱动 memory 版本相互独立。

pending work 刷新根目录 `plan-draft.md` 和审计过的 draft JSON；做出决定后 root pending view 消失；批准后的更新刷新 `plan.md` 与 current/revision JSON。这些文件由 SQLite 派生，绝不会反向作为执行状态读取。

`project:pending_plan` pointer 在 Runtime Context 中优先级最高，其次是 active pointer 和 working summary。dynamic prompt 每次模型调用前重读两者，因此批准与进度变化会立即可见；完整 plan/draft 通过 exact tools 读取。

### 是否需要规划

Planning 按 turn 使用 `auto`、`force`、`skip` 三态控制（`src/planning/mode.py` 中的 `PlanningMode`；聊天命令声明在 `src/interfaces/commands.py`）。`/plan` 强制生成 draft，并结束当前 turn 等待审批；`/noplan` 在本轮移除持久 planning 工具并允许直接 scientific delegation，但不会绕过 worker validation。被 `/noplan` 取代的 pending draft 会在 context 准备前以可审计理由 rejected；已有 active plan 保持不变。

### 取消与执行会话

每个 turn 按项目、thread 和执行 owner 注册一个执行会话（`src/core/execution/sessions.py`），并在其中运行。每个 delegate 都把当前聊天 thread id 作为 `chat_thread_id` 传给 worker。工具通过 `runtime_session_key()` 从 run config 解析当前会话 key，再用 `supervise()`/`run_cancellable()`（`src/core/execution/process.py`）或显式进程登记，把 LAMMPS、analysis 与渲染、dichromatic map 以及 coding 沙盒子进程挂到该会话下。stop 请求或断开连接会取消会话：已登记的进程树被终止，等待 LAMMPS rank 或 coding slot 的调用随即结束，`ExecutionCancellationMiddleware` 阻止后续模型和工具调用。服务关闭时，`cancel_all_sessions()` 取消所有会话。

## Streaming 事件

`src/agents/streaming/events.py` 负责把 LangGraph 的 `updates`、`messages`、`custom` stream mode 转成 UI event。事件词汇统一在 `src/core/stream_events.py` 中声明：`StreamEventType`、每种事件一个列出字段的 `TypedDict`、`validate_stream_event()` 和 `WORKER_FORWARDED_EVENTS`。网页从 `web/assets/js/stream_events.js` 导入相同的类型名；该文件在仓库根目录用 `PYTHONPATH=src python -m core.stream_events` 生成（`--check` 检查是否过期）。

主要事件类型：

- `status`: 当前状态文案。
- `thinking`: reasoning/thinking 增量。
- `thinking_withheld`: 模型进行了推理，但 provider 未公开任何文本；携带 `reasoning_tokens`（未知时为 0）。trace 把它保存为 `reasoning_withheld` 记录，绝不并入该轮的 reasoning 文本。
- `tool_start`: 工具开始，包含工具名、label 和供客户端使用的截断输入。
- `tool_end`: 工具结束，包含供客户端使用的截断输出。
- `tool_progress`: streaming 时检测到 tool call chunk。
- `agent_result_delta`: delegate worker 最终回答的文本增量。
- `agent_result_reset`: worker 当前 model turn 转而调用工具时，丢弃之前的临时回答文本。
- `final_start`: 最终回答开始。
- `token`: 最终回答 token。
- `reset_stream`: 前面流出的临时文本被工具调用打断，需要 UI 重置。
- `done`: 完整回复和 thread id。
- `error`: agent 没有返回有效回复或执行异常。

`tool_label()` 从注册表解析显示名：delegate 显示其 worker 的 display name，其他工具使用 `ToolCapabilitySpec.display_name` 或所属领域的 `DomainExtension.tool_labels`，未声明的工具显示原始名称。例如 `delegate_plan` 显示为 `Planning agent`，`delegate_lammps` 显示为 `LAMMPS agent`，`run_gb_minimize` 显示为 `GB minimization`。

API 通过容量为 1 的 bounded queue 把这些事件连接到浏览器，使 backpressure 紧贴模型流，避免服务端积累大量 token 后一次性刷新。`ChatTraceRecorder` 为每个 parent/label 分别维护 open reasoning segment，因此并行 worker 的交错 token 会回到各自的 segment。某个 worker 遇到自己的 tool boundary 时只封闭该 worker 的 segment；final answer/error 时再封闭其余 segment。SQLite 仍是权威存储；每轮结束时以及文件浏览访问发现导出 revision 过期时，都会原子重建人类可读的 `chat/transcript.md`。API 与 CLI 的每一轮都经过 `src/interfaces/turn.py` 中的 `run_turn()`：准备会话、运行 graph 流、通过 `src/interfaces/recording.py` 记录轮次与 trace，并安排完成后的记忆处理；只有输入解析和事件显示仍由各入口分别负责。

tool event 在进程内携带私有、未截断的 input/output 字段。`ChatTraceRecorder` 将完整值写入 SQLite；SSE 和 CLI 边界会移除私有字段，只保留有界 preview。历史 trace API 也会把已存储的完整 tool I/O 投影成有界 preview，因此浏览器显示和网络 payload 大小不会影响永久 transcript 的完整性。预览上限默认分别为工具输入 280 字符、工具输出 400 字符；可在 `.env`（同名 shell 变量优先）中设置 `GBCLAW_MAX_TOOL_INPUT` 与 `GBCLAW_MAX_TOOL_OUTPUT` 并重启来覆盖，不会改变永久 transcript 内容。

delegate tool 的 raw output 是较大的 `WorkerResult` 或 run-result JSON envelope，因此交互端不会再显示被截断到语法不完整的 JSON 前缀，而是直接显示委托的 `task` 与 worker 的 `summary`。两者默认上限分别为 1200 和 8000 字符，可通过 `GBCLAW_MAX_DELEGATE_INPUT`、`GBCLAW_MAX_DELEGATE_OUTPUT` 调整；完整 envelope 仍保存在 SQLite 中。

嵌套 worker event 使用 LangChain 实际生成的 tool-call ID 作为 `parent_id`；`parent_name` 只负责显示。tool start/end 记录把该调用 ID 持久化为可空的 `tool_call_id`，因此多个同名 delegate 在实时和历史 trace 中都能分别配对。按名字匹配仅作为旧数据的读取 fallback。

## LLM 构建和 thinking 适配

`src/core/llm/factory.py` 按 endpoint 的 `provider` 找到已注册的 `ProviderAdapter`（`src/core/llm/providers/base.py`：`name`、`aliases`、`apply_thinking()`、`build()`，以及可选的 `token_counter()` 与 `reasons_when_disabled()`）来构建模型。内置 adapter：

- `deepseek`
- `openai`（别名 `openai-compatible`、`openai_compatible`）
- `anthropic`（别名 `claude`）

`register_provider()` 可注册新的 adapter。领域扩展在 `DomainExtension.llm_providers` 中以 `"module:attr"` 声明一个无参 factory；API 和 CLI 在启动时（`src/interfaces/lifecycle.py` 的 `start_extensions()`）先注册这些 provider，再构建 orchestrator。

`.key` 中的 `thinking` 和 `reasoning_effort` 会先归一化成 `ThinkingSettings`，再映射到不同 provider：

- DeepSeek 或 DeepSeek-compatible OpenAI endpoint：写入 `extra_body.thinking.type`，映射统一 effort 档位，保留流式 `reasoning_content`，并在 tool-call turn 回传它。
- OpenAI Chat Completions: 使用 `reasoning_effort`；兼容网关也可能返回非标准 reasoning 字段。
- OpenAI Responses: 使用 `reasoning.effort`、请求 `reasoning.summary=auto`，并把流式 `reasoning.summary[]` block 归一化给 UI。
- 非官方 OpenAI-compatible gateway: 使用 `ChatOpenAIWithReasoning` 保留 `reasoning_content` / `reasoning_details`；网关返回这些字段时 streaming UI 会显示 thinking，并在工具循环中保留所需 reasoning。
- Anthropic：Claude 4.5 及更早版本自动使用 manual `budget_tokens`；Claude 4.6 及更新版本使用 adaptive thinking + effort。输出统一请求为 LangChain v1 block，带签名的 thinking block 仍能在工具调用时完整回传。

所有公开的 reasoning 形式都会归一化为同一个 `{type: "thinking", text: ...}` 应用事件；当 provider 报告了推理（reasoning block 或 reasoning token 计数）却没有任何文本时，stream 改为发出 `{type: "thinking_withheld", reasoning_tokens: ...}`，而不是编造文本。因此 stream 与持久化层无需按 provider 分支。

### 轮次完成后的记忆更新

Fact extraction 和 summary generation 是 assistant turn 完成后的辅助调用。它们通过 `dataclasses.replace()` 克隆已配置的 `memory` endpoint，把 `thinking` 设为 `"disabled"`、清空 `reasoning_effort`，不会修改原 endpoint 对象。Fact extraction 的请求窗口最多 64K token，其中先扣除输出上限 8,192 token 和 1,024 token 余量；semantic-summary generation 的输出上限取 `[runtime_context] project_summary_max_tokens`、endpoint 输出上限与 24,000（12,000 字符上限 × 每字符 2 token）中的最小值。无法完全关闭推理的模型（GPT-6、o-series、始终 adaptive 的 Claude）在答案上限之外另得 4,096 token 推理余量（不超过 endpoint 自身上限）。DeepSeek 会收到显式 disabled thinking body；OpenAI 模型收到该代际可接受的最低档位（GPT-5 为 `minimal`，GPT-5.1–5.x 为 `none`，GPT-6 与 o-series 为 `low`，无法识别的部署名不发送档位）；Anthropic 会收到显式 disabled thinking（始终启用 thinking 的 Claude family 则使用 low-effort + omitted-display 作为最接近的合法行为）。这些调用发生在用户可见回答完成后，而 `src/interfaces/turn.py` 中的 `prepare_project_session()` 在轮次开始前协调遗留 run 清理、checkpoint 修复、旧聊天中断及 publication 恢复，再调用只读记忆构造器，从 `memory/index.sqlite` 构造 request-scoped project context。恢复过程不调用 LLM，也不重跑科研工具。

Semantic-summary generation 采用增量模式：每次只组合上一版有界 working summary 和按 revision 排序、按 token 限制的一批合并后 run/fact changes，不会把完整 semantic database 序列化进模型请求。

Project summary 固定保留 `Tool results`、`Conversation-derived context`、`Agent reports` 和 `Workflow state` 四个章节。宿主按派生来源分组输入，并校验每个 fact 引用是否放在对应章节。对话提取和 session summarization 继续保留，但不能将对话解释改标为工具结果。过滤与来源语义见[事实来源](project-memory.md#事实来源)。

### 控制当前对话的上下文大小

Orchestrator 额外启用短期 context middleware：可恢复的旧工具详情只在 model request 中替换为引用卡片，精简阈值按 working 对话计算，不受检索记忆大小触发；消息达到预算后，由禁用 thinking 的 memory endpoint 生成结构化 session summary 并持久更新 checkpoint。Project semantic summary 通过 dynamic system prompt 注入，不会作为 Human/AI message 保存。内部 summarization stream 会被 SSE 层过滤，只显示一次 context compression status。普通 worker graph 按任务临时创建，使用相同的工具输出投影，但不使用这里的 session 压缩，而是在单次调用内使用单池 `WorkerCompactionMiddleware`（见 Worker agents）；跨调用的结论与执行状态通过项目 memory 独立保存。

下述 token 额度是上限，不是摘要应该达到的长度。Project summary 目标为 6,000 字符，最多 12,000 字符；session summary 目标为 2,000 字符，最多 4,000 字符。详细证据仍通过精确查询和产物读取获取。

默认总上下文为 256K tokens（K = 1024）：项目摘要不超过 32K、session summary 不超过 32K、近期未压缩对话不超过 64K、实时计划/检索记忆不超过 64K，并为生成输出和安全余量预留 32K；剩余 32K 容纳 system prompt 与 tool schema。

Working 对话达到 48K 时提前触发压缩，以保留 24K 近期消息为目标，硬上限保持 64K；检索记忆不参与该触发计数。完整工具调用块超过保留目标、但仍在 working 硬上限内时，保留整个块；仍无法安全压缩到上限内时，返回可恢复的暂停提示，保留状态与输出，不自动重跑计算。摘要校验和请求时预算控制共同限制各类内容，不截短 semantic database 或 checkpoint 中的原始 tool result。

上下文预算在本地按模型家族的词表计数，词表随代码放在 [`src/core/llm/providers/vocab/`](../../src/core/llm/providers/vocab/README.md)：GPT 名称（包括网关部署别名中的名称）使用 tiktoken 的 o200k/cl100k，DeepSeek 模型使用 DeepSeek-V3 分词器，计数与 API 返回的输入 token 一致。每条消息和工具定义的封装另加 5% 余量，工具定义按请求实际发送的格式计数。没有内置词表的模型（包括 Claude）按一字符一个 token 计数。

在 `.env` 设置 `GBCLAW_CONTEXT_1M=1` 并重启，可将运行时 token 预算统一乘以 4。默认值会随开关自动缩放；显式定义数值预算会覆盖默认值，除非清楚影响，否则保持不定义。显式配置的 endpoint `max_input_tokens` 仍然生效；更大的生成输出额度或 system/tool 开销会缩减实际可用输入预算。完整配置见[配置说明](configuration.md#runtime-context-与-working-summary)。
