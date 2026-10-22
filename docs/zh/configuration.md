# Configuration

本文说明如何选择模型、接入科研运行环境，以及设置资源上限。首次安装请先阅读[快速开始](getting-started.md)。通常只需配置模型端点和本机环境名称，其余预算可以先使用默认值。

## 设计逻辑

GBclaw 将科研决策、模型访问和机器资源分别管理。复杂规划可以使用更擅长推理的模型，常规分析可以使用另一种模型，但它们仍遵循相同的项目记录和执行规则。配置决定角色可以使用哪些资源；工具自身仍负责科研参数与结果的校验。

| 想调整的内容 | 配置位置 | 分开管理的原因 |
| --- | --- | --- |
| 模型凭据、端点和角色路由 | 仓库根目录 `.key` | 更换模型不需要修改科研工具。 |
| 模型输入输出上限和上下文分配 | `.key`，包括 `[runtime_context]` | 每次请求都必须符合所选端点的实际容量。 |
| Conda 环境名称、调度、超时和进程资源 | 仓库根目录 `.env` 或进程环境变量 | 本机运行设置独立于提示词和模型凭据。 |
| 生成的 Python 脚本可以使用的环境 | `src/domains/coding/environments.toml` | 由宿主程序声明 coding worker 可使用的运行环境。 |
| Agent 职责和领域操作说明 | 各 agent 与领域旁的提示词模块（见 [Prompt files](#prompt-files)） | 提示词指导工具使用，执行服务负责约束实际行为。 |

需要区分三类上限：模型能读多少、能生成多少，以及工具能使用多少计算资源。调大其中一类不会自动扩大另外两类。Project summary 和 session summary 还各有较小的内容上限，避免上下文窗口变大后，摘要也不断膨胀成历史存档。详细记录始终可以通过记忆工具查询。

这些边界的整体设计见[架构与设计](architecture.md)，下文列出当前配置项及默认值。

## LLM config

`src/core/llm/config.py` 从仓库根目录 `.key` 读取模型配置。

两种模式：

### 单默认模型

当 `[settings] use_tier_routing = false` 时，必须有：

```ini
[default]
provider = openai
api_mode = responses
api_key = ...
base_url = ...
model = ...
temperature = 0.3
timeout = 120
thinking = disabled
```

所有 role 都使用 default endpoint。

### Tier routing

当 `use_tier_routing = true` 时，必须有：

```ini
[model_routing]
default = medium
orchestrator = premium
planning = premium
memory = medium
structure = medium
lammps = medium
analysis = medium
coding = medium
literature = premium

[premium]
...

[medium]
...

[light]
...
```

支持 tier 名：

- `premium`
- `medium`
- `light`

`ModelRouter.for_role(role)` 会先查 role，再 fallback 到 `model_routing.default`。

`planning` role 只用于按需深度规划，可以单独路由到推理能力更强的模型，而不提高所有常规 orchestrator/worker 调用的成本。

`coding` 角色用于临时 Deep Agents worker；未显式配置时使用 `model_routing.default`。

## Coding 沙盒

Coding worker 要求 Bubblewrap（默认 `/usr/bin/bwrap`）。可用的只读 Conda 环境路径由 `src/domains/coding/environments.toml` 的宿主白名单管理；可用 `GBCLAW_CODING_ENVIRONMENTS_FILE` 指向另一份注册表。注册表的相对路径以仓库根目录为基准解析。

默认别名 `current` 跟随运行服务的 Python 环境；`lammps` 从 `GBCLAW_LAMMPS_CONDA_ENV` 读取实际 Conda 环境名。可选别名 `ase` 从 `GBCLAW_ASE_CONDA_ENV`（默认 `GBclaw-ase`）读取 Atomic Simulation Environment 脚本使用的环境名。别名不随实际环境改名而变化。注册不会自动安装环境或依赖；未安装的环境仍在目录中显示为不可用，不阻止应用启动。

资源边界由 `.env` 中的 `GBCLAW_CODING_*`（包括 Deep Agents 图步数上限 `GBCLAW_CODING_RECURSION_LIMIT`，默认 `200`）和可选 `GBCLAW_BWRAP_PATH` 控制，由 `src/domains/coding/settings.py` 读取。Coding 专属的单任务默认计算份额与全局总预算分别由 `GBCLAW_CODING_AGENT_COMPUTE_SLOTS=4` 和 `GBCLAW_CODING_TOTAL_COMPUTE_SLOTS=36` 控制。外部网络禁用不可关闭；MPI 只能使用私有网络命名空间。详见 [coding-agent.md](coding-agent.md)。

单沙盒默认每条命令最多运行 300 秒、每个任务最多运行 1800 秒，可写工作区为 128 MiB，每进程地址空间为 4096 MiB，CPU 时间为 600 秒，最多运行 96 个进程；对应变量为 `GBCLAW_CODING_COMMAND_TIMEOUT_SEC`、`GBCLAW_CODING_TASK_TIMEOUT_SEC`、`GBCLAW_CODING_MAX_WORKSPACE_MB`、 `GBCLAW_CODING_MEMORY_MB`、`GBCLAW_CODING_CPU_SECONDS` 与 `GBCLAW_CODING_MAX_PROCESSES`，实际 process cap 还会按该次 compute allocation 收紧。

内存、累计 CPU 时间和任务数量预算也覆盖整个进程树。 `GBCLAW_CODING_RESOURCE_MODE=auto|monitor|cgroup` 默认 `auto`；没有可用的 `GBCLAW_CODING_CGROUP_ROOT` 委派时明确回退到采样监测。委派目录必须是 `/sys/fs/cgroup` 下的绝对目录，并提供 `cpu`、`memory`、`pids` controller。`cgroup` 模式下若无法强制执行限制（包括内核不支持 `cgroup.kill`），coding 沙盒拒绝启动、该次委托失败；服务本身仍会启动。 `GBCLAW_CODING_MEMORY_MB` 同时作为树内存预算，`GBCLAW_CODING_MAX_PROCESSES` 在树级统计进程和线程；任务 timeout 也包含准入等待与模型等待。采样精度边界见 Coding 文档。

## 记忆模型调用

`memory` 角色为回答完成后的事实提取和摘要生成提供基础端点。每次辅助调用都会复制该端点配置、关闭 thinking 并清空 reasoning effort，不会修改其他角色使用的配置。如果模型的最低推理档不是“关闭”（GPT-6、o 系列、always-adaptive 的 Claude），它仍会以最低档推理；这类副本会在按答案大小设定的输出上限之上再加 4,096 token 的余量（不超过端点自身上限），避免推理 token 截断答案。事实提取请求窗口最多 64K token，其中先扣除输出上限和 1,024 token 余量（输出上限 8,192 时约 55K 输入 token），最多写 8,192 输出 token（20 条 mutation 及其值和摘录）；结论摘要 2,048；project summary 和 session summary 按字符上限的两倍 token 预留，避免中文被截断。Project summary 目标为 6,000 字符，最多 12,000 字符，同时受 `[runtime_context] project_summary_max_tokens` 和端点输出上限约束：实际上限为 `min(12,000, T − 256)` 字符，其中 T = min(project_summary_max_tokens, memory 端点输出上限, 24,000)；memory 端点关闭 thinking 且未设 `max_tokens`（默认 8,192）时为 7,936 字符。

## LLMEndpoint 字段

`LLMEndpoint` 包含：

- `provider`
- `api_mode`
- `api_key`
- `base_url`
- `model`
- `temperature`
- `max_tokens`
- `max_input_tokens`
- `timeout`
- `thinking`
- `reasoning_effort`

如果没有显式 `max_tokens`：

- thinking disabled: 8192
- thinking enabled: 32768

`thinking` 支持 `enabled` / `disabled`，也接受 true/false 风格别名。 `reasoning_effort` 统一使用 `low` / `medium` / `high` / `xhigh` / `max`。对于已知模型代际不支持的档位，适配层会映射到最近的可用档位，不要求用户填写 provider 专属值。

`max_tokens` 同时限制 thinking 与最终答案；`max_input_tokens` 是输入上下文窗口。适配层将生成上限转换为 OpenAI Responses 的 `max_output_tokens`、OpenAI Chat Completions 的 `max_completion_tokens`，或对应 provider 的 `max_tokens`。它不限制 Python 工具返回、嵌套 worker 历史或读取的报告；这些由统一结果投影和记忆分页读取控制。扩大 working 或打开 1M 不会扩大单次生成上限。

简洁输出提示词负责约束结论篇幅，同时保留推理和工具调用的生成额度。开启 `thinking = enabled` 时，建议省略 `max_tokens`（默认 32768）或至少设为 16384，并优先使用 `reasoning_effort = low`。将 thinking 与过小的预算（如 8192）一起使用，可能让推理耗尽输出额度，最终没有生成回答。

没有显式配置 `max_input_tokens` 时，短期上下文预算默认采用 262144 tokens；`GBCLAW_CONTEXT_1M=1` 时采用 1048576 tokens。这些是本地预算默认值；如果 endpoint 实际窗口更小，应显式填写更低的值。开关不会缩放显式填写的 endpoint 上限；可用的 model profile 上限也会约束运行时上下文。定义 `max_input_tokens` 会覆盖自动默认值。除非明确需要固定 endpoint 上限，并清楚它对两种上下文模式的影响，否则保持不定义。

四个字段在所有 provider 上语义一致：

- `max_tokens`：全部生成输出（包括 thinking 与最终答案）的硬上限。
- `max_input_tokens`：GBclaw 本地输入上下文上限，不作为生成参数发送给模型。
- `thinking`：统一的思考开关意图。
- `reasoning_effort`：统一的软推理深度意图。

适配层按 provider 转换：

- DeepSeek：发送 `thinking.type` 和 `reasoning_effort`；`medium` 映射为 `high`， `xhigh` 映射为 `max`，从 `reasoning_content` 读取推理。
- OpenAI：Chat Completions 使用 `reasoning_effort`；Responses 使用 `reasoning.effort` 和 `reasoning.summary=auto`。GPT-5.6 支持 `max`，较早的已知 GPT-5 版本会把 `max` 映射到其可用最高档。`thinking = disabled` 时，GBclaw 发送该代模型能接受的最低档：GPT-5 用 `minimal`，GPT-5.1–5.x 用 `none`，GPT-6 和 o 系列用 `low`，无法识别的部署名则不发送档位。
- Anthropic：Claude 4.5 及更早版本根据 `max_tokens + reasoning_effort` 自动推导旧式 `budget_tokens`；Claude 4.6 及更新版本自动使用 adaptive thinking 与 `output_config.effort`。Claude thinking block 会归一化成与其他 provider 相同的 UI 事件。

Reasoning 模型往往拒绝 sampling 参数。因此开启 thinking 时，适配层会省略 `temperature`；adaptive Claude 即使显式关闭 thinking，也会省略该字段。

对 `provider = openai`，`api_mode` 支持：

- `chat_completions`（默认）：兼容范围最广，最终答案仍会流式输出。只有网关显式返回非标准 `reasoning_content`、`reasoning`、`thinking` 或 `reasoning_details` 字段时，reasoning 面板才有正文；GBclaw 不会伪造 OpenAI 隐藏的内部推理。
- `responses`：使用 `/responses` 和 `output_version="responses/v1"`。开启 thinking 时会请求 `reasoning.summary = auto`，并把网关公开的 reasoning-summary block 流式显示到 reasoning 面板；endpoint 必须支持 Responses API 和 function calling。该模式会主动省略 `temperature`，因为 reasoning deployment 通常拒绝它，且企业内部模型名会使 SDK 无法按公开模型名自动完成这一步。

官方与企业私有 OpenAI 部署可共用 GBclaw 的字段结构，但 deployment 名称、API mode、上下文与限流、认证方式及是否公开 reasoning 都由 endpoint 决定。企业网关还可能把 deployment ID 映射到不同的实际模型版本，因此审计时应以响应 metadata 为准。

`provider = anthropic` 使用 Anthropic Messages API，也接受 `provider = claude` 别名。该 section 保持 `api_mode = chat_completions`；在统一配置里它只表示“不使用 OpenAI Responses”，不会被发给 Anthropic。`base_url` 可以是 Anthropic 官方 API 或兼容的 Messages endpoint。

## Runtime Context 与 Working Summary

### 如何选择预算

建议先保留默认值；如果模型端点的输入窗口较小，应明确设置它的 `max_input_tokens`。只有端点确实支持时才启用 1M 模式。上下文分配表示某一层最多可以使用多少空间，并不要求每次请求都填满。

两种摘要服务于不同目的：project summary 帮助 agent 找到项目的重要研究状态，session summary 帮助它衔接当前对话。两者都不需要重复完整工具输出。具体保存哪些记录、如何查回细节，见[项目记忆](project-memory.md)。

`[runtime_context]` 由 `src/core/settings/runtime_context.py` 读取，控制 user-facing orchestrator 的短期上下文，约束增量 project working-summary 流，并决定委托 worker 的压缩阈值。委托出去的 worker（structure、LAMMPS、analysis、planning、coding）每个任务新建、用完即弃，采用更简单的策略：运行时窗口（`total_context_tokens`，同时受 endpoint 与 model profile 的 `max_input_tokens` 限制）扣除输出预留（endpoint 输出上限与 `safety_margin_tokens` 取较大者）和 system prompt/tool schema 后作为一个池子，达到 `worker_compact_fraction`（默认 0.8；256K、32K 预留时为 0.8 ×（229,376 − 静态 token），最多约 179K）时压缩。压缩保留任务消息和最新的完整工具调用块（约池子的四分之一），其余由 worker 自身端点在关闭 thinking 的情况下替换为一份摘要：已完成的工作只留结果，进行中的工作保留继续所需的细节。留在上下文里的摘要最多占池子的六分之一；放不下的已完成结果会写入归档文件并在原位留下指针，不会丢弃。被压掉的工具结果会先归档，可用 `read_tool_output` 读回。provider 返回上下文超限时会压缩请求并重试一次；单个工具调用块本身超过池子时，委托以结构化的上下文错误（`worker_block_exceeds_context`）失败。Coding worker 保留 Deep Agents 的摘要器及其向沙盒 backend 的历史卸载，但使用相同的阈值、token 计数、摘要协议和 COMPLETED 溢出归档；provider 超限时由 Deep Agents 摘要后重试，而不是抛出上述错误。`enabled = false` 时 GBclaw 不为 worker 安装压缩；coding worker 此时只保留 Deep Agents 自身的摘要默认值。

**除非你清楚自己需要怎样的覆盖，否则保持 token 预算不定义。** 显式定义会覆盖 GBclaw 内置默认值。默认值会随 `GBCLAW_CONTEXT_1M` 自动缩放；下面注释中的数值仅说明 256K 基础模式，无需取消注释就会生效。

```ini
[runtime_context]
enabled = true
# 显式定义会覆盖内置默认值；除非清楚覆盖的影响，否则保持不定义。
# total_context_tokens = 262144
# active_context_tokens = 65536
# project_summary_max_tokens = 32768
# summary_model_max_tokens = 32768
# memory_context_tokens = 65536
# safety_margin_tokens = 32768
# project_summary_working_set_max_tokens = 65536
# worker_compact_fraction = 0.8
```

相关环境变量 / `.env`（不在 `.key`）：

```bash
GBCLAW_CONTEXT_1M=0
# GBCLAW_TOOL_KEEP_RESULTS=4
```

这里 K 表示 1024 tokens。默认分配如下：

| 上下文组成 | 默认模式 | `GBCLAW_CONTEXT_1M=1` |
|---|---:|---:|
| 总上下文 | 256K | 1M（1024K） |
| Project summary 上下文预留 | 不超过 32K | 不超过 128K |
| 新 project summary 内容 | 目标 6,000 / 最多 12,000 字符（memory 端点保持默认 8,192 token 输出上限时为 7,936） | 相同的固定上限 |
| Session summary 读取额度 | 不超过 32K | 不超过 128K |
| 新 session summary 内容 | 目标 2,000 / 最多 4,000 字符 | 相同的固定上限 |
| Working：近期未压缩对话 | 不超过 64K | 不超过 256K |
| 实时记忆：计划状态和检索结果 | 不超过 64K | 不超过 256K |
| 生成输出与安全预留 | 32K | 128K |
| 剩余 system prompt 与 tool schema 空间 | 32K | 128K |

这些数值是上限和预留，不会在每次请求中填满。设置 `GBCLAW_CONTEXT_1M=1` 后，默认运行时 token 预算自动乘以 4。刻意设置的 `[runtime_context]` 覆盖值也按此规则缩放，因此始终填写 256K 基础模式的数值。模型配置中显式填写的 `max_input_tokens` 则保持固定，可能阻止运行时使用完整 1M 预算。独立的项目摘要更新输入预算也从 64K 变为 256K。 `GBCLAW_TOOL_KEEP_RESULTS` 等数量型配置不缩放。修改后需要重启服务。

- `total_context_tokens`：运行时总预算，同时受选中 endpoint 的 `max_input_tokens` 限制。开关不会扩大 provider 的实际窗口，也不会覆盖显式配置的 endpoint 上限。
- `active_context_tokens`：近期未压缩对话的上限。项目摘要、压缩对话和实时记忆使用各自预算。
- `GBCLAW_TOOL_KEEP_RESULTS`：普通上下文编辑保留的最近工具结果数量，默认 4。摘要调用前，如果完整请求仍然过大，近期结果也可能被替换为经过核验的归档引用卡；这一设置不能绕过上下文硬上限。
- `project_summary_max_tokens`：控制项目摘要生成、写入校验、动态注入和 orchestrator 预留的独立 token 上限。新摘要目标为 6,000 字符，最多 12,000 字符；更小的 token 额度会进一步降低内容上限并保留必要的格式空间。字符上限不随 1M 模式扩大，也不等同于 provider 的 token 计数。已有摘要超出 token 上限时不会注入模型；生成时还遵守 memory 端点的输出上限。
- `summary_model_max_tokens`：session summary 的 token 额度，覆盖已有 checkpoint 摘要以及替换前的校验。新摘要目标为 2,000 字符，最多 4,000 字符；token 额度较小时会进一步收紧，字符上限不随 1M 模式扩大。生成端点同时遵守输出上限和小型写入预算；已有摘要在有效替换成功前仍使用较大的读取额度。
- `memory_context_tokens`：每次请求中 pending/active plan pointer 与检索记忆的总预算。每次 orchestrator 模型调用前从 SQLite 重读计划指针；过大的查询结果以完整 tool result 或检索指针表示，原始数据库和 checkpoint 记录仍然保留。
- `project_summary_working_set_max_tokens`：单次增量 project summary 更新的最大输入，包含上一版摘要或有界数据库索引、当前优先事项、summarizer prompt 和按 revision 排序的变更 runs/facts；必须大于 `project_summary_max_tokens`。实际值还会受 memory model 输入窗口扣除生成/安全预留后的容量限制。这是独立摘要请求的输入预算，不是 orchestrator 的 working 对话预算。
- `safety_margin_tokens`：主请求的最低预留，已包含生成输出；如果 endpoint 的输出上限更大，则按更大的输出额度预留。
- `worker_compact_fraction`：委托 worker 的历史达到可用窗口的这一比例时压缩（默认 0.8，范围 0.3–0.95）。它是比例，不随 1M 模式缩放。调低可以让长任务每次调用更便宜；完整历史仍可通过归档的工具输出读回。

默认未压缩对话达到 48K 时触发摘要压缩，目标保留 24K 近期消息，working 硬上限仍为 64K。这些阈值随有效 working 预算缩放，包含 1M 模式；检索记忆不占用该触发预算。endpoint 窗口更小、输出预留更多或 system/tool 开销更大时，会相应缩减有效分配。无效配置会明确报错，不会发送超出窗口的请求。

### 对话上下文增长时

压缩先在本次请求中缩小工具结果的展示形式，不修改原始记录。完整引用索引仍放不下时，只有核验过归档内容哈希、项目与调用权限、工具身份，以及结果对应关系后，才允许替换成小型归档引用卡。`read_tool_output` 可以分页读回完整结果，无需重新计算。用户消息、待决选择和原始 checkpoint 仍保留；引用卡只是预览，不构成新的科研证据，也不是新的持久事实。

如果对话仍有容量压力，或需要转换旧版摘要格式，再调用 session summary 模型。空白、格式错误、过长或明确截断的摘要最多纠正一次，每次请求都计入完整源内容和纠正指令。传输或认证失败不会增加这类重试。确定性缩减后仍能放入窗口的请求可以继续；无法符合硬上限时保留 checkpoint，并返回 `paused`。同一请求内失败的旧格式转换会在 checkpoint 中记录一个私有指纹，不会在每一步工具调用后反复执行；新的用户请求可以重试。该标记不会绕过 working/summary 硬上限，也不包含任何对话文本。恢复诊断只记录失败类别、尝试次数和预算用量，不记录 provider 错误正文或完整提示词。重试已暂停的聊天需要新的 `request_id`，复用旧 ID 会得到已保存的响应。

### 摘要保存在哪里

Project semantic summary 和 session summary 是两层不同内容：前者保存在 `memory/index.sqlite`，通过 Runtime Context 动态加入 system prompt；后者由 LangChain middleware 保存到 thread checkpoint，只负责当前对话的连续性。

默认 thread ID 保存在 `project.json`。间隔很久后重新进入项目，或重启服务后打开项目，仍会使用同一线程和它的 session summary，而无需把完整聊天存档重新发送给模型。请求准备、界面预览和每次模型调用的动态注入复用项目记忆快照读取逻辑；读取快照不会改变线程标识。

### 保持项目摘要精简

Project summary 优先保留目标、长期约束、未完成事项、阻碍和关键证据引用，先压缩早期已完成工作的叙述、参数清单和重复报告。生成输入由有界增量记录与当前优先事项组成；过长的旧摘要仅在此次生成输入中替换成有界数据库索引。最多执行一次初始生成和一次纠正；持续出现长度、完成状态或必需引用问题时，使用有界索引作为摘要。服务端或网络异常、无效证据引用会保留当前摘要。

替换摘要时，完整旧版本与新版本一起在 SQLite 事务中归档和更新。压缩不会删除 facts、runs、artifacts 或 conclusions。通过 `query_project_memory(entity="summary_archive", query=...)` 搜索旧摘要，再用 `archive_id=...` 读取；可读 Markdown 视图位于 `memory/summary_archive/`。当前精确记录仍通过普通 fact/run 查询获取。实现细节见[项目摘要模块](../../src/project_memory/summary/README.md)。

回答完成后的记忆处理期间，流式 API 发送 `finalizing` 状态。只有结果持久保存且执行占用已释放后才发送终态 `done`，浏览器会等到那时才恢复发送按钮。

### 保持对话连贯

Session summary 只保留当前主题、有效约束、未完成事项和最多三个检索引用。工具测量值、科学解释和已完成工作清单属于持久结果与项目记忆。摘要模型接收工具状态、引用卡和调用身份，而不是原始结果或执行参数；用户原文、紧邻的上一条回答及尚未解决的选择受到保护；如果这条回答本身就放不下工作区，它也会被纳入摘要，全文仍保存在聊天记录里。已被使用的小型输出同样会得到归档引用，保存的原始 ToolMessage 不变。完整源提示词、当前保留的上下文和纠正指令都计入摘要输入预算。摘要超限时不会靠截断强行通过校验。相关实现位于 [`src/agents/middleware/`](../../src/agents/middleware/README.md)。

这些预算不改变证据来源。Project summary 分别保留 `Tool results`、 `Conversation-derived context`、`Agent reports`、`Workflow state` 四个章节，并校验 fact 引用的来源。对话提取继续保留；`source_type` 由已保存的 provenance 推导，不通过配置开关自行选择。详见[事实来源](project-memory.md#事实来源)。`GBCLAW_TOOL_KEEP_RESULTS` 统计近期工具结果数量，不是对话轮数。

## Model provider

`src/core/llm/factory.py` 按 endpoint 的 `provider` 值选择已注册的 provider adapter。内置 adapter 位于 `src/core/llm/providers/`：

- `deepseek`
- `openai`（也接受 `openai-compatible` 或 `openai_compatible`）
- `anthropic`（也接受 `claude`）

领域扩展可在 `DomainExtension.llm_providers` 中声明 adapter factory 来增加 provider；API 和 CLI 在启动时注册它，此后 `.key` 即可使用该名称。详见 [agent 运行时](agent-runtime.md#llm-构建和-thinking-适配)。

thinking 适配：

- DeepSeek native 或 DeepSeek-compatible OpenAI endpoint：使用 `extra_body.thinking.type`，当前 turn 中带 tool call 的步骤回传 reasoning。
- OpenAI 官方/企业 Chat Completions：使用 `reasoning_effort`；兼容网关通过 `ChatOpenAIWithReasoning` 保留非标准 reasoning 字段。
- OpenAI 官方/企业 Responses：使用 `reasoning.effort` 和公开的 reasoning summary。
- Anthropic：根据 Claude 模型代际自动选择 legacy manual 或 adaptive thinking。

所有公开的 provider 格式（`reasoning_content`、OpenAI reasoning summary、Anthropic `thinking` block、LangChain v1 `reasoning` block）都会变成同一种内部 `thinking` 流事件，因此 browser、CLI、transcript 和 trace persistence 无需感知 provider。不同 API 公开的内容本身并不完全相同：OpenAI 公开的是 summary 而不是隐藏思维链；如果 Chat Completions 网关完全不返回 reasoning 字段，GBclaw 不会伪造内容。当回复带有 reasoning 项但 summary 为空（reasoning effort 较低时常见，或网关剥掉了 summary），或者只有 reasoning token 计数而没有文本时，GBclaw 会发出带该计数（provider 未报告时为 0）的 `thinking_withheld` 事件，让 trace 能区分"没有推理"和"推理了但未公开"。

推理内容只用于展示：聊天记录（`turns.reasoning` 和 trace events）归档它，browser 和 CLI 显示它。turn 进行中，带 tool call 的步骤会把 `reasoning_content` 回传给 provider；最终回答只发送正文，context 预算也只计算实际发送的内容。下一条请求到来时，orchestrator 会从 checkpoint 中删除之前各轮的推理，同时删除暂停或失败的尝试留下的、与新请求相同且没有得到回答的请求副本。Session summary、项目记忆和工具输出存档都不包含推理。provider 以 content block 返回的推理（Anthropic thinking、OpenAI Responses reasoning item）按原样保留，因为这些 API 要求它与 tool call 配对。

## Web search config

`src/core/settings/web_search.py` 从 `.key` 的 `[web_search]` 读取可选配置：

```ini
[web_search]
enabled = true
api_key = ...
model = ...
base_url = https://api.openai.com/v1
timeout = 60
```

如果没有 `[web_search]`，或其中 `api_key`、`model`、`base_url` 留空，且 literature endpoint 为 `provider = openai` 并使用官方 OpenAI/Azure OpenAI `base_url`，web search 会复用该 endpoint 的对应值。

`is_web_search_available()` 返回 false 时，orchestrator 不会注入 `search_web` 工具。

## `.env` 加载

`src/core/settings/env.py` 会从仓库根目录 `.env` 读取环境变量，并且：

- 使用 `python-dotenv`。
- `override=False`，不会覆盖进程里已有环境变量。
- 同一个 path 只加载一次。

`GBCLAW_SERIAL_EXECUTION`（`src/core/settings/execution.py`）是 Structure/LAMMPS 共用的调度开关，默认值为 `1`：

- `1`：在各自的调度器内，一次只准入一个 Structure 计算或 LAMMPS run。
- `0`：允许无依赖的任务并发执行。Structure 调用共享同一个、上限为 `GBCLAW_WORKERS` 的进程池；LAMMPS run 共享 MPI rank 总预算。

Structure 共享进程池（`src/core/execution/process_pool.py`）默认最多使用 18 个进程。`GBCLAW_WORKERS` 可覆盖这个总上限，但不会超过服务 CPU affinity 范围内可用的物理核心数。Linux 下，每个池进程绑定到一个不同的物理核心，允许运行在该核心可用的超线程上；BLAS/OpenMP 数学库每进程限制为一个计算线程。一个任务可同时使用多个池进程，多个并发任务共享同一个池。这与 LAMMPS 的 MPI 预算独立，修改后需要重启服务。物理核心绑定依赖 Linux 的 CPU affinity 和 sysfs topology 支持。

另有两项设置决定项目存放位置和加载哪些领域扩展：

- `GBCLAW_PROJECTS_ROOT`：存放项目工作区的目录（默认 `<repo>/projects`），例如用于在隔离副本上运行。
- `GBCLAW_EXTENSIONS`：逗号分隔的可信扩展模块 `module[:attribute]` 路径，在内置领域之后加载；默认 attribute 为 `MEMORY_EXTENSION`。未设置 `GBCLAW_EXTENSIONS` 时仍会读取旧名称 `GBCLAW_MEMORY_EXTENSIONS`。注册表在每个进程首次使用时构建一次，修改后需要重启。详见[扩展开发指南](extension-guide.md)。

## LAMMPS config

`src/domains/lammps/common/settings.py` 定义 LAMMPS 执行参数。

环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GBCLAW_LAMMPS_CONDA_ENV` | `GBclaw-lammps` | 执行 LAMMPS Python binding 的 conda env |
| `GBCLAW_POTENTIALS_DIR` | `<repo>/data/potentials` | 存放 `potentials.json` 及其注册势函数文件的目录；相对路径从仓库根目录解析 |
| `GBCLAW_LAMMPS_AGENT_MPI_RANKS` | `18` | 单次 agent run 默认 MPI ranks |
| `GBCLAW_LAMMPS_TOTAL_MPI_RANKS` | `18` | minimization、GB energy、shear、NEB 共用的 MPI rank 总预算 |
| `GBCLAW_LAMMPS_NEB_REPLICAS` | `16` | LAMMPS NEB 默认 partition/replica 数量 |
| `GBCLAW_LAMMPS_NEB_RANKS_PER_REPLICA` | `1` | 每个 NEB replica 的默认 MPI ranks |
| `GBCLAW_GB_MINIMIZE_TIMEOUT_SEC` | `3600` | 单次 gb_minimize wall-clock timeout |
| `GBCLAW_STRUCTURE_MINIMIZE_TIMEOUT_SEC` | `3600` | 单次 structure_minimize wall-clock timeout |
| `GBCLAW_GB_ENERGY_TIMEOUT_SEC` | `14400` | 单次 gb_energy（bicrystal + 4 个 reference）wall-clock timeout |
| `GBCLAW_GB_SHEAR_TIMEOUT_SEC` | `7200` | 单次 gb_shear wall-clock timeout |
| `GBCLAW_GB_NEB_TIMEOUT_SEC` | `14400` | 单次 partitioned gb_neb wall-clock timeout |

托管任务使用 `--map-by core --bind-to core`。普通任务默认使用 18 个 MPI rank，总预算也为 18 个 rank；这些是固定默认值，不会根据宿主 CPU 数量自动检测。请按应用实际可用的物理核心数配置两项预算。NEB 默认使用 16 个 replica，每个 1 rank。如果调低总预算，也要调整 NEB 拓扑，确保全部 replica 能同时装入预算。

派生值：

- 串行模式：`LAMMPS_MAX_PARALLEL_AGENTS = 1`
- 并行模式：`LAMMPS_MAX_PARALLEL_AGENTS = TOTAL_MPI_RANKS // AGENT_MPI_RANKS`
- `GB_MINIMIZE_SCRIPT = <repo>/src/domains/lammps/minimize/engine.py`
- `STRUCTURE_MINIMIZE_SCRIPT = <repo>/src/domains/lammps/structure_minimize/engine.py`
- `GB_ENERGY_SCRIPT = <repo>/src/domains/lammps/energy/engine.py`
- `GB_SHEAR_SCRIPT = <repo>/src/domains/lammps/shear/engine.py`
- `GB_NEB_SCRIPT = <repo>/src/domains/lammps/neb/engine.py`
- `POTENTIALS_METADATA = <GBCLAW_POTENTIALS_DIR>/potentials.json`

这些 engine 脚本按文件路径在 `GBCLAW_LAMMPS_CONDA_ENV` 中运行。`src/domains/lammps/common/resources.py` 按每个 run 实际使用的 MPI ranks 预留资源。并行模式下，只有当所需 ranks 能装入 `GBCLAW_LAMMPS_TOTAL_MPI_RANKS` 剩余预算时才会启动；串行模式下则等待其他 LAMMPS run 全部结束。例如总预算为 100、默认单次使用 20 ranks 时，最多可同时运行 5 个默认任务。显式 `np` 和 NEB topology 都按实际大小计费，单个 run 也不得超过总预算。

`potentials.json` 内的相对 `file` 只相对于该 metadata 文件所在目录解析。显式绝对 potential 路径保持不变；不属于 catalog 的显式相对路径则从仓库根目录解析。如果 `name.eam` 同时也是 catalog alias，可以写成 `./name.eam` 来明确选择仓库相对文件。

## LAMMPS artifact names

`src/domains/lammps/common/artifacts.py` 集中定义 LAMMPS 输出文件名：

- `gb_minimize.cfg`
- `gb_minimized.cfg`
- `gb_minimized.lmp`
- `top_boundary.ids`
- `bot_boundary.ids`
- `gb_minimize.lammpstrj`
- `gb_energy_summary.csv`
- `gb_energy.json`（完整 bicrystal/reference 状态、结果和收敛元数据）
- `shear_curve.csv`
- `shear_run.json`（原子化更新的状态、进度、完整逐点数据和 CFG frame 索引）
- `gb_sheared.cfg`
- `gb_sheared.lmp`
- `structures/`（由 `output.dump_every` 选定 loading step 的 CFG）
- `mep_profile.csv`（给人查看的 NEB minimum-energy path）
- `minimize_run.json`（所有 minimization stage、最终指标和收敛警告）
- `mep.json`（给 agent 读取的原始 NEB reaction-coordinate/energy profile）

这些常量同时被 launcher、tools 和 artifact indexing 使用，避免字符串分散。

## GB LAMMPS 尺寸策略

`src/domains/lammps/common/defaults.py` 是 LAMMPS worker 尺寸策略和准静态剪切默认值的共享来源。box size policy 为：

- `TARGET_X_NM = 20.0`
- `TARGET_Y_PER_GRAIN_NM = 10.0`
- `TARGET_Z_NM = 1.0`
- `DEFAULT_NZ_PERIODS = "1-3"`
- `MIN_Y_PER_GRAIN_NM = 10.0`
- `MIN_Z_NM = 1.0`
- `X_MISMATCH_MAX_PERCENT = 2.0`
- `X_MATCH_CANDIDATE_LIMIT = 6`
- `BOX_DIM_FACTOR = 3.0`
- `BOX_VOLUME_FACTOR = 4.0`

`estimate_gb_box_size()` 和 config writer 都把这些值用于约束 agent 自己选择的几何。省略重复数时自动应用策略并生成整数建议；显式正数保持原值，包括非整数。只有用户明确确认 top/bottom 两组尺寸时，agent 才能传 `user_confirmed_size_override=true`，把超出策略的项目转成可追溯 construction warning；agent 不得用它绕过自己生成尺寸的限制。`target_x_nm` 只用于候选排序，`x_pbc` 只改变 x 留白和连续连接方式，不改变重复匹配。

该模块也定义 NEB 的代码默认 topology：`NEB_REPLICAS = 16`、 `NEB_RANKS_PER_REPLICA = 1`。上面的 `.env` 变量会覆盖 writer tool 默认值；每个 canonical NEB config 都会记录最终 topology，并校验其 16-rank（或覆盖后的）乘积不超过总 rank budget。

该模块中的扁平 `SHEAR_*` 常量是准静态剪切默认值的唯一来源。加载模式与方向、 coarse/fine schedule、反向间隔、弹簧刚度、逐点最小化参数和输出频率都在这里统一定义。 writer tool 签名、运行时校验配置和所有动态 agent prompt 都直接导入这些常量，因此修改一处即可同步所有可执行调用方；静态文档不再复制具体数值。位移加载会将两个 spring 字段保存为 `null`；弹簧加载则会把配置的 per-atom 值乘以对应 boundary group 的原子数。

## Prompt files

每份提示词都放在使用它的 agent 或领域旁边：

| 文件 | 用途 |
| --- | --- |
| `src/agents/orchestrator/prompt.py` | 最高层 GBclaw 身份/全局规则、计划与记忆约定，以及插入各已注册 worker 路由小节的 `build_orchestrator_prompt()` |
| `src/agents/orchestrator/web_search_prompt.py` | 可选 web search 使用边界 |
| `src/agents/planner/prompt.py` | planning worker 的职责约束与动态 domain-knowledge prompt 构造 |
| `src/agents/planner/domain_knowledge.md` | 维护者可编辑的常见分析方法库；每次 planning 调用都会重新加载 |
| `src/agents/prompts/__init__.py` | 追加到 orchestrator 和 worker 提示词末尾的统一结果交接规则 |
| `src/domains/structure/prompt.py` | Structure worker 工具使用规则、Sigma/plane/orientation 约束 |
| `src/domains/lammps/prompt.py` | LAMMPS worker frame、sizing、config/run 流程 |
| `src/domains/analysis/prompt.py` | Analysis worker 应力历史分析与结构渲染规则 |
| `src/domains/coding/prompt.py` | Coding worker 的沙盒、输入与发布规则 |

每个领域的 `prompt.py` 还定义 `ORCHESTRATOR_ROUTING`，即该 worker 在 orchestrator capability routing 中的小节，由 `WorkerDefinition.routing_guidance` 引用。`domains/lammps/prompt.py` 另外定义 `PLANNER_GUIDANCE`，即 planning agent 提示词中关于 shear 默认参数的规则，由 `WorkerDefinition.planning_guidance` 引用。

`src/project_memory/prompts/extraction.py` 定义 turn 后受约束的 fact 提取协议。Project summary 的提示词与其策略、生成、校验、存储和视图一起位于 `src/project_memory/summary/`，其中 `prompt.py` 定义来源分区和压缩优先级。领域 extraction hint 和 summary guidance 由 registry manifest 提供。

## LangSmith

`src/core/settings/langsmith.py` 负责读取 LangSmith 配置并设置环境变量。`create_agent_graph()` 默认会调用 `configure_langsmith()`，除非 `enable_tracing=False`。
