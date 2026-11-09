"""Tests for the shared DataCatalogBuilder (agents/tools/data_catalog.py).

Batch 1 scope: dimension tables only (regions, bu_rc_groups, dist_ie_locations).
Batch 2 tests (fact tables, real measures, grain transforms) are marked xfail/skip
where noted.

No frozen LEGACY_HASH: the hash was computed off the deleted synthetic schema and
must not be reintroduced (it would churn on every real-data change). Structural
correctness is verified by asserting key presence and field types instead.
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from epoch_switch.core.data_catalog import (
    DataCatalogBuilder,
    legacy_catalog_view,
    LEGACY_CATALOG_KEYS,
    expand_region_bucket,
)
from epoch_switch.regulation_cli import _all_catalog_targets


EXTENSION_KEYS = {
    "structured_relationships",
    "field_index",
    "filter_definitions",
    "grain_definitions",
    "quality_check_definitions",
    "executor_capabilities",
    "schema_signature",
}


# ── Shared fixture ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def catalog() -> dict:
    return DataCatalogBuilder().build()


@pytest.fixture(scope="module")
def legacy(catalog) -> dict:
    return legacy_catalog_view(catalog)


# ── v0.1 legacy view contract ──────────────────────────────────────────────────

def test_legacy_catalog_view_v01_contract(legacy):
    """The compatibility view keeps only its declared planner-facing keys."""
    assert legacy["catalog_version"] == "0.1"
    assert EXTENSION_KEYS.isdisjoint(legacy), (
        "Extension keys must not bleed into the v0.1 view"
    )
    # All v0.1 keys must be present.
    for key in LEGACY_CATALOG_KEYS:
        assert key in legacy, f"v0.1 key missing: {key}"
    # available_date_range must have the clamp keys the executor reads (renamed Batch 3).
    adr = legacy["available_date_range"]
    assert "min_period_date" in adr, "d1_data_request_planner._clamp_time_window needs this"
    assert "max_period_date" in adr


def test_v02_catalog_has_extension_keys(catalog):
    """v0.2 catalog must include all extension keys on top of legacy keys."""
    assert catalog["catalog_version"] == "0.2"
    assert EXTENSION_KEYS <= set(catalog)


# ── Measure requestable flag contract ─────────────────────────────────────────

def test_measures_all_carry_requestable_flag(legacy):
    """Every grouped measure exposed to DA1 must carry an explicit requestable bool."""
    measures = legacy["measures"]
    for group_name, group in measures.items():
        for measure_name, defn in group.items():
            assert isinstance(defn, dict), (
                f"{group_name}.{measure_name} must be a dict"
            )
            assert "requestable" in defn, (
                f"{group_name}.{measure_name} is missing 'requestable' flag"
            )
            assert isinstance(defn["requestable"], bool), (
                f"{group_name}.{measure_name}.requestable must be bool"
            )


def test_requestable_flag_known_values(legacy):
    """Spot-check the requestable flag values for representative measures."""
    m = legacy["measures"]
    # Orderable energy measures
    assert m["energy_fact_measures"]["amount_consumed_mwh"]["requestable"] is True
    assert m["energy_fact_measures"]["co2e_t"]["requestable"] is True
    assert m["energy_fact_measures"]["emissions_t"]["requestable"] is True
    # share_renewable is a derived ratio — not orderable
    assert m["energy_fact_measures"]["share_renewable"]["requestable"] is False
    # KPI measures: orderable via kind="measure", name="value" + kpi_name filter
    assert m["kpi_fact_measures"]["value"]["requestable"] is True
    assert m["kpi_fact_measures"]["kpi_value"]["requestable"] is True
    # co2_total_t is a pre-aggregated summary — not a primary target
    assert m["kpi_fact_measures"]["co2_total_t"]["requestable"] is False
    # Derived emissions: both orderable
    assert m["derived_measures"]["scope2_location_t"]["requestable"] is True
    assert m["derived_measures"]["scope2_market_proxy_t"]["requestable"] is True


# ── Grain decision locked to quarter/year/multi_year ──────────────────────────

def test_time_grain_is_quarterly_only(catalog):
    """Week and month grains must be absent — quarterly source data, no splitting."""
    time_grain = catalog["allowed_grains"]["time_grain"]
    assert set(time_grain) == {"quarter", "year", "multi_year"}, (
        f"Expected only quarter/year/multi_year, got: {time_grain}"
    )
    assert "week" not in time_grain
    assert "month" not in time_grain

    grain_defs = catalog["grain_definitions"]["time"]
    assert set(grain_defs.keys()) == {"quarter", "year", "multi_year"}


def test_entity_grain_unchanged(catalog):
    """Entity grain must include all expected levels."""
    assert set(catalog["allowed_grains"]["entity_grain"]) >= {
        "site", "country", "region", "bu_rc", "portfolio"
    }


# ── Derived/virtual filters clearly marked ────────────────────────────────────

def test_size_tier_and_emea_marked_derived(catalog):
    """size_tier and emea must be virtual (no real source column)."""
    filters = catalog["selectable_filters"]["entity_filters"]
    for name in ("size_tier", "emea"):
        assert name in filters, f"Filter '{name}' missing from entity_filters"
        entry = filters[name]
        assert isinstance(entry, dict), f"'{name}' should be a dict"
        assert entry.get("derived") is True, f"'{name}' must be marked derived=True"
        assert entry.get("source_column") is None, f"'{name}' must have source_column=None"


# ── Real dimension filter values present ──────────────────────────────────────

def test_real_country_filters_populated(catalog):
    """Regions table must populate country_code and cdp_region filters."""
    ef = catalog["selectable_filters"]["entity_filters"]
    # country_code: list of ISO2 strings
    assert isinstance(ef["country_code"], list)
    assert len(ef["country_code"]) > 10
    assert "DE" in ef["country_code"]
    # cdp_region: known CDP labels
    assert isinstance(ef["cdp_region"], list)
    assert len(ef["cdp_region"]) >= 3


def test_real_bu_rc_filters_populated(catalog):
    """BU/RC groups table must populate bu_rc and bu_rc_group."""
    ef = catalog["selectable_filters"]["entity_filters"]
    assert isinstance(ef["bu_rc"], list)
    assert len(ef["bu_rc"]) > 5
    assert isinstance(ef["bu_rc_group"], list)
    assert len(ef["bu_rc_group"]) > 3


def test_synthetic_location_filters_populated(catalog):
    """The shipped synthetic locations must populate both location filters."""
    ef = catalog["selectable_filters"]["entity_filters"]
    assert isinstance(ef["location_id"], list)
    assert len(ef["location_id"]) == 80
    assert len(ef["location_name"]) == 80


# ── Legacy alias keys present for normalizer compat ───────────────────────────

def test_legacy_alias_keys_present(catalog):
    """iso2_code, region_name, bu_rc_code must be present for backward compat."""
    ef = catalog["selectable_filters"]["entity_filters"]
    for alias in ("iso2_code", "region_name", "bu_rc_code"):
        assert alias in ef, f"Legacy alias '{alias}' must be present for normalizer compat"


def test_available_date_range_clamp_keys(catalog):
    """_clamp_time_window in d1_data_request_planner reads these exact keys (renamed Batch 3)."""
    adr = catalog["available_date_range"]
    assert "min_period_date" in adr
    assert "max_period_date" in adr


# ── Structured relationships — non-deferred endpoints in field_index ───────────

def test_non_deferred_relationships_reference_existing_columns(catalog):
    """Non-deferred relationship endpoints must exist in field_index."""
    available = set(catalog["field_index"])
    live_rels = [
        r for r in catalog["structured_relationships"]
        if not r.get("deferred", False)
    ]
    assert len(live_rels) >= 2, "At least the 2 dim↔dim joins should be live"
    for rel in live_rels:
        assert rel["from"] in available, (
            f"Relationship '{rel['relationship_id']}' from='{rel['from']}' not in field_index"
        )
        assert rel["to"] in available, (
            f"Relationship '{rel['relationship_id']}' to='{rel['to']}' not in field_index"
        )


# ── CLI catalog target surface preserved ──────────────────────────────────────

def test_v02_catalog_preserves_cli_catalog_target_surface(catalog, legacy):
    """_all_catalog_targets must return the same set from v0.2 and legacy views."""
    assert _all_catalog_targets(catalog) == _all_catalog_targets(legacy)


# ── Schema signature ignores profile counts, not schema ───────────────────────

def test_schema_signature_ignores_profile_counts_and_filter_values(catalog):
    """Signature must be stable under row_count/null_count/filter-value changes."""
    builder = DataCatalogBuilder()
    original_sig = catalog["schema_signature"]

    # Change row_count + null_count + a filter value — signature must not change.
    changed_values = deepcopy(catalog)
    first_table = next(iter(changed_values["source_tables"]))
    changed_values["source_tables"][first_table]["row_count"] += 100
    changed_values["source_tables"][first_table]["columns"][0]["null_count"] += 10
    first_ef_key = next(
        k for k, v in changed_values["selectable_filters"]["entity_filters"].items()
        if isinstance(v, list)
    )
    changed_values["selectable_filters"]["entity_filters"][first_ef_key] = ["XX"]
    assert builder._schema_signature(changed_values) == original_sig

    # Rename a column — signature must change.
    changed_schema = deepcopy(catalog)
    changed_schema["source_tables"][first_table]["columns"][0]["name"] = "renamed_col"
    assert builder._schema_signature(changed_schema) != original_sig


# ── Source tables have expected real columns ───────────────────────────────────

def test_source_tables_real_columns(catalog):
    """Spot-check that real column names from the source tables are present."""
    tables = catalog["source_tables"]
    assert "regions" in tables
    assert "bu_rc_groups" in tables
    assert "locations" in tables

    reg_cols = {c["name"] for c in tables["regions"]["columns"]}
    assert {"country_code", "cdp_region", "country_name"} <= reg_cols

    loc_cols = {c["name"] for c in tables["locations"]["columns"]}
    assert {"location_id", "location_name", "country", "bu_rc_name"} <= loc_cols


# ── Batch 2 stubs (xfail — fact tables not loaded yet) ────────────────────────

def test_batch2a_fact_measures_wired():
    """Batch 2a/2b: energy-table measures are live (not deferred)."""
    catalog = DataCatalogBuilder().build()
    md = catalog["measure_definitions"]
    for name in ("energy_mwh", "co2_t", "amount_consumed_mwh", "co2e_t", "renewable_energy"):
        assert not md[name].get("deferred"), f"Measure '{name}' should be live in Batch 2a"
    assert "dist_ie_energy_raw.amount_consumed_mwh" in md["amount_consumed_mwh"]["requires"]
    # KPI domain is now live (Batch 3: derived sums over energy/emissions tables).
    assert not md["value"].get("deferred"), "value (kpi) must be live in Batch 3"


def test_batch2a_available_date_range_from_fiscal_data():
    """Batch 2a: available_date_range is derived from real fiscal year+quarter data."""
    catalog = DataCatalogBuilder().build()
    adr = catalog["available_date_range"]
    assert not adr.get("placeholder"), "Date range should be real in Batch 2a"
    assert adr["min_period_date"] < adr["max_period_date"]
    # FY2022 Q1 = Nov 15, 2021 (earliest); max grows as new fiscal years are added.
    assert adr["min_period_date"].startswith("2021")
    assert adr["max_period_date"] > "2025-01-01"  # at least FY2025 Q4 or beyond
    assert "available_fiscal_years" in adr
    assert set(adr["available_fiscal_years"]) >= {2022, 2023, 2024, 2025, 2026}


def test_batch2a_media_type_and_energy_classification_populated(catalog):
    """Batch 2a: energy_filters populated from energy fact table."""
    ef = catalog["selectable_filters"]["energy_filters"]
    assert isinstance(ef["media_type"], list)
    assert len(ef["media_type"]) >= 10
    assert "Electricity" in ef["media_type"]
    assert "Natural Gas" in ef["media_type"]
    assert isinstance(ef["energy_classification"], list)
    assert {"Primary Energy", "Secondary Energy"} <= set(ef["energy_classification"])


def test_batch2a_grain_definitions_not_deferred(catalog):
    """Batch 2a: quarter and year grain definitions should be active."""
    gd = catalog["grain_definitions"]["time"]
    assert not gd["quarter"].get("deferred"), "quarter grain should be live in Batch 2a"
    assert not gd["year"].get("deferred"), "year grain should be live in Batch 2a"
    assert "time_period_format" in gd["quarter"]
    assert "time_period_format" in gd["year"]


def test_batch2a_energy_source_not_deferred(catalog):
    """Batch 2a: energy source domain should be active."""
    domains = catalog["source_domains"]
    assert not domains["energy"].get("deferred"), "energy domain should be live in Batch 2a"
    assert not domains["kpi"].get("deferred"), "kpi domain is live in Batch 3 (derived sums)"


def test_batch2a_energy_relationships_not_deferred(catalog):
    """Batch 2a: energy↔region and energy↔bu_rc joins are live."""
    live_rels = {
        r["relationship_id"]
        for r in catalog["structured_relationships"]
        if not r.get("deferred", False)
    }
    assert "energy_region" in live_rels
    assert "energy_bu_rc" in live_rels


# ── Batch 2b tests ────────────────────────────────────────────────────────────

def test_batch2b_scope_filter_populated(catalog):
    """Batch 2b: scope filter is populated from the emissions table."""
    ef = catalog["selectable_filters"]["energy_filters"]
    scope = ef["scope"]
    assert isinstance(scope, list), "scope must be a plain list (not a deferred stub)"
    assert set(scope) == {"Scope 1", "Scope 2", "Other"}, (
        f"Expected exactly {{Scope 1, Scope 2, Other}}, got {set(scope)}"
    )


def test_batch2b_emissions_measures_not_deferred(catalog):
    """Batch 2b: emissions-related measures are live (not deferred)."""
    md = catalog["measure_definitions"]
    for name in ("emissions_t", "scope2_location_t", "scope2_market_proxy_t", "share_renewable"):
        assert not md[name].get("deferred"), f"Measure '{name}' should be live in Batch 2b"
    # KPI domain is now live (Batch 3: derived sums over real tables).
    assert not md["value"].get("deferred"), "value (kpi) must be live in Batch 3"


def test_batch2b_energy_emissions_relationship_live(catalog):
    """Batch 2b: energy↔emissions join is now live."""
    rels_by_id = {r["relationship_id"]: r for r in catalog["structured_relationships"]}
    assert "energy_emissions" in rels_by_id
    assert not rels_by_id["energy_emissions"].get("deferred", True), (
        "energy_emissions relationship must be live in Batch 2b"
    )
    # energy_location and energy_overview still deferred.
    assert rels_by_id["energy_location"].get("deferred"), "energy_location still deferred"
    assert rels_by_id["energy_overview"].get("deferred"), "energy_overview still deferred"


def test_batch2b_emissions_table_in_source_tables(catalog):
    """Batch 2b: dist_ie_energy_emissions_raw is in source_tables and field_index."""
    assert "dist_ie_energy_emissions_raw" in catalog["source_tables"]
    em_cols = {c["name"] for c in catalog["source_tables"]["dist_ie_energy_emissions_raw"]["columns"]}
    assert {"fiscal_year", "fiscal_quarter", "scope", "co2e_t", "emissions_t"} <= em_cols
    assert "dist_ie_energy_emissions_raw.scope" in catalog["field_index"]
    assert "dist_ie_energy_emissions_raw.co2e_t" in catalog["field_index"]


def test_batch2b_size_tier_and_emea_values_populated(catalog):
    """Batch 2b: size_tier and emea retain derived=True but now have populated values."""
    ef = catalog["selectable_filters"]["entity_filters"]
    # size_tier
    st = ef["size_tier"]
    assert st.get("derived") is True, "size_tier must still be marked derived"
    assert set(st.get("values", [])) == {"large", "medium", "small"}
    # emea
    emea = ef["emea"]
    assert emea.get("derived") is True, "emea must still be marked derived"
    assert set(emea.get("values", [])) == {True, False}


def test_batch2b_emissions_source_domain_live(catalog):
    """Batch 2b: emissions source domain is live."""
    domains = catalog["source_domains"]
    assert "emissions" in domains, "emissions domain must exist"
    assert not domains["emissions"].get("deferred"), "emissions domain must be live"
    # kpi is now live (Batch 3)
    assert not domains["kpi"].get("deferred")


# ── expand_region_bucket ───────────────────────────────────────────────────────

def test_expand_region_bucket_emea_upper():
    """'EMEA' (upper-case) expands to the derived emea filter."""
    assert expand_region_bucket("EMEA") == {"emea": [True]}


def test_expand_region_bucket_emea_lower():
    """'emea' (lower-case) also expands — matching is case-insensitive."""
    assert expand_region_bucket("emea") == {"emea": [True]}


def test_expand_region_bucket_literal_region_returns_none():
    """A literal cdp_region value ('Europe') is not a bucket — returns None."""
    assert expand_region_bucket("Europe") is None


def test_expand_region_bucket_non_string_returns_none():
    """Non-string inputs (None, list, etc.) return None without error."""
    assert expand_region_bucket(None) is None
    assert expand_region_bucket(42) is None
    assert expand_region_bucket(["EMEA"]) is None
