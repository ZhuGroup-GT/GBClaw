# Agent Tools

本页按科研流程介绍工具，帮助你找到需要的功能、理解输入输出，并核对结果的适用条件。表中的工具名是 agent 的调用接口；使用系统时，可以直接用自然语言向 orchestrator 描述科研任务。整体设计请先阅读[架构与设计逻辑](architecture.md)。

## 按任务查找工具

| 想完成的工作 | 从哪里开始 | 交给下一阶段的内容 |
| --- | --- | --- |
| 制订或修改研究计划 | [Project plan tools](#project-plan-tools) | 待确认的草案，以及批准后带有稳定步骤 ID 的计划。 |
| 确定晶体几何和候选晶面 | [CSL tools](#csl-tools) | 精确旋转身份、晶粒取向，或注明近似条件的 near-CSL 候选。 |
| 检查并选择 near-CSL 投影胞 | [晶粒双色图](#晶粒双色图)与[交互指南](dichromatic-map.md) | 用户确认的选点、拟合变换和几何证据。 |
| 构建或弛豫双晶模型 | [LAMMPS tools](#lammps-tools) | 配置好的 run，以及执行后的结构、日志和收敛记录。 |
| 弛豫已有帧而不重新建模 | [已有结构的普通最小化](#已有结构的普通最小化) | 单帧或有序批次中每个输入与输出的精确对应关系。 |
| 计算晶界能、剪切响应或跃迁路径 | [GB energy](#gb-energy)、[GB shear](#gb-shear)和 [GB NEB](#gb-neb) | 可供后续分析使用的计算记录与已索引产物。 |
| 分析或可视化已完成的计算 | [Analysis tools](#analysis-tools) | 派生测量、图片及其原始计算引用。 |
| 编写现有工具未覆盖的小型分析 | [Coding agent](coding-agent.md) | 从临时工作区中验证并发布的脚本与选定结果。 |
| 找回过去的结果或具体文件 | [Project memory 与 artifact 工具](#project-memory-与-artifact-工具) | 搜索候选、精确记录和文件引用。 |

**Run** 是带有稳定 ID 和状态的一次执行记录；**artifact** 是该次执行使用或生成的已索引文件；**role** 表示文件用途，例如配置或最终结构。下游工具通过这些引用选择准确的数据，无需把轨迹或大表格复制进模型上下文。

通常的流程是**规划 → 确定几何 → 配置并运行模拟 → 分析 → 人工审阅与调整**。彼此独立的案例可以并行，但分析仍需等待对应的计算产物。本页沿着这些边界组织，方便读者判断每个决定应由哪一部分负责。

## 为什么这样划分工具

架构将科研决策与可重复的数值操作分开。Agent 决定研究哪个晶界、检查哪个结果、提出什么后续计算；工具实现负责几何检查、参数校验、执行和结果登记。将这些步骤明确下来，可以让不同案例采用一致的计算方法。

输入输出保留 run 和 artifact 身份，因为科学结论依赖具体模型、参数和源数据。例如，NEB 端点必须指向某一份准确的结构文件，仅仅知道“对话里提过一次应力下降”还不够。程序执行完成、最小化收敛和物理解读成立也分别具有不同含义，各工作流会保留相应限定。

已建立的方法使用固定工具，小范围的新需求由 [coding agent](coding-agent.md) 补充。当某个脚本成为经常使用的方法时，可以审阅其物理假设和数值行为，再按统一的来源记录与发布规则，将它整理为正式工具。接入位置见[扩展开发指南](extension-guide.md)。

## 工具分层原则

公共接口只暴露模型需要作出的决定，执行和记录工作由服务层完成：

- agent-facing tools 只暴露 LLM 需要做决策的动作。
- worker 专属实现位于各自的 domain package `domains/structure/`、`domains/lammps/`、`domains/analysis/`，每个 workflow 有自己的工具模块（例如 `domains/lammps/neb/tool.py`）；各 domain 的 `catalog.py` 汇总其 worker 的工具。记忆适配器位于 `agents/memory/tools/`，计划工具位于 `agents/planner/tools.py`。
- 文件写入、memory record、subprocess、schema validation 等确定性逻辑放在内部 helper/service 函数。
- project memory 写入通常是工具内部副作用，不让 agent 手动调用写 memory 工具。
- project memory 先通过全量索引搜索发现候选，再用结构化 semantic query 精确读取；artifact 通过稳定 run ID 与 role 读取。

## Orchestrator tools

orchestrator 的工具来自 `src/agents/orchestrator/graph.py`。`delegate_*` 工具来自注册表：每个 `WorkerDefinition` 声明其 delegate factory，由 `src/agents/workers/lifecycle.py` 中的 `build_registered_worker_tools()` 按注册顺序创建。`list_available_potentials`、`list_coding_environments` 等领域工具来自 `DomainExtension.orchestrator_tools` 的 factory（`src/agents/orchestrator/contributions.py` 中的 `build_domain_orchestrator_tools()`），放在 memory/plan/web 工具之前或之后。

| Tool | 来源 | 用途 |
| --- | --- | --- |
| `query_project_memory` | `agents/memory/tools/query.py` | 查询结构化 runs、持久 facts、semantic summary 或历史 summary 归档 |
| `search_project_memory` | `agents/memory/tools/query.py` | 搜索全部 fact/run/artifact metadata，返回有界候选 reference |
| `list_run_artifacts` / `read_run_artifact` | `agents/memory/tools/artifacts.py` | 按 run ID/role list 或读取 manifest-verified artifact |
| `read_tool_output` | `agents/memory/tools/output.py` | 按 output ID、JSON 字段和有界分页读取已保存的完整工具返回。 |
| `read_agent_conclusion` | `agents/memory/tools/conclusions.py` | 按独立编号读取某次子 agent 调用的完整最终结论或来源 metadata。 |
| `get_task_state` | `agents/memory/tools/task_state.py` | 读取 thread 进度或某次调用的状态、阻塞原因与结论引用。 |
| `report_artifact_usage` | `agents/memory/tools/artifacts.py` | 按 provenance 报告 artifact bytes |
| `get_project_plan` | `agents/planner/tools.py` | 精确读取 active/named plan 或不可变结构修订 |
| `get_project_plan_draft` | `agents/planner/tools.py` | 精确读取 pending/named approval draft |
| `approve_project_plan_draft` / `reject_project_plan_draft` | `agents/planner/tools.py` | 应用当前用户的显式决定 |
| `update_project_plan_step` | `agents/planner/tools.py` | 对非科研 plan step 执行经过校验的状态转换 |
| `search_web` | `agents/orchestrator/web_search.py` | 可选，查询当前外部事实并返回 sources |
| `list_available_potentials` | `domains/lammps/catalog.py` | 读取注册的势函数 metadata |
| `list_coding_environments` | `domains/coding/environments.py` | 列出 allowlisted Coding Conda alias/capability，不暴露 host 路径 |
| `delegate_plan` | `agents/planner/delegate.py` | 深入解析复杂问题并返回按依赖排序的建议性计划 |
| `delegate_csl` | `domains/structure/delegate.py` | 向 Structure worker 委派 CSL/GB plane/orientation 任务 |
| `delegate_lammps` | `domains/lammps/delegate.py` | 委派 LAMMPS config/run 任务 |
| `delegate_analysis` | `domains/analysis/delegate.py` | 委派 OVITO render / post-processing 任务 |
| `delegate_coding` | `domains/coding/delegate.py` | 在临时沙盒中执行 specialist 未覆盖的小型脚本、转换或绘图 |

科研 worker 的业务工具同样来自注册表。每个 domain 的 `catalog.py` 构建工具列表，其 `extension.py` 通过 `DomainExtension.tool_providers` 中的 `ToolProviderSpec(key, worker, factory)` 声明该列表。`WorkerSpec` 通过 `src/agents/workers/spec.py` 中的 `registered_worker_tools()` 加载工具：该函数按注册顺序合并同一 worker 的全部 provider，并拒绝重复的工具名，因此扩展无需修改内置 worker 即可为其增加工具。流式事件、trace 和 Web UI 中的显示名称来自 `ToolCapabilitySpec.display_name` 或 `DomainExtension.tool_labels`，未声明时使用工具名。

worker 会获得只读 semantic search/query 和 artifact list/read 工具，不能修改 summary 或删除 artifact。 planning worker 没有科研执行工具；只有它获得 request-scoped `create_project_plan_draft`。当 `delegate_plan` 带 `existing_plan_id` 时，它还会获得精确 `get_project_plan` 和 planning-only `revise_project_plan_draft`。同时预留显式 `extra_tools` 注入点，但默认不接入 web search。

coding worker 的 project 文件边界更窄：输入必须是精确的 completed-run artifact reference，由 host 快照到只读 `/inputs`。Deep Agents 提供 `ls`、`read_file`、`write_file`、`edit_file`、`delete`、`glob`、`grep` 和 `execute`；host 另外加入 `request_input_artifacts`（在授权范围内追加输入）和 `publish_coding_result`。只有 `publish_coding_result` 发布的文件，或 worker 未发布即停止时由 host 按声明的 `expected_outputs` 保存的文件会持久化；其余 workspace 内容在调用结束时删除。

delegate 的 `environment` 参数必须来自 `list_coding_environments`。默认别名 `current` 跟随运行服务的 Python 环境；`lammps` 从 `GBCLAW_LAMMPS_CONDA_ENV` 读取实际 Conda 环境名。可选别名 `ase` 从 `GBCLAW_ASE_CONDA_ENV` 读取环境名，默认为 `GBclaw-ase`，只有环境和 ASE 均已安装后才能使用。实际环境改名不会改变别名；可用性检查与依赖边界见[运行环境注册表](coding-agent.md#运行环境注册表)。`lammps` 只允许小型自定义串行或受限本机 MPI LAMMPS 验证，不允许替代已有或 production LAMMPS workflow。

同一个 listing 还返回 coding compute-slot 上限；`delegate_coding` 的可选 `compute_slots` 与 `mpi_ranks` 会从所有并发 worker 共用的 coding 专属 weighted budget 中预留。

## Project plan tools

计划将“提出工作”与“批准执行”分开。草案让研究者先检查计算内容和依赖关系，批准后才成为当前计划。后续修改也会保留正在执行及已完成工作的身份和历史。

领域公共 API 是 `planning` package：`models.py` 保存 schema 与稳定 key， `service.py` 负责 approved plan 进度/revision，`draft_service.py` 负责 proposal/approval/rejection； `agents/planner/tools.py` 只负责 LangChain adapter。

`create_project_plan_draft` 写审计 `plan_draft:<draft_id>` 与 `project:pending_plan`，不会激活或执行拟议计划。step 使用稳定 ID、明确 capability 和有序 dependencies。`get_project_plan_draft` 返回精确 proposal；`get_project_plan` 只返回已批准的 active/named plan 或指定 revision。

修改请求由 orchestrator 将用户的完整要求和 existing plan ID 交给 planning worker， orchestrator 自己不修改计划结构。`revise_project_plan_draft` 可以提出 metadata 和未来 pending/blocked 工作的修改，但不能删除或改写 running 或历史 step。显式批准会原子地 create/revise active plan、记录 source draft、retract pending pointer 并写不可变 revision；reject 保留草案审计记录但不改变 active plan。

所有 step transition 都经过校验并增加版本；带 `plan_id`/`step_id` 的科研 delegate 会自动更新自己的边界状态，orchestrator、memory、user-input 和未来 search step 则使用 `update_project_plan_step`。每次数据库写入都会在同一事务更新 FTS 与 semantic revision。 pending `plan-draft.md`、active `plan.md`、draft/current/revision JSON 都可直接在项目文件浏览器查看，但不是可写状态。`PlanApprovalGateMiddleware` 每次 tool call 都重读 pending pointer；未决定时阻止执行/进度/替换 planning，并要求最新 user message 明确授权 approve/reject。

## CSL tools

涉及晶体学身份或晶界几何的问题从这里开始。精确 CSL 结果与 near-CSL 近似具有不同含义：相近的参考旋转可以帮助描述一般晶界，但不会让它成为精确 CSL 构型。工具在输出与持久记录中都保留这一区别。

定义在 `src/domains/structure/csl/tools.py`，汇总为 `STRUCTURE_AGENT_TOOLS`。`src/domains/structure/catalog.py` 再加入[晶粒双色图工具](#晶粒双色图)，组成 Structure worker 的 tool provider：

| Tool | 作用 |
| --- | --- |
| `analyze_tilt_geometry(plane1, plane2, axis=None, plane1_is_family=False, plane2_is_family=False, axis_is_family=False)` | 解析可选的立方晶面族/晶向族，并在不枚举 CSL 的情况下返回 pure-tilt 几何。工具会按 proper cubic symmetry 将带符号/置换的原始成员归并为有序无向晶面对等价类，因此 `ambiguous` 只表示存在多个物理等价类。对于两个晶粒局部坐标中的具体晶面及 `axis_is_family=True`，工具通过立方对称分别解析 `plane1_axis`/`plane2_axis`，同时返回公共参考系表示；不会再把两个局部 Miller 三元组的原始叉积当作物理轴。 |
| `compare_miller_planes(plane1, plane2=None, axis=None, reference_family=None)` | 约化一个 Miller 法向，或比较两个：检查与轴垂直性、比较晶面夹角，并在不做 CSL 枚举的情况下寻找最近的立方晶族成员。 |
| `match_csl_tilt_references(axis, basis, plane1, plane2, axis_is_family=False, ...)` | 按指定晶体构型验证 Sigma，扫描角度容差内的全部旋转，再匹配有序精确晶面对并返回两侧偏差。`axis_is_family=True` 会先解析并对齐晶粒局部晶面/轴对，并同时返回公共坐标和局部坐标下的参考晶面。 |
| `score_csl_tilt_plane_pairs(axis, basis, plane1, plane2, candidate_pairs, ...)` | 在不枚举 Sigma、不生成晶面表的情况下，按固定轴对称性验证并确定性评分已给的精确 CSL tilt 晶面行。 |
| `suggest_low_index_near_csl_planes(axis, source_run_id=None, source_branches=None, source_pairs=None, ..., deduplicate=True, gb_type="tilt")` | 筛选精确 CSL tilt 晶面对的轴相容低指数近似，返回两侧误差和失配角变化。`source_run_id` 一次读取已完成精确枚举保存的全部晶面表行（最多 2,000 行；更大的 run 用 `source_branches` 缩小范围）；`source_pairs` 用于没有保存 run 的最多 50 行。在同一分支内，若两条建议在保持倾转轴的立方对称操作下是同一晶面对（每个法线不计正负、两晶粒顺序均可），就合并为一个候选并列出全部来源行。模型看到按分支列出的去重候选表，上限 14,000 字符，超出部分以 `omitted_candidates` 标记；保存的结果保留每一行。仅接受 pure tilt 边界。 |
| `list_csl_sigmas(basis, u, v, w, limit=50)` | 给定晶体构型和旋转轴，列出全部 `(Sigma, angle, rotation_m, rotation_n)` 分支；保留重复 Sigma 行。 |
| `list_csl_gb_planes(..., rotation_m=None, rotation_n=None)` | 给一个精确旋转分支列出 symmetry-filtered GB plane；Sigma 有歧义时必须提供分支整数。 |
| `list_csl_gb_planes_batch(..., sigmas=None, rotations=None)` | 并行列出最多 25 个旋转分支的 GB plane；重复 Sigma 使用 `rotations` descriptor。两个晶面表工具的表较大时，模型收到按 `[GB1, GB2, 类型代码, 参考原子数]` 紧凑列出的行，视图上限 14,000 字符；未放入的行计入 `omitted_rows`，并附指向 `full_output` 的提示。工具回复、trace 和保存的 artifact 仍是完整的文本表。 |
| `get_tilt_gb_orientation(..., rotation_m=None, rotation_n=None)` | 为一个精确 CSL tilt 旋转生成 `gb_minimize` 可用的 top/bottom orientation 和 period；歧义 Sigma 必须指定分支。 |

Structure worker 还会获得带类型参数的记忆查询 `query_csl_orientation`，按 basis、旋转轴、Sigma、GB plane 和分支整数返回一条已保存的精确 CSL 旋转/晶面取向。

关键约束：

- `basis` 支持 `fcc`、`bcc`、`diamond`、`sc`。
- GB plane search `limit` 会 clamp 到 `1..6`。
- GB1/GB2 在输出前会自动约分为 primitive Miller index；CSL-compatible 缩放量只保留给内部公周期胞构造。
- Sigma 1 是 identity，不做 GB plane 查询。
- Sigma 是重合指数，不是唯一旋转 ID；精确身份是 `(axis, basis, Sigma, rotation_m, rotation_n)`。仅当基本区间中只有一个分支时才接受只传 Sigma，否则工具会返回所有可选分支。
- batch 查询使用 `core/execution/process_pool.py` 的共享 Structure worker process pool。串行模式在 pool 外加 task-level lock；并行模式允许多个 caller 同时提交，但 executor 会把合计进程数限制在 `GBCLAW_WORKERS` 内。共享总上限默认为 18，并受可用物理核心数限制。Linux 下，每个池进程绑定到不同的物理核心，数学库每进程使用一个计算线程。
- `get_tilt_gb_orientation` 只支持 tilt GB，frame 为 `y = GB normal`、`z = tilt axis`、`x = in-plane`。
- 绑定 project 的 CSL 工具会对规范化输入生成 fingerprint；存在完全相同的 completed CSL artifact 时，直接返回其 run/artifact ID 和已存结果，不再重新计算。
- CSL orientation fact 及其 typed memory query 会把 `rotation_m`/`rotation_n` 纳入 lookup identity。分支感知查询可以读取匹配的旧 fact，但绝不会误读仅 Sigma 相同的另一分支。
- 复合反向查询是常规 fast path。基础审计路径为 `analyze_tilt_geometry` → `list_csl_sigmas` → `list_csl_gb_planes(_batch)` → `score_csl_tilt_plane_pairs`；它用于展示中间证据、自定义候选、复现和诊断，不需要让模型目测排列晶面表。
- 完成的反向/评分分析会写入一个确定性 `gb_crystallography` fact，其 key 由规范化的 basis、axis 和有序晶面唯一确定。它保存实际几何与描述性 CSL 分析，并显式标记 `authoritative_for_structure_construction=false`；失败或未完成的 run 不会写入该 fact。
- near-CSL 输出是几何筛选候选。晶面对发生变化后只能标记为“参照源 Sigma”，不能声称仍保留该精确 Sigma；后续建模应使用 general tilt orientation 流程，不应把旧 Sigma 传给 `get_tilt_gb_orientation`。正向筛选必须提供非空的精确源 Sigma； general 输入必须使用反向查询。
- 反向查询从给定 general 晶面对推导 tilt misorientation，按指定晶体构型枚举 Sigma，并扫描默认 5° 角度容差内的全部旋转。选中的 reference 还必须满足最差单侧晶面偏差阈值，并按晶面对质量排序。只要任一偏差非零，该晶界仍是 general/non-Sigma，选中的 Sigma 只能作为 reference。反向查询的 exact-plane limit 默认为 5。
- `suggest_low_index_near_csl_planes` 要求 `gb_type="tilt"`；twist 和 mixed 边界会被拒绝，因为仅靠晶面对夹角无法描述它们的完整失配。

底层 crystallography 在 `src/domains/structure/csl/generator.py`，agent 不直接调用其中的大量函数。

## 晶粒双色图

Structure worker 使用 `src/domains/structure/dichromatic/tools.py` 中的工具：

| Tool | 作用 |
| --- | --- |
| `get_dichromatic_map_options(lattice="FCC", axis="110")` | 根据整数 tilt axis 返回库支持的角度区间、轴向原子层和 CSL 预设。 |
| `render_dichromatic_map(parameters, purpose, source_run_id=None)` | 通过无窗口 CLI 输出项目 PNG 和数值 JSON，记录绘图目的及可选的已完成来源 run。 |
| `prepare_dichromatic_cell_selection(purpose, parameters=None, source_run_id=None)` | 为初始设置弹出并预填悬浮窗，由用户选择四个胞顶点。省略 `parameters` 可复用已完成、未施加应变的 dichromatic-map run 的设置。 |
| `get_dichromatic_cell_selection(request_id)` | 读取已保存请求的状态、确认的选点和已有应变结果。 |
| `apply_dichromatic_cell_strain(request_id, ...)` | 对用户已确认的胞施加应变，记录变换、残差、artifacts 及来源 selection run。 |
| `analyze_dichromatic_cell_edges(request_id)` | 使用已保存应变比较全部轴向层中的同层配对，返回每条边的全局最小值、双晶粒向量、多个标注和候选摘要；已有全部层分析直接复用。 |

共享的 `DichromaticMapRequest` 接收 `lattice`（`SC`、`FCC`、`BCC`）、`axis`、 `angle_deg`、`lattice_constant`、`width`、`height`、`center`、`marker_size`、 `display_rotation_deg`、`g1_color`、`g2_color`、`visible_grain_layers`、 `show_coincidence`、`show_csl_cell` 和 `local_cutoff`。省略角度时使用库针对该轴的预设；明确输入的角度会完整保留，不会吸附到附近的 CSL 值。选择角度和层之前先查询 options，因为对称性约化后的角度区间随旋转轴变化。层编号从 0 开始，两个晶粒各传一份层列表； `null` 表示全部显示。

坐标、视野宽高、胞矢量和近邻距离阈值使用 a0 单位。`lattice_constant` 明确给出以 Angstrom 为单位的 a0，默认 `1.0`，不隐含任何材料。`marker_size` 是 points squared 单位的标记面积。晶粒 1 使用实心标记，晶粒 2 使用空心标记，轴向各层使用不同形状。显示旋转和层筛选只改变图像；精确胞计数对应完整的物理胞，不随显示视野改变。

适配器复用已安装的 [DichromaticMap](https://github.com/Yazhuo-Liu/DichromaticMap) 数值模块，通过 Matplotlib Agg 绘图。原库的桌面 CLI 即使只保存 PNG 也会启动 Qt，因此服务端使用独立的 `domains.structure.dichromatic.adapter` 入口。重合检测只比较相同轴向层的原子。可选 local pairs 是互为最近邻、间距大于精确重合容差且不超过 `local_cutoff` 的配对，阈值最大为 `0.5 a0`；局部近邻不代表精确 CSL。未识别出精确公周期胞时保持空结果，不替换为应变拟合。输出的胞是保持 A 层的二维公周期胞，不一定是三维 primitive cell；原子数采用半开二维胞及一个完整轴向周期的计数约定。该工具绘制理想未应变晶格，不执行晶界弛豫或能量计算。

Agent 与浏览器导出都会创建独立的 `dichromatic_map` workflow，目录为 `artifacts/structure/dichromatic_map/{run_id}/`。文件包括规范输入 `input.json`、 `dichromatic_map.png`（role `dichromatic_map_png`）、`dichromatic_map.json` （role `dichromatic_map_data`）、`render.log` 和 run manifest。完成的 run 及其 `scientific_conclusion` fact 记录 purpose、调用来源 origin、可选 `source_run_id`、实际几何、单位换算、计数、适用范围和精确 artifact 引用；完整点坐标留在 JSON artifact。来源 run 表示本次绘图的科研上下文，理想晶格投影本身不读取 CFG。浏览器与 agent 共用同一套导出和记忆记录流程。

[交互选胞流程](dichromatic-map.md) 将准备候选、用户确认和施加应变分开。Agent 不能替用户指定四个候选点；网页提交前只返回请求引用。应变只作用于明确选定的二维投影胞，不会把原始未应变的 near-CSL 边界重新标记为精确 CSL。

在 `GBclaw` conda 环境独立运行：

```bash
PYTHONPATH=src conda run --no-capture-output -n GBclaw \
  python -m domains.structure.dichromatic.adapter \
  --input-json /tmp/dichromatic-input.json --output-dir /tmp/dichromatic-output
```

输入文件是 `DichromaticMapRequest` JSON 对象，例如 `{"lattice":"FCC","axis":"110","angle_deg":38.94244126898138,"width":12,"height":9}`。使用模块方式启动，避免脚本文件名遮蔽已安装的 `dichromatic_map` 包。独立入口写出 PNG 和 JSON，项目 run/fact 由 agent 或浏览器适配器记录。投影前检查资源边界：约化轴指数限制为 `[-12,12]`，两个晶粒的临时候选列总数不超过 100,000。

## LAMMPS tools

模拟配置与执行分为两个动作。配置工具解析并校验物理模型、保存参数，返回一个 run ID；执行工具使用同一个 ID 生成结构与数值结果。这样既能检查将要执行的内容，也能事后将结果追溯到准确的输入。

GB minimize、shear、GB energy 和 structure minimize 导出的 CFG 均按 LAMMPS atom type 写入配置中的元素符号，包括边界层和中间帧。同种元素的两个晶粒可以分别对应两个 atom type，但元素映射相同，例如 `Ni Ni`，因此每个原子的 LAMMPS type 还以附加列 `type` 写出。坐标保留 12 位有效数字，H0 晶胞用精确盒子值改写（LAMMPS 本身只打印 6 位），所以这些 CFG 可以无损转回 LAMMPS data。

每个 workflow 的工具实现在 `src/domains/lammps/` 下各自的 `tool.py`（`minimize/`、`structure_minimize/`、`energy/`、`shear/`、`neb/` 和 `cfg_convert/`）；`infer_tilt_orientation` 位于 `orientation.py`。`src/domains/lammps/catalog.py` 定义 `list_available_potentials`，并把全部工具汇总到单一 `LAMMPS_AGENT_TOOLS` 列表，由 LAMMPS extension 声明为 LAMMPS worker 的 tool provider：

| Tool | 作用 |
| --- | --- |
| `list_available_potentials(element="")` | 读取配置的 potential 目录中的 `potentials.json`，可按元素过滤。 |
| `convert_cfg_to_lammps(cfg_paths, ...)` | 把项目内 extended CFG 转为保留 LAMMPS atom type 的 `atom_style atomic` LMP artifact；如有兼容的已注册 source LMP，则恢复其 box origin，并索引完整源/输出映射。 |
| `infer_tilt_orientation(...)` | 从 tilt axis 和 GB plane/family 推断 LAMMPS orientation。`(100)` 等圆括号输入是具体晶面，使用默认的 `plane_is_family=false`；只有 `{100}` 等晶族才设为 `true`。 |
| `estimate_gb_box_size(...)` | 只读估算尺寸、x mismatch、有效重复匹配候选和 approx atoms。省略晶粒尺寸时自动确定，显式尺寸保持不变；每个候选都包含材料 nm 尺寸和 top/bottom 完整 `[nx,ny,nz]`，不受 `x_pbc` 影响。 |
| `write_gb_minimize_config(...)` | 校验输入，自动确定省略的重复数，将解析后的显式重复数写入配置，并返回 configured minimization `run_id`。 |
| `run_gb_minimize(run_id, ...)` | 在同一稳定 run 下外部执行 MPI minimization。 |
| `write_structure_minimize_config(purpose=..., source_lmp=..., structures=..., ...)` | 对已有 LMP 或有序批量输入配置固定盒最小化；`source_lmp` 与 `structures` 二选一，逐项记录原始帧来源和用途。 |
| `run_structure_minimize(run_id, ...)` | 执行独立的 `structure_minimize`；批量项目依次计算并输出到同一 run 目录，不加载荷，可选 fixed IDs 固定全部方向。 |
| `write_gb_energy_config(...)` | 校验连续周期 x-z / y 真空的 GB-energy 输入，并返回 configured `gb_energy` run ID。 |
| `run_gb_energy(run_id, ...)` | 松弛 bicrystal 和四套 bulk/surface reference，计算 GB energy，并持久化 artifacts/run/fact。 |
| `write_gb_shear_config(source_run_id, ...)` | 连接已完成 minimization 或 completed shear 的最终状态，并返回 configured shear `run_id`。 |
| `run_gb_shear(run_id, ...)` | 在该 run 下外部执行 MPI 准静态 x shear。 |
| `write_gb_neb_config(initial_structure_path, final_structure_path, ...)` | 为精确 CFG/LMP 端点配置 NEB，可选 shear 边界约束，并记录端点来源。 |
| `run_gb_neb(run_id)` | 执行已配置 NEB，在内部转换 CFG 端点并输出 MEP 数据。 |

`convert_cfg_to_lammps()` 单次调用即创建并完成一个 `cfg_convert` run。canonical `input.json` 保存源路径和 SHA-256 指纹；run 会快照每个 CFG、写出每个 `converted_lmp`，并把源/输出两端注册到 manifest 和语义 artifact 索引。由于 extended CFG 不保存 `xlo/ylo/zlo`，转换器会查找 source run 中兼容的 `minimized_lmp` 或 `sheared_lmp` 并复用其 box origin；找不到时会明确返回 warning，再把盒中心置于 `(0,0,0)`。若 CFG 晶胞就是该 LMP 的盒子（精确相等或正是 LAMMPS 打印的截断值），则同时采用其精确晶胞（`cell_source`）。原子 ID 与 image flags 保持不变。atom type 取自 CFG 的 `type` 列；没有该列的 CFG 按原子 ID 从该 LMP 取得；否则按元素标签分配，共用同一元素的 type 会合并，并给出 warning（`type_source`）。倾斜晶胞以 H0 的行作为晶胞向量；返回结果给出后续 `read_data` 可直接使用的精确路径以及 origin reference/fingerprint。

### 已有结构的普通最小化

若目标只有 CFG，先调用 `convert_cfg_to_lammps(cfg_paths=[精确帧路径], purpose=...)`，从结果选定对应的 `converted_lmp` artifact ID 或项目相对路径，作为 `write_structure_minimize_config` 的 `source_lmp`；必填 `purpose` 记录本次选帧和弛豫的目的。随后以返回的 run ID 调用 `run_structure_minimize`。已有 LMP 可以跳过转换。

多帧任务将完整有序输入交给一个 LAMMPS worker。缺少 LMP 时，一次调用 `convert_cfg_to_lammps(cfg_paths=[...], purpose=...)` 转换，并保留每个源帧与输出的精确映射。随后 writer 以 `structures` 替代 `source_lmp`，只调用一次 writer 和一次 runner：

```python
write_structure_minimize_config(
    purpose="Compare unloaded states selected around stress drops",
    structures=[
        {"source_lmp": "<精确的第一个 LMP artifact ID>",
         "purpose": "Relax the selected pre-drop peak without loads"},
        {"source_lmp": "<精确的第二个 LMP artifact ID>",
         "purpose": "Relax the selected post-drop minimum without loads"},
    ],
)
```

所有 item 在一个 `structure_minimize` run 内按输入顺序执行。每项可以覆盖公共 purpose、约束、势函数／PBC 和最小化参数。item 指定 `fixed_ids` 或 `fixed_ids_paths` 时，会替换整套公共固定原子选择，因此 `fixed_ids=[]` 可使该项所有原子自由。执行前统一校验全部输入。批量结果保留每项及其精确源／输出映射；后续工具应明确选中目标输出 artifact，不能只给 batch run ID 后默认取第一项。 `label` 是可选的人类可读项目名称，例如 `before_drop`，不会改变最终构型文件名；处理大量帧时可以全部省略。item 省略 `purpose` 时，继承整个批量任务的 purpose。

盒长、倾斜量和原点保持不变，每项只执行一次 `minimize`。默认所有原子自由，不施加弹簧、外载荷或 `box/relax`。提供 `fixed_ids` 或 `fixed_ids_paths`（项目内 ID 文件或 artifact ID）时，列出的所有原子均通过 `setforce 0 0 0` 固定 x/y/z。PBC 与原子约束独立：默认继承所选来源的 PBC 和势函数；无来源记录的导入需明确提供 `boundary`、已注册的 `potential_name` 和逐 type 的 `elements`。固定盒支持 `p`/`f`，拒绝会改变盒子的 shrink-wrap。支持 atomic 正交／restricted-triclinic data，以及 `eam/fs`、`eam/alloy`、 `adp`、`nep` 的元素映射。

运行保存在 `artifacts/lammps/structure_minimize/{run_id}/`。单结构运行包含 `minimized.lmp`、`minimized.cfg`、输入／势函数快照、轨迹、日志和 `minimize_run.json`。批量的所有最小化构型直接放在同一个 run 目录，使用实际输入 LMP 的文件主名加 `_minimized`：例如 `frame_120.lmp` 输出为 `frame_120_minimized.lmp` 和 `frame_120_minimized.cfg`。输入文件主名重复时，自动加序号得到 `frame_120_minimized_2.lmp`／`.cfg` 等。使用 artifact ID 输入时也按其实际源文件名命名，不使用 artifact ID 或可选 label。

每项输入、工作文件和日志放在 `items/<item_key>/`，内部 item key 包含输入顺序编号。结果的有序 `items` 保存 `item_index`、`item_key`、用途、source/origin、约束、状态及精确输出 artifact ID／路径。首次失败或取消即停止批量计算，已成功的结果保留，后续未执行项标记为 `pending`；汇总分别统计完成、失败、取消、待执行和收敛项。

结果记录前后能量和力、停止原因、收敛标记，以及盒子、ID、type 和固定原子位移检查。completed 不等于达到收敛容差；无约束弛豫可能改变加载位移，来源应变只描述原始帧。

转换通过 `run_sources` 为每个原始帧建立关系，同一运行的多帧也分别保存。原运行、 artifact、路径、hash、可用的步数／应变以及 purpose 会贯穿转换和最小化，保存在配置、产物 metadata、语义记忆、搜索和 worker handoff 中。独立 workflow 不改变 shear 默认来源；shear 仍要求初始 GB 构建所提供的边界 ID。其输出可以直接选为全自由 NEB 的端点。

### Config 写入

`write_gb_minimize_config()` 会：

- 把 `GBMinimizeConfig` 的所有科学字段暴露为强类型工具参数；不常修改的 minimization 参数保留默认值，agent 可以省略。
- 补齐 catalog 默认值，构造完整 canonical config，并校验类型和数值范围。
- 解析并校验势函数文件，然后把绝对路径写入 config。
- 对 `eam/alloy`、`eam/fs` 和 `adp`，LAMMPS 会用势函数文件中的元素质量覆盖 `mass` 设置，因此 config 记录该质量；显式给出且不同的 `mass` 会被替换，并作为 warning 报告。`write_gb_energy_config()` 同样如此。
- 使用这份最终 config 估算几何并执行 `box_size_violations()`。
- `top_size` 或 `bottom_size` 省略时按策略生成整数建议；显式正数（包括非整数）保持原值，不截断，并产生相应 warning。
- agent 自选几何不合格时不创建 run。用户明确确认两组精确尺寸后可传 `user_confirmed_size_override=true`，此时创建 run，并把所有策略问题持久化为 warning； agent 不得用该开关绕过自己生成尺寸的约束。
- 两种 x 边界模式都把 20 nm 作为缺省的 x 优选尺寸。正数 `target_x_nm` 会改变排序目标，但不会成为尺寸下限；agent 建议仍保留每晶粒 10 nm y 和 1 nm z 的最小值。所有建议的 top/bottom x 整数重复数组合都必须满足 `domains/lammps/common/defaults.py` 中缺省的小于 2% mismatch。
- `x_pbc=false` 和 `x_pbc=true` 使用相同的 x 重复匹配与 mismatch 校验；该参数只影响 LAMMPS box 的 x 留白和连续连接方式。
- 创建 configured semantic run，并在其 artifact 目录写唯一的 canonical `input.json`。
- 把 canonical config 镜像到可搜索的 run parameters，同时仍以 `input.json` 为权威来源。
- 只把该文件注册一次，artifact role 为 primary `config`。执行阶段直接复用它，不会再生成重复的 `config_used.json`。

配置 schema 在 `src/domains/lammps/minimize/config.py`，核心 dataclass 包括：

- `MaterialConfig`
- `SimulationConfig`
- `PotentialConfig`
- `Orientation`
- `GrainConfig`
- `GBMinimizeConfig`

默认 simulation 包含 Fe BCC `a0=2.8553`、`min_style=cg`、`relax_cycles=4`、 `boundary="p p p"`，并设置 `x_pbc=false`，因此 x 两侧各保留 `y_vacuum`。应保持 `boundary="p p p"`；x 方向真空 vs 周期只通过 `x_pbc` / `y_vacuum` 控制。写入/加载时若 `boundary` 不是 `p p p` 会被拒绝，便于 agent 重新生成。设置 `x_pbc=true` 后， x 两侧改为仅保留 `y_eps`，得到适合后续 sliding workflow 的近似连续周期 x box。 `enable_initial_staged_relaxation` 默认也是 `false`；启用后，会在常规循环 box relaxation 之前依次执行三次预松弛：冻结 bottom、冻结 top、最后全原子自由。

### Run 执行

`run_gb_minimize()` 会：

1. 解析 project 内 configured `run_id`。
2. 默认使用 `LAMMPS_AGENT_MPI_RANKS`，除非用户显式传 `np`。
3. 直接写入 `artifacts/lammps/minimize/{run_id}/`。
4. 复用已经注册的 config，并按 artifact role 注册新产生的 log、结构、boundary IDs 和 trajectory。
5. 调 `run_in_lammps_env()` 执行：

```text
conda run --no-capture-output -n <LAMMPS_CONDA_ENV>
  mpirun --map-by core --bind-to core -n <np>
  python -u src/domains/lammps/minimize/engine.py --output-dir ... --config ...
```

6. 从 `log.lammps` 提取 key metrics。
7. 让同一 semantic run 转到 completed、failed 或 cancelled。

最小化 runner 将完整启动输出保存为 `launcher.stdout.log` 和 `launcher.stderr.log`，对应索引角色 `launcher_stdout`、`launcher_stderr`。即使 Python 导入失败、尚未生成 `log.lammps`，仍可查阅这两个文件。返回的错误优先提取实际 Python 异常或 LAMMPS 错误，不再只保留 traceback 开头。对于确定的依赖或启动配置错误，agent 应先报告并解决原因，再创建相同配置的替代运行；用户明确要求重试时除外。

托管 LAMMPS 工作流将每个 MPI rank 分配到物理核。LAMMPS 使用 `-nonbuf` 关闭屏幕和日志缓冲，并在每条 Python 命令返回时显式刷新输出；长时间最小化期间通过 `thermo_modify flush yes` 立即刷新 thermo 行。这也兼容 `-nonbuf` 只对 partition 模式生效的 LAMMPS 版本。网页日志预览每 500 ms 检查更新。Thermo 仍按配置的步数间隔输出（GB 最小化为每 100 步）。

Runner 还会写入并索引 `minimize_run.json`。其中逐个保存所有 LAMMPS minimization stage 的 stopping criterion、收敛标记、最终能量/力和 iteration 数。收敛结论只看最后一次 minimization（`convergence_basis="final_minimization"`）。中间阶段的未收敛计数仅用于历史诊断，不触发收敛警告；最后一步未达到容差时才提示。completed 与达到容差分别报告。

LAMMPS delegate 向 orchestrator 返回结论、紧凑的 run／manifest 引用、最终收敛指标及已验证的后续分析请求，不再附带完整工具调用历史或重复原任务。内部 trace 仍用于计划跟踪，网页保留流式工具事件；详细科学数据可通过已索引的 artifacts 查阅。

结果和 `minimize_run.json` 同时保留来自规范构建配置的 `structure_metadata`。对于 `x_pbc=false`、`y_vacuum>0` 且具有最终 CFG 的已完成运行，结果会带上必需的 `analysis_followups`，请求 `analyze_gb_periodicity`。除非用户明确跳过，orchestrator 默认继续委派 Analysis 执行；LAMMPS runner 本身不启动 OVITO。这里判断的是构建时的 X 两侧真空，Analysis 还会独立核对 relax 后的实际几何。LAMMPS `boundary="p p p"` 和 CFG 导入的 PBC 标记仍可能都是周期的。失败、取消或 X 连续周期的结果不会触发此交接。已有精确来源及参数相符的分析时复用结果，不重跑最小化；分析失败或不支持时，单独报告，不改变已完成的最小化结果。

LAMMPS 子进程控制在：

- `src/domains/lammps/common/runner.py`: `conda run`、timeout、cancel polling。
- `src/core/execution/sessions.py`: session cancel event、active process tracking、 process tree kill。
- `src/domains/lammps/common/resources.py`: 基于 `src/core/execution/resources.py` 中 `SlotPool` 的串行/按 MPI ranks 加权的并发准入。

### GB energy

`write_gb_energy_config()` 复用 minimization writer 的 material、orientation、repeat、potential 和 minimization 强类型字段，也使用相同的自动尺寸与几何限制。该方法要求 `x_pbc=true`、`boundary="p p p"` 和正数 y vacuum，因为当前热力学循环假定 x-z 界面连续周期，只扣除外侧两个 y 自由表面。该 cell 在 y=0 只有一个内部 GB，y vacuum 隔离周期镜像；结果明确记录 `interface_count=1`、两个自由表面和分母 `A`，不支持双 GB/`2A` 模式。

`run_gb_energy()` 先松弛 bicrystal，再分别松弛 top/bottom 的 bulk 和双自由表面 slab reference。它记录每原子 bulk energy、单表面 energy、界面面积，以及 `gamma_GB = (E_bicrystal - E_bulk,top - E_bulk,bottom - gamma_s,top*A - gamma_s,bottom*A) / A`，以 J/m² 和 eV/Å² 两套单位给出。bicrystal、四套 reference 的 log/structure/trajectory、 `gb_energy_summary.csv` 和完整 `gb_energy.json` 都会注册；紧凑结果进入 Project Memory 的 run，成功后还会确定性写入 `run:<run_id>:main_conclusion` 这一 `scientific_conclusion` fact，不新增模型可直接调用的 memory 写工具。

### GB shear

`write_gb_shear_config()` 可以接收 completed minimization 或 completed shear 的显式 `source_run_id`，也可以解析最新完成的 minimization。它通过索引 artifact role 解析最终 LMP 和两个 boundary ID；source 路径、element/mass、boundary condition、pair style 和 potential file 全部从父运行继承，不作为 shear writer 的可修改参数。Loading、每步 minimization 和 output 使用具名类型参数，不再提供自由格式的 `overrides_json`。校验后的 canonical JSON 既作为 primary `input.json` 保存，也镜像进可搜索的 run parameters。

`run_gb_shear()` 根据 atom ID 恢复上下 boundary group，并对每个目标应变点执行配置的 `cg` 或 `fire` minimization。`load_type="displacement"` 会固定上下 boundary 原子的 x、y 坐标，并在每个增量步只直接移动 top group。`load_type="spring_force"` 则逐原子固定 boundary 的 y 坐标，并在 x 方向施加上下 spring tether。实际剪切应变使用每一步当前 top/bottom y-COM 间距。

Material element 和 mass 是从父运行继承的必填 canonical 字段；`read_data` 已恢复质量，因此 mass 在 shear config 中作为 provenance 保留。启动 LAMMPS 前，managed runner 会重新解析该父运行，并拒绝 source fingerprint、artifact 名称、material、boundary condition 或 potential 的任何变化。

两个 strain increment 都是非负 magnitude。`loading_direction` 接受 `x` 或 `-x`，用于选择第一段加载方向。`steps_per_half_cycle` 为 `null` 时整次计算保持单向加载；将其设为正整数后，driver 会每隔指定步数反向。`half_cycles` 声明预期的半循环数量；当总步数小于 `steps_per_half_cycle*half_cycles` 时，writer 返回 warning，但不会自动增加步数。completed shear 可以从最终 `gb_sheared.lmp` 续算；子运行会复制该 LMP 和两份 ID 文件，并把本次相对应变加到父运行累计应变上。读取 `input.json` 和 `shear_run.json` 时会校验 schema，遇到缺失或不兼容的 schema version 会直接报错，不自动迁移这些文件。

复制后的 `top_boundary_ids` 和 `bottom_boundary_ids` 会直接注册到 shear run；后续 NEB 配置可以使用这些 role，不必再回溯 minimization。

未提供参数时，writer 的所有值均来自 `src/domains/lammps/common/defaults.py` 中的扁平 `SHEAR_*` 常量；tool schema 和运行时配置也导入同一组常量。弹簧加载时，driver 会将配置的 per-atom 值乘以 boundary atom count，得到传给 LAMMPS 的总 spring constant。位移加载时，canonical `input.json` 中两个 spring 字段都写为 `null`。

运行会写出 `inputs/source.lmp`、复制的 boundary-ID 文件、`config_used.json`、`shear_curve.csv`、`structures/gb_shear_step_*.cfg`、`gb_sheared.cfg` 和 `gb_sheared.lmp`。曲线保存 step、increment stage、当前加载方向、从 1 开始的加载 half-cycle/full-cycle 编号、half-cycle 内步数、目标与实际剪切应变、当前 slab 间距、修正后的上下剪切应力以及六个修正后的应力张量分量。弹簧反力应力使用初始原子占据区域的 x-z 面积；LAMMPS 张量先由 pressure 转为 Cauchy stress，再乘以“初始 box 体积 / 初始原子占据区域体积”，以消除真空层造成的稀释。单步未达到力容差会累计进 run metadata，但不会中止后续加载。位移加载时，top shear stress 使用修正后的 `sigma_xy`，bottom shear stress 使用 `-sigma_xy`；六个应力张量分量仍正常输出。

`shear_run.json` 在每个已完成加载点之后原子化更新，包含 `running`/`completed`/`failed`/`cancelled` 状态、最后完成步、初始原子 bounds、参考面积/体积及应力修正因子、当前上下 COM、当前间距、修正后的上下剪切应力和六分量应力、保存所有已完成步骤及其加载方向/half-cycle/full-cycle 标识的 `points` 数组，以及将实际输出 CFG 的步骤映射到精确路径的 `frames` 数组。`dump_every` 只控制 CFG/frame 输出频率，不影响逐点历史。

Semantic run row 会持续更新最后完成的 step/strain/convergence、当前加载方向/cycle、当前间距、完整修正后的当前应力状态和运行中 peak summary。Monitor 跳过没有变化的 metadata，只把新发现的 frame 注册为永久 derived artifact。

终态结果索引 source run/fingerprint、结果完整性、修正后的 top shear stress 峰值及其 step/strain/direction/cycle、boundary-stress 来源、修正后的当前应力状态、COM、最终应变/direction/cycle 和收敛计数；这些数据直接来自 `shear_run.json`。供人查看的 `shear_curve.csv` 保留在 run 目录，但不注册进 Project Memory。Frame metadata、结构、日志和 `shear_run.json` 仍为永久 artifacts。

### GB NEB

`write_gb_neb_config(initial_structure_path, final_structure_path, ...)` 接收初态、终态 `.cfg`/`.lmp` 的精确项目相对路径或 artifact ID。端点可以来自 shear，也可以是单个或批量 `structure_minimize` 的结果。每次 NEB 仍明确选择两个构型作为初态和终态；可以从同一批量结果选两个，也可以来自不同的单个／批量运行。批量结果只是候选端点的来源，工具不把整批构型作为一个输入端点，不自动配对，也不表示一次计算多端点路径。结构文件本身不包含正在生效的 LAMMPS `fix` 命令。

`constraint_mode="none"` 表示所有原子自由，不需要边界 ID 文件。需要保留 shear 边界约束时使用 `constraint_mode="shear_boundaries"`，并同时提供 `top_boundary_ids_path` 和 `bottom_boundary_ids_path`。省略 mode 时，有 ID 输入则选择 `shear_boundaries`，没有则选择 `none`；显式 `none` 会拒绝 ID 输入，避免静默丢弃已给定的约束。

材料、质量、PBC、pair style 和势函数从精确端点对应的已注册模拟配置继承，也支持定位到批量最小化的具体 item。可选 `shear_config_path` 可以显式提供 shear 模拟配置；无来源记录的导入需给出已注册的 `potential_name`、`boundary` 和 `element`。两个端点的物理设置、 atom ID 与 cell 必须兼容；NEB 当前支持单元素。Writer 将文件大小和 SHA-256 固化进 canonical config，并将端点、原始帧来源和 `purpose` 写入项目记忆。

CFG 在 `run_gb_neb()` 的准备阶段内部确定性转换，无需预先单独调用转换工具。直接输入的 LMP 必须使用 `atom_style atomic`。

默认 partition topology 为 16 replicas × 每 replica 1 rank。`.env` 可通过 `GBCLAW_LAMMPS_NEB_REPLICAS` 和 `GBCLAW_LAMMPS_NEB_RANKS_PER_REPLICA` 覆盖，`write_gb_neb_config` 也接受 `replicas`/`ranks_per_replica`；两者乘积不得超过 `GBCLAW_LAMMPS_TOTAL_MPI_RANKS`（默认 18）。Driver 将完整 global communicator 传给 LAMMPS 并使用 `-partition`，从而保留 `fix neb` 所需的 universe 通信。在 `shear_boundaries` 模式下，top/bottom group 按 atom ID 重建，并在 `fix neb` 之后设置 `fix setforce 0 0 NULL`； `none` 不设置边界力约束。

完成后会注册各 replica 的 trajectory/final LMP、日志、供人查看的 `mep_profile.csv`，以及供 agent 读取的 `mep.json`。JSON 只包含完整 reaction-coordinate/energy profile 和 topology。能量为各 replica 最终构型的全精度势能（`energy_source: replica_evaluation`）；LAMMPS 打印的 NEB 表只有 8 位有效数字（10^4 eV 量级的体系约为 1 meV），只在两者不一致时使用（`neb_table`）。鞍点与势垒由 Analysis 独立推导，因此同一条路径可以正确保存零个、一个或多个鞍点。

## Analysis tools

分析使用已确定身份的模拟产物，并生成带有来源引用的新证据。已有工具能够回答问题时，优先使用它，可以让不同案例的应力下降判据、位移参考和 NEB 鞍点筛选保持一致。把图表解释为物理结论之前，请核对下文各方法的适用条件。

定义在 `src/domains/analysis/periodicity_tools.py`，以及 `src/domains/analysis/` 下 `stress_strain/`、`neb_mep/`、`rendering/` 各自的 `tool.py`。`src/domains/analysis/catalog.py` 将它们汇总为 `ANALYSIS_AGENT_TOOLS`，由 Analysis extension 声明为 Analysis worker 的 tool provider：

普通单条／多条曲线绘图使用 `plot_stress_strain_comparison`，drop／极值分析使用 `stress_strain_analysis`。`delegate_analysis(required_artifact_roles=...)` 可指定必需输出角色。委派层依据中央记忆注册表校验科学工具结果，并核对数据库索引、manifest 和实际文件。缺少产物或未恢复的工具失败会在结论记忆、任务状态中登记为 failed／partial；支持修正参数后重试和验证已有结果后复用。工具过程不进入返回给 orchestrator 的结论。

| Tool | 作用 |
| --- | --- |
| `analyze_gb_periodicity(...)` | 联合分析已完成、X 两侧有真空的 `gb_minimize` CFG 的坐标与弹性应变，返回摘要图、优先审查候选及备选项，保留完整证据，并比较构建 mismatch／尺寸供用户选择。 |
| `stress_strain_analysis(...)` | 对 `gb_shear` 历史并行执行应力跌幅阈值和局部极值检测，合并两种证据，汇总首个与后续重复跌落，映射结构帧并输出 JSON 和对比 PNG。 |
| `plot_stress_strain_comparison(...)` | 绘制 1–12 个 completed shear 历史；单元素 source 列表绘制单条曲线，支持标签、颜色、线型、marker、正负/绝对坐标、图例、标题和尺寸设置。 |
| `analyze_neb_mep(...)` | 检测 MEP 离散导数所有由正变负的位置（包括平坦鞍点平台），输出可复用的多鞍点 JSON 和 conclusion fact。 |
| `plot_neb_mep(...)` | 将 completed `gb_neb` 的已索引最小能量路径绘制为 reaction coordinate—energy difference 曲线，并开放曲线/鞍点样式、图例、标注、标题和坐标轴标签。 |
| `render_atomic_structure(...)` | 通过 OVITO 渲染已索引 CFG，可叠加参考帧位移箭头，并保存 PNG／JSON 和来源信息。 |
| `render_gb_plan_view(...)` | 在已索引 CFG 中估计沿 Y 法向的平面晶界，渲染两侧相邻原子层，可叠加相对于参考帧的位移箭头，并保存分层诊断和来源关系。 |

实现重点：

- 应力—应变分析通过已完成 shear 的 `source_run_id` 选择输入（running/failed 的来源只有显式 `allow_partial=true` 时才接受），直接读取已索引的 `run_metadata` artifact，不把完整逐点历史塞入模型上下文。
- 默认分析 `top_shear_stress_mpa` 的绝对值，同一份历史同时执行两种检测。 `threshold_detection`/`threshold_events` 使用 median/MAD 鲁棒阈值寻找相邻点的应力突降，并将连续超阈值突降合并；`minimum_drop_mpa` 可显式指定阈值。 `extrema_detection`/`extrema_events` 根据斜率符号变化识别局部极值：正转负是极大值，负转正是极小值。平坦平台作为一个极值处理，将每个峰与其后的谷配对，覆盖完整下降段，包括低于阈值的缓慢下降。
- 极值斜率沿加载先后顺序，用应力差除以实际应变增量的绝对值，因此反向加载仍保持相同的峰谷含义。应变增量为零或缺失时，使用应力差的符号并明确记录斜率不可用。 `extrema_tolerance_mpa` 默认为 `0.0`，保留所有符号反转，并合并应力相等的平台。设为正数时，仅在连续点的总应力范围不超过容差时将其合并为平台；许多微小增量的累计变化一旦超出该范围，仍会形成趋势。这种有界平台容差可以合并微小波动，但不是事件跌幅下限；工具不平滑曲线，也不额外按事件幅度过滤。
- 两种方法均不跨 loading half-cycle 或缺失 step 比较。所有有限的已完成点都会参与分析，不受 convergence flag 影响；未收敛只保留为 warning metadata，不会让曲线断开。
- `combined_events` 按时间顺序合并同一次下降的两种证据，保留 `both`、`extrema_only` 或 `threshold_only` 支持标签和来源事件索引。顶层 `events`/`event_count` 对应合并结果；两种原始方法各自保留事件列表与数量。数值相近但发生时间不同的峰谷仍是独立事件。曲线结尾尚未出现回升的下降段标记为 `post_minimum_confirmed=false`，不能把终点认定为已完成跌落或新的稳定状态。
- `curve_summary` 在每个 segment/half-cycle 内通过 `first_complete_event` 和 `subsequent_events` 分开描述首个完整跌落与后续跌落，保存跌幅分布、峰谷应力范围、首个跌幅与后续中位跌幅之比、重复间隔统计、代表事件 ID 和 `interpretation_notes`。如果开始时已有未确认的下降段，它会单独保留；通过 `first_complete_is_first_observed_event` 判断首个完整事件是否也是首个观测事件。因此首次大跌落和后续较小重复跌落都会保留，即使全局阈值漏掉了后者。这些是曲线的描述性证据；是否发生结构变化仍需要原子结构证据，不能仅凭应力曲线断言。
- `stress_strain_analysis.json` 保存列数组、三种检测结果、峰谷 step/strain/drop、谷值是否确认、曲线汇总及结构帧映射。`stress_strain_curve.png` 用三个面板对比阈值、极值和合并结果。极值和合并视图的事件后帧必须位于目标 step 当时或之后，且在同一 segment 内；没有符合条件的帧时返回不可用。原始阈值视图保留最近帧选择，可能选到目标之前的帧，此时不能将其用作 drop 后结构。近似后续帧保留 step 偏差，不能称为精确的谷值结构。两份文件以及 primary 的 `source_shear_run.json` 快照（`source_metadata_snapshot`）都作为永久 artifact 写入 `artifacts/analysis/stress_strain/{run_id}/`。工具返回值带有面向模型边界的 `model_view`：`stress_strain_analysis` 把每个合并事件列成一行（support、峰值 step/strain/stress、事件与结束 step、降幅、事件后应力与帧、是否确认谷值），并附检测参数、局部极值和曲线汇总；`plot_stress_strain_comparison` 列出每条曲线的统计（点数、加载方向、首/末/最小/最大应变和应力及对应 step）。worker 直接从该视图汇报，只在缺少某个字段时才读 JSON artifact。超过 15,000 字符时，视图先把 `local_extrema` 替换为 `{"omitted": true, "read_from": "stress_strain_data"}`，再截去尾部行并以一个 `{"omitted_events", "total_events", "read_from"}` 标记收尾（对比视图用同一标记表示省略的曲线）；这些标记是内容被省略的唯一迹象。
- analysis run 完成后，工具会在 `run:<analysis_run_id>:main_conclusion` 确定性写入且只写入一条 `scientific_conclusion` fact。该紧凑 fact 保存 source shear run、检测参数、三种结果的数量和事件证据、合并支持标签、曲线汇总、峰谷和 frame step；完整数组仍只保存在 JSON artifact。因此结论可直接搜索，不依赖 turn 结束后的模型提取。Memory handoff 只提供有长度限制的预览；若预览缺少某种方法或首个／后续事件信息，应读取精确 conclusion 或已索引的分析 JSON，不能将被截断理解为没有后续小跌落。
- `analyze_neb_mep` 与 `plot_neb_mep` 都要求 completed `gb_neb` source 恰好有一个已索引 `mep_json`，并使用相同的有限差分定义：内部导数每个由正变负的位置都是 saddle candidate，夹在正负导数之间的零导数平台算一个 candidate。分析写入 `neb_mep_analysis.json`；绘图在 `artifacts/analysis/neb/{run_id}/` 写入 `neb_mep_plot.json` 与 `neb_mep.png`。结果保留 source fingerprint、导数容差、完整鞍点列表、最高鞍点、端点能差和各鞍点势垒。
- 通用结构渲染的输入由 `source_run_id` 和 artifact role 选择；输出写入 `artifacts/analysis/render/{run_id}/render.png` 和 `render.json`。
- 当 `intermediate_structure` role 对应多个 CFG 时，`render_atomic_structure` 必须接收精确 `source_step` 或 `source_artifact_id`；批量最小化中的某个 CFG 使用 artifact ID。如果 `dump_every` 没有保存事件 step，近似 frame 只能用于说明附近结构；谷后结构仅允许映射到同段中目标 step 当时或之后的帧，不能使用谷前帧替代。
- `analysis_mode` 支持 `CNA`、`DXA`、`none`。
- 默认 CNA，默认不隐藏任何 atom type。
- OVITO 渲染通过 `sys.executable src/domains/analysis/rendering/ovito_rendering.py ...` 子进程完成，避免 Qt/OVITO 在 API 进程内崩溃。
- 成功后创建 linked semantic run，并把 PNG 和 JSON 注册为永久 derived provenance。

### Relax 后晶界的周期候选

`analyze_gb_periodicity(source_run_id=..., purpose=...)` 使用已完成的 `gb_minimize` run、已索引的 `minimized_cfg`，以及显示 `x_pbc=false` 和正数 X 真空留白的规范构建配置。若该 role 有多个 artifact，需用精确 `source_artifact_id` 消除歧义。当前支持正交盒子内单个近似平面的 Y 法向晶界。分析会根据实际占据范围核对几何并排除外表面原子，不能仅凭配置中的真空量或 CFG PBC 标记断言存在可分析的内部晶界。

不需要提供上、下晶粒的周期比例。PTM bulk 取向与实际最短晶格平移给出低阶匹配候选，分层坐标 FFT、完整三维坐标匹配和原子行结构单元与有符号弹性应变 FFT、局部应变间距共同用于评估短尺度及可能的长尺度。分别从两侧远场晶体识别 FCC/BCC 类型及有效晶格常数；弹性应变只统计晶体结构识别有效的原子，排除非晶体晶界核心。报告的体积应变采用 OVITO 定义 `tr(Eulerian-Almansi strain)/3`，不等同于体积相对变化量。来源取向用于锚定晶粒名称和有符号晶向；构建重复数只作为背景，不能作为预设答案。候选保留支持／反对依据、有限窗口限制、倍周期关系和局部间距变化。小构型可能只能报告长尺度证据不足；单个 Fourier 峰不能证明唯一周期，也不能确定 secondary dislocation 的 Burgers vector。

坐标与应变频谱保留实际弱峰及明确的审查失败原因，不再按最强峰的固定百分比截断。应变审查结合绝对应变幅度、局部突出程度、窗函数敏感性、原子重新分箱和平滑稳定性。近场和远场应变曲线共用物理功率单位；远场也有相同峰只作为解释信息，不自动否决该峰。彩色 S/L 线表示独立识别的结构单元、局部间距或坐标候选，不代表指定的 FFT 峰。当原子行结构单元关联到附近晶格候选时，分别保留实测原子行长度、坐标平移长度、差异及其他可行关联，保留完整 `a[hkl]` 的非整数重复数。

最终纯 X 晶格重复候选必须通过两侧晶粒的平移闭合检查。对于 `c*a[uvw]`，先将方向约为互质整数：FCC 在 `u+v+w` 为偶数时允许系数为 0.5 的倍数，否则只能为整数；BCC 则仅在三个指标全为奇数时允许 0.5 的倍数，否则只能为整数。零算偶数。未约分方向的系数步长还需除以指标的最大公约数。这是实空间平移规则，不能使用衍射消光条件替代。两侧合法名义长度还必须分别与测得的坐标周期相差不超过 0.3 个最近邻距离；该容差不会放宽系数的合法性。

`lattice_filter` 汇总通过、未通过和未解析的数量；默认首选／备选和测量关联的 PBC 尺寸比较仅使用通过检查的候选。原始坐标假设及拒绝原因仍保存在 JSON/CSV 和摘要图中。FFT 波长、局部应变间距和原子行结构单元保留原始测量值， `pure_x_repeat_eligible=false` 表示它们不能直接作为构建系数。平移闭合只是必要条件，还需检查 mismatch、完整晶界匹配及化学有序；斜向平移需单独做完整矢量检查。

独立的 `gb_periodicity` run 在 `artifacts/analysis/gb_periodicity/{run_id}/` 保留以下永久 artifact：

| 文件 | Artifact role | 内容 |
| --- | --- | --- |
| `multiscale_review/multiscale_summary.png` | `gb_periodicity_overview` | 唯一摘要：结构／应变频谱、空间特征、短长尺度、全部坐标候选及概念说明。 |
| `period_candidates.json` | `gb_periodicity_data` | 完整候选、参数、证据、限制和来源元数据。 |
| `agent_summary.json` | `gb_periodicity_summary` | 紧凑候选证据及尚未选择的状态。 |
| `multiscale_review/multiscale_review.json` | `gb_periodicity_multiscale_data` | 联合证据、审查排序、结构单元关联及尺度关系。 |
| `multiscale_review/analysis_status.json` | `gb_periodicity_multiscale_status` | 联合分析状态、紧凑候选交接及证据不可用的原因。 |
| `multiscale_review/all_candidates.csv` | `gb_periodicity_candidate_table` | 全部坐标候选、晶格重复数、匹配误差及频谱证据。 |
| `multiscale_review/ALL_CANDIDATES.md` | `gb_periodicity_candidate_notes` | 可直接阅读的完整候选对比。 |
| `elastic/elastic_periodicity.json` | `gb_periodicity_elastic_data` | 完整应变曲线／频谱、晶体参考、峰审查和坐标对照。 |
| `elastic/elastic_atoms.npz` | `gb_periodicity_elastic_atoms` | 每个原子的应变、有效性、晶粒及参考区 mask。 |
| `elastic/review_index.json` | `gb_periodicity_elastic_index` | 当前摘要与详细证据的紧凑索引。 |
| `elastic/structure_fft_peaks.csv` | `gb_periodicity_structure_fft_peaks` | 实际结构频谱峰、远场比较和审查结果。 |
| `elastic/strain_fft_peaks.csv` | `gb_periodicity_strain_fft_peaks` | 实际有符号应变峰、幅度、数值稳定性及远场比较。 |
| `elastic/strain_fft_review.json` | `gb_periodicity_strain_fft_review` | 应变审查规则、各层峰数量及来源指纹。 |
| `local_strain/local_strain_events.json`、`.png` | `gb_periodicity_local_strain_data`、`gb_periodicity_local_strain_plot` | 空间应变极值、局部间距及诊断图。 |
| `coordinate_rows/coordinate_rows.json`、`.png` | `gb_periodicity_coordinate_rows_data`、`gb_periodicity_coordinate_rows_plot` | 原子行结构单元、中断位置及诊断图。若内部没有达到 30 个原子的原子行则不画图，JSON 记录 `status: no_qualifying_rows` 及原因。 |
| `pbc_size_comparison.json` | `pbc_size_comparison` | 构建 mismatch、完整重复数组、物理尺寸和警告。 |
| `source_metadata.json` | `source_metadata_snapshot` | 精确源 artifact 和规范构建上下文。 |
| `analysis_policy.json` | `analysis_policy` | 传给子进程的分析策略。 |
| `boundary.json` | `gb_periodicity_boundary` | 已解析的晶界位置、外表面排除范围及选择依据。 |
| `boundary_selection.npz` | `gb_periodicity_selection` | 输入构型的内部、缺陷及晶界邻域选择 mask。 |
| `crystal_mapping.json` | `gb_periodicity_crystal_mapping` | PTM 晶粒取向及坐标平移到晶格周期的映射。 |
| `candidate_curves.npz` | `gb_periodicity_curves` | 完整坐标匹配曲线及数值证据。 |
| `modulation_profiles.json` | `gb_periodicity_profiles` | 坐标调制曲线及局部间距证据。 |
| `ptm_cache/ptm_atoms.npz` | `gb_periodicity_ptm` | 每个原子的 PTM 结构、取向和坐标。 |
| `ptm_cache/ptm_metadata.json` | `gb_periodicity_ptm_metadata` | PTM 输入指纹及分析元数据。 |
| `candidate_evidence.png` | `gb_periodicity_candidates` | 候选尺度及相互竞争的证据。 |
| `periodicity_diagnostics.png` | `gb_periodicity_diagnostics` | 晶界选择及周期诊断。 |
| `in_plane_periodicity.png` | `gb_periodicity_in_plane` | 保留分层的面内证据。 |
| `long_period_modulation.png` | `gb_periodicity_modulation` | 长尺度信号，也保留未解析／窗口不足的结果。 |
| `analysis.log` | `log` | 子进程 stdout/stderr（debug）。 |

摘要图之外，坐标诊断图及联合分析的全部详细输出仍完整保留。PTM 结构面板保持 X/Y 物理尺度 1:1，避免为填满面板而压缩构型。摘要图标注可能的周期及未解析的证据，不替用户确定唯一周期。

只有对应分析阶段确实产出的文件才会注册。不支持或无法解析的几何保留可用诊断证据及结果 JSON，并不意味着总有全部 PNG、已解析的晶界或晶格映射。实际输出以返回的 artifact manifest 为准。`multiscale_analysis_status` 记录联合证据是否可用；若几何、弹性参考或计算错误导致完整联合流程无法完成，run 标记为失败并保留原因及已有诊断。不生成坐标摘要作为替代输出。联合分析不可用、或完成但没有更长周期候选时，结果会提醒：可能是体系沿晶界方向（x）不够长，未能显示更长的周期。

紧凑工具结果通过 `summary_image_path` 返回摘要图的项目相对路径，`image_paths` 默认只包含已验证存在的摘要图。其他图片保留在 manifest 和落盘的 `diagnostic_image_paths` 中，紧凑回复不列出完整图库。Analysis 向 orchestrator 交接、以及 orchestrator 向用户回复时，优先展示摘要图和简短的候选结论。

`review_priority_candidate` 给出按公开规则最值得审查的候选，`alternative_candidates` 保留至多四个备选项；该优先级可能未解析，不是概率、唯一物理周期或用户的最终选择。 `short_motif_candidates` 与 `longer_candidates` 区分不同尺度，`total_candidate_count` 给出全部保留候选的数量。

通过 `detailed_report_path`、`candidate_table_path`、artifact manifest 及 conclusion lookup key 按需查阅完整证据。正常回复直接使用紧凑结论；只有具体问题无法从摘要回答时才读取相应详细 artifact。额外诊断图仅在用户要求时展示。 `presentation_policy=summary_only` 同样适用于失败结果：`image_paths=[]` 不展示图片，不会引用未生成的摘要图，也不自动原样重试确定性错误。

当长波分析下限超过观测窗口允许的上限时，摘要面板明确标注无法解析该波段，同时保留可用的完整频谱。空数据或非正功率不会被强制放到对数轴上而导致整张摘要生成失败。

数值曲线、频谱、原子应变、mask、局部事件、原子行与峰审查保存在索引的证据文件内，不塞入模型上下文。PNG 路径复用对话现有的图片显示流程。Run 及确定性 conclusion fact 保留精确来源关系和分析目的，便于记忆检索复用。

Orchestrator 将候选与 `pbc_size_comparison` 一起汇总。尺寸对比使用来源的材料、实际取向、原有 Y/Z 尺寸和 `x_pbc=true`，保留非整数 X 重复数。坐标匹配的 mismatch 定义为 `abs(L_top-L_bottom)/mean(L_top,L_bottom)`；构建估算器则使用 `abs(L_top-L_bottom)/max(L_top,L_bottom)`，对比百分比时应说明分母。若需要补充精确尺寸，再调用只读 `estimate_gb_box_size`，不重跑最小化。 `selected_candidate_id` 保持 null，`requires_user_decision` 保持 true：分析及默认后续流程只提供候选，不替用户选择周期盒子、不施加 strain，也不授权新的 simulation。

### 通用结构位移渲染

`render_atomic_structure` 保留所选 `CNA`、`DXA` 或 `none` 模式及其原子显示设置。默认不启用位移箭头。提供 `reference_run_id` 后启用，并通过 `reference_artifact_role="minimized_cfg"` 选择参考 role；有多个文件时使用精确 `reference_step` 或 `reference_artifact_id`。当前结构和参考结构都来自已索引 CFG，当前批量结果可通过 `source_artifact_id` 精确选择。两个渲染工具都会把 PNG 裁剪到非透明像素范围，四周保留 50 像素空白；JSON 的 camera 记录保留渲染尺寸、裁剪框和最终尺寸。

两个完整结构必须具有完全相同且各自唯一的整数 atom ID，匹配先于 `hide_structure_types` 执行。OVITO 计算当前位置减参考位置，关闭 affine mapping，保留完整三维位移，包括刚体平移和应变。默认 `minimum_image_convention=True`；若两帧之间位移超过半个周期盒长，使用展开坐标并关闭该设置。可选 `pbc` 的 X/Y/Z 布尔值同时应用于两个输入，否则采用导入的周期性。

通用 top view 沿 -Z 观察。红色箭头采用 centered alignment、`arrow_width=0.1` 和 `arrow_offset_z=1.0`。默认 `arrow_scale_mode="physical_capped"` 不放大小位移；当显示集合中的最长三维矢量超过 `arrow_max_length=2.0` 时统一缩小。允许设置更短的正上限，禁止超过 2。可选 `median_capped` 先将非零 XY 投影中位数映射到 `arrow_target_length=1.5`，再应用相同三维上限。两个模式均保留长度比例和真实位移，纯法向位移也受限制。晶界 plan view 使用 XZ 投影和 +Y 偏移。

`render` run 将 `render.png` 注册为 `render` role，`render.json` 注册为 `render_data`。 JSON 保留两个输入的 fingerprint、可追溯的原始帧来源、`purpose`、分析模式、显示选择、可选的实际位移向量／统计及箭头设置。完成后写入可搜索的 `run:<render_run_id>:main_conclusion` fact；紧凑结果和 fact 不包含逐原子数组。所有向量和显示长度使用输入长度单位，GBclaw 的 metal-unit 构型为 Å；单位未知的外部文件仅保证最多 2 个输入单位。`displacement.render` 独立记录缩放政策、物理/显示最大长度、是否触发上限和算法版本，不修改物理数组。CLI 同样支持 `--arrow-scale-mode` 与 `--arrow-max-length`。

独立渲染使用 `GBclaw` conda 环境：

```bash
conda run --no-capture-output -n GBclaw python src/domains/analysis/rendering/ovito_rendering.py \
  path/to/current.cfg --reference path/to/reference.cfg \
  -o render/current/render.png
```

不提供 `--reference` 时正常渲染，不显示位移箭头。可选参数包括 `--arrow-width`、 `--arrow-offset-z`、`--arrow-target-length`、`--no-minimum-image` 和 `--pbc 'p f p'`。 CLI 在 PNG 旁写入 JSON；agent adapter 负责项目索引和来源记忆。

### 晶界 plan view

`render_gb_plan_view` 接收 `source_run_id`、`source_artifact_role="minimized_cfg"`，可选的 `source_step` 或精确 `source_artifact_id`，以及 `gb_y`、`slab_thickness`、`pbc`、 `image_width=3200`、`view_width` 和 `purpose`。Shear 帧通过 `intermediate_structure` 和已索引的精确 step 选择；批量最小化中的某个 CFG 通过 artifact ID 选择。若多个文件同时匹配，工具会要求明确选择，不会默认取第一帧或第一个批量结果。

工具先在完整结构上执行 adaptive CNA，再进行切片。非 FCC/BCC 原子作为晶界候选依据；定位时排除 Y 方向最外侧区域，仅在检测到横向真空的方向裁掉侧面区域。内部密集缺陷带和可分辨的原子层间隙用于估计晶界位置。先在定位内区确定两侧相邻原子层，再以它们与更外一层的中间平面为界，将原子层选择扩展到完整横向范围。默认对称 slab 会适当加厚，以保留归入这两层的自由边缘畸变原子；仅在能排除所有相邻外层原子时自动采用该厚度，否则要求手动指定厚度。自由边缘处的这种几何归层仍是近似。JSON 记录定位裁剪范围、候选带、内区及完整横向原子层的边界和数量，以及两侧分别估计的层间距。

正交视角沿 `(0,-1,0)`，up direction 为 `(0,0,-1)`，对应 roll 180°：X 向右、Z 向下。图像中心取模拟盒子的 X/Z 中心。较低 Y 一侧为黑色，较高 Y 一侧为乳白色；颜色表示几何两侧，不是 CNA 类型，也不代表已经独立确定晶粒归属。默认显示整个 slab，较宽结构会额外生成居中局部图。`view_width` 可指定以输入长度单位表示的居中视野宽度。默认 `atom_scale=0.7`，通过 OVITO 原生粒子显示设置，将导入的原子半径显示为 70%。PNG 输出采用透明背景。

自动定位适用于轴对齐正交盒子中的单个、近似平面且法向沿 Y 的晶界。存在多个候选带、无法分辨原子层，或对称 slab 无法只包含两侧各一层时，需要显式给出 `gb_y` 和／或 `slab_thickness`。手动厚度可能包含不完整或多层原子，结果会标注这一点。坐标采用 OVITO 导入后的笛卡尔原点和输入长度单位。`pbc` 可选，使用 X/Y/Z 三个布尔值；默认保留 OVITO 导入的周期性。真空识别不会修改周期性，也不能反推出原来的 LAMMPS boundary 设置。

叠加位移时，传入 `reference_run_id`、`reference_artifact_role="minimized_cfg"`，以及需要明确选择时的精确 `reference_step` 或 `reference_artifact_id`。两个 CFG 必须有完全相同且各自唯一的整数 atom ID；工具按 ID 匹配，不会按文件行序匹配。晶界定位和 slab 仍由当前帧的 CNA 和原子层间隙决定。OVITO 原生位移 modifier 计算当前位置减参考位置，关闭 affine cell mapping，保留完整三维分量、刚体平移和应变。默认 `minimum_image_convention=True`；若两帧之间位移可能超过半个周期盒长，应使用展开坐标并关闭该设置。显式 `pbc` 同时应用于两个输入。

红色原生 OVITO 位移箭头以当前原子为中心，默认沿 Y 偏移 `arrow_offset_y=1.0`，宽度 `arrow_width=0.1`。这些仅影响显示，不移动原子，也不改变实际位移。所有箭头共用一个缩放因子，采用与通用渲染相同的 `physical_capped`/`median_capped` 策略， `arrow_max_length=2.0` 是 slab 内所有三维显示矢量的硬上限。仅可选 median 模式使用 `arrow_target_length=1.5` 和 XZ 投影。纯 Y 位移沿视线方向，仍受三维上限限制。图中箭头长度属于显示长度，不能直接当作实际位移；输入为 GBclaw metal-unit 构型时单位为 Å。

`gb_plan_view.json` 的 `displacement.summary` 保存完整结构及当前 slab 的统计， `displacement.selected` 保存 slab 内的 atom ID、未缩放三维位移和模长， `displacement.render` 保存统一缩放因子及箭头设置，同时记录参考帧 fingerprint、cell 和周期性。工具紧凑返回值与 memory fact 不包含逐原子数组。

Agent 每次调用创建 `render_kind="gb_plan_view"` 的 `render` run，输出目录为 `artifacts/analysis/render/{run_id}/`，并注册以下永久 derived artifacts：

| 文件 | Artifact role | 内容 |
| --- | --- | --- |
| `plan_view.png` | `gb_plan_view` | 完整 slab 视图，或指定的居中视野。 |
| `plan_view_detail.png` | `gb_plan_view_detail` | 可选，同一 slab 的居中局部图。 |
| `y_profile.png` | `gb_plan_view_profile` | 完整及局部 CNA／原子层分布，并标出切片范围。 |
| `gb_plan_view.json` | `gb_plan_view_data` | 定位依据、原子层、slab、来源、相机、可选位移与 warnings。 |

Run 和 artifacts 保留所选 CFG 的 artifact ID、fingerprint、可追溯的原始帧来源及 `purpose`。位移图还通过 `displacement_reference` 和 `reference_origin_frame` source 关系保留精确参考 artifact 及其原始帧来源。完成后在 `run:<render_run_id>:main_conclusion` 写入可搜索的 conclusion fact，完整证据仍以 JSON 为准。自动定位有歧义时，失败 run 保留诊断 JSON，供 agent 检查原因。

独立命令使用已安装 OVITO 的 `GBclaw` conda 环境：

```bash
conda run --no-capture-output -n GBclaw python src/domains/analysis/rendering/gb_plan_view.py \
  path/to/structure.cfg --output-dir plan_view/structure
```

当前帧与参考帧的对比例子：

```bash
conda run --no-capture-output -n GBclaw python src/domains/analysis/rendering/gb_plan_view.py \
  path/to/current.cfg --reference path/to/reference.cfg \
  --output-dir plan_view/current
```

CLI 同样支持 `--gb-y`、`--slab-thickness`、`--pbc 'p f p'`、`--image-width` 和 `--view-width`，以及 `--atom-scale`、`--arrow-width`、`--arrow-offset-y`、 `--arrow-target-length`、`--arrow-scale-mode`、`--arrow-max-length` 和 `--no-minimum-image`。直接调用 CLI 只写文件，不创建项目记忆； agent adapter 负责索引来源和记忆。 OVITO／Qt 的执行与 agent runtime 分进程隔离。

## Project memory 与 artifact 工具

先用项目摘要了解研究进展，再按当前问题取回证据。搜索用于寻找候选记录，精确查询用于确认相关 run 或 fact，artifact 工具用于定位支撑文件。摘要中没有某个细节，不代表对应结果已被删除。摘要归档、来源追溯和保存规则见[项目记忆](project-memory.md)。

`query_project_memory` 返回结构化 semantic records，每次选择一个 entity：

| Entity | Filters |
| --- | --- |
| `runs` | `workflow`、`status`、`source_fingerprint`、`source_run_id`、`source_relation`、`run_id`、`limit` |
| `facts` | `fact_kind`、`lookup_key`、`source_type`、`include_retracted`、`limit` |
| `summary` | 无 filter |
| `summary_archive` | 可选的查询文本、读取完整快照的 `archive_id`、`limit` |

Run 与 fact filter 不可混用。Chat history、reasoning 和 trace 不会通过此工具暴露。

Run/fact 每次最多返回 `limit` 条记录（上限 100），响应包含 `has_more`、`next_cursor`、 `source_revision`。保持筛选条件并传 `cursor=next_cursor` 续页；并发语义变更会返回 `query_snapshot_changed`，此时重新查询。Summary 同时返回新鲜度、提取和发布健康状态。

精确查询默认每页 12,000 字符、最多 24,000 字符，通过 `offset_chars` 和 `max_chars` 控制。大结果返回无损的 `serialized_json_page`、`next_offset_chars` 和 `full_output` 快照引用。可通过 `read_tool_output` 稳定地续读快照，或用 `json_pointer` 只读取需要的字段。直接通过查询工具续读非零 `offset_chars` 时必须附带返回的 `snapshot_id`，保证读取原快照。

每个 run 结果都包含有序 `sources` 列表。`source_run_id` 会匹配任意 normalized source，不再只匹配旧的单值 shortcut；`source_relation` 可进一步限定反向关系，例如 `comparison_curve` 或 `neb_profile`。

Workflow 筛选值由中央注册表生成并列在工具 schema 中。周期性分析使用 `workflow="gb_periodicity"`，`analyze_gb_periodicity` 是执行工具名。已注册工具名和显式兼容名会解析为正式筛选值，不改变存储 workflow。未知名称会返回合法值列表，不代表分析已启动。

`search_project_memory(query, entity_types=None, limit=10, offset=0, include_noncompleted_runs=False, include_task_states=False, source_type=None)` 搜索所有已索引 fact value、run parameters/progress/results，以及 artifact path/metadata。它用于 working summary 信息不全或尚不知道 stable key 的场景。结果只包含有界 title/snippet、entity type/ID、时间、 rank 和稳定 `reference`，并通过 `has_more`、`next_offset` 分页。重要科研细节必须再通过精确 memory query 或 artifact 工具确认。`entity_types` 支持 `fact`、`run`、`artifact`，agent-facing `limit` 最大为 25。默认排除仅描述执行进度的 `task_state`；显式审计可设 `include_task_states=true`。running、failed 和 cancelled 的 run（及其 artifact/派生 fact）默认排除，除非 `include_noncompleted_runs=true`；`source_type` 按来源筛选 fact，并隐含 `entity_types=["fact"]`。连续性查询使用 `get_task_state`，科学执行/复用必须核对真实 run。自身 running 游标不代表后台分析任务。

对 facts 而言，`fact_kind` 是持久化层支持的固定类别，`lookup_key` 是某条事实的具体身份。未知 filter 名会被明确拒绝；因此空结果表示没有匹配的已持久化事实，而不是走了 mock 或 fallback 查询。

科研文件通过稳定 run ID 和 manifest role 访问：

| Tool | 作用 |
| --- | --- |
| `list_run_artifacts(run_id, role=None, offset=0, limit=20, offset_chars=0, max_chars=12000, snapshot_id=None)` | 分页列出经 manifest 校验的 artifact，每页最多 100 条（`next_offset`）；较大的列表返回无损字符分页和 `full_output` 快照，用 `snapshot_id` 续读。 |
| `read_run_artifact(run_id, role, artifact_id=None, max_chars=12000, offset_chars=0)` | 读取唯一匹配的可读 text artifact，单次最多 24,000 字符；较大文件返回下一 offset；原子结构和轨迹会被拒绝。 |
| `read_tool_output(output_id, offset_chars=0, max_chars=12000, json_pointer=None)` | 按需读取归档的科学工具输出，可先选择 JSON 字段再分页；不提供聊天历史或 delegate trace，不会重跑原工具。 |
| `read_agent_conclusion(conclusion_id, offset_chars=0, max_chars=12000, section="report")` | 分页读取经过哈希校验的完整结论；`section="metadata"` 读取完整任务目的、结果状态和来源信息。 |
| `get_task_state(lookup_key=None, thread_id=None, offset_chars=0, max_chars=12000, snapshot_id=None)` | 默认读取当前 thread，也可使用精确旧调用引用；执行状态不代表科学成功或用户批准。较大的结果返回 `full_output` 引用；用 `snapshot_id=full_output.output_id` 加 `offset_chars=next_offset_chars` 续读，或用 `read_tool_output` 读取该快照。 |
| `report_artifact_usage(workflow=None)` | 按 provenance 报告 artifact bytes。 |

这些工具会拒绝路径逃逸和 index/manifest 不一致，也不提供删除操作；primary 与 derived artifact 都是永久科研记录。 `.lmp`、`.cfg`、`.lammpstrj` 等文本编码的机器 artifact 会通过稳定的 run ID 和 role 传给 LAMMPS 或 analysis 工具，而不会复制进语言模型上下文。

## Web search tool

`agents/orchestrator/web_search.py` 使用 OpenAI Responses API 的 `web_search` tool。

- 只有 `core.settings.web_search.is_web_search_available()` 返回 true 时 orchestrator 才会注入 `search_web`。
- 支持 `allowed_domains`、`blocked_domains`、`max_sources`。
- 返回正文和 citations。
- prompt 明确要求 web search 只用于当前外部事实，不替代 project memory、本地 artifact、CSL 或 LAMMPS 输出。

## Potentials catalog

`domains/lammps/common/potentials.py` 从 `GBCLAW_POTENTIALS_DIR` 读取 `potentials.json`（默认目录是 `data/potentials`）：

- `list_potential_records()` 用于展示。
- `resolve_potential()` 根据 `element`、`lattice` 和 metadata 中的 defaults/hints 选一个记录。
- catalog 中的相对文件路径严格从 metadata 所在目录解析；显式自定义相对路径则从仓库根目录解析。
