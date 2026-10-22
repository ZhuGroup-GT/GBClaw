"""Agent tools for NEB minimum-energy-path analysis and plots."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from langchain_core.tools import tool

from core.util.graph_config import runtime_project_id
from core.util.io import write_json_atomic
from domains.analysis.common import verified_artifact_path
from domains.analysis.neb_mep.analysis import analyze_neb_mep_payload
from project_memory.models import (
    FactRecord,
    RunRecord,
    RunSourceRecord,
    RunStatus,
    Workflow,
)
from project_memory.publication.facts import make_run_fact
from project_memory.publication.results import RunToolResult
from project_workspace.artifacts.models import ProvenanceClass
from project_workspace.store import ProjectStore


def neb_plot_facts(run: RunRecord, result: dict[str, Any]) -> list[FactRecord]:
    """Keep all saddle candidates, uncertainties and plotting choices."""
    return [make_run_fact(run, {
        **result,
        "summary": f"Rendered NEB minimum-energy path with {result.get('saddle_count')} saddle candidates",
        "parameters": {key: value for key, value in run.parameters.items()
                       if key != "memory_tool" and not key.startswith("_")},
    })]


def _load_neb_mep_analysis(
    *,
    store: ProjectStore,
    service: Any,
    project_id: str,
    source_run_id: str,
    derivative_tolerance_eV_per_coordinate: float,
) -> tuple[Any, Any, str, dict[str, Any]]:
    source_run = service.memory.get_run(source_run_id)
    if (
        source_run.workflow is not Workflow.GB_NEB
        or source_run.status is not RunStatus.COMPLETED
    ):
        raise ValueError(f"source run {source_run_id} must be a completed gb_neb run")
    artifacts = service.memory.query_artifacts(
        run_id=source_run_id,
        role="mep_json",
    )
    if len(artifacts) != 1:
        raise ValueError(
            f"source run {source_run_id} must have exactly one mep_json artifact; "
            f"found {len(artifacts)}"
        )
    mep_artifact = artifacts[0]
    mep_path = verified_artifact_path(
        store=store,
        service=service,
        project_id=project_id,
        run_id=source_run_id,
        artifact=mep_artifact,
    )
    content = mep_path.read_bytes()
    source_fingerprint = "sha256:" + hashlib.sha256(content).hexdigest()
    payload = json.loads(content)
    analysis = analyze_neb_mep_payload(
        payload,
        derivative_tolerance_eV_per_coordinate=(
            derivative_tolerance_eV_per_coordinate
        ),
    )
    analysis["source"] = {
        "run_id": source_run_id,
        "artifact_id": mep_artifact.artifact_id,
        "artifact_path": mep_artifact.path,
        "fingerprint": source_fingerprint,
    }
    return source_run, mep_artifact, source_fingerprint, analysis


@tool
def analyze_neb_mep(
    source_run_id: str,
    derivative_tolerance_eV_per_coordinate: float = 0.0,
) -> str:
    """Find every discrete saddle candidate in a completed LAMMPS NEB profile.

    A saddle candidate is an interior local maximum where the finite-difference
    derivative changes from positive to negative. Zero-slope plateaus between
    those signs produce one candidate. The result may contain zero, one, or many
    saddles and reports each replica, reaction coordinate, energy, derivatives,
    plateau bounds, and forward/reverse barrier.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config. Analysis must be written under the project."
    source_run_id = source_run_id.strip()
    if not source_run_id:
        return "source_run_id must not be empty"
    tolerance = float(derivative_tolerance_eV_per_coordinate)
    if not math.isfinite(tolerance) or tolerance < 0.0:
        return "derivative_tolerance_eV_per_coordinate must be finite and non-negative"

    store = ProjectStore()
    service = store.runs(project_id)
    try:
        _, mep_artifact, source_fingerprint, analysis = _load_neb_mep_analysis(
            store=store,
            service=service,
            project_id=project_id,
            source_run_id=source_run_id,
            derivative_tolerance_eV_per_coordinate=tolerance,
        )
        run = service.create_running_run(
            workflow=Workflow.NEB_ANALYSIS,
            memory_tool="analyze_neb_mep",
            source_run_id=source_run_id,
            source_fingerprint=source_fingerprint,
            sources=[
                RunSourceRecord(
                    source_run_id=source_run_id,
                    relation="neb_profile",
                    ordinal=0,
                    source_fingerprint=source_fingerprint,
                    metadata={"source_artifact_id": mep_artifact.artifact_id},
                )
            ],
            parameters={
                "mode": "saddle_analysis",
                "source_mep_artifact_id": mep_artifact.artifact_id,
                "derivative_tolerance_eV_per_coordinate": tolerance,
            },
        )
        output_dir = service.artifacts.run_directory(run.run_id)
        data_path = output_dir / "neb_mep_analysis.json"
        write_json_atomic(data_path, analysis)
        data_artifact = service.register_artifact(
            run.run_id,
            "neb_mep_analysis",
            data_path.name,
            ProvenanceClass.DERIVED,
            media_type="application/json",
            metadata={
                "source_run_id": source_run_id,
                "replica_count": analysis["replica_count"],
                "saddle_count": analysis["saddle_count"],
            },
        )
        result = {
            "source_run_id": source_run_id,
            "replica_count": analysis["replica_count"],
            "derivative_tolerance_eV_per_coordinate": tolerance,
            "final_relative_energy_eV": analysis["final_relative_energy_eV"],
            "saddle_count": analysis["saddle_count"],
            "saddles": analysis["saddles"],
            "highest_saddle": analysis["highest_saddle"],
            "warnings": analysis["warnings"],
            "data": data_artifact.path,
        }
        terminal = service.complete_run(
            run.run_id, result=result, memory_tool="analyze_neb_mep",
            conclusion={
                "conclusion_type": "neb_saddle_analysis",
                "source_neb_run_id": source_run_id,
                "source_mep_artifact_id": mep_artifact.artifact_id,
                "derivative_tolerance_eV_per_coordinate": tolerance,
                "replica_count": analysis["replica_count"],
                "final_relative_energy_eV": analysis["final_relative_energy_eV"],
                "saddle_count": analysis["saddle_count"],
                "saddles": analysis["saddles"],
                "highest_saddle": analysis["highest_saddle"],
                "warnings": analysis["warnings"],
                "data_artifact_path": data_artifact.path,
            },
        )
        result = terminal.result
    except Exception as exc:
        if "run" in locals():
            terminal = service.fail_run(run.run_id, error=str(exc))
            return RunToolResult(
                run_id=run.run_id,
                workflow=terminal.workflow,
                status=terminal.status,
                manifest=terminal.manifest_path,
                error=terminal.error,
            ).to_json()
        return str(exc)

    fact_error = (
        f"analysis completed but conclusion indexing failed: {result['fact_indexing_error']}"
        if result.get("fact_indexing_error") else ""
    )

    return RunToolResult(
        run_id=terminal.run_id,
        workflow=terminal.workflow,
        status=terminal.status,
        manifest=terminal.manifest_path,
        artifacts=[{"role": data_artifact.role, "path": data_artifact.path}],
        result=result,
        error=fact_error,
    ).to_json()


