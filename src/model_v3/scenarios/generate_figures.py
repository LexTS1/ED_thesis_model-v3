"""Generate reproducible thesis figures from scenario-tree summaries."""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import pandas as pd
import yaml

from model_v3.scenarios.plot_style import (
    CLIMATE_PATHWAY_LABELS,
    CLIMATE_PATHWAY_ORDER,
    CLIMATE_WINDOW_LABELS,
    CLIMATE_WINDOW_ORDER,
    FIGURE_SIZES,
    PATHWAY_COLORS,
    TECHNOLOGY_CASE_LABELS,
    TECHNOLOGY_CASE_ORDER,
    TECHNOLOGY_COLORS,
    apply_thesis_style,
    climate_pathway_label,
    climate_window_label,
    require_columns,
    save_figure,
    scenario_label,
    stable_scenario_sort,
    technology_case_label,
)
from model_v3.scenarios.summary_contract import BASELINE_SCENARIO_ID, REQUIRED_METRIC_COLUMNS


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "scenario_tree"
DEFAULT_FIGURES_ROOT = REPO_ROOT / "figures" / "scenario_tree"
DEFAULT_COMPARISON_DEFINITIONS = REPO_ROOT / "config" / "scenario_tree" / "comparison_definitions.yaml"
SCRIPT_NAME = "model_v3.scenarios.generate_figures"

REQUIRED_DIRECTORIES = [
    "structure",
    "climate",
    "phase1_annual_demand",
    "seasonal_monthly_shift",
    "annual_demand",
    "grid_impact",
    "uncertainty",
    "infrastructure_stress",
    "metadata",
]

COMBINED_STRESS_SCENARIO_ID = "long_term_2080_2100__rcp_8_5__tech_high_electrification_pv_ev"
STOCHASTIC_BAND_COLUMNS = [
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "metric",
    "p10",
    "p50",
    "p90",
]


@dataclass(frozen=True)
class FigureSpec:
    figure_id: str
    title: str
    category: str
    filename: str
    metrics: tuple[str, ...]


FIGURE_SPECS = [
    FigureSpec("fig_scenario_tree_structure", "Scenario-tree structure", "structure", "scenario_tree_structure", ()),
    FigureSpec(
        "fig_climate_temperature_by_window_rcp",
        "Temperature by climate window and pathway",
        "climate",
        "climate_temperature_by_window_rcp",
        ("mean_T_out_C", "winter_mean_T_out_C", "summer_mean_T_out_C"),
    ),
    FigureSpec(
        "fig_climate_hdd_cdd_by_window_rcp",
        "Heating and cooling degree days by climate window and pathway",
        "climate",
        "climate_hdd_cdd_by_window_rcp",
        ("HDD_15", "HDD_18", "CDD_22"),
    ),
    FigureSpec(
        "fig_climate_solar_by_window_rcp",
        "Solar forcing by climate window and pathway",
        "climate",
        "climate_solar_by_window_rcp",
        ("mean_solar_W_m2",),
    ),
    FigureSpec(
        "fig_phase1_hdd_cdd_dot_interval",
        "Annual HDD and CDD by Phase 1 climate scenario",
        "phase1_annual_demand",
        "phase1_hdd_cdd_dot_interval",
        ("HDD_18", "CDD_22"),
    ),
    FigureSpec(
        "fig_phase1_hdd_pct_decrease_heatmap",
        "HDD percentage change versus historical baseline",
        "phase1_annual_demand",
        "phase1_hdd_pct_decrease_heatmap",
        ("delta_HDD_18_pct",),
    ),
    FigureSpec(
        "fig_phase1_cdd_abs_increase_heatmap",
        "CDD absolute change versus historical baseline",
        "phase1_annual_demand",
        "phase1_cdd_abs_increase_heatmap",
        ("delta_CDD_22_abs",),
    ),
    FigureSpec(
        "fig_output2_monthly_demand_stacked",
        "Monthly demand timing by climate scenario",
        "seasonal_monthly_shift",
        "output2_monthly_demand_stacked",
        ("monthly_space_heating_useful_kWh", "monthly_electricity_gross_kWh", "monthly_gas_kWh"),
    ),
    FigureSpec(
        "fig_output2_seasonal_heating_shift",
        "Seasonal useful heating demand shift",
        "seasonal_monthly_shift",
        "output2_seasonal_heating_shift",
        ("seasonal_space_heating_useful_kWh",),
    ),
    FigureSpec(
        "fig_output2_monthly_cooling_pressure",
        "Monthly cooling-pressure indicators",
        "seasonal_monthly_shift",
        "output2_monthly_cooling_pressure",
        ("monthly_CDD_22", "monthly_overheating_hours", "monthly_indoor_temperature_exceedance_degree_hours"),
    ),
    FigureSpec(
        "fig_output2_seasonal_heating_share",
        "Seasonal share of annual useful heating demand",
        "seasonal_monthly_shift",
        "output2_seasonal_heating_share",
        ("seasonal_heating_share_pct",),
    ),
    FigureSpec(
        "fig_annual_electricity_by_scenario",
        "Annual electricity demand by scenario",
        "annual_demand",
        "annual_electricity_by_scenario",
        ("annual_electricity_gross_kWh", "annual_grid_import_kWh"),
    ),
    FigureSpec(
        "fig_annual_gas_by_scenario",
        "Annual gas demand by scenario",
        "annual_demand",
        "annual_gas_by_scenario",
        ("annual_gas_kWh",),
    ),
    FigureSpec(
        "fig_annual_heat_dhw_by_scenario",
        "Useful heating and domestic hot-water demand by scenario",
        "annual_demand",
        "annual_heat_dhw_by_scenario",
        ("annual_useful_heating_kWh", "annual_dhw_kWh"),
    ),
    FigureSpec(
        "fig_peak_grid_import_by_scenario",
        "Peak grid import by scenario",
        "grid_impact",
        "peak_grid_import_by_scenario",
        ("peak_grid_import_W", "winter_peak_grid_import_W", "summer_peak_grid_import_W"),
    ),
    FigureSpec(
        "fig_grid_import_export_by_scenario",
        "Annual grid import and export by scenario",
        "grid_impact",
        "grid_import_export_by_scenario",
        ("annual_grid_import_kWh", "annual_grid_export_kWh"),
    ),
    FigureSpec(
        "fig_pv_self_consumption_export_by_scenario",
        "PV generation, self-consumption, and export by scenario",
        "grid_impact",
        "pv_self_consumption_export_by_scenario",
        ("pv_generation_kWh", "pv_self_consumption_kWh", "pv_export_fraction"),
    ),
    FigureSpec(
        "fig_ev_charging_by_scenario",
        "EV charging demand by scenario",
        "grid_impact",
        "ev_charging_by_scenario",
        ("ev_charging_kWh",),
    ),
    FigureSpec(
        "fig_uncertainty_band_grid_import",
        "Stochastic uncertainty band for annual grid import",
        "uncertainty",
        "uncertainty_band_grid_import",
        ("annual_grid_import_kWh",),
    ),
    FigureSpec(
        "fig_uncertainty_band_peak_import",
        "Stochastic uncertainty band for peak grid import",
        "uncertainty",
        "uncertainty_band_peak_import",
        ("peak_grid_import_W",),
    ),
    FigureSpec(
        "fig_uncertainty_band_useful_heating",
        "Stochastic uncertainty band for useful heating demand",
        "uncertainty",
        "uncertainty_band_useful_heating",
        ("annual_useful_heating_kWh",),
    ),
    FigureSpec(
        "fig_winter_peak_vs_electrification",
        "Winter peak grid import versus electrification level",
        "infrastructure_stress",
        "winter_peak_vs_electrification",
        ("winter_peak_grid_import_W",),
    ),
    FigureSpec(
        "fig_summer_peak_emergence",
        "Summer peak emergence relative to winter peak",
        "infrastructure_stress",
        "summer_peak_emergence",
        ("summer_peak_grid_import_W", "winter_peak_grid_import_W"),
    ),
    FigureSpec(
        "fig_combined_stress_case_grid_peak",
        "Baseline versus combined stress-case grid peak",
        "infrastructure_stress",
        "combined_stress_case_grid_peak",
        ("peak_grid_import_W", "winter_peak_grid_import_W", "summer_peak_grid_import_W"),
    ),
]


