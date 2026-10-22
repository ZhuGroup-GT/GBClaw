"""
----------------------------------------------------
Original code from: R. Hadian, B. Grabowski, J. Neugebauer @ Max-Planck-Institut fuer Eisenforschung (https://github.com/oekosheri/GB_code)
Modified by: Yazhuo Liu @ Georgia Tech
----------------------------------------------------
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
----------------------------------------------------
For Human Users:
----------------------------------------------------
This module is a collection of functions that produce CSL properties for the Structure worker.
When run from the terminal, the code runs in two modes.

 First mode:
  'csl_generator.py sigmas u v w basis [limit]' ----->  Where the u v w are the
  indices of the rotation axis and basis is fcc, bcc, diamond, or sc. The limit
  is the maximum Sigma of interest. Sigma is always validated for that basis.
  (the limit by default: 100)

 Second mode:
 'csl_generator.py planes u v w basis sigma [limit] [m n]' -----> Where basis is
  either fcc, bcc, diamond or sc. You read the sigma of interest from the first
  mode run. The limit here refers to CSL GB inclinations. The bigger the limit,
  the higher the indices of CSL planes.
  (the limit by default: 2)
----------------------------------------------------
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
----------------------------------------------------
For Agents:
----------------------------------------------------
This module can be used from the CLI or imported as a Python library.

CLI (two modes):

  Mode 1 — list possible Sigma values for a rotation axis:
    python src/domains/structure/csl/generator.py sigmas <u> <v> <w> <basis> [limit]

    Parameters:
      u, v, w  — integer Miller indices of the rotation axis (e.g. 1 1 0).
                 Common factors are removed automatically (e.g. 2 2 0 -> 1 1 0).
      basis    — crystal structure: fcc, bcc, diamond, or sc.
      limit    — (optional) maximum Sigma to search; default 50.

    Examples:
      python src/domains/structure/csl/generator.py sigmas 1 1 0 fcc
      python src/domains/structure/csl/generator.py sigmas 1 1 0 bcc 50

    Output: every Sigma/Theta/m/n branch for the given axis. Sigma can repeat.

  Mode 2 — list CSL grain-boundary planes for a chosen Sigma:
    python src/domains/structure/csl/generator.py planes <u> <v> <w> <basis> <sigma> [limit] [m n]

    Parameters:
      u, v, w  — same rotation-axis indices as in Mode 1.
      basis    — crystal structure: fcc, bcc, diamond, or sc.
      sigma    — target CSL Sigma (pick from Mode 1 output), e.g. 3, 5, 7.
      limit    — (optional) upper bound on GB plane indices (inclination search
                 range); larger values include higher-index CSL planes.
                 Default 2.
      m, n     — exact branch from Mode 1; required when Sigma has multiple
                 branches on the selected axis.

    Examples:
      python src/domains/structure/csl/generator.py planes 1 1 0 bcc 3
      python src/domains/structure/csl/generator.py planes 1 1 0 fcc 3 2
      python src/domains/structure/csl/generator.py planes 1 1 0 fcc 33 2 5 2

    Output columns: GB1, GB2 (plane normals for grain 1 and 2), Type
    (Twist / Tilt / Symmetric Tilt / Mixed), Number of Atoms (orthogonal cell).

Python import (preferred for programmatic use):
  from domains.structure.csl import generator as csl
  import pandas as pd

  axis = [1, 1, 0]
  sigma = 3
  lim = 2
  basis = "bcc"

  # Resolve (m, n) for the target Sigma
  _, m, n = csl.resolve_csl_rotation(axis, basis, sigma)

  # Unfiltered GB plane list (may contain symmetry-equivalent duplicates)
  V1, V2, MeanPlanes, Gb = csl.Create_Possible_GB_Plane_List(axis, m, n, lim)
  df = pd.DataFrame({"GB1": list(V1), "GB2": list(V2), "Type": Gb})
  df.head()

  # Filtered list: fixed-axis cubic symmetry dedup, smallest atom count per key
  V1, V2, Gb, Num = csl.Create_Filtered_GB_Plane_List(axis, basis, m, n, lim)
  df_filtered = pd.DataFrame(
      {"GB1": list(V1), "GB2": list(V2), "Type": Gb, "NumAtoms": list(Num)}
  )
  df_filtered.head()

Notes:
  - axis / uvw: rotation axis indices, e.g. [1, 1, 0] or [1, 1, 1]
  - basis: one of "fcc", "bcc", "diamond", "sc" (required for Sigma enumeration)
  - lim: upper limit on plane indices; larger values give more GB inclinations
  - Create_Possible_GB_Plane_List returns (V1, V2, MeanPlanes, Type)
  - Create_Filtered_GB_Plane_List returns (V1, V2, Type, NumAtoms)
----------------------------------------------------
"""


import sys
from itertools import combinations
from pathlib import Path
from math import degrees, atan, sqrt, pi, ceil, cos, acos, sin, gcd
import numpy as np
from numpy import dot, cross
from numpy.linalg import det, norm, inv

_SRC_ROOT = Path(__file__).resolve().parents[3]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))


def _parallel_map():
    """Import the shared pool in package and direct-CLI execution modes."""
    from core.execution.process_pool import parallel_map
    return parallel_map


def get_cubic_sigma(uvw, m, n=1):
    """
    Primitive-cubic analytical Sigma used by the legacy cell construction.

    This helper does not validate a rotation against a requested crystal
    lattice. Public enumeration must use :func:`get_lattice_sigma` with an
    explicit basis.

    Formula based on the book:
    'Interfaces in crystalline materials',
     Sutton and Balluffi, clarendon press, 1996.
     generates possible sigma values.
    arguments:
    uvw -- the axis
    m,n -- two integers (n by default 1)

    """
    u, v, w = uvw
    sqsum = u*u + v*v + w*w
    sigma = m*m + n*n * sqsum
    while sigma != 0 and sigma % 2 == 0:
        sigma /= 2
    return sigma if sigma > 1 else None


_BRAVAIS_PRIMITIVE_BASES = {
    "sc": np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=object),
    "fcc": np.array([[0, 1, 1], [1, 0, 1], [1, 1, 0]], dtype=object),
    "bcc": np.array([[1, 1, -1], [1, -1, 1], [-1, 1, 1]], dtype=object),
    # Diamond has an FCC Bravais lattice plus a motif. CSL rotations are
    # validated against the translational (FCC) lattice here.
    "diamond": np.array([[0, 1, 1], [1, 0, 1], [1, 1, 0]], dtype=object),
}


