"""Agent tools for stress-strain drop analysis and multi-run comparison plots."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from core.util.graph_config import runtime_project_id
from core.util.io import write_json_atomic
from domains.analysis.common import verified_artifact_path
from domains.analysis.stress_strain.analysis import (
    STRESS_FIELDS,
    analyze_stress_strain_metadata,
    metadata_points_to_arrays,
    plot_stress_strain_analysis,
)
from domains.analysis.stress_strain.model_view import (
    stress_strain_comparison_model_view,
    stress_strain_model_view,
)

from domains.analysis.stress_strain.analysis import (
    plot_stress_strain_comparison as _plot_stress_strain_comparison,
)
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


def stress_strain_comparison_facts(run: RunRecord, result: dict[str, Any]) -> list[FactRecord]:
    """Retain every plotted run and its presentation settings."""
    return [make_run_fact(run, {
        **result,
        "summary": f"Plotted {len(result.get('source_run_ids', []))} stress-strain curve(s) "
                   f"using {run.parameters.get('stress_field')}",
        "parameters": {key: value for key, value in run.parameters.items()
                       if key != "memory_tool" and not key.startswith("_")},
    })]


def _add_project_frame_paths(
    analysis: dict,
    metadata_artifact_path: str,
    indexed_frame_artifacts: list[Any],
) -> None:
    """Add manifest-addressable project paths while retaining metadata-relative paths."""
    source_run_root = Path(metadata_artifact_path).parent
    indexed_by_step = {
        int(artifact.metadata["step"]): artifact
        for artifact in indexed_frame_artifacts
        if artifact.metadata.get("step") is not None
    }

    def enrich(frame: dict | None) -> None:
        if not frame or not frame.get("path"):
            return
        relative = Path(str(frame["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            frame["renderable"] = False
            return
        frame["artifact_role"] = "intermediate_structure"
        frame["source_step"] = int(frame["step"])
        indexed = indexed_by_step.get(int(frame["step"]))
        if indexed is None:
            frame["project_path"] = (source_run_root / relative).as_posix()
            frame["renderable"] = False
            return
        frame["artifact_id"] = indexed.artifact_id
        frame["project_path"] = indexed.path
        frame["renderable"] = True

    for frame in analysis.get("frames") or []:
        enrich(frame)
    for field in ("events", "threshold_events", "extrema_events", "combined_events"):
        for event in analysis.get(field) or []:
            enrich(event.get("pre_peak_frame"))
            enrich(event.get("post_event_frame"))
    for extrema in (analysis.get("local_extrema") or {}).values():
        for extremum in extrema:
            enrich(extremum.get("frame"))


def _compact_stress_event(event: dict[str, Any]) -> dict[str, Any]:
    """Retain event identity, independent evidence and source-frame selection."""
    record = {
        key: event.get(key)
        for key in (
            "event_index", "segment_index", "event_step", "event_end_step",
            "event_strain", "event_end_strain", "loading_direction",
            "loading_half_cycle", "loading_cycle", "stress_drop_mpa",
            "largest_single_step_drop_mpa", "pre_peak_step", "pre_peak_strain",
            "pre_peak_stress_mpa", "pre_peak_abs_stress_mpa", "stress_after_mpa",
            "support", "threshold_event_indices", "extrema_event_index",
            "post_minimum_confirmed", "event_complete", "descent_start_step",
            "descent_end_step", "post_minimum_step", "drop_amplitude_basis",
        )
        if key in event
    }
    for field in ("pre_peak_frame", "post_event_frame"):
        frame = event.get(field) or {}
        for key in ("step", "exact", "artifact_id", "step_offset"):
            record[f"{field}_{key}"] = frame.get(key)
    return record


def _stress_analysis_views(analysis: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    """Expose both detectors and their union together to tools and durable facts."""
    views = {
        key: analysis[key]
        for key in (
            "threshold_detection", "threshold_event_count", "extrema_detection",
            "extrema_event_count", "combined_event_count", "local_extrema", "curve_summary",
        )
        if key in analysis
    }
    for field in ("threshold_events", "extrema_events", "combined_events"):
        if field in analysis:
            views[field] = (
                [_compact_stress_event(event) for event in analysis[field]]
                if compact else analysis[field]
            )
    return views


def _stress_strain_conclusion_value(
    *,
    source_run_id: str,
    analysis: dict,
    data_path: str,
    plot_path: str,
) -> dict[str, Any]:
    """Build the compact, deterministic fact derived from one completed analysis."""
    events = [_compact_stress_event(event) for event in analysis.get("events") or []]
    detection = analysis.get("detection") or {}
    return {
        "conclusion_type": "stress_strain_events",
        "source_shear_run_id": source_run_id,
        "source": analysis.get("source") or {},
        "stress_field": analysis.get("stress_field"),
        "stress_signal": analysis.get("stress_signal"),
        "point_count": analysis.get("point_count"),
        "nonconverged_point_count": analysis.get("nonconverged_point_count"),
        "excluded_nonconverged_point_count": analysis.get(
            "excluded_nonconverged_point_count"
        ),
        "detection_method": detection.get("method"),
        "detection_threshold_mpa": (analysis.get("threshold_detection") or detection).get("threshold_mpa"),
        "event_count": analysis.get("event_count"),
        "events": events,
        **_stress_analysis_views(analysis, compact=True),
        "data_artifact_path": data_path,
        "plot_artifact_path": plot_path,
    }


@tool
def stress_strain_analysis(
    source_run_id: str,
    stress_field: str = "top_shear_stress_mpa",
    minimum_drop_mpa: float | None = None,
    sensitivity: float = 3.5,
    use_absolute_stress: bool = True,
    allow_partial: bool = False,
    extrema_tolerance_mpa: float = 0.0,
) -> str:
    """Analyze the complete point history in a gb_shear ``shear_run.json``.

    Return threshold drops, local peak-to-valley drops, and their chronological
    union separately, plus a three-panel PNG and first/subsequent-event summaries.
    Top-level events are the union, with both/extrema_only/threshold_only evidence.
    Equal-height peaks at different steps remain separate. Unfinished descents
    never claim a confirmed valley or a stable structural state.

    ``stress_field`` may be top/bottom shear stress or one of the six sigma fields.
    By default event detection uses absolute stress so either loading direction is
    handled consistently. Set ``minimum_drop_mpa`` for an explicit threshold; when it
    is omitted, a robust MAD-based threshold is used. Larger ``sensitivity`` values
    raise that threshold; they do not remove extrema-only events from the union.
    ``extrema_tolerance_mpa`` optionally coalesces plateau points whose total stress
    range stays within that tolerance; zero preserves every local sign change, with
    no smoothing or event-amplitude cutoff. Peaks are positive-to-negative changes
    and valleys negative-to-positive along loading
    order, including plateaus. The first drop and later recurring drops are reported
    separately; structural change needs independent structural evidence. A source run
    must be completed unless ``allow_partial`` is explicitly true. Every finite loading point
    participates in analysis; ``converged=false`` is retained only as warning metadata.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config. Analysis must be written under the project."
    normalized_field = stress_field.strip().lower()
    if normalized_field not in STRESS_FIELDS:
        return f"Invalid stress_field {stress_field!r}. Use one of {sorted(STRESS_FIELDS)}."

    store = ProjectStore()
    service = store.runs(project_id)
    try:
        source_run = service.memory.get_run(source_run_id)
        if source_run.workflow is not Workflow.GB_SHEAR:
            raise ValueError(
                f"source run {source_run_id} is {source_run.workflow.value}, not gb_shear"
            )
        if source_run.status is not RunStatus.COMPLETED and not allow_partial:
            raise ValueError(
                f"source shear run {source_run_id} is {source_run.status.value}; "
                "set allow_partial=true only when partial-curve analysis is intended"
            )
        metadata_artifacts = service.memory.query_artifacts(
            run_id=source_run_id, role="run_metadata"
        )
        if len(metadata_artifacts) != 1:
            raise ValueError(
                f"source run {source_run_id} must have exactly one run_metadata artifact; "
                f"found {len(metadata_artifacts)}"
            )
        metadata_artifact = metadata_artifacts[0]
        metadata_path = verified_artifact_path(
            store=store,
            service=service,
            project_id=project_id,
            run_id=source_run_id,
            artifact=metadata_artifact,
        )
        source_bytes = metadata_path.read_bytes()
        source_fingerprint = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
        metadata = json.loads(source_bytes)
        if not isinstance(metadata, dict):
            raise ValueError(f"{metadata_artifact.path} must contain a JSON object")
        analysis = analyze_stress_strain_metadata(
            metadata,
            stress_field=normalized_field,
            minimum_drop_mpa=minimum_drop_mpa,
            sensitivity=sensitivity,
            use_absolute_stress=use_absolute_stress,
            extrema_tolerance_mpa=extrema_tolerance_mpa,
        )
        _add_project_frame_paths(
            analysis,
            metadata_artifact.path,
            service.memory.query_artifacts(
                run_id=source_run_id, role="intermediate_structure"
            ),
        )
        run = service.create_running_run(
            workflow=Workflow.STRESS_STRAIN_ANALYSIS,
            memory_tool="stress_strain_analysis",
            source_run_id=source_run_id,
            source_fingerprint=source_fingerprint,
            sources=[RunSourceRecord(
                source_run_id=source_run_id, relation="stress_history", ordinal=0,
                source_fingerprint=source_fingerprint,
                metadata={"source_artifact_id": metadata_artifact.artifact_id,
                          "source_status": source_run.status.value},
            )],
            parameters={
                "source_metadata_artifact_id": metadata_artifact.artifact_id,
                "stress_field": normalized_field,
                "minimum_drop_mpa": minimum_drop_mpa,
                "sensitivity": sensitivity,
                "use_absolute_stress": use_absolute_stress,
                "extrema_tolerance_mpa": extrema_tolerance_mpa,
                "event_methods": ["threshold", "local_extrema", "combined"],
                "convergence_affects_analysis": False,
                "allow_partial": allow_partial,
            },
        )
        output_dir = service.artifacts.run_directory(run.run_id)
        source_snapshot = output_dir / "source_shear_run.json"
        source_snapshot.write_bytes(source_bytes)
        snapshot_artifact = service.register_artifact(
            run.run_id, "source_metadata_snapshot", source_snapshot.name,
            ProvenanceClass.PRIMARY, media_type="application/json",
            metadata={"source_run_id": source_run_id,
                      "source_artifact_id": metadata_artifact.artifact_id,
                      "source_fingerprint": source_fingerprint,
                      "source_status": source_run.status.value},
        )
        json_path = output_dir / "stress_strain_analysis.json"
        plot_path = output_dir / "stress_strain_curve.png"
        analysis["source"] = {
            "run_id": source_run_id,
            "run_status": source_run.status.value,
            "metadata_artifact_id": metadata_artifact.artifact_id,
            "metadata_path": metadata_artifact.path,
            "fingerprint": source_fingerprint,
            "snapshot_artifact_id": snapshot_artifact.artifact_id,
            "snapshot_path": snapshot_artifact.path,
        }
        write_json_atomic(json_path, analysis)
        plot_stress_strain_analysis(analysis, plot_path)
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

    try:
        data_artifact = service.register_artifact(
            run.run_id,
            "stress_strain_data",
            json_path.name,
            ProvenanceClass.DERIVED,
            media_type="application/json",
            metadata={
                "source_run_id": source_run_id,
                "point_count": analysis["point_count"],
                "event_count": analysis["event_count"],
                "stress_field": normalized_field,
            },
        )
        plot_artifact = service.register_artifact(
            run.run_id,
            "stress_strain_plot",
            plot_path.name,
            ProvenanceClass.DERIVED,
            media_type="image/png",
            metadata={
                "source_run_id": source_run_id,
                "point_count": analysis["point_count"],
                "event_count": analysis["event_count"],
                "stress_field": normalized_field,
            },
        )
        compact_result = {
            "source_run_id": source_run_id,
            "source_status": source_run.status.value,
            "source_fingerprint": source_fingerprint,
            "source_snapshot": snapshot_artifact.path,
            "point_count": analysis["point_count"],
            "nonconverged_point_count": analysis["nonconverged_point_count"],
            "excluded_nonconverged_point_count": analysis[
                "excluded_nonconverged_point_count"
            ],
            "stress_field": normalized_field,
            "stress_signal": analysis["stress_signal"],
            "detection": analysis["detection"],
            "event_count": analysis["event_count"],
            "events": analysis["events"],
            **_stress_analysis_views(analysis),
            "summary": (analysis.get("curve_summary") or {}).get("summary", ""),
            "data": data_artifact.path,
            "plot": plot_artifact.path,
        }
        terminal = service.complete_run(
            run.run_id, result=compact_result, memory_tool="stress_strain_analysis",
            conclusion=_stress_strain_conclusion_value(
                source_run_id=source_run_id,
                analysis=analysis,
                data_path=data_artifact.path,
                plot_path=plot_artifact.path,
            ),
        )
        compact_result = terminal.result
    except Exception as exc:
        terminal = service.fail_run(run.run_id, error=str(exc))
        return RunToolResult(
            run_id=run.run_id,
            workflow=terminal.workflow,
            status=terminal.status,
            manifest=terminal.manifest_path,
            error=terminal.error,
        ).to_json()

    fact_error = (
        f"analysis completed but conclusion indexing failed: {compact_result['fact_indexing_error']}"
        if compact_result.get("fact_indexing_error") else ""
    )

    reply = RunToolResult(
        run_id=run.run_id,
        workflow=terminal.workflow,
        status=terminal.status,
        manifest=terminal.manifest_path,
        artifacts=[
            {"role": snapshot_artifact.role, "path": snapshot_artifact.path},
            {"role": data_artifact.role, "path": data_artifact.path},
            {"role": plot_artifact.role, "path": plot_artifact.path},
        ],
        result=compact_result,
        error=fact_error,
    )
    reply.model_view = stress_strain_model_view(reply.model_dump(mode="json"))
    return reply.to_json()


