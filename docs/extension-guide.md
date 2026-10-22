# Extension guide

[中文](zh/extension-guide.md) · [Architecture and design](architecture.md) · [Memory design](project-memory.md) · [Source map](src-code-map.md)

Use this guide to add a specialist agent, a scientific tool, a fact type, a tool for the orchestrator, an HTTP route, or a chat-model provider. A domain supplies declarations and implementation; the host provides common storage, retrieval, publication checks, and the worker lifecycle. The running example adds a coordination-number analysis without changing those shared services.

## Why extensions use declarations

New scientific capabilities should not each invent a way to save results, identify sources, or recover interrupted publication. Otherwise, adding an agent would require changes across storage, prompts, queries, plan tracking, and recovery code, and those implementations could disagree about what “completed” means.

`DomainExtension` makes these shared requirements explicit in one declaration: which workflows exist, which tools publish results, which facts they own, which memory access each worker needs, and which host hooks the domain adds. The host validates and freezes that vocabulary at startup. Domain code still owns the scientific algorithm and its validation; registration does not establish that a method is physically appropriate.

This implements the manuscript's modular design: specialists exchange focused findings and stable evidence references, while common infrastructure preserves the wider investigation. When a custom coding task becomes a recurring method, validate its assumptions and numerical behavior before promoting it to a registered scientific tool. Reusing a checked implementation avoids repeatedly generating slightly different algorithms.

## Choose the smallest extension

| What you are adding | What to implement |
| --- | --- |
| A one-off calculation or analysis | Use the existing coding worker with an available environment and explicit inputs; a new agent is unnecessary. |
| A reusable scientific operation | Implement the tool and add it to a worker's business tools with a `ToolProviderSpec`; this also works for a built-in worker. If its result should persist as evidence, also declare its workflow and `ToolMemorySpec` and publish through a `ToolRunSession` (step 3). |
| A new kind of persistent research record | Declare its fact type, canonical key, authorized writer, and evidence requirements; add a fact builder when a tool produces it. |
| A specialist with a distinct prompt or tool set | Add a `WorkerDefinition`, its memory policy, a delegate factory, and a prompt module; the host assembles routing and the shared lifecycle. |
| A domain-specific read interface | Add a query provider that enforces project scope and its declared access policy. |
| A tool the orchestrator itself uses, an HTTP route, startup/shutdown work, or a chat-model provider | The host-hook fields in step 4. |

Follow steps 1–3 for a complete new specialist, or keep only the applicable declarations when extending an existing domain. Step 4 lists optional policies and host hooks, step 5 shows how to check an extension without a language model, and step 6 explains recovery. Memory infrastructure lives in `src/project_memory` and the registry in `src/extensions`; ordinary additions do not require edits to storage, orchestrator assembly, plan gates, search, or publication recovery.