def _det3(matrix) -> int:
    """Return the exact determinant of a 3x3 integer matrix."""
    a = matrix
    return int(
        a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
        - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
        + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
    )


def _adjugate3(matrix) -> np.ndarray:
    """Return the exact adjugate of a 3x3 integer matrix."""
    a = matrix
    cofactors = np.array(
        [
            [
                a[1][1] * a[2][2] - a[1][2] * a[2][1],
                -(a[1][0] * a[2][2] - a[1][2] * a[2][0]),
                a[1][0] * a[2][1] - a[1][1] * a[2][0],
            ],
            [
                -(a[0][1] * a[2][2] - a[0][2] * a[2][1]),
                a[0][0] * a[2][2] - a[0][2] * a[2][0],
                -(a[0][0] * a[2][1] - a[0][1] * a[2][0]),
            ],
            [
                a[0][1] * a[1][2] - a[0][2] * a[1][1],
                -(a[0][0] * a[1][2] - a[0][2] * a[1][0]),
                a[0][0] * a[1][1] - a[0][1] * a[1][0],
            ],
        ],
        dtype=object,
    )
    return cofactors.T


def _quaternion_rotation_numerator(uvw, m, n) -> tuple[np.ndarray, int]:
    """Return the exact rational rotation as ``numerator / denominator``."""
    u, v, w = (int(value) for value in uvw)
    a, b, c, d = int(m), int(n) * u, int(n) * v, int(n) * w
    denominator = a * a + b * b + c * c + d * d
    numerator = np.array(
        [
            [
                a * a + b * b - c * c - d * d,
                2 * (b * c - a * d),
                2 * (b * d + a * c),
            ],
            [
                2 * (b * c + a * d),
                a * a - b * b + c * c - d * d,
                2 * (c * d - a * b),
            ],
            [
                2 * (b * d - a * c),
                2 * (c * d + a * b),
                a * a - b * b - c * c + d * d,
            ],
        ],
        dtype=object,
    )
    return numerator, denominator


