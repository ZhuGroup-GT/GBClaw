"""The analysis worker's tool set."""

from __future__ import annotations

from domains.analysis.neb_mep.tool import analyze_neb_mep, plot_neb_mep
from domains.analysis.periodicity_tools import analyze_gb_periodicity
from domains.analysis.rendering.tool import render_atomic_structure, render_gb_plan_view
from domains.analysis.stress_strain.tool import (
    plot_stress_strain_comparison,
    stress_strain_analysis,
)

ANALYSIS_AGENT_TOOLS = [
    analyze_gb_periodicity,
    stress_strain_analysis,
    plot_stress_strain_comparison,
    analyze_neb_mep,
    plot_neb_mep,
    render_atomic_structure,
    render_gb_plan_view,
]


def analysis_agent_tools() -> list:
    """Tool provider for the analysis worker (see domains/analysis/extension.py)."""
    return list(ANALYSIS_AGENT_TOOLS)