def resolve_cli_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists() or cwd_candidate.parent.exists():
        return cwd_candidate
    return REPO_ROOT / path


def figure_specs() -> list[FigureSpec]:
    return list(FIGURE_SPECS)


def create_figure_directories(figures_root: Path) -> None:
    for directory in REQUIRED_DIRECTORIES:
        (figures_root / directory).mkdir(parents=True, exist_ok=True)


def load_csv(path: Path, *, required: bool = True) -> pd.DataFrame:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Missing required CSV file: {path}")
        return pd.DataFrame()
    return pd.read_csv(path)


def _git_state() -> tuple[str, bool | str]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL).strip())
        return commit, dirty
    except Exception:
        return "not_available", "not_available"


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def validate_required_metrics(frame: pd.DataFrame, metrics: Iterable[str], context: str) -> None:
    missing = [metric for metric in metrics if metric not in frame.columns and f"{metric}_mean" not in frame.columns]
    if missing:
        raise ValueError(f"{context} missing required metric(s): {', '.join(missing)}")


def metric_stats_from_aggregates(aggregate_df: pd.DataFrame, metrics: Iterable[str]) -> pd.DataFrame:
    """Return one long row per scenario and metric with mean and percentile columns."""

    id_columns = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]
    require_columns(aggregate_df, id_columns, "scenario aggregate metrics")
    validate_required_metrics(aggregate_df, metrics, "scenario aggregate metrics")
    rows: list[dict[str, Any]] = []
    for _, row in aggregate_df.iterrows():
        base = {column: row[column] for column in id_columns}
        for metric in metrics:
            mean_col = f"{metric}_mean"
            p10_col = f"{metric}_p10"
            p50_col = f"{metric}_median"
            p90_col = f"{metric}_p90"
            p05_col = f"{metric}_p05"
            p95_col = f"{metric}_p95"
            value = row[mean_col] if mean_col in aggregate_df.columns else row[metric]
            rows.append(
                {
                    **base,
                    "metric": metric,
                    "mean": pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0],
                    "p10": pd.to_numeric(pd.Series([row.get(p10_col, value)]), errors="coerce").iloc[0],
                    "p50": pd.to_numeric(pd.Series([row.get(p50_col, value)]), errors="coerce").iloc[0],
                    "p90": pd.to_numeric(pd.Series([row.get(p90_col, value)]), errors="coerce").iloc[0],
                    "p05": pd.to_numeric(pd.Series([row.get(p05_col, value)]), errors="coerce").iloc[0],
                    "p95": pd.to_numeric(pd.Series([row.get(p95_col, value)]), errors="coerce").iloc[0],
                }
            )
    return stable_scenario_sort(pd.DataFrame(rows))


def prepare_climate_metric_data(aggregate_df: pd.DataFrame, metrics: Iterable[str]) -> pd.DataFrame:
    stats = metric_stats_from_aggregates(aggregate_df, metrics)
    stats = stats[stats["technology_case_id"] != "tech_current_stock"].copy().pipe(
        lambda df: pd.concat([stats[stats["scenario_id"] == BASELINE_SCENARIO_ID], df], ignore_index=True)
    )
    grouped = (
        stats.groupby(["climate_window_id", "climate_pathway_id", "metric"], as_index=False)
        .agg(mean=("mean", "mean"), p10=("p10", "mean"), p50=("p50", "mean"), p90=("p90", "mean"))
    )
    grouped["technology_case_id"] = "climate_forcing"
    grouped["scenario_id"] = grouped["climate_window_id"] + "__" + grouped["climate_pathway_id"] + "__climate_forcing"
    return stable_scenario_sort(grouped)


def climate_2050_policy(realization_df: pd.DataFrame) -> tuple[bool, bool]:
    near = realization_df[realization_df["climate_window_id"] == "near_future_2030_2049"]
    mid = realization_df[realization_df["climate_window_id"] == "mid_century_2050_2070"]
    near_includes = bool(near.get("climate_includes_2050", pd.Series(dtype=object)).map(_truthy).any()) if not near.empty else False
    mid_includes = bool(mid.get("climate_includes_2050", pd.Series(dtype=object)).map(_truthy).any()) if not mid.empty else True
    return near_includes, mid_includes


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def _metric_label(metric: str) -> str:
    labels = {
        "annual_electricity_gross_kWh": "Gross electricity (kWh)",
        "annual_grid_import_kWh": "Grid import (kWh)",
        "annual_grid_export_kWh": "Grid export (kWh)",
        "annual_gas_kWh": "Gas demand (kWh)",
        "annual_useful_heating_kWh": "Useful heating (kWh)",
        "annual_dhw_kWh": "DHW demand (kWh)",
        "peak_grid_import_W": "Peak import (W)",
        "winter_peak_grid_import_W": "Winter peak import (W)",
        "summer_peak_grid_import_W": "Summer peak import (W)",
        "pv_generation_kWh": "PV generation (kWh)",
        "pv_self_consumption_kWh": "PV self-consumption (kWh)",
        "pv_export_fraction": "PV export fraction",
        "ev_charging_kWh": "EV charging (kWh)",
        "mean_T_out_C": "Mean outdoor temperature (C)",
        "winter_mean_T_out_C": "Winter mean temperature (C)",
        "summer_mean_T_out_C": "Summer mean temperature (C)",
        "HDD_15": "HDD 15",
        "HDD_18": "HDD 18",
        "CDD_22": "CDD 22",
        "mean_solar_W_m2": "Mean solar irradiance (W/m2)",
    }
    return labels.get(metric, metric.replace("_", " "))


def _short_metric_label(metric: str) -> str:
    return _metric_label(metric).replace(" (kWh)", "").replace(" (W)", "").replace(" (C)", "")


def _x_labels(frame: pd.DataFrame, mode: str = "scenario") -> list[str]:
    if mode == "climate":
        return [
            f"{climate_window_label(row['climate_window_id'])}\n{climate_pathway_label(row['climate_pathway_id'])}"
            for _, row in frame.iterrows()
        ]
    return [scenario_label(row) for _, row in frame.iterrows()]


def _compact_climate_labels(frame: pd.DataFrame) -> list[str]:
    window_labels = {
        "baseline_1981_2005": "Baseline",
        "near_future_2030_2049": "2030-49",
        "mid_century_2050_2070": "2050-70",
        "long_term_2080_2100": "2080-2100",
    }
    pathway_labels = {
        "historical": "Hist.",
        "rcp_2_6": "RCP2.6",
        "rcp_4_5": "RCP4.5",
        "rcp_8_5": "RCP8.5",
    }
    return [
        f"{window_labels.get(str(row['climate_window_id']), str(row['climate_window_id']))}\n"
        f"{pathway_labels.get(str(row['climate_pathway_id']), str(row['climate_pathway_id']))}"
        for _, row in frame.iterrows()
    ]


def _format_unique_range(values: pd.Series, *, integer: bool = True) -> str:
    numeric = pd.to_numeric(values, errors="coerce").dropna().unique()
    if len(numeric) == 0:
        return "n/a"
    if len(numeric) == 1:
        return str(int(numeric[0])) if integer else f"{float(numeric[0]):g}"
    return f"{int(min(numeric))}-{int(max(numeric))}" if integer else f"{float(min(numeric)):g}-{float(max(numeric)):g}"


