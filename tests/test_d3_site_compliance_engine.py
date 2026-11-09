"""Tests for SiteComplianceEngine — deterministic math only.

Tests cover: 3-year averaging, gap computation, all three status bands,
multi-rule evaluation, missing column handling, empty data, and per-site
independence.  No LLM, no EvidenceStore.
"""
from __future__ import annotations

import pytest

from epoch_switch.agents.data.d3_time_series.d3_time_series_engine import (
    RecipeEntry,
    SiteComplianceEngine,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _energy_product(rows: list[dict]) -> dict:
    return {"tables": {"energy": {"data": rows}}}


def _uc1_recipe() -> list[RecipeEntry]:
    return [
        RecipeEntry(
            threshold_id="enefg_8",
            rule_label="§8 EnMS obligation",
            column="energy_mwh",
            threshold_value=7500.0,
            near_breach_ratio=0.95,
        ),
        RecipeEntry(
            threshold_id="enefg_16",
            rule_label="§16 waste-heat obligation",
            column="energy_mwh",
            threshold_value=2500.0,
            near_breach_ratio=0.95,
        ),
    ]


ENGINE = SiteComplianceEngine()


# ── Empty / degenerate cases ────────────────────────────────────────────────────

def test_empty_recipe_returns_empty():
    result = ENGINE.compute(_energy_product([{"energy_mwh": 100}]), [])
    assert result.rows == []
    assert result.site_count == 0


def test_empty_data_returns_empty():
    result = ENGINE.compute({"tables": {}}, _uc1_recipe())
    assert result.rows == []


def test_empty_energy_rows_returns_empty():
    result = ENGINE.compute(_energy_product([]), _uc1_recipe())
    assert result.rows == []


def test_missing_column_skips_rule():
    recipe = [RecipeEntry("r", "R", "no_such_col", 1000.0)]
    result = ENGINE.compute(_energy_product([{"energy_mwh": 500}]), recipe)
    assert result.rows == []


# ── Status band tests (single site, single rule) ────────────────────────────────

def test_compliant_status():
    data = _energy_product([{"location_id": "A", "energy_mwh": 1000.0}])
    recipe = [RecipeEntry("r", "R", "energy_mwh", 7500.0, 0.95)]
    result = ENGINE.compute(data, recipe)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.status == "compliant"
    assert row.near_breach is False
    assert row.gap == pytest.approx(1000.0 - 7500.0)


def test_near_breach_lower_bound():
    # 7125 / 7500 = 0.95 — exactly on the near_breach boundary → near_breach
    data = _energy_product([{"location_id": "A", "energy_mwh": 7125.0}])
    recipe = [RecipeEntry("r", "R", "energy_mwh", 7500.0, 0.95)]
    result = ENGINE.compute(data, recipe)
    row = result.rows[0]
    assert row.status == "near_breach"
    assert row.near_breach is True


def test_just_below_near_breach_band():
    # 7124 / 7500 = 0.9499 < 0.95 → compliant
    data = _energy_product([{"location_id": "A", "energy_mwh": 7124.0}])
    recipe = [RecipeEntry("r", "R", "energy_mwh", 7500.0, 0.95)]
    result = ENGINE.compute(data, recipe)
    assert result.rows[0].status == "compliant"


def test_obligated_status_exactly_at_threshold():
    data = _energy_product([{"location_id": "A", "energy_mwh": 7500.0}])
    recipe = [RecipeEntry("r", "R", "energy_mwh", 7500.0, 0.95)]
    result = ENGINE.compute(data, recipe)
    row = result.rows[0]
    assert row.status == "obligated"
    assert row.near_breach is False
    assert row.gap == pytest.approx(0.0)


def test_obligated_status_above_threshold():
    data = _energy_product([{"location_id": "A", "energy_mwh": 9000.0}])
    recipe = [RecipeEntry("r", "R", "energy_mwh", 7500.0, 0.95)]
    result = ENGINE.compute(data, recipe)
    row = result.rows[0]
    assert row.status == "obligated"
    assert row.gap == pytest.approx(9000.0 - 7500.0)


# ── 3-year average ──────────────────────────────────────────────────────────────

def test_3yr_average_across_years():
    # Three years for site A: 6000, 7000, 8000 → avg = 7000
    data = _energy_product([
        {"location_id": "A", "location_name": "Site A", "time_period": "FY2022", "energy_mwh": 6000.0},
        {"location_id": "A", "location_name": "Site A", "time_period": "FY2023", "energy_mwh": 7000.0},
        {"location_id": "A", "location_name": "Site A", "time_period": "FY2024", "energy_mwh": 8000.0},
    ])
    recipe = [RecipeEntry("r", "R", "energy_mwh", 7500.0, 0.95)]
    result = ENGINE.compute(data, recipe)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.avg_3yr == pytest.approx(7000.0)
    # 7000 / 7500 = 0.933 < 0.95 → compliant
    assert row.status == "compliant"
    assert set(row.years_used) == {"FY2022", "FY2023", "FY2024"}


def test_3yr_avg_yields_near_breach():
    # avg = 7250 / 7500 = 0.967 ≥ 0.95 → near_breach
    data = _energy_product([
        {"location_id": "A", "time_period": "FY2022", "energy_mwh": 7000.0},
        {"location_id": "A", "time_period": "FY2023", "energy_mwh": 7250.0},
        {"location_id": "A", "time_period": "FY2024", "energy_mwh": 7500.0},
    ])
    recipe = [RecipeEntry("r", "R", "energy_mwh", 7500.0, 0.95)]
    result = ENGINE.compute(data, recipe)
    row = result.rows[0]
    assert row.avg_3yr == pytest.approx(7250.0)
    assert row.status == "near_breach"


# ── Per-site independence ───────────────────────────────────────────────────────

def test_multiple_sites_independent_status():
    data = _energy_product([
        {"location_id": "A", "location_name": "Erlangen", "energy_mwh": 1000.0},
        {"location_id": "B", "location_name": "Munich", "energy_mwh": 7200.0},
        {"location_id": "C", "location_name": "Berlin",  "energy_mwh": 9000.0},
    ])
    recipe = [RecipeEntry("enefg_8", "§8", "energy_mwh", 7500.0, 0.95)]
    result = ENGINE.compute(data, recipe)
    assert result.site_count == 3
    by_loc = {r.location_id: r for r in result.rows}
    assert by_loc["A"].status == "compliant"
    assert by_loc["B"].status == "near_breach"   # 7200/7500 = 0.96
    assert by_loc["C"].status == "obligated"


# ── Multi-rule evaluation (UC1: §8 + §16) ──────────────────────────────────────

def test_dual_rules_same_site():
    # Site with 3000 MWh: obligated under §16 (3000>2500), compliant under §8
    data = _energy_product([
        {"location_id": "A", "location_name": "Nürnberg", "energy_mwh": 3000.0},
    ])
    result = ENGINE.compute(data, _uc1_recipe())
    # 2 rules × 1 site = 2 rows
    assert len(result.rows) == 2
    rows_by_rule = {r.rule_id: r for r in result.rows}
    assert rows_by_rule["enefg_8"].status == "compliant"
    assert rows_by_rule["enefg_16"].status == "obligated"


def test_summary_counts_accurate():
    data = _energy_product([
        {"location_id": "A", "energy_mwh": 1000.0},   # compliant §8, compliant §16
        {"location_id": "B", "energy_mwh": 7200.0},   # near §8, obligated §16
        {"location_id": "C", "energy_mwh": 9000.0},   # obligated §8, obligated §16
    ])
    result = ENGINE.compute(data, _uc1_recipe())
    # §8: A=compliant, B=near, C=obligated → 1 obl, 1 near, 1 compl
    # §16: A=compliant, B=obligated, C=obligated → 2 obl, 0 near, 1 compl
    # total rows = 6
    assert len(result.rows) == 6
    assert result.n_obligated == 3
    assert result.n_near_breach == 1
    assert result.n_compliant == 2


# ── Gap values ─────────────────────────────────────────────────────────────────

def test_gap_negative_when_compliant():
    data = _energy_product([{"location_id": "A", "energy_mwh": 3000.0}])
    recipe = [RecipeEntry("r", "R", "energy_mwh", 7500.0)]
    result = ENGINE.compute(data, recipe)
    assert result.rows[0].gap < 0


def test_gap_zero_at_threshold():
    data = _energy_product([{"location_id": "A", "energy_mwh": 7500.0}])
    recipe = [RecipeEntry("r", "R", "energy_mwh", 7500.0)]
    result = ENGINE.compute(data, recipe)
    assert result.rows[0].gap == pytest.approx(0.0)


def test_gap_positive_when_obligated():
    data = _energy_product([{"location_id": "A", "energy_mwh": 10000.0}])
    recipe = [RecipeEntry("r", "R", "energy_mwh", 7500.0)]
    result = ENGINE.compute(data, recipe)
    assert result.rows[0].gap == pytest.approx(2500.0)


# ── registry_id forwarding ─────────────────────────────────────────────────────

def test_registry_id_forwarded():
    data = _energy_product([{"location_id": "A", "energy_mwh": 1000.0}])
    recipe = [RecipeEntry("r", "R", "energy_mwh", 7500.0)]
    result = ENGINE.compute(data, recipe, registry_id="uc1_test")
    assert result.registry_id == "uc1_test"
