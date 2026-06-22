from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_methodology_document_exists() -> None:
    assert (REPO_ROOT / "docs/model_v3_scenario_tree_methodology.md").exists()


def test_assumptions_document_exists() -> None:
    assert (REPO_ROOT / "docs/model_v3_scenario_tree_assumptions.md").exists()


def test_documentation_index_references_canonical_methodology() -> None:
    text = _read("docs/model_v3_scenario_tree_documentation_index.md")

    assert "docs/model_v3_scenario_tree_methodology.md" in text
    assert "docs/thesis_methodology_scenario_tree_subsection.md" not in text


def test_2050_overlap_policy_is_explicitly_mentioned() -> None:
    text = _read("docs/model_v3_scenario_tree_methodology.md")

    assert "near-future canonical window ends on 2049-12-31" in text
    assert "mid-century canonical window starts on 2050-01-01" in text
    assert "2050 is assigned only to the mid-century" in text


def test_canonical_scenario_leaf_id_format_is_documented() -> None:
    text = _read("docs/model_v3_scenario_tree_methodology.md")

    assert "{climate_window_id}__{climate_pathway_id}__{technology_case_id}__{realization_id}" in text


def test_baseline_special_case_is_documented() -> None:
    text = _read("docs/model_v3_scenario_tree_methodology.md")

    assert "baseline_1981_2005__historical__tech_current_stock" in text
    assert "Baseline special case" in text


def test_four_traceability_questions_are_documented() -> None:
    text = _read("docs/model_v3_scenario_tree_methodology.md")

    assert "Which climate forcing was used?" in text
    assert "Which technology assumptions were active?" in text
    assert "Which stochastic seed/cohort generated this result?" in text
    assert "Which exact model/config produced the output?" in text
