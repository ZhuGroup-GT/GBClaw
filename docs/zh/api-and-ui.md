# API 和 Web UI 入口

本文说明一条聊天请求如何从浏览器输入变成保存到项目中的结果，也供开发其他客户端时参考。`src/interfaces/api/` 负责 HTTP/WebSocket 接口，`web/` 负责展示对话、计划和产物。启动方式见[快速开始](getting-started.md)。

## 设计逻辑

科研任务的执行时间可能长于一次页面交互，因此后端负责执行和持久记录，浏览器负责展示进度、提交用户决定。刷新页面时，界面重新读取这些记录，而不是根据屏幕上的消息重新拼出项目状态。

三个标识分别回答不同问题：

| 标识 | 含义 | 保留时间 |
| --- | --- | --- |
| `project_id` | 记忆、计划和产物属于哪个研究工作区？ | 跨页面访问和后端重启保留。 |
| `thread_id` | Agent 应继续哪条对话的 checkpoint？ | 持久保存；重新进入同一项目通常继续其默认线程。 |
| `request_id` | 这一次聊天请求是否已经开始或结束？ | 随请求准入记录和终态结果保存。 |

前端占用和执行占用分开管理。浏览器或 CLI 会话保持项目占用，避免两个客户端同时提交相互冲突的修改；每轮获准执行的请求再持有执行锁，直到任务与清理结束。这样既能让不同项目独立运行，也能保证同一项目的写入有序。

模型输出最后一个字并不代表请求已经结束。后端还需要保存回答、完成本轮的记忆处理，再释放执行占用。流式接口在这段时间发送 `phase="finalizing"`，只有下一轮可以进入时才发送终态 `done`。因此，即使界面已显示回答，项目仍可能因正在收尾而返回 HTTP 409。

聊天历史、研究记忆和产物分别保存，让界面可以回放对话，同时保留“对话中的说法”和“工具计算结果”的来源区别。整体设计见[架构与设计](architecture.md)和[项目记忆](project-memory.md)。

## FastAPI app

入口文件：

```text
src/interfaces/api/app.py
```

