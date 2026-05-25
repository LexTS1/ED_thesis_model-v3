"""Generate a lightweight bounded DHW calibration report.

This runner exercises the stochastic DHW event generator directly. It does not
execute scenario leaves or a full cohort experiment.
"""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[5]))

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
import yaml

from model_v3.data.loaders import load_occupancy_spec
from model_v3.stochastic.dhw_generator import generate_dhw_events
from model_v3.stochastic.household_classifier import resolve_household_class


DEFAULT_CONFIG = "config/model.yaml"
DEFAULT_REPORT_DIR = "reports/model_v3/validation/demand/dhw"
SOURCE_LINKS = {
    "VMM household water use": "https://vmm.vlaanderen.be/feiten-cijfers/water/drinkwater/indicator-waterverbruik-huishoudens",
    "Fuentes et al. DHW review": "https://www.sciencedirect.com/science/article/pii/S1364032117308614",
    "European DHW field-study context": "https://www.mdpi.com/1996-1073/14/11/3314",
}


def _repo_root_from_args(path: str | Path | None) -> Path:
    return Path(path or ".").expanduser().resolve()


def _resolve_path(repo_root: Path, value: str | Path | None) -> Path:
    path = Path(str(value or "")).expanduser()
    return path if path.is_absolute() else repo_root / path


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, Mapping):
        raise ValueError(f"Expected a YAML mapping in `{path}`.")
    return dict(loaded)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")


