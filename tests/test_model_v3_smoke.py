"""Smoke test for the model_v3 skeleton."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path


MODEL_V3_SRC = Path(__file__).resolve().parents[1] / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.cohort.cohort_engine import run_cohort_simulation
from model_v3.adapters.forcing_builder import build_prepared_forcing
from model_v3.baseline import MODELLED_END_USE_KEYS, target_electricity_kwh
from model_v3.data.data_module import load_all_sources
from model_v3.interfaces import InputDataset, ModelOutputs, PreparedForcing, TimeSeriesData
from model_v3.output.persistence import persist_cohort_results
from model_v3.simulation.annual_runner import _filter_dataset_to_year, _prepare_reference_year_input, run_annual_simulation
from model_v3.validation.runners.validate_against_aggregate import validate_against_aggregate
from model_v3.validation.runners.validate_baseline_annual import validate_baseline_annual
from model_v3.validation.runners.validate_against_synthetic import validate_against_synthetic
from model_v3.validation.runners.runner_utils import build_runner_cli
from pipelines import run_model_v3


def _load_model_config_for_short_smoke() -> dict:
    """Load the synthetic smoke config without asking it to represent a full year."""

    config_path = Path(__file__).resolve().parents[1] / "config" / "model.yaml"
    config = run_model_v3.load_config(config_path=config_path)
    config.setdefault("simulation", {})
    config["simulation"]["max_steps"] = 24
    config["simulation"]["reference_year"] = None
    timestamps = [f"2023-01-01T{hour:02d}:00:00+01:00" for hour in range(24)]
    daytime_solar = [0.0] * 7 + [80.0, 180.0, 320.0, 450.0, 550.0, 600.0, 520.0, 390.0, 230.0, 90.0] + [0.0] * 7
    appliance_shape = [180.0] * 6 + [260.0, 340.0, 300.0, 240.0, 220.0, 230.0, 250.0, 260.0, 280.0, 330.0, 420.0, 520.0, 610.0, 560.0, 460.0, 360.0, 280.0, 220.0]
    lighting_shape = [120.0] * 7 + [70.0, 40.0, 20.0, 15.0, 15.0, 15.0, 20.0, 30.0, 55.0, 90.0, 130.0, 160.0, 170.0, 150.0, 130.0, 120.0, 120.0]
    cooking_shape = [20.0] * 7 + [180.0, 80.0, 20.0, 20.0, 60.0, 120.0, 60.0, 20.0, 20.0, 80.0, 260.0, 360.0, 220.0, 70.0, 30.0, 20.0, 20.0]

    sources = config.setdefault("data", {}).setdefault("sources", {})
    sources.setdefault("weather", {})["timestamps"] = timestamps
    sources["weather"]["data"] = {"T_outdoor_C": [5.0 + (hour % 6) * 0.2 for hour in range(24)]}
    sources.setdefault("load_profiles", {})["timestamps"] = timestamps
    sources["load_profiles"]["original_timestep_seconds"] = 3600
    sources["load_profiles"]["units"] = "W"
    sources["load_profiles"]["data"] = {
        "appliances": appliance_shape,
        "lighting": lighting_shape,
        "cooking": cooking_shape,
    }
    sources.setdefault("internal_gains", {})["timestamps"] = timestamps
    sources["internal_gains"]["data"] = {"Q_internal_gains_W": [140.0 + (hour % 5) * 15.0 for hour in range(24)]}
    sources.setdefault("solar", {})["timestamps"] = timestamps
    sources["solar"]["data"] = {"Q_solar_gains_W": daytime_solar}
    return config


def _load_thesis_config_for_full_year() -> dict:
    config_path = Path(__file__).resolve().parents[1] / "config" / "thesis.yaml"
    config = run_model_v3.load_config(config_path=config_path)
    config.setdefault("simulation", {})
    config["simulation"]["max_steps"] = None
    return config


class ModelV3SmokeTest(unittest.TestCase):
    """Verify that the model_v3 scaffold imports and runs."""

    def test_main_smoke(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "config" / "model.yaml"
        config = run_model_v3.load_config(config_path=config_path)
        input_data = load_all_sources(config=config)
        prepared = build_prepared_forcing(input_dataset=input_data)
        outputs = run_model_v3.run_pipeline(config=config)

        self.assertIsInstance(prepared, PreparedForcing)
        self.assertIsInstance(outputs, ModelOutputs)
        self.assertTrue(outputs.run_id.startswith("model-v3-"))
        self.assertNotIn("placeholder", outputs.run_id)
        self.assertTrue(hasattr(outputs, "P_el_total_W"))
        self.assertIsNotNone(outputs.P_el_total_W)
        self.assertGreaterEqual(outputs.P_el_total_W, 0.0)
        required_fields = (
            "P_el_total_W",
            "P_el_space_heating_W",
            "P_el_dhw_W",
            "P_el_appliances_W",
            "P_el_lighting_W",
            "P_el_cooking_W",
            "Q_unmet_heating_W",
            "Q_excess_heat_W",
            "comfort_violation_degC",
            "comfort_violation_degree_hours",
        )
        for field_name in required_fields:
            self.assertTrue(hasattr(outputs, field_name))
            self.assertIsNotNone(getattr(outputs, field_name))

    def test_cohort_smoke(self) -> None:
        config = _load_model_config_for_short_smoke()
        config.setdefault("cohort", {})
        config["cohort"]["n_households"] = 10
        config["cohort"]["minimum_households"] = 10
        results = run_cohort_simulation(config=config)

        self.assertIn("mean_profile", results)
        self.assertIn("std_profile", results)
        self.assertIn("diversity_factor", results)
        self.assertIn("P10_profile", results)
        self.assertIn("P90_profile", results)
        self.assertIn("aggregate_dhw_profile", results)
        self.assertIn("aggregated_dhw_peak_W", results)
        self.assertGreater(results["std_profile"], 0.0)
        self.assertGreater(results["P90_profile"], results["P10_profile"])
        self.assertGreaterEqual(results["diversity_factor"], 1.0)
        self.assertGreater(results["aggregated_dhw_peak_W"], 0.0)
        self.assertEqual(results["run_metadata"]["random_seed"], config["cohort"]["random_seed"])
        self.assertEqual(results["run_metadata"]["reference_year"], config["simulation"]["reference_year"])
        self.assertIn("household_class_counts", results["sampled_population"])
        self.assertEqual(sum(results["sampled_population"]["household_class_counts"].values()), results["n_households"])
        self.assertIn("aggregate_calibrated_electricity_kWh", results["annual_energy_summary"])
        self.assertGreater(results["annual_energy_summary"]["aggregate_calibrated_electricity_kWh"], 0.0)
        self.assertTrue(results["annual_calibration_summary"]["available"])
        self.assertIn("raw_annual_energy_kWh", results["household_summaries"][0])
        self.assertIsNotNone(results["household_summaries"][0]["raw_annual_energy_kWh"])

        with tempfile.TemporaryDirectory() as tmp_dir:
            config.setdefault("outputs", {})
            config["outputs"]["root_dir"] = tmp_dir
            artifact_paths = persist_cohort_results(results=results, config=config)
            summary_path = Path(artifact_paths["json"])
            household_energy_path = Path(artifact_paths["household_annual_energy_csv"])
            diagnostics_path = Path(artifact_paths["household_calibration_diagnostics_json"])

            self.assertTrue(summary_path.exists())
            self.assertTrue(household_energy_path.exists())
            self.assertTrue(diagnostics_path.exists())

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertIn("run_metadata", summary)
            self.assertIn("annual_energy_summary", summary)
            self.assertNotIn("household_profiles", summary)
            self.assertNotIn("household_event_profiles", summary)
            self.assertNotIn("household_dhw_profiles", summary)
            self.assertNotIn("aggregate_profile", summary)

    def test_validation_smoke(self) -> None:
        config = _load_model_config_for_short_smoke()
        config.setdefault("cohort", {})
        config["cohort"]["n_households"] = 10
        validation_results = validate_against_synthetic(config=config)
        metrics = validation_results["metrics"]
        report_path = Path(validation_results["report_path"])

        self.assertIn("mean", metrics)
        self.assertIn("variance", metrics)
        self.assertIn("distribution", metrics)
        self.assertIn("temporal", metrics)
        self.assertIn("events", metrics)
        self.assertIn("acceptance_metrics", validation_results)
        self.assertIn("acceptance", validation_results)
        self.assertTrue(report_path.exists())

        for metric_group in metrics.values():
            for value in metric_group.values():
                self.assertFalse(value != value)
        self.assertLess(abs(metrics["mean"]["CVRMSE"]), 500.0)
        self.assertGreaterEqual(metrics["temporal"]["Pearson_correlation"], -1.0)
        self.assertLessEqual(metrics["temporal"]["Pearson_correlation"], 1.0)

    def test_annual_simulation_smoke(self) -> None:
        config = _load_model_config_for_short_smoke()

        annual_results = run_annual_simulation(config=config)

        self.assertEqual(annual_results["n_steps"], 24)
        self.assertEqual(len(annual_results["profile_frame"]), 24)
        self.assertIn("annual_energy_kWh", annual_results)
        self.assertGreaterEqual(annual_results["annual_energy_kWh"], 0.0)
        self.assertIn("space_heating_thermal_kWh", annual_results)
        self.assertIn("dhw_thermal_kWh", annual_results)
        self.assertIn("peak_dhw_thermal_W", annual_results)
        self.assertIn("peak_total_thermal_W", annual_results)
        self.assertIn("Q_total_thermal_W", annual_results["profile_frame"].columns)
        self.assertIn("electricity_calibration", annual_results)
        calibration = annual_results["electricity_calibration"]
        end_use_columns = {
            "appliances": "P_el_appliances_W",
            "lighting": "P_el_lighting_W",
            "cooking": "P_el_cooking_W",
            "dhw": "P_el_dhw_W",
            "space_heating": "P_el_space_heating_W",
        }
        for column_name in end_use_columns.values():
            self.assertIn(column_name, annual_results["profile_frame"].columns)
            self.assertIn(f"raw_{column_name}", annual_results["profile_frame"].columns)
        self.assertIn("raw_P_el_total_W", annual_results["profile_frame"].columns)
        for diagnostic_key in (
            "target_annual_kWh_by_end_use",
            "raw_annual_kWh_by_end_use",
            "calibrated_annual_kWh_by_end_use",
            "scale_factor_by_end_use",
            "fallback_used_by_end_use",
        ):
            self.assertIn(diagnostic_key, calibration)
            for end_use in MODELLED_END_USE_KEYS:
                self.assertIn(end_use, calibration[diagnostic_key])
        for end_use in MODELLED_END_USE_KEYS:
            expected_kwh = calibration["target_annual_kWh_by_end_use"][end_use]
            if end_use == "space_heating":
                expected_kwh *= annual_results["n_steps"] / 8760.0
            self.assertAlmostEqual(
                calibration["calibrated_annual_kWh_by_end_use"][end_use],
                expected_kwh,
                delta=max(1e-6, expected_kwh * 1e-9),
            )

    def test_systems_module_flag_disables_equipment_outputs(self) -> None:
        config = _load_model_config_for_short_smoke()
        config.setdefault("modules", {})
        config["modules"]["systems"] = False

        annual_results = run_annual_simulation(config=config)
        frame = annual_results["profile_frame"]
        calibration = annual_results["electricity_calibration"]

        self.assertTrue((frame["Q_heating_supplied_W"] == 0.0).all())
        self.assertTrue((frame["P_el_space_heating_W"] == 0.0).all())
        self.assertTrue((frame["P_el_dhw_W"] == 0.0).all())
        self.assertTrue((frame["P_el_space_heating_technology_W"] == 0.0).all())
        self.assertTrue((frame["P_el_dhw_technology_W"] == 0.0).all())
        self.assertEqual(calibration["target_annual_kWh_by_end_use"]["space_heating"], 0.0)
        self.assertEqual(calibration["target_annual_kWh_by_end_use"]["dhw"], 0.0)
        self.assertEqual(annual_results["annual_energy_by_carrier_kWh"]["natural_gas"], 0.0)

    def test_cohort_module_flag_disables_cohort_runner(self) -> None:
        config = _load_model_config_for_short_smoke()
        config.setdefault("modules", {})
        config["modules"]["cohort"] = False

        with self.assertRaisesRegex(RuntimeError, "modules.cohort=true.*modules.cohort=false"):
            run_cohort_simulation(config=config)

    def test_belgian_technology_yaml_is_loaded(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "config" / "model.yaml"
        config = run_model_v3.load_config(config_path=config_path)

        self.assertIn("technologies", config)
        self.assertIn("technology_sources", config)
        self.assertAlmostEqual(
            config["technologies"]["heating"]["stock_baseline"]["variables"]["main_space_heating_share"]["belgium"]["gas"],
            0.637,
        )
        self.assertEqual(
            config["technology_sources"]["statbel_electric_vehicle_stock_2025"]["reference_year"],
            2025,
        )
        self.assertTrue(config["uncertainty"]["technology"]["use_belgian_stock_baseline"])

    def test_carrier_aware_heating_pv_and_ev_outputs(self) -> None:
        config = _load_model_config_for_short_smoke()
        config.setdefault("systems", {}).setdefault("heating", {})["technology_type"] = "gas_boiler"
        config.setdefault("systems", {}).setdefault("dhw", {})["technology_type"] = "linked_to_space_heating"
        config.setdefault("der", {}).setdefault("pv", {})["enabled"] = True
        config.setdefault("mobility", {}).setdefault("ev", {})["enabled"] = True

        annual_results = run_annual_simulation(config=config)
        frame = annual_results["profile_frame"]

        for column_name in (
            "P_gas_space_heating_W",
            "P_gas_dhw_W",
            "P_el_gross_actual_W",
            "P_el_grid_import_W",
            "P_el_grid_export_W",
            "P_pv_generation_W",
            "P_el_ev_charging_W",
        ):
            self.assertIn(column_name, frame.columns)
        self.assertGreater(annual_results["annual_energy_by_carrier_kWh"]["natural_gas"], 0.0)
        self.assertGreaterEqual(annual_results["annual_pv_generation_kWh"], 0.0)
        self.assertGreater(annual_results["annual_ev_charging_kWh"], 0.0)
        self.assertTrue((frame["P_el_grid_import_W"] >= 0.0).all())
        self.assertTrue((frame["P_el_grid_export_W"] >= 0.0).all())

    def test_aggregate_validation_smoke(self) -> None:
        config = _load_model_config_for_short_smoke()
        config.setdefault("validation", {})
        config["validation"]["aggregate_mode"] = "normalized_internal"
        with tempfile.TemporaryDirectory() as tmp_dir:
            reference_path = Path(tmp_dir) / "aggregate_reference.csv"
            reference_path.write_text(
                "timestamp,value\n"
                + "\n".join(
                    f"2023-01-01T{hour:02d}:00:00+01:00,{400 + 50 * (hour % 6)}"
                    for hour in range(24)
                )
                + "\n",
                encoding="utf-8",
            )
            config["validation"]["aggregate_path"] = str(reference_path)
            config["validation"]["timestamp_column"] = "timestamp"
            config["validation"]["value_column"] = "value"
            config["validation"]["aggregate_units"] = "W"

            validation_results = validate_against_aggregate(config=config)
        self.assertIn("metrics", validation_results)
        self.assertIn("independence", validation_results)
        self.assertIn("shape", validation_results["metrics"])
        self.assertIn("seasonal", validation_results["metrics"])
        self.assertIn("temporal", validation_results["metrics"])
        self.assertIn("validation_independence", validation_results["independence"])

    def test_baseline_annual_validation_smoke(self) -> None:
        config = _load_model_config_for_short_smoke()

        results = validate_baseline_annual(config=config)
        self.assertIn("annual_summary", results)
        self.assertIn("end_use", results)
        self.assertIn("checks", results)
        self.assertIn("annual_electricity_kWh", results["annual_summary"])
        self.assertIn("appliances_share", results["end_use"])
        self.assertIn("electricity_calibration", results)

    def test_full_year_thermal_scaling_sanity(self) -> None:
        for household_count in (1, 10):
            with self.subTest(household_count=household_count):
                config = _load_thesis_config_for_full_year()
                config.setdefault("cohort", {})
                config["cohort"]["n_households"] = household_count
                config["cohort"]["minimum_households"] = household_count

                annual_results = run_annual_simulation(config=config)

                self.assertGreater(annual_results["space_heating_thermal_kWh"], 5000.0)
                self.assertGreater(annual_results["dhw_thermal_kWh"], 1000.0)

    def test_full_year_stochastic_variance_and_peak_sanity(self) -> None:
        config = _load_thesis_config_for_full_year()
        config.setdefault("cohort", {})
        config["cohort"]["n_households"] = 10
        config["cohort"]["minimum_households"] = 10

        results = run_cohort_simulation(config=config)
        peak_distribution = dict(results["peak_distribution"])

        self.assertEqual(results["n_steps"], 8760)
        self.assertEqual(results["n_households"], 10)
        self.assertGreater(results["std_profile"], 0.0)
        self.assertGreater(results["diversity_factor"], 1.0)
        self.assertGreater(peak_distribution["max_peak_W"], peak_distribution["min_peak_W"])
        self.assertTrue(results["annual_calibration_summary"]["available"])

    def test_weather_reference_year_coverage_guard(self) -> None:
        weather = TimeSeriesData(
            timestamps=(datetime(2023, 1, 1, 0),),
            columns={"T_outdoor_C": (5.0,)},
            metadata={"input_file_path": "unit://short-weather.csv"},
        )
        input_data = InputDataset(
            source_data={"weather": weather},
            target_resolution_seconds=3600,
        )
        config = {
            "simulation": {"reference_year": 2023, "max_steps": 24},
            "data": {"target_resolution_seconds": 3600},
        }

        with self.assertRaisesRegex(ValueError, "selected 1 rows.*expected near 8760"):
            _prepare_reference_year_input(input_data, config=config)

    def test_requested_year_unavailable_is_printed(self) -> None:
        weather = TimeSeriesData(
            timestamps=(datetime(2022, 1, 1, 0),),
            columns={"T_outdoor_C": (5.0,)},
            metadata={"input_file_path": "unit://wrong-year-weather.csv"},
        )
        input_data = InputDataset(
            source_data={"weather": weather},
            target_resolution_seconds=3600,
        )
        config = {
            "simulation": {"reference_year": 2023, "max_steps": 24},
            "data": {"target_resolution_seconds": 3600},
        }

        captured = StringIO()
        with redirect_stdout(captured):
            with self.assertRaisesRegex(ValueError, "Requested year 2023 is unavailable"):
                _prepare_reference_year_input(input_data, config=config)
        self.assertIn("Requested year 2023 is unavailable", captured.getvalue())

    def test_representative_dataset_unavailable_year_is_printed(self) -> None:
        load_profiles = TimeSeriesData(
            timestamps=(datetime(2022, 1, 1, 0),),
            columns={"P_el_appliances_W": (10.0,)},
            metadata={"input_file_path": "unit://wrong-year-loads.csv"},
        )

        captured = StringIO()
        with redirect_stdout(captured):
            filtered = _filter_dataset_to_year(load_profiles, 2023)

        self.assertEqual(len(filtered.timestamps), 1)
        self.assertIn("Requested year 2023 is unavailable", captured.getvalue())
        self.assertIn("reference_year_warnings", filtered.metadata)

    def test_validation_runner_cli_accepts_config(self) -> None:
        parser = build_runner_cli("unit parser")
        args = parser.parse_args(["--config", "config/thesis.yaml", "--quick"])
        self.assertEqual(args.config, "config/thesis.yaml")
        self.assertTrue(args.quick)

        parser_without_quick = build_runner_cli("unit parser", include_quick=False)
        args_without_quick = parser_without_quick.parse_args(["--config", "config/thesis.yaml"])
        self.assertEqual(args_without_quick.config, "config/thesis.yaml")
        self.assertFalse(hasattr(args_without_quick, "quick"))


if __name__ == "__main__":
    unittest.main()
