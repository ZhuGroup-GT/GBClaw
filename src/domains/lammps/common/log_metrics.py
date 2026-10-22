"""Minimization metrics parsed from LAMMPS logs."""

from __future__ import annotations

import math
import re

_GB_MINIMIZE_METRIC_PATTERNS = {
    "atoms": re.compile(r"with\s+(\d+)\s+atoms"),
    "wall_time": re.compile(r"Total wall time:\s*(\S+)"),
    "iterations": re.compile(r"Iterations, force evaluations\s*=\s*(\d+)\s+(\d+)"),
    "stopping": re.compile(r"Stopping criterion\s*=\s*(.+)"),
}


def extract_minimization_metrics(log_text: str) -> dict:
    """Record every stage, but assess convergence using only the final stage."""
    metrics: dict = {}
    if not log_text:
        return metrics

    def finite_value(match: re.Match[str] | None, group: int) -> float | None:
        if match is None:
            return None
        try:
            value = float(match.group(group))
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    for key, pattern in _GB_MINIMIZE_METRIC_PATTERNS.items():
        match = pattern.search(log_text)
        if not match:
            continue
        if key == "iterations":
            metrics["iterations"] = int(match.group(1))
            metrics["force_evaluations"] = int(match.group(2))
        elif key == "atoms":
            metrics["atoms"] = int(match.group(1))
        else:
            metrics[key] = match.group(1).strip()

    minimizations: list[dict] = []
    for index, block in enumerate(log_text.split("Minimization stats:")[1:], start=1):
        block = block.split("MPI task timing breakdown:", 1)[0]
        stopping = re.search(r"Stopping criterion\s*=\s*([^\n]+)", block)
        energy = re.search(
            r"Energy initial, next-to-last, final\s*=\s*"
            r"(\S+)\s+(\S+)\s+(\S+)",
            block,
        )
        force_norm = re.search(
            r"Force two-norm initial, final\s*=\s*(\S+)\s+(\S+)",
            block,
        )
        force_max = re.search(
            r"Force max component initial, final\s*=\s*(\S+)\s+(\S+)",
            block,
        )
        iterations = re.search(
            r"Iterations, force evaluations\s*=\s*(\d+)\s+(\d+)",
            block,
        )
        if not stopping:
            continue
        criterion = stopping.group(1).strip()
        record = {
            "minimization_index": index,
            "stopping_criterion": criterion,
            "converged": criterion.casefold()
            in {"force tolerance", "energy tolerance"},
            "energy_initial_eV": finite_value(energy, 1),
            "energy_next_to_last_eV": finite_value(energy, 2),
            "energy_final_eV": finite_value(energy, 3),
            "force_two_norm_initial": finite_value(force_norm, 1),
            "force_two_norm_final": finite_value(force_norm, 2),
            "force_max_component_initial": finite_value(force_max, 1),
            "force_max_component_final": finite_value(force_max, 2),
            "iterations": int(iterations.group(1)) if iterations else None,
            "force_evaluations": int(iterations.group(2)) if iterations else None,
        }
        minimizations.append(record)

    if minimizations:
        final = minimizations[-1]
        nonconverged = [
            item for item in minimizations if item["converged"] is False
        ]
        metrics.update(
            {
                "minimization_count": len(minimizations),
                "nonconverged_minimization_count": len(nonconverged),
                "all_minimizations_converged": not nonconverged,
                "convergence_basis": "final_minimization",
                "final_minimization_converged": final["converged"],
                "stopping": final["stopping_criterion"],
                "energy_initial": final["energy_initial_eV"],
                "energy_final": final["energy_final_eV"],
                "force_norm_initial": final["force_two_norm_initial"],
                "force_norm_final": final["force_two_norm_final"],
                "iterations": final["iterations"],
                "force_evaluations": final["force_evaluations"],
                "minimizations": minimizations,
                "warnings": (
                    [
                        "The final minimization did not meet an energy/force tolerance "
                        f"(stopping criterion: {final['stopping_criterion']}); "
                        "the final structure and metrics remain available."
                    ]
                    if not final["converged"]
                    else []
                ),
            }
        )

    return metrics
