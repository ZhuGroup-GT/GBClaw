"""LAMMPS execution settings for GBclaw (conda env, potentials, MPI)."""

from __future__ import annotations

from pathlib import Path

from domains.lammps.common.potential_files import BUNDLED_POTENTIALS_DIR
from core.paths import REPO_ROOT, SRC_ROOT
from core.settings.execution import SERIAL_EXECUTION
from core.settings.env import get_env_int, get_env_str
from domains.lammps.common.defaults import NEB_RANKS_PER_REPLICA, NEB_REPLICAS



def _runtime_path_from_env(name: str, default: Path) -> Path:
    """Resolve a runtime path, with relative overrides anchored at the repo root."""
    raw = get_env_str(name).strip()
    candidate = Path(raw).expanduser() if raw else default
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate.resolve()


# Conda environment that provides the Python LAMMPS bindings (not the GBclaw env).
LAMMPS_CONDA_ENV = get_env_str(
    "GBCLAW_LAMMPS_CONDA_ENV", "GBclaw-lammps"
).strip() or "GBclaw-lammps"

# Engine scripts run by file path inside LAMMPS_CONDA_ENV.
_WORKFLOWS = SRC_ROOT / "domains" / "lammps"
GB_MINIMIZE_SCRIPT = _WORKFLOWS / "minimize" / "engine.py"
STRUCTURE_MINIMIZE_SCRIPT = _WORKFLOWS / "structure_minimize" / "engine.py"
GB_ENERGY_SCRIPT = _WORKFLOWS / "energy" / "engine.py"
GB_SHEAR_SCRIPT = _WORKFLOWS / "shear" / "engine.py"
GB_NEB_SCRIPT = _WORKFLOWS / "neb" / "engine.py"

POTENTIALS_DIR = _runtime_path_from_env(
    "GBCLAW_POTENTIALS_DIR",
    BUNDLED_POTENTIALS_DIR,
)
POTENTIALS_METADATA = POTENTIALS_DIR / "potentials.json"

# Fixed defaults; configure both budgets for the available physical cores.
LAMMPS_AGENT_MPI_RANKS = get_env_int(
    "GBCLAW_LAMMPS_AGENT_MPI_RANKS", 18, minimum=1
)
LAMMPS_TOTAL_MPI_RANKS = get_env_int(
    "GBCLAW_LAMMPS_TOTAL_MPI_RANKS", 18, minimum=1
)
if LAMMPS_AGENT_MPI_RANKS > LAMMPS_TOTAL_MPI_RANKS:
    raise ValueError(
        "GBCLAW_LAMMPS_AGENT_MPI_RANKS must not exceed "
        "GBCLAW_LAMMPS_TOTAL_MPI_RANKS"
    )
LAMMPS_MAX_PARALLEL_AGENTS = (
    1
    if SERIAL_EXECUTION
    else LAMMPS_TOTAL_MPI_RANKS // LAMMPS_AGENT_MPI_RANKS
)

# NEB has one LAMMPS partition per replica.  These effective defaults may be
# overridden in .env, but every configured run records the resolved values.
LAMMPS_NEB_REPLICAS = get_env_int(
    "GBCLAW_LAMMPS_NEB_REPLICAS", NEB_REPLICAS, minimum=2
)
LAMMPS_NEB_RANKS_PER_REPLICA = get_env_int(
    "GBCLAW_LAMMPS_NEB_RANKS_PER_REPLICA",
    NEB_RANKS_PER_REPLICA,
    minimum=1,
)

# Wall-clock limit for a single gb_minimize run (seconds).
GB_MINIMIZE_TIMEOUT_SEC = get_env_int(
    "GBCLAW_GB_MINIMIZE_TIMEOUT_SEC", 3600, minimum=60
)
STRUCTURE_MINIMIZE_TIMEOUT_SEC = get_env_int(
    "GBCLAW_STRUCTURE_MINIMIZE_TIMEOUT_SEC", 3600, minimum=60
)

# Wall-clock limit for one GB-energy run (bicrystal plus four references).
GB_ENERGY_TIMEOUT_SEC = get_env_int(
    "GBCLAW_GB_ENERGY_TIMEOUT_SEC", 14400, minimum=60
)

# Wall-clock limit for one quasi-static shear run (seconds).
GB_SHEAR_TIMEOUT_SEC = get_env_int(
    "GBCLAW_GB_SHEAR_TIMEOUT_SEC", 7200, minimum=60
)

# Wall-clock limit for one partitioned NEB run (seconds).
GB_NEB_TIMEOUT_SEC = get_env_int(
    "GBCLAW_GB_NEB_TIMEOUT_SEC", 14400, minimum=60
)
