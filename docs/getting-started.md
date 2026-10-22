# Getting started

[Documentation](README.md) · [中文](zh/getting-started.md) · [Architecture and design](architecture.md)

GBclaw's Web UI and terminal CLI use the same project workspaces, research records, chat history, and checkpoints. Start with either interface; a project can be reopened later.

Runtime setup: [LAMMPS with Python](#install-lammps-with-python-support) · [ASE](#install-ase) · [Connect the environments to GBclaw](#connect-the-environments-to-gbclaw).

## Why setup separates models and scientific runtimes

The application environment runs agents and manages projects. An LLM endpoint supplies the model; scientific runtimes provide packages used by calculations. Configuring a model does not install LAMMPS or ASE, and choosing a Conda environment does not choose the model. This separation lets you change either independently.

Start with one model in `.key` and the main `GBclaw` environment. Use `.env` to adapt resource budgets and environment names to your machine. Add optional calculation runtimes when a workflow needs them, and begin with a small task before launching a large study.

## Install and configure

GBClaw runs on Linux only (macOS and Windows are not supported; WSL2 is untested), and Conda must be available in your shell. The supplied [`conda_env.yml`](../conda_env.yml) is a pinned Linux environment snapshot, including Python, the agent framework, OVITO, and analysis dependencies.

```bash
git clone https://github.com/ZhuGroup-GT/GBClaw.git
cd GBClaw
conda env create -f conda_env.yml
cp .key.example .key
cp .env.example .env
```

Copy the templates only for a new setup; retain existing local configuration when updating. Edit `.key` before starting:

- Keep `[settings] use_tier_routing = false` for one model serving all roles.
- Set the `[default]` provider, API mode, base URL, model, and API key for your endpoint.
- Enable tier routing later if you want different models for planning, workers, and memory.

`.key` holds credentials and model settings. `.env` holds execution resources and timeouts; commented entries show defaults, and values already exported by the shell take precedence. See [configuration](configuration.md) for supported providers, routing, context budgets, optional web search, and runtime settings.

## Start the Web UI

```bash
./start_web.sh
```

Open **http://127.0.0.1:9999/**. The launcher activates the `GBclaw` Conda environment and serves the frontend and API together. Create a project by sending your first message, or open an existing project from the project switcher.

To change the port:

```bash
API_PORT=8000 ./start_web.sh
```

`API_HOST` sets the bind address. The launcher accepts ports from 1024 to 65535 only; put a reverse proxy in front of it when the service has to answer on 80 or 443. The server has no authentication and accepts requests from any origin, so keep it on `127.0.0.1` unless a trusted reverse proxy fronts it.

Source auto-reload is disabled by default so edits do not restart a running calculation. For development, use `API_RELOAD=1 ./start_web.sh`. To start the service manually:

```bash
conda activate GBclaw
PYTHONPATH=src python -m uvicorn interfaces.api.app:app --host 127.0.0.1 --port 9999 --ws websockets
```

Use `python -m uvicorn` to select the active environment's Python. Hosting and clients that connect through a reverse proxy must support both SSE and WebSockets; see [API and Web UI](api-and-ui.md#project-occupancy).

## Start the terminal CLI

```bash
./start_cli.sh
```

The CLI activates the same `GBclaw` environment, requires `.key`, and needs an interactive terminal. It opens a project menu without starting a web server. Select or create a project (by number, or by typing its ID), then type at the `> ` prompt. Reopening a project retains its research history; the last 10 turns are shown on entry. A project that an open browser tab holds cannot be entered until that tab closes; the menu is shown again.

| Command | Action |
| --- | --- |
| `/help` | Show the commands and the input rules. |
| `/plan <task>` | Request a plan that requires explicit approval before execution. |
| `/noplan <task>` | Execute a bounded request directly, retaining scientific validation. |
| `/projects`, `/switch` | List projects or switch to another project. |
| `/thread` | Show the current project and thread IDs. |
| `/history [N]` | Show the last N turns (default 10, at most 200); interrupted and failed turns are marked. |
| `/clear` | Clear the terminal display. |
| `/quit`, `/exit`, `/q` | Exit the CLI. |

The prompt is a readline line: the arrow keys edit it and recall earlier messages of the current project (kept in the project's `chat/cli_history`), and Tab completes commands. A line that ends with a backslash continues on the next line; a line consisting of ``` opens a block that the next ``` line closes, so code and blank lines can be entered; pasted text is sent as one message. Ctrl+C during a continuation line discards the draft. A message that begins with `/` but is not a command, such as a file path, is sent as chat; an unknown short `/word` is reported instead, and a leading space always forces chat.

While a turn runs, each tool call prints as one line (`▸ label  arguments`) followed by a three-line preview of its result, worker tool calls are indented under their delegate, reasoning is shown behind `│`, and the answer streams after `GBclaw:`. When the turn finishes, image artifacts are listed as file paths. Ctrl+C during a turn stops it at once: the model stream is cut, registered subprocesses such as LAMMPS are terminated, a tool already running finishes first, and the turn is recorded as interrupted (its partial answer is discarded, as in the Web UI); a second Ctrl+C exits without waiting, and the project is repaired when it is next opened. At the prompt, Ctrl+C twice within two seconds or Ctrl+D exits. The project menu also accepts `q`. Set `NO_COLOR=1` for plain terminal output.

Plans can also be approved or revised through ordinary chat. `/noplan` rejects an existing pending draft when it replaces that draft; the original remains in the audit history. See [agent runtime](agent-runtime.md) for planning and delegation behavior.

To start the CLI manually, run it from the repository root, where it looks for `.key`:

```bash
conda activate GBclaw
PYTHONPATH=src python -m interfaces.cli
```

## Enable the scientific runtimes you need

| Workflow | Runtime requirement |
| --- | --- |
| Structure/CSL and analysis | The main `GBclaw` environment; OVITO is included in its environment file. |
| Managed LAMMPS simulations | A separate Conda environment, default `GBclaw-lammps`, with the LAMMPS Python bindings, compatible MPI, and the pair styles required by your selected potential. Set `GBCLAW_LAMMPS_CONDA_ENV` to its name. |
| Custom coding | Linux Bubblewrap and `prlimit`, plus a registered runtime in `src/domains/coding/environments.toml`. |
| Custom ASE tasks | The optional `ase` coding runtime, configured by `GBCLAW_ASE_CONDA_ENV` (default `GBclaw-ase`), with Atomic Simulation Environment installed by the host administrator. |

Registering an ASE or LAMMPS environment does not create it or install packages. Ask the agent to list coding environments to check availability before requesting a custom computation. The coding worker executes within the selected read-only environment; it does not install dependencies. See [coding runtimes](coding-agent.md#runtime-environment-registry).

LAMMPS ranks and the shared Structure worker pool have independent resource budgets. Configure them for your host in `.env` before running simulations; the LAMMPS defaults reserve 18 ranks, and an NEB run must fit its entire replica topology within the total rank budget. The bundled interatomic potentials and their `potentials.json` catalog are in `data/potentials/`; set `GBCLAW_POTENTIALS_DIR` to use another directory. See [LAMMPS configuration](configuration.md#lammps-config) and the [tool reference](tools.md) for potentials, inputs, and workflow requirements.

### Install LAMMPS with Python support

For the current CPU workflows, start with the prebuilt [LAMMPS PyPI package](https://pypi.org/project/lammps/). It includes the Python interface and matching shared library, with the `MANYBODY` package for EAM/ADP potentials and `REPLICA` for NEB. You do not need to compile LAMMPS yourself. If you need custom packages, a particular MPI installation, or hardware-specific acceleration, use the optional [source build](#build-lammps-from-source-optional) instead.

**1. Create the environment and install LAMMPS.** Use the same Conda installation as the main `GBclaw` environment. On Linux, the PyPI wheel uses MPICH; `lammps[mpi]` installs the MPI runtime, while `mpi4py` provides Python access to MPI. Install NumPy for GBclaw's calculation scripts. See the [mpi4py installation guide](https://mpi4py.readthedocs.io/en/stable/install.html#wheel-packages) for the binary packages.

```bash
conda create -n GBclaw-lammps --override-channels -c conda-forge python=3.12 pip
conda activate GBclaw-lammps
python -m pip install --only-binary=:all: "lammps[mpi]" mpi4py numpy
python -m pip check
```

`--only-binary` selects prebuilt packages. If no compatible wheel is available for your platform, follow the source instructions below instead.

**2. Check the Python interface and required styles.** Constructing a LAMMPS instance checks that Python can load the shared library; a successful import alone is insufficient. This also checks the command and output-flushing interfaces used by GBclaw.

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

**3. Check MPI with two processes.** Run this in the same environment on a machine or job allocation with at least two available CPU cores:

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

Both rank 0 and rank 1 should report two MPI ranks. LAMMPS, `mpi4py`, and the MPI launcher must use a compatible MPI implementation. If a check fails, inspect `command -v python mpirun`: both should resolve inside the selected environment. These checks validate runtime capabilities; validate your selected potential and simulation inputs before production calculations.

<details>
<summary>Optional: check the replica initialization used by NEB</summary>

This initializes two replicas with one rank each, without creating atoms or running a simulation. Each process should report one rank in its LAMMPS replica while Python still sees both global ranks.

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

#### Build LAMMPS from source (optional)

Choose this method instead of the pip installation when you need to customize the build or no compatible wheel is available. It follows [Install LAMMPS with Python using conda](https://yazhuoliu.com/articles/install-lammps-python/) and the official [Python installation guide](https://docs.lammps.org/Python_install.html). This CPU example creates the same `GBclaw-lammps` environment using Open MPI.

<details>
<summary>Show the source compilation and installation steps</summary>

**1. Create the environment and download the source.** Install Open MPI and `mpi4py` together so their dependencies agree. See the [Conda packages section of the mpi4py guide](https://mpi4py.readthedocs.io/en/stable/install.html#conda-packages).

```bash
conda create -n GBclaw-lammps --override-channels -c conda-forge \
  python=3.12 pip setuptools wheel git cmake ninja compilers \
  openmpi mpi4py numpy
conda activate GBclaw-lammps

mkdir -p "$HOME/src"
git clone --depth 1 --branch release \
  https://github.com/lammps/lammps.git "$HOME/src/lammps-gbclaw"
```

**2. Build and install the executable, shared library, and Python package.** Keep `GBclaw-lammps` activated throughout this step. Adjust `--parallel 4` to the resources available on your machine, and add any extra package or accelerator settings your workflow requires.

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

`BUILD_SHARED_LIBS` creates the library Python loads, and `install-python` installs the bindings with that build's library into the selected Python environment. Repeat `install-python` after rebuilding LAMMPS. The installation prefix and runtime library path place the native installation inside Conda without requiring `sudo`; see [LAMMPS build and installation options](https://docs.lammps.org/Build_basics.html#install-lammps-after-a-build).

`MANYBODY` supplies [`eam/fs`](https://docs.lammps.org/pair_eam.html) and [`adp`](https://docs.lammps.org/pair_adp.html); `REPLICA` supplies the [NEB workflow](https://docs.lammps.org/neb.html). The separate [`PYTHON` package](https://docs.lammps.org/Packages_details.html#python-package) lets LAMMPS input scripts call Python and is not required for Python to call LAMMPS.

Run the Python, MPI, and optional replica checks above while `GBclaw-lammps` is active, then follow [Connect the environments to GBclaw](#connect-the-environments-to-gbclaw).

</details>

### Install ASE

ASE (Atomic Simulation Environment) provides Python tools for building, reading, writing, and manipulating atomic structures. Following the [official installation guide](https://docs.ase-lib.org/install.html), install its PyPI package inside a separate Conda environment. Python 3.12 satisfies the documented Python 3.11-or-newer requirement.

```bash
conda create -n GBclaw-ase --override-channels -c conda-forge python=3.12 pip
conda activate GBclaw-ase
python -m pip install --upgrade ase
python -m pip check
```

Check the installed version and a small structure read/write round trip. This example runs entirely in memory and requires neither a display nor an external calculator:

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

For ASE's own test suite, install `python -m pip install --upgrade 'ase[test]'` and run `ase test` in this environment. ASE calculators that use external calculation engines require those engines to be installed and configured for the ASE runtime. See [ASE calculators](https://docs.ase-lib.org/ase/calculators/calculators.html) when extending this setup.

### Connect the environments to GBclaw

In the repository root, edit the corresponding entries in `.env` for the runtimes you installed:

```dotenv
GBCLAW_LAMMPS_CONDA_ENV=GBclaw-lammps
GBCLAW_ASE_CONDA_ENV=GBclaw-ase
```

These are the defaults; use your actual environment names if they differ. The managed LAMMPS worker uses `GBCLAW_LAMMPS_CONDA_ENV`, and `src/domains/coding/environments.toml` already registers the `lammps` and `ase` aliases using these variables. There is no registry change needed for the default setup. For environments outside the main Conda installation, see [runtime environment discovery](coding-agent.md#runtime-environment-registry).

Start or restart the Web UI or CLI from the repository root using the commands above. The application still runs in `GBclaw`; it selects the scientific environment for each task. Ask the agent to list the coding environments and confirm that the installed aliases are available. A simple first ASE request is: “Use the `ase` environment to create an FCC Ni unit cell and save it as an extended XYZ file.”

## Continue an investigation

Keep one frontend attached to a project at a time. Close its browser tab or leave its CLI session before opening that project elsewhere. Different projects can be used independently.

The file browser exposes generated structures, plots, reports, and logs. Summaries provide orientation; ask the agent to retrieve the underlying run, fact, or artifact when exact details matter. See [project memory](project-memory.md) for the storage and evidence model.
