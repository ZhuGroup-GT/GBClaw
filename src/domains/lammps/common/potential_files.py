"""Potential file locations, including paths recorded before potentials moved (stdlib only)."""

from __future__ import annotations

from pathlib import Path

from core.paths import REPO_ROOT

BUNDLED_POTENTIALS_DIR = REPO_ROOT / "data" / "potentials"
# Runs and configs written before the move record absolute paths under here.
LEGACY_POTENTIALS_DIR = REPO_ROOT / "src" / "tools" / "potentials"


def current_potential_path(path: str | Path) -> Path:
    """Map a recorded bundled-potential path to its current location.

    Only a missing file under the former ``src/tools/potentials`` is remapped,
    and only when the same file exists in ``data/potentials``; content checks
    (sha256) still apply to the remapped file.
    """
    candidate = Path(path)
    if candidate.exists():
        return candidate
    try:
        relative = candidate.resolve().relative_to(LEGACY_POTENTIALS_DIR)
    except ValueError:
        return candidate
    moved = BUNDLED_POTENTIALS_DIR / relative
    return moved if moved.exists() else candidate


# Pair styles whose setfl/Finnis-Sinclair file sets each element's mass in LAMMPS.
FILE_MASS_PAIR_STYLES = frozenset({"eam/alloy", "eam/fs", "adp"})


def potential_file_mass(path: str | Path, pair_style: str, element: str) -> float | None:
    """The mass LAMMPS assigns ``element`` from a setfl-family potential file.

    ``pair_coeff`` for these styles replaces masses set by ``mass`` commands, so
    configurations should record this value. Returns None for other styles,
    unknown elements, or files that do not follow the setfl layout.
    """
    if pair_style not in FILE_MASS_PAIR_STYLES:
        return None
    try:
        with current_potential_path(path).open("r", encoding="utf-8", errors="replace") as handle:
            lines = iter(handle.read().splitlines()[3:])
            elements = next(lines).split()
            count = int(elements[0])
            names = elements[1:1 + count]
            nrho, _drho, nr, *_rest = next(lines).split()
            values_per_element = int(nrho) + int(nr) * (count if pair_style == "eam/fs" else 1)
            for name in names:
                header = next(lines).split()
                if name == element:
                    return float(header[1])
                seen = 0
                while seen < values_per_element:
                    seen += len(next(lines).split())
                if seen != values_per_element:
                    return None
    except (OSError, StopIteration, ValueError, IndexError):
        return None
    return None