@tool
def plot_stress_strain_comparison(
    source_run_ids: list[str],
    labels: list[str] | None = None,
    colors: list[str] | None = None,
    line_styles: list[str] | None = None,
    markers: list[str] | None = None,
    stress_field: str = "top_shear_stress_mpa",
    use_absolute_strain: bool = False,
    use_absolute_stress: bool = False,
    show_legend: bool = True,
    legend_title: str | None = None,
    legend_location: str = "best",
    title: str | None = None,
    x_label: str | None = None,
    y_label: str | None = None,
    linewidth: float = 1.7,
    marker_size: float = 2.8,
) -> str:
    """Plot 1–12 completed shear histories on one configurable figure.

    For a single curve pass [source_run_id]; no comparison or drop detection is
    required. For drop/extrema analysis use stress_strain_analysis instead.
    source_run_ids controls curve order. labels, colors, line_styles, and markers
    may be supplied with one value per run. Legend visibility, title/location,
    axis labels, signed/absolute axes, and line/marker sizes are model-controlled.
    Nonconverged points remain connected and are counted as warning metadata.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config. Analysis must be written under the project."
    if not isinstance(source_run_ids, list) or not 1 <= len(source_run_ids) <= 12:
        return "source_run_ids must contain between 1 and 12 completed gb_shear run IDs"
    if len(set(source_run_ids)) != len(source_run_ids):
        return "source_run_ids must not contain duplicate run IDs"
    normalized_field = stress_field.strip().lower()
    if normalized_field not in STRESS_FIELDS:
        return f"Invalid stress_field {stress_field!r}. Use one of {sorted(STRESS_FIELDS)}."
    if not math.isfinite(float(linewidth)) or linewidth <= 0:
        return "linewidth must be a finite positive number"
    if not math.isfinite(float(marker_size)) or marker_size < 0:
        return "marker_size must be a finite non-negative number"

    count = len(source_run_ids)
    for name, values in (
        ("labels", labels),
        ("colors", colors),
        ("line_styles", line_styles),
        ("markers", markers),
    ):
        if values is not None and len(values) != count:
            return f"{name} must contain exactly one value per source_run_id"

    store = ProjectStore()
    service = store.runs(project_id)
    series: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    try:
        for index, source_run_id in enumerate(source_run_ids):
            source_run = service.memory.get_run(source_run_id)
            if (
                source_run.workflow is not Workflow.GB_SHEAR
                or source_run.status is not RunStatus.COMPLETED
            ):
                raise ValueError(
                    f"source run {source_run_id} must be a completed gb_shear run"
                )
            artifacts = service.memory.query_artifacts(
                run_id=source_run_id, role="run_metadata"
            )
            if len(artifacts) != 1:
                raise ValueError(
                    f"source run {source_run_id} must have exactly one run_metadata artifact"
                )
            metadata_path = verified_artifact_path(
                store=store,
                service=service,
                project_id=project_id,
                run_id=source_run_id,
                artifact=artifacts[0],
            )
            content = metadata_path.read_bytes()
            content_fingerprint = "sha256:" + hashlib.sha256(content).hexdigest()
            digest.update(source_run_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
            metadata = json.loads(content)
            arrays = metadata_points_to_arrays(metadata)
            series.append(
                {
                    "source_run_id": source_run_id,
                    "source_artifact_id": artifacts[0].artifact_id,
                    "source_fingerprint": content_fingerprint,
                    "label": (
                        str(labels[index])
                        if labels is not None
                        else source_run_id
                    ),
                    "color": colors[index] if colors is not None else None,
                    "line_style": (
                        line_styles[index] if line_styles is not None else "-"
                    ),
                    "marker": markers[index] if markers is not None else "o",
                    "point_count": len(arrays["step"]),
                    "nonconverged_point_count": sum(
                        value is False for value in arrays["converged"]
                    ),
                    "arrays": arrays,
                }
            )

        parameters = {
            "mode": "single_curve_plot" if count == 1 else "comparison_plot",
            "memory_tool": "plot_stress_strain_comparison",
            "source_run_ids": list(source_run_ids),
            "labels": [item["label"] for item in series],
            "colors": [item["color"] for item in series],
            "line_styles": [item["line_style"] for item in series],
            "markers": [item["marker"] for item in series],
            "stress_field": normalized_field,
            "use_absolute_strain": use_absolute_strain,
            "use_absolute_stress": use_absolute_stress,
            "convergence_affects_plot": False,
            "show_legend": show_legend,
            "legend_title": legend_title,
            "legend_location": legend_location,
            "title": title,
            "x_label": x_label,
            "y_label": y_label,
            "linewidth": linewidth,
            "marker_size": marker_size,
        }
        run = service.create_running_run(
            workflow=Workflow.STRESS_STRAIN_ANALYSIS,
            memory_tool="plot_stress_strain_comparison",
            parameters=parameters,
            source_fingerprint=f"sha256:{digest.hexdigest()}",
            sources=[
                RunSourceRecord(
                    source_run_id=item["source_run_id"],
                    relation="comparison_curve",
                    ordinal=index,
                    source_fingerprint=item["source_fingerprint"],
                    metadata={
                        "label": item["label"],
                        "source_artifact_id": item["source_artifact_id"],
                    },
                )
                for index, item in enumerate(series)
            ],
        )
        output_dir = service.artifacts.run_directory(run.run_id)
        data_path = output_dir / "stress_strain_comparison.json"
        plot_path = output_dir / "stress_strain_comparison.png"
        payload = {"version": 1, **parameters, "series": series}
        write_json_atomic(data_path, payload)
        _plot_stress_strain_comparison(
            series,
            plot_path,
            stress_field=normalized_field,
            use_absolute_strain=use_absolute_strain,
            use_absolute_stress=use_absolute_stress,
            show_legend=show_legend,
            legend_title=legend_title,
            legend_location=legend_location,
            title=title,
            x_label=x_label,
            y_label=y_label,
            linewidth=linewidth,
            marker_size=marker_size,
        )
        data_artifact = service.register_artifact(
            run.run_id,
            "stress_strain_comparison_data",
            data_path.name,
            ProvenanceClass.DERIVED,
            media_type="application/json",
            metadata={"source_run_ids": source_run_ids},
        )
        plot_artifact = service.register_artifact(
            run.run_id,
            "stress_strain_comparison_plot",
            plot_path.name,
            ProvenanceClass.DERIVED,
            media_type="image/png",
            metadata={"source_run_ids": source_run_ids},
        )
        result = {
            "source_run_ids": source_run_ids,
            "series": [
                {
                    key: item[key]
                    for key in (
                        "source_run_id",
                        "label",
                        "color",
                        "line_style",
                        "marker",
                        "point_count",
                        "nonconverged_point_count",
                    )
                }
                for item in series
            ],
            "data": data_artifact.path,
            "plot": plot_artifact.path,
        }
        terminal = service.complete_run(
            run.run_id, result=result, memory_tool="plot_stress_strain_comparison"
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

    reply = RunToolResult(
        run_id=terminal.run_id,
        workflow=terminal.workflow,
        status=terminal.status,
        manifest=terminal.manifest_path,
        artifacts=[
            {"role": data_artifact.role, "path": data_artifact.path},
            {"role": plot_artifact.role, "path": plot_artifact.path},
        ],
        result=result,
    )
    reply.model_view = stress_strain_comparison_model_view(
        reply.model_dump(mode="json"), series, stress_field=normalized_field,
        use_absolute_strain=use_absolute_strain, use_absolute_stress=use_absolute_stress)
    return reply.to_json()
