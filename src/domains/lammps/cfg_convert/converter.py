"""Convert extended CFG structures to ``atom_style atomic`` LAMMPS data for the LAMMPS worker.

The converter uses only the Python standard library. It accepts the no-velocity
extended-CFG form written by LAMMPS ``dump ... cfg`` and supports orthogonal or
right-handed triclinic cells (H0 rows are the cell vectors). Output coordinates
are rotated to LAMMPS restricted-triclinic form.

Extended CFG stores element labels but neither LAMMPS atom types (several types
may share an element) nor the Cartesian box origin, and LAMMPS prints the H0
cell with six significant digits. GBclaw's engines therefore add a ``type``
auxiliary column and exact H0 values. For other CFG files, a LAMMPS data file
of the same atoms (``LammpsDataReference``) supplies the types by atom ID and,
when the CFG cell is that box as printed, its exact cell and origin.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CFGAtom:
    atom_id: int
    label: str
    mass: float
    fractional: tuple[float, float, float]
    image: tuple[int, int, int]
    atom_type: int | None = None


@dataclass(frozen=True)
class CFGStructure:
    source: Path
    cell: tuple[tuple[float, float, float], ...]
    atoms: tuple[CFGAtom, ...]


@dataclass(frozen=True)
class RestrictedCell:
    lx: float
    ly: float
    lz: float
    xy: float
    xz: float
    yz: float
    origin: tuple[float, float, float]


@dataclass(frozen=True)
class CFGConversionResult:
    source: Path
    output: Path
    atoms: int
    atom_types: int
    source_cell: tuple[tuple[float, float, float], ...]
    restricted_cell: RestrictedCell
    coordinate_origin: str
    type_map: tuple[dict[str, object], ...]
    type_source: str = "cfg_labels"
    cell_source: str = "cfg_header"

    def to_dict(self) -> dict[str, object]:
        cell = self.restricted_cell
        return {
            "atoms": self.atoms,
            "atom_types": self.atom_types,
            "source_cell_angstrom": [list(row) for row in self.source_cell],
            "restricted_cell_angstrom": {
                "lx": cell.lx,
                "ly": cell.ly,
                "lz": cell.lz,
                "xy": cell.xy,
                "xz": cell.xz,
                "yz": cell.yz,
                "origin": list(cell.origin),
                "geometric_center": [
                    cell.origin[0] + 0.5 * (cell.lx + cell.xy + cell.xz),
                    cell.origin[1] + 0.5 * (cell.ly + cell.yz),
                    cell.origin[2] + 0.5 * cell.lz,
                ],
            },
            "coordinate_origin": self.coordinate_origin,
            "type_map": [dict(item) for item in self.type_map],
            "type_source": self.type_source,
            "cell_source": self.cell_source,
        }


_PARTICLE_RE = re.compile(r"^Number of particles\s*=\s*(\d+)\s*$")
_SCALE_RE = re.compile(
    r"^A\s*=\s*([-+0-9.eE]+)\s+Angstrom(?:s)?(?:\s+\(basic length-scale\))?\s*$"
)
_CELL_RE = re.compile(
    r"^H0\(([123]),([123])\)\s*=\s*([-+0-9.eE]+)(?:\s+A)?\s*$"
)
_ENTRY_RE = re.compile(r"^entry_count\s*=\s*(\d+)\s*$")
_AUX_RE = re.compile(r"^auxiliary\[(\d+)\]\s*=\s*(\S+)\s*$")
_BOX_BOUNDS_RE = re.compile(
    r"^\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([xyz])lo\s+\3hi\s*$"
)
_BOX_TILT_RE = re.compile(
    r"^\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+xy\s+xz\s+yz\s*$"
)


def _integer(value: float, *, path: Path, line: int, label: str) -> int:
    rounded = round(value)
    if not math.isclose(value, rounded, rel_tol=0.0, abs_tol=1.0e-8):
        raise ValueError(f"{path}:{line}: {label}={value} is not an integer")
    return int(rounded)


def read_extended_cfg(path: Path | str) -> CFGStructure:
    source = Path(path).resolve()
    lines = source.read_text(encoding="utf-8").splitlines()
    count: int | None = None
    scale = 1.0
    raw_cell: list[list[float | None]] = [[None] * 3 for _ in range(3)]
    entry_count: int | None = None
    entry_line: int | None = None
    no_velocity = False

    for index, raw in enumerate(lines):
        line = raw.strip()
        if match := _PARTICLE_RE.match(line):
            count = int(match.group(1))
        elif match := _SCALE_RE.match(line):
            scale = float(match.group(1))
        elif match := _CELL_RE.match(line):
            i, j = int(match.group(1)) - 1, int(match.group(2)) - 1
            raw_cell[i][j] = float(match.group(3))
        elif line == ".NO_VELOCITY.":
            no_velocity = True
        elif match := _ENTRY_RE.match(line):
            entry_count = int(match.group(1))
            entry_line = index
            break

    if count is None or entry_count is None or entry_line is None:
        raise ValueError(f"{source}: incomplete extended-CFG header")
    if count <= 0:
        raise ValueError(f"{source}: Number of particles must be positive")
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError(f"{source}: basic length scale must be finite and positive")
    if not no_velocity:
        raise ValueError(f"{source}: velocity-containing CFG files are not supported")
    if entry_count < 3:
        raise ValueError(f"{source}: entry_count must be at least 3")
    if any(value is None for row in raw_cell for value in row):
        raise ValueError(f"{source}: all nine H0 cell entries are required")
    if any(not math.isfinite(float(value)) for row in raw_cell for value in row):
        raise ValueError(f"{source}: H0 cell entries must be finite")

    auxiliary: dict[str, int] = {}
    cursor = entry_line + 1
    expected_auxiliary = entry_count - 3
    while len(auxiliary) < expected_auxiliary and cursor < len(lines):
        line = lines[cursor].strip()
        cursor += 1
        if not line:
            continue
        match = _AUX_RE.match(line)
        if not match:
            raise ValueError(
                f"{source}:{cursor}: expected an auxiliary field declaration"
            )
        auxiliary[match.group(2)] = 3 + int(match.group(1))
    if "id" not in auxiliary:
        raise ValueError(f"{source}: auxiliary id field is required")

    atoms: list[CFGAtom] = []
    while cursor < len(lines) and len(atoms) < count:
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        if cursor >= len(lines):
            break
        try:
            mass = float(lines[cursor].strip())
        except ValueError as exc:
            raise ValueError(f"{source}:{cursor + 1}: expected an atomic mass") from exc
        if not math.isfinite(mass) or mass <= 0.0:
            raise ValueError(
                f"{source}:{cursor + 1}: atomic mass must be finite and positive"
            )
        cursor += 1
        if cursor >= len(lines) or not lines[cursor].strip():
            raise ValueError(f"{source}:{cursor + 1}: expected an atom label")
        label = lines[cursor].strip()
        cursor += 1
        if cursor >= len(lines):
            raise ValueError(f"{source}: missing atom values after label")
        fields = lines[cursor].split()
        cursor += 1
        if len(fields) < entry_count:
            raise ValueError(
                f"{source}:{cursor}: expected {entry_count} atom values, got {len(fields)}"
            )
        values = [float(value) for value in fields[:entry_count]]
        if any(not math.isfinite(value) for value in values):
            raise ValueError(f"{source}:{cursor}: atom values must be finite")
        atom_id = _integer(values[auxiliary["id"]], path=source, line=cursor, label="id")
        image = tuple(
            _integer(values[auxiliary[name]], path=source, line=cursor, label=name)
            if name in auxiliary
            else 0
            for name in ("ix", "iy", "iz")
        )
        atom_type = None
        if "type" in auxiliary:
            atom_type = _integer(values[auxiliary["type"]], path=source, line=cursor, label="type")
            if atom_type <= 0:
                raise ValueError(f"{source}:{cursor}: atom type must be positive")
        atoms.append(
            CFGAtom(
                atom_id=atom_id,
                label=label,
                mass=mass,
                fractional=(values[0], values[1], values[2]),
                image=image,
                atom_type=atom_type,
            )
        )

    if len(atoms) != count:
        raise ValueError(f"{source}: header says {count} atoms, read {len(atoms)}")
    if len({atom.atom_id for atom in atoms}) != count:
        raise ValueError(f"{source}: duplicate atom IDs are not allowed")
    cell = tuple(
        tuple(float(value) * scale for value in row)  # type: ignore[arg-type]
        for row in raw_cell
    )
    return CFGStructure(source=source, cell=cell, atoms=tuple(atoms))


def cfg_has_type_column(path: Path | str) -> bool:
    """True when an extended CFG declares the LAMMPS ``type`` auxiliary column."""
    with Path(path).open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if match := _AUX_RE.match(line):
                if match.group(2) == "type":
                    return True
            elif line and "=" not in line and not line.startswith("."):
                return False  # the first atom record ends the header
    return False


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _norm(vector: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(vector, vector))


def _scale(vector: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    return tuple(component * factor for component in vector)  # type: ignore[return-value]


def _subtract(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return tuple(left[index] - right[index] for index in range(3))  # type: ignore[return-value]


def _determinant(cell: tuple[tuple[float, float, float], ...]) -> float:
    return (
        cell[0][0] * (cell[1][1] * cell[2][2] - cell[1][2] * cell[2][1])
        - cell[0][1] * (cell[1][0] * cell[2][2] - cell[1][2] * cell[2][0])
        + cell[0][2] * (cell[1][0] * cell[2][1] - cell[1][1] * cell[2][0])
    )


def restricted_cell(cell: tuple[tuple[float, float, float], ...]) -> RestrictedCell:
    """Rotate the CFG cell vectors (H0 rows) into LAMMPS restricted-triclinic form."""
    determinant = _determinant(cell)
    if not math.isfinite(determinant) or determinant <= 0.0:
        raise ValueError("CFG cell must be non-singular and right-handed")
    a, b, c = (tuple(row) for row in cell)
    lx = _norm(a)
    if lx <= 0.0:
        raise ValueError("CFG first cell vector has zero length")
    a_hat = _scale(a, 1.0 / lx)
    xy = _dot(b, a_hat)
    b_perpendicular = _subtract(b, _scale(a_hat, xy))
    ly = _norm(b_perpendicular)
    if ly <= 0.0:
        raise ValueError("CFG first two cell vectors are collinear")
    b_hat = _scale(b_perpendicular, 1.0 / ly)
    xz = _dot(c, a_hat)
    yz = _dot(c, b_hat)
    lz_squared = _dot(c, c) - xz * xz - yz * yz
    if lz_squared <= 0.0:
        raise ValueError("CFG cell vectors are coplanar")
    lz = math.sqrt(lz_squared)
    if any(not math.isfinite(value) for value in (lx, ly, lz, xy, xz, yz)):
        raise ValueError("CFG cell conversion produced non-finite box parameters")
    origin = (
        -0.5 * (lx + xy + xz),
        -0.5 * (ly + yz),
        -0.5 * lz,
    )
    return RestrictedCell(lx=lx, ly=ly, lz=lz, xy=xy, xz=xz, yz=yz, origin=origin)


def read_lammps_data_cell(path: Path | str) -> RestrictedCell:
    """Read restricted-triclinic box dimensions and origin from a LAMMPS data file."""
    source = Path(path).resolve()
    bounds: dict[str, tuple[float, float]] = {}
    tilt = (0.0, 0.0, 0.0)
    with source.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith("Atoms"):
                break
            if match := _BOX_BOUNDS_RE.match(line):
                bounds[match.group(3)] = (
                    float(match.group(1)),
                    float(match.group(2)),
                )
            elif match := _BOX_TILT_RE.match(line):
                tilt = tuple(float(match.group(i)) for i in range(1, 4))
    if set(bounds) != {"x", "y", "z"}:
        raise ValueError(f"{source}: missing LAMMPS box bounds")
    values = [value for pair in bounds.values() for value in pair] + list(tilt)
    if any(not math.isfinite(value) for value in values):
        raise ValueError(f"{source}: LAMMPS box values must be finite")
    lx = bounds["x"][1] - bounds["x"][0]
    ly = bounds["y"][1] - bounds["y"][0]
    lz = bounds["z"][1] - bounds["z"][0]
    if min(lx, ly, lz) <= 0.0:
        raise ValueError(f"{source}: LAMMPS box lengths must be positive")
    return RestrictedCell(
        lx=lx,
        ly=ly,
        lz=lz,
        xy=tilt[0],
        xz=tilt[1],
        yz=tilt[2],
        origin=(bounds["x"][0], bounds["y"][0], bounds["z"][0]),
    )


def read_lammps_atomic_data(path: Path | str) -> dict[str, Any]:
    """Read the exact atom/cell subset needed from an atomic LAMMPS data file."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    atom_count: int | None = None
    atom_types: int | None = None
    bounds: dict[str, tuple[float, float]] = {}
    tilts = (0.0, 0.0, 0.0)
    atoms_header: int | None = None
    for index, raw in enumerate(lines):
        content = raw.split("#", 1)[0].strip()
        tokens = content.split()
        if len(tokens) == 2 and tokens[1] == "atoms":
            atom_count = int(tokens[0])
        elif len(tokens) == 3 and tokens[1:] == ["atom", "types"]:
            atom_types = int(tokens[0])
        elif len(tokens) >= 4 and tokens[2:] == ["xlo", "xhi"]:
            bounds["x"] = (float(tokens[0]), float(tokens[1]))
        elif len(tokens) >= 4 and tokens[2:] == ["ylo", "yhi"]:
            bounds["y"] = (float(tokens[0]), float(tokens[1]))
        elif len(tokens) >= 4 and tokens[2:] == ["zlo", "zhi"]:
            bounds["z"] = (float(tokens[0]), float(tokens[1]))
        elif len(tokens) == 6 and tokens[3:] == ["xy", "xz", "yz"]:
            tilts = (float(tokens[0]), float(tokens[1]), float(tokens[2]))
        if raw.strip().startswith("Atoms"):
            style = raw.partition("#")[2].strip().lower()
            if style and style != "atomic":
                raise ValueError(
                    f"{path}: Atoms section must use atom_style atomic, got {style!r}"
                )
            atoms_header = index
            break

    if atom_count is None or atom_count <= 0:
        raise ValueError(f"{path}: positive atom count is required")
    if atom_types is None or atom_types <= 0:
        raise ValueError(f"{path}: positive atom type count is required")
    if set(bounds) != {"x", "y", "z"}:
        raise ValueError(f"{path}: x/y/z box bounds are required")
    if atoms_header is None:
        raise ValueError(f"{path}: Atoms section is required")
    # Data sections may appear in either order, including Masses after Atoms.
    masses: dict[int, float] = {}
    in_masses = False
    for raw in lines:
        content = raw.split("#", 1)[0].strip()
        if content == "Masses":
            in_masses = True
            continue
        tokens = content.split()
        if not in_masses or not tokens:
            continue
        if tokens[0][0].isalpha():
            in_masses = False
            continue
        if len(tokens) != 2:
            raise ValueError(f"{path}: invalid Masses record {content!r}")
        type_id, mass = int(tokens[0]), float(tokens[1])
        if type_id <= 0 or type_id in masses or not math.isfinite(mass) or mass <= 0:
            raise ValueError(f"{path}: invalid or duplicate atom-type mass")
        masses[type_id] = mass
    cell_values = tuple(
        value
        for axis in "xyz"
        for value in bounds[axis]
    ) + tilts
    if any(not math.isfinite(value) for value in cell_values):
        raise ValueError(f"{path}: box values must be finite")
    lx = bounds["x"][1] - bounds["x"][0]
    ly = bounds["y"][1] - bounds["y"][0]
    lz = bounds["z"][1] - bounds["z"][0]
    if min(lx, ly, lz) <= 0.0:
        raise ValueError(f"{path}: box lengths must be positive")

    atoms: dict[int, tuple[float, float, float]] = {}
    types_by_id: dict[int, int] = {}
    for raw in lines[atoms_header + 1 :]:
        content = raw.split("#", 1)[0].strip()
        if not content:
            continue
        tokens = content.split()
        if len(tokens) < 5:
            if atoms:
                break
            continue
        try:
            atom_id = int(tokens[0])
            atom_type = int(tokens[1])
            x, y, z = (float(tokens[2]), float(tokens[3]), float(tokens[4]))
            if len(tokens) >= 8:
                ix, iy, iz = (int(tokens[5]), int(tokens[6]), int(tokens[7]))
            else:
                ix, iy, iz = (0, 0, 0)
        except ValueError:
            if atoms:
                break
            continue
        if atom_id <= 0 or atom_id in atoms:
            raise ValueError(f"{path}: atom IDs must be unique positive integers")
        if not 1 <= atom_type <= atom_types:
            raise ValueError(f"{path}: atom {atom_id} has an invalid type {atom_type}")
        xy, xz, yz = tilts
        unwrapped = (
            x + ix * lx + iy * xy + iz * xz,
            y + iy * ly + iz * yz,
            z + iz * lz,
        )
        if any(not math.isfinite(value) for value in unwrapped):
            raise ValueError(f"{path}: atom {atom_id} has non-finite coordinates")
        atoms[atom_id] = unwrapped
        types_by_id[atom_id] = atom_type
        if len(atoms) == atom_count:
            break
    if len(atoms) != atom_count:
        raise ValueError(
            f"{path}: header says {atom_count} atoms, read {len(atoms)} atomic records"
        )
    return {
        "atoms": atoms,
        "atom_types": atom_types,
        "types_by_id": types_by_id,
        "masses": masses,
        "cell": cell_values,
    }


