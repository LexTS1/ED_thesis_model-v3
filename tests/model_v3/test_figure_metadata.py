from __future__ import annotations

from pathlib import Path

import pandas as pd

from model_v3.scenarios.generate_figures import generate_figures

from .figure_test_utils import write_minimal_figure_experiment


def test_metadata_rows_are_unique_and_reference_outputs(tmp_path: Path) -> None:
    experiment_root, figures_root = write_minimal_figure_experiment(tmp_path)

    generate_figures(
        experiment_root=experiment_root,
        figures_root=figures_root,
        comparison_root=experiment_root / "summaries" / "comparison_level",
        realization_metrics=experiment_root / "summaries" / "realization_level" / "scenario_leaf_metrics.csv",
        scenario_aggregates=experiment_root / "summaries" / "scenario_level" / "scenario_aggregate_metrics.csv",
        comparison_definitions=Path("config/scenario_tree/comparison_definitions.yaml"),
        formats=["png", "pdf"],
        write_metadata_flag=True,
        write_captions_flag=True,
    )

    metadata = pd.read_csv(figures_root / "metadata" / "figure_metadata.csv")

    assert metadata["figure_id"].is_unique
    assert len(metadata) == 17
    assert metadata["metrics_used"].notna().all()
    for _, row in metadata.iterrows():
        assert row["figure_file_png"].endswith(".png")
        assert row["figure_file_pdf"].endswith(".pdf")
        assert row["source_data_files"]


def test_caption_drafts_include_each_figure_and_metric_context(tmp_path: Path) -> None:
    experiment_root, figures_root = write_minimal_figure_experiment(tmp_path)

    generate_figures(
        experiment_root=experiment_root,
        figures_root=figures_root,
        comparison_root=experiment_root / "summaries" / "comparison_level",
        realization_metrics=experiment_root / "summaries" / "realization_level" / "scenario_leaf_metrics.csv",
        scenario_aggregates=experiment_root / "summaries" / "scenario_level" / "scenario_aggregate_metrics.csv",
        comparison_definitions=Path("config/scenario_tree/comparison_definitions.yaml"),
        formats=["png", "pdf"],
        write_metadata_flag=True,
        write_captions_flag=True,
    )

    captions = (figures_root / "thesis_caption_drafts.md").read_text(encoding="utf-8")

    assert captions.count("### fig_") == 17
    assert "annual grid import" in captions.lower()
    assert "canonical climate analysis window" in captions
    assert "2050" in captions
