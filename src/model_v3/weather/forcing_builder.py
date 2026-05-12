"""Build aligned climate forcing bundles for the annual simulation pipeline."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from model_v3.interfaces import TimeSeriesData


@dataclass(frozen=True)
class PreparedForcing:
    """Aligned weather and solar forcing bundle for one simulation member."""

    frame: pd.DataFrame

    @property
    def temperature_C(self) -> pd.Series:
        return self.frame["temperature_C"]

    @property
    def ghi_Wm2(self) -> pd.Series:
        return self.frame["ghi_Wm2"]

    @property
    def solar_south(self) -> pd.Series:
        return self.frame["solar_south"]

    @property
    def solar_east(self) -> pd.Series:
        return self.frame["solar_east"]

    @property
    def solar_west(self) -> pd.Series:
        return self.frame["solar_west"]

    @property
    def solar_north(self) -> pd.Series:
        return self.frame["solar_north"]

    @property
    def wind_ms(self) -> pd.Series | None:
        return self.frame["wind_ms"] if "wind_ms" in self.frame.columns else None


def _ensure_datetime_indexed_frame(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    """Validate a forcing frame and return a sorted copy."""

    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError(f"{label} must be indexed by datetime.")
    prepared = frame.sort_index().copy()
    if prepared.empty:
        raise ValueError(f"{label} is empty.")
    if prepared.index.has_duplicates:
        raise ValueError(f"{label} contains duplicate timestamps.")
    return prepared


def build_forcing(weather_df: pd.DataFrame, solar_dict: dict[str, pd.Series]) -> PreparedForcing:
    """Combine weather and facade solar series into one perfectly aligned forcing frame."""

    weather = _ensure_datetime_indexed_frame(weather_df, label="weather_df")
    required_weather_columns = {"temperature_C", "ghi_Wm2"}
    missing_weather = required_weather_columns.difference(weather.columns)
    if missing_weather:
        raise ValueError(f"weather_df is missing required columns: {sorted(missing_weather)}")

    required_orientations = ("south", "east", "west", "north")
    missing_orientations = sorted(set(required_orientations).difference(solar_dict))
    if missing_orientations:
        raise ValueError(f"solar_dict is missing orientations: {missing_orientations}")

    frame = pd.DataFrame(index=weather.index)
    frame["temperature_C"] = weather["temperature_C"].astype(float)
    frame["ghi_Wm2"] = weather["ghi_Wm2"].astype(float)
    if "wind_ms" in weather.columns:
        frame["wind_ms"] = weather["wind_ms"].astype(float)

    for orientation in required_orientations:
        series = solar_dict[orientation]
        if not isinstance(series.index, pd.DatetimeIndex):
            raise TypeError(f"solar_dict['{orientation}'] must be indexed by datetime.")
        if not series.sort_index().index.equals(weather.index):
            raise ValueError(f"solar_dict['{orientation}'] timestamps do not align with weather_df.")
        frame[f"solar_{orientation}"] = series.astype(float)

    if frame.isna().any().any():
        raise ValueError("Prepared climate forcing contains NaN values.")
    return PreparedForcing(frame=frame)


def forcing_to_source_data(forcing: PreparedForcing) -> dict[str, TimeSeriesData]:
    """Convert an aligned climate forcing bundle into model_v3 source datasets."""

    frame = forcing.frame
    timestamps = tuple(frame.index.to_pydatetime())
    weather_columns: dict[str, tuple[float | None, ...]] = {
        "T_outdoor_C": tuple(float(value) for value in frame["temperature_C"].to_numpy(dtype=float)),
        "ghi_Wm2": tuple(float(value) for value in frame["ghi_Wm2"].to_numpy(dtype=float)),
    }
    if "wind_ms" in frame.columns:
        weather_columns["wind_ms"] = tuple(float(value) for value in frame["wind_ms"].to_numpy(dtype=float))

    return {
        "weather": TimeSeriesData(
            timestamps=timestamps,
            columns=weather_columns,
            metadata={
                "source_name": "weather",
                "original_timestep_seconds": 3600,
                "original_resolution": 3600,
                "climate_source": "pvgis_member",
            },
        ),
        "solar": TimeSeriesData(
            timestamps=timestamps,
            columns={
                "I_south": tuple(float(value) for value in frame["solar_south"].to_numpy(dtype=float)),
                "I_east": tuple(float(value) for value in frame["solar_east"].to_numpy(dtype=float)),
                "I_west": tuple(float(value) for value in frame["solar_west"].to_numpy(dtype=float)),
                "I_north": tuple(float(value) for value in frame["solar_north"].to_numpy(dtype=float)),
            },
            metadata={
                "source_name": "solar",
                "original_timestep_seconds": 3600,
                "original_resolution": 3600,
                "climate_source": "pvgis_member",
            },
        ),
    }