@dataclass(frozen=True)
class LammpsDataReference:
    """Atom types, masses and the exact cell of the same atoms in LAMMPS data."""

    cell: RestrictedCell
    atom_types: int
    masses: dict[int, float]
    types_by_id: dict[int, int]


def read_lammps_reference(path: Path | str) -> LammpsDataReference:
    data = read_lammps_atomic_data(path)
    xlo, xhi, ylo, yhi, zlo, zhi, xy, xz, yz = data["cell"]
    cell = RestrictedCell(lx=xhi - xlo, ly=yhi - ylo, lz=zhi - zlo, xy=xy, xz=xz, yz=yz,
                          origin=(xlo, ylo, zlo))
    return LammpsDataReference(cell, int(data["atom_types"]), dict(data["masses"]),
                               dict(data["types_by_id"]))


def _printed_cell_matches(cfg: RestrictedCell, exact: RestrictedCell) -> bool:
    """True when ``cfg`` is ``exact`` itself or as LAMMPS prints H0 (%g)."""
    def same(value: float, reference: float) -> bool:
        return (math.isclose(value, reference, rel_tol=1.0e-12, abs_tol=1.0e-12)
                or math.isclose(value, float(f"{reference:g}"), rel_tol=1.0e-12, abs_tol=1.0e-12))
    return all(same(getattr(cfg, name), getattr(exact, name))
               for name in ("lx", "ly", "lz", "xy", "xz", "yz"))