def _coverage_context(realization_df: pd.DataFrame, *, scope: str, tariffs: str = "N/A") -> str:
    cohort = _format_unique_range(realization_df["cohort_size"]) if "cohort_size" in realization_df else "n/a"
    if {"scenario_id", "realization_id"}.issubset(realization_df.columns):
        seed_counts = realization_df.groupby("scenario_id")["realization_id"].nunique()
        seeds = _format_unique_range(seed_counts)
    elif "n_successful_realizations" in realization_df:
        seeds = _format_unique_range(realization_df["n_successful_realizations"])
    else:
        seeds = "n/a"
    return (
        f"{scope}; cohort={cohort} households; seeds/scenario={seeds}; "
        f"full 2800 leaves not run; no active cooling; tariffs={tariffs}."
    )


def _add_context_note(fig: plt.Figure, note: str) -> None:
    if note:
        fig.text(0.5, 0.012, note, ha="center", va="bottom", fontsize=7.0, color="#4d4d4d")


def plot_metric_bars(
    data: pd.DataFrame,
    spec: FigureSpec,
    output_base: Path,
    formats: Iterable[str],
    *,
    x_mode: str = "scenario",
    y_label: str | None = None,
    context_note: str = "",
) -> dict[str, Path]:
    metrics = list(spec.metrics)
    nrows = len(metrics)
    fig, axes = plt.subplots(nrows=nrows, ncols=1, figsize=FIGURE_SIZES["wide"], squeeze=False)
    for ax, metric in zip(axes.flat, metrics):
        part = stable_scenario_sort(data[data["metric"] == metric])
        if part.empty:
            ax.text(0.5, 0.5, "No rows available", transform=ax.transAxes, ha="center", va="center")
            ax.set_axis_off()
            continue
        x = list(range(len(part)))
        colors = [
            PATHWAY_COLORS.get(str(row["climate_pathway_id"]), TECHNOLOGY_COLORS.get(str(row["technology_case_id"]), "#6b6b6b"))
            for _, row in part.iterrows()
        ]
        lower = (part["mean"] - part["p10"]).clip(lower=0)
        upper = (part["p90"] - part["mean"]).clip(lower=0)
        ax.bar(x, part["mean"], color=colors, edgecolor="#333333", linewidth=0.4)
        ax.errorbar(x, part["mean"], yerr=[lower, upper], fmt="none", ecolor="#202020", capsize=3, linewidth=1.0)
        ax.set_title(_short_metric_label(metric))
        ax.set_ylabel(y_label or _metric_label(metric))
        ax.set_xticks(x)
        ax.set_xticklabels(_x_labels(part, x_mode), rotation=0, ha="center")
    fig.suptitle(spec.title)
    _add_context_note(fig, context_note)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    return save_figure(fig, output_base, formats)


def _phase1_degree_day_rows(degree_day_df: pd.DataFrame) -> pd.DataFrame:
    required = [
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "HDD_18_mean",
        "HDD_18_p10",
        "HDD_18_p50",
        "HDD_18_p90",
        "CDD_22_mean",
        "CDD_22_p10",
        "CDD_22_p50",
        "CDD_22_p90",
    ]
    require_columns(degree_day_df, required, "annual climate degree-day comparison")
    rows: list[dict[str, Any]] = []
    for _, row in degree_day_df.iterrows():
        base = {
            "scenario_id": row["scenario_id"],
            "climate_window_id": row["climate_window_id"],
            "climate_pathway_id": row["climate_pathway_id"],
            "technology_case_id": row["technology_case_id"],
        }
        for metric in ["HDD_18", "CDD_22"]:
            rows.append(
                {
                    **base,
                    "metric": metric,
                    "mean": pd.to_numeric(pd.Series([row[f"{metric}_mean"]]), errors="coerce").iloc[0],
                    "p10": pd.to_numeric(pd.Series([row[f"{metric}_p10"]]), errors="coerce").iloc[0],
                    "p50": pd.to_numeric(pd.Series([row[f"{metric}_p50"]]), errors="coerce").iloc[0],
                    "p90": pd.to_numeric(pd.Series([row[f"{metric}_p90"]]), errors="coerce").iloc[0],
                }
            )
    return stable_scenario_sort(pd.DataFrame(rows))


def plot_phase1_degree_day_intervals(
    degree_day_df: pd.DataFrame,
    spec: FigureSpec,
    output_base: Path,
    formats: Iterable[str],
    *,
    context_note: str = "",
) -> dict[str, Path]:
    data = _phase1_degree_day_rows(degree_day_df)
    fig, axes = plt.subplots(nrows=2, ncols=1, figsize=FIGURE_SIZES["wide"], squeeze=False)
    for ax, metric in zip(axes.flat, ["HDD_18", "CDD_22"]):
        part = stable_scenario_sort(data[data["metric"] == metric])
        if part.empty:
            ax.text(0.5, 0.5, "No Phase 1 degree-day rows available", transform=ax.transAxes, ha="center", va="center")
            continue
        x = list(range(len(part)))
        lower = (part["p50"] - part["p10"]).clip(lower=0)
        upper = (part["p90"] - part["p50"]).clip(lower=0)
        colors = [PATHWAY_COLORS.get(str(value), "#6b6b6b") for value in part["climate_pathway_id"]]
        ax.errorbar(x, part["p50"], yerr=[lower, upper], fmt="none", ecolor="#202020", capsize=4, linewidth=1.0)
        ax.scatter(x, part["p50"], c=colors, edgecolor="#202020", zorder=3)
        ax.set_title(_short_metric_label(metric))
        ax.set_ylabel("Degree-days per year")
        ax.set_xticks(x)
        ax.set_xticklabels(_x_labels(part, "climate"), rotation=0, ha="center")
    fig.suptitle(spec.title)
    axes.flat[-1].set_xlabel("Whiskers show P10-P90 across unique climate-window years")
    _add_context_note(fig, context_note)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    return save_figure(fig, output_base, formats)


def plot_phase1_degree_day_heatmap(
    degree_day_df: pd.DataFrame,
    spec: FigureSpec,
    output_base: Path,
    formats: Iterable[str],
    *,
    metric: str,
    label: str,
    cmap: str,
    context_note: str = "",
) -> dict[str, Path]:
    require_columns(
        degree_day_df,
        ["climate_window_id", "climate_pathway_id", "technology_case_id", metric],
        "annual climate degree-day comparison",
    )
    future = degree_day_df[
        (degree_day_df["technology_case_id"] == "tech_frozen_stock")
        & (degree_day_df["climate_window_id"] != "baseline_1981_2005")
    ].copy()
    future[metric] = pd.to_numeric(future[metric], errors="coerce")
    row_ids = [item for item in CLIMATE_WINDOW_ORDER if item != "baseline_1981_2005" and item in set(future["climate_window_id"])]
    col_ids = [item for item in CLIMATE_PATHWAY_ORDER if item != "historical" and item in set(future["climate_pathway_id"])]
    matrix = future.pivot_table(index="climate_window_id", columns="climate_pathway_id", values=metric, aggfunc="mean").reindex(index=row_ids, columns=col_ids)

    fig, ax = plt.subplots(figsize=FIGURE_SIZES["standard"])
    if matrix.empty:
        ax.text(0.5, 0.5, "No Phase 1 future degree-day rows available", transform=ax.transAxes, ha="center", va="center")
        ax.set_axis_off()
    else:
        image = ax.imshow(matrix.to_numpy(dtype=float), aspect="auto", cmap=cmap)
        ax.set_xticks(range(len(col_ids)))
        ax.set_xticklabels([climate_pathway_label(item) for item in col_ids])
        ax.set_yticks(range(len(row_ids)))
        ax.set_yticklabels([climate_window_label(item) for item in row_ids])
        ax.set_title(spec.title)
        for row_idx, _ in enumerate(row_ids):
            for col_idx, _ in enumerate(col_ids):
                value = matrix.iloc[row_idx, col_idx]
                if pd.notna(value):
                    suffix = "%" if metric.endswith("_pct") else ""
                    ax.text(col_idx, row_idx, f"{value:.1f}{suffix}", ha="center", va="center", color="#202020", fontsize=9)
        colorbar = fig.colorbar(image, ax=ax)
        colorbar.set_label(label)
    _add_context_note(fig, context_note)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    return save_figure(fig, output_base, formats)


