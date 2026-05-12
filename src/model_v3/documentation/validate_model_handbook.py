"""Validate the generated model_v3 handbook artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


REQUIRED_CHAPTERS = [
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
    "Terminology",
]

UNSUPPORTED_COMPLETION_PATTERNS = [
    re.compile(r"\ball scenario leaves (have been|were|are) run\b", re.IGNORECASE),
    re.compile(r"\ball leaves (have been|were|are) run\b", re.IGNORECASE),
    re.compile(r"\ball runs (completed|are complete|have completed)\b", re.IGNORECASE),
    re.compile(r"\bcomplete scenario tree (has been|was) simulated\b", re.IGNORECASE),
]


def load_yaml(path: Path) -> Mapping[str, Any]:
    if yaml is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, Mapping) else {}


def resolve_repo_root(source: Path, manifest: Mapping[str, Any]) -> Path:
    outputs = manifest.get("outputs", {}) if isinstance(manifest.get("outputs", {}), Mapping) else {}
    md_rel = outputs.get("handbook_markdown")
    if isinstance(md_rel, str) and md_rel:
        parts = Path(md_rel).parts
        if parts:
            candidate = source.resolve()
            for _ in parts:
                candidate = candidate.parent
            return candidate
    return source.resolve().parents[1] if len(source.resolve().parents) > 1 else source.resolve().parent


def headings(markdown: str) -> set[str]:
    found: set[str] = set()
    for line in markdown.splitlines():
        if line.startswith("#"):
            found.add(line.lstrip("#").strip())
    return found


def no_placeholder_text(markdown: str) -> list[str]:
    errors: list[str] = []
    in_known_missing = False
    for idx, line in enumerate(markdown.splitlines(), start=1):
        if line.startswith("#"):
            in_known_missing = "Known missing items" in line
        if in_known_missing:
            continue
        if re.search(r"\blorem ipsum\b", line, re.IGNORECASE):
            errors.append(f"line {idx}: placeholder lorem ipsum")
        if re.search(r"\bTODO\b|\bTBD\b", line):
            errors.append(f"line {idx}: placeholder TODO/TBD")
    return errors


def included_figures(markdown: str) -> list[tuple[str, str]]:
    figures = []
    for line in markdown.splitlines():
        match = re.match(r"!\[(.*?)\]\((.*?)\)", line.strip())
        if match:
            figures.append((match.group(1), match.group(2)))
    return figures


def figure_caption_errors(markdown: str, repo_root: Path) -> list[str]:
    errors: list[str] = []
    lines = markdown.splitlines()
    for idx, line in enumerate(lines):
        match = re.match(r"!\[(.*?)\]\((.*?)\)", line.strip())
        if not match:
            continue
        alt, path_text = match.groups()
        if not alt.strip():
            errors.append(f"figure at line {idx + 1} has no alt/caption text")
        if not (repo_root / path_text).exists():
            errors.append(f"figure path missing: {path_text}")
        following = "\n".join(lines[idx + 1 : idx + 5])
        if "Caption:" not in following:
            errors.append(f"figure at line {idx + 1} has no nearby Caption line")
    return errors


def likely_local_path(token: str) -> bool:
    if "/" not in token:
        return False
    if any(marker in token for marker in ["{", "}", "*", "$", "--", "\n"]):
        return False
    if token.startswith(("http://", "https://", "python3 ", "Figure:", "Caption:")):
        return False
    if token.startswith(("config/", "inputs/", "src/", "model_v3/", "figures/", "reports/", "docs/", "tests/")):
        return True
    return False


def path_tokens(token: str) -> list[str]:
    """Split a Markdown code token that may list multiple source paths."""

    parts = []
    for semi_part in token.split(";"):
        for and_part in re.split(r"\s+and\s+", semi_part):
            cleaned = and_part.strip().strip(".:,")
            if cleaned:
                parts.append(cleaned)
    return parts


def source_path_errors(markdown: str, repo_root: Path) -> list[str]:
    errors: list[str] = []
    for idx, line in enumerate(markdown.splitlines(), start=1):
        for token in re.findall(r"`([^`]+)`", line):
            for candidate in path_tokens(token):
                if not likely_local_path(candidate):
                    continue
                if (repo_root / candidate).exists():
                    continue
                lower_line = line.lower()
                if any(status in lower_line for status in ["missing", "not found", "if present", "expected"]):
                    continue
                errors.append(f"line {idx}: referenced path does not exist or is not marked missing: {candidate}")
    return errors


def unsupported_claim_errors(markdown: str, manifest: Mapping[str, Any]) -> list[str]:
    registry = manifest.get("run_registry", {}) if isinstance(manifest.get("run_registry", {}), Mapping) else {}
    all_completed = bool(registry.get("all_leaves_completed", False))
    if all_completed:
        return []
    errors = []
    for pattern in UNSUPPORTED_COMPLETION_PATTERNS:
        match = pattern.search(markdown)
        if match:
            errors.append(f"unsupported completion claim detected: {match.group(0)}")
    return errors


def pdf_visual_errors(handbook: Path) -> list[str]:
    """Use macOS sips when available to catch visibly blank first pages."""

    if not handbook.exists() or shutil.which("sips") is None:
        return []
    try:
        from PIL import Image, ImageStat  # type: ignore
    except Exception:
        return []
    with tempfile.TemporaryDirectory(prefix="model_v3_handbook_pdf_") as tmp:
        png = Path(tmp) / "first_page.png"
        result = subprocess.run(
            ["sips", "-s", "format", "png", str(handbook), "--out", str(png)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not png.exists():
            return [f"PDF first-page rasterization failed: {result.stderr.strip() or result.stdout.strip()}"]
        image = Image.open(png).convert("L")
        histogram = image.histogram()
        nonwhite_pixels = sum(histogram[:250])
        mean = ImageStat.Stat(image).mean[0]
        if nonwhite_pixels < 1000 or mean > 254.5:
            return [f"PDF first page appears blank: nonwhite_pixels={nonwhite_pixels}, mean_luminance={mean:.2f}"]
    return []


def validate(handbook: Path, source: Path, manifest_path: Path, print_summary: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    if not handbook.exists():
        errors.append(f"PDF does not exist: {handbook}")
    else:
        errors.extend(pdf_visual_errors(handbook))
    if not source.exists():
        errors.append(f"Markdown source does not exist: {source}")
        markdown = ""
    else:
        markdown = source.read_text(encoding="utf-8")
    if not manifest_path.exists():
        errors.append(f"Manifest does not exist: {manifest_path}")
        manifest: Mapping[str, Any] = {}
    else:
        manifest = load_yaml(manifest_path)
    repo_root = resolve_repo_root(source, manifest)

    found_headings = headings(markdown)
    for chapter in REQUIRED_CHAPTERS:
        if chapter not in found_headings:
            errors.append(f"Missing major chapter: {chapter}")
    if "2050" not in markdown or "mid-century" not in markdown:
        errors.append("2050 policy is not explicitly documented")
    if "Caveats, gaps, and limitations" not in markdown:
        errors.append("Caveats chapter is missing")
    if "Terminology" not in found_headings:
        errors.append("Terminology chapter is missing")

    briefing = source.parent / "model_v3_supervisor_briefing.md"
    if not briefing.exists():
        errors.append(f"Supervisor briefing missing: {briefing}")

    figures = included_figures(markdown)
    if not figures:
        errors.append("No included figures found")
    errors.extend(figure_caption_errors(markdown, repo_root))
    errors.extend(no_placeholder_text(markdown))
    errors.extend(source_path_errors(markdown, repo_root))
    errors.extend(unsupported_claim_errors(markdown, manifest))

    result = {
        "passed": not errors,
        "errors": errors,
        "figures_checked": len(figures),
        "terminology_chapter": "present" if "Terminology" in found_headings else "missing",
        "caveats_chapter": "present" if "Caveats, gaps, and limitations" in markdown else "missing",
        "policy_2050_documented": "yes" if "2050" in markdown and "mid-century" in markdown else "no",
        "unsupported_claims_detected": len(unsupported_claim_errors(markdown, manifest)),
    }
    if print_summary:
        if result["passed"]:
            print("Model handbook validation passed.")
        else:
            print("Model handbook validation failed.")
        print(f"PDF exists: {handbook.exists()}")
        print(f"Markdown source exists: {source.exists()}")
        print(f"Manifest exists: {manifest_path.exists()}")
        print(f"Figures checked: {result['figures_checked']}")
        print(f"Terminology chapter: {result['terminology_chapter']}")
        print(f"Caveats chapter: {result['caveats_chapter']}")
        print(f"2050 policy documented: {result['policy_2050_documented']}")
        print(f"Unsupported claims detected: {result['unsupported_claims_detected']}")
        if errors:
            print("Errors:")
            for error in errors:
                print(f"- {error}")
    if errors:
        raise SystemExit(1)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the generated model_v3 handbook.")
    parser.add_argument("--handbook", required=True, help="Handbook PDF path.")
    parser.add_argument("--source", required=True, help="Handbook Markdown source path.")
    parser.add_argument("--manifest", required=True, help="Handbook manifest YAML path.")
    parser.add_argument("--print-summary", action="store_true", help="Print validation summary.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    validate(
        handbook=Path(args.handbook),
        source=Path(args.source),
        manifest_path=Path(args.manifest),
        print_summary=bool(args.print_summary),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