def _cartesian(
    fractional: tuple[float, float, float], cell: RestrictedCell
) -> tuple[float, float, float]:
    s0, s1, s2 = fractional
    ox, oy, oz = cell.origin
    return (
        ox + s0 * cell.lx + s1 * cell.xy + s2 * cell.xz,
        oy + s1 * cell.ly + s2 * cell.yz,
        oz + s2 * cell.lz,
    )


def _atom_types(
    structure: CFGStructure,
    mass_override: float | None,
    reference: LammpsDataReference | None,
) -> tuple[dict[int, int], tuple[dict[str, object], ...], str]:
    """Return LAMMPS types by atom ID, the type table, and where the types came from."""
    atoms = structure.atoms
    if all(atom.atom_type is not None for atom in atoms):
        types = {atom.atom_id: int(atom.atom_type) for atom in atoms}
        source = "cfg_type_column"
        if reference is not None and any(reference.types_by_id.get(atom_id) != atom_type
                                         for atom_id, atom_type in types.items()):
            reference = None
    elif reference is not None and all(atom.atom_id in reference.types_by_id for atom in atoms):
        types = {atom.atom_id: reference.types_by_id[atom.atom_id] for atom in atoms}
        source = "source_lammps_data"
    else:
        # Element labels only: types that share a label and mass merge.
        keys = sorted({(atom.label, mass_override if mass_override is not None else atom.mass)
                       for atom in atoms})
        ids = {key: index + 1 for index, key in enumerate(keys)}
        types = {atom.atom_id: ids[(atom.label, mass_override if mass_override is not None else atom.mass)]
                 for atom in atoms}
        return types, tuple({"type": ids[key], "cfg_label": key[0], "mass": key[1]} for key in keys), "cfg_labels"

    count = max(max(types.values()), reference.atom_types if reference is not None else 0)
    present: dict[int, set[tuple[str, float]]] = {}
    for atom in atoms:
        present.setdefault(types[atom.atom_id], set()).add((atom.label, atom.mass))
    species = set().union(*present.values())
    table = []
    for type_id in range(1, count + 1):
        if type_id in present:
            labels = {label for label, _ in present[type_id]}
            masses = {mass for _, mass in present[type_id]}
            if len(labels) != 1 or (mass_override is None and len(masses) != 1):
                raise ValueError(f"{structure.source}: atom type {type_id} mixes element labels or masses")
            label, mass = next(iter(labels)), next(iter(masses))
        elif len(species) == 1:
            # One species: a type without atoms here is the same element.
            label, mass = next(iter(species))
        elif reference is not None and type_id in reference.masses:
            label, mass = None, reference.masses[type_id]
        else:
            raise ValueError(f"{structure.source}: atom type {type_id} has no atoms and no known mass")
        table.append({"type": type_id, "cfg_label": label,
                      "mass": mass_override if mass_override is not None else mass})
    return types, tuple(table), source


