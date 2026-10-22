"""Static defaults for LAMMPS grain-boundary construction and loading."""

# GB construction and box-sizing policy. X repeat matching is independent of
# the boundary mode used later by LAMMPS.
X_MISMATCH_MAX_PERCENT = 2.0

TARGET_X_NM = 20.0
TARGET_Y_PER_GRAIN_NM = 10.0
TARGET_Z_NM = 1.0

TARGET_BOX_NM = (
    TARGET_X_NM,
    2.0 * TARGET_Y_PER_GRAIN_NM,
    TARGET_Z_NM,
)

TARGET_Y_PER_GRAIN_ANGSTROM = TARGET_Y_PER_GRAIN_NM * 10.0
DEFAULT_NZ_PERIODS = "1-3"

# Default occupied-material extents for production GB structures. The x value
# is a preferred size for integer repeat matching, not a hard lower bound.
MIN_Y_PER_GRAIN_NM = TARGET_Y_PER_GRAIN_NM
MIN_Z_NM = TARGET_Z_NM

# Integer x-period matching searches up to the same hard x cap used by the box
# validator and returns a bounded set of alternatives.
X_MATCH_CANDIDATE_LIMIT = 6
PBC_X_PADDING_EPS_FACTOR = 1.5

BOX_DIM_FACTOR = 3.0
BOX_VOLUME_FACTOR = 4.0

# Quasi-static GB shear defaults. These constants are the single source used
# by the writer tool, validated runtime config, and dynamic agent prompts.
SHEAR_LOAD_TYPE = "displacement"
SHEAR_LOADING_DIRECTION = "x"
SHEAR_INITIAL_STRAIN_INCREMENT = 2.5e-4
SHEAR_INITIAL_STEPS = 200
SHEAR_FINE_STRAIN_INCREMENT = 2.5e-4
SHEAR_FINE_STEPS = 0
SHEAR_STEPS_PER_HALF_CYCLE = None
SHEAR_HALF_CYCLES = 1

# eV/Angstrom^2 per boundary atom. The driver multiplies this by the group
# atom count before passing a total spring constant to LAMMPS.
SHEAR_SPRING_PER_ATOM = 0.1

SHEAR_MIN_STYLE = "cg"
SHEAR_MIN_ETOL = 0.0
SHEAR_MIN_FTOL = 1.0e-6
SHEAR_MIN_MAXITER = 3000
SHEAR_MIN_MAXEVAL = 10000

SHEAR_DUMP_EVERY = 1
SHEAR_THERMO_EVERY = 100

# Grain-boundary nudged elastic band defaults.  The replica topology is kept
# here as the code-level default; common/settings.py applies optional .env
# overrides before the writer tool exposes it.
NEB_REPLICAS = 16
NEB_RANKS_PER_REPLICA = 1

NEB_SPRING = 1.0
NEB_MIN_STYLE = "fire"
NEB_TIMESTEP = 0.01
NEB_ETOL = 0.0
NEB_FTOL = 1.0e-4
NEB_N1 = 10000
NEB_N2 = 10000
NEB_NEVERY = 100
NEB_DUMP_EVERY = 1000
NEB_VERBOSITY = "default"