def get_lattice_sigma(uvw, basis, m, n=1):
    """Compute the CSL index from the requested cubic Bravais lattice.

    The rotation is transformed into the primitive basis of ``sc``, ``fcc``,
    ``bcc``, or the FCC Bravais lattice underlying ``diamond``. The coincidence
    index is then computed exactly from the congruence kernel of that rational
    rotation; no Sigma result is borrowed from another crystal basis.
    """
    basis_norm = str(basis).strip().lower()
    if basis_norm not in _BRAVAIS_PRIMITIVE_BASES:
        raise ValueError(
            f"Unsupported basis {basis!r}; choose from "
            f"{', '.join(sorted(_BRAVAIS_PRIMITIVE_BASES))}."
        )
    axis = reduce_miller_indices(uvw)
    rotation_numerator, rotation_denominator = _quaternion_rotation_numerator(
        axis, m, n
    )
    primitive = _BRAVAIS_PRIMITIVE_BASES[basis_norm]
    primitive_det = _det3(primitive)
    transformed_numerator = (
        _adjugate3(primitive) @ rotation_numerator @ primitive
    )
    transformed_denominator = primitive_det * rotation_denominator
    if transformed_denominator < 0:
        transformed_numerator = -transformed_numerator
        transformed_denominator = -transformed_denominator

    common = abs(int(transformed_denominator))
    for value in transformed_numerator.flat:
        common = gcd(common, abs(int(value)))
    denominator = int(transformed_denominator // common)
    numerator = np.asarray(transformed_numerator // common, dtype=object)

    # L = numerator*Z^3 + denominator*Z^3. The gcd of the maximal minors
    # gives [Z^3:L], so |im(numerator mod denominator)| is d^3/[Z^3:L].
    generators = np.concatenate(
        (numerator, denominator * np.eye(3, dtype=object)), axis=1
    )
    lattice_index = 0
    for columns in combinations(range(6), 3):
        minor = generators[:, columns]
        lattice_index = gcd(lattice_index, abs(_det3(minor)))
    if lattice_index == 0:
        return None
    sigma = denominator**3 // lattice_index
    return int(sigma) if sigma > 1 else None


def get_cubic_theta(uvw, m, n=1):
    """
    generates possible theta values.
    arguments:
    uvw -- the axis
    m,n -- two integers (n by default 1)
    """
    u, v, w = uvw
    sqsum = u*u + v*v + w*w
    if m > 0:
        return 2 * atan(sqrt(sqsum) * n / m)
    else:
        return pi


def get_theta_m_n_list(uvw, basis, sigma):
    """
    Finds integers m and n lists that match the input sigma.
    """
    if sigma == 1:
        return [(0., 0., 0.)]
    thetas = []
    max_m = int(ceil(sqrt(4*sigma)))

    for m in range(1, max_m):
        for n in range(1, max_m):
            if gcd(m, n) != 1:
                continue
            s = get_lattice_sigma(uvw, basis, m, n)
            if s == sigma:
                theta = get_cubic_theta(uvw, m, n)
                thetas.append((theta, m, n))
    thetas.sort(key=lambda x: x[0])
    return thetas


def fixed_axis_fundamental_max_angle(uvw) -> float:
    """Return the fixed-axis fundamental half-period in radians.

    Cubic rotations about a symmetry axis are periodic modulo the smallest
    non-identity proper cubic symmetry that keeps the *directed* axis fixed.
    Swapping the two grains identifies opposite signs of the residual angle,
    leaving half that period as the fundamental interval.  Thus <100>, <110>,
    and <111> axes use 45, 90, and 60 degrees, respectively; a generic axis
    without a non-trivial stabilizer uses 180 degrees.
    """

    axis = reduce_miller_indices(uvw).astype(float)
    axis /= norm(axis)
    stabilizer_angles = []
    for symmetry in _cubic_symmetry_matrices():
        mapped = dot(symmetry, axis)
        if norm(mapped - axis) > 1.0e-8:
            continue
        cosine = max(-1.0, min(1.0, 0.5 * (float(np.trace(symmetry)) - 1.0)))
        angle = acos(cosine)
        if angle > 1.0e-8:
            stabilizer_angles.append(angle)
    period = min(stabilizer_angles) if stabilizer_angles else 2.0 * pi
    return 0.5 * period


def get_fundamental_theta_m_n_list(uvw, basis, sigma):
    """Return every distinct basis-valid rotation branch for one Sigma.

    ``get_theta_m_n_list`` intentionally exposes all positive quaternion
    parameterizations, including rotations outside the fixed-axis fundamental
    interval.  Public CSL workflows need all *physical branches* inside that
    interval, rather than the historical ``matches[0]`` shortcut.
    """

    maximum = fixed_axis_fundamental_max_angle(uvw)
    matches = [
        (theta, int(m), int(n))
        for theta, m, n in get_theta_m_n_list(uvw, basis, sigma)
        if theta <= maximum + 1.0e-10
    ]
    unique = []
    for theta, m, n in matches:
        if any(abs(theta - old_theta) <= 1.0e-10 for old_theta, _, _ in unique):
            continue
        unique.append((theta, m, n))
    return unique


def resolve_csl_rotation(
    uvw,
    basis,
    sigma,
    *,
    rotation_m=None,
    rotation_n=None,
):
    """Resolve one exact ``(theta, m, n)`` branch or reject ambiguity.

    Bare Sigma input remains convenient when the axis/basis has one branch.
    When several branches share a Sigma, callers must pass both quaternion
    integers returned by :func:`list_csl_rotations_for_axis`.
    """

    matches = get_fundamental_theta_m_n_list(uvw, basis, int(sigma))
    if not matches:
        raise ValueError(
            f"No basis-valid CSL rotation for basis={basis}, "
            f"axis={reduce_miller_indices(uvw).tolist()}, Sigma={int(sigma)}."
        )
    if (rotation_m is None) != (rotation_n is None):
        raise ValueError("rotation_m and rotation_n must be supplied together.")
    if rotation_m is not None:
        requested = (int(rotation_m), int(rotation_n))
        for theta, m, n in matches:
            if (m, n) == requested:
                return theta, m, n
        choices = ", ".join(
            f"(m={m}, n={n}, theta={degrees(theta):.8f} deg)"
            for theta, m, n in matches
        )
        raise ValueError(
            f"Rotation (m={requested[0]}, n={requested[1]}) is not a "
            f"fundamental branch of Sigma {int(sigma)}; choose {choices}."
        )
    if len(matches) > 1:
        choices = ", ".join(
            f"(m={m}, n={n}, theta={degrees(theta):.8f} deg)"
            for theta, m, n in matches
        )
        raise ValueError(
            f"Sigma {int(sigma)} is ambiguous for basis={basis}, "
            f"axis={reduce_miller_indices(uvw).tolist()}; choose one rotation "
            f"branch by passing rotation_m and rotation_n: {choices}."
        )
    return matches[0]


def list_csl_rotations_for_axis(uvw, basis, limit):
    """Return ``(sigma, theta, m, n)`` for every fundamental branch."""

    uvw = np.array(uvw, dtype=int)
    tasks = [(uvw.tolist(), basis, sigma) for sigma in range(2, limit + 1)]
    rotations = []
    for task in tasks:
        result = _sigma_lookup_worker(task)
        if result:
            rotations.extend(result)
    rotations.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
    return rotations


def print_list(uvw, basis, limit):
    """
    prints a list of smallest sigmas/angles for a given axis(uvw).
    """
    for sigma, theta, m, n in list_csl_rotations_for_axis(uvw, basis, limit):
        print(
            "Sigma:   {0:3d}  Theta:  {1:5.2f}  m: {2:3d}  n: {3:3d}"
            .format(sigma, degrees(theta), m, n)
        )


def rot(a, Theta):
    """
    produces a rotation matrix.
    arguments:
    a -- an axis
    Theta -- an angle
    """
    c = cos(Theta)
    s = sin(Theta)
    a = a / norm(a)
    ax, ay, az = a
    return np.array([[c + ax * ax * (1 - c), ax * ay * (1 - c) - az * s,
                      ax * az * (1 - c) + ay * s],
                    [ay * ax * (1 - c) + az * s, c + ay * ay * (1 - c),
                        ay * az * (1 - c) - ax * s],
                     [az * ax * (1 - c) - ay * s, az * ay * (1 - c) + ax * s,
                      c + az * az * (1 - c)]])


# Helpful Functions:
# -------------------#

def integer_array(A, tol=1e-7):
    """
    returns True if an array is ineteger.
    """
    return np.all(abs(np.round(A) - A) < tol)


def angv(a, b):
    """
    returns the angle between two vectors.
    """
    ang = acos(np.round(dot(a, b)/norm(a)/norm(b), 8))
    return round(degrees(ang), 7)


def ang(a, b):
    """
    returns the cos(angle) between two vectors.
    """
    ang = np.round(dot(a, b)/norm(a)/norm(b), 7)
    return abs(ang)


def CommonDivisor(a):
    """
    returns the common factor of vector a and the reduced vector.
    """
    CommFac = []
    a = np.array(a)
    for i in range(2, 100):
        while (a[0] % i == 0 and a[1] % i == 0 and a[2] % i == 0):
            a = a / i
            CommFac.append(i)
    return(a.astype(int), (abs(np.prod(CommFac))))


def reduce_miller_indices(plane):
    """Return primitive integer Miller indices while preserving their sign.

    Miller normals that differ only by a nonzero integer scale describe the
    same crystallographic plane.  Use an exact gcd instead of ``CommonDivisor``
    so factors greater than 99 are reduced as well.
    """
    values = np.round(np.asarray(plane)).astype(int)
    if values.shape != (3,):
        raise ValueError("Miller indices must contain exactly three integers.")
    common = 0
    for component in values:
        common = gcd(common, abs(int(component)))
    if common == 0:
        raise ValueError("Miller indices must not all be zero.")
    return values // common


def _cubic_symmetry_matrices():
    """
    returns the cubic symmetry matrices.
    """
    Sym = np.zeros([24, 3, 3])
    Sym[0, :] = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    Sym[1, :] = [[1, 0, 0], [0, -1, 0], [0, 0, -1]]
    Sym[2, :] = [[-1, 0, 0], [0, 1, 0], [0, 0, -1]]
    Sym[3, :] = [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]
    Sym[4, :] = [[0, -1, 0], [-1, 0, 0], [0, 0, -1]]
    Sym[5, :] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    Sym[6, :] = [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]
    Sym[7, :] = [[0, 1, 0], [1, 0, 0], [0, 0, -1]]
    Sym[8, :] = [[-1, 0, 0], [0, 0, -1], [0, -1, 0]]
    Sym[9, :] = [[-1, 0, 0], [0, 0, 1], [0, 1, 0]]
    Sym[10, :] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    Sym[11, :] = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]
    Sym[12, :] = [[0, 1, 0], [0, 0, 1], [1, 0, 0]]
    Sym[13, :] = [[0, 1, 0], [0, 0, -1], [-1, 0, 0]]
    Sym[14, :] = [[0, -1, 0], [0, 0, 1], [-1, 0, 0]]
    Sym[15, :] = [[0, -1, 0], [0, 0, -1], [1, 0, 0]]
    Sym[16, :] = [[0, 0, 1], [1, 0, 0], [0, 1, 0]]
    Sym[17, :] = [[0, 0, 1], [-1, 0, 0], [0, -1, 0]]
    Sym[18, :] = [[0, 0, -1], [1, 0, 0], [0, -1, 0]]
    Sym[19, :] = [[0, 0, -1], [-1, 0, 0], [0, 1, 0]]
    Sym[20, :] = [[0, 0, -1], [0, -1, 0], [-1, 0, 0]]
    Sym[21, :] = [[0, 0, -1], [0, 1, 0], [1, 0, 0]]
    Sym[22, :] = [[0, 0, 1], [0, -1, 0], [1, 0, 0]]
    Sym[23, :] = [[0, 0, 1], [0, 1, 0], [-1, 0, 0]]
    return Sym


def _reduced_plane_key(plane):
    """
    returns a sign-independent reduced tuple for a plane normal.
    """
    plane = reduce_miller_indices(plane)

    for component in plane:
        if component != 0:
            if component < 0:
                plane = -plane
            break
    return tuple(int(component) for component in plane)


def _axis_preserving_symmetries(uvw):
    """
    returns cubic symmetries that keep the rotation axis fixed up to sign.
    """
    uvw_key = _reduced_plane_key(uvw)
    return [Sym for Sym in _cubic_symmetry_matrices()
            if _reduced_plane_key(dot(Sym, uvw)) == uvw_key]


def _canonical_gb_pair_key(gb1, gb2, uvw):
    """
    returns a canonical key for a GB pair under fixed-axis cubic symmetry.
    """
    keys = []
    for Sym in _axis_preserving_symmetries(uvw):
        plane1 = _reduced_plane_key(dot(Sym, gb1))
        plane2 = _reduced_plane_key(dot(Sym, gb2))
        keys.append((plane1, plane2))
        keys.append((plane2, plane1))
    return min(keys)


def SmallestInteger(a):
    """
    returns the smallest multiple integer to make an integer array.
    """
    a = np.array(a)
    for i in range(1, 200):
        testV = i * a
        if integer_array(testV):
            break
    return (testV, i) if integer_array(testV) else None


def SymmEquivalent(arr):
    """
    returns cubic symmetric eqivalents of the given 2 dimensional vector.
    """
    arr = np.atleast_2d(arr)
    Result = []
    Sym = _cubic_symmetry_matrices()
    for i in range(len(Sym)):
        for j in range(len(arr)):
            Result.append(dot(Sym[i, :], arr[j]))
    Result = np.array(Result)
    return np.unique(Result, axis=0)


def Create_Possible_GB_Plane_List(uvw, m=5, n=1, lim=5):
    """
    generates GB planes and specifies the character.

    arguments:
    uvw -- axis of rotation.
    m,n -- the two necessary integers
    lim -- upper limit for the plane indices

    """
    uvw = np.array(uvw)
    Theta = get_cubic_theta(uvw, m, n)
    Sigma = get_cubic_sigma(uvw, m, n)
    R1 = rot(uvw, Theta)

    # List and character of possible GB planes:
    x = np.arange(-lim, lim + 1, 1)
    y = x
    z = x
    indice = (np.stack(np.meshgrid(x, y, z)).T).reshape(len(x) ** 3, 3)
    indice_0 = indice[np.where(np.sum(abs(indice), axis=1) != 0)]
    indice_0 = indice_0[np.argsort(norm(indice_0, axis=1))]

    # extract the minimal cell:
    Min_1, Min_2 = Create_minimal_cell_Method_1(Sigma, uvw, R1)
    V1 = np.zeros([len(indice_0), 3])
    V2 = np.zeros([len(indice_0), 3])
    GBtype = []
    tol = 0.001
    # Mirrorplanes cubic symmetry
    MP = np.array([[1, 0, 0],
                   [0, 1, 0],
                   [0, 0, 1],
                   [1, 1, 0],
                   [0, 1, 1],
                   [1, 0, 1],
                   ], dtype='float')
    # Find GB plane coordinates:
    for i in range(len(indice_0)):
        if CommonDivisor(indice_0[i])[1] <= 1:
            V1[i, :] = (indice_0[i, 0] * Min_1[:, 0] +
                        indice_0[i, 1] * Min_1[:, 1] +
                        indice_0[i, 2] * Min_1[:, 2])
            V2[i, :] = (indice_0[i, 0] * Min_2[:, 0] +
                        indice_0[i, 1] * Min_2[:, 1] +
                        indice_0[i, 2] * Min_2[:, 2])

    V1 = (V1[~np.all(V1 == 0, axis=1)]).astype(int)
    V2 = (V2[~np.all(V2 == 0, axis=1)]).astype(int)
    MeanPlanes = (V1 + V2) / 2

    # Check the type of GB plane: Symmetric tilt, tilt, twist
    for i in range(len(V1)):
        if ang(V1[i], uvw) < tol:

            for j in range(len(SymmEquivalent(MP))):
                if 1 - ang(MeanPlanes[i], SymmEquivalent(MP)[j]) < tol:
                    GBtype.append('Symmetric Tilt')
                    break
            else:
                GBtype.append('Tilt')
        elif 1 - ang(V1[i], uvw) < tol:
            GBtype.append('Twist')
        else:
            GBtype.append('Mixed')

    return (V1, V2, MeanPlanes, GBtype)


def Create_minimal_cell_Method_1(sigma, uvw, R):
    """
    finds Minimal cell by means of a numerical search.
    (An alternative analytical method can be used too).
    arguments:
    sigma -- gb sigma
    uvw -- rotation axis
    R -- rotation matrix
    """
    uvw = np.array(uvw)
    MiniCell_1 = np.zeros([3, 3])
    MiniCell_1[:, 2] = uvw

    lim = 20
    x = np.arange(-lim, lim + 1, 1)
    y = x
    z = x
    indice = (np.stack(np.meshgrid(x, y, z)).T).reshape(len(x) ** 3, 3)

    # remove 0 vectors and uvw from the list
    indice_0 = indice[np.where(np.sum(abs(indice), axis=1) != 0)]
    condition1 = ((abs(dot(indice_0, uvw) / norm(indice_0, axis=1) /
                       norm(uvw))).round(7))
    indice_0 = indice_0[np.where(condition1 != 1)]

    if MiniCell_search(indice_0, MiniCell_1, R, sigma):

        M1, M2 = MiniCell_search(indice_0, MiniCell_1, R, sigma)
        return (M1, M2)
    else:
        return None


def MiniCell_search(indices, MiniCell_1, R, sigma):

    tol = 0.001
    # norm1 = norm(indices, axis=1)
    newindices = dot(R, indices.T).T
    nn = indices[np.all(abs(np.round(newindices) - newindices) < 1e-6, axis=1)]
    TestVecs = nn[np.argsort(norm(nn, axis=1))]
    # print(len(indices), len(TestVecs),TestVecs[:20])

    Found = False
    count = 0
    while (not Found) and count < len(TestVecs) - 1:
        if 1 - ang(TestVecs[count], MiniCell_1[:, 2]) > tol:
            # and  (ang(TestVecs[i],uvw) > tol):
            MiniCell_1[:, 1] = (TestVecs[count])
            count += 1
            for j in range(len(TestVecs)):
                if (1 - ang(TestVecs[j], MiniCell_1[:, 2]) > tol) and (
                        1 - ang(TestVecs[j], MiniCell_1[:, 1]) > tol):
                    if (ang(TestVecs[j],
                            cross(MiniCell_1[:, 2], MiniCell_1[:, 1])) > tol):
                        #  The condition that the third vector can not be
                        #  normal to any other two.
                        #  and (ang(TestVecs[i],uvw)> tol) and
                        # (ang(TestVecs[i],MiniCell[:,1])> tol)):
                        MiniCell_1[:, 0] = (TestVecs[j]).astype(int)
                        Det1 = abs(round(det(MiniCell_1), 5))
                        MiniCell_1 = (MiniCell_1).astype(int)
                        MiniCell_2 = ((np.round(dot(R, MiniCell_1), 7))
                                      .astype(int))
                        Det2 = abs(round(det(MiniCell_2), 5))

                        if ((abs(Det1 - sigma)) < tol and
                                (abs(Det2 - sigma)) < tol):
                            Found = True
                            break
    if Found:
        return MiniCell_1, MiniCell_2
    else:
        return Found


def Basis(basis):
    """
    defines the basis.
    """
    # Cubic basis
    if str(basis) == 'fcc':
        basis = np.array([[0, 0, 0],
                          [0.5, 0, 0.5],
                          [0.5, 0.5, 0],
                          [0, 0.5, 0.5]], dtype=float)
    elif str(basis) == 'bcc':
        basis = np.array([[0, 0, 0],
                          [0.5, 0.5, 0.5]], dtype=float)
    elif str(basis) == 'sc':
        basis = np.eye(3)

    elif str(basis) == 'diamond':
        basis = np.array([[0, 0, 0],
                          [0.5, 0, 0.5],
                          [0.5, 0.5, 0],
                          [0, 0.5, 0.5],
                          [0.25, 0.25, 0.25],
                          [0.75, 0.25, 0.75],
                          [0.75, 0.75, 0.25],
                          [0.25, 0.75, 0.75]], dtype=float)
    else:
        print('Sorry! For now only works for cubic lattices ...')
        sys.exit()

    return basis


def Find_Orthogonal_cell(basis, uvw, m, n, GB1):
    """
    finds Orthogonal cells from the CSL minimal cells.
    arguments:
    basis -- lattice basis
    uvw -- rotation axis
    m,n -- two necessary integers
    GB1 -- input plane orientation
    """
    # Changeable limit
    lim = 15
    uvw = np.array(uvw)
    Theta = get_cubic_theta(uvw, m, n)
    Sigma = get_cubic_sigma(uvw, m, n)
    R = rot(uvw, Theta)
    GB2 = np.round((dot(R, GB1)), 6)
    x = np.arange(-lim, lim + 1, 1)
    y = x
    z = x
    indice = (np.stack(np.meshgrid(x, y, z)).T).reshape(len(x) ** 3, 3)
    indice_0 = indice[np.where(np.sum(abs(indice), axis=1) != 0)]
    indice_0 = indice_0[np.argsort(norm(indice_0, axis=1))]
    OrthoCell_1 = np.zeros([3, 3])
    OrthoCell_1[:, 0] = np.array(GB1)
    OrthoCell_2 = np.zeros([3, 3])
    OrthoCell_2[:, 0] = np.array(GB2)
    # extract the minimal cells:
    Min_1, Min_2 = Create_minimal_cell_Method_1(Sigma, uvw, R)
    # Find Ortho vectors:
    tol = 0.001
    if ang(OrthoCell_1[:, 0], uvw) < tol:
        OrthoCell_1[:, 1] = uvw
        OrthoCell_2[:, 1] = uvw
    else:

        for i in range(len(indice_0)):

            v1 = (indice_0[i, 0] * Min_1[:, 0] +
                  indice_0[i, 1] * Min_1[:, 1] +
                  indice_0[i, 2] * Min_1[:, 2])
            v2 = (indice_0[i, 0] * Min_2[:, 0] +
                  indice_0[i, 1] * Min_2[:, 1] +
                  indice_0[i, 2] * Min_2[:, 2])
            if ang(v1, OrthoCell_1[:, 0]) < tol:
                OrthoCell_1[:, 1] = v1
                OrthoCell_2[:, 1] = v2
                break
    OrthoCell_1[:, 2] = np.cross(OrthoCell_1[:, 0], OrthoCell_1[:, 1])
    OrthoCell_2[:, 2] = np.cross(OrthoCell_2[:, 0], OrthoCell_2[:, 1])

    if (CommonDivisor(OrthoCell_1[:, 2])[1] ==
            CommonDivisor(OrthoCell_2[:, 2])[1]):
        OrthoCell_1[:, 2] = CommonDivisor(OrthoCell_1[:, 2])[0]
        OrthoCell_2[:, 2] = CommonDivisor(OrthoCell_2[:, 2])[0]
    # OrthoCell_1 = OrthoCell_1
    # OrthoCell_2 = OrthoCell_2
    # Test
    # OrthoCell_3 = (dot(R, OrthoCell_1))
    Volume_1 = (round(det(OrthoCell_1), 5))
    Volume_2 = (round(det(OrthoCell_2), 5))
    Num = Volume_1 * len(Basis(basis)) * 2

    if Volume_1 == Volume_2:
        OrthoCell_1 = OrthoCell_1.astype(float)
        OrthoCell_2 = OrthoCell_2.astype(float)

        if basis == 'sc' or basis == 'diamond':

            return ((OrthoCell_1.astype(float),
                     OrthoCell_2.astype(float), Num.astype(int)))

        elif basis == 'fcc' or basis == 'bcc':
            ortho1, ortho2 = Ortho_fcc_bcc(basis, OrthoCell_1, OrthoCell_2)
            Volume_1 = (round(det(ortho1), 5))
            Num = Volume_1 * len(Basis(basis)) * 2
            return (ortho1, ortho2, Num.astype(int))

    else:
        return None


_TILT_TOL = 0.001


def _reduce_int_vector(vec) -> tuple[int, int, int]:
    reduced = reduce_miller_indices(vec)
    return tuple(int(component) for component in reduced)


def _orientation_from_ortho_cell(
    ortho_cell: np.ndarray,
) -> tuple[dict[str, list[int]], dict[str, tuple[int, int, int]]]:
    """Map orthogonal cell columns to gb_minimize orientation (y=normal, z=axis, x=cross)."""
    raw = {}
    for label, column in {"x": 2, "y": 0, "z": 1}.items():
        integer_direction = SmallestInteger(ortho_cell[:, column])
        if integer_direction is None:
            raise ValueError(
                f"Could not convert {label}-orientation to integer indices: "
                f"{ortho_cell[:, column].tolist()}"
            )
        raw[label] = [int(round(value)) for value in integer_direction[0]]
    reduced = {
        "x": _reduce_int_vector(raw["x"]),
        "y": _reduce_int_vector(raw["y"]),
        "z": _reduce_int_vector(raw["z"]),
    }
    return raw, reduced


def tilt_gb_orthogonal_orientation(
    uvw,
    basis: str,
    sigma: int,
    gb1,
    *,
    a0: float = 2.8553,
    rotation_m: int | None = None,
    rotation_n: int | None = None,
) -> dict:
    """Derive gb_minimize orientations for a CSL tilt grain boundary.

    Uses ``Find_Orthogonal_cell`` and maps columns to simulation axes:
    y = GB normal, z = tilt axis, x = in-plane direction (cross product).
    """
    uvw_arr = reduce_miller_indices(uvw)
    gb1_arr = reduce_miller_indices(gb1)

    theta, m, n = resolve_csl_rotation(
        uvw_arr,
        basis,
        int(sigma),
        rotation_m=rotation_m,
        rotation_n=rotation_n,
    )
    rotation = rot(uvw_arr, theta)

    if ang(gb1_arr, uvw_arr) >= _TILT_TOL:
        if 1 - ang(gb1_arr, uvw_arr) < _TILT_TOL:
            raise ValueError(
                f"GB plane {gb1_arr.tolist()} is a twist boundary (parallel to axis). "
                "Only tilt GBs are supported."
            )
        raise ValueError(
            f"GB plane {gb1_arr.tolist()} is not a tilt boundary for axis {uvw_arr.tolist()}."
        )

    # Public tools expose primitive Miller indices, but the paired CSL plane
    # normals must share an integer scale before constructing a common periodic
    # cell. Recover that scale deterministically instead of requiring the model
    # (or user) to pass a non-primitive normal such as [-11, -11, 11].
    scaled_partner = SmallestInteger(dot(rotation, gb1_arr))
    if scaled_partner is None:
        raise ValueError(
            f"Could not find a CSL-compatible integer scale for GB plane "
            f"{gb1_arr.tolist()}."
        )
    gb1_csl_scale = int(scaled_partner[1])
    gb1_csl_arr = gb1_arr * gb1_csl_scale
    gb2_csl_arr = np.round(scaled_partner[0]).astype(int)

    ortho = Find_Orthogonal_cell(basis, uvw_arr, m, n, gb1_csl_arr)
    if ortho is None:
        raise ValueError(
            f"Could not build orthogonal cell for axis {uvw_arr.tolist()}, "
            f"Sigma {sigma}, primitive GB plane {gb1_arr.tolist()} "
            f"(CSL scale {gb1_csl_scale})."
        )

    ortho_top, ortho_bot, atom_estimate = ortho
    top_raw, top_reduced = _orientation_from_ortho_cell(ortho_top)
    bot_raw, bot_reduced = _orientation_from_ortho_cell(ortho_bot)

    top_x_period = float(norm(top_reduced["x"]))
    bot_x_period = float(norm(bot_reduced["x"]))
    top_y_period = float(norm(top_reduced["y"]))
    bot_y_period = float(norm(bot_reduced["y"]))
    top_z_period = float(norm(top_reduced["z"]))
    bot_z_period = float(norm(bot_reduced["z"]))

    return {
        "axis": uvw_arr.tolist(),
        "basis": basis,
        "sigma": int(sigma),
        "gb_plane": gb1_arr.tolist(),
        "gb_plane_csl_scaled": gb1_csl_arr.tolist(),
        "paired_gb_plane_csl_scaled": gb2_csl_arr.tolist(),
        "gb_plane_csl_scale": gb1_csl_scale,
        "theta_deg": float(degrees(theta)),
        "m": int(m),
        "n": int(n),
        "is_tilt": True,
        "top": {
            "orientation_raw": top_raw,
            "orientation": {
                "x": list(top_reduced["x"]),
                "y": list(top_reduced["y"]),
                "z": list(top_reduced["z"]),
            },
            "x_period_lattice": top_x_period,
            "y_period_lattice": top_y_period,
            "z_period_lattice": top_z_period,
            "x_period_angstrom": top_x_period * a0,
            "y_period_angstrom": top_y_period * a0,
            "z_period_angstrom": top_z_period * a0,
        },
        "bottom": {
            "orientation_raw": bot_raw,
            "orientation": {
                "x": list(bot_reduced["x"]),
                "y": list(bot_reduced["y"]),
                "z": list(bot_reduced["z"]),
            },
            "x_period_lattice": bot_x_period,
            "y_period_lattice": bot_y_period,
            "z_period_lattice": bot_z_period,
            "x_period_angstrom": bot_x_period * a0,
            "y_period_angstrom": bot_y_period * a0,
            "z_period_angstrom": bot_z_period * a0,
        },
        "orthogonal_cell_atoms": int(atom_estimate),
        "a0": a0,
    }


def _matches_gb_type(plane_type: str, gb_type: str | None) -> bool:
    """Case-insensitive substring match used by plane listing filters.

    ``gb_type="tilt"`` matches both ``Tilt`` and ``Symmetric Tilt``.
    """
    if not gb_type:
        return True
    needle = str(gb_type).strip().lower()
    if not needle:
        return True
    return needle in str(plane_type).lower()


def _plane_filter_worker(args):
    """Process-pool worker: orthogonal cell + symmetry key for one GB plane."""
    basis, uvw_list, m, n, v1_list, v2_list, gb_type = args
    uvw = np.array(uvw_list, dtype=int)
    v1 = np.array(v1_list, dtype=int)
    v2 = np.array(v2_list, dtype=int)
    key = _canonical_gb_pair_key(v1, v2, uvw)
    ortho = Find_Orthogonal_cell(basis, uvw, m, n, v1)
    if ortho is None:
        return None
    return key, v1, v2, gb_type, ortho, int(ortho[2])


def _sigma_lookup_worker(args):
    """Return all fundamental rotation branches for one Sigma value."""
    uvw_list, basis, sigma = args
    uvw = np.array(uvw_list, dtype=int)
    matches = get_fundamental_theta_m_n_list(uvw, basis, sigma)
    if not matches:
        return None
    return [(sigma, theta, m, n) for theta, m, n in matches]


def _merge_plane_filter_results(results):
    """Reduce per-plane worker output into deduplicated GB plane lists."""
    best_by_key = {}
    key_order = []
    for item in results:
        if item is None:
            continue
        key, v1, v2, gb_type, ortho, atom_count = item
        if key not in best_by_key:
            key_order.append(key)
        if key not in best_by_key or atom_count < best_by_key[key][4]:
            best_by_key[key] = (v1, v2, gb_type, ortho, atom_count)

    filtered_V1 = []
    filtered_V2 = []
    filtered_Type = []
    filtered_Num = []
    for key in key_order:
        gb1, gb2, gbtype, ortho, _ = best_by_key[key]
        filtered_V1.append(gb1)
        filtered_V2.append(gb2)
        filtered_Type.append(gbtype)
        filtered_Num.append(ortho[2])

    return (np.array(filtered_V1).astype(int),
            np.array(filtered_V2).astype(int),
            filtered_Type,
            np.array(filtered_Num).astype(int))


def _filter_planes_sequential(uvw, basis, m, n, V1, V2, Type):
    """Fallback: filter GB planes on a single core."""
    results = []
    for i in range(len(V1)):
        item = _plane_filter_worker(
            (basis, uvw.tolist(), m, n, V1[i].tolist(), V2[i].tolist(), Type[i])
        )
        if item is not None:
            results.append(item)
    return _merge_plane_filter_results(results)


def Create_Filtered_GB_Plane_List(
    uvw, basis, m, n, lim=3, *, parallel_filtering=True, gb_type=None
):
    """
    returns symmetry-filtered GB planes with the smallest atom counts.

    Uses a process pool to evaluate orthogonal cells in parallel by default.
    Set ``parallel_filtering=False`` when the caller already runs inside a
    process-pool worker to avoid creating a nested process pool.

    When ``gb_type`` is set (e.g. ``"tilt"``), candidates that fail the
    Type-column substring match are dropped **before** orthogonal-cell
    construction. That skips the expensive Mixed/Twist work when only tilt
    rows are requested. Classification itself still examines the full Miller
    mesh; only the orthogonal-cell stage is pruned.
    """
    uvw = np.array(uvw)
    V1, V2, _, Type = Create_Possible_GB_Plane_List(uvw, m, n, lim)
    if len(V1) == 0:
        return (
            np.array([], dtype=int),
            np.array([], dtype=int),
            [],
            np.array([], dtype=int),
        )

    if gb_type:
        keep = [
            i for i, plane_type in enumerate(Type)
            if _matches_gb_type(plane_type, gb_type)
        ]
        if not keep:
            return (
                np.array([], dtype=int),
                np.array([], dtype=int),
                [],
                np.array([], dtype=int),
            )
        V1 = V1[keep]
        V2 = V2[keep]
        Type = [Type[i] for i in keep]

    tasks = [
        (basis, uvw.tolist(), m, n, V1[i].tolist(), V2[i].tolist(), Type[i])
        for i in range(len(V1))
    ]
    if not parallel_filtering:
        return _filter_planes_sequential(uvw, basis, m, n, V1, V2, Type)

    parallel_map = _parallel_map()
    try:
        results = parallel_map(_plane_filter_worker, tasks)
    except Exception:
        return _filter_planes_sequential(uvw, basis, m, n, V1, V2, Type)
    return _merge_plane_filter_results(results)


def print_list_GB_Planes(uvw, basis, m, n, lim=3):
    """
    prints lists of GB planes given an axis, basis, m and n.
    """
    V1, V2, Type, Num = Create_Filtered_GB_Plane_List(uvw, basis, m, n, lim)
    for i in range(len(V1)):
        print("{0:<20s}   {1:<20s}   {2:<20s}   {3:<10s}"
              .format(str(V1[i]), str(V2[i]), Type[i], str(Num[i])))


# ___CSL/DSC vector construction___#

# According to Grimmer et al. (Acta Cryst. (1974). A30, 197-207) ,
#  DSC and CSL lattices for bcc and fcc were made from the Sc lattice
# via body_centering and face_centering:
def odd_even(M1):
    """
    finds odd and even elements of a matrix.
    """
    d_e = np.array([['a', 'a', 'a'],
                    ['a', 'a', 'a'],
                    ['a', 'a', 'a']], dtype=str)
    for i in range(3):
        for j in range(3):
            if abs(M1[i][j]) % 2 == 0:
                d_e[i][j] = 'e'
            else:
                d_e[i][j] = 'd'
    return d_e


def self_test_b(a):
    z_b = np.eye(3, 3)
    M = a.copy()
    for i in range(3):
        if np.all(odd_even(M)[:, i] == ['d', 'd', 'd']):
            z_b[i][i] = 0.5
            break

    return z_b


def binary_test_b(a):
    count = 0
    z_b = np.eye(3, 3)
    for i in [0, 1]:
        for j in [1, 2]:
            if i != j and count < 1:
                M = a.copy()
                M[:, j] = M[:, i] + M[:, j]
                if np.all(odd_even(M)[:, j] == ['d', 'd', 'd']):
                    count = count + 1
                    z_b[i][j] = 0.5
                    z_b[j][j] = 0.5
    return z_b


def tertiary_test_b(a):
    z_b = np.eye(3, 3)
    M = a.copy()
    M[:, 2] = M[:, 0] + M[:, 1] + M[:, 2]
    for k in range(3):
        if np.all(odd_even(M)[:, k] == ['d', 'd', 'd']):
            z_b[0][k] = 0.5
            z_b[1][k] = 0.5
            z_b[2][k] = 0.5
            break
    return z_b


def body_centering(b):
    """
    converting a single crystal minimal cell to a bcc one.
    """
    z_b = np.eye(3, 3)
    while det(z_b) != 0.5:
        if det(self_test_b(b)) == 0.5:
            z_b = self_test_b(b)
            break
        if det(binary_test_b(b)) == 0.5:
            z_b = binary_test_b(b)
            break
        if det(tertiary_test_b(b)) == 0.5:
            z_b = tertiary_test_b(b)
            break
    return z_b


def face_centering(a):
    """
    converting a single crystal minimal cell to an fcc one.
    """
    z_f = np.eye(3, 3)
    M = a.copy()
    count = 0
    for i in range(3):
        if (np.all(odd_even(M)[:, i] == ['d', 'd', 'e']) or
                np.all(odd_even(M)[:, i] == ['e', 'd', 'd']) or
                np.all(odd_even(M)[:, i] == ['d', 'e', 'd'])):
            count = count + 1
            z_f[i][i] = 0.5
    if det(z_f) == 0.25:
        return (z_f)
    else:
        for i in [0, 1]:
            for j in [1, 2]:
                if i != j and count < 2:
                    M = a.copy()
                    M[:, j] = M[:, i] + M[:, j]
                    if (np.all(odd_even(M)[:, j] == ['d', 'd', 'e']) or
                            np.all(odd_even(M)[:, j] == ['e', 'd', 'd']) or
                            np.all(odd_even(M)[:, j] == ['d', 'e', 'd'])):
                        count = count + 1
                        z_f[i][j] = 0.5
                        z_f[j][j] = 0.5

    return z_f if det(z_f) == 0.25 else None


def CSL_vec(basis, minicell):
    """
    CSL minimal cell for sc, fcc and bcc.
    arguments:
    basis -- a lattice basis(fcc or bcc)
    minicell -- gb minimal cell
    """
    C_sc = minicell.copy()
    if basis == 'sc':
        C = C_sc.copy()
    if basis == 'bcc':
        C = dot(C_sc, body_centering(C_sc))
    if basis == 'fcc':
        C = dot(C_sc, face_centering(C_sc))
    return C


# An auxilary function to help reduce the size of the small orthogonal cell
# that is decided otherwise based on Sc for fcc and bcc

def Ortho_fcc_bcc(basis, O1, O2):
    ortho1 = np.zeros((3, 3))
    ortho2 = np.zeros((3, 3))
    if basis == 'fcc':
        base = np.delete(Basis('fcc').T, 0, 1)
    elif basis == 'bcc':
        base = ((np.array([[0.5, 0.5, 0.5], [0.5, 0.5, -0.5],
                           [-0.5, 0.5, 0.5]])).T)

    for i in range(3):
        Min_d = min(CommonDivisor(dot(O1[:, i], inv(base)))[1],
                    CommonDivisor(dot(O2[:, i], inv(base)))[1])
        ortho1[:, i] = O1[:, i] / Min_d
        ortho2[:, i] = O2[:, i] / Min_d
    return (ortho1, ortho2)

def main():
    try:
        mode = sys.argv[1].strip().lower()
        if mode == "sigmas" and len(sys.argv) in {6, 7}:
            uvw = reduce_miller_indices([int(value) for value in sys.argv[2:5]])
            basis = sys.argv[5].strip().lower()
            limit = int(sys.argv[6]) if len(sys.argv) == 7 else 50
            print(
                f"Basis-validated CSLs for basis={basis}, axis={uvw.tolist()}, "
                f"limit={limit}"
            )
            print_list(uvw, basis, limit)
            return

        if mode == "planes" and len(sys.argv) in {7, 8, 10}:
            uvw = reduce_miller_indices([int(value) for value in sys.argv[2:5]])
            basis = sys.argv[5].strip().lower()
            sigma = int(sys.argv[6])
            limit = int(sys.argv[7]) if len(sys.argv) in {8, 10} else 2
            rotation_m = int(sys.argv[8]) if len(sys.argv) == 10 else None
            rotation_n = int(sys.argv[9]) if len(sys.argv) == 10 else None
            theta, m, n = resolve_csl_rotation(
                uvw,
                basis,
                sigma,
                rotation_m=rotation_m,
                rotation_n=rotation_n,
            )
            print(
                f"CSL planes for basis={basis}, Sigma={sigma}, "
                f"theta={degrees(theta):.8f} deg, m={m}, n={n}"
            )
            print(
                " GB1-------------------GB2-------------------Type----------"
                "Number of Atoms "
            )
            print_list_GB_Planes(uvw, basis, m, n, limit)
            return
    except (IndexError, TypeError, ValueError) as exc:
        print(f"Invalid CSL command: {exc}\n")

    print(__doc__)

if __name__ == '__main__':
    main()
