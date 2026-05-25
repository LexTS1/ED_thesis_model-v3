from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from model_v3.scenarios.generate_figures import generate_figures
from model_v3.scenarios.validate_figures import FigureValidationError, validate_figures

from .figure_test_utils import write_minimal_figure_experiment


def _generate(tmp_path: Path) -> tuple[Path, Path]:
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
    return experiment_root, figures_root


def test_figure_validator_passes_on_generated_fixture(tmp_path: Path) -> None:
    experiment_root, figures_root = _generate(tmp_path)

    summary = validate_figures(figures_root=figures_root, experiment_root=experiment_root)

    assert summary["figures_checked"] == 24
    assert summary["manual_spreadsheet_dependencies"] == 0
    assert summary["near_future_includes_2050"] is False
    assert summary["mid_century_includes_2050"] is True


def test_missing_figure_file_is_detected(tmp_path: Path) -> None:
    experiment_root, figures_root = _generate(tmp_path)
    (figures_root / "climate" / "climate_solar_by_window_rcp.png").unlink()

    with pytest.raises(FigureValidationError, match="Missing required figure file"):
        validate_figures(figures_root=figures_root, experiment_root=experiment_root)


def test_missing_caption_is_detected(tmp_path: Path) -> None:
    experiment_root, figures_root = _generate(tmp_path)
    captions_path = figures_root / "thesis_caption_drafts.md"
    text = captions_path.read_text(encoding="utf-8").replace("### fig_ev_charging_by_scenario", "### removed_ev_caption")
    captions_path.write_text(text, encoding="utf-8")

    with pytest.raises(FigureValidationError, match="Caption draft missing"):
        validate_figures(figures_root=figures_root, experiment_root=experiment_root)


def test_missing_metadata_row_is_detected(tmp_path: Path) -> None:
    experiment_root, figures_root = _generate(tmp_path)
    metadata_path = figures_root / "metadata" / "figure_metadata.csv"
    metadata = pd.read_csv(metadata_path)
    metadata = metadata[metadata["figure_id"] != "fig_ev_charging_by_scenario"]
    metadata.to_csv(metadata_path, index=False)

    with pytest.raises(FigureValidationError, match="Metadata missing figure ID"):
        validate_figures(figures_root=figures_root, experiment_root=experiment_root)


def test_timestamped_filename_is_rejected(tmp_path: Path) -> None:
    experiment_root, figures_root = _generate(tmp_path)
    bad_path = figures_root / "climate" / "climate_temperature_20260509T120000.png"
    bad_path.write_bytes((figures_root / "climate" / "climate_temperature_by_window_rcp.png").read_bytes())

    with pytest.raises(FigureValidationError, match="timestamped"):
        validate_figures(figures_root=figures_root, experiment_root=experiment_root)