同一 package 中还有 `deps.py`（route 读取启动时放入 `app.state` 的服务，尚未初始化时返回 HTTP 503）、`ownership.py`（[项目占用](#项目占用)中的项目写入 token 校验）、`schemas.py`（Pydantic models）和 `routers/`（`chat.py`、`projects.py`、`registry.py`）。

应用启动时初始化：

- `ProjectStore(get_default_projects_root())`
- `ModelRouter(load_llm_config())`
- `memory_endpoint = model_router.for_role("memory")`
- `runtime_context_config = load_runtime_context_config()`
- `src/interfaces/lifecycle.py` 中的 `start_extensions()`：先注册各 extension 声明的 LLM provider，再运行它们的 `startup` hook，每个进程只执行一次。Structure extension 的 hook 负责 warmup CSL 工具使用的共享 process pool。
- `agent = create_orchestrator_agent(model_router, project_store=..., runtime_context_config=...)`
- 后台任务 `_memory_retry_loop`：通过 `drain_ready_retry_jobs()` 定期处理排队的 memory 重试任务，仍有任务时轮询更快

应用关闭时：

- 先停止 memory 重试任务（最多等待 5 秒，随后取消）。
- 取消所有 execution session，并终止其登记的进程，包括 LAMMPS 任务（`cancel_all_sessions()`）。
- 如果 checkpointer 是 `ProjectCheckpointRouter`，关闭 SQLite 连接。
- 按相反顺序运行 extension 的 `shutdown` hook（`stop_extensions()`）；Structure extension 借此关闭共享进程池。

FastAPI 同时挂载：

- `/api/...` chat routes（`routers/chat.py`）
- `/api/projects/...` project routes（`routers/projects.py`）
- `/api/registry`（`routers/registry.py`）
- domain extension 在 `DomainExtension.routers` 中声明的 router，由 `src/interfaces/lifecycle.py` 的 `extension_routers()` 按 registry 顺序解析；Structure extension 提供 [DichromaticMap 接口](#dichromaticmap-api-与悬浮工具)
- `/brand-assets/`，提供 `docs/assets/` 中的共享图片资源
- `/` 静态 `web/` 前端
- `/favicon.ico`，以 SVG 返回默认项目图标

README、文档和 Web UI 共用 [`docs/assets/` 中的项目素材](../assets/README.md)。网页页头和标签页使用方形 SVG icon，应用切换到深色主题时使用浅色前景版本。部署时应同时包含 `docs/assets/` 和 `web/`；服务仅挂载素材目录，不对外提供整个文档目录。

## 项目占用

自定义客户端应依次完成：占用项目、保持连接、在写请求中携带 token。`thread_id` 只标识对话状态，不授予项目写权限。

前端在进入项目前连接 `WS /api/projects/{id}/connection`。成功时首先收到 `{type: "claimed", token, project}`；另一标签页或浏览器尝试占用同一项目时收到 `{type: "error", detail}`，随后以 1008 关闭连接。文件锁覆盖连接的整个生命周期，包括页面空闲和处于后台期间。每次连接由服务端生成独立 token，不保存到浏览器 storage。

保持连接，并在聊天、停止、删除项目及所有项目级 DichromaticMap POST 中携带 `X-GBclaw-Project-Token: <token>`。缺失、过期或属于其他前端的 token 在产生副作用前返回 409。该校验是 `src/interfaces/api/ownership.py` 中的 router dependency `protect_project_write`，chat、project 和 DichromaticMap router 共用。列表、创建项目、只读文件/历史/记忆接口与不保存项目的预览不需要该 header。 CLI 会话也会一直占用项目，直到退出或切换项目。不同 project 可以独立工作；同项目不同 thread 共用一个执行锁。

切换时先占用目标项目，成功后释放原项目；失败会保留原项目。关闭页面释放连接，异常断网由 WebSocket ping/pong 检测。已进入的写请求及执行仍会阻止接管，直到清理完成。断线后禁用输入，并中止正在运行的流式请求（服务端按断连处理并取消该轮），通过 **Switch** 重新打开项目；页面从浏览器缓存恢复时也先重新占用。聊天运行期间禁用切换和删除。

`start_web.sh` 默认监听 `127.0.0.1:9999`（`API_HOST`、`API_PORT`），并明确选择 Uvicorn `websockets` transport，ping 间隔与超时均为 20 秒。反向代理需要同时支持 WebSocket upgrade 和 SSE。外部 HTTP 客户端也必须保持占用连接，单独传 `thread_id` 不能取得写权限。

## Chat API

定义在 `src/interfaces/api/routers/chat.py`。

### 请求标识与重试

每轮可提供独立 `request_id`，限 1–128 个字母、数字、`_` 或 `-`。前端自动生成，stream、 classic fallback 和 stop 使用同一个 ID。省略时服务端生成，并在首个 SSE status 或 classic response 返回。准入与结果持久保存到 `chat/history.sqlite`：同 ID、同规范消息、planning mode 和 thread 会复用终态结果并返回 `replayed=true`，支持普通和流式接口互相重试。修改任务或 thread 后复用 ID 返回 409；健康执行仍在运行时也返回 409，不启动第二次计算。 owner 消失后优先恢复已提交回答，否则记录 interrupted，不重跑工具。失败请求复用原始错误；有意重新尝试需要新 ID。

### 规划与批准

每个 chat request 可传入 `planning_mode="auto" | "force" | "skip"`。默认 `auto` 由 orchestrator 判断；`/plan <task>` 选择 `force`，`/noplan <task>` 选择 `skip`。Skip mode 只禁用本轮的持久 plan 与 approval 工具，不绕过晶体学推断、尺寸估算、配置校验和 provenance 检查。如果已有 pending draft，`skip` 请求（`/noplan <task>` 或 `planning_mode="skip"`）会在直接执行前将它拒绝并保留在审计记录中；active plan 不会被修改。存在 pending draft 时，浏览器不会发送 `/plan`，而是提示用户先批准、拒绝或请求修改。API 只接受 `planning_mode` 这一种 planning policy 字段。这两个命令只在 `src/interfaces/commands.py` 的 `CHAT_COMMANDS` 中声明一次，API、CLI help 和 Web UI 都读取这份声明。在 Web 输入框键入 `/` 会打开由 [`GET /api/registry`](#registry-api) 的 `chat_commands` 列表生成的命令菜单；筛选、键盘选择以及 `/plan`、`/noplan` 解析都使用这份列表。

### `GET /api/health`

返回：

```json
{"status": "ok"}
```

### `POST /api/chat`

非流式聊天接口。

流程：

1. 解析 message 与 planning mode（单独的 `/plan` 或冲突的 mode 返回 422），读取 project（404），确定 thread（请求中的 `thread_id`，否则使用项目保存的 thread），并检查启动时创建的服务，包括 memory LLM endpoint（503）。
2. 校验前端占用 token、保持请求存活锁、取得项目执行锁，然后注册 LAMMPS cancellation session。
3. `skip` 请求先拒绝 pending plan draft。随后在线程池执行 `prepare_project_session()`：取消上一轮遗留的 run、修复 checkpoint、把遗留的 running assistant turn 标记为 interrupted、重放待发布的 memory，并从 SQLite 返回 request-scoped project context；dynamic prompt 在 model call 时注入 summary，不改写 checkpoint。
4. 进入统一的 recorded-chat 执行路径：创建 user turn 和 running assistant turn，把 agent events 交给 `ChatTraceRecorder`，最后完成、失败或中断同一个 assistant turn。
5. assistant turn 成功完成后，提取持久事实，并在满足刷新策略时更新 semantic summary。
6. 出错时修复 checkpoint，结束时注销 cancellation session。

第 2 步的占用（`TurnOwnership`）和第 3–6 步的单轮执行（`run_turn()`）来自 `src/interfaces/turn.py`，CLI 也使用这条 turn pipeline。非 streaming 接口等待的也是同一套 recorded execution，因此两种 API 的 history 与 semantic-memory 行为一致。

### `POST /api/chat/stream`

通过 SSE 持续返回事件的流式聊天接口。

route 会在 project preparation 之前先发一个 `status` event，之后进入和 `/chat` 相同的 recorded-chat 执行路径。Agent 输出由 `src/agents/streaming/events.py` 中的 `iter_chat_events()` 提供，事件类型见[流式事件](#流式事件)：

- 每个 event 以 `data: <json>\n\n` 发送。
- 容量为 1 的 bounded queue 提供 backpressure，避免 token 在服务端堆积后突然批量刷新。
- 内部 session summarization token 不会转发到 UI；压缩发生时只发送一次 `Compressing conversation context…` status。
- `ChatTraceRecorder` 为每个 parent/label 分别维护 open reasoning segment；即使并行 worker 的 SSE event 交错，也会合并回各自的 segment。
- 转发到 orchestrator stream 的 worker event 携带 `parent_id`（delegate 唯一的 tool-call ID）和 `parent_name`（其 tool name，仅供显示），名字不作为 identity；旧的 name-only event 仍可通过 fallback 读取。
- 浏览器每个 animation frame 最多更新一次 open reasoning 节点，并行 parent 切换时不重建完整 live trace DOM。
- model 仍在写出某个工具调用时会发送 `tool_progress` event，浏览器据此立即创建 provisional tool row；后续 `tool_start` 再补齐稳定 call ID 和有界 input，避免参数尚未结束时界面看起来卡住。
- worker 最终回答的 delta（`agent_result_delta`）会流入其最后一个 reasoning/tool step 之后的独立 `Agent result` 框；若该 model turn 转而调用工具，临时结果框会被重置（`agent_result_reset`），随后由 `tool_end` 的有界 worker summary 完成规范化。该 result 同时关闭 delegate 的 parent scope，后续无 parent 的 reasoning 会在 worker 卡片外按 orchestrator reasoning 渲染。orchestrator 流式输出最终答案时 live trace 保持可见，完成后再转为常规折叠 trace。
- UI 断连时设置 cancel event，并调用 `cancel_session()`。
- 原执行在后台继续消费事件直到同步 delegate 返回，期间保持项目执行锁；不再发送 UI 事件。被取消的 turn 不会因为迟到的回答变成 completed，也不会执行语义提取。
- graph 内部的 `done` 会转成 `phase="finalizing"` 的 `status` event，期间保存 assistant turn，执行 fact extraction，以及按策略到期的 summary refresh。只有 request receipt 已持久保存、执行锁已释放，route 才发送终态 `done`；浏览器在此之前保持输入区忙碌状态。
- 异常时尝试 repair checkpoint，然后发送 `error` event。

SQLite 是 chat 的权威存储；`chat/transcript.md` 是通过原子重建生成的人类可读视图：每轮结束时重建一次，文件 API 列出 `chat/` 或读取 `chat/transcript.md` 时也会重建，不会按 token 更新。

### 流式事件

`src/core/stream_events.py` 声明所有生产者（graph stream、worker 调用、turn pipeline、API transport）和消费者（trace recorder、CLI renderer、Web UI）共用的事件词表。`StreamEventType` 定义事件类型，每种类型对应一个列出字段的 `TypedDict`，`validate_stream_event()` 按这些字段校验单个事件：

| `type` | 字段 | 含义 |
| --- | --- | --- |
| `status` | `message`，可选 `phase`、`request_id`、`replayed` | 等待提示中的进度文字；`phase` 标记 `finalizing` 等传输阶段；`replayed` 表示对重复 request ID 的回放。 |
| `thinking` | `text` | 下一段模型 reasoning。 |
| `thinking_withheld` | `reasoning_tokens` | 模型推理了，但服务端没有返回任何文本（reasoning summary 为空，或只有 token 计数）；计数未知时 `reasoning_tokens` 为 0。 |
| `final_start` | — | 最终回答开始，随后是 `token` event。 |
| `token` | `text` | 下一段最终回答。 |
| `reset_stream` | — | 丢弃已流出的最终回答；它已被一次工具调用取代。 |
| `tool_start` | `id`、`name`、`label`、`input` | 工具调用开始；`input` 是参数的有界预览。 |
| `tool_progress` | `id`、`name`、`label` | model 仍在写出某个工具调用。 |
| `tool_end` | `id`、`name`、`label`、`output`、`image_paths`，可选 `status`、`interactions` | 工具调用返回；`output` 是返回内容的有界预览。 |
| `agent_result_delta` | `text` | 下一段 worker 最终回答，显示在对应 delegate 下。 |
| `agent_result_reset` | — | 丢弃已流出的 worker 回答。 |
| `done` | `reply`，可选 `thread_id`、`project_id`、`request_id`、`replayed`、`image_paths`、`status`、`recovery` | 本轮回答。graph 发出 `reply` 和 `thread_id`；API 的终态 event 还携带 `ChatResponse` 的其余字段。 |
| `error` | `message` | 本轮失败。 |

`WORKER_FORWARDED_EVENTS` 列出 worker 从自身 graph 转发到 orchestrator stream 的类型：`tool_start`、`tool_end`、`thinking`、`thinking_withheld`、`status`、`agent_result_delta` 和 `agent_result_reset`，这些 event 同时携带 `parent_id` 和 `parent_name`。进程内消费者可能看到私有的 `_raw_*` 字段，event 离开进程前会被移除。

trace recorder 将 `thinking_withheld` 保存为 `reasoning_withheld` trace event（`input_json` 为 `{"reasoning_tokens": N}`），因此 `GET /api/projects/{id}/history/{turn_id}` 会返回它；它不会加入本轮的 reasoning 文本。Web UI、CLI 和 `chat/transcript.md` 显示类似 "Reasoned for N tokens; the provider returned no summary." 的提示。

Web UI 从 `web/assets/js/stream_events.js` 导入相同的类型名。该文件是生成的：修改 `StreamEventType` 后，在激活 `GBclaw` 环境后于仓库根目录运行 `PYTHONPATH=src python -m core.stream_events`；加 `--check` 只报告不一致而不写文件（退出码 1）。

### `POST /api/chat/stop`

用于用户主动停止当前 run。

流程：

1. 校验当前前端与正在执行的 thread；其他前端或不匹配的 thread 返回 409。 stop 锁阻止清理过程中开始下一轮。
2. 核验提供的 `request_id`，再给精确 execution owner 原子投递共享停止信号。 HTTP 请求可以由另一个服务 worker 接收；执行者读取信号、取消资源等待并终止自己的 LAMMPS/Coding subprocess。
3. 仅在没有执行 owner 时修复 checkpoint；活跃执行的恢复由正常清理或下一轮准备完成。
4. 找到 active assistant turn 并标记为 interrupted。其 reasoning 取自 `partial_trace` 中 `kind: "reasoning"` 的片段，否则取 legacy `reasoning`，再否则取已持久化的 reasoning trace event。已持久化的 trace event 保留；`partial_trace` 中的其他片段（tool call、agent result）不会写入。

`ChatStopRequest` 兼容 `partial_trace` 和 legacy `reasoning`。停止操作不会再追加一条独立的 interruption record。活跃 stop 返回 `status="stop_requested"`，线程实际退出可能更晚。提供 ID 但没有活跃执行时返回 `not_running` 且不改对话历史。尚未准入的 ID 会保存取消记录，阻止稍后抵达的同 ID chat 启动；已有终态结果保持原样。不匹配的活跃 ID 或已登记 thread 返回 409。省略 ID 保留按当前 thread 停止的兼容语义，不能防止迟到 stop；没有执行时会修复 checkpoint、中断 running assistant turn 并返回 `status="stopped"`。重复中断保持终态历史；活跃 Coding 的 workspace 等 worker 关闭 backend 后再清理，stop 只标记 run cancelled。原 chat 连接仍在线时，协作取消返回 `interrupted` classic response 或 SSE done，不误报 500。取消优先于同时发生的 context recovery 异常，且不会进入完成后的语义提取。

Web UI 的停止按钮只在本轮运行时显示。点击后携带本轮 `request_id` 与 `partial_trace` 调用 `/api/chat/stop`，随后在本地中止流式请求，把运行中的工具行标为 Stopped，并显示 "Stopped by user. Partial progress was saved."；后端未确认时提示仅完成了本地停止。

## Project API

定义在 `src/interfaces/api/routers/projects.py`。

| Method | Path | 作用 |
| --- | --- | --- |
| `GET` | `/api/projects` | 列出 projects，按创建时间倒序 |
| `POST` | `/api/projects` | 创建 project |
| `WS` | `/api/projects/{id}/connection` | 独占项目并取得写入 token |
| `GET` | `/api/projects/{id}` | 获取 project metadata |
| `DELETE` | `/api/projects/{id}` | 删除整个 project workspace |
| `GET` | `/api/projects/{id}/memory` | 返回 summary metadata，以及存在时的精确 active/pending plan 记录 |
| `GET` | `/api/projects/{id}/history` | 返回 user/assistant transcript，用于 UI replay |
| `GET` | `/api/projects/{id}/history/{turn_id}` | 返回单个 turn 及其 reasoning 和 trace events |
| `GET` | `/api/projects/{id}/files` | 列目录 |
| `GET` | `/api/projects/{id}/files/stat` | 获取文件/目录 stat；目录还返回文件浏览器轮询使用的 listing fingerprint |
| `GET` | `/api/projects/{id}/files/content` | 读取 text preview |
| `GET` | `/api/projects/{id}/files/download` | 下载文件 |
| `GET` | `/api/projects/{id}/files/raw` | 按推测的 media type inline 返回文件（图片预览与聊天图片） |

所有 file routes 都通过 `project_workspace/artifacts/files.py` 的 `prepare_project_path()` 解析路径：它调用 `resolve_project_path()` 拒绝逃逸项目根目录的路径，并在请求指向 `chat/` 或 `chat/transcript.md` 时重建 transcript。

### 为什么通过聊天批准计划

批准计划是一项研究决定，应与任务要求和结果一起保存在对话记录中。因此，界面按钮通过公共聊天路径提交用户消息，而不直接编辑计划文件。

草案等待批准时，composer 会在输入框上方显示标题、draft ID、revision 和 step 数。 **Approve & execute** 会发送一条显式用户消息；**Disagree / request changes** 会把修改模板填入同一个聊天框，用户编辑后再发送。自然语言批准/反馈也走同一 chat path。panel 从 memory API 的 `pending_plan` 恢复，因此刷新页面或切换 project 后仍可见。

file routes 会暴露 pending `plan-draft.md`、active `plan.md`、`memory/plans/drafts/` 下的审计 JSON，以及 current/revision JSON。文件浏览器仍为只读，状态只能由 SQLite-backed plan tools 修改。

## Registry API

定义在 `src/interfaces/api/routers/registry.py`。`GET /api/registry` 返回 Web UI 标注和处理流式事件所需的后端声明，浏览器无需自行保存一份副本（`RegistryResponse`）：

| 字段 | 内容 |
| --- | --- |
| `delegate_labels` | 每个已注册 worker 的 delegate tool name → 显示名称，例如 `delegate_csl` → `Structure agent`。 |
| `preserve_final_stream` | 设置了 `ToolCapabilitySpec.preserve_final_stream` 的工具名（已排序）。orchestrator 调用这些工具时保留已流出的最终回答，不将其重置。 |
| `chat_commands` | `CHAT_COMMANDS` 中每个命令对应一条 `ChatCommandRecord`（`command`、`planning_mode`、`description`、`missing_task_message`）。 |

`web/assets/js/registry.js` 在启动时与 health check 一起加载 registry。请求失败时，界面退回到由工具名生成的显示名称，命令菜单为空；服务端仍会解析 `/plan` 和 `/noplan`。

## DichromaticMap API 与悬浮工具

接口定义在 `src/domains/structure/routes.py`，通过 `src/domains/structure/extension.py` 中 Structure extension 的 `routers` 声明挂载。数值预览不调用 agent 或 LLM；项目导出与 Structure worker 的 `render_dichromatic_map` 使用相同的无窗口 CLI、run 记录和 memory fact。

| Method | Path | 作用 |
| --- | --- | --- |
| `GET` | `/api/dichromatic-map/options?lattice=FCC&axis=110` | 返回 `geometry`、`presets`、`defaults` 和资源 `limits`；geometry 包括层名称及 `angle_range.minimum_deg` / `maximum_deg`。 |
| `POST` | `/api/dichromatic-map/preview` | 接收 `DichromaticMapRequest`，返回点坐标、同层重合点、可选近邻配对、精确胞信息和视野边界，不保存项目 run。 |
| `POST` | `/api/projects/{project_id}/dichromatic-map/export` | 接收 `{parameters, purpose, source_run_id?}`，返回含 run/artifact 引用及紧凑科研结果的 `RunToolResult`；可选来源 run 必须已在当前项目完成。 |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests` | 用 `{parameters, purpose, source_run_id?}` 准备选胞请求。 |
| `GET` | `/api/projects/{project_id}/dichromatic-map/requests?status=pending` | 刷新网页或切换项目后恢复该项目的请求。 |
| `GET` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}` | 读取权威几何、候选 ID、revision、选点和结果。 |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}/preview` | 用 `{center, width, height}` 扩展待选请求的视野，保留原来的物理几何。 |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}/selection` | 提交四个 `candidate_ids` 和 `expected_revision`，校验并记录用户选定的胞。 |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}/cancel` | 用 `expected_revision` 取消待选请求。 |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}/apply` | 对确认的胞施加应变，支持应变和旋转限值；Structure tool 使用同一服务。 |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}/edge-analysis` | 使用已保存应变分析所选的四条边，或直接复用已有测量和标注。 |

预览请求例如 `{"lattice":"FCC","axis":"110","width":12,"height":9}`。导出时将该对象放入 `parameters`；`purpose` 说明保存目的，省略时使用浏览器探索的默认描述。完整参数与科研范围见 [工具说明](tools.md#晶粒双色图)。不支持的几何、无效层编号或过大的投影网格会返回校验错误。

DichromaticMap 悬浮窗由气泡打开，最小化后恢复为气泡。控件支持晶格、轴、角度与预设选择，两侧晶粒各自的层显示和颜色、显示旋转、同层重合点与精确胞叠加，以及可选的局部近邻配对。标记外观使用固定默认值。图中可平移、缩放和测量两个原子的投影距离；拖动和缩放会通过数值接口补充新视野中的原子，由 canvas 绘制，不反复下载渲染 PNG。坐标使用 a0，明确输入的晶格常数用于 Angstrom 换算。**Save current view** 将当前可见视野及其参数作为永久 PNG/JSON 写入当前项目（尚未打开项目时先创建）；视野每个方向须在 0.1–100 a₀ 之间。**Use in chat** 先保存图像，或复用未改变的已有导出，再把 purpose、 run ID、PNG 路径、参数和已选择的测量结果填入聊天框，由用户发送，不会自动发消息。仅浏览或预览不会创建记忆 run。

交互选 near-CSL 胞时，agent 工具在普通预览旁发送紧凑的 `interactions` 引用。悬浮窗读取该请求，让用户选择四个候选点，并将 ID 提交给 selection 接口。**Confirm cell and continue** 保存选择后开启下一轮对话，由 agent 读取记录并施加应变；若当前对话仍在运行，就等它结束后再继续。准备选点不会让 agent 调用一直阻塞。项目隔离、状态和记忆来源见 [完整流程](dichromatic-map.md)。网页使用数值库与 canvas，不嵌入桌面 Qt 窗口。

## 聊天中的科研图片

图片解析基于项目 artifact 引用，不依赖某一个固定 PNG 文件名。 `project_workspace/artifacts/presentation.py` 在缩短 trace 预览之前，从完整的嵌套 tool result 提取图片路径。Streaming event 和 history response 通过 `image_paths` 保留多张图片，即使显示的工具输出被截短也不会丢失后面的引用。`web/assets/js/artifact_images.js` 统一解析项目路径，把 Markdown 图片和链接映射到 file API，补上回答中尚未显示的工具图片，并按规范化路径去重。双色图、OVITO 渲染、应力曲线、NEB 曲线等项目图片均使用同一路径，覆盖实时对话和历史回放。其他项目的路径及目录穿越会被拒绝，查看大图使用现有图片弹窗。

## Schemas

`src/interfaces/api/schemas.py` 定义 Pydantic request/response：

- health: `HealthResponse`
- project: `ProjectSummary`、`ProjectResponse`、`ProjectListResponse`、`CreateProjectRequest`
- file browser: `FileEntryResponse`、`FileListResponse`、`FileStatResponse`、`FileContentResponse`
- memory/history: `ProjectMemoryResponse`、`HistoryTurnSummaryResponse`、`HistoryTurnDetailResponse`、`TraceEventResponse`、`ProjectHistoryResponse`
- chat: `ChatRequest`、`ChatResponse`、`ChatStopRequest`、`ChatStopResponse`
- registry: `RegistryResponse`、`ChatCommandRecord`

## Web UI 文件

静态前端在 `web/`（入口页面 `web/index.html`），由 FastAPI mount。

关键 JS：

- `web/assets/js/app.js`: 主聊天应用逻辑。
- `web/assets/js/config.js`: API endpoint / runtime 配置。
- `web/assets/js/registry.js`: 启动时加载 `GET /api/registry`，向其他模块提供 delegate 显示名称、preserve-final-stream 集合和斜杠命令。
- `web/assets/js/stream_events.js`: 流式事件类型名，由 `src/core/stream_events.py` 生成。
- `web/assets/js/chat_commands.js`: 基于 registry 命令列表的斜杠命令匹配与解析。
- `web/assets/js/project_connection.js`: 项目占用 WebSocket 与写入 token header。
- `web/assets/js/files.js`: 项目文件浏览器：目录自适应轮询（基于 `files/stat` fingerprint）、文本预览及日志类文件实时追踪、图片在图片查看器中打开、下载、未打开项目时的项目选择列表，以及文件面板底部的引用信息（窄屏堆叠布局下隐藏）。
- `web/assets/js/content.js`: 页面文案（含引用信息）和各主题使用的项目 icon 地址。
- `web/assets/js/markdown.js`: markdown / KaTeX / syntax highlight 处理。
- `web/assets/js/autoScroll.js`: chat auto scroll。
- `web/assets/js/panelResize.js`: 面板大小调整。
- `web/assets/js/theme.js`: 主题。
- `web/assets/js/image_modal.js`: 全屏图片查看器：打开时整图适配并居中（不超过原始像素尺寸）；滚轮、−/+ 按钮或 `+`/`-` 键以光标为中心缩放（10% 或更小的适配比例，至 1200%）；拖动平移；双击在适配与 100% 之间切换；**Reset** 或 `0` 恢复适配；Esc 关闭。
- `web/assets/js/artifact_images.js`: 项目图片引用解析和聊天渲染。
- `web/assets/js/dichromatic_widget.js`: 悬浮图控件、canvas 交互与项目/聊天衔接。

前端依赖的 vendor assets 在 `web/assets/vendor/`，样式在 `web/assets/css/`（`main.css`，以及双色图悬浮窗使用的 `dichromatic_widget.css`）。

## CLI

`src/interfaces/cli/` package 提供命令行聊天入口（在仓库根目录运行 `PYTHONPATH=src python -m interfaces.cli`，或运行会激活 `GBclaw` Conda 环境的 `./start_cli.sh`；两者都需要交互式终端和仓库根目录下的 `.key` 文件）。`main.py` 构建 model router、启动 extension、创建 orchestrator、显示项目菜单，退出时取消 session、关闭 checkpointer 并停止 extension；`chat.py` 运行聊天循环，每一轮在工作线程上执行，主线程渲染事件并处理 Ctrl+C；`input.py`、`render.py` 和 `projects.py` 分别提供 readline 输入（斜杠命令补全、按项目保存的历史、反斜杠和 ``` 续行、整段粘贴）、流式事件渲染和项目菜单。它和 API 使用同一套：

- `ProjectStore`
- `ModelRouter`
- orchestrator graph
- `start_extensions()` / `stop_extensions()`
- 公共 turn pipeline（`src/interfaces/turn.py` 中的 `run_turn()`，包括 `prepare_project_session()`）
- `/plan`、`/noplan` 使用的 `CHAT_COMMANDS`
- project history
- 流式事件词表（`src/core/stream_events.py` 的 `StreamEventType`），由 CLI 自己的 `render.py` 渲染
- 项目占用（`ProjectFrontendLease`，整个会话期间持有）与 `TurnOwnership`；后者在主线程创建，因此 Ctrl+C 可以调用它的 `cancel()`（设置 session 标志并终止已注册的子进程），再通过 `TurnControl` 取消 turn 协程以立即切断进行中的模型输出流，随后像 API 的 stop 路由一样调用 `cancel_running_runs_for_chat()`

CLI 将这些公共服务适配为终端输入、斜杠命令、项目切换、历史展示、流式输出和中断处理。科研执行或持久化行为的改动应放在公共服务中，让浏览器和终端获得一致的行为。
