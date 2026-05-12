from __future__ import annotations

from pathlib import Path

from model_v3.documentation.build_model_handbook import (
    build_handbook_markdown,
    collect_context,
    generate_handbook_figures,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _minimal_repo(root: Path) -> None:
    _write(
        root / "config/model_v3/scenario_tree/climate_windows.yaml",
        """
schema_version: "1.0.0"
temporal_window_policy:
  raw_processed_files_may_overlap: true
  overlapping_years: [2050]
  canonical_analysis_windows_must_overlap: false
  year_2050_assignment: mid_century_2050_2070
  near_future_excludes_2050: true
climate_windows:
  baseline_1981_2005:
    climate_window_id: baseline_1981_2005
    canonical_start: "1981-01-01"
    canonical_end: "2005-12-31"
    source_file_window: "1981-2005"
    window_type: baseline
    allowed_pathways: [historical]
  near_future_2030_2049:
    climate_window_id: near_future_2030_2049
    canonical_start: "2030-01-01"
    canonical_end: "2049-12-31"
    source_file_window: "2030-2050"
    window_type: future
    allowed_pathways: [rcp_2_6, rcp_4_5, rcp_8_5]
  mid_century_2050_2070:
    climate_window_id: mid_century_2050_2070
    canonical_start: "2050-01-01"
    canonical_end: "2070-12-31"
    source_file_window: "2050-2070"
    window_type: future
    allowed_pathways: [rcp_2_6, rcp_4_5, rcp_8_5]
  long_term_2080_2100:
    climate_window_id: long_term_2080_2100
    canonical_start: "2080-01-01"
    canonical_end: "2100-12-31"
    source_file_window: "2080-2100"
    window_type: future
    allowed_pathways: [rcp_2_6, rcp_4_5, rcp_8_5]
""",
    )
    _write(
        root / "config/model_v3/scenario_tree/technology_cases.yaml",
        """
technology_cases:
  tech_current_stock:
    label: Current stock
    applicable_window_types: [baseline]
  tech_frozen_stock:
    label: Frozen stock
    applicable_window_types: [future]
  tech_moderate_electrification:
    label: Moderate electrification
    applicable_window_types: [future]
  tech_high_electrification_pv_ev:
    label: High electrification with PV and EV
    applicable_window_types: [future]
""",
    )
    _write(root / "config/model_v3/scenario_tree/scenario_tree_schema.yaml", "schema_version: '1.0.0'\n")
    _write(root / "config/model_v3/scenario_tree/realization_policy.yaml", "realization_policy:\n  number_of_seeds: 100\n")
    _write(root / "config/model_v3/scenario_tree/comparison_definitions.yaml", "comparison_groups: {}\n")
    _write(root / "config/model_v3/belgian_technology_inputs.yaml", "technologies: {}\n")
    _write(root / "model_v3/experiments/scenario_tree/manifests/scenario_leaf_index.csv", "scenario_leaf_id,scenario_id\n")
    _write(root / "model_v3/experiments/scenario_tree/manifests/run_registry.csv", "scenario_leaf_id,status\n")
    _write(root / "docs/model_v3_scenario_tree_design.md", "# design\n")
    _write(root / "src/model_v3/interfaces.py", "class InputDataset: pass\n")


def test_handbook_source_generation_minimal_repo(tmp_path: Path) -> None:
    _minimal_repo(tmp_path)
    context = collect_context(tmp_path)
    context.figure_infos = generate_handbook_figures(context, tmp_path / "docs/model_v3_handbook_assets", write_figures=False)

    markdown = build_handbook_markdown(context)

    assert "# Terminology" in markdown
    assert "# Chapter 13 - Caveats, gaps, and limitations" in markdown
    assert "2050 is assigned only to the mid-century canonical analysis window" in markdown


def test_missing_expected_files_are_marked_missing(tmp_path: Path) -> None:
    _minimal_repo(tmp_path)
    context = collect_context(tmp_path)
    markdown = build_handbook_markdown(context)

    assert "Appendix D - Known missing items" in markdown
    assert "| reports/scenario_tree_validation_report.md | missing |" in markdown