@tool
def plot_neb_mep(
    source_run_id: str,
    label: str | None = None,
    color: str = "#1f5a94",
    line_style: str = "-",
    marker: str = "o",
    show_legend: bool = True,
    legend_location: str = "best",
    title: str | None = None,
    x_label: str = "Reaction coordinate",
    y_label: str = "Energy difference relative to initial state (eV)",
    linewidth: float = 1.8,
    marker_size: float = 4.5,
    show_saddle: bool = True,
    saddle_color: str = "#c62828",
    annotate_saddle: bool = True,
    derivative_tolerance_eV_per_coordinate: float = 0.0,
    show_grid: bool = True,
) -> str:
    """Plot a completed LAMMPS NEB minimum-energy path from its indexed mep_json.

    The x axis is the reaction coordinate and the y axis is ``relative_energy_eV``,
    i.e. the energy difference from the initial replica. The model may control the
    curve label/color/style/marker, legend, titles, line/marker sizes, saddle highlight,
    annotation, and grid. The source NEB artifact is never modified; this creates a
    linked ``neb_analysis`` run containing reusable JSON and PNG artifacts.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config. Analysis must be written under the project."
    if not source_run_id.strip():
        return "source_run_id must not be empty"
    source_run_id = source_run_id.strip()
    if not math.isfinite(float(linewidth)) or linewidth <= 0:
        return "linewidth must be a finite positive number"
    if not math.isfinite(float(marker_size)) or marker_size < 0:
        return "marker_size must be a finite non-negative number"
    tolerance = float(derivative_tolerance_eV_per_coordinate)
    if not math.isfinite(tolerance) or tolerance < 0.0:
        return "derivative_tolerance_eV_per_coordinate must be finite and non-negative"

    store = ProjectStore()
    service = store.runs(project_id)
    try:
        _, mep_artifact, source_fingerprint, analysis = _load_neb_mep_analysis(
            store=store,
            service=service,
            project_id=project_id,
            source_run_id=source_run_id,
            derivative_tolerance_eV_per_coordinate=tolerance,
        )
        profile = analysis["profile"]
        saddles = analysis["saddles"]
        parameters = {
            "mode": "mep_plot",
            "memory_tool": "plot_neb_mep",
            "source_mep_artifact_id": mep_artifact.artifact_id,
            "label": label or "Minimum energy path",
            "color": color,
            "line_style": line_style,
            "marker": marker,
            "show_legend": show_legend,
            "legend_location": legend_location,
            "title": title,
            "x_label": x_label,
            "y_label": y_label,
            "linewidth": linewidth,
            "marker_size": marker_size,
            "show_saddle": show_saddle,
            "saddle_color": saddle_color,
            "annotate_saddle": annotate_saddle,
            "derivative_tolerance_eV_per_coordinate": tolerance,
            "show_grid": show_grid,
        }
        run = service.create_running_run(
            workflow=Workflow.NEB_ANALYSIS,
            memory_tool="plot_neb_mep",
            source_run_id=source_run_id,
            source_fingerprint=source_fingerprint,
            sources=[
                RunSourceRecord(
                    source_run_id=source_run_id,
                    relation="neb_profile",
                    ordinal=0,
                    source_fingerprint=source_fingerprint,
                    metadata={"source_artifact_id": mep_artifact.artifact_id},
                )
            ],
            parameters=parameters,
        )
        output_dir = service.artifacts.run_directory(run.run_id)
        data_path = output_dir / "neb_mep_plot.json"
        plot_path = output_dir / "neb_mep.png"
        plot_payload = {
            "version": 1,
            "source": analysis["source"],
            "analysis": analysis,
            "plot": parameters,
        }
        write_json_atomic(data_path, plot_payload)

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        reaction_coordinates = [
            float(item["reaction_coordinate"]) for item in profile
        ]
        relative_energies = [float(item["relative_energy_eV"]) for item in profile]
        figure, axis = plt.subplots(figsize=(7.2, 4.8))
        try:
            axis.plot(
                reaction_coordinates,
                relative_energies,
                color=color,
                linestyle=line_style,
                marker=marker,
                linewidth=linewidth,
                markersize=marker_size,
                label=parameters["label"],
            )
            axis.axhline(0.0, color="#666666", linewidth=0.8, alpha=0.65)
            if show_saddle and saddles:
                axis.scatter(
                    [float(item["reaction_coordinate"]) for item in saddles],
                    [float(item["relative_energy_eV"]) for item in saddles],
                    color=saddle_color,
                    s=max(28.0, marker_size * marker_size * 1.5),
                    zorder=3,
                    label=f"Saddle candidates ({len(saddles)})",
                )
                if annotate_saddle:
                    for saddle in saddles:
                        axis.annotate(
                            f"S{saddle['saddle_number']}: "
                            f"{float(saddle['relative_energy_eV']):.6g} eV",
                            xy=(
                                float(saddle["reaction_coordinate"]),
                                float(saddle["relative_energy_eV"]),
                            ),
                            xytext=(8, 8),
                            textcoords="offset points",
                            color=saddle_color,
                        )
            axis.set_xlabel(x_label)
            axis.set_ylabel(y_label)
            axis.set_title(title or "Minimum Energy Path")
            if show_grid:
                axis.grid(True, alpha=0.25)
            if show_legend:
                axis.legend(loc=legend_location)
            figure.tight_layout()
            figure.savefig(plot_path, dpi=200, bbox_inches="tight")
        finally:
            plt.close(figure)

        data_artifact = service.register_artifact(
            run.run_id,
            "neb_mep_plot_data",
            data_path.name,
            ProvenanceClass.DERIVED,
            media_type="application/json",
            metadata={
                "source_run_id": source_run_id,
                "replica_count": len(profile),
                "saddle_count": len(saddles),
            },
        )
        plot_artifact = service.register_artifact(
            run.run_id,
            "neb_mep_plot",
            plot_path.name,
            ProvenanceClass.DERIVED,
            media_type="image/png",
            metadata={
                "source_run_id": source_run_id,
                "replica_count": len(profile),
                "saddle_count": len(saddles),
                "saddle_replicas": [int(item["replica"]) for item in saddles],
            },
        )
        result = {
            "source_run_id": source_run_id,
            "replica_count": len(profile),
            "derivative_tolerance_eV_per_coordinate": tolerance,
            "final_relative_energy_eV": analysis["final_relative_energy_eV"],
            "saddle_count": len(saddles),
            "saddles": saddles,
            "highest_saddle": analysis["highest_saddle"],
            "warnings": analysis["warnings"],
            "data": data_artifact.path,
            "plot": plot_artifact.path,
        }
        terminal = service.complete_run(run.run_id, result=result, memory_tool="plot_neb_mep")
        result = terminal.result
    except Exception as exc:
        if "run" in locals():
            terminal = service.fail_run(run.run_id, error=str(exc))
            return RunToolResult(
                run_id=run.run_id,
                workflow=terminal.workflow,
                status=terminal.status,
                manifest=terminal.manifest_path,
                error=terminal.error,
            ).to_json()
        return str(exc)

    return RunToolResult(
        run_id=terminal.run_id,
        workflow=terminal.workflow,
        status=terminal.status,
        manifest=terminal.manifest_path,
        artifacts=[
            {"role": data_artifact.role, "path": data_artifact.path},
            {"role": plot_artifact.role, "path": plot_artifact.path},
        ],
        result=result,
    ).to_json()
