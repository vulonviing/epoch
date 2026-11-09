from __future__ import annotations

from epoch_switch.agents.data.d1_loader.derived_case_facts_builder import (
    build_derived_case_facts,
)
from epoch_switch.usecases.registry import ThresholdSpec


def _energy_product(rows: list[dict]) -> dict:
    return {
        "tables": {
            "energy": {
                "data": rows,
            }
        }
    }


def test_empty_thresholds_returns_none():
    result = build_derived_case_facts({}, _energy_product([{"energy_mwh": 100}]))
    assert result is None


def test_no_energy_data_returns_none():
    params = {"enefg_8": ThresholdSpec(value=7500, column="energy_mwh", aggregation="max")}
    result = build_derived_case_facts(params, {"tables": {}})
    assert result is None


def test_no_energy_rows_returns_none():
    params = {"enefg_8": ThresholdSpec(value=7500, column="energy_mwh", aggregation="max")}
    result = build_derived_case_facts(params, _energy_product([]))
    assert result is None


def test_missing_column_skips_threshold():
    params = {"enefg_8": ThresholdSpec(value=7500, column="missing_col", aggregation="max")}
    result = build_derived_case_facts(
        params, _energy_product([{"energy_mwh": 100}])
    )
    assert result is None


def test_single_threshold_below():
    params = {"enefg_8": ThresholdSpec(value=7500, column="energy_mwh", aggregation="max")}
    data = _energy_product([
        {"location_id": 1, "energy_mwh": 3000},
        {"location_id": 2, "energy_mwh": 5000},
    ])
    result = build_derived_case_facts(params, data)
    assert result is not None
    assert result["observed_value"] == 5000.0
    assert result["threshold"] == 7500.0
    assert result["calculation_method"] == "deterministic_threshold_comparison"
    assert result["evidence_assessable"] is True
    assert "near_breach" not in result
    assert "near_breach_details" not in result


def test_single_threshold_above():
    params = {
        "enefg_8": ThresholdSpec(
            value=7500, column="energy_mwh", aggregation="max", near_breach_ratio=0.95
        )
    }
    data = _energy_product([
        {"location_id": 1, "energy_mwh": 7200},
    ])
    result = build_derived_case_facts(params, data)
    assert result is not None
    assert result["observed_value"] == 7200.0


def test_exact_threshold():
    params = {"enefg_8": ThresholdSpec(value=7500, column="energy_mwh", aggregation="max")}
    data = _energy_product([{"location_id": 1, "energy_mwh": 7500}])
    result = build_derived_case_facts(params, data)
    assert result is not None
    assert result["observed_value"] == 7500.0
    assert result["threshold"] == 7500.0


def test_dual_thresholds_closest_wins():
    params = {
        "enefg_8": ThresholdSpec(value=7500, column="energy_mwh", aggregation="max"),
        "enefg_16": ThresholdSpec(value=2500, column="energy_mwh", aggregation="max"),
    }
    data = _energy_product([
        {"location_id": 1, "energy_mwh": 3000},
    ])
    result = build_derived_case_facts(params, data)
    assert result is not None
    # §16 has the highest ratio (3000/2500=1.2), so it is the "closest"
    assert result["observed_value"] == 3000.0
    assert result["threshold"] == 2500.0


def test_dual_thresholds_both_below():
    params = {
        "enefg_8": ThresholdSpec(value=7500, column="energy_mwh", aggregation="max"),
        "enefg_16": ThresholdSpec(value=2500, column="energy_mwh", aggregation="max"),
    }
    data = _energy_product([
        {"location_id": 1, "energy_mwh": 1000},
    ])
    result = build_derived_case_facts(params, data)
    assert result is not None
    assert result["observed_value"] == 1000.0


def test_aggregation_sum():
    params = {"enefg_total": ThresholdSpec(value=10000, column="energy_mwh", aggregation="sum")}
    data = _energy_product([
        {"location_id": 1, "energy_mwh": 4000},
        {"location_id": 2, "energy_mwh": 3000},
        {"location_id": 3, "energy_mwh": 3000},
    ])
    result = build_derived_case_facts(params, data)
    assert result["observed_value"] == 10000.0


def test_aggregation_mean():
    params = {"avg_threshold": ThresholdSpec(value=5000, column="energy_mwh", aggregation="mean")}
    data = _energy_product([
        {"location_id": 1, "energy_mwh": 5000},
        {"location_id": 2, "energy_mwh": 4000},
    ])
    result = build_derived_case_facts(params, data)
    assert result["observed_value"] == 4500.0


def test_zero_threshold_handled_gracefully():
    params = {"zero": ThresholdSpec(value=0, column="energy_mwh", aggregation="max")}
    data = _energy_product([{"location_id": 1, "energy_mwh": 100}])
    result = build_derived_case_facts(params, data)
    assert result is not None


def test_dict_spec_compatible():
    params = {"enefg_8": {"value": 7500, "column": "energy_mwh", "aggregation": "max", "near_breach_ratio": 0.95}}
    data = _energy_product([{"location_id": 1, "energy_mwh": 7200}])
    result = build_derived_case_facts(params, data)
    assert result is not None
    assert result["observed_value"] == 7200.0
