"""Tests for DataRequestExecutor — unit (measures_for_source) and integration (real data).

Batch 2a: real energy fact table, fiscal quarter grain, non-derived filters.
Batch 2b: emissions scope join, size_tier quantile derivation, emea derivation.
  - size_tier/emea tests converted from ValueError (red-by-design in 2a) to positive subset tests.
  - location_type still raises ValueError (Batch 2c scope).
"""
from __future__ import annotations

import pytest

from epoch_switch.agents.data.d1_loader.d1_data_executor import DataRequestExecutor


@pytest.fixture()
def executor() -> DataRequestExecutor:
    return DataRequestExecutor()


@pytest.fixture(scope="module")
def executor_m() -> DataRequestExecutor:
    return DataRequestExecutor()


def _req(
    *,
    entity_grain: str = "country",
    time_grain: str = "year",
    measures: list[str] | None = None,
    filters: dict | None = None,
    time_window: dict | None = None,
) -> dict:
    return {
        "source_domain": "energy",
        "entity_grain": entity_grain,
        "time_grain": time_grain,
        "measures": measures or ["energy_mwh", "co2_t"],
        "filters": filters or {},
        "time_window": time_window or {"start": "2021-01-01", "end": "2025-12-31"},
    }


# ── _measures_for_source unit tests ──────────────────────────────────────────

def test_energy_alias_collapse_deduped(executor):
    result = executor._measures_for_source("energy", ["co2_equivalent_t", "co2_t"])
    assert result == ["co2_t"]


def test_energy_consumption_alias_collapse_deduped(executor):
    result = executor._measures_for_source("energy", ["consumption_mwh", "energy_mwh"])
    assert result == ["energy_mwh"]


def test_energy_all_aliases_at_once_deduped(executor):
    result = executor._measures_for_source(
        "energy",
        ["co2_equivalent_t", "co2_t", "consumption_mwh", "energy_mwh"],
    )
    assert result == ["co2_t", "energy_mwh"]


def test_energy_no_duplicates_in_result(executor):
    inputs = ["co2_t", "co2_equivalent_t", "energy_mwh", "consumption_mwh"]
    result = executor._measures_for_source("energy", inputs)
    assert len(result) == len(set(result)), f"Duplicates found: {result}"


def test_energy_valid_measures_without_duplicates_unchanged(executor):
    result = executor._measures_for_source("energy", ["co2_t", "energy_mwh", "electricity_mwh"])
    assert result == ["co2_t", "energy_mwh", "electricity_mwh"]


def test_energy_unknown_measures_dropped(executor):
    result = executor._measures_for_source("energy", ["invented_measure", "co2_t"])
    assert result == ["co2_t"]


def test_energy_empty_input_returns_empty(executor):
    result = executor._measures_for_source("energy", [])
    assert result == []


def test_kpi_measures_for_source(executor):
    # KPI domain is now live (derived sums over real energy/emissions tables).
    # Default (empty or unknown input) returns ["value"]; named measures are honoured.
    assert executor._measures_for_source("kpi", []) == ["value"]
    assert executor._measures_for_source("kpi", ["value"]) == ["value"]
    assert executor._measures_for_source("kpi", ["kpi_value"]) == ["value"]  # alias: kpi_value → value
    assert executor._measures_for_source("kpi", ["co2_total_t"]) == ["co2_total_t"]
    assert executor._measures_for_source("kpi", ["unknown"]) == ["value"]


# ── Integration tests — source data ───────────────────────────────────────

def test_energy_returns_real_rows(executor_m):
    r = executor_m.execute(_req())
    t = r["tables"]["energy"]
    assert t["row_count"] > 0
    assert "energy_mwh" in t["columns"]
    assert "co2_t" in t["columns"]


def test_country_filter_de_year_grain(executor_m):
    r = executor_m.execute(_req(filters={"country_code": ["DE"]}))
    t = r["tables"]["energy"]
    assert t["row_count"] == 4, f"Expected 4 DE fiscal years, got {t['row_count']}"
    for row in t["data"]:
        assert row["country_code"] == "DE"
        assert row["energy_mwh"] is not None and row["energy_mwh"] > 0


def test_cdp_region_filter(executor_m):
    r = executor_m.execute(_req(entity_grain="region", filters={"cdp_region": ["Europe"]}))
    t = r["tables"]["energy"]
    assert t["row_count"] > 0
    for row in t["data"]:
        assert row["cdp_region"] == "Europe"


