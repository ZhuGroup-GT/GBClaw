"""LAMMPS Python interface with command output flushed for live log previews."""

from __future__ import annotations

from lammps import lammps as _lammps


class lammps(_lammps):
    """Flush command output, including errors, before returning to Python.

    Some LAMMPS versions apply ``-nonbuf`` only to partitioned simulations.
    Explicit flushing also covers commands that reopen the logfile. Long
    minimizations use ``thermo_modify flush yes`` to flush while still running.
    """

    def command(self, cmd: str):
        try:
            return super().command(cmd)
        finally:
            self.flush_buffers()
