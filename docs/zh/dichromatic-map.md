# DichromaticMap 交互选胞

晶粒双色图（dichromatic map）叠加显示两个不同取向晶粒的格点，帮助研究者检查近似重合位置，并选择需要进一步分析的投影胞。在 GBclaw 中，Structure agent 准备候选点，研究者选择四个顶点，后端再计算对齐所选胞需要的应变。

本页说明交互过程及其结果的含义。它与晶体学筛选、后续模拟的关系见[架构与设计逻辑](architecture.md)及[Structure 工具参考](tools.md#晶粒双色图)。

## 设计逻辑

Near-CSL 研究可能有多个合理候选胞，尺寸和所需应变也不同。让选胞过程可视、可确认，研究者才能根据具体科学问题决定分析哪一种几何。Agent 准备候选并报告测量，确认步骤则保存研究者实际选择的胞。

网页与后端传递数值坐标和稳定候选 ID，因此缩放视图、恢复会话或事后复查时，都能保留准确选点。PNG 方便查看结果，可复现的几何信息则由数值选点和保存的变换承载。

准备候选、确认选点和施加应变分别具有独立状态与记录。因此，打开选点窗口不会被误认为胞计算已完成，刷新页面也不会悄悄启动计算。这延续了整体架构中工作提议、研究者决定和执行记录的职责划分。

## 可以怎样提出需求

如果已有一个已完成、尚未施加应变的 map run，可以这样要求：

> 打开这个晶粒双色图的选胞窗口，让我选择 near-CSL 胞。确认四个顶点后，请报告拟合应变、残差和候选边界向量。

新建图时，说明晶格、倾转轴、转角、晶格常数，以及希望分析的问题。Agent 可以先查询支持的选项，再准备窗口。下文介绍准备完成后的操作流程。

## 使用过程

1. 告诉 agent 准备初始 dichromatic／near-CSL 设置或打开选点窗口。初始设置默认进入交互流程；仅在明确要求静态图片时导出 PNG。准备窗口不会代替用户选点或施加应变。 `prepare_dichromatic_cell_selection` 保存项目内的请求并返回 `interaction` 引用，预填晶格、实际带符号旋转轴和转角、a0、层、near-pair cutoff、视野与用途。若已有未施加应变的 dichromatic-map run，省略 `parameters`、传入该 run 的 `source_run_id` 即可复用保存的设置并保留来源，不必重新导出图片。
2. 悬浮窗自动打开，参数、晶轴对应的角度预设／范围和层信息均已填好。拖动或缩放时，视野接口返回新区域的原子坐标。选点期间物理几何保持固定。
3. 沿预期胞的周边顺序，点击同一轴向层中的四个高亮配对位置。网页为点编号，支持撤销和清空。原子标记尺寸与形状使用默认值，不额外暴露外观控件。
4. 点击 **Confirm cell and continue**。网页提交四个候选 ID 和当前 revision；后端校验这些点属于该请求、能够组成有效的胞，再保存用户选择。几何无效时显示错误，保留选点供用户修改。
5. 下一轮对话把请求 ID 交给 Structure。它先调用 `get_dichromatic_cell_selection`，对 `selected` 请求调用 `apply_dichromatic_cell_strain`，用保存的数值选点计算变换后的图、胞、应变/旋转及残差。已经 `applied` 的请求复用已有结果。

继续对话由用户点击确认按钮触发。如果当前对话仍在运行，网页会等它结束再继续。切换项目不会把请求或排队的续跑消息带到另一个项目。最小化保留当前窗口状态。刷新网页后，项目请求接口既恢复待选请求，也恢复已经确认、尚未施加应变的选择。恢复后的已选请求提供 **Continue with agent** 按钮；刷新网页本身不会启动或重试应变求解。

施加应变后，悬浮窗显示对齐后的数值图。后续平移或缩放使用保存的形变梯度 F 和平移量重新生成可见区域的原子，不会再次求解应变，也不会退回原来的未应变晶粒视图。 Tilt axis 菜单提供常用晶轴；选择 **Custom axis…** 可输入任意支持的 Miller 指数。切换晶轴后，角度预设和层列表随之刷新。

## 状态与科研含义

请求状态说明交互已经完成到哪一步：

| 状态 | 含义 |
| --- | --- |
| `pending` | 候选点已准备好，研究者尚未确认胞。 |
| `selected` | 有效的四顶点选择已确认并保存。 |
| `applying` | 正在计算所选胞的应变。 |
| `applied` | 应变结果已保存，可以复用。 |
| `cancelled` | 待选请求已取消。 |

尚未选择或已取消的请求不能施加应变；过期 revision 不能覆盖更新后的选择。相同设置再次申请已完成的应变计算会返回原结果，修改设置需新建请求，不静默覆盖旧结果。

准备阶段的 delegate 完成只代表候选点准备完毕，不代表用户已选择胞。属于项目计划的工作，只要选点或应变尚未完成，其 step 就保持 blocked；后续对话带回原 plan/step ID 继续执行。计算真正失败时仍登记为失败。

投影长度使用 a0，`lattice_constant` 指定与 Angstrom 的换算。近邻配对本身不能证明原始未应变结构是精确 CSL。显式施加应变是在给定应变和旋转限值内，对齐选定的二维投影胞；不表示已获得三维 primitive cell、弛豫晶界构型或晶界能。

## 应变后的边界向量

边界测量回答一个具体的几何问题：沿所选胞的每条边，来自两个晶粒、处于同一轴向层的格点，最短非零间距是多少？计算使用已保存的原子身份和变换，所以结果不依赖当前视野或可见层。

搜索覆盖用户所选四边形的四条完整有限边界，在**全部轴向层**中分别比较同层 G1/G2 原子，再保留每条边跨层的最小距离、全部并列配对及其所属层。原子必须恰好位于线段上，不会扩大成附近条带，也不会混合不同层的原子。零距离重合配对不参与最小值比较；若边上的两晶粒原子行完全重合，则返回的非零间距属于晶格平移，并明确标注没有失配证据。

`edge_analysis` 保存各层结果、每条边跨所有层的最小距离、配对数量、原子身份、所属层和 G1→G2 位移，以及原库 Measure vector 对应的双晶粒表示。`lattice_notation` 使用各晶粒**变形后的常规晶格基**； `current_components_a0` 使用极分解旋转后的正交晶体坐标系。简单分数必须满足记录的误差要求，否则保留 `≈`、数值分量和重构误差。这些是 secondary misfit dislocation 的候选 Burgers vector，几何最近距离本身不能确认实际位错，也不等价于完成 Burgers 回路测量。

PNG 和悬浮窗同时绘制多个编号向量（`E1.1`、`E1.2` 等），按边列出 G1/G2 晶格表示及 a0/Å 长度。箭头使用实际长度，只偏移标签；悬浮窗可以隐藏标注或折叠列表。拖动时直接使用已保存的测量结果，只改变显示投影。导出 PNG 的整张图片和数据视区均为正方形，围绕所选 cell 居中适配并留出边距；使用已保存的应变重新生成整个区域的原子，不拉伸或裁切原来的交互窗口，也不重新求解应变。 JSON 同时记录导出视区和原始视区。

对已有但尚无全部层边界测量的 applied 请求，可调用 `analyze_dichromatic_cell_edges(request_id)`，或 POST 对应的 `/edge-analysis` 接口。它直接使用已保存的 F 与平移，创建以原应变 run 为来源的分析 run，不重新求解或改写原记录。已有的全部层分析直接复用；只计算所选层的历史结果保留作来源证据，不会被当成全部层的最小值。全部向量和标注保存在 `dichromatic_map.json`；较大的 agent/记忆返回值明确标注为预览，并引用完整 artifact。精简的 `edge_vector_summary` 保留最小距离、达到最小值的层、双晶粒带符号的晶格表示、并列数量及科研限定，经 Structure delegate 传给最终回复的 agent，要求明确报告候选向量，不能只报告应变指标。超出计算数量或精度限制时显式报告 incomplete，不悄悄遗漏并列最短配对。

## 持久化与扩展位置

待选请求保存于当前项目的 `runtime/dichromatic_requests/`。迁移尚有未完成交互的项目时，应保留这个目录。用户确认后创建独立、已完成的 `dichromatic_map` run，保存 `dichromatic_cell_selection` artifact；施加应变再创建一个 run，以确认选点的 run 为来源，保存 PNG 和数值 JSON。

统一 registry 中的 `select_dichromatic_cell`、`apply_dichromatic_cell_strain` 与 `analyze_dichromatic_cell_edges` publisher 把请求 ID、用途、选点、科研结果及来源关系登记进记忆；`render_dichromatic_map` 发布静态导出。完整点坐标留在 artifacts。待选请求和只读预览不伪装成已完成科研结果；记忆发布失败沿用已有的恢复机制。

主要代码位置：

- `src/domains/structure/dichromatic/vectors.py`：完整有限边界的精确配对及双晶粒向量表示。
- `src/domains/structure/dichromatic/adapter.py`：坐标、稳定候选 ID、胞校验及基于已安装库的应变计算。
- `src/domains/structure/dichromatic/interactions.py`：项目归属、revision、用户选点、计划引用及关联 run。
- `src/domains/structure/routes.py`：选项、数值预览、静态导出，以及请求列表、选点、取消、应变和边界分析接口，由 API 通过 `src/domains/structure/extension.py` 中的 `routers` 声明挂载。
- `src/domains/structure/dichromatic/tools.py`：agent 的选项、静态导出（`render_dichromatic_map`，与导出接口共用）、准备、读取、施加应变和边界分析工具。
- `src/agents/streaming/events.py`：工具预览截短前提取紧凑 `interactions` 引用； `src/domains/structure/delegate.py` 把引用保留在父级结果中。
- `web/assets/js/dichromatic_widget.js`：canvas、视野请求、点击选胞和明确的对话续跑。

接口详情见 [API 与 UI](api-and-ui.md#dichromaticmap-api-与悬浮工具)，登记约定见 [扩展开发指南](extension-guide.md)。
