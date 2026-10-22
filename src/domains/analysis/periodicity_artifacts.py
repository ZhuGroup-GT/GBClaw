"""Files a GB periodicity run writes, named once for the tool and the analysis subprocess.

Standard library only: ``periodicity_tools`` imports this module and never the
periodicity package, which keeps numpy and OVITO out of the server process.
"""

# Joint structure/strain outputs, keyed as in the multiscale ``artifact_paths``.
MULTISCALE_FILES = {
    "summary_image": "multiscale_review/multiscale_summary.png",
    "multiscale_report": "multiscale_review/multiscale_review.json",
    "candidate_table": "multiscale_review/all_candidates.csv",
    "candidate_details": "multiscale_review/ALL_CANDIDATES.md",
    "elastic_report": "elastic/elastic_periodicity.json",
    "elastic_atoms": "elastic/elastic_atoms.npz",
    "strain_peak_table": "elastic/strain_fft_peaks.csv",
    "strain_peak_review": "elastic/strain_fft_review.json",
    "structure_peak_table": "elastic/structure_fft_peaks.csv",
    "review_index": "elastic/review_index.json",
    "local_strain_report": "local_strain/local_strain_events.json",
    "local_strain_image": "local_strain/local_strain_events.png",
    "coordinate_rows_report": "coordinate_rows/coordinate_rows.json",
    "coordinate_rows_image": "coordinate_rows/coordinate_rows.png",
}
# Not drawn when no atom row qualifies (coordinate_rows.json then says why).
OPTIONAL_MULTISCALE_FILES = frozenset({"coordinate_rows_image"})
MULTISCALE_STATUS_FILE = "multiscale_review/analysis_status.json"

# Every artifact the tool may register: index role, run-relative file and media
# type, in registration order. Files are registered only when they exist.
ARTIFACTS = (
    ("gb_periodicity_data", "period_candidates.json", "application/json"),
    ("gb_periodicity_summary", "agent_summary.json", "application/json"),
    ("pbc_size_comparison", "pbc_size_comparison.json", "application/json"),
    ("gb_periodicity_overview", MULTISCALE_FILES["summary_image"], "image/png"),
    ("gb_periodicity_candidates", "candidate_evidence.png", "image/png"),
    ("gb_periodicity_diagnostics", "periodicity_diagnostics.png", "image/png"),
    ("gb_periodicity_in_plane", "in_plane_periodicity.png", "image/png"),
    ("gb_periodicity_modulation", "long_period_modulation.png", "image/png"),
    ("gb_periodicity_curves", "candidate_curves.npz", "application/octet-stream"),
    ("gb_periodicity_profiles", "modulation_profiles.json", "application/json"),
    ("gb_periodicity_boundary", "boundary.json", "application/json"),
    ("gb_periodicity_selection", "boundary_selection.npz", "application/octet-stream"),
    ("gb_periodicity_crystal_mapping", "crystal_mapping.json", "application/json"),
    ("gb_periodicity_ptm", "ptm_cache/ptm_atoms.npz", "application/octet-stream"),
    ("gb_periodicity_ptm_metadata", "ptm_cache/ptm_metadata.json", "application/json"),
    ("gb_periodicity_multiscale_status", MULTISCALE_STATUS_FILE, "application/json"),
    ("gb_periodicity_multiscale_data", MULTISCALE_FILES["multiscale_report"], "application/json"),
    ("gb_periodicity_candidate_table", MULTISCALE_FILES["candidate_table"], "text/csv"),
    ("gb_periodicity_candidate_notes", MULTISCALE_FILES["candidate_details"], "text/markdown"),
    ("gb_periodicity_elastic_data", MULTISCALE_FILES["elastic_report"], "application/json"),
    ("gb_periodicity_elastic_atoms", MULTISCALE_FILES["elastic_atoms"], "application/octet-stream"),
    ("gb_periodicity_elastic_index", MULTISCALE_FILES["review_index"], "application/json"),
    ("gb_periodicity_structure_fft_peaks", MULTISCALE_FILES["structure_peak_table"], "text/csv"),
    ("gb_periodicity_strain_fft_peaks", MULTISCALE_FILES["strain_peak_table"], "text/csv"),
    ("gb_periodicity_strain_fft_review", MULTISCALE_FILES["strain_peak_review"], "application/json"),
    ("gb_periodicity_local_strain_data", MULTISCALE_FILES["local_strain_report"], "application/json"),
    ("gb_periodicity_local_strain_plot", MULTISCALE_FILES["local_strain_image"], "image/png"),
    ("gb_periodicity_coordinate_rows_data", MULTISCALE_FILES["coordinate_rows_report"], "application/json"),
    ("gb_periodicity_coordinate_rows_plot", MULTISCALE_FILES["coordinate_rows_image"], "image/png"),
)
