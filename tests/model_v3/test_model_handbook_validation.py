from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from model_v3.documentation.validate_model_handbook import validate


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _minimal_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
            "0000000c49444154789c63606060000000040001f61738550000000049454e44ae426082"
        )
    )


def _manifest(path: Path, all_completed: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "outputs": {"handbook_markdown": "docs/model_v3_complete_model_handbook.md"},
                "run_registry": {
                    "enumerated_scenario_leaves": 2,
                    "successful_scenario_leaves": 2 if all_completed else 1,
                    "all_leaves_completed": all_completed,
                },
            }
        ),
        encoding="utf-8",
    )


def _valid_source(root: Path, include_terminology: bool = True, extra: str = "") -> Path:
    _minimal_png(root / "docs/model_v3_handbook_assets/model_architecture.png")
    _write(root / "docs/model_v3_supervisor_briefing.md", "# briefing\n")
    chapters = [
        "Executive summary",
        "Chapter 1 - Purpose and thesis context",
        "Chapter 2 - High-level model architecture",
        "Chapter 3 - Model physics and simulation logic",
        "Chapter 4 - Inputs and data sources",
        "Chapter 5 - Scenario-tree design",
        "Chapter 6 - Directory structure and experiment space",
        "Chapter 7 - Configuration generation",
        "Chapter 8 - Running the model",
        "Chapter 9 - Outputs and standardized metrics",
        "Chapter 10 - Comparison framework",
        "Chapter 11 - Validation and quality assurance",
        "Chapter 12 - Figures and interpretation guide",
        "Chapter 13 - Caveats, gaps, and limitations",
        "Chapter 14 - Recommended next improvements",
        "Chapter 15 - How to use the model",
        "Chapter 16 - Supervisor presentation guide",
    ]
    if include_terminology:
        chapters.append("Terminology")
    body = "\n\n".join(f"# {chapter}\n2050 mid-century policy is documented." for chapter in chapters)
    body += "\n\n![Architecture](docs/model_v3_handbook_assets/model_architecture.png)\nCaption: Architecture\n"
    body += "\n" + extra
    source = root / "docs/model_v3_complete_model_handbook.md"
    _write(source, body)
    return source


def test_validator_rejects_missing_pdf(tmp_path: Path) -> None:
    source = _valid_source(tmp_path)
    manifest = tmp_path / "docs/model_v3_complete_model_handbook_manifest.yaml"
    _manifest(manifest)

    with pytest.raises(SystemExit):
        validate(
            handbook=tmp_path / "docs/model_v3_complete_model_handbook.pdf",
            source=source,
            manifest_path=manifest,
        )


def test_validator_rejects_missing_terminology_chapter(tmp_path: Path) -> None:
    source = _valid_source(tmp_path, include_terminology=False)
    pdf = tmp_path / "docs/model_v3_complete_model_handbook.pdf"
    _write(pdf, "%PDF-1.4\n")
    manifest = tmp_path / "docs/model_v3_complete_model_handbook_manifest.yaml"
    _manifest(manifest)

    with pytest.raises(SystemExit):
        validate(handbook=pdf, source=source, manifest_path=manifest)


def test_validator_rejects_unsupported_all_runs_claim(tmp_path: Path) -> None:
    source = _valid_source(tmp_path, extra="All scenario leaves have been run.")
    pdf = tmp_path / "docs/model_v3_complete_model_handbook.pdf"
    _write(pdf, "%PDF-1.4\n")
    manifest = tmp_path / "docs/model_v3_complete_model_handbook_manifest.yaml"
    _manifest(manifest, all_completed=False)

    with pytest.raises(SystemExit):
        validate(handbook=pdf, source=source, manifest_path=manifest)

