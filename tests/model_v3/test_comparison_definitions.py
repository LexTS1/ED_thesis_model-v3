from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from model_v3.scenarios.generate_comparisons import (
    ComparisonGenerationError,
    _validate_metric_columns,
    expand_metric_set,
    load_comparison_definitions,
)
from model_v3.scenarios.summary_contract import REQUIRED_METRIC_COLUMNS
from model_v3.scenarios.validate_comparisons import _validate_definition_references

from tests.model_v3.comparison_test_utils import DEFINITIONS_PATH


def test_comparison_definitions_parse_and_metrics_exist() -> None:
    definitions = load_comparison_definitions(DEFINITIONS_PATH)
    metrics = expand_metric_set(definitions, "all_major_metrics")
    frame = pd.DataFrame(columns=REQUIRED_METRIC_COLUMNS)

    _validate_metric_columns(frame, metrics)

    assert definitions["schema_version"] == "model_v3.comparison_definitions.v1"
    assert "annual_grid_import_kWh" in metrics


def test_invalid_metric_reference_fails() -> None:
    definitions = load_comparison_definitions(DEFINITIONS_PATH)
    frame = pd.DataFrame(columns=REQUIRED_METRIC_COLUMNS)

    with pytest.raises(ComparisonGenerationError):
        _validate_metric_columns(frame, [*expand_metric_set(definitions, "all_major_metrics"), "missing_metric"])


def test_invalid_technology_and_window_references_fail() -> None:
    definitions = load_comparison_definitions(DEFINITIONS_PATH)
    frame = pd.DataFrame(columns=REQUIRED_METRIC_COLUMNS)
    bad_tech = dict(definitions)
    bad_tech["comparison_groups"] = dict(definitions["comparison_groups"])
    bad_tech["comparison_groups"]["technology_only"] = dict(definitions["comparison_groups"]["technology_only"])
    bad_tech["comparison_groups"]["technology_only"]["compared_technology_case_ids"] = ["bad_technology"]
    errors = _validate_definition_references(bad_tech, frame, Path("config/model_v3/scenario_tree"))
    assert any("bad_technology" in error for error in errors)

    bad_window = dict(definitions)
    bad_window["comparison_groups"] = dict(definitions["comparison_groups"])
    bad_window["comparison_groups"]["climate_only"] = dict(definitions["comparison_groups"]["climate_only"])
    bad_window["comparison_groups"]["climate_only"]["include_climate_windows"] = ["bad_window"]
    errors = _validate_definition_references(bad_window, frame, Path("config/model_v3/scenario_tree"))
    assert any("bad_window" in error for error in errors)