def convert_cfg_file(
    source: Path | str,
    output: Path | str,
    *,
    mass_override: float | None = None,
    box_origin: tuple[float, float, float] | None = None,
    reference: LammpsDataReference | None = None,
) -> CFGConversionResult:
    """Write ``atom_style atomic`` data for one extended CFG.

    ``reference`` is LAMMPS data of the same atoms: it supplies atom types when
    the CFG has no ``type`` column, and its exact cell and origin when the CFG
    cell is that box (exactly or as LAMMPS prints it). Otherwise ``box_origin``
    places the CFG cell; without either the box is centered at the origin.
    """
    structure = read_extended_cfg(source)
    if mass_override is not None and (
        not math.isfinite(mass_override) or mass_override <= 0.0
    ):
        raise ValueError("mass_override must be finite and positive")
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cell = restricted_cell(structure.cell)
    coordinate_origin = "box_center"
    cell_source = "cfg_header"
    if reference is not None and _printed_cell_matches(cell, reference.cell):
        cell = reference.cell
        coordinate_origin = "source_lammps_box"
        cell_source = "source_lammps_data"
    elif box_origin is not None:
        if len(box_origin) != 3 or any(
            not math.isfinite(float(value)) for value in box_origin
        ):
            raise ValueError("box_origin must contain three finite coordinates")
        cell = RestrictedCell(
            lx=cell.lx,
            ly=cell.ly,
            lz=cell.lz,
            xy=cell.xy,
            xz=cell.xz,
            yz=cell.yz,
            origin=tuple(float(value) for value in box_origin),
        )
        coordinate_origin = "source_lammps_box"

    types_by_id, type_map, type_source = _atom_types(structure, mass_override, reference)
    ox, oy, oz = cell.origin
    xhi, yhi, zhi = ox + cell.lx, oy + cell.ly, oz + cell.lz

    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(
            f"# Converted from {structure.source.name}; "
            f"coordinate origin: {coordinate_origin}\n\n"
        )
        handle.write(f"{len(structure.atoms)} atoms\n")
        handle.write(f"{len(type_map)} atom types\n\n")
        handle.write(f"{ox:.16g} {xhi:.16g} xlo xhi\n")
        handle.write(f"{oy:.16g} {yhi:.16g} ylo yhi\n")
        handle.write(f"{oz:.16g} {zhi:.16g} zlo zhi\n")
        if any(abs(value) > 1.0e-14 for value in (cell.xy, cell.xz, cell.yz)):
            handle.write(f"{cell.xy:.16g} {cell.xz:.16g} {cell.yz:.16g} xy xz yz\n")
        handle.write("\nMasses\n\n")
        for item in type_map:
            label = f" # {item['cfg_label']}" if item["cfg_label"] is not None else ""
            handle.write(f"{item['type']} {float(item['mass']):.16g}{label}\n")
        handle.write("\nAtoms # atomic\n\n")
        for atom in sorted(structure.atoms, key=lambda current: current.atom_id):
            x, y, z = _cartesian(atom.fractional, cell)
            ix, iy, iz = atom.image
            handle.write(
                f"{atom.atom_id} {types_by_id[atom.atom_id]} {x:.16g} {y:.16g} {z:.16g} "
                f"{ix} {iy} {iz}\n"
            )

    return CFGConversionResult(
        source=structure.source,
        output=output_path,
        atoms=len(structure.atoms),
        atom_types=len(type_map),
        source_cell=structure.cell,
        restricted_cell=cell,
        coordinate_origin=coordinate_origin,
        type_map=type_map,
        type_source=type_source,
        cell_source=cell_source,
    )