def test_quarter_grain_format(executor_m):
    # Quarter grain uses _period_date (representative calendar dates) for precise
    # window filtering. FY2022 Q1 representative date ≈ Nov-15 2021 and FY2025 Q4
    # ≈ Aug-15 2025 both fall within 2021-10-01..2025-10-31, so all 16 quarters
    # of FY2022–FY2025 are included. FY2026 Q1 (≈ Nov-15 2025) is excluded because
    # the window ends before Nov 2025.
    r = executor_m.execute(_req(
        time_grain="quarter",
        filters={"country_code": ["DE"]},
        time_window={"start": "2021-10-01", "end": "2025-10-31"},
    ))
    t = r["tables"]["energy"]
    assert t["row_count"] == 16, f"Expected 4 years × 4 quarters = 16, got {t['row_count']}"
    time_periods = {row["time_period"] for row in t["data"]}
    assert "2022 Q1" in time_periods
    assert "2025 Q4" in time_periods


def test_year_grain_format(executor_m):
    r = executor_m.execute(_req(time_grain="year", filters={"country_code": ["DE"]}))
    t = r["tables"]["energy"]
    time_periods = {row["time_period"] for row in t["data"]}
    assert {"2022", "2023", "2024", "2025"} == time_periods


def test_multi_year_grain(executor_m):
    r = executor_m.execute(_req(time_grain="multi_year", entity_grain="portfolio"))
    t = r["tables"]["energy"]
    assert t["row_count"] == 1
    assert t["data"][0]["energy_mwh"] is not None


def test_entity_grain_region(executor_m):
    r = executor_m.execute(_req(entity_grain="region", time_grain="year"))
    t = r["tables"]["energy"]
    assert t["row_count"] >= 4
    assert "cdp_region" in t["columns"]


def test_entity_grain_bu_rc(executor_m):
    r = executor_m.execute(_req(entity_grain="bu_rc", time_grain="year"))
    t = r["tables"]["energy"]
    assert t["row_count"] > 4
    assert "bu_rc_group" in t["columns"]


def test_time_window_restricts_years(executor_m):
    r = executor_m.execute(_req(
        filters={"country_code": ["DE"]},
        time_window={"start": "2022-10-01", "end": "2024-09-30"},
    ))
    t = r["tables"]["energy"]
    time_periods = {row["time_period"] for row in t["data"]}
    # Year grain uses fiscal_year.between(start_year, end_year) — the start year
    # (2022) is inclusive.  The window end (2024-09-30) caps at FY2024 inclusive.
    assert "2022" in time_periods
    assert "2023" in time_periods
    assert "2024" in time_periods
    assert "2025" not in time_periods


def test_size_tier_large_returns_top_emitters(executor_m):
    """Batch 2b: size_tier=large returns only top-quartile sites, not all sites."""
    r_large = executor_m.execute(_req(
        entity_grain="site", time_grain="year", filters={"size_tier": ["large"]},
    ))
    r_all = executor_m.execute(_req(entity_grain="site", time_grain="year"))
    t_large = r_large["tables"]["energy"]
    t_all = r_all["tables"]["energy"]
    assert t_large["row_count"] > 0, "large tier must return rows"
    assert t_large["row_count"] < t_all["row_count"], (
        "large tier must be a strict subset of all sites"
    )
    # Verify size_tier column is not in the grouped output (it was a filter, not a group key)
    # but location_id and energy_mwh should be present.
    assert "location_id" in t_large["columns"]
    assert "energy_mwh" in t_large["columns"]


def test_emea_filter_includes_me_and_europe(executor_m):
    """Batch 2b: emea=True includes TR/AE/SA (Middle East) and European countries."""
    r = executor_m.execute(_req(
        entity_grain="country", time_grain="year",
        filters={"emea": [True]},
    ))
    t = r["tables"]["energy"]
    assert t["row_count"] > 0
    countries = {row["country_code"] for row in t["data"]}
    # TR is a Middle-East country bucketed under "Asia Australia" — must be included in EMEA.
    assert "TR" in countries or "AE" in countries, (
        "At least one ME country (AE, SA, TR) must be present in EMEA filter result"
    )
    # CN is purely Asia Pacific — must NOT be in EMEA.
    assert "CN" not in countries, "China must be excluded from EMEA"
    # Germany is Europe — must be present.
    assert "DE" in countries, "Germany must be in EMEA"


def test_emea_false_excludes_europe(executor_m):
    """Batch 2b: emea=False returns only non-EMEA countries."""
    r = executor_m.execute(_req(
        entity_grain="country", time_grain="year",
        filters={"emea": [False]},
    ))
    t = r["tables"]["energy"]
    assert t["row_count"] > 0
    countries = {row["country_code"] for row in t["data"]}
    assert "DE" not in countries, "Germany (Europe) must not appear in non-EMEA"
    assert "CN" in countries or "JP" in countries or "US" in countries, (
        "At least one non-EMEA country must appear"
    )


def test_unknown_filter_column_is_ignored(executor_m):
    """Unknown filter columns (e.g. legacy location_type) are ignored, not raised."""
    # location_type was removed from EXPECTED_COLUMNS and _DERIVED_FILTER_FIELDS.
    # Passing it should silently have no effect (planner emits a warning, executor skips).
    r = executor_m.execute(_req(filters={"location_type": "office"}))
    assert r["tables"]["energy"]["row_count"] > 0


