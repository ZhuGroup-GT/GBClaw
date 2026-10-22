# 扩展开发指南

[English](../extension-guide.md) · [架构与设计](architecture.md) · [记忆设计](project-memory.md) · [源码地图](src-code-map.md)

新增专业 agent、科研工具、事实类型、orchestrator 使用的工具、HTTP 接口或聊天模型 provider 时，可以按本指南操作。领域模块提供声明和业务实现；宿主统一提供存储、查询、发布检查和 worker 生命周期。下文以配位数分析为例，展示如何在不改动这些公共服务的情况下新增能力。

## 设计逻辑：为什么用声明接入

每项新科研能力都不应重新发明结果保存、来源标识和失败恢复机制。否则，新增 agent 就要同时修改存储、提示词、查询、计划跟踪和恢复代码，各处甚至可能对“完成”的含义产生分歧。

`DomainExtension` 把公共要求集中声明：有哪些工作流、哪些工具发布结果、它们可以写哪些事实、各 worker 需要什么记忆访问能力，以及领域增加了哪些宿主 hook。宿主在启动时校验并冻结这些定义。领域模块仍负责科研算法及其验证；成功注册并不能证明方法在物理上适用。

这对应论文中的模块化设计：专业 agent 交换精简发现和稳定证据引用，公共基础设施保留完整研究脉络。某项临时代码分析成为常用方法后，应先确认其物理假设和数值行为，再将它固化为注册工具。复用经过检查的实现，可以减少反复生成代码带来的算法差异。

## 先选择最小的扩展范围

| 要增加的能力 | 需要实现的部分 |
| --- | --- |
| 一次性的计算或分析 | 使用现有 coding worker，明确可用环境和输入；不必新建 agent。 |
| 可反复使用的科研操作 | 实现工具，再通过 `ToolProviderSpec` 加入某个 worker 的业务工具；内建 worker 同样适用。若其结果需要作为证据保存，还要声明工作流和 `ToolMemorySpec`，并通过 `ToolRunSession` 发布（第 3 步）。 |
| 新的持久科研记录类型 | 声明事实类型、规范键、授权写入方和证据要求；由工具产生时补充 fact builder。 |
| 需要独立提示词或工具集的专业 agent | 添加 `WorkerDefinition`、记忆策略、delegate factory 和提示词模块，由宿主组装路由及公共生命周期。 |
| 领域专用查询接口 | 添加 query provider，并在实现中约束项目范围及所声明的访问权限。 |
| orchestrator 自身使用的工具、HTTP 接口、启动/停止工作或聊天模型 provider | 第 4 步中的宿主 hook 字段。 |

完整新增一个专业 agent 时可依次完成第 1–3 步；扩展已有领域时，只保留需要的声明。第 4 步列出可选策略和宿主 hook，第 5 步说明如何在不调用语言模型的情况下检查扩展，第 6 步说明恢复。记忆基础设施集中在 `src/project_memory`，注册表在 `src/extensions`；普通扩展不需要修改存储、主 agent 装配、计划门禁、查询或发布恢复代码。

