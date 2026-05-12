from __future__ import annotations

import csv
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenarios.audit_scenario_tree import TRACEABILITY_COLUMNS, build_traceability_matrix  # noqa: E402
from tests.model_v3.test_scenario_tree_audit import LEAF_ID, _fixture  # noqa: E402


def test_required_columns_exist_in_traceability_matrix(tmp_path: Path) -> None:
    experiment_root, config_root, _ = _fixture(tmp_path)

    rows, _ = build_traceability_matrix(experiment_root=experiment_root, config_root=config_root)

    assert set(TRACEABILITY_COLUMNS).issubset(rows[0])


def test_one_row_per_successful_scenario_leaf(tmp_path: Path) -> None:
    experiment_root, config_root, _ = _fixture(tmp_path)

    rows, _ = build_traceability_matrix(experiment_root=experiment_root, config_root=config_root)
    success_count = sum(
        1
        for row in csv.DictReader((experiment_root / "manifests" / "run_registry.csv").open())
        if row["status"] == "success"
    )

    assert len(rows) == success_count


def test_traceability_complete_false_if_required_fields_missing(tmp_path: Path) -> None:
    experiment_root, config_root, _ = _fixture(tmp_path)
    (experiment_root / "runs" / LEAF_ID / "inputs_manifest.yaml").unlink()

    rows, _ = build_traceability_matrix(experiment_root=experiment_root, config_root=config_root)

    assert rows[0]["traceability_complete"] == "false"


def test_no_duplicate_scenario_leaf_rows(tmp_path: Path) -> None:
    experiment_root, config_root, _ = _fixture(tmp_path)

    rows, _ = build_traceability_matrix(experiment_root=experiment_root, config_root=config_root)
    leaf_ids = [row["scenario_leaf_id"] for row in rows]

    assert len(leaf_ids) == len(set(leaf_ids))


def test_config_and_input_manifest_hash_fields_exist(tmp_path: Path) -> None:
    experiment_root, config_root, _ = _fixture(tmp_path)

    rows, _ = build_traceability_matrix(experiment_root=experiment_root, config_root=config_root)

    assert rows[0]["config_hash_sha256"]
    assert rows[0]["inputs_manifest_hash_sha256"]
