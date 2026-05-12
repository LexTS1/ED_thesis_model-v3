"""Validate processed climate forcing CSV files."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).with_name("climate_config.yaml")
REPORT_PATH = REPO_ROOT / "reports" / "climate_input_validation.md"

LOGGER = logging.getLogger("climate.validate_climate_inputs")

REQUIRED_COLUMNS = [
    "timestamp",
    "T_out_C",
    "I_solar_W_m2",
    "scenario",
    "window",
    "gcm_model",
    "rcm_model",
    "ensemble_member",
    "source_files",
]


@dataclass
class FileValidationResult:
    path: Path
    row_count: int = 0
    coverage: str = "n/a"
    temperature_stats: dict[str, float] = field(default_factory=dict)
    radiation_stats: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def repo_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"Climate config is empty or invalid: {path}")
    return config


def load_metadata(csv_path: Path) -> dict[str, Any]:
    metadata_path = csv_path.with_suffix(".metadata.json")
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"metadata_error": f"Could not parse {relative_path(metadata_path)}"}


def processed_csv_files(processed_root: Path, window_filter: str | None = None) -> list[Path]:
    files = sorted(processed_root.glob("*/*.csv"))
    if window_filter:
        files = [path for path in files if path.parent.name == window_filter]
    return files


def missing_value_count(series: pd.Series) -> int:
    missing = series.isna()
    if series.dtype == object:
        missing = missing | series.astype(str).str.strip().eq("")
    return int(missing.sum())


def numeric_stats(series: pd.Series) -> dict[str, float]:
    numeric = pd.to_numeric(series, errors="coerce")
    return {
        "min": float(numeric.min()),
        "max": float(numeric.max()),
        "mean": float(numeric.mean()),
    }


def single_column_value(
    frame: pd.DataFrame,
    column: str,
    result: FileValidationResult,
) -> str | None:
    if column not in frame.columns:
        return None
    values = sorted(str(value) for value in frame[column].dropna().unique())
    if len(values) == 1:
        return values[0]
    if not values:
        result.errors.append(f"Column `{column}` has no values.")
        return None
    result.errors.append(f"Column `{column}` has multiple values: {', '.join(values)}.")
    return values[0]


def expected_daily_dates(
    start: pd.Timestamp,
    end: pd.Timestamp,
    calendar: str,
) -> pd.DatetimeIndex | None:
    normalized_calendar = calendar.lower().strip()
    if normalized_calendar in {"360_day", "360"}:
        return None
    dates = pd.date_range(start, end, freq="D")
    if normalized_calendar in {"noleap", "no_leap", "365_day", "365"}:
        dates = dates[~((dates.month == 2) & (dates.day == 29))]
    return dates


def validate_timestamps(
    frame: pd.DataFrame,
    config: dict[str, Any],
    metadata: dict[str, Any],
    result: FileValidationResult,
) -> None:
    if "timestamp" not in frame.columns:
        return

    duplicate_count = int(frame["timestamp"].duplicated().sum())
    if duplicate_count:
        result.errors.append(f"Found {duplicate_count} duplicated timestamp value(s).")

    parsed = pd.to_datetime(frame["timestamp"], errors="coerce")
    invalid_count = int(parsed.isna().sum())
    if invalid_count:
        result.errors.append(f"Found {invalid_count} timestamp value(s) that pandas cannot parse.")
        result.coverage = f"{frame['timestamp'].iloc[0]} to {frame['timestamp'].iloc[-1]}"
        return

    dates = parsed.dt.normalize()
    result.coverage = f"{dates.min().date()} to {dates.max().date()}"

    duplicate_dates = int(dates.duplicated().sum())
    if duplicate_dates:
        result.errors.append(f"Found {duplicate_dates} duplicated calendar date(s).")

    window = single_column_value(frame, "window", result)
    if not window:
        return
    if window not in config["climate_windows"]:
        result.errors.append(f"Unknown climate window in CSV: {window!r}.")
        return

    window_config = config["climate_windows"][window]
    start = pd.Timestamp(str(window_config["start"]))
    end = pd.Timestamp(str(window_config["end"]))
    start_year = int(window_config["start_year"])
    end_year = int(window_config["end_year"])

    min_year = int(dates.dt.year.min())
    max_year = int(dates.dt.year.max())
    if min_year != start_year or max_year != end_year:
        result.errors.append(
            f"Expected year coverage {start_year}-{end_year}, found {min_year}-{max_year}."
        )

    outside_window = int(((dates < start) | (dates > end)).sum())
    if outside_window:
        result.errors.append(f"Found {outside_window} timestamp(s) outside the configured window.")

    calendar = str(metadata.get("time_calendar", ""))
    expected = expected_daily_dates(start, end, calendar)
    if expected is None:
        result.warnings.append(
            "Skipping Gregorian missing-date check because metadata reports a 360_day calendar."
        )
        return

    actual = pd.DatetimeIndex(dates.drop_duplicates().sort_values())
    missing = expected.difference(actual)
    extra = actual.difference(expected)
    if len(missing):
        preview = ", ".join(date.strftime("%Y-%m-%d") for date in missing[:5])
        result.errors.append(f"Missing {len(missing)} expected daily timestamp(s): {preview}.")
    if len(extra):
        preview = ", ".join(date.strftime("%Y-%m-%d") for date in extra[:5])
        result.errors.append(f"Found {len(extra)} unexpected daily timestamp(s): {preview}.")


def validate_required_columns(frame: pd.DataFrame, result: FileValidationResult) -> None:
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing_columns:
        result.errors.append(f"Missing required column(s): {', '.join(missing_columns)}.")
        return

    for column in REQUIRED_COLUMNS:
        count = missing_value_count(frame[column])
        if count:
            result.errors.append(f"Column `{column}` contains {count} missing value(s).")


def validate_numeric_ranges(frame: pd.DataFrame, result: FileValidationResult) -> None:
    if "T_out_C" in frame.columns:
        temperature = pd.to_numeric(frame["T_out_C"], errors="coerce")
        invalid = int(temperature.isna().sum())
        if invalid:
            result.errors.append(f"Column `T_out_C` contains {invalid} non-numeric value(s).")
        else:
            result.temperature_stats = numeric_stats(temperature)
            if temperature.min() < -35.0 or temperature.max() > 50.0:
                result.warnings.append(
                    "Temperature range is outside the Belgium plausibility bounds "
                    "[-35, 50] degC."
                )

    if "I_solar_W_m2" in frame.columns:
        radiation = pd.to_numeric(frame["I_solar_W_m2"], errors="coerce")
        invalid = int(radiation.isna().sum())
        if invalid:
            result.errors.append(f"Column `I_solar_W_m2` contains {invalid} non-numeric value(s).")
        else:
            result.radiation_stats = numeric_stats(radiation)
            negative = int((radiation < 0.0).sum())
            if negative:
                result.errors.append(f"Column `I_solar_W_m2` contains {negative} negative value(s).")


def validate_file(path: Path, config: dict[str, Any]) -> FileValidationResult:
    result = FileValidationResult(path=path)
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        result.errors.append(f"Could not read CSV: {exc}")
        return result

    result.row_count = int(len(frame))
    metadata = load_metadata(path)
    if metadata.get("metadata_error"):
        result.warnings.append(str(metadata["metadata_error"]))

    if frame.empty:
        result.errors.append("CSV is empty.")
        return result

    validate_required_columns(frame, result)
    validate_timestamps(frame, config, metadata, result)
    validate_numeric_ranges(frame, result)
    return result


def format_stat(stats: dict[str, float]) -> str:
    if not stats:
        return "n/a"
    return f"min {stats['min']:.3f}, max {stats['max']:.3f}, mean {stats['mean']:.3f}"


def render_report(
    results: list[FileValidationResult],
    processed_root: Path,
    report_path: Path,
) -> str:
    error_count = sum(len(result.errors) for result in results)
    if not results:
        error_count = 1
    warning_count = sum(len(result.warnings) for result in results)
    lines = [
        "# Climate Input Validation Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Processed root: `{relative_path(processed_root)}`",
        f"Files checked: {len(results)}",
        f"Errors: {error_count}",
        f"Warnings: {warning_count}",
        "",
    ]

    if not results:
        lines.extend(
            [
                "No processed climate CSV files were found.",
                "",
                "Run preprocessing after downloading raw CORDEX chunks, for example:",
                "",
                "```bash",
                "python -m src.climate.preprocess_cordex --window baseline --scenario historical",
                "```",
                "",
            ]
        )
    else:
        for result in results:
            lines.extend(
                [
                    f"## {relative_path(result.path)}",
                    "",
                    f"- Coverage: {result.coverage}",
                    f"- Number of rows: {result.row_count}",
                    f"- Temperature `T_out_C`: {format_stat(result.temperature_stats)}",
                    f"- Radiation `I_solar_W_m2`: {format_stat(result.radiation_stats)}",
                    "",
                    "### Errors",
                ]
            )
            if result.errors:
                lines.extend(f"- {error}" for error in result.errors)
            else:
                lines.append("- None")

            lines.extend(["", "### Warnings"])
            if result.warnings:
                lines.extend(f"- {warning}" for warning in result.warnings)
            else:
                lines.append("- None")
            lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate processed climate forcing CSV files.")
    parser.add_argument("--window", help="Limit validation to one processed climate window.")
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Path to the climate YAML config.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPORT_PATH,
        help="Markdown validation report path.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    try:
        config = load_config(args.config)
    except Exception as exc:
        LOGGER.error("Failed to load validation config: %s", exc)
        return 2

    processed_root = repo_path(config["paths"]["processed_root"])
    files = processed_csv_files(processed_root, args.window)
    results = [validate_file(path, config) for path in files]
    render_report(results, processed_root, args.report)

    if not results:
        LOGGER.error(
            "No processed CSV files found under %s. Report written to %s.",
            relative_path(processed_root),
            relative_path(args.report),
        )
        return 1

    error_count = sum(len(result.errors) for result in results)
    warning_count = sum(len(result.warnings) for result in results)
    LOGGER.info(
        "Validation finished for %s file(s): %s error(s), %s warning(s). Report: %s",
        len(results),
        error_count,
        warning_count,
        relative_path(args.report),
    )
    return 1 if error_count else 0


if __name__ == "__main__":
    sys.exit(main())