事实带有派生的 `source_type`：`tool_result`、`conversation`、`agent_report` 或 `workflow_state`。科研工具发布的结果与对话提取的偏好、解释分别保留。`completed_run` 证据表示对话解释有已完成运行支持，不会把该解释变成工具结果；worker 报告也保留自身来源及支持性引用。查询、worker handoff 和摘要均保留这些区别，见[事实来源](project-memory.md#事实来源)。

注册表在首次使用时构建，此后在进程生命周期内保持固定，确保 schema、路由和发布策略使用同一套定义。扩展必须在任何代码读取注册表之前配置（存储模型在导入时即读取），声明变化后需要重启进程。不提供旧模块路径转发或历史发布协议适配。

## 内建 worker

为 worker 增加工具（`ToolProviderSpec`）或向其 delegate factory 传参（`OrchestratorTools.worker_options`）时，用的是 agent key。Structure worker 的 agent key 与 delegate 工具名不同。

| Agent key | Delegate 工具 | Endpoint role | `execution_kind` | Plan capability | Tool provider | 声明位置 |
| --- | --- | --- | --- | --- | --- | --- |
| `plan` | `delegate_plan` | `planning` | `planning` | — | — | `extensions/core.py` |
| `structure` | `delegate_csl` | `structure` | `scientific` | `csl` | `structure_tools` | `domains/structure/extension.py` |
| `lammps` | `delegate_lammps` | `lammps` | `scientific` | `lammps` | `lammps_tools` | `domains/lammps/extension.py` |
| `analysis` | `delegate_analysis` | `analysis` | `scientific` | `analysis` | `analysis_tools` | `domains/analysis/extension.py` |
| `coding` | `delegate_coding` | `coding` | `scientific` | `coding` | —（Deep Agents 沙箱工具与 run session 工具） | `domains/coding/extension.py` |

这些 plan capability 与宿主的 `orchestrator`、`project_memory`、`user_input`、`external_search` 一起，构成 planning agent 可以分配的步骤能力。

## 1. 声明领域

声明模块只导入 spec，不导入模型、工具、存储或 LLM。hook 使用 `"module:attribute"` 导入路径字符串。加载器收集全部声明、校验引用和重复身份、冻结注册表，然后生成 workflow/fact schema。内建声明包括 [`extensions/core.py`](../../src/extensions/core.py) 中的宿主词表（计划与记忆工具、`delegate_plan` worker），以及每个领域的 `extension.py`；[`domains/analysis/extension.py`](../../src/domains/analysis/extension.py) 是较简洁的参考。

示例包 `my_domain/` 按内建领域的布局组织：`extension.py`（声明）、`delegate.py`（delegate factory）、`agent.py`（worker）、`prompt.py`（提示词）、`catalog.py`（tool provider）和 `tools.py`（业务工具）。

```python
# my_domain/extension.py
from extensions.specs import (
    AgentMemorySpec, DomainExtension, ToolMemorySpec, ToolProviderSpec,
    WorkerDefinition, WorkflowMemorySpec,
)

MEMORY_EXTENSION = DomainExtension(
    key="coordination",
    workflows=(WorkflowMemorySpec(
        key="coordination", run_prefix="coord2",
        artifact_directory="artifacts/coordination",
        summary_guidance="保留来源结构、截断半径和配位数分布。",
    ),),
    tools=(ToolMemorySpec(
        key="measure_coordination", workflow="coordination",
        required_artifact_roles=("coordination_data",),
    ),),
    workers=(WorkerDefinition(
        agent="coordination", delegate_tool="delegate_coordination",
        endpoint_role="coordination",
        factory="my_domain.delegate:make_delegate",
        execution_kind="scientific", plan_capability="coordination",
        display_name="Coordination agent",
        memory=AgentMemorySpec("coordination", recall_workflows=("coordination",)),
        routing_guidance="my_domain.prompt:ORCHESTRATOR_ROUTING",
        planning_guidance="my_domain.prompt:PLANNER_GUIDANCE",
    ),),
    tool_providers=(ToolProviderSpec(
        "coordination_tools", "coordination", "my_domain.catalog:coordination_tools",
    ),),
    tool_labels=(("measure_coordination", "Coordination measurement"),),
    search_aliases=(("配位数", "coordination"),),
)
```

`WorkerDefinition` 各字段：

| 字段 | 含义 |
| --- | --- |
| `agent` | 唯一的 agent key。tool provider、`worker_options`、记忆策略和 trace 都用它指代该 worker。 |
| `delegate_tool` | orchestrator 调用的工具名；必须唯一，并与 factory 返回的工具同名。 |
| `endpoint_role` | 传给 `ModelRouter.for_role()` 的角色名。`use_tier_routing = true` 时，`.key` 的 `[model_routing]` 把角色映射到 tier，未列出的角色使用该表的 `default` tier；未启用分级路由时，所有角色都使用 `[default]`。 |
| `factory` | `"module:callable"` 形式的 delegate factory（第 2 步）。 |
| `execution_kind` | `scientific`（默认）：用户用 `/plan` 强制规划时、以及计划等待审批时被阻止。`planning`：`/noplan` 下及计划等待审批时被阻止。`read`：不受这些门禁限制。工具只做计算或查询、不发布结果的 worker 用 `read`；运行会发布结果的计算时用 `scientific`。 |
| `plan_capability` | 计划可以分配给该 worker 的小写步骤能力名，会加入 `PlanCapability` 及 planning agent 的能力列表。声明后 delegate 必须暴露 `plan_id` 和 `step_id`。planning agent 的能力说明（`agents/planner/domain_knowledge.md`）只描述内建能力，请在 `planning_guidance` 中说明你的能力用于什么。 |
| `display_name` | 该 delegate 在流式事件、trace 和网页端的显示名称。 |
| `memory` | 该 worker 的 `AgentMemorySpec`（见下表），其 key 必须等于 `agent`。没有记忆策略（此处或 `DomainExtension.agents` 中）的 worker 会被拒绝。 |
| `routing_guidance` | 一个 `str` 的 `"module:ATTRIBUTE"`：该 worker 在 orchestrator 能力路由中的小节（第 2 步）。 |
| `planning_guidance` | 一个 `str` 的 `"module:ATTRIBUTE"`：planning agent 规划该 worker 步骤时遵守的规则（第 2 步）。 |

`AgentMemorySpec` 各字段；除 `key` 外都可省略，`AgentMemorySpec("coordination")` 就是合法的最小写法：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `key` | — | 该 worker 的 agent key。 |
| `recall_workflows` | `()` | 记忆 handoff 优先召回其已完成 run 与事实的工作流。 |
| `all_workflows` | `False` | 改为优先所有工作流。 |
| `artifact_access` | `"read"` | `"read"` 额外提供 `list_run_artifacts` 和 `read_run_artifact`；`"metadata"` 不提供，并且 `read_tool_output` 只能读取本次调用的输出。 |
| `excluded_fact_kinds` | `()` | 不进入记忆 handoff 的事实类型。 |
| `report_artifact_usage` | `False` | 额外提供 `report_artifact_usage` 工具。 |
| `query_providers` | `()` | 该 worker 获得的 `QueryProviderSpec` 工具的 key（第 4 步）。 |

每个 worker 还都会获得 `query_project_memory`、`search_project_memory`、`read_agent_conclusion`、`get_task_state` 和 `read_tool_output`。

### 代码放在哪里、如何启用

扩展就是一个可导入的 Python 包。可以与内建包一起放在 `src/` 下，也可以作为独立的包安装到环境中（`pip install -e`）或放到 `PYTHONPATH` 上；`start_web.sh` 和 `start_cli.sh` 会把 `src` 放在最前，并保留已有的 `PYTHONPATH`。然后在启动 API/CLI 的环境中或在 `.env` 里启用：

```bash
export GBCLAW_EXTENSIONS=my_domain.extension
```

多个模块用逗号分隔。`module:attribute` 可以指定 `MEMORY_EXTENSION` 以外的导出名。未设置 `GBCLAW_EXTENSIONS` 时仍读取旧名 `GBCLAW_MEMORY_EXTENSIONS`。这是宿主配置的可信 Python 扩展，不由模型动态选择。嵌入 GBclaw 的宿主也可以在注册表首次使用前调用 `extensions.configure(extension_paths=("my_domain.extension",), include_builtins=True)`；注册表加载后再调用 `configure()` 会报错。`load_registry()` 也可以构建独立快照；运行中不允许修改全局词表。

## 2. 实现 worker

注册的 factory 接收 `endpoint` 以及关键字参数 `project_store`、`memory_endpoint`，返回同步 LangChain `BaseTool`。工具函数必须暴露 `task` 和宿主注入的 `tool_call_id`；声明了 `plan_capability` 时，还必须暴露 `plan_id`、`step_id`。生命周期包装器会从调用参数中读取 `plan_id` 和 `step_id` 来追踪计划进度，函数体本身不需要使用它们。

```python
# my_domain/delegate.py
from typing import Annotated
from langchain_core.tools import InjectedToolCallId, tool
from agents.workers.lifecycle import current_worker_invocation
from my_domain.agent import invoke_coordination_agent


def make_delegate(endpoint, *, project_store=None, memory_endpoint=None):
    @tool
    def delegate_coordination(
        task: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        plan_id: str | None = None,
        step_id: str | None = None,
    ):
        """分析明确指定结构的配位数。"""
        invocation = current_worker_invocation()
        return invoke_coordination_agent(
            endpoint, task,
            project_id=invocation.project_id,
            chat_thread_id=invocation.chat_thread_id,
            on_event=invocation.emit,
        )
    return delegate_coordination
```

`invoke_coordination_agent` 是领域自己的实现。参照 [analysis worker](../../src/domains/analysis/agent.py) 声明一个 [`WorkerSpec`](../../src/agents/workers/spec.py)，业务工具由已声明的 provider 提供：

```python
# my_domain/agent.py
from agents.workers.spec import WorkerSpec
from my_domain.prompt import COORDINATION_PROMPT

COORDINATION_WORKER = WorkerSpec(
    agent="coordination", label="Coordination agent",
    status_message="Coordination analysis running…", prompt=COORDINATION_PROMPT,
)
invoke_coordination_agent = COORDINATION_WORKER.invoke

# my_domain/catalog.py
from my_domain.tools import measure_coordination

def coordination_tools() -> list:
    return [measure_coordination]
```

`WorkerSpec.invoke` 通过 `agents.graph.create_agent_graph` 构建图，从该 worker 已注册的 tool provider 取得业务工具，并返回保留完整内部 trace 的 `WorkerResult`。在已注册的调用上下文中，公共 graph factory 自动加入记忆工具和召回 prompt。绕过公共 graph factory 的专用构建器（例如 Coding 的 DeepAgent）显式调用 `worker_memory_inputs(agent, business_tools)`。

`ToolProviderSpec(key, worker, factory)` 指向一个无参数的 `"module:callable"`，返回该 worker 的 `BaseTool` 序列，参见 [`domains/analysis/catalog.py`](../../src/domains/analysis/catalog.py)。key 必须是唯一的小写标识符，worker 是上表中的 agent key 或你自己的 worker。同一 worker 的多个 provider 按注册表顺序依次贡献工具，因此第三方扩展可以为内建 worker 增加工具；同一 worker 的 provider 之间出现重名工具时，构建该 worker 的图会报错。`WorkerSpec.tools` 或调用 `invoke` 时显式传入的 `tools=` 会取代 provider。为内建 worker 增加的工具只有该 worker 通过工具名和说明知道；orchestrator 的路由小节和 planning agent 的提示词不会改变，所以要在工具 docstring 中把用途写完整。

宿主 [`build_registered_worker_tools`](../../src/agents/workers/lifecycle.py) 自动包装 factory 返回的工具，统一完成：先创建任务游标，再召回记忆、设置调用上下文、追踪计划、验收交付物、保存结论，最后清理上下文。在 orchestrator 以外单独装配时，调用一次 `create_worker_delegate(tool, agent=..., project_store=..., memory_endpoint=...)`。

内部返回 **`WorkerResult`、`RunToolResult` 或 `WorkerOutcome` 对象**，不要提前转成 JSON。宿主必须先拿到完整结果与 trace 完成验收，再生成面向模型的输出。`WorkerOutcome` 明确表达 `completed`、`reused`、`partial`、`failed`、`awaiting_user`、`cancelled`、`interrupted`，以及证据引用和下一步动作。未知终态会被拒绝。

### 提示词模块

`prompt.py` 存放 worker 的 system prompt，以及宿主插入其他提示词的两段文字。内建领域把路由小节命名为 `ORCHESTRATOR_ROUTING`，LAMMPS 另外定义了 `PLANNER_GUIDANCE`；提示词文字约在 100 列处换行：

```python
# my_domain/prompt.py
COORDINATION_PROMPT = """You are the GBclaw Coordination specialist. Measure coordination numbers of the
structure named in the task with your tools; do not talk to the user.

- Use the exact run and artifact IDs given in the task.
- Report the cutoff, the coordination distribution and the run ID of every measurement.
"""

# orchestrator 按注册表顺序把这些小节拼接到 "## Capability routing" 之下。
ORCHESTRATOR_ROUTING = """### Coordination

Use ``delegate_coordination`` for coordination-number distributions of a completed structure. Pass the
source run ID and the structure artifact; do not ask it to build or relax structures."""

# planning agent 的提示词在其规划规则中加入这段文字，每个 worker 一段。
PLANNER_GUIDANCE = """- Coordination steps measure an existing structure; plan the structure-producing step first."""
```

`build_orchestrator_prompt()` 按注册表顺序拼接各 worker 的路由小节，小节之间空一行；与内建领域一样，每个小节以 `### 名称` 标题开头，并用 ``Use ``delegate_x`` for …`` 句式说明用途。planning agent 的提示词按原文加入各 worker 的 planning guidance，因此应写成一个或多个 `- ` 列表项。两者都未声明的 worker 不增加任何内容。执行策略来自宿主声明，不来自模型参数。领域仍负责自己的算法、prompt、业务工具清单和来源授权。

## 3. 编写工具并发布结果

只做计算或查询的工具（例如几何估算）可以是普通的 LangChain `@tool`：它不保存任何内容，宿主把它视为查询。结果需要作为证据保存、供后续步骤、用户或恢复流程引用的工具，则需要工作流和 `ToolMemorySpec`。声明 `ToolMemorySpec` 后，该工具被视为科学工具并纳入计划追踪：worker 报告成功时，宿主会检查它调用过的每个已声明工具都有已完成的 run 及所需产物角色。只调用普通工具的 worker 没有需要验收的内容，除非 delegate 调用要求了交付物或产物角色，否则其报告按只读解释处理。

在 worker 调用中，工具通过运行配置找到所属项目：`core.util.graph_config.runtime_project_id()` 返回当前项目 ID，`ProjectStore()` 打开默认项目根目录，内建 analysis 工具就是这样做的。通过绑定工具身份的 [`ToolRunSession`](../../src/project_memory/publication/session.py) 发布。下面假设 `calculate_coordination` 是领域计算函数，返回可 JSON 序列化的数据：

```python
# my_domain/tools.py
import json
from langchain_core.tools import tool
from core.util.graph_config import runtime_project_id
from project_workspace.artifacts.models import ProvenanceClass
from project_workspace.store import ProjectStore


def run_measurement(store, project_id, parameters):
    with store.runs(project_id).tool_run(
        "measure_coordination", parameters=parameters,
    ) as session:
        result = calculate_coordination(parameters)
        output = session.artifact_directory / "coordination.json"
        output.write_text(json.dumps(result), encoding="utf-8")
        session.register_artifact(
            "coordination_data", output.name, ProvenanceClass.PRIMARY,
        )
        run = session.complete(
            result=result,
            conclusion={"summary": "Coordination measured", **result},
            mode="facts",
        )
        return run, session.result_ref


@tool
def measure_coordination(source_run_id: str, cutoff_angstrom: float) -> str:
    """测量已完成 run 中最小化结构的配位数。"""
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    run, reference = run_measurement(
        ProjectStore(), project_id,
        {"source_run_id": source_run_id, "cutoff_angstrom": cutoff_angstrom},
    )
    return json.dumps(reference)
```

派生工作通过 `tool_run` 的来源参数传入准确的 source run、fingerprint 和 `RunSourceRecord`。`session.result_ref` 包含 run、完整结果位置、产物、事实引用及发布契约；它是检索入口，不能替代科学数据本身。

`mode="facts"` 发布声明的事实；`mode="artifacts_only"` 在注册策略允许省略事实时，明确表示只完成产物记录。异常、取消或未调用 complete 就退出，会结束当前 running 记录并保留已登记的诊断产物，不自动重跑计算。

配置与交互流程仍可分阶段执行：在 `create_configured_run` 或 `create_running_run` 时绑定 `memory_tool`，通过 `complete_run` 完成发布。现有科学入口已在计算前绑定工具；`render_atomic_structure` 是实际使用 session 的例子。

服务在**创建运行前**检查 hook 能否导入、是否为同步 callable、签名是否匹配。

## 4. 在领域声明中增加策略和宿主 hook

| 需要的能力 | 声明 / hook |
| --- | --- |
| 多条或专用类型事实 | `FactMemorySpec`、`ToolMemorySpec.fact_kinds` 和 `fact_builder="module:function"`；builder 签名为 `(run, result) -> list[FactRecord]`。 |
| 必需产物 | `ToolMemorySpec.required_artifact_roles`。 |
| 专用证据校验 | tool/workflow 的 `publication_validator`，签名 `(run, result, facts)`，不合格时抛异常。 |
| 面向模型的紧凑输出 | `model_result_keys` 或 `model_result_builder(result)`。工具也可以在 JSON 回复中自带有界的 `model_view` 对象；使用 run session 的工具设置 `RunToolResult.model_view`，应力–应变工具即如此（`domains/analysis/stress_strain/model_view.py`）。模型完整看到该视图（约 15,000 字符以内），代替通用投影；完整回复存档，可用 `read_tool_output` 读取。完整证据仍保留。 |
| 领域查询工具 | `QueryProviderSpec(key, factory)` 与 `AgentMemorySpec.query_providers`；`factory(store)` 返回一个与 key 同名的 `BaseTool`。 |
| 用户明确选择的偏好/约束 | fact 的 `writer="conversation_extraction"`、`extraction_policy="user_explicit"`、key 规则和 `extraction_hint`。 |
| 提取事实的 canonical key | fact 的 `fixed_lookup_key`、`key_pattern` 或 `canonical_key_policy(kind=..., requested_key=..., source_run_id=...)`。 |
| 自动召回优先级 | fact 的 `context_priority`（越大越优先），agent 的 `recall_workflows` 或 `all_workflows`。 |
| 检索词汇 | domain/fact/workflow 的 `search_aliases`。 |
| 摘要保留要点 | fact/workflow 的 `summary_guidance`，随记录加入摘要输入。 |
| 取消后的临时目录清理 | workflow 的 `ephemeral_directories`，声明 run 下的直接子目录名；已登记的永久产物受到保护。 |
| 非科学发布工具的执行策略 | `ToolCapabilitySpec`，例如配置步骤追踪或交互行为。可选的 `model_view` 是一个 `"module:callable"`，把较大回复的文本转换为模型看到的有界 dict，代替通用预览；回复本身、存档和已保存的 artifact 均不变。声明的视图只用于超过 4,000 字符且自身不含 `model_view` 的回复。为发布工具声明 capability 会取代其默认策略，需同时设置 `operation="scientific", plan_tracking=True`。 |
| 工具在流式事件、trace 和网页端的展示名称 | domain 的 `tool_labels`，即 `(工具名, 展示名)` 对；同一工具的展示名冲突会被拒绝。`ToolCapabilitySpec.display_name` 优先，delegate 工具显示其 worker 的 `display_name`。 |
| HTTP 接口 | domain 的 `routers`，FastAPI `APIRouter` 对象的 `"module:attribute"` 路径，由 API 按注册表顺序挂载。 |
| 进程启动/停止时的工作 | domain 的 `startup` 与 `shutdown`，无参数、同步的 `"module:callable"` hook。 |
| orchestrator 自身使用的工具 | domain 的 `orchestrator_tools`，即 `OrchestratorToolsSpec(key, factory, placement)`。`factory(*, project_store)` 在每次构建 orchestrator 时调用一次，返回 `agents/orchestrator/contributions.py` 中的 `OrchestratorTools(tools, worker_options)`；`placement="leading"` 把工具放在宿主的记忆、计划和 web search 工具之前，`"trailing"`（默认）放在其后。`worker_options` 把 agent key 映射到传给其 delegate factory 的额外关键字参数，使工具与 delegate 共用同一对象（coding 以此共享环境注册表、资源限制和资源池）。 |
| 新的聊天模型 provider | domain 的 `llm_providers`，无参数的 `"module:callable"` factory（adapter 类本身即可），返回具备 `name`、`aliases`、`apply_thinking`、`build` 的 `ProviderAdapter`；其 name 与 aliases 成为 `.key` 中可用的 `provider` 值。可选的 `token_counter(model_name)` 为它按名称识别的模型家族返回精确的文本计数函数；没有时，上下文预算对该家族按一字符一个 token 计数。可选的 `reasons_when_disabled(endpoint)` 在 `thinking=disabled` 时模型仍以最低档推理的情况下返回 True；宿主辅助调用随之在输出上限上增加固定 4,096 tokens 余量，但不超过该 endpoint 的上限。 |

query provider 自动取得 query/read 能力分类，按需查询的完整输出不会被通用投影压缩。provider 自己仍须校验项目上下文并遵守所声明的读取范围。`artifact_access="metadata"` 不装配通用文件读取工具，但并不是任意可信扩展代码的沙箱。`recall_workflows` 是召回偏好，不是访问控制列表。

默认发布 `scientific_conclusion`，主 key 为 `run:{run_id}:main_conclusion`。只发布专用类型的工具设置 `conclusion_lookup_key=None`。通常事实 key 必须归属自己的 run；只有领域明确需要跨运行共享身份时才设置 `shared_fact_keys=True`。公共 writer 继续检查归属、证据、CAS 版本及批次原子性。注册确定性事实类别不意味着允许聊天提取器自行断言它。计划、任务游标与 agent 报告仍由独立宿主 writer 管理。

Builder 不设置 `source_type`：宿主根据 `evidence_kind`、来源引用与 fact kind 推导该字段。只读取工具结果时，使用 `query_project_memory(entity="facts", source_type="tool_result")` 或 `search_project_memory(query="...", source_type="tool_result")`。来源过滤的 search 默认只查 facts，与显式 run/artifact entity 过滤混用会报错。领域 `summary_guidance` 必须保留输入来源，不能把提取的解释或报告提升为工具结果。

API 与 CLI 都通过 [`interfaces/lifecycle.py`](../../src/interfaces/lifecycle.py) 执行宿主 hook。启动时先注册每个 `llm_providers` adapter（早于任何模型构建；名称或别名已被占用时报错），再按注册表顺序在每个进程内运行一次 `startup` hook，任一 hook 抛出异常都会使宿主启动失败。停止时按相反顺序运行 `shutdown` hook，每个 hook 相互隔离，失败只记录日志，不会跳过其余 hook。[structure 声明](../../src/domains/structure/extension.py) 是实际例子：它挂载 `domains.structure.routes:router`，并预热和关闭共享进程池。

## 5. 不调用语言模型检查扩展

注册、提示词拼装和 graph 构建都不需要调用模型，也不需要 `.key`。把下面的脚本保存为例如 `check_extension.py`，在仓库根目录用 `PYTHONPATH=src:<包含 my_domain 的目录> python check_extension.py` 运行。脚本与 API、CLI 一样读取 `.env`，所以 `GBCLAW_EXTENSIONS` 可以 export，也可以写在 `.env` 中：

```python
from core.settings.env import load_dotenv
load_dotenv()  # 与 API、CLI 一样读取 .env

import extensions
extensions.validate_hooks()  # 每个 hook 都能导入并接受宿主传入的参数

from agents.workers.spec import registered_worker_tools
from agents.streaming.events import tool_label
from agents.orchestrator.prompt import build_orchestrator_prompt
from agents.planner.prompt import build_plan_agent_prompt

print([tool.name for tool in registered_worker_tools("coordination")])  # 该 worker 的业务工具
print([tool.name for tool in registered_worker_tools("analysis")])  # 为内建 worker 增加的工具排在最后
print(tool_label("delegate_coordination"), tool_label("measure_coordination"))
assert "### Coordination" in build_orchestrator_prompt()
assert "``coordination``" in build_plan_agent_prompt()

from core.llm.config import LLMConfig, LLMEndpoint, ModelRouter
from agents.workers.lifecycle import build_registered_worker_tools

endpoint = LLMEndpoint(  # 构造模型不会发出请求
    provider="openai", api_key="unused", base_url="http://127.0.0.1:9", model="placeholder",
    temperature=0.0, max_tokens=None, max_input_tokens=None, timeout=30,
    thinking="disabled", reasoning_effort=None, api_mode="chat_completions",
)
router = ModelRouter(LLMConfig(use_tier_routing=False, model_routing={}, default=endpoint))
print([tool.name for tool in build_registered_worker_tools(router)])  # 包含 delegate_coordination
```

构建 orchestrator 时也会调用 `validate_hooks()`。它在不调用 hook 的前提下解析全部声明：fact builder、model-result builder、publication validator、canonical key policy、capability 的 `model_view`，query provider、tool provider、orchestrator tools 与 worker 的 factory，以及 `startup`、`shutdown`、`llm_providers` hook，都必须能导入为同步 callable，且签名能接受宿主传入的参数；`routers`、`routing_guidance` 和 `planning_guidance` 必须能导入，两段说明文字必须是 `str`。报错会指明扩展和字段，例如 `coordination extension, workers[coordination].routing_guidance: invalid attribute ...`。无法导入的扩展模块会报 `Cannot import extension module ...`；向 `registered_worker_tools()` 传入未注册的 agent key 也会报错。这能提前发现接线错误，不能证明领域实现永远不会发生运行时错误。以 `max_input_tokens=None` 构造的 endpoint 按模型档案的输入上限预算，模型没有档案时使用 `default_max_input_tokens()`（262144，设置 `GBCLAW_CONTEXT_1M` 时为 1,048,576）；`.key` 中未写 `max_input_tokens` 的小节使用该默认值，并以模型档案为上限。

## 6. 恢复

运行绑定 `contract_version` 和策略指纹。完成发布先保存永久 fact/conclusion artifact 及恢复指针；重试读取保存的数据，不再调用 builder、模型或科学计算。相同发布重试不增加版本，旧发布不能覆盖并发更新。

缺少版本、版本变化或策略指纹变化都会明确阻止恢复。不保留历史解码器，也不自动迁移数据。修改 hook 的语义时应提升 `contract_version`：指纹覆盖声明字段和 hook 路径，**不覆盖 hook 的 Python 源码**。修改注册声明不会删除已有科学文件。