Facts carry a derived `source_type`: `tool_result`, `conversation`, `agent_report`, or `workflow_state`. Keep scientific tool publications separate from conversation-derived preferences and interpretations. `completed_run` evidence supports a conversational interpretation; it does not make that interpretation a tool result. Worker reports retain their own origin and supporting references. Queries, worker handoff and summaries preserve these distinctions; see [fact sources](project-memory.md#fact-sources).

The registry is built on first use and then intentionally fixed for the life of the process, so schemas, routing, and publication policies use the same definitions. Select extensions before anything reads it (storage models read it when they are imported), and restart processes after changing declarations. Legacy import paths and historical publication adapters are not provided.

## Built-in workers

Name a worker by its agent key, for example when `ToolProviderSpec` adds a tool to it or when `OrchestratorTools.worker_options` passes arguments to its delegate factory. The agent key differs from the delegate tool name for the Structure worker.

| Agent key | Delegate tool | Endpoint role | `execution_kind` | Plan capability | Tool providers | Declared in |
| --- | --- | --- | --- | --- | --- | --- |
| `plan` | `delegate_plan` | `planning` | `planning` | — | — | `extensions/core.py` |
| `structure` | `delegate_csl` | `structure` | `scientific` | `csl` | `structure_tools` | `domains/structure/extension.py` |
| `lammps` | `delegate_lammps` | `lammps` | `scientific` | `lammps` | `lammps_tools` | `domains/lammps/extension.py` |
| `analysis` | `delegate_analysis` | `analysis` | `scientific` | `analysis` | `analysis_tools` | `domains/analysis/extension.py` |
| `coding` | `delegate_coding` | `coding` | `scientific` | `coding` | — (Deep Agents sandbox and run-session tools) | `domains/coding/extension.py` |

The plan capabilities, together with the host's `orchestrator`, `project_memory`, `user_input` and `external_search`, are the step capabilities the planning agent may assign.

## 1. Declare a domain

Keep the declaration module free of models, tools, storage and LLM imports. Hooks are `"module:attribute"` import strings; the loader collects all declarations, validates references and duplicates, freezes the registry, then exposes the workflow/fact schemas. Built-in declarations are the host vocabulary in [`extensions/core.py`](../src/extensions/core.py) (plan and memory tools, the `delegate_plan` worker) and one `extension.py` per domain; [`domains/analysis/extension.py`](../src/domains/analysis/extension.py) is a compact reference.

The example package `my_domain/` follows the built-in layout: `extension.py` (declarations), `delegate.py` (delegate factory), `agent.py` (worker), `prompt.py` (prompts), `catalog.py` (tool provider) and `tools.py` (business tools).

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
        summary_guidance="Retain the source structure, cutoff and coordination distribution.",
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

`WorkerDefinition` fields:

| Field | Meaning |
| --- | --- |
| `agent` | Unique agent key. Tool providers, `worker_options`, memory policies and traces refer to the worker by it. |
| `delegate_tool` | Name of the tool the orchestrator calls; it must be unique and equal the name of the tool the factory returns. |
| `endpoint_role` | Role passed to `ModelRouter.for_role()`. With `use_tier_routing = true`, `[model_routing]` in `.key` maps roles to tiers; an unlisted role uses that table's `default` tier. Without tier routing, every role uses `[default]`. |
| `factory` | `"module:callable"` delegate factory (step 2). |
| `execution_kind` | `scientific` (default): blocked while the user forces planning with `/plan` and while a plan awaits approval. `planning`: blocked under `/noplan` and while a plan awaits approval. `read`: never blocked by these gates. Use `read` for a worker whose tools only compute or look up without publishing results, and `scientific` when it runs computations that publish results. |
| `plan_capability` | Lowercase step capability that plans may assign to this worker; it is added to `PlanCapability` and to the planning agent's list of capabilities. A worker with one must expose `plan_id` and `step_id`. The planning agent's capability reference (`agents/planner/domain_knowledge.md`) describes only the built-in capabilities, so say what yours is for in `planning_guidance`. |
| `display_name` | Label of the delegate in streams, traces and the Web UI. |
| `memory` | The worker's `AgentMemorySpec` (below); its key must equal `agent`. A worker without a memory policy (here or in `DomainExtension.agents`) is rejected. |
| `routing_guidance` | `"module:ATTRIBUTE"` of a `str`: this worker's section of the orchestrator's capability routing (step 2). |
| `planning_guidance` | `"module:ATTRIBUTE"` of a `str`: rules the planning agent follows when it plans this worker's steps (step 2). |

`AgentMemorySpec` fields; all but `key` are optional, so `AgentMemorySpec("coordination")` is a valid minimum:

| Field | Default | Meaning |
| --- | --- | --- |
| `key` | — | The worker's agent key. |
| `recall_workflows` | `()` | Workflows whose completed runs and facts the worker's memory handoff prefers. |
| `all_workflows` | `False` | Prefer every workflow instead. |
| `artifact_access` | `"read"` | `"read"` adds `list_run_artifacts` and `read_run_artifact`; `"metadata"` omits them and limits `read_tool_output` to the worker's own invocation. |
| `excluded_fact_kinds` | `()` | Fact kinds kept out of the worker's memory handoff. |
| `report_artifact_usage` | `False` | Adds the `report_artifact_usage` tool. |
| `query_providers` | `()` | Keys of `QueryProviderSpec` tools the worker receives (step 4). |

Every worker also receives `query_project_memory`, `search_project_memory`, `read_agent_conclusion`, `get_task_state` and `read_tool_output`.

### Where the code lives and how to enable it

An extension is an importable Python package. Keep it in `src/` beside the built-in packages, or in a separate package that is installed in the environment (`pip install -e`) or placed on `PYTHONPATH`; `start_web.sh` and `start_cli.sh` put `src` first and keep an existing `PYTHONPATH` after it. Then enable it in the environment that starts the API or CLI, or in `.env`:

```bash
export GBCLAW_EXTENSIONS=my_domain.extension
```

Comma-separated modules are supported. `module:attribute` selects a declaration export other than `MEMORY_EXTENSION`. The older name `GBCLAW_MEMORY_EXTENSIONS` is still read when `GBCLAW_EXTENSIONS` is unset. These are trusted Python extensions configured by the host, not model-selected plugins. A host that embeds GBclaw can instead call `extensions.configure(extension_paths=("my_domain.extension",), include_builtins=True)` before the registry is first used; once it is loaded, `configure()` raises. `load_registry()` can also build isolated snapshots; mutating a live registry is unsupported.

## 2. Implement the worker

The registered factory accepts `endpoint`, keyword arguments `project_store` and `memory_endpoint`, and returns a synchronous LangChain `BaseTool`. Its callable exposes `task` and an injected `tool_call_id`; a worker with `plan_capability` also exposes `plan_id` and `step_id`. The lifecycle wrapper reads `plan_id` and `step_id` from the call to track plan progress, so the function body does not need them.

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
        """Analyze the coordination of an explicitly identified structure."""
        invocation = current_worker_invocation()
        return invoke_coordination_agent(
            endpoint, task,
            project_id=invocation.project_id,
            chat_thread_id=invocation.chat_thread_id,
            on_event=invocation.emit,
        )
    return delegate_coordination
```

`invoke_coordination_agent` is domain code. Following the [analysis worker](../src/domains/analysis/agent.py), declare a [`WorkerSpec`](../src/agents/workers/spec.py) and supply its business tools through the declared provider:

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

`WorkerSpec.invoke` builds the graph with `agents.graph.create_agent_graph`, takes the worker's business tools from its registered tool providers, and returns a `WorkerResult` with the full internal trace. Inside a registered invocation, the graph factory automatically adds memory tools and the recall prompt. Specialized graph builders, such as Coding's DeepAgent, use `worker_memory_inputs(agent, business_tools)` explicitly.

`ToolProviderSpec(key, worker, factory)` names a zero-argument `"module:callable"` that returns a sequence of `BaseTool` objects for the named worker, as in [`domains/analysis/catalog.py`](../src/domains/analysis/catalog.py). The key is a unique lowercase identifier and the worker is an agent key from the table above or your own. Providers for the same worker contribute in registry order, so a third-party extension can add tools to a built-in worker; repeated tool names across one worker's providers are rejected when its graph is built. `WorkerSpec.tools`, or an explicit `tools=` argument to `invoke`, replaces the providers. A tool added to a built-in worker is known only to that worker, through its name and description; the orchestrator's routing section and the planning agent's prompt stay unchanged, so describe the tool fully in its docstring.

The host's [`build_registered_worker_tools`](../src/agents/workers/lifecycle.py) wraps the returned tool automatically. The shared lifecycle creates the task cursor before recall, installs invocation context, tracks plan progress, verifies requested deliverables, persists the conclusion, and resets context on exit. Direct assembly outside the orchestrator uses `create_worker_delegate(tool, agent=..., project_store=..., memory_endpoint=...)` once.

Return **`WorkerResult`, `RunToolResult`, or `WorkerOutcome` objects**, not pre-serialized JSON. The host needs the full result and trace before producing the model-facing response. `WorkerOutcome` explicitly represents `completed`, `reused`, `partial`, `failed`, `awaiting_user`, `cancelled`, or `interrupted`, with evidence references and a next action. Unknown final statuses are rejected.

### The prompt module

`prompt.py` holds the worker's system prompt and the two texts the host inserts into other prompts. Built-in domains keep the routing section as `ORCHESTRATOR_ROUTING`, and LAMMPS also defines `PLANNER_GUIDANCE`; their prompt text is wrapped at about 100 columns:

```python
# my_domain/prompt.py
COORDINATION_PROMPT = """You are the GBclaw Coordination specialist. Measure coordination numbers of the
structure named in the task with your tools; do not talk to the user.

- Use the exact run and artifact IDs given in the task.
- Report the cutoff, the coordination distribution and the run ID of every measurement.
"""

# The orchestrator joins these sections in registry order under "## Capability routing".
ORCHESTRATOR_ROUTING = """### Coordination

Use ``delegate_coordination`` for coordination-number distributions of a completed structure. Pass the
source run ID and the structure artifact; do not ask it to build or relax structures."""

# The planning agent's prompt includes this, one block per worker, among its planning rules.
PLANNER_GUIDANCE = """- Coordination steps measure an existing structure; plan the structure-producing step first."""
```

`build_orchestrator_prompt()` joins every worker's routing section in registry order, separated by blank lines; start each with a `### Name` heading and a sentence of the form ``Use ``delegate_x`` for …``, as in the built-in domains. The planning agent's prompt adds each planning guidance as written, so write it as one or more `- ` list items. A worker without either adds nothing. Plan/scientific policies come from the host manifest, not the model's tool arguments. Business algorithms, prompts, tool lists and source authorization remain the domain's responsibility.

## 3. Write tools and publish results

A tool that only computes or looks something up, such as a geometry estimate, can be a plain LangChain `@tool`; it persists nothing, and the host treats it as a query. A tool whose result should persist as evidence, so that later steps, the user, or recovery can cite it, needs a workflow and a `ToolMemorySpec`. Declaring a `ToolMemorySpec` makes the tool scientific and plan-tracked: when a worker reports success, the host checks that every declared tool it called has a completed run with the required artifact roles. A worker that calls only plain tools has nothing to verify, and its report stands as a read-only explanation unless the delegate call requested deliverables or artifact roles.

Inside a worker invocation, a tool finds its project through the run configuration: `core.util.graph_config.runtime_project_id()` returns the active project ID, and `ProjectStore()` opens the default projects root, as the built-in analysis tools do. Publish through the bound [`ToolRunSession`](../src/project_memory/publication/session.py). This example assumes `calculate_coordination` is the domain's calculation and returns JSON-compatible data:

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
    """Measure coordination numbers of the minimized structure of a completed run."""
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    run, reference = run_measurement(
        ProjectStore(), project_id,
        {"source_run_id": source_run_id, "cutoff_angstrom": cutoff_angstrom},
    )
    return json.dumps(reference)
```

Pass exact source run IDs, fingerprints and `RunSourceRecord` entries through `tool_run` source arguments for derived work. `session.result_ref` contains the run, full-result location, artifacts, fact references and publication contract. It is a retrieval reference, not a substitute for the scientific data.

`mode="facts"` publishes the declared facts; `mode="artifacts_only"` explicitly completes an artifact-only operation when its registered policy permits omitting facts. An exception, cancellation, or leaving the context without completing terminates the running record and retains registered diagnostics. No calculation is retried automatically.

Configuration/interactive workflows can keep separate phases: bind `memory_tool` when calling `create_configured_run` or `create_running_run`, and finish through `complete_run`. All existing scientific entry points bind their tool before computation. `render_atomic_structure` is a concrete session-based implementation.

The service checks hook importability, synchronous callability and signature **before creating the run**.

## 4. Add domain policies and host hooks

| Requirement | Declaration / hook |
| --- | --- |
| Multiple or typed facts | `FactMemorySpec` plus `ToolMemorySpec.fact_kinds` and `fact_builder="module:function"`; builder signature `(run, result) -> list[FactRecord]`. |
| Artifact requirements | `ToolMemorySpec.required_artifact_roles`. |
| Additional evidence validation | Tool/workflow `publication_validator`; signature `(run, result, facts)`, raising on invalid evidence. |
| Compact model result | `model_result_keys` or `model_result_builder(result)`. A tool can instead put its own bounded `model_view` object in its JSON reply; a run-session tool sets `RunToolResult.model_view`, as the stress–strain tools do (`domains/analysis/stress_strain/model_view.py`). The model sees that view whole (up to about 15,000 characters) in place of the generic projection, and the full reply is archived for `read_tool_output`. Full evidence remains stored. |
| Domain query tool | `QueryProviderSpec(key, factory)` plus `AgentMemorySpec.query_providers`; `factory(store)` returns one `BaseTool` with the same name as `key`. |
| User-owned preference or constraint | Fact `writer="conversation_extraction"`, `extraction_policy="user_explicit"`, key rule and `extraction_hint`. |
| Canonical extraction key | Fact `fixed_lookup_key`, `key_pattern`, or `canonical_key_policy(kind=..., requested_key=..., source_run_id=...)`. |
| Recall priority | Fact `context_priority` (higher first); agent `recall_workflows` or `all_workflows`. |
| Search vocabulary | Domain/fact/workflow `search_aliases`. |
| Summary preservation | Fact/workflow `summary_guidance`, attached to summary input records. |
| Scratch cleanup after cancellation | Workflow `ephemeral_directories`, a tuple of direct run-relative directory names; registered permanent artifacts are protected. |
| Non-publication tool execution policy | `ToolCapabilitySpec`, e.g. configuration tracking or interaction behavior. Its optional `model_view` is a `"module:callable"` that turns a large reply's text into the bounded dict the model sees instead of a generic preview; the reply, its archive and saved artifacts stay unchanged. A declared view is used only for replies over 4,000 characters that carry no `model_view` of their own. Declaring a capability for a publication tool replaces its default policy, so also set `operation="scientific", plan_tracking=True`. |
| Tool display name in streams, traces and the Web UI | Domain `tool_labels`, `(tool name, label)` pairs; conflicting labels for one tool are rejected. A `ToolCapabilitySpec.display_name` takes precedence, and a delegate tool shows its worker's `display_name`. |
| HTTP endpoints | Domain `routers`, `"module:attribute"` paths of FastAPI `APIRouter` objects that the API mounts in registry order. |
| Process start/stop work | Domain `startup` and `shutdown`, zero-argument synchronous `"module:callable"` hooks. |
| Tools for the orchestrator itself | Domain `orchestrator_tools`, `OrchestratorToolsSpec(key, factory, placement)`. `factory(*, project_store)` runs once per orchestrator build and returns `OrchestratorTools(tools, worker_options)` from `agents/orchestrator/contributions.py`; `placement="leading"` puts the tools before the host's memory, planning and web-search tools, `"trailing"` (default) after them. `worker_options` maps agent keys to extra keyword arguments for their delegate factories, so a tool and its delegate share one object (coding shares its environment registry, limits and resource pool this way). |
| Another chat-model provider | Domain `llm_providers`, zero-argument `"module:callable"` factories (an adapter class qualifies) returning a `ProviderAdapter` with `name`, `aliases`, `apply_thinking` and `build`; its name and aliases become valid `.key` `provider` values. An optional `token_counter(model_name)` returns an exact text counter for the model family it recognizes by name; without one, context budgets count that family at one token per character. An optional `reasons_when_disabled(endpoint)` returns True when `thinking=disabled` still leaves the model reasoning at its lowest level; host auxiliary calls then add a fixed 4,096-token allowance to their output cap, bounded by the endpoint's limit. |

Query providers automatically receive query/read capabilities, including full detail preservation for the model. Their implementation must enforce project context and its promised read scope. `artifact_access="metadata"` omits generic file-reading tools; it does not sandbox arbitrary trusted extension code. Recall workflows are preferences, not access-control lists.

The default tool fact is `scientific_conclusion` with key `run:{run_id}:main_conclusion`. A typed-only tool sets `conclusion_lookup_key=None`. Facts normally belong to their run's key namespace; set `shared_fact_keys=True` only when the domain deliberately needs shared identities. Core writers still enforce ownership, evidence, CAS versions, and all-or-nothing batches. Registering a deterministic kind does not authorize the chat extractor to invent it. Plan facts, task cursors and agent reports have their own host-controlled writers.

Do not assign `source_type` in a builder: the host computes it from `evidence_kind`, source references and fact kind. For computation-only recall, use `query_project_memory(entity="facts", source_type="tool_result")` or `search_project_memory(query="...", source_type="tool_result")`. Source-filtered search defaults to facts and rejects explicit run/artifact entity filters. A domain's `summary_guidance` must preserve the supplied origin; it cannot promote extracted interpretations or reports into tool results.

The API and the CLI run host hooks through [`interfaces/lifecycle.py`](../src/interfaces/lifecycle.py). At start, each `llm_providers` adapter is registered before any model is built (a name or alias that is already taken is rejected), then `startup` hooks run once per process in registry order; an exception stops the host from starting. At stop, `shutdown` hooks run in reverse order, each isolated so a failure is logged without skipping the others. The [structure declaration](../src/domains/structure/extension.py) is a working example: it mounts `domains.structure.routes:router` and warms up and shuts down the shared process pool.

## 5. Check an extension without a language model

Registration, prompt assembly and graph construction need no model call and no `.key`. Save this script, for example as `check_extension.py`, and run it from the repository root with `PYTHONPATH=src:<directory that contains my_domain> python check_extension.py`. It reads `.env` as the API and CLI do, so `GBCLAW_EXTENSIONS` may be exported or set there:

```python
from core.settings.env import load_dotenv
load_dotenv()  # read .env as the API and CLI do

import extensions
extensions.validate_hooks()  # every hook imports and accepts the host's arguments

from agents.workers.spec import registered_worker_tools
from agents.streaming.events import tool_label
from agents.orchestrator.prompt import build_orchestrator_prompt
from agents.planner.prompt import build_plan_agent_prompt

print([tool.name for tool in registered_worker_tools("coordination")])  # the worker's business tools
print([tool.name for tool in registered_worker_tools("analysis")])  # a tool added to a built-in worker comes last
print(tool_label("delegate_coordination"), tool_label("measure_coordination"))
assert "### Coordination" in build_orchestrator_prompt()
assert "``coordination``" in build_plan_agent_prompt()

from core.llm.config import LLMConfig, LLMEndpoint, ModelRouter
from agents.workers.lifecycle import build_registered_worker_tools

endpoint = LLMEndpoint(  # constructing models makes no request
    provider="openai", api_key="unused", base_url="http://127.0.0.1:9", model="placeholder",
    temperature=0.0, max_tokens=None, max_input_tokens=None, timeout=30,
    thinking="disabled", reasoning_effort=None, api_mode="chat_completions",
)
router = ModelRouter(LLMConfig(use_tier_routing=False, model_routing={}, default=endpoint))
print([tool.name for tool in build_registered_worker_tools(router)])  # includes delegate_coordination
```

`validate_hooks()` also runs when the orchestrator is built. It resolves every declared hook without invoking it: fact builders, model-result builders, publication validators, canonical key policies, capability `model_view` callables, the query-provider, tool-provider, orchestrator-tools and worker factories, and the `startup`, `shutdown` and `llm_providers` hooks must import as synchronous callables whose signatures accept the host's arguments; `routers`, `routing_guidance` and `planning_guidance` must import, and the two guidance texts must be strings. An error names the extension and field, for example `coordination extension, workers[coordination].routing_guidance: invalid attribute ...`. An extension module that cannot be imported fails with `Cannot import extension module ...`, and an unknown agent key passed to `registered_worker_tools()` is an error. This detects wiring errors, not every possible runtime error in domain code. An endpoint built with `max_input_tokens=None` is budgeted at its model profile's input limit, or `default_max_input_tokens()` (262144, or 1,048,576 with `GBCLAW_CONTEXT_1M`) when the model has none; a `.key` section without `max_input_tokens` uses that default capped by the profile.

## 6. Recovery

A run binds `contract_version` and a policy fingerprint. Completed publication stores a permanent fact/conclusion artifact and a recovery pointer; retry reads that saved payload instead of rerunning builders, models or calculations. Identical retries are idempotent. Concurrent changes cannot be overwritten by a stale publication.

Missing versions, changed versions or changed policy fingerprints stop recovery with an explicit error. There is no historical decoder or automatic data migration. Increment `contract_version` when changing a hook's semantics: the fingerprint covers declaration fields and hook paths, **not the Python implementation of the hook**. Changing registry declarations does not delete existing scientific files.
