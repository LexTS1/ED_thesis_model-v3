from __future__ import annotations

import csv
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenarios.audit_scenario_tree import build_traceability_matrix, run_audit  # noqa: E402


LEAF_ID = "baseline_1981_2005__historical__tech_current_stock__seed_0000"
SCENARIO_ID = "baseline_1981_2005__historical__tech_current_stock"


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    experiment_root = tmp_path / "scenario_tree"
    config_root = tmp_path / "config"
    figures_root = tmp_path / "figures"
    run_dir = experiment_root / "runs" / LEAF_ID
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    (figures_root / "metadata").mkdir(parents=True)

    climate = tmp_path / "weather.csv"
    climate.write_text("timestamp,T_out_C,I_solar_W_m2\n1981-01-01T00:00:00,5,10\n", encoding="utf-8")
    tech_inputs = tmp_path / "belgian_technology_inputs.yaml"
    tech_inputs.write_text("technology_inputs: {}\n", encoding="utf-8")
    (config_root / "technology_cases.yaml").parent.mkdir(parents=True)
    (config_root / "technology_cases.yaml").write_text("technology_cases: {}\n", encoding="utf-8")
    (config_root / "climate_windows.yaml").write_text("climate_windows: {}\n", encoding="utf-8")
    (config_root / "realization_policy.yaml").write_text(
        "realization_policy:\n  number_of_seeds: 1\n", encoding="utf-8"
    )
    (config_root / "scenario_tree_schema.yaml").write_text(
        "schema_version: '1.0.0'\nclimate_pathways:\n  future_pathways: []\n", encoding="utf-8"
    )

    run_config = {
        "scenario_leaf": {
            "id": LEAF_ID,
            "scenario_id": SCENARIO_ID,
            "climate_window_id": "baseline_1981_2005",
            "climate_pathway_id": "historical",
            "technology_case_id": "tech_current_stock",
            "realization_id": "seed_0000",
        },
        "climate": {
            "forcing_file": str(climate),
            "analysis_start": "1981-01-01",
            "analysis_end": "2005-12-31",
            "source_file_window": "1981-2005",
        },
        "technology": {
            "case_id": "tech_current_stock",
            "metadata_file": str(config_root / "technology_cases.yaml"),
            "belgian_technology_inputs": str(tech_inputs),
        },
        "stochastic": {"realization_id": "seed_0000", "seed_index": 0, "seed_value": 0, "cohort_size": 3},
        "output": {"outputs_dir": str(outputs_dir)},
    }
    (run_dir / "run_config.yaml").write_text(yaml.safe_dump(run_config, sort_keys=False), encoding="utf-8")
    inputs_manifest = {
        "scenario_leaf_id": LEAF_ID,
        "scenario_id": SCENARIO_ID,
        "climate_forcing": {
            "file": str(climate),
            "analysis_start": "1981-01-01",
            "analysis_end": "2005-12-31",
            "source_file_window": "1981-2005",
        },
        "technology": {
            "metadata_file": str(config_root / "technology_cases.yaml"),
            "belgian_technology_inputs": str(tech_inputs),
        },
        "stochastic": {"seed_index": 0, "seed_value": 0, "cohort_size": 3},
    }
    (run_dir / "inputs_manifest.yaml").write_text(yaml.safe_dump(inputs_manifest, sort_keys=False), encoding="utf-8")
    (outputs_dir / "standardized_leaf_summary.csv").write_text("scenario_leaf_id\n" + LEAF_ID + "\n", encoding="utf-8")

    _write_csv(
        experiment_root / "manifests" / "scenario_leaf_index.csv",
        [
            {
                "scenario_leaf_id": LEAF_ID,
                "scenario_id": SCENARIO_ID,
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "realization_id": "seed_0000",
                "run_config_path": str(run_dir / "run_config.yaml"),
                "inputs_manifest_path": str(run_dir / "inputs_manifest.yaml"),
            }
        ],
    )
    _write_csv(
        experiment_root / "manifests" / "run_registry.csv",
        [
            {
                "run_attempt_id": "attempt",
                "scenario_leaf_id": LEAF_ID,
                "scenario_id": SCENARIO_ID,
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "realization_id": "seed_0000",
                "timestamp_start_utc": "2026-05-09T00:00:00Z",
                "status": "success",
                "config_path": str(run_dir / "run_config.yaml"),
                "inputs_manifest_path": str(run_dir / "inputs_manifest.yaml"),
                "climate_forcing_file": str(climate),
                "belgian_technology_inputs": str(tech_inputs),
                "random_seed": "0",
                "cohort_size": "3",
                "model_version": "test",
                "output_path": str(outputs_dir),
            }
        ],
    )
    _write_csv(
        experiment_root / "summaries" / "realization_level" / "scenario_leaf_metrics.csv",
        [
            {
                "scenario_leaf_id": LEAF_ID,
                "scenario_id": SCENARIO_ID,
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "realization_id": "seed_0000",
                "seed_index": "0",
                "seed_value": "0",
                "cohort_size": "3",
                "analysis_start": "1981-01-01",
                "analysis_end": "2005-12-31",
                "source_file_window": "1981-2005",
                "raw_outputs_dir": str(outputs_dir),
            }
        ],
    )
    _write_csv(experiment_root / "summaries" / "scenario_level" / "scenario_aggregate_metrics.csv", [{"scenario_id": SCENARIO_ID}])
    _write_csv(figures_root / "metadata" / "figure_metadata.csv", [{"figure_id": "fig_test"}])
    return experiment_root, config_root, figures_root