def _scenario_panel_grid(n: int) -> tuple[int, int]:
    if n <= 3:
        return 1, max(n, 1)
    if n <= 6:
        return 2, 3
    return 2, 5


def plot_output2_monthly_demand_stacked(
    monthly_df: pd.DataFrame,
    spec: FigureSpec,
    output_base: Path,
    formats: Iterable[str],
    *,
    context_note: str = "",
) -> dict[str, Path]:
    require_columns(
        monthly_df,
        [
            "scenario_id",
            "climate_window_id",
            "climate_pathway_id",
            "technology_case_id",
            "month",
            "monthly_space_heating_useful_kWh_mean",
            "monthly_electricity_gross_kWh_mean",
            "monthly_gas_kWh_mean",
        ],
        "monthly demand-shift comparison",
    )
    scenarios = stable_scenario_sort(monthly_df[["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]].drop_duplicates())
    nrows, ncols = _scenario_panel_grid(len(scenarios))
    fig, axes = plt.subplots(nrows=nrows, ncols=ncols, figsize=(14.0, 7.8), squeeze=False, sharex=True)
    colors = {
        "monthly_space_heating_useful_kWh_mean": "#b2182b",
        "monthly_electricity_gross_kWh_mean": "#2166ac",
        "monthly_gas_kWh_mean": "#6b6b6b",
    }
    labels = {
        "monthly_space_heating_useful_kWh_mean": "Useful heating",
        "monthly_electricity_gross_kWh_mean": "Gross electricity",
        "monthly_gas_kWh_mean": "Gas",
    }
    for ax, (_, scenario) in zip(axes.flat, scenarios.iterrows()):
        part = monthly_df[monthly_df["scenario_id"] == scenario["scenario_id"]].sort_values("month")
        months = part["month"].astype(int).tolist()
        bottom = pd.Series(0.0, index=part.index)
        for metric in colors:
            values = pd.to_numeric(part[metric], errors="coerce").fillna(0.0)
            ax.bar(months, values, bottom=bottom, color=colors[metric], label=labels[metric], linewidth=0)
            bottom = bottom + values
        ax.set_title(f"{climate_window_label(scenario['climate_window_id'])}\n{climate_pathway_label(scenario['climate_pathway_id'])}", fontsize=8.5)
        ax.set_xticks(range(1, 13))
        ax.set_xlim(0.4, 12.6)
    for ax in axes.flat[len(scenarios):]:
        ax.set_axis_off()
    axes.flat[0].set_ylabel("kWh/month")
    handles, legend_labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="lower center", ncol=3)
    fig.suptitle(spec.title)
    _add_context_note(fig, context_note)
    fig.tight_layout(rect=(0, 0.08, 1, 0.96))
    return save_figure(fig, output_base, formats)


def plot_output2_seasonal_heating_shift(
    seasonal_df: pd.DataFrame,
    spec: FigureSpec,
    output_base: Path,
    formats: Iterable[str],
    *,
    context_note: str = "",
) -> dict[str, Path]:
    require_columns(
        seasonal_df,
        ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "season", "seasonal_space_heating_useful_kWh_mean"],
        "seasonal demand-shift comparison",
    )
    part = seasonal_df[seasonal_df["season"].isin(["winter", "shoulder", "summer"])].copy()
    scenarios = stable_scenario_sort(part[["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]].drop_duplicates())
    x = list(range(len(scenarios)))
    width = 0.24
    seasons = ["winter", "shoulder", "summer"]
    colors = {"winter": "#4575b4", "shoulder": "#74add1", "summer": "#f46d43"}
    fig, ax = plt.subplots(figsize=FIGURE_SIZES["wide"])
    for offset, season in enumerate(seasons):
        values = []
        for _, scenario in scenarios.iterrows():
            row = part[(part["scenario_id"] == scenario["scenario_id"]) & (part["season"] == season)]
            values.append(float(row["seasonal_space_heating_useful_kWh_mean"].iloc[0]) if not row.empty else 0.0)
        ax.bar([item + (offset - 1) * width for item in x], values, width=width, color=colors[season], label=season.title())
    ax.set_xticks(x)
    ax.set_xticklabels(_compact_climate_labels(scenarios), rotation=0, ha="center", fontsize=7.5)
    ax.set_ylabel("Useful heating (kWh/season)")
    ax.set_title(spec.title)
    ax.legend(ncol=3)
    _add_context_note(fig, context_note)
    fig.tight_layout(rect=(0, 0.055, 1, 1))
    return save_figure(fig, output_base, formats)


def plot_output2_monthly_cooling_pressure(
    monthly_df: pd.DataFrame,
    spec: FigureSpec,
    output_base: Path,
    formats: Iterable[str],
    *,
    context_note: str = "",
) -> dict[str, Path]:
    metrics = ["monthly_CDD_22_mean", "monthly_overheating_hours_mean", "monthly_indoor_temperature_exceedance_degree_hours_mean"]
    require_columns(
        monthly_df,
        ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "month", *metrics],
        "monthly demand-shift comparison",
    )
    scenarios = stable_scenario_sort(monthly_df[["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]].drop_duplicates())
    fig, axes = plt.subplots(nrows=3, ncols=1, figsize=FIGURE_SIZES["wide"], squeeze=False, sharex=True)
    labels = {
        "monthly_CDD_22_mean": "CDD 22",
        "monthly_overheating_hours_mean": "Overheating hours",
        "monthly_indoor_temperature_exceedance_degree_hours_mean": "Indoor exceedance degree-hours",
    }
    for ax, metric in zip(axes.flat, metrics):
        for _, scenario in scenarios.iterrows():
            part = monthly_df[monthly_df["scenario_id"] == scenario["scenario_id"]].sort_values("month")
            color = PATHWAY_COLORS.get(str(scenario["climate_pathway_id"]), "#4d4d4d")
            label = f"{climate_window_label(scenario['climate_window_id'])} {climate_pathway_label(scenario['climate_pathway_id'])}"
            ax.plot(part["month"], part[metric], marker="o", linewidth=1.1, color=color, alpha=0.75, label=label)
        ax.set_ylabel(labels[metric])
    axes.flat[-1].set_xticks(range(1, 13))
    axes.flat[-1].set_xlabel("Month")
    axes.flat[0].set_title(spec.title)
    handles, legend_labels = axes.flat[0].get_legend_handles_labels()
    by_label = dict(zip(legend_labels, handles))
    fig.legend(by_label.values(), by_label.keys(), loc="lower center", ncol=3)
    _add_context_note(fig, context_note)
    fig.tight_layout(rect=(0, 0.15, 1, 0.96))
    return save_figure(fig, output_base, formats)


def plot_output2_seasonal_heating_share(
    seasonal_df: pd.DataFrame,
    spec: FigureSpec,
    output_base: Path,
    formats: Iterable[str],
    *,
    context_note: str = "",
) -> dict[str, Path]:
    require_columns(
        seasonal_df,
        ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "season", "seasonal_heating_share_pct_mean"],
        "seasonal demand-shift comparison",
    )
    part = seasonal_df[seasonal_df["season"].isin(["winter", "spring", "summer", "autumn"])].copy()
    scenarios = stable_scenario_sort(part[["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]].drop_duplicates())
    seasons = ["winter", "spring", "summer", "autumn"]
    colors = {"winter": "#4575b4", "spring": "#91bfdb", "summer": "#f46d43", "autumn": "#fdae61"}
    x = list(range(len(scenarios)))
    bottom = [0.0 for _ in x]
    fig, ax = plt.subplots(figsize=FIGURE_SIZES["wide"])
    for season in seasons:
        values = []
        for _, scenario in scenarios.iterrows():
            row = part[(part["scenario_id"] == scenario["scenario_id"]) & (part["season"] == season)]
            values.append(float(row["seasonal_heating_share_pct_mean"].iloc[0]) if not row.empty else 0.0)
        ax.bar(x, values, bottom=bottom, color=colors[season], label=season.title(), linewidth=0)
        bottom = [a + b for a, b in zip(bottom, values)]
    ax.set_xticks(x)
    ax.set_xticklabels(_compact_climate_labels(scenarios), rotation=0, ha="center", fontsize=7.5)
    ax.set_ylim(0, 105)
    ax.set_ylabel("Share of annual useful heating (%)")
    ax.set_title(spec.title)
    ax.legend(ncol=4)
    _add_context_note(fig, context_note)
    fig.tight_layout(rect=(0, 0.055, 1, 1))
    return save_figure(fig, output_base, formats)


def plot_structure(output_base: Path, formats: Iterable[str], *, context_note: str = "") -> dict[str, Path]:
    fig, ax = plt.subplots(figsize=FIGURE_SIZES["structure"])
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    levels = [
        ("Climate window", 0.08),
        ("Climate pathway", 0.32),
        ("Technology case", 0.56),
        ("Stochastic realization", 0.80),
    ]
    for title, x in levels:
        ax.text(x, 0.95, title, ha="center", va="center", fontsize=12, fontweight="bold")

    rows = [
        (
            "Baseline 1981-2005",
            "Historical",
            "Current stock",
            "seed_0000 ... seed_0099\n100 stochastic realizations",
            "#f2f2f2",
        ),
        (
            "Near future 2030-2049\n2050 excluded",
            "RCP 2.6 / RCP 4.5 / RCP 8.5",
            "Frozen stock\nModerate electrification\nHigh electrification + PV/EV",
            "seed_0000 ... seed_0099\nper scenario",
            "#eef7fb",
        ),
        (
            "Mid-century 2050-2070\n2050 included",
            "RCP 2.6 / RCP 4.5 / RCP 8.5",
            "Frozen stock\nModerate electrification\nHigh electrification + PV/EV",
            "seed_0000 ... seed_0099\nper scenario",
            "#eff7ef",
        ),
        (
            "Long term 2080-2100",
            "RCP 2.6 / RCP 4.5 / RCP 8.5",
            "Frozen stock\nModerate electrification\nHigh electrification + PV/EV",
            "seed_0000 ... seed_0099\nper scenario",
            "#fff4e6",
        ),
    ]
    y_positions = [0.78, 0.58, 0.38, 0.18]
    box_width = 0.19
    box_height = 0.13
    xs = [0.08, 0.32, 0.56, 0.80]
    for row, y in zip(rows, y_positions):
        for x, text in zip(xs, row[:4]):
            rect = plt.Rectangle((x - box_width / 2, y - box_height / 2), box_width, box_height, facecolor=row[4], edgecolor="#4d4d4d", linewidth=0.8)
            ax.add_patch(rect)
            ax.text(x, y, text, ha="center", va="center", fontsize=8.6)
        for left, right in zip(xs[:-1], xs[1:]):
            ax.annotate("", xy=(right - box_width / 2, y), xytext=(left + box_width / 2, y), arrowprops={"arrowstyle": "->", "lw": 1.1, "color": "#333333"})

    ax.text(
        0.5,
        0.035,
        "scenario_id = {climate_window_id}__{climate_pathway_id}__{technology_case_id}; "
        "scenario_leaf_id appends __{realization_id}. Baseline is the historical current-stock special case.",
        ha="center",
        va="center",
        fontsize=9,
    )
    _add_context_note(fig, context_note)
    fig.tight_layout()
    return save_figure(fig, output_base, formats)


def plot_uncertainty_band(
    bands_df: pd.DataFrame,
    spec: FigureSpec,
    output_base: Path,
    formats: Iterable[str],
    *,
    context_note: str = "",
) -> dict[str, Path]:
    require_columns(
        bands_df,
        ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "metric", "p10", "p50", "p90"],
        "stochastic uncertainty bands",
    )
    metric = spec.metrics[0]
    part = stable_scenario_sort(bands_df[bands_df["metric"] == metric].copy())
    fig, ax = plt.subplots(figsize=FIGURE_SIZES["wide"])
    if part.empty:
        ax.text(0.5, 0.5, "No stochastic band rows available", transform=ax.transAxes, ha="center", va="center")
    else:
        x = list(range(len(part)))
        lower = (part["p50"] - part["p10"]).clip(lower=0)
        upper = (part["p90"] - part["p50"]).clip(lower=0)
        colors = [TECHNOLOGY_COLORS.get(str(value), "#6b6b6b") for value in part["technology_case_id"]]
        ax.errorbar(x, part["p50"], yerr=[lower, upper], fmt="o", color="#222222", ecolor="#222222", capsize=4)
        ax.scatter(x, part["p50"], c=colors, edgecolor="#222222", zorder=3)
        ax.set_xticks(x)
        ax.set_xticklabels(_x_labels(part), rotation=0, ha="center")
    ax.set_title(spec.title)
    ax.set_ylabel(_metric_label(metric))
    ax.set_xlabel("Scenario group; whiskers show P10-P90 across stochastic realizations")
    _add_context_note(fig, context_note)
    fig.tight_layout(rect=(0, 0.055, 1, 1))
    return save_figure(fig, output_base, formats)


def plot_winter_peak_vs_electrification(
    data: pd.DataFrame,
    spec: FigureSpec,
    output_base: Path,
    formats: Iterable[str],
    *,
    context_note: str = "",
) -> dict[str, Path]:
    metric = spec.metrics[0]
    part = metric_stats_from_aggregates(data, [metric])
    part = part[part["technology_case_id"].isin(TECHNOLOGY_CASE_ORDER[1:])].copy()
    fig, ax = plt.subplots(figsize=FIGURE_SIZES["wide"])
    if part.empty:
        ax.text(0.5, 0.5, "No future electrification rows available", transform=ax.transAxes, ha="center", va="center")
    else:
        grouped = stable_scenario_sort(part)
        x = list(range(len(grouped)))
        colors = [TECHNOLOGY_COLORS.get(str(value), "#6b6b6b") for value in grouped["technology_case_id"]]
        ax.bar(x, grouped["mean"], color=colors, edgecolor="#333333", linewidth=0.4)
        ax.set_xticks(x)
        ax.set_xticklabels(_x_labels(grouped), ha="center")
    ax.set_title(spec.title)
    ax.set_ylabel(_metric_label(metric))
    _add_context_note(fig, context_note)
    fig.tight_layout(rect=(0, 0.055, 1, 1))
    return save_figure(fig, output_base, formats)


def plot_summer_peak_emergence(
    data: pd.DataFrame,
    spec: FigureSpec,
    output_base: Path,
    formats: Iterable[str],
    *,
    context_note: str = "",
) -> tuple[dict[str, Path], list[str]]:
    warnings: list[str] = []
    stats = metric_stats_from_aggregates(data, spec.metrics)
    pivot = stats.pivot_table(
        index=["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"],
        columns="metric",
        values="mean",
        aggfunc="first",
    ).reset_index()
    zero_winter = pivot[pivot["winter_peak_grid_import_W"].fillna(0) == 0]
    if not zero_winter.empty:
        warnings.append(f"Skipped {len(zero_winter)} row(s) with zero winter peak when computing summer-to-winter ratio.")
    pivot = pivot[pivot["winter_peak_grid_import_W"].fillna(0) != 0].copy()
    pivot["summer_to_winter_peak_ratio"] = pivot["summer_peak_grid_import_W"] / pivot["winter_peak_grid_import_W"]
    pivot = stable_scenario_sort(pivot)
    fig, ax = plt.subplots(figsize=FIGURE_SIZES["wide"])
    if pivot.empty:
        ax.text(0.5, 0.5, "No valid summer-to-winter peak ratios available", transform=ax.transAxes, ha="center", va="center")
    else:
        x = list(range(len(pivot)))
        colors = [TECHNOLOGY_COLORS.get(str(value), "#6b6b6b") for value in pivot["technology_case_id"]]
        ax.bar(x, pivot["summer_to_winter_peak_ratio"], color=colors, edgecolor="#333333", linewidth=0.4)
        ax.axhline(1.0, color="#222222", linestyle="--", linewidth=1.0)
        ax.set_xticks(x)
        ax.set_xticklabels(_x_labels(pivot), ha="center")
    ax.set_title(spec.title)
    ax.set_ylabel("Summer peak / winter peak")
    _add_context_note(fig, context_note)
    fig.tight_layout(rect=(0, 0.055, 1, 1))
    return save_figure(fig, output_base, formats), warnings


def plot_combined_stress(
    data: pd.DataFrame,
    spec: FigureSpec,
    output_base: Path,
    formats: Iterable[str],
    *,
    context_note: str = "",
) -> dict[str, Path]:
    stats = metric_stats_from_aggregates(data, spec.metrics)
    part = stats[stats["scenario_id"].isin([BASELINE_SCENARIO_ID, COMBINED_STRESS_SCENARIO_ID])].copy()
    fig, axes = plt.subplots(nrows=1, ncols=len(spec.metrics), figsize=FIGURE_SIZES["wide"], squeeze=False)
    for ax, metric in zip(axes.flat, spec.metrics):
        metric_part = stable_scenario_sort(part[part["metric"] == metric])
        if metric_part.empty:
            ax.text(0.5, 0.5, "No rows", transform=ax.transAxes, ha="center", va="center")
            continue
        labels = [
            "Baseline\nhistorical current stock" if row["scenario_id"] == BASELINE_SCENARIO_ID else "Combined stress\nlong-term RCP 8.5 high electrification"
            for _, row in metric_part.iterrows()
        ]
        colors = [TECHNOLOGY_COLORS.get(str(value), "#6b6b6b") for value in metric_part["technology_case_id"]]
        x = list(range(len(metric_part)))
        ax.bar(x, metric_part["mean"], color=colors, edgecolor="#333333", linewidth=0.4)
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_title(_short_metric_label(metric))
    axes.flat[0].set_ylabel("Grid import peak (W)")
    fig.suptitle(spec.title)
    _add_context_note(fig, context_note)
    fig.tight_layout(rect=(0, 0.055, 1, 1))
    return save_figure(fig, output_base, formats)


def _metadata_row(
    spec: FigureSpec,
    files: dict[str, Path],
    *,
    source_files: Iterable[Path],
    scenario_filters: str,
    row_count: int,
    generated_at_utc: str,
    git_commit: str,
    git_is_dirty: bool | str,
    context_note: str,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "figure_id": spec.figure_id,
        "figure_title": spec.title,
        "figure_file_png": _relative(files.get("png", Path(""))) if "png" in files else "",
        "figure_file_pdf": _relative(files.get("pdf", Path(""))) if "pdf" in files else "",
        "figure_category": spec.category,
        "source_data_files": ";".join(_relative(path) for path in source_files),
        "metrics_used": ";".join(spec.metrics) if spec.metrics else "scenario_tree_dimensions",
        "scenario_filters": scenario_filters,
        "generated_at_utc": generated_at_utc,
        "script": SCRIPT_NAME,
        "git_commit": git_commit,
        "git_is_dirty": git_is_dirty,
        "row_count_used": int(row_count),
        "caption_id": spec.figure_id,
        "context_note": context_note,
        "status": "generated",
        "warnings": "; ".join(str(item) for item in warnings if item),
    }


def _caption_for(spec: FigureSpec) -> str:
    metrics = ", ".join(_metric_label(metric) for metric in spec.metrics) if spec.metrics else "scenario-tree dimensions"
    base = {
        "structure": (
            "Scenario-tree structure used for the model_v3 thesis experiments. The diagram shows the ordered construction of climate window, climate pathway, technology case, and stochastic seed realization identifiers, including the historical current-stock baseline special case."
        ),
        "climate": (
            f"{spec.title}. Values are generated from standardized scenario summary metrics ({metrics}) and grouped by canonical climate analysis window and pathway. Near-future covers 2030-2049 and excludes 2050, while mid-century covers 2050-2070 and includes 2050, preserving the non-overlapping canonical window policy."
        ),
        "phase1_annual_demand": (
            f"{spec.title}. Values are generated from the Phase 1 annual climate degree-day comparison table ({metrics}). HDD 18 is used as the climate proxy for space-heating exposure, while CDD 22 is used as a cooling-exposure indicator rather than a modeled cooling-demand result."
        ),
        "seasonal_monthly_shift": (
            f"{spec.title}. Values are generated from Output 2 monthly and seasonal demand-shift comparison tables ({metrics}). Heating, electricity, and gas are model outputs; CDD, overheating hours, and indoor exceedance are cooling-pressure indicators, not active cooling electricity demand. Maximum indoor temperature is diagnostic only and is not used as a main conclusion metric."
        ),
        "annual_demand": (
            f"{spec.title}. Bars show scenario-level means from the Phase 5 aggregate summary with P10-P90 stochastic whiskers where available for {metrics}. Scenarios are grouped by climate window, pathway, and technology case."
        ),
        "grid_impact": (
            f"{spec.title}. Values are generated from standardized scenario-level grid impact metrics ({metrics}); bars show means and P10-P90 whiskers where available. Zero PV or EV values are retained as modelled zero demand or generation rather than treated as missing data."
        ),
        "uncertainty": (
            f"{spec.title}. Markers show P50 and whiskers show P10-P90 stochastic realization bands from the Phase 6 stochastic robustness output for {metrics}. The bands represent seed-level variation within each scenario group."
        ),
        "infrastructure_stress": (
            f"{spec.title}. The figure compares infrastructure stress indicators using {metrics} from standardized scenario summaries, with scenarios ordered by the canonical climate and technology dimensions. The 2050 policy follows the canonical climate windows where climate windows are shown."
        ),
    }
    interpretation = {
        "fig_summer_peak_emergence": " Ratios above one indicate that summer grid import peaks exceed winter peaks for the corresponding scenario.",
        "fig_combined_stress_case_grid_peak": " The comparison isolates the historical baseline against the long-term RCP 8.5 high-electrification PV/EV stress case.",
        "fig_winter_peak_vs_electrification": " The comparison highlights how winter peak import changes across frozen stock, moderate electrification, and high electrification cases.",
    }.get(spec.figure_id, "")
    return base[spec.category] + interpretation


def write_metadata(metadata_rows: list[dict[str, Any]], metadata_dir: Path) -> tuple[Path, Path]:
    metadata_dir.mkdir(parents=True, exist_ok=True)
    csv_path = metadata_dir / "figure_metadata.csv"
    yaml_path = metadata_dir / "figure_metadata.yaml"
    columns = [
        "figure_id",
        "figure_title",
        "figure_file_png",
        "figure_file_pdf",
        "figure_category",
        "source_data_files",
        "metrics_used",
        "scenario_filters",
        "generated_at_utc",
        "script",
        "git_commit",
        "git_is_dirty",
        "row_count_used",
        "caption_id",
        "context_note",
        "status",
        "warnings",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(metadata_rows)
    with yaml_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(metadata_rows, handle, sort_keys=False)
    return csv_path, yaml_path


def write_captions(metadata_rows: list[dict[str, Any]], captions_path: Path) -> Path:
    by_id = {spec.figure_id: spec for spec in FIGURE_SPECS}
    lines = ["# Thesis caption drafts", ""]
    for row in metadata_rows:
        spec = by_id[str(row["figure_id"])]
        figure_file = row.get("figure_file_pdf") or row.get("figure_file_png")
        lines.extend(
            [
                f"### {spec.figure_id}",
                "",
                f"**File:** `{figure_file}`",
                "",
                f"**Draft caption:** {_caption_for(spec)}",
                "",
                f"**Coverage/context:** {row.get('context_note', '')}",
                "",
                f"**Metrics used:** `{row['metrics_used'] or 'scenario-tree metadata'}`",
                "",
                f"**Scenario grouping:** {row['scenario_filters']}",
                "",
            ]
        )
    captions_path.write_text("\n".join(lines), encoding="utf-8")
    return captions_path


def _output_base(figures_root: Path, spec: FigureSpec) -> Path:
    return figures_root / spec.category / spec.filename


def generate_figures(
    *,
    experiment_root: Path,
    figures_root: Path,
    comparison_root: Path,
    realization_metrics: Path,
    scenario_aggregates: Path,
    comparison_definitions: Path,
    formats: Iterable[str],
    write_metadata_flag: bool,
    write_captions_flag: bool,
) -> dict[str, Any]:
    apply_thesis_style()
    create_figure_directories(figures_root)
    realization_df = load_csv(realization_metrics)
    aggregate_df = load_csv(scenario_aggregates)
    bands_path = comparison_root / "stochastic_robustness" / "stochastic_uncertainty_bands.csv"
    bands_path_exists = bands_path.exists()
    bands_df = load_csv(bands_path, required=False)
    if bands_df.empty and not bands_path_exists:
        bands_df = pd.DataFrame(columns=STOCHASTIC_BAND_COLUMNS)
    phase1_degree_day_path = comparison_root / "annual_climate_degree_day_comparison.csv"
    phase1_degree_day_df = load_csv(phase1_degree_day_path)
    monthly_shift_path = comparison_root / "monthly_demand_shift_comparison.csv"
    monthly_shift_df = load_csv(monthly_shift_path)
    seasonal_shift_path = comparison_root / "seasonal_demand_shift_comparison.csv"
    seasonal_shift_df = load_csv(seasonal_shift_path)
    combined_stress_path = comparison_root / "combined_stress_case" / "combined_stress_case_absolute_metrics.csv"
    combined_stress_path_exists = combined_stress_path.exists()

    missing_required = [metric for metric in REQUIRED_METRIC_COLUMNS if metric not in realization_df.columns]
    if missing_required:
        raise ValueError(f"Realization metrics missing required metric(s): {', '.join(missing_required)}")
    generated_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    git_commit, git_is_dirty = _git_state()
    metadata_rows: list[dict[str, Any]] = []

    config_sources = [
        REPO_ROOT / "config" / "scenario_tree" / "scenario_tree_schema.yaml",
        REPO_ROOT / "config" / "scenario_tree" / "climate_windows.yaml",
        REPO_ROOT / "config" / "scenario_tree" / "technology_cases.yaml",
        REPO_ROOT / "config" / "scenario_tree" / "realization_policy.yaml",
    ]
    aggregate_source = [scenario_aggregates]
    realization_source = [realization_metrics]
    bands_source = [bands_path]
    phase1_degree_day_source = [phase1_degree_day_path]
    output2_source = [monthly_shift_path, seasonal_shift_path]
    climate_context = _coverage_context(realization_df, scope="climate-only", tariffs="N/A")
    mixed_context = _coverage_context(realization_df, scope="mixed climate-only/technology-stress", tariffs="N/A")
    stress_context = _coverage_context(realization_df, scope="technology-stress", tariffs="N/A")
    structure_context = _coverage_context(realization_df, scope="scenario-tree design/available runs", tariffs="N/A")

    for spec in FIGURE_SPECS:
        output_base = _output_base(figures_root, spec)
        warnings: list[str] = []
        context_note = mixed_context
        if spec.category == "structure":
            context_note = structure_context
            files = plot_structure(output_base, formats, context_note=context_note)
            source_files = [experiment_root / "manifests" / "scenario_leaf_index.csv", *config_sources]
            row_count = len(realization_df)
            scenario_filters = "all canonical scenario-tree dimensions; seed_0000 through seed_0099 summarized"
        elif spec.category == "climate":
            data = prepare_climate_metric_data(aggregate_df, spec.metrics)
            context_note = climate_context
            files = plot_metric_bars(data, spec, output_base, formats, x_mode="climate", context_note=context_note)
            source_files = aggregate_source + realization_source
            row_count = len(data)
            scenario_filters = "climate metrics deduplicated by climate window and pathway; future current-stock cases excluded"
            if data.empty:
                warnings.append("No climate rows available after canonical filtering.")
        elif spec.figure_id == "fig_phase1_hdd_cdd_dot_interval":
            context_note = climate_context
            files = plot_phase1_degree_day_intervals(phase1_degree_day_df, spec, output_base, formats, context_note=context_note)
            source_files = phase1_degree_day_source
            row_count = len(phase1_degree_day_df)
            scenario_filters = "Phase 1 climate-only groups; unique climate-window years with repeated stochastic seeds de-duplicated"
        elif spec.figure_id == "fig_phase1_hdd_pct_decrease_heatmap":
            files = plot_phase1_degree_day_heatmap(
                phase1_degree_day_df,
                spec,
                output_base,
                formats,
                metric="delta_HDD_18_pct",
                label="HDD 18 change versus baseline (%)",
                cmap="YlGnBu_r",
                context_note=climate_context,
            )
            context_note = climate_context
            source_files = phase1_degree_day_source
            row_count = len(phase1_degree_day_df)
            scenario_filters = "future Phase 1 frozen-stock climate groups; historical baseline used for deltas"
        elif spec.figure_id == "fig_phase1_cdd_abs_increase_heatmap":
            files = plot_phase1_degree_day_heatmap(
                phase1_degree_day_df,
                spec,
                output_base,
                formats,
                metric="delta_CDD_22_abs",
                label="CDD 22 change versus baseline (degree-days/year)",
                cmap="YlOrRd",
                context_note=climate_context,
            )
            context_note = climate_context
            source_files = phase1_degree_day_source
            row_count = len(phase1_degree_day_df)
            scenario_filters = "future Phase 1 frozen-stock climate groups; historical baseline used for absolute CDD deltas"
        elif spec.figure_id == "fig_output2_monthly_demand_stacked":
            context_note = climate_context
            files = plot_output2_monthly_demand_stacked(monthly_shift_df, spec, output_base, formats, context_note=context_note)
            source_files = output2_source
            row_count = len(monthly_shift_df)
            scenario_filters = "Output 2 Phase 1 climate-only groups; monthly means across selected climate years and realizations"
        elif spec.figure_id == "fig_output2_seasonal_heating_shift":
            context_note = climate_context
            files = plot_output2_seasonal_heating_shift(seasonal_shift_df, spec, output_base, formats, context_note=context_note)
            source_files = output2_source
            row_count = len(seasonal_shift_df)
            scenario_filters = "Output 2 winter, shoulder, and summer useful heating demand by Phase 1 climate group"
        elif spec.figure_id == "fig_output2_monthly_cooling_pressure":
            context_note = climate_context
            files = plot_output2_monthly_cooling_pressure(monthly_shift_df, spec, output_base, formats, context_note=context_note)
            source_files = output2_source
            row_count = len(monthly_shift_df)
            scenario_filters = "Output 2 cooling-pressure indicators only; no active cooling final energy is included"
        elif spec.figure_id == "fig_output2_seasonal_heating_share":
            context_note = climate_context
            files = plot_output2_seasonal_heating_share(seasonal_shift_df, spec, output_base, formats, context_note=context_note)
            source_files = output2_source
            row_count = len(seasonal_shift_df)
            scenario_filters = "Output 2 seasonal share of annual useful heating demand by Phase 1 climate group"
        elif spec.category in {"annual_demand", "grid_impact"}:
            data = metric_stats_from_aggregates(aggregate_df, spec.metrics)
            context_note = mixed_context
            files = plot_metric_bars(data, spec, output_base, formats, context_note=context_note)
            source_files = aggregate_source
            row_count = len(data)
            scenario_filters = "all available standardized scenario aggregate rows"
        elif spec.category == "uncertainty":
            context_note = mixed_context
            files = plot_uncertainty_band(bands_df, spec, output_base, formats, context_note=context_note)
            source_files = bands_source if bands_path_exists else aggregate_source
            row_count = int((bands_df["metric"] == spec.metrics[0]).sum()) if "metric" in bands_df else 0
            scenario_filters = "Phase 6 stochastic robustness rows for the requested metric"
            if not bands_path_exists:
                warnings.append("Missing stochastic uncertainty band CSV; generated placeholder figure.")
        elif spec.figure_id == "fig_winter_peak_vs_electrification":
            context_note = stress_context
            files = plot_winter_peak_vs_electrification(aggregate_df, spec, output_base, formats, context_note=context_note)
            source_files = aggregate_source
            row_count = len(metric_stats_from_aggregates(aggregate_df, spec.metrics))
            scenario_filters = "future technology cases: frozen stock, moderate electrification, high electrification + PV/EV"
        elif spec.figure_id == "fig_summer_peak_emergence":
            context_note = mixed_context
            files, extra_warnings = plot_summer_peak_emergence(aggregate_df, spec, output_base, formats, context_note=context_note)
            warnings.extend(extra_warnings)
            source_files = aggregate_source
            row_count = len(metric_stats_from_aggregates(aggregate_df, spec.metrics))
            scenario_filters = "all available scenarios with nonzero winter peak"
        elif spec.figure_id == "fig_combined_stress_case_grid_peak":
            context_note = stress_context
            files = plot_combined_stress(aggregate_df, spec, output_base, formats, context_note=context_note)
            source_files = aggregate_source + ([combined_stress_path] if combined_stress_path_exists else [])
            row_count = int(aggregate_df["scenario_id"].isin([BASELINE_SCENARIO_ID, COMBINED_STRESS_SCENARIO_ID]).sum())
            scenario_filters = f"{BASELINE_SCENARIO_ID} versus {COMBINED_STRESS_SCENARIO_ID}"
            if not combined_stress_path_exists:
                warnings.append("Missing combined-stress comparison CSV; generated figure from aggregate rows only.")
        else:
            raise RuntimeError(f"Unhandled figure spec: {spec.figure_id}")

        metadata_rows.append(
            _metadata_row(
                spec,
                files,
                source_files=source_files,
                scenario_filters=scenario_filters,
                row_count=row_count,
                generated_at_utc=generated_at_utc,
                git_commit=git_commit,
                git_is_dirty=git_is_dirty,
                context_note=context_note,
                warnings=warnings,
            )
        )

    metadata_csv = figures_root / "metadata" / "figure_metadata.csv"
    metadata_yaml = figures_root / "metadata" / "figure_metadata.yaml"
    if write_metadata_flag:
        metadata_csv, metadata_yaml = write_metadata(metadata_rows, figures_root / "metadata")
    captions_path = figures_root / "thesis_caption_drafts.md"
    if write_captions_flag:
        captions_path = write_captions(metadata_rows, captions_path)

    near_includes, mid_includes = climate_2050_policy(realization_df)
    png_count = len(list(figures_root.glob("*/*.png")))
    pdf_count = len(list(figures_root.glob("*/*.pdf")))
    return {
        "figures_written": len(metadata_rows),
        "png_files": png_count,
        "pdf_files": pdf_count,
        "metadata_file": metadata_csv,
        "metadata_yaml": metadata_yaml,
        "caption_drafts": captions_path,
        "manual_spreadsheet_dependencies": 0,
        "near_future_includes_2050": near_includes,
        "mid_century_includes_2050": mid_includes,
        "simulations_run": 0,
        "metadata_rows": metadata_rows,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", default=str(DEFAULT_EXPERIMENT_ROOT))
    parser.add_argument("--figures-root", default=str(DEFAULT_FIGURES_ROOT))
    parser.add_argument("--comparison-root", default=None)
    parser.add_argument("--realization-metrics", default=None)
    parser.add_argument("--scenario-aggregates", default=None)
    parser.add_argument("--comparison-definitions", default=str(DEFAULT_COMPARISON_DEFINITIONS))
    parser.add_argument("--format", dest="formats", action="append", choices=["png", "pdf"], default=None)
    parser.add_argument("--write-metadata", action="store_true")
    parser.add_argument("--write-captions", action="store_true")
    parser.add_argument("--strict", action="store_true", default=True)
    parser.add_argument("--print-summary", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    experiment_root = resolve_cli_path(args.experiment_root)
    figures_root = resolve_cli_path(args.figures_root)
    comparison_root = resolve_cli_path(args.comparison_root) if args.comparison_root else experiment_root / "summaries" / "comparison_level"
    realization_metrics = (
        resolve_cli_path(args.realization_metrics)
        if args.realization_metrics
        else experiment_root / "summaries" / "realization_level" / "scenario_leaf_metrics.csv"
    )
    scenario_aggregates = (
        resolve_cli_path(args.scenario_aggregates)
        if args.scenario_aggregates
        else experiment_root / "summaries" / "scenario_level" / "scenario_aggregate_metrics.csv"
    )
    formats = args.formats or ["png", "pdf"]
    summary = generate_figures(
        experiment_root=experiment_root,
        figures_root=figures_root,
        comparison_root=comparison_root,
        realization_metrics=realization_metrics,
        scenario_aggregates=scenario_aggregates,
        comparison_definitions=resolve_cli_path(args.comparison_definitions),
        formats=formats,
        write_metadata_flag=args.write_metadata,
        write_captions_flag=args.write_captions,
    )
    if args.print_summary:
        print("Figure generation complete.")
        print(f"Figures written: {summary['figures_written']}")
        print(f"PNG files: {summary['png_files']}")
        print(f"PDF files: {summary['pdf_files']}")
        print(f"Metadata file: {_relative(Path(summary['metadata_file']))}")
        print(f"Caption drafts: {_relative(Path(summary['caption_drafts']))}")
        print(f"Manual spreadsheet dependencies: {summary['manual_spreadsheet_dependencies']}")
        print(f"Near-future includes 2050: {'yes' if summary['near_future_includes_2050'] else 'no'}")
        print(f"Mid-century includes 2050: {'yes' if summary['mid_century_includes_2050'] else 'no'}")
        print(f"Simulations run: {summary['simulations_run']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
