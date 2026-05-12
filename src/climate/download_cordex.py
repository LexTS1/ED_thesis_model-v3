"""Download CORDEX climate chunks from the Copernicus CDS API.

The script intentionally does not configure credentials. The CDS API client reads
standard credentials from ~/.cdsapirc.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).with_name("climate_config.yaml")

LOGGER = logging.getLogger("climate.download_cordex")


@dataclass(frozen=True)
class DownloadTask:
    """One deterministic CDS request and local target path."""

    dataset: str
    request: dict[str, Any]
    target_path: Path
    window: str
    scenario: str
    start_year: int
    end_year: int


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


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"Climate config is empty or invalid: {path}")
    return config


def deterministic_filename(
    window: str,
    scenario: str,
    model_chain: dict[str, str],
    start_year: int,
    end_year: int,
) -> str:
    return (
        f"cordex_{window}_{scenario}_{model_chain['gcm_model']}_"
        f"{model_chain['rcm_model']}_{model_chain['ensemble_member']}_"
        f"{start_year}_{end_year}.zip"
    )


def build_cds_request(
    config: dict[str, Any],
    scenario: str,
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    model_chain = config["model_chain"]
    request: dict[str, Any] = {
        "domain": config["domain"],
        "experiment": scenario,
        "horizontal_resolution": config["horizontal_resolution"],
        "temporal_resolution": config.get("temporal_resolution", "daily_mean"),
        "variable": config["variables"],
        "gcm_model": model_chain["gcm_model"],
        "rcm_model": model_chain["rcm_model"],
        "ensemble_member": model_chain["ensemble_member"],
        "start_year": [str(start_year)],
        "end_year": [str(end_year)],
    }
    request.update(config.get("request_options", {}))
    return request


def iter_download_tasks(
    config: dict[str, Any],
    window_filter: str | None = None,
    scenario_filter: str | None = None,
) -> Iterable[DownloadTask]:
    raw_root = repo_path(config["paths"]["raw_root"])
    dataset = config["dataset"]
    model_chain = config["model_chain"]
    scenarios = config["scenarios"]
    windows = config["climate_windows"]

    for scenario, scenario_config in scenarios.items():
        if scenario_filter and scenario != scenario_filter:
            continue

        for window in scenario_config.get("windows", []):
            if window_filter and window != window_filter:
                continue
            if window not in windows:
                raise KeyError(f"Scenario {scenario!r} references unknown window {window!r}")

            for chunk in windows[window]["chunks"]:
                start_year = int(chunk["start_year"])
                end_year = int(chunk["end_year"])
                request = build_cds_request(config, scenario, start_year, end_year)
                target = (
                    raw_root
                    / window
                    / scenario
                    / deterministic_filename(window, scenario, model_chain, start_year, end_year)
                )
                yield DownloadTask(
                    dataset=dataset,
                    request=request,
                    target_path=target,
                    window=window,
                    scenario=scenario,
                    start_year=start_year,
                    end_year=end_year,
                )


def print_dry_run_task(task: DownloadTask, overwrite: bool) -> None:
    payload = {
        "dataset": task.dataset,
        "target": str(task.target_path.relative_to(REPO_ROOT)),
        "window": task.window,
        "scenario": task.scenario,
        "chunk": {"start_year": task.start_year, "end_year": task.end_year},
        "target_exists": task.target_path.exists(),
        "would_skip_existing": task.target_path.exists() and not overwrite,
        "request": task.request,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def download_task(client: Any, task: DownloadTask, overwrite: bool) -> bool:
    if task.target_path.exists() and not overwrite:
        LOGGER.info("Skipping existing file: %s", task.target_path.relative_to(REPO_ROOT))
        return True

    task.target_path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.info(
        "Downloading %s %s %s-%s to %s",
        task.window,
        task.scenario,
        task.start_year,
        task.end_year,
        task.target_path.relative_to(REPO_ROOT),
    )
    try:
        client.retrieve(task.dataset, task.request, str(task.target_path))
    except Exception:
        LOGGER.exception(
            "CDS request failed for %s/%s %s-%s; continuing with remaining chunks",
            task.window,
            task.scenario,
            task.start_year,
            task.end_year,
        )
        return False
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download configured CORDEX climate chunks from CDS."
    )
    parser.add_argument("--window", help="Limit downloads to one climate window.")
    parser.add_argument("--scenario", help="Limit downloads to one scenario.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated CDS requests without downloading files.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download even when the deterministic target file already exists.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Path to the climate YAML config.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    try:
        config = load_config(args.config)
        tasks = list(iter_download_tasks(config, args.window, args.scenario))
    except Exception as exc:
        LOGGER.error("Failed to build download tasks: %s", exc)
        return 2

    if not tasks:
        LOGGER.error(
            "No download tasks matched the requested filters. window=%r scenario=%r",
            args.window,
            args.scenario,
        )
        return 1

    if args.dry_run:
        for task in tasks:
            print_dry_run_task(task, args.overwrite)
        LOGGER.info("Dry run generated %s CDS request(s). No files were downloaded.", len(tasks))
        return 0

    try:
        import cdsapi  # type: ignore[import-not-found]
    except ImportError:
        LOGGER.error(
            "cdsapi is not installed. Install requirements.txt and configure ~/.cdsapirc."
        )
        return 2

    client = cdsapi.Client(progress=False)
    success_count = 0
    failure_count = 0
    for task in tasks:
        if download_task(client, task, args.overwrite):
            success_count += 1
        else:
            failure_count += 1

    LOGGER.info("Download run finished: %s succeeded, %s failed.", success_count, failure_count)
    return 1 if failure_count else 0


if __name__ == "__main__":
    sys.exit(main())