def test_audit_script_runs_on_small_synthetic_fixture(tmp_path: Path) -> None:
    experiment_root, config_root, figures_root = _fixture(tmp_path)

    summary = run_audit(
        experiment_root=experiment_root,
        config_root=config_root,
        figures_root=figures_root,
        reports_root=tmp_path / "reports",
        write_reports=True,
    )

    assert summary["traceability_complete"] is True
    assert summary["counts"]["successful_scenario_leaves"] == 1
    assert (tmp_path / "reports" / "scenario_tree_traceability_matrix.csv").exists()


def test_missing_run_config_causes_traceability_failure(tmp_path: Path) -> None:
    experiment_root, config_root, _ = _fixture(tmp_path)
    (experiment_root / "runs" / LEAF_ID / "run_config.yaml").unlink()

    rows, warnings = build_traceability_matrix(experiment_root=experiment_root, config_root=config_root)

    assert rows[0]["traceability_complete"] == "false"
    assert "run_config_exists" in rows[0]["missing_traceability_fields"]
    assert warnings


def test_missing_climate_forcing_causes_traceability_failure(tmp_path: Path) -> None:
    experiment_root, config_root, _ = _fixture(tmp_path)
    Path(next(csv.DictReader((experiment_root / "manifests" / "run_registry.csv").open()))["climate_forcing_file"]).unlink()

    rows, _ = build_traceability_matrix(experiment_root=experiment_root, config_root=config_root)

    assert rows[0]["traceability_complete"] == "false"
    assert "climate_forcing_exists" in rows[0]["missing_traceability_fields"]


def test_missing_technology_input_causes_traceability_failure(tmp_path: Path) -> None:
    experiment_root, config_root, _ = _fixture(tmp_path)
    Path(next(csv.DictReader((experiment_root / "manifests" / "run_registry.csv").open()))["belgian_technology_inputs"]).unlink()

    rows, _ = build_traceability_matrix(experiment_root=experiment_root, config_root=config_root)

    assert rows[0]["traceability_complete"] == "false"
    assert "belgian_technology_inputs_exists" in rows[0]["missing_traceability_fields"]


def test_missing_standardized_summary_causes_traceability_failure(tmp_path: Path) -> None:
    experiment_root, config_root, _ = _fixture(tmp_path)
    (experiment_root / "runs" / LEAF_ID / "outputs" / "standardized_leaf_summary.csv").unlink()

    rows, _ = build_traceability_matrix(experiment_root=experiment_root, config_root=config_root)

    assert rows[0]["traceability_complete"] == "false"
    assert "standardized_leaf_summary_exists" in rows[0]["missing_traceability_fields"]


def test_successful_output_row_answers_all_four_traceability_questions(tmp_path: Path) -> None:
    experiment_root, config_root, _ = _fixture(tmp_path)

    rows, _ = build_traceability_matrix(experiment_root=experiment_root, config_root=config_root)
    row = rows[0]

    assert row["climate_forcing_file"]
    assert row["technology_case_id"] == "tech_current_stock"
    assert row["seed_value"] == "0"
    assert row["run_config"]
    assert row["traceability_complete"] == "true"
