from __future__ import annotations

from pathlib import Path

from model_v3.scenarios.generate_figures import generate_figures

from .figure_test_utils import write_minimal_figure_experiment


def test_figure_generation_writes_stable_png_and_pdf_files(tmp_path: Path) -> None:
    experiment_root, figures_root = write_minimal_figure_experiment(tmp_path)

    summary = generate_figures(
        experiment_root=experiment_root,
        figures_root=figures_root,
        comparison_root=experiment_root / "summaries" / "comparison_level",
        realization_metrics=experiment_root / "summaries" / "realization_level" / "scenario_leaf_metrics.csv",
        scenario_aggregates=experiment_root / "summaries" / "scenario_level" / "scenario_aggregate_metrics.csv",
        comparison_definitions=Path("config/model_v3/scenario_tree/comparison_definitions.yaml"),
        formats=["png", "pdf"],
        write_metadata_flag=True,
        write_captions_flag=True,
    )

    assert summary["figures_written"] == 17
    assert summary["png_files"] == 17
    assert summary["pdf_files"] == 17
    assert (figures_root / "structure" / "scenario_tree_structure.png").exists()
    assert (figures_root / "structure" / "scenario_tree_structure.pdf").exists()
    assert all(" " not in path.name for path in figures_root.glob("*/*.*"))
    assert not any("xlsx" in row["source_data_files"] for row in summary["metadata_rows"])
