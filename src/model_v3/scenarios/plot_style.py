"""Shared thesis plotting style and scenario ordering helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import pandas as pd


DPI = 220
LINE_WIDTH = 1.8
MARKER_SIZE = 5.5
GRID_ALPHA = 0.28

FIGURE_SIZES = {
    "wide": (11.0, 6.2),
    "standard": (8.0, 5.2),
    "tall": (8.2, 7.2),
    "structure": (12.0, 7.0),
}

CLIMATE_WINDOW_ORDER = [
    "baseline_1981_2005",
    "near_future_2030_2049",
    "mid_century_2050_2070",
    "long_term_2080_2100",
]

CLIMATE_PATHWAY_ORDER = [
    "historical",
    "rcp_2_6",
    "rcp_4_5",
    "rcp_8_5",
]

TECHNOLOGY_CASE_ORDER = [
    "tech_current_stock",
    "tech_frozen_stock",
    "tech_moderate_electrification",
    "tech_high_electrification_pv_ev",
]

CLIMATE_WINDOW_LABELS = {
    "baseline_1981_2005": "Baseline 1981-2005",
    "near_future_2030_2049": "Near future 2030-2049",
    "mid_century_2050_2070": "Mid-century 2050-2070",
    "long_term_2080_2100": "Long term 2080-2100",
}

CLIMATE_PATHWAY_LABELS = {
    "historical": "Historical",
    "rcp_2_6": "RCP 2.6",
    "rcp_4_5": "RCP 4.5",
    "rcp_8_5": "RCP 8.5",
}

TECHNOLOGY_CASE_LABELS = {
    "tech_current_stock": "Current stock",
    "tech_frozen_stock": "Frozen stock",
    "tech_moderate_electrification": "Moderate electrification",
    "tech_high_electrification_pv_ev": "High electrification + PV/EV",
}

PATHWAY_COLORS = {
    "historical": "#4d4d4d",
    "rcp_2_6": "#2b8cbe",
    "rcp_4_5": "#41ab5d",
    "rcp_8_5": "#d95f0e",
}

TECHNOLOGY_COLORS = {
    "tech_current_stock": "#595959",
    "tech_frozen_stock": "#756bb1",
    "tech_moderate_electrification": "#1b9e77",
    "tech_high_electrification_pv_ev": "#d95f02",
}


def apply_thesis_style() -> None:
    """Apply restrained Matplotlib defaults for thesis figures."""

    plt.rcParams.update(
        {
            "figure.dpi": DPI,
            "savefig.dpi": DPI,
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.5,
            "lines.linewidth": LINE_WIDTH,
            "lines.markersize": MARKER_SIZE,
            "axes.grid": True,
            "grid.alpha": GRID_ALPHA,
            "grid.linewidth": 0.6,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def climate_window_label(value: str) -> str:
    return CLIMATE_WINDOW_LABELS.get(str(value), str(value).replace("_", " "))


def climate_pathway_label(value: str) -> str:
    return CLIMATE_PATHWAY_LABELS.get(str(value), str(value).replace("_", " "))


def technology_case_label(value: str) -> str:
    return TECHNOLOGY_CASE_LABELS.get(str(value), str(value).replace("_", " "))


def scenario_label(row: pd.Series) -> str:
    return "\n".join(
        [
            climate_window_label(str(row["climate_window_id"])),
            climate_pathway_label(str(row["climate_pathway_id"])),
            technology_case_label(str(row["technology_case_id"])),
        ]
    )


def _order_index(order: list[str], value: object) -> int:
    try:
        return order.index(str(value))
    except ValueError:
        return len(order)


def stable_scenario_sort(frame: pd.DataFrame) -> pd.DataFrame:
    """Sort rows by canonical climate, pathway, technology, and scenario IDs."""

    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out["_window_order"] = out["climate_window_id"].map(lambda value: _order_index(CLIMATE_WINDOW_ORDER, value))
    out["_pathway_order"] = out["climate_pathway_id"].map(lambda value: _order_index(CLIMATE_PATHWAY_ORDER, value))
    out["_technology_order"] = out["technology_case_id"].map(lambda value: _order_index(TECHNOLOGY_CASE_ORDER, value))
    sort_cols = ["_window_order", "_pathway_order", "_technology_order"]
    for col in ["scenario_id", "metric"]:
        if col in out.columns:
            sort_cols.append(col)
    out = out.sort_values(sort_cols).drop(columns=["_window_order", "_pathway_order", "_technology_order"])
    return out.reset_index(drop=True)


def require_columns(frame: pd.DataFrame, columns: Iterable[str], context: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{context} missing required column(s): {', '.join(missing)}")


def save_figure(fig: plt.Figure, output_base: Path, formats: Iterable[str]) -> dict[str, Path]:
    """Save a figure to stable paths and return written files by format."""

    output_base.parent.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for fmt in formats:
        suffix = fmt.lower().lstrip(".")
        if suffix not in {"png", "pdf"}:
            raise ValueError(f"Unsupported figure format: {fmt}")
        path = output_base.with_suffix(f".{suffix}")
        fig.savefig(path, bbox_inches="tight")
        written[suffix] = path
    plt.close(fig)
    return written
