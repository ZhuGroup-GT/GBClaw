# 快速上手

[文档导航](README.md) · [English](../getting-started.md) · [架构与设计逻辑](architecture.md)

GBclaw 的 Web UI 和终端 CLI 使用同一套项目工作区、研究记录、聊天历史和检查点。可以选择任一入口，之后再打开原项目继续工作。

计算环境安装：[支持 Python 的 LAMMPS](#安装支持-python-的-lammps) · [ASE](#安装-ase) · [接入 GBclaw](#将计算环境接入-gbclaw)。

## 为什么要区分模型与科学计算环境

应用环境运行 agent 并管理项目，LLM endpoint 提供模型，科学计算环境提供计算所需的软件包。配置模型不会安装 LAMMPS 或 ASE，选择 Conda 环境也不等于选择模型。这样两者可以独立调整。

开始时，在 `.key` 中配置一个模型，并安装主环境 `GBclaw`。在 `.env` 中按机器条件调整资源预算和环境名。需要具体工作流时再准备相应计算环境，先用一个小任务确认配置，然后再开展大规模研究。

## 安装与配置

GBClaw 只支持 Linux（不支持 macOS 和 Windows，WSL2 未经测试），并确保当前 shell 可以运行 Conda。[`conda_env.yml`](../../conda_env.yml) 是固定版本的 Linux 环境快照，包含 Python、agent 框架、OVITO 和分析依赖。

```bash
git clone https://github.com/ZhuGroup-GT/GBClaw.git
cd GBClaw
conda env create -f conda_env.yml
cp .key.example .key
cp .env.example .env
```

复制模板用于首次安装；更新已有安装时，保留自己的本地配置。启动前编辑 `.key`：

- 使用一个模型承担全部角色时，保留 `[settings] use_tier_routing = false`。
- 在 `[default]` 中填写 endpoint 的 provider、API mode、base URL、model 和 API key。
- 如需为规划、worker 和记忆选择不同模型，再启用 tier routing。

`.key` 保存凭证和模型设置，`.env` 保存运行资源和超时配置。模板中的注释展示默认值；shell 已导出的环境变量优先于 `.env`。支持的 provider、模型路由、上下文预算、可选网页搜索和运行配置见[配置指南](configuration.md)。

## 启动 Web UI

```bash
./start_web.sh
```

打开 **http://127.0.0.1:9999/**。启动脚本会激活 `GBclaw` Conda 环境，同时提供前端与 API。发送第一条消息可以创建项目，也可以从项目切换器打开已有项目。

更换端口：

```bash
API_PORT=8000 ./start_web.sh
```

`API_HOST` 设置监听地址。启动脚本只接受 1024 到 65535 之间的端口，需要在 80 或 443 上提供服务时请在前面放反向代理。服务没有身份认证，也接受任意来源的请求，除非前面有可信的反向代理，否则请保持 `127.0.0.1`。

默认关闭源码自动重载，避免编辑文件时重启正在运行的计算。开发时可使用 `API_RELOAD=1 ./start_web.sh`。手动启动方式为：

```bash
conda activate GBclaw
PYTHONPATH=src python -m uvicorn interfaces.api.app:app --host 127.0.0.1 --port 9999 --ws websockets
```

`python -m uvicorn` 使用当前环境的 Python。如果经过反向代理连接，代理需要支持 SSE 和 WebSocket，详见 [API 与界面](api-and-ui.md#项目占用)。

## 启动终端 CLI

```bash
./start_cli.sh
```

CLI 使用同一 `GBclaw` 环境，要求存在 `.key` 并在交互终端中运行。它先打开项目菜单，不启动 Web 服务。按编号或直接输入项目 ID 选择项目，或创建新项目，然后在 `> ` 提示符下输入请求；重新打开项目时，原有研究历史仍然保留，进入时显示最近 10 轮。被打开着的浏览器标签占用的项目无法进入，此时会重新显示菜单。

| 命令 | 用途 |
| --- | --- |
| `/help` | 查看命令和输入规则。 |
| `/plan <task>` | 请求制定计划，明确批准后才执行。 |
| `/noplan <task>` | 直接执行范围明确的请求，仍保留科学检查。 |
| `/projects`、`/switch` | 列出项目或切换项目。 |
| `/thread` | 查看当前 project 和 thread ID。 |
| `/history [N]` | 查看最近 N 轮（默认 10，最多 200）；被中断或失败的轮次会标出。 |
| `/clear` | 清空终端显示。 |
| `/quit`、`/exit`、`/q` | 退出 CLI。 |

提示符是一行 readline 输入：方向键可以编辑，也可以调出当前项目之前输入过的消息（保存在项目的 `chat/cli_history`），Tab 补全命令。行尾是反斜杠时在下一行继续；单独一行 ``` 打开一个代码块，直到下一行 ``` 结束，可以输入代码和空行；粘贴的多行文本作为一条消息发送。在续行时按 Ctrl+C 会丢弃草稿。以 `/` 开头但不是命令的内容（例如文件路径）会作为聊天发送；未知的短 `/word` 会被提示，行首加一个空格则一定作为聊天发送。

一轮运行期间，每次工具调用打印为一行（`▸ 标签  参数`），后面是最多三行的结果预览；worker 的工具调用缩进显示在委派它的工具下；推理内容显示在 `│` 之后；回复在 `GBclaw:` 后流式输出。一轮结束时列出图片产物的文件路径。运行中按 Ctrl+C 会立即停止这一轮：切断模型输出流，终止已注册的子进程（例如 LAMMPS），正在线程中运行的工具先执行完，该轮记录为已中断（未完成的回答会被丢弃，与 Web 界面一致）；再按一次 Ctrl+C 则不等待直接退出，项目在下次打开时修复。在提示符下，两秒内连续按两次 Ctrl+C 或按 Ctrl+D 退出。项目菜单也接受 `q`。设置 `NO_COLOR=1` 可以关闭彩色输出。

也可以通过普通聊天批准计划或提出修改。如果 `/noplan` 替代了待批准草案，系统会拒绝该草案，同时保留审计记录。计划和委派流程见 [agent 运行时](agent-runtime.md)。

手动启动 CLI 时，需要在仓库根目录运行，CLI 会在当前目录查找 `.key`：

```bash
conda activate GBclaw
PYTHONPATH=src python -m interfaces.cli
```

## 准备任务需要的科学计算环境

| 工作流 | 环境要求 |
| --- | --- |
| Structure/CSL 和分析 | 主环境 `GBclaw`；环境文件已经包含 OVITO。 |
| 标准 LAMMPS 模拟 | 独立 Conda 环境，默认为 `GBclaw-lammps`，需要 LAMMPS Python binding、兼容的 MPI 和所选势函数需要的 pair style。用 `GBCLAW_LAMMPS_CONDA_ENV` 指定环境名。 |
| 自定义代码 | Linux Bubblewrap、`prlimit`，以及 `src/domains/coding/environments.toml` 中已注册的运行环境。 |
| 自定义 ASE 任务 | 可选 `ase` 运行环境，由 `GBCLAW_ASE_CONDA_ENV` 指定，默认名为 `GBclaw-ase`；宿主需要先安装 Atomic Simulation Environment。 |

注册 ASE 或 LAMMPS 环境不会自动创建环境或安装软件包。请求自定义计算之前，可以让 agent 列出 coding 环境以检查可用性。Coding worker 使用选定的只读环境执行，不会自行安装依赖，详见 [coding 环境](coding-agent.md#运行环境注册表)。

LAMMPS 的 MPI rank 和 Structure worker 的并发池使用独立资源预算。计算前应根据机器条件设置 `.env`；LAMMPS 默认预留 18 个 rank，NEB 的全部 replica 必须同时满足总 rank 预算。内置原子间势函数及其 `potentials.json` 清单位于 `data/potentials/`；如需使用其他目录，设置 `GBCLAW_POTENTIALS_DIR`。势函数、输入和具体要求见[配置](configuration.md#lammps-config)与[科学工具](tools.md)。

### 安装支持 Python 的 LAMMPS

当前 CPU 工作流可以直接使用预编译的 [LAMMPS PyPI 软件包](https://pypi.org/project/lammps/)。它包含 Python 接口及匹配的共享库，并启用 EAM/ADP 势函数需要的 `MANYBODY` 包和 NEB 需要的 `REPLICA` 包，无需自行编译。需要定制软件包、使用特定 MPI 安装或针对硬件配置加速时，再选择可选的[源码编译](#从源码编译-lammps可选)。

**1. 创建环境并安装 LAMMPS。** 使用安装主环境 `GBclaw` 的同一套 Conda。Linux 上的 PyPI wheel 使用 MPICH；`lammps[mpi]` 安装 MPI 运行库，`mpi4py` 提供 Python 访问 MPI 的接口，再安装 GBclaw 计算脚本需要的 NumPy。预编译包说明见 [mpi4py 安装指南](https://mpi4py.readthedocs.io/en/stable/install.html#wheel-packages)。

```bash
conda create -n GBclaw-lammps --override-channels -c conda-forge python=3.12 pip
conda activate GBclaw-lammps
python -m pip install --only-binary=:all: "lammps[mpi]" mpi4py numpy
python -m pip check
```

`--only-binary` 指定使用预编译包。如果当前平台没有匹配的 wheel，改用下面的源码安装方式。

**2. 检查 Python 接口和所需样式。** 创建 LAMMPS 实例可以检查 Python 能否加载共享库，仅能成功 import 还不够。下面同时检查 GBclaw 使用的命令执行和输出刷新接口。

```bash
python - <<'PY'
import sys
import numpy as np
from mpi4py import MPI
from lammps import lammps

lmp = lammps(comm=MPI.COMM_WORLD, cmdargs=["-log", "none", "-screen", "none"])
try:
    assert lmp.has_mpi_support, "LAMMPS was built without MPI"
    assert lmp.has_style("pair", "eam/fs"), "Missing MANYBODY: eam/fs"
    assert lmp.has_style("pair", "adp"), "Missing MANYBODY: adp"
    assert lmp.has_style("fix", "neb"), "Missing REPLICA: fix neb"
    assert lmp.get_mpi_comm().Get_size() == MPI.COMM_WORLD.Get_size()
    lmp.command("units metal")
    lmp.flush_buffers()
    print("Python:", sys.executable)
    print("NumPy:", np.__version__)
    print("MPI:", MPI.get_vendor())
    print("LAMMPS version:", lmp.version())
    print("LAMMPS Python interface and required styles are available.")
finally:
    lmp.close()
PY
```

**3. 检查双进程 MPI。** 在同一环境中执行，确保机器或调度系统分配的作业资源至少有两个可用 CPU 核：

```bash
mpirun --map-by core --bind-to core -n 2 python -c '
from mpi4py import MPI
from lammps import lammps

comm = MPI.COMM_WORLD
lmp = lammps(comm=comm, cmdargs=["-log", "none", "-screen", "none", "-nonbuf"])
try:
    assert comm.size == 2
    assert lmp.extract_setting("world_size") == comm.size
    assert lmp.get_mpi_comm().Get_size() == comm.size
    lmp.command("units metal")
    lmp.flush_buffers()
    print(f"Rank {comm.rank}: Python and LAMMPS share {comm.size} MPI ranks")
finally:
    lmp.close()
'
```

应看到 rank 0 和 rank 1 都报告两个 MPI rank。LAMMPS、`mpi4py` 和 MPI 启动器必须使用兼容的 MPI 实现。如果检查失败，查看 `command -v python mpirun`，它们都应来自选定环境。这些检查验证的是运行能力，正式计算前仍需验证所选势函数和模拟输入。

<details>
<summary>可选：检查 NEB 使用的 replica 初始化</summary>

下面初始化两个 replica，每个使用一个 rank，不创建原子，也不运行模拟。每个进程应报告其 LAMMPS replica 只有一个 rank，而 Python 仍能看到两个全局 rank。

```bash
mpirun --map-by core --bind-to core -n 2 python -c '
from mpi4py import MPI
from lammps import lammps

comm = MPI.COMM_WORLD
lmp = lammps(comm=comm, cmdargs=[
    "-in", "/dev/null", "-partition", "2x1",
    "-log", "none", "-plog", "none", "-screen", "none", "-pscreen", "none",
])
try:
    assert comm.size == 2
    assert lmp.get_mpi_comm().Get_size() == 1
    assert lmp.has_style("fix", "neb")
    print(f"Global rank {comm.rank}: one rank in this LAMMPS replica")
finally:
    lmp.close()
'
```

</details>

#### 从源码编译 LAMMPS（可选）

需要定制编译或当前平台没有匹配的 wheel 时，选择这一方式替代上面的 pip 安装。流程参考 [Install LAMMPS with Python using conda](https://yazhuoliu.com/articles/install-lammps-python/) 和官方 [Python 安装指南](https://docs.lammps.org/Python_install.html)。下面的 CPU 示例使用 Open MPI，创建的环境同样命名为 `GBclaw-lammps`。

<details>
<summary>展开源码编译与安装步骤</summary>

**1. 创建环境并下载源码。** 一起安装 Open MPI 和 `mpi4py`，使它们使用一致的依赖，参见 [mpi4py 指南的 Conda 安装部分](https://mpi4py.readthedocs.io/en/stable/install.html#conda-packages)。

```bash
conda create -n GBclaw-lammps --override-channels -c conda-forge \
  python=3.12 pip setuptools wheel git cmake ninja compilers \
  openmpi mpi4py numpy
conda activate GBclaw-lammps

mkdir -p "$HOME/src"
git clone --depth 1 --branch release \
  https://github.com/lammps/lammps.git "$HOME/src/lammps-gbclaw"
```

**2. 编译并安装可执行文件、共享库和 Python 包。** 整个步骤保持 `GBclaw-lammps` 激活状态。根据机器资源调整 `--parallel 4` 的编译并行数，并添加实际工作流需要的软件包或加速设置。

```bash
cmake -S "$HOME/src/lammps-gbclaw/cmake" \
  -B "$HOME/src/lammps-gbclaw/build" -G Ninja \
  -D CMAKE_BUILD_TYPE=Release \
  -D CMAKE_C_COMPILER="$CONDA_PREFIX/bin/mpicc" \
  -D CMAKE_CXX_COMPILER="$CONDA_PREFIX/bin/mpicxx" \
  -D Python_EXECUTABLE="$CONDA_PREFIX/bin/python" \
  -D CMAKE_INSTALL_PREFIX="$CONDA_PREFIX" \
  -D CMAKE_INSTALL_LIBDIR=lib \
  -D LAMMPS_INSTALL_RPATH=ON \
  -D BUILD_MPI=ON \
  -D BUILD_SHARED_LIBS=ON \
  -D PKG_MANYBODY=ON \
  -D PKG_REPLICA=ON

cmake --build "$HOME/src/lammps-gbclaw/build" --parallel 4
cmake --install "$HOME/src/lammps-gbclaw/build"
cmake --build "$HOME/src/lammps-gbclaw/build" --target install-python
```

`BUILD_SHARED_LIBS` 生成供 Python 加载的共享库，`install-python` 将 Python binding 与本次编译的共享库一起安装到选定的 Python 环境。以后重新编译 LAMMPS，也要重新执行 `install-python`。安装前缀和运行时库路径将原生安装放在 Conda 环境内，无需 `sudo`；详见 [LAMMPS 编译与安装选项](https://docs.lammps.org/Build_basics.html#install-lammps-after-a-build)。

`MANYBODY` 提供 [`eam/fs`](https://docs.lammps.org/pair_eam.html) 和 [`adp`](https://docs.lammps.org/pair_adp.html)，`REPLICA` 提供 [NEB 工作流](https://docs.lammps.org/neb.html)。另一个名为 [`PYTHON`](https://docs.lammps.org/Packages_details.html#python-package) 的可选包用于从 LAMMPS 输入脚本调用 Python，并不是 Python 调用 LAMMPS 的前提。

保持 `GBclaw-lammps` 激活，运行前面的 Python、MPI 及可选 replica 检查，然后按后文[将计算环境接入 GBclaw](#将计算环境接入-gbclaw)。

</details>

### 安装 ASE

ASE（Atomic Simulation Environment）提供构建、读取、写出和处理原子结构的 Python 工具。按[官方安装指南](https://docs.ase-lib.org/install.html)，在独立 Conda 环境中安装 PyPI 软件包。这里选择 Python 3.12，满足官方文档要求的 Python 3.11 或更新版本。

```bash
conda create -n GBclaw-ase --override-channels -c conda-forge python=3.12 pip
conda activate GBclaw-ase
python -m pip install --upgrade ase
python -m pip check
```

检查版本，并用一个小结构验证写出后再读回的结果。示例完全在内存中运行，无需图形界面或外部计算程序：

```bash
python - <<'PY'
from io import StringIO
import sys
import ase
import numpy as np
from ase.build import bulk
from ase.io import read, write

atoms = bulk("Ni", "fcc", a=3.52, cubic=True)
buffer = StringIO()
write(buffer, atoms, format="extxyz")
buffer.seek(0)
restored = read(buffer, format="extxyz")
assert len(restored) == 4
assert restored.get_chemical_symbols() == atoms.get_chemical_symbols()
assert np.allclose(restored.positions, atoms.positions)
assert np.allclose(restored.cell, atoms.cell)
print("Python:", sys.executable)
print("ASE:", ase.__version__)
print("ASE structure and I/O check passed.")
PY
```

如需运行 ASE 自带的测试，在该环境中执行 `python -m pip install --upgrade 'ase[test]'`，再运行 `ase test`。使用外部计算引擎的 ASE calculator，需要为 ASE 运行环境安装和配置对应引擎。继续扩展时可参考 [ASE calculators](https://docs.ase-lib.org/ase/calculators/calculators.html)。

### 将计算环境接入 GBclaw

在仓库根目录的 `.env` 中，根据已经安装的环境修改对应配置：

```dotenv
GBCLAW_LAMMPS_CONDA_ENV=GBclaw-lammps
GBCLAW_ASE_CONDA_ENV=GBclaw-ase
```

以上就是默认值；使用其他环境名时改为实际名称。标准 LAMMPS worker 使用 `GBCLAW_LAMMPS_CONDA_ENV`，`src/domains/coding/environments.toml` 已经通过这两个变量注册了 `lammps` 和 `ase` 别名，默认安装不需要再修改注册文件。如果环境不在主 Conda 安装目录下，查找方式见 [coding 环境注册](coding-agent.md#运行环境注册表)。

在仓库根目录按前面的命令启动或重启 Web UI、CLI。应用仍然运行在 `GBclaw` 中，再为具体任务选择科学计算环境。让 agent 列出 coding 环境，确认已经安装的别名可用。第一个 ASE 请求可以是：“使用 `ase` 环境创建一个 FCC Ni 晶胞，并保存为 extended XYZ 文件。”

## 继续已有研究

同一项目同时只连接一个前端。准备在其他入口打开项目时，先关闭原浏览器标签页或退出原 CLI 会话。不同项目可以独立使用。

文件浏览器提供生成的结构、图表、报告和日志。摘要帮助你和 agent 了解当前进展；需要准确细节时，让 agent 查询对应的 run、fact 或 artifact。存储方式和证据来源见[项目记忆](project-memory.md)。
