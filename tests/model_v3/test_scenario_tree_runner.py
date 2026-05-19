"""Tests for the scenario-tree runner orchestration layer."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenarios import run_scenario_tree  # noqa: E402
from model_v3.scenarios.registry import read_registry  # noqa: E402
from model_v3.scenarios.registry import append_registry_row  # noqa: E402
from model_v3.scenarios.selection import load_leaf_records, select_leaf_records  # noqa: E402


BASELINE_LEAF_ID = "baseline_1981_2005__historical__tech_current_stock__seed_0000"
FUTURE_LEAF_ID = "mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0000"


def _leaf_parts(leaf_id: str) -> dict[str, str]:
    climate_window_id, climate_pathway_id, technology_case_id, realization_id = leaf_id.split("__")
    scenario_id = "__".join((climate_window_id, climate_pathway_id, technology_case_id))
    return {
        "scenario_leaf_id": leaf_id,
        "scenario_id": scenario_id,
        "climate_window_id": climate_window_id,
        "climate_pathway_id": climate_pathway_id,
        "technology_case_id": technology_case_id,
        "realization_id": realization_id,
    }


def _write_leaf(root: Path, leaf_id: str, *, missing_climate: bool = False) -> None:
    experiment_root = root / "scenario_tree"
    run_dir = experiment_root / "runs" / leaf_id
    outputs_dir = run_dir / "outputs"
    logs_dir = run_dir / "logs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    parts = _leaf_parts(leaf_id)
    climate_path = root / f"{leaf_id}_weather.csv"
    if not missing_climate:
        climate_path.write_text("timestamp,T_out_C,I_solar_W_m2\n2050-01-01T12:00:00,5.0,10.0\n", encoding="utf-8")
    belgian_inputs = root / "belgian_technology_inputs.yaml"
    belgian_inputs.write_text("technology_inputs: {}\n", encoding="utf-8")
    technology_cases = root / "technology_cases.yaml"
    technology_cases.write_text("technology_cases: {}\n", encoding="utf-8")

    run_config = {
        "schema_version": "model_v3.scenario_leaf_config.v1",
        "scenario_leaf": {
            "id": leaf_id,
            **parts,
        },
        "climate": {
            "window_id": parts["climate_window_id"],
            "pathway_id": parts["climate_pathway_id"],
            "forcing_file": str(climate_path),
            "analysis_start": "2050-01-01",
            "analysis_end": "2050-12-31",
        },
        "technology": {
            "case_id": parts["technology_case_id"],
            "metadata_file": str(technology_cases),
            "belgian_technology_inputs": str(belgian_inputs),
        },
        "stochastic": {
            "realization_id": parts["realization_id"],
            "seed_value": 0,
            "cohort_size": 3,
        },
        "model_options": {"run_mode": "scenario_leaf", "write_outputs": True},
        "output": {
            "run_dir": str(run_dir),
            "outputs_dir": str(outputs_dir),
            "logs_dir": str(logs_dir),
        },
        "validation": {"config_complete": True, "missing_required_inputs": []},
        "provenance": {"phase": 3},
    }
    (run_dir / "run_config.yaml").write_text(yaml.safe_dump(run_config, sort_keys=False), encoding="utf-8")
    inputs_manifest = {
        "schema_version": "model_v3.inputs_manifest.v1",
        "scenario_leaf_id": leaf_id,
        "scenario_id": parts["scenario_id"],
        "climate_forcing": {"file": str(climate_path), "exists": not missing_climate},
        "technology": {"belgian_technology_inputs": str(belgian_inputs), "belgian_technology_inputs_exists": True},
        "stochastic": {"seed_value": 0, "cohort_size": 3},
        "validation": {"config_complete": True, "missing_required_inputs": []},
    }
    (run_dir / "inputs_manifest.yaml").write_text(yaml.safe_dump(inputs_manifest, sort_keys=False), encoding="utf-8")


def _write_index(root: Path, leaf_ids: list[str]) -> Path:
    path = root / "scenario_tree" / "manifests" / "scenario_leaf_index.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "scenario_leaf_id",
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "realization_id",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for leaf_id in leaf_ids:
            writer.writerow(_leaf_parts(leaf_id))
    return path


def _space(tmp_path: Path, leaf_ids: list[str] | None = None) -> tuple[Path, Path]:
    leaf_ids = leaf_ids or [BASELINE_LEAF_ID, FUTURE_LEAF_ID]
    for leaf_id in leaf_ids:
        _write_leaf(tmp_path, leaf_id)
    return tmp_path / "scenario_tree", _write_index(tmp_path, leaf_ids)


def test_dry_run_plan_is_deterministic_and_does_not_create_outputs(tmp_path: Path) -> None:
    experiment_root, index = _space(tmp_path)
    records = load_leaf_records(index)

    plan1 = run_scenario_tree.build_run_plan(records, experiment_root=experiment_root, registry_rows=[], repo_root=tmp_path)
    plan2 = run_scenario_tree.build_run_plan(records, experiment_root=experiment_root, registry_rows=[], repo_root=tmp_path)

    assert [item.record.scenario_leaf_id for item in plan1] == [item.record.scenario_leaf_id for item in plan2]
    assert [item.status for item in plan1] == ["eligible", "eligible"]
    assert not any((experiment_root / "runs" / BASELINE_LEAF_ID / "outputs").iterdir())


def test_dry_run_allows_missing_output_and_log_directories(tmp_path: Path) -> None:
    experiment_root, index = _space(tmp_path, [BASELINE_LEAF_ID])
    (experiment_root / "runs" / BASELINE_LEAF_ID / "outputs").rmdir()
    (experiment_root / "runs" / BASELINE_LEAF_ID / "logs").rmdir()

    plan = run_scenario_tree.build_run_plan(
        load_leaf_records(index),
        experiment_root=experiment_root,
        registry_rows=[],
        repo_root=tmp_path,
    )

    assert plan[0].status == "eligible"
    assert not (experiment_root / "runs" / BASELINE_LEAF_ID / "outputs").exists()
    assert not (experiment_root / "runs" / BASELINE_LEAF_ID / "logs").exists()


def test_dry_run_detects_missing_config(tmp_path: Path) -> None:
    experiment_root, index = _space(tmp_path, [BASELINE_LEAF_ID])
    (experiment_root / "runs" / BASELINE_LEAF_ID / "run_config.yaml").unlink()

    plan = run_scenario_tree.build_run_plan(
        load_leaf_records(index),
        experiment_root=experiment_root,
        registry_rows=[],
        repo_root=tmp_path,
    )

    assert plan[0].status == "invalid"
    assert plan[0].skip_reason == "missing_config"


def test_dry_run_detects_missing_climate_input(tmp_path: Path) -> None:
    experiment_root = tmp_path / "scenario_tree"
    _write_leaf(tmp_path, BASELINE_LEAF_ID, missing_climate=True)
    index = _write_index(tmp_path, [BASELINE_LEAF_ID])

    plan = run_scenario_tree.build_run_plan(
        load_leaf_records(index),
        experiment_root=experiment_root,
        registry_rows=[],
        repo_root=tmp_path,
    )

    assert plan[0].status == "invalid"
    assert plan[0].skip_reason == "missing_input"


def test_single_leaf_selection_and_mock_execution_writes_registry_and_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    experiment_root, index = _space(tmp_path)
    record = select_leaf_records(load_leaf_records(index), scenario_leaf_id=BASELINE_LEAF_ID)[0]
    registry_file = experiment_root / "manifests" / "run_registry.csv"

    def fake_run_model_from_config(config_path: Path, **_: object) -> dict[str, object]:
        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        output_path = Path(config["output"]["outputs_dir"]) / "fake_output.txt"
        output_path.write_text("ok\n", encoding="utf-8")
        print("fake stdout")
        return {"status": "success", "outputs": [str(output_path)], "metrics": {}, "message": "ok"}

    monkeypatch.setattr(run_scenario_tree, "run_model_from_config", fake_run_model_from_config)
    result = run_scenario_tree.execute_leaf(
        record,
        experiment_root=experiment_root,
        registry_file=registry_file,
        force=False,
        ignore_stale_running=False,
        base_model_config_path=tmp_path / "unused.yaml",
        repo_root=tmp_path,
    )

    assert result["status"] == "success"
    rows = read_registry(registry_file)
    assert len(rows) == 1
    assert rows[0]["scenario_leaf_id"] == BASELINE_LEAF_ID
    assert rows[0]["status"] == "success"
    attempt_log = Path(rows[0]["log_path"])
    assert (attempt_log / "run_stdout.log").read_text(encoding="utf-8").strip() == "fake stdout"
    assert (attempt_log / "run_stderr.log").exists()
    assert (attempt_log / "runner_status.yaml").exists()


def test_failure_is_logged_without_deleting_run_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    experiment_root, index = _space(tmp_path, [BASELINE_LEAF_ID])
    record = load_leaf_records(index)[0]
    registry_file = experiment_root / "manifests" / "run_registry.csv"

    def failing_run_model_from_config(config_path: Path, **_: object) -> dict[str, object]:
        _ = config_path
        raise RuntimeError("mock model failure")

    monkeypatch.setattr(run_scenario_tree, "run_model_from_config", failing_run_model_from_config)
    result = run_scenario_tree.execute_leaf(
        record,
        experiment_root=experiment_root,
        registry_file=registry_file,
        force=False,
        ignore_stale_running=False,
        base_model_config_path=tmp_path / "unused.yaml",
        repo_root=tmp_path,
    )

    assert result["status"] == "failed"
    rows = read_registry(registry_file)
    assert rows[0]["status"] == "failed"
    assert rows[0]["error_type"] == "RuntimeError"
    assert (experiment_root / "runs" / BASELINE_LEAF_ID).is_dir()


def test_successful_leaf_is_skipped_unless_forced(tmp_path: Path) -> None:
    experiment_root, index = _space(tmp_path, [BASELINE_LEAF_ID])
    registry_file = experiment_root / "manifests" / "run_registry.csv"
    append_registry_row(
        registry_file,
        {
            "run_attempt_id": "attempt_1",
            "scenario_leaf_id": BASELINE_LEAF_ID,
            "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
            "climate_window_id": "baseline_1981_2005",
            "climate_pathway_id": "historical",
            "technology_case_id": "tech_current_stock",
            "realization_id": "seed_0000",
            "timestamp_start_utc": "2026-05-09T00:00:00Z",
            "timestamp_end_utc": "2026-05-09T00:00:01Z",
            "duration_seconds": "1.000",
            "status": "success",
        },
    )
    records = load_leaf_records(index)

    plan = run_scenario_tree.build_run_plan(
        records,
        experiment_root=experiment_root,
        registry_rows=read_registry(registry_file),
        force=False,
        repo_root=tmp_path,
    )
    forced = run_scenario_tree.build_run_plan(
        records,
        experiment_root=experiment_root,
        registry_rows=read_registry(registry_file),
        force=True,
        repo_root=tmp_path,
    )

    assert plan[0].status == "skipped"
    assert plan[0].skip_reason == "already_successful"
    assert forced[0].status == "eligible"