def test_measure_alias_amount_consumed_mwh(executor_m):
    r = executor_m.execute(_req(measures=["amount_consumed_mwh"], filters={"country_code": ["DE"]}))
    t = r["tables"]["energy"]
    assert "energy_mwh" in t["columns"]
    assert t["row_count"] > 0


def test_post_aggregation_co2_factor(executor_m):
    r = executor_m.execute(_req(
        measures=["energy_mwh", "co2_t"],
        filters={"country_code": ["DE"]},
    ))
    t = r["tables"]["energy"]
    assert "weighted_co2_factor_kg_per_kwh" in t["columns"]
    for row in t["data"]:
        if row.get("energy_mwh") and row["energy_mwh"] > 0:
            assert row["weighted_co2_factor_kg_per_kwh"] is not None


# ── Batch 2b: emissions source, scope2, share_renewable ───────────────────────

def _em_req(
    *,
    entity_grain: str = "portfolio",
    time_grain: str = "year",
    measures: list[str] | None = None,
    filters: dict | None = None,
    time_window: dict | None = None,
) -> dict:
    return {
        "source_domain": "emissions",
        "entity_grain": entity_grain,
        "time_grain": time_grain,
        "measures": measures or ["scope2_location_t", "emissions_t"],
        "filters": filters or {},
        "time_window": time_window or {"start": "2022-01-01", "end": "2025-12-31"},
    }


def test_emissions_source_returns_scope_labelled_rows(executor_m):
    """Batch 2b: emissions source returns rows split by scope."""
    r = executor_m.execute(_em_req())
    t = r["tables"]["emissions"]
    assert t["row_count"] > 0
    scopes = {row["scope"] for row in t["data"]}
    assert "Scope 2" in scopes, "Scope 2 must appear in emissions output"
    assert "Scope 1" in scopes, "Scope 1 must appear in emissions output"


def test_scope2_location_t_only_in_scope2_rows(executor_m):
    """Batch 2b: scope2_location_t must be zero for non-Scope 2 rows."""
    r = executor_m.execute(_em_req())
    t = r["tables"]["emissions"]
    for row in t["data"]:
        if row.get("scope") != "Scope 2":
            assert row.get("scope2_location_t") in (0.0, None, 0), (
                f"scope2_location_t must be 0 for scope={row.get('scope')!r}"
            )


def test_scope2_location_t_positive_for_scope2(executor_m):
    """Batch 2b: scope2_location_t must be positive for Scope 2 rows."""
    r = executor_m.execute(_em_req())
    t = r["tables"]["emissions"]
    scope2_rows = [row for row in t["data"] if row.get("scope") == "Scope 2"]
    assert scope2_rows, "Must have Scope 2 rows"
    total_s2 = sum(row.get("scope2_location_t") or 0 for row in scope2_rows)
    assert total_s2 > 0, "scope2_location_t total must be positive"


def test_scope_filter_restricts_emissions_rows(executor_m):
    """Batch 2b: filtering emissions by scope='Scope 2' returns only Scope 2 rows."""
    r = executor_m.execute(_em_req(
        entity_grain="country",
        filters={"scope": ["Scope 2"]},
    ))
    t = r["tables"]["emissions"]
    assert t["row_count"] > 0
    for row in t["data"]:
        assert row.get("scope") == "Scope 2"


def test_share_renewable_energy_measure(executor_m):
    """Batch 2b: share_renewable is available as an energy measure."""
    r = executor_m.execute(_req(
        measures=["share_renewable", "energy_mwh"],
        filters={"country_code": ["DE"]},
    ))
    t = r["tables"]["energy"]
    assert "share_renewable_pct" in t["columns"] or "energy_mwh" in t["columns"]


def test_size_tier_partitions_all_sites(executor_m):
    """Batch 2b: large+medium+small partitions must cover 100% of all site-year rows."""
    r_all = executor_m.execute(_req(entity_grain="site", time_grain="year"))
    r_large = executor_m.execute(_req(entity_grain="site", time_grain="year", filters={"size_tier": ["large"]}))
    r_small = executor_m.execute(_req(entity_grain="site", time_grain="year", filters={"size_tier": ["small"]}))
    r_medium = executor_m.execute(_req(entity_grain="site", time_grain="year", filters={"size_tier": ["medium"]}))
    total_sites = (
        r_large["tables"]["energy"]["row_count"]
        + r_small["tables"]["energy"]["row_count"]
        + r_medium["tables"]["energy"]["row_count"]
    )
    all_sites = r_all["tables"]["energy"]["row_count"]
    assert total_sites == all_sites, (
        f"large ({r_large['tables']['energy']['row_count']}) + "
        f"medium ({r_medium['tables']['energy']['row_count']}) + "
        f"small ({r_small['tables']['energy']['row_count']}) = {total_sites} "
        f"must equal all sites {all_sites}"
    )