def _write_markdown(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _energy_kwh(profile_w: Iterable[float], timestep_hours: float = 1.0) -> float:
    return float(np.clip(np.asarray(list(profile_w), dtype=float), 0.0, None).sum() * timestep_hours / 1000.0)


def _mean_daily_profile(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.groupby(frame["timestamp"].dt.strftime("%H:%M"))["Q_dhw_demand_W"].mean()
    return grouped.rename_axis("time_of_day").reset_index(name="mean_dhw_W")


def _fallback_occupancy_spec() -> dict[str, Any]:
    return {
        "dt_minutes": 60,
        "states": ["away", "awake", "sleep"],
        "fallback_weights": {
            "weekday": {"away": 0.05, "awake": 0.75, "sleep": 0.20},
            "weekend": {"away": 0.05, "awake": 0.75, "sleep": 0.20},
        },
        "rules": {
            "weekday": [
                {"state": "sleep", "start": "23:00", "end": "07:00", "p": 0.85},
                {"state": "awake", "start": "07:00", "end": "23:00", "p": 0.85},
            ],
            "weekend": [
                {"state": "sleep", "start": "23:00", "end": "08:00", "p": 0.85},
                {"state": "awake", "start": "08:00", "end": "23:00", "p": 0.85},
            ],
        },
    }


def _interval_mean(profile: pd.DataFrame, start_hour: int, end_hour: int) -> float:
    hours = profile["time_of_day"].str.slice(0, 2).astype(int)
    if start_hour < end_hour:
        mask = (hours >= start_hour) & (hours < end_hour)
    else:
        mask = (hours >= start_hour) | (hours < end_hour)
    return float(profile.loc[mask, "mean_dhw_W"].mean()) if bool(mask.any()) else 0.0


def run_dhw_calibration(
    repo_root: Path,
    config: Mapping[str, Any],
    *,
    report_dir: Path,
    reference_year: int = 2024,
) -> dict[str, Any]:
    behaviour_cfg = dict(dict(config.get("uncertainty", {})).get("behaviour", {}))
    calibration_cfg = dict(behaviour_cfg.get("dhw_calibration", {}))
    occupancy_spec = load_occupancy_spec(config=config) or _fallback_occupancy_spec()
    timestamps = pd.date_range(
        f"{int(reference_year)}-01-01T00:00:00+01:00",
        periods=8760,
        freq="h",
    )
    representative_runs = [
        {"household_id": "one_person", "occupants": 1.0, "seed": 101, "class": "low_flat", "intensity": 1.0},
        {"household_id": "two_person", "occupants": 2.0, "seed": 202, "class": "workday_absent", "intensity": 1.0},
        {"household_id": "family", "occupants": 4.0, "seed": 303, "class": "peak_heavy_family", "intensity": 1.0},
    ]

    rows: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    profile_frames: list[pd.DataFrame] = []
    for run in representative_runs:
        generated = generate_dhw_events(
            timestamps=tuple(timestamps),
            target_resolution_seconds=3600,
            occupancy_spec=occupancy_spec,
            occupants_per_dwelling=float(run["occupants"]),
            occupancy_threshold=float(dict(config.get("model", {})).get("occupancy_threshold", 0.5)),
            schedule_variation_seed=int(run["seed"]),
            occupancy_time_shift_hours=0.0,
            transition_variability_scale=1.0,
            state_duration_scale=1.0,
            occupancy_state_biases={},
            household_class=resolve_household_class(str(run["class"])),
            household_random_effect_u=0.0,
            rng=np.random.default_rng(int(run["seed"])),
            event_frequency_scale=1.0,
            event_intensity_scale=float(run["intensity"]),
            dhw_calibration=calibration_cfg,
        )
        profile_w = np.asarray(generated["output_load_W"], dtype=float)
        annual_kwh = _energy_kwh(profile_w)
        occupants = float(run["occupants"])
        rows.append(
            {
                "household_id": run["household_id"],
                "occupants": occupants,
                "annual_useful_dhw_kWh": annual_kwh,
                "annual_useful_dhw_kWh_per_person": annual_kwh / occupants if occupants else 0.0,
                "event_count": int(dict(generated["event_summary"]).get("total_event_count", 0)),
                "peak_dhw_W": float(np.max(profile_w)) if len(profile_w) else 0.0,
            }
        )
        for event in generated["event_log"]:
            logs.append({"household_id": run["household_id"], **dict(event)})
        profile_frames.append(
            pd.DataFrame(
                {
                    "timestamp": timestamps,
                    "household_id": run["household_id"],
                    "Q_dhw_demand_W": profile_w,
                }
            )
        )

    summary = pd.DataFrame(rows)
    events = pd.DataFrame(logs)
    profiles = pd.concat(profile_frames, ignore_index=True)
    mean_daily = _mean_daily_profile(profiles)

    report_dir.mkdir(parents=True, exist_ok=True)
    summary_path = report_dir / "dhw_bounded_calibration_summary.csv"
    events_path = report_dir / "dhw_bounded_calibration_events.csv"
    profile_path = report_dir / "dhw_bounded_calibration_mean_daily.csv"
    metrics_path = report_dir / "dhw_bounded_calibration_metrics.json"
    report_path = report_dir / "dhw_bounded_calibration_report.md"
    summary.to_csv(summary_path, index=False)
    events.to_csv(events_path, index=False)
    mean_daily.to_csv(profile_path, index=False)

    metrics = {
        "validation_scope": "bounded_dhw_useful_draw_off_calibration",
        "reference_year": int(reference_year),
        "calibration_enabled": bool(calibration_cfg.get("enabled", False)),
        "daily_useful_kWh_per_person_target": calibration_cfg.get("daily_useful_kWh_per_person", {}).get("base"),
        "annual_useful_dhw_kWh_per_person_mean": float(summary["annual_useful_dhw_kWh_per_person"].mean()),
        "annual_useful_dhw_kWh_per_household_mean": float(summary["annual_useful_dhw_kWh"].mean()),
        "morning_mean_W": _interval_mean(mean_daily, 6, 9),
        "evening_mean_W": _interval_mean(mean_daily, 18, 22),
        "night_mean_W": _interval_mean(mean_daily, 0, 5),
        "event_count_by_type": events["event_type"].value_counts().to_dict() if not events.empty else {},
        "mean_event_volume_liters_by_type": events.groupby("event_type")["volume_liters"].mean().dropna().to_dict()
        if "volume_liters" in events.columns and not events.empty
        else {},
        "source_links": SOURCE_LINKS,
        "outputs": {
            "summary_csv": str(summary_path.relative_to(repo_root)),
            "events_csv": str(events_path.relative_to(repo_root)),
            "mean_daily_csv": str(profile_path.relative_to(repo_root)),
            "metrics_json": str(metrics_path.relative_to(repo_root)),
            "report_md": str(report_path.relative_to(repo_root)),
        },
    }
    _write_json(metrics_path, metrics)

    lines = [
        "# Model v3 DHW Bounded Calibration",
        "",
        "- classification: bounded useful draw-off calibration, not full validation",
        "- model object being tested: `model_v3.stochastic.dhw_generator.generate_dhw_events`",
        "- runtime boundary: `Q_dhw_demand_W` useful DHW heat; production/storage/distribution losses are outside this demand layer",
        "- scenario leaves and full cohort experiments were not run",
        "",
        "## Calibration targets",
        "",
        f"- useful DHW target: `{metrics['daily_useful_kWh_per_person_target']}` kWh/person/day",
        "- equivalent draw volume: `32` L/person/day at 42 degC with 10 degC cold-water inlet",
        "- event timing: morning and evening draw windows carry most event probability",
        "- event mix: sink, shower, dishwashing, and bath events are represented as volume-derived useful heat draws",
        "",
        "## Lightweight checks",
        "",
        f"- mean annual useful DHW: `{metrics['annual_useful_dhw_kWh_per_household_mean']:.1f}` kWh/household/year across representative runs",
        f"- mean annual useful DHW per person: `{metrics['annual_useful_dhw_kWh_per_person_mean']:.1f}` kWh/person/year",
        f"- morning mean load: `{metrics['morning_mean_W']:.1f}` W",
        f"- evening mean load: `{metrics['evening_mean_W']:.1f}` W",
        f"- night mean load: `{metrics['night_mean_W']:.1f}` W",
        f"- event count by type: `{metrics['event_count_by_type']}`",
        "",
        "## Evidence anchors",
        "",
        *[f"- [{name}]({url})" for name, url in SOURCE_LINKS.items()],
        "",
        "## Interpretation",
        "",
        "The report checks that the stochastic DHW event layer has the intended annual useful-energy scale and daily timing shape. It does not validate appliance-level hot-water behaviour against Belgian metered DHW events, because that reference is not available in the model inputs.",
    ]
    _write_markdown(report_path, lines)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Run lightweight DHW bounded calibration.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--reference-year", type=int, default=2024)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    repo_root = _repo_root_from_args(args.repo_root)
    config_path = _resolve_path(repo_root, args.config)
    report_dir = _resolve_path(repo_root, args.report_dir)
    metrics = run_dhw_calibration(
        repo_root,
        _load_yaml(config_path),
        report_dir=report_dir,
        reference_year=int(args.reference_year),
    )
    if args.print_summary:
        print("DHW bounded calibration complete.")
        print(f"Mean annual useful DHW per person: {metrics['annual_useful_dhw_kWh_per_person_mean']:.1f} kWh/person/year")
        print(f"Report: {metrics['outputs']['report_md']}")


if __name__ == "__main__":
    main()
