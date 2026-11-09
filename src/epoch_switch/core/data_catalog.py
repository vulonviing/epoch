"""Shared runtime schema catalog — single source of truth for DA1, D1, and D2.

This is cross-cutting runtime infrastructure rather than agent-owned business
logic.  DA1, D1, D2, and the CLI import it through ``epoch_switch.core``.

Schema: quarterly grain (Q1/Q2/Q3/Q4 + annual Y).
Batch 1: dimension tables only (regions, bu_rc_groups, dist_ie_locations).
Batch 2a: energy fact table; measures/grain/relationships un-deferred.
Batch 2b: emissions fact table; scope filter, scope2/emissions measures,
  size_tier/emea derived dimensions materialized.
Batch 2c (deferred): D2 quality engine rewrite, kpi domain fate.

Grain decision (locked): quarter / year / multi_year.
  - week and month are dropped — no sub-quarterly source data exists.
  - UC1 "monthly ETS tracking" becomes quarterly; documented limitation.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

from epoch_switch import config


# ---------------------------------------------------------------------------
# Composite region bucket expansion
# ---------------------------------------------------------------------------
# Registry site_filter values like "EMEA" are human-readable bucket labels,
# not literal cdp_region values.  The catalog owns this mapping because it
# owns the derived-filter definitions (see selectable_filters.entity_filters).
#
# Each entry maps an upper-cased bucket label to the derived filter dict that
# the executor materialises in-pipeline.  Add future buckets (e.g. "APAC")
# here alongside their catalog-declared derived filter.
COMPOSITE_REGION_BUCKETS: dict[str, dict[str, list]] = {
    # Europe + Africa + Middle East (AE/SA/TR).  The executor produces a
    # boolean `emea` column via _add_derived_dimensions(); _apply_filters()
    # then applies {"emea": [True]} generically.
    "EMEA": {"emea": [True]},
}


def expand_region_bucket(region_value: object) -> dict[str, list] | None:
    """Return the derived-filter dict for a composite region bucket label.

    Examples:
        expand_region_bucket("EMEA")  -> {"emea": [True]}
        expand_region_bucket("emea")  -> {"emea": [True]}   # case-insensitive
        expand_region_bucket("Europe") -> None               # literal cdp_region
        expand_region_bucket(None)     -> None
    """
    if isinstance(region_value, str):
        return COMPOSITE_REGION_BUCKETS.get(region_value.upper())
    return None


LEGACY_CATALOG_KEYS = (
    "catalog_version",
    "minimum_source_grain",
    "available_date_range",
    "source_tables",
    "selectable_filters",
    "measures",
    "allowed_grains",
    "quality_policies",
    "column_quality_policies",
    "relationships",
    # measure_definitions/source_domains give DA1 the table-qualified
    # `requires` columns, `source_domain`, and semantic `note` needed to
    # disambiguate same-shaped measures (e.g. amount_consumed_mwh from
    # dist_ie_energy_raw vs. scope2_market_proxy_t from
    # dist_ie_energy_emissions_raw). The bare "measures" entries alone
    # (unit/default_aggregation/requestable) carry none of that.
    "measure_definitions",
    "source_domains",
)


def legacy_catalog_view(catalog: dict[str, Any]) -> dict[str, Any]:
    """Return the exact v0.1 shape consumed by existing planners and CLI code."""
    view = {
        key: deepcopy(catalog[key])
        for key in LEGACY_CATALOG_KEYS
        if key in catalog
    }
    view["catalog_version"] = "0.1"
    return view


class DataCatalogBuilder:
    """Build a column/value catalog from the source data files.

    Batch 1 loads only the three dimension tables:
      regions.csv, bu_rc_groups.csv, dist_ie_locations.csv.

    Fact tables (energy, emissions, overview) and the full measure/grain/
    quality definitions are deferred to Batch 2.  Items marked "deferred"
    in the output are real schema entries that will be populated in Batch 2
    — they are emitted now so planners and executors can see the intended
    schema without breaking import/validation contracts.
    """

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or config.DATA_DIR

    # ── Public entry point ────────────────────────────────────────────────────

    def build(self) -> dict[str, Any]:
        tables = self._load_tables()
        source_tables = self._source_tables(tables)
        selectable_filters = self._selectable_filters(tables)
        measures = self._measures()
        source_domains = self._source_domains()
        relationships = self._structured_relationships()
        measure_definitions = self._measure_definitions()
        filter_definitions = self._filter_definitions(selectable_filters)
        grain_definitions = self._grain_definitions()
        quality_checks = self._quality_check_definitions()
        executor_capabilities = self._executor_capabilities(measures)

        catalog: dict[str, Any] = {
            "catalog_version": "0.2",
            # Grain locked to quarterly (source data grain).
            "minimum_source_grain": {
                "entity": "site",
                "time": "quarter",
                "weekly_supported": False,
                "monthly_supported": False,
                "note": (
                    "Source data is quarterly (fiscal quarters Q1-Q4). "
                    "Weekly and monthly grains are not available."
                ),
            },
            "available_date_range": self._available_date_range(tables),
            "source_tables": source_tables,
            "selectable_filters": selectable_filters,
            "measures": measures,
            "allowed_grains": {
                "entity_grain": ["site", "country", "region", "bu_rc", "portfolio"],
                "time_grain": ["quarter", "year", "multi_year"],  # week/month dropped
            },
            "quality_policies": [
                "include_all",
                "exclude_inconsistent",
                "exclude_missing_and_inconsistent",
                "report_flags_only",
            ],
            "column_quality_policies": self._column_quality_policies(),
            "relationships": {
                "region": "regions.country_code <- locations.country (ISO2)",
                "bu_rc": "bu_rc_groups.bu_rc <- locations.bu_rc_name",
                "site": "locations.location_id <- energy.location_id (Batch 2)",
                "report": "overview.report_id = energy.report_id = emissions.report_id (Batch 2)",
            },
            "source_domains": source_domains,
            "structured_relationships": relationships,
            "field_index": self._field_index(source_tables, source_domains),
            "measure_definitions": measure_definitions,
            "filter_definitions": filter_definitions,
            "grain_definitions": grain_definitions,
            "quality_check_definitions": quality_checks,
            "executor_capabilities": executor_capabilities,
        }
        catalog["schema_signature"] = self._schema_signature(catalog)
        return catalog

    # ── Table loading ─────────────────────────────────────────────────────────

    # Representative mid-month dates per Siemens fiscal quarter (Oct–Sep FY).
    # Used to derive real available_date_range bounds from fiscal_year+fiscal_quarter.
    _QUARTER_MONTH_DAY: dict[str, tuple[int, int]] = {
        "Q1": (-1, 11, 15),   # Oct–Dec of FY-1 → Nov 15 of FY-1
        "Q2": (0, 2, 15),     # Jan–Mar of FY   → Feb 15 of FY
        "Q3": (0, 5, 15),     # Apr–Jun of FY   → May 15 of FY
        "Q4": (0, 8, 15),     # Jul–Sep of FY   → Aug 15 of FY
    }

    def _load_tables(self) -> dict[str, pd.DataFrame]:
        """Batch 2b: dimension tables + energy + emissions fact tables.
        Overview is deferred to Batch 2c.
        """
        files = {
            "regions": "regions.csv",
            "bu_rc_groups": "bu_rc_groups.csv",
            "locations": "dist_ie_locations.csv",
            "dist_ie_energy_raw": "dist_ie_energy_raw.csv",
            "dist_ie_energy_emissions_raw": "dist_ie_energy_emissions_raw.csv",
        }
        return {name: pd.read_csv(self.data_dir / fname) for name, fname in files.items()}

    def _fiscal_period_date(self, fiscal_year: int, fiscal_quarter: str) -> str:
        """Return a deterministic representative ISO date for a fiscal year+quarter."""
        mapping = self._QUARTER_MONTH_DAY.get(fiscal_quarter)
        if mapping is None:
            return f"{fiscal_year}-06-15"
        fy_offset, month, day = mapping
        year = fiscal_year + fy_offset
        return f"{year}-{month:02d}-{day:02d}"

    # ── Source tables ─────────────────────────────────────────────────────────

    def _source_tables(self, tables: dict[str, pd.DataFrame]) -> dict[str, Any]:
        return {
            name: {
                "row_count": int(len(df)),
                "columns": [
                    {
                        "name": col,
                        "dtype": str(df[col].dtype),
                        "null_count": int(df[col].isna().sum()),
                        "unique_count": int(df[col].nunique(dropna=True)),
                    }
                    for col in df.columns
                ],
            }
            for name, df in tables.items()
        }

    # ── Available date range ──────────────────────────────────────────────────

    def _available_date_range(self, tables: dict[str, pd.DataFrame]) -> dict[str, Any]:
        """Compute real bounds from energy fact fiscal_year + fiscal_quarter."""
        energy = tables.get("dist_ie_energy_raw")
        if energy is None or "fiscal_year" not in energy.columns:
            return {
                "min_period_date": "2019-01-01",
                "max_period_date": "2026-12-31",
                "placeholder": True,
            }
        dates = [
            self._fiscal_period_date(int(row.fiscal_year), str(row.fiscal_quarter))
            for row in energy[["fiscal_year", "fiscal_quarter"]].drop_duplicates().itertuples(index=False)
            if pd.notna(row.fiscal_year) and pd.notna(row.fiscal_quarter)
        ]
        if not dates:
            return {"min_period_date": "2019-01-01", "max_period_date": "2026-12-31", "placeholder": True}
        fiscal_years = sorted(energy["fiscal_year"].dropna().unique().astype(int).tolist())
        fiscal_quarters = sorted(energy["fiscal_quarter"].dropna().unique().tolist())
        return {
            "min_period_date": min(dates),
            "max_period_date": max(dates),
            "available_fiscal_years": fiscal_years,
            "available_fiscal_quarters": fiscal_quarters,
        }

    # ── Selectable filters ────────────────────────────────────────────────────

    def _selectable_filters(self, tables: dict[str, pd.DataFrame]) -> dict[str, Any]:
        regions = tables["regions"]
        bu_rc_groups = tables["bu_rc_groups"]
        locations = tables["locations"]
        emissions = tables["dist_ie_energy_emissions_raw"]

        entity_filters: dict[str, Any] = {
            # From regions.csv
            "country_code": self._values(regions, "country_code"),
            "country_name": self._values(regions, "country_name"),
            "cdp_region": self._values(regions, "cdp_region"),
            # Legacy alias expected by normalizers (iso2_code -> country_code)
            "iso2_code": {
                "values": self._values(regions, "country_code"),
                "legacy_alias_of": "country_code",
            },
            # Legacy alias (region_name -> cdp_region)
            "region_name": {
                "values": self._values(regions, "cdp_region"),
                "legacy_alias_of": "cdp_region",
            },
            # From bu_rc_groups.csv
            "bu_rc": self._values(bu_rc_groups, "bu_rc"),
            "bu_rc_group": self._values(bu_rc_groups, "bu_rc_group"),
            # From locations.csv
            "bu_rc_name": self._values(locations, "bu_rc_name"),
            "bu_rc_id": self._values(locations, "bu_rc_id"),
            # Legacy alias (bu_rc_code -> bu_rc_id)
            "bu_rc_code": {
                "values": self._values(locations, "bu_rc_id"),
                "legacy_alias_of": "bu_rc_id",
            },
            "location_id": self._values(locations, "location_id"),
            "location_name": self._values(locations, "location_name"),
            "location_country": self._values(locations, "country"),
            "location_city": self._values(locations, "city"),
            # Derived/virtual: EMEA region grouping.
            # Materialised in Batch 2b: cdp_region in {Europe,Africa} OR country in {AE,SA,TR}.
            # AE/SA/TR are the only Middle-East countries present in the source data;
            # they sit under cdp_region "Asia Australia" so an explicit list is required.
            "emea": {
                "derived": True,
                "source_column": None,
                "derivation": (
                    "cdp_region in {Europe, Africa} OR country in {AE, SA, TR}. "
                    "AE/SA/TR are bucketed under cdp_region=Asia Australia."
                ),
                "values": [True, False],
            },
            # Derived/virtual: size_tier.
            # Materialised in Batch 2b: quantile of avg annual co2e_t per location_id.
            # >p75 → large, p25–p75 → medium, <p25 → small. A fixed absolute-tonnage
            # threshold was rejected in favor of this quantile approach.
            "size_tier": {
                "derived": True,
                "source_column": None,
                "derivation": (
                    "Quantile of avg annual co2e_t per location_id over all loaded "
                    "fiscal years: >p75 → large, p25–p75 → medium, <p25 → small."
                ),
                "values": ["large", "medium", "small"],
            },
        }

        return {
            "entity_filters": entity_filters,
            # Time filters: derived from energy fact table so new fiscal years appear
            # automatically without code changes.
            "time_filters": {
                "fiscal_year": sorted(
                    int(v)
                    for v in tables["dist_ie_energy_raw"]["fiscal_year"]
                    .dropna()
                    .unique()
                    if str(v).isdigit()
                ),
                "fiscal_quarter": self._values(
                    tables["dist_ie_energy_raw"], "fiscal_quarter"
                ),
                # Legacy key names read by _clamp_time_window — kept as stubs.
                "iso_year": {"deferred": True, "note": "Batch 2: from fiscal_year"},
                "iso_week": {"deferred": True, "note": "Batch 2: not available (quarterly grain)"},
            },
            # Energy filters: populated from energy fact table.
            "energy_filters": {
                "media_type": self._values(tables["dist_ie_energy_raw"], "media_type"),
                "energy_classification": self._values(tables["dist_ie_energy_raw"], "energy_classification"),
                # scope: from emissions table — Batch 2b: now live.
                "scope": self._values(emissions, "scope"),
                # Legacy aliases
                "media_name": {
                    "values": self._values(tables["dist_ie_energy_raw"], "media_type"),
                    "legacy_alias_of": "media_type",
                },
                "energy_class": {
                    "values": self._values(tables["dist_ie_energy_raw"], "energy_classification"),
                    "legacy_alias_of": "energy_classification",
                },
            },
            # Quality filters: deferred (all status=Approved in source; no is_missing/is_late).
            "quality_filters": {
                "is_missing": {"deferred": True, "note": "Batch 2: not in source data"},
                "is_late": {"deferred": True, "note": "Batch 2: not in source data"},
                "data_quality_flag": {"deferred": True, "note": "Batch 2"},
            },
        }

    # ── Measures ──────────────────────────────────────────────────────────────

    def _measures(self) -> dict[str, Any]:
        """Batch 2b: energy and emissions measures live; kpi still deferred.

        Each entry carries ``requestable: bool`` so DA1 knows which measures it
        may name as a catalog target vs. which are internal ingredients/derived
        ratios that cannot be directly ordered.  The flag is exposed to DA1 via
        ``legacy_catalog_view`` (which deep-copies the ``measures`` key).
        """
        return {
            "energy_fact_measures": {
                # Legacy aliases — executor emits these names.
                "energy_mwh": {
                    "unit": "MWh",
                    "default_aggregation": "sum",
                    "requestable": True,
                    "note": (
                        "Sum of amount_consumed_mwh across all energy_classification values. "
                        "Primary and Secondary Energy are disjoint by media_type — no double-count. "
                        "D2 enforces this partition as a critical invariant at runtime. "
                        "Equals EnEfG Endenergie for this dataset; confirm carrier scope per regulation."
                    ),
                },
                "co2_t": {"unit": "tCO2e", "default_aggregation": "sum", "requestable": True},
                # Real column names from dist_ie_energy_raw.
                "amount_consumed_mwh": {"unit": "MWh", "default_aggregation": "sum", "requestable": True},
                "co2e_t": {"unit": "tCO2e", "default_aggregation": "sum", "requestable": True},
                "renewable_energy": {"unit": "MWh", "default_aggregation": "sum", "requestable": True},
                # Batch 2b: now live.  share_renewable is a derived ratio —
                # not directly requestable (ingredient-only).
                "share_renewable": {"unit": "fraction", "default_aggregation": "weighted", "requestable": False},
                "emissions_t": {
                    "unit": "t", "default_aggregation": "sum",
                    "note": "From dist_ie_energy_emissions_raw",
                    "requestable": True,
                },
            },
            # KPI domain: derived sums over the fact tables.
            # value = Σ amount_consumed_mwh (energy_consumption_total, by media_type/location).
            # co2_total_t = Σ co2e_t (from emissions table, by scope/location).
            "kpi_fact_measures": {
                # value / kpi_value: orderable via kind="measure", name="value",
                # filtered by kpi_name (see DA1.md KPI rule).
                "value": {
                    "unit": "MWh", "default_aggregation": "sum",
                    "note": "energy_consumption_total: Σ amount_consumed_mwh, breakdown by media_type/location",
                    "requestable": True,
                },
                "kpi_value": {
                    "unit": "MWh", "default_aggregation": "sum",
                    "note": "alias for value (energy_consumption_total)",
                    "requestable": True,
                },
                # co2_total_t is a pre-aggregated summary — not a primary DA1 target.
                "co2_total_t": {
                    "unit": "tCO2e", "default_aggregation": "sum",
                    "note": "Σ co2e_t from emissions table, breakdown by scope/location",
                    "requestable": False,
                },
            },
            # Derived measures from emissions scope join — Batch 2b: now live.
            # Note: co2_factor appears only inside scope2_market_proxy_t.requires —
            # it is a raw conversion-rate column, NOT an orderable measure itself.
            "derived_measures": {
                "scope2_location_t": {
                    "formula": "sum(co2e_t where scope='Scope 2')",
                    "requires": ["co2e_t", "scope"],
                    "requestable": True,
                },
                "scope2_market_proxy_t": {
                    "formula": "sum(amount_consumed_mwh * co2_factor where scope='Scope 2')",
                    "requires": ["amount_consumed_mwh", "co2_factor"],
                    "requestable": True,
                },
            },
        }

    # ── Column quality policies ───────────────────────────────────────────────

    def _column_quality_policies(self) -> dict[str, Any]:
        return {
            "energy.amount_consumed_mwh": {
                "nullable": True,
                "zero_policy": "allowed_but_review_when_co2_positive",
                "valid_range": [0, None],
                "deferred": True,
            },
            "emissions.co2e_t": {
                "nullable": True,
                "zero_policy": "allowed_when_scope2_grid_factor_missing",
                "valid_range": [0, None],
                "deferred": True,
            },
        }

    # ── Source domains ────────────────────────────────────────────────────────

    def _source_domains(self) -> dict[str, Any]:
        """Keep energy/kpi/energy_and_kpi domain names alive (hardcoded in planners/CLI)."""
        dim_tables = ["locations", "regions", "bu_rc_groups"]
        return {
            "energy": {
                "fact_table": "dist_ie_energy_raw",
                "emissions_table": "dist_ie_energy_emissions_raw",
                "time_reference_table": "dist_ie_overview",
                "dimension_tables": dim_tables,
                "note": "Batch 2b: energy + emissions fact tables loaded; overview = Batch 2c.",
            },
            "emissions": {
                "fact_table": "dist_ie_energy_emissions_raw",
                "dimension_tables": dim_tables,
                "note": "Batch 2b: scope-labelled co2e_t and emissions_t.",
            },
            "kpi": {
                "fact_table": "dist_ie_energy_raw",
                "emissions_table": "dist_ie_energy_emissions_raw",
                "dimension_tables": dim_tables,
                "note": (
                    "Batch 3: KPI = derived sums. value/kpi_value = Σ amount_consumed_mwh "
                    "(breakdown by media_type/location); co2_total_t = Σ co2e_t from emissions."
                ),
            },
            "energy_and_kpi": {
                "composed_of": ["energy", "kpi"],
                "note": "Batch 3: both domains live; energy + kpi views over the same fact tables.",
            },
        }

    # ── Structured relationships ──────────────────────────────────────────────

    def _structured_relationships(self) -> list[dict[str, Any]]:
        """Emit dim↔dim joins as live; fact-anchored joins as deferred."""
        live = [
            # Dim↔dim joins — both endpoints loaded in Batch 1.
            {
                "relationship_id": "location_region",
                "from": "locations.country",
                "to": "regions.country_code",
                "cardinality": "many_to_one",
                "join_type": "left",
                "deferred": False,
            },
            {
                "relationship_id": "location_bu_rc",
                "from": "locations.bu_rc_name",
                "to": "bu_rc_groups.bu_rc",
                "cardinality": "many_to_one",
                "join_type": "left",
                "deferred": False,
            },
        ]
        # Batch 2a: energy-anchored dim joins are now live (energy table loaded).
        energy_live = [
            {
                "relationship_id": "energy_region",
                "from": "dist_ie_energy_raw.country",
                "to": "regions.country_code",
                "cardinality": "many_to_one",
                "join_type": "left",
                "deferred": False,
            },
            {
                "relationship_id": "energy_bu_rc",
                "from": "dist_ie_energy_raw.bu_rc_name",
                "to": "bu_rc_groups.bu_rc",
                "cardinality": "many_to_one",
                "join_type": "left",
                "deferred": False,
            },
        ]
        # Batch 2b: emissions and overview joins.
        deferred = [
            {
                "relationship_id": "energy_location",
                "from": "dist_ie_energy_raw.location_id",
                "to": "locations.location_id",
                "cardinality": "many_to_one",
                "join_type": "left",
                "deferred": True,
                "note": "Batch 2b: location dim join for geocoordinates/address",
            },
            {
                "relationship_id": "energy_overview",
                "from": "dist_ie_energy_raw.report_id",
                "to": "dist_ie_overview.report_id",
                "cardinality": "many_to_one",
                "join_type": "left",
                "deferred": True,
                "note": "Batch 2b: needed for approval_date precision",
            },
            {
                "relationship_id": "energy_emissions",
                "from": "dist_ie_energy_raw.report_id",
                "to": "dist_ie_energy_emissions_raw.report_id",
                "cardinality": "many_to_one",
                "join_type": "left",
                "deferred": False,
                "note": "Batch 2b: scope-labelled co2e_t; 100% report_id coverage.",
            },
        ]
        return live + energy_live + deferred

    # ── Field index ───────────────────────────────────────────────────────────

    def _field_index(
        self,
        source_tables: dict[str, Any],
        source_domains: dict[str, Any],
    ) -> dict[str, Any]:
        table_domains: dict[str, list[str]] = {}
        for domain, definition in source_domains.items():
            if definition.get("deferred"):
                continue
            if domain == "energy_and_kpi":
                continue
            dim_tables = definition.get("dimension_tables", [])
            for table in dim_tables:
                table_domains.setdefault(table, []).append(domain)

        measure_columns = {"amount_consumed_mwh", "co2e_t", "emissions_t", "renewable_energy",
                           "share_renewable", "co2_factor"}
        time_columns = {"fiscal_year", "fiscal_quarter", "approval_date",
                        "creation_date", "edition_date"}
        id_suffixes = ("_id",)

        index: dict[str, Any] = {}
        for table, metadata in source_tables.items():
            for column in metadata["columns"]:
                name = column["name"]
                role = (
                    "measure" if name in measure_columns
                    else "time" if name in time_columns
                    else "identifier" if any(name.endswith(s) for s in id_suffixes)
                    else "dimension"
                )
                qualified = f"{table}.{name}"
                index[qualified] = {
                    "table": table,
                    "column": name,
                    "dtype": column["dtype"],
                    "nullable": column["null_count"] > 0,
                    "role": role,
                    "source_domains": sorted(table_domains.get(table, [])),
                }
        return index

    # ── Measure definitions ───────────────────────────────────────────────────

    def _measure_definitions(self) -> dict[str, Any]:
        """Batch 2b: energy + emissions measures live; kpi still deferred."""
        def _m(domain, requires, agg, requestable, *, deferred=False, note=""):
            return {
                "source_domain": domain,
                "requires": requires,
                "aggregation": agg,
                "requestable": requestable,
                "deferred": deferred,
                "note": note,
            }

        return {
            # Batch 2a: energy-table measures are live.
            "energy_mwh": _m("energy", ["dist_ie_energy_raw.amount_consumed_mwh"], "sum", True,
                              note="Legacy alias for amount_consumed_mwh"),
            "co2_t": _m("energy", ["dist_ie_energy_raw.co2e_t"], "sum", True,
                        note="Legacy alias for co2e_t (total, not scope-labelled)"),
            "amount_consumed_mwh": _m("energy", ["dist_ie_energy_raw.amount_consumed_mwh"],
                                      "sum", True),
            "co2e_t": _m("energy", ["dist_ie_energy_raw.co2e_t"], "sum", True),
            "renewable_energy": _m("energy", ["dist_ie_energy_raw.renewable_energy"],
                                   "sum", True),
            # Batch 2b: emissions-table measures now live.
            "emissions_t": _m("emissions", ["dist_ie_energy_emissions_raw.emissions_t"],
                              "sum", True, note="Scope-labelled total emissions"),
            "share_renewable": _m("energy", ["dist_ie_energy_raw.renewable_energy",
                                             "dist_ie_energy_raw.amount_consumed_mwh"],
                                  "weighted", False,
                                  note="renewable_energy / amount_consumed_mwh"),
            "scope2_location_t": _m("emissions",
                                    ["dist_ie_energy_emissions_raw.co2e_t",
                                     "dist_ie_energy_emissions_raw.scope"],
                                    "sum", True,
                                    note="sum co2e_t where scope='Scope 2'"),
            "scope2_market_proxy_t": _m("emissions",
                                        ["dist_ie_energy_emissions_raw.amount_consumed_mwh",
                                         "dist_ie_energy_emissions_raw.co2_factor"],
                                        "sum", True,
                                        note="sum amount_consumed_mwh * co2_factor where scope='Scope 2'"),
            # KPI domain: Batch 3 — derived sums, now live.
            # value/kpi_value are requestable (DA1 KPI rule: kind="measure", name="value" + kpi_name filter).
            "value": _m("kpi", ["dist_ie_energy_raw.amount_consumed_mwh"], "sum", True,
                        note="energy_consumption_total: Σ amount_consumed_mwh, by media_type/location"),
            "kpi_value": _m("kpi", ["dist_ie_energy_raw.amount_consumed_mwh"], "sum", True,
                            note="alias for value (energy_consumption_total)"),
            # co2_total_t is a pre-aggregated summary — not a primary DA1 target.
            "co2_total_t": _m("kpi", ["dist_ie_energy_emissions_raw.co2e_t"], "sum", False,
                              note="Σ co2e_t from emissions, by scope/location"),
        }

    # ── Filter definitions ────────────────────────────────────────────────────

    def _filter_definitions(self, selectable_filters: dict[str, Any]) -> dict[str, Any]:
        """Map filter names to their real source column (or legacy/derived marker)."""
        entity_filters = selectable_filters.get("entity_filters", {})
        definitions: dict[str, Any] = {}
        for name, values in entity_filters.items():
            if isinstance(values, dict) and values.get("derived"):
                definitions[name] = {
                    "source": None,
                    "derived": True,
                    "source_domains": ["energy"],
                    "join_paths": {},
                    "value_source": f"selectable_filters.entity_filters.{name}",
                    "empty_value_semantics": "no_constraint",
                    "derivation": values.get("derivation", ""),
                }
            elif isinstance(values, dict) and "legacy_alias_of" in values:
                real = values["legacy_alias_of"]
                definitions[name] = {
                    "source": f"regions.{real}" if real else None,
                    "legacy_alias_of": real,
                    "source_domains": ["energy"],
                    "join_paths": {"energy": ["energy_region"]},
                    "value_source": f"selectable_filters.entity_filters.{name}",
                    "empty_value_semantics": "no_constraint",
                }
            else:
                # Regular dimension filter.
                source_map = {
                    "country_code": "regions.country_code",
                    "country_name": "regions.country_name",
                    "cdp_region": "regions.cdp_region",
                    "bu_rc": "bu_rc_groups.bu_rc",
                    "bu_rc_group": "bu_rc_groups.bu_rc_group",
                    "bu_rc_name": "locations.bu_rc_name",
                    "bu_rc_id": "locations.bu_rc_id",
                    "location_id": "locations.location_id",
                    "location_name": "locations.location_name",
                    "location_country": "locations.country",
                    "location_city": "locations.city",
                }
                definitions[name] = {
                    "source": source_map.get(name),
                    "source_domains": ["energy"],
                    "join_paths": {"energy": ["energy_location"]},
                    "value_source": f"selectable_filters.entity_filters.{name}",
                    "empty_value_semantics": "no_constraint",
                }
        return definitions

    # ── Grain definitions ─────────────────────────────────────────────────────

    def _grain_definitions(self) -> dict[str, Any]:
        """Quarter/year/multi_year only. Week and month dropped (no sub-quarterly data)."""
        return {
            "entity": {
                "site": {
                    "group_by": [
                        "location_id", "location_name", "location_country",
                        "bu_rc_name", "bu_rc_group", "country_code", "cdp_region",
                    ],
                    "identity_field": "locations.location_id",
                    "requires": ["locations.location_id"],
                },
                "country": {
                    "group_by": ["country_code", "country_name", "cdp_region"],
                    "identity_field": "regions.country_code",
                    "requires": ["regions.country_code"],
                },
                "region": {
                    "group_by": ["cdp_region"],
                    "requires": ["regions.cdp_region"],
                },
                "bu_rc": {
                    "group_by": ["bu_rc", "bu_rc_group"],
                    "requires": ["bu_rc_groups.bu_rc"],
                },
                "portfolio": {"group_by": [], "requires": []},
            },
            "time": {
                "quarter": {
                    "period_sources": {
                        "energy": "dist_ie_energy_raw.fiscal_quarter",
                        "emissions": "dist_ie_energy_emissions_raw.fiscal_quarter",
                    },
                    "transform": "fiscal_quarter",
                    "time_period_format": "{fiscal_year} {fiscal_quarter}",
                    "note": "Executor groups by fiscal_year + fiscal_quarter.",
                },
                "year": {
                    "period_sources": {
                        "energy": "dist_ie_energy_raw.fiscal_year",
                        "emissions": "dist_ie_energy_emissions_raw.fiscal_year",
                    },
                    "transform": "fiscal_year_sum",
                    "time_period_format": "{fiscal_year}",
                    "note": "Executor groups by fiscal_year (Q1+Q2+Q3+Q4 summed).",
                },
                "multi_year": {
                    "period_sources": {},
                    "transform": "single_window",
                },
            },
        }

    # ── Quality check definitions ─────────────────────────────────────────────

    def _quality_check_definitions(self) -> dict[str, Any]:
        return {
            "schema": {"tables": ["all"], "requires": [], "deferred": False},
            "completeness": {
                "tables": ["dist_ie_energy_raw", "dist_ie_energy_emissions_raw"],
                "requires": [], "deferred": True,
            },
            "duplicates": {
                "tables": ["dist_ie_energy_raw"],
                "requires": ["location_id", "fiscal_year", "fiscal_quarter", "media_type"],
                "deferred": True,
            },
            "referential_integrity": {
                "tables": ["dist_ie_energy_raw"],
                "requires": ["location_id", "report_id"],
                "deferred": True,
            },
            "validity": {
                "tables": ["dist_ie_energy_raw", "dist_ie_energy_emissions_raw"],
                "requires": ["amount_consumed_mwh", "co2e_t"],
                "deferred": True,
            },
            "timeliness": {
                "tables": ["dist_ie_overview"],
                "requires": ["approval_date"],
                "deferred": True,
                "note": "Batch 2: is_late not in source data; use approval_date lag",
            },
        }

    # ── Executor capabilities ─────────────────────────────────────────────────

    def _executor_capabilities(self, measures: dict[str, Any]) -> dict[str, Any]:
        """Keep the legacy requestable/alias names alive for validator compat."""
        return {
            "source_domains": ["energy", "kpi", "energy_and_kpi"],
            "requestable_measures": {
                "energy": [
                    "energy_mwh", "co2_t", "amount_consumed_mwh", "co2e_t",
                    "renewable_energy", "share_renewable",
                    "electricity_mwh", "primary_energy_mwh", "secondary_energy_mwh",
                ],
                "emissions": [
                    "emissions_t", "scope2_location_t", "scope2_market_proxy_t",
                ],
                "kpi": ["value", "kpi_value", "co2_total_t"],
            },
            "measure_aliases": {
                "consumption_mwh": "energy_mwh",
                "co2_equivalent_t": "co2_t",
            },
            "generated_only_measures": [
                "scope2_market_proxy_t",
            ],
            "empty_filter_semantics": "no_constraint",
            "entity_grains": ["site", "country", "region", "bu_rc", "portfolio"],
            "time_grains": ["quarter", "year", "multi_year"],  # week/month dropped
            "quality_policies": [
                "include_all", "exclude_inconsistent",
                "exclude_missing_and_inconsistent", "report_flags_only",
            ],
            "quality_policy_definitions": {
                "include_all": {"excluded_flags": [], "quality_columns": []},
                "report_flags_only": {
                    "excluded_flags": [],
                    "quality_columns": ["dist_ie_energy_raw.status"],
                },
                "exclude_inconsistent": {
                    "excluded_flags": ["inconsistent"],
                    "quality_columns": ["dist_ie_energy_raw.status"],
                },
                "exclude_missing_and_inconsistent": {
                    "excluded_flags": ["missing", "inconsistent"],
                    "quality_columns": ["dist_ie_energy_raw.status"],
                },
            },
        }

    # ── Schema signature ──────────────────────────────────────────────────────

    def _schema_signature(self, catalog: dict[str, Any]) -> str:
        """Hash of structural content only (not row counts, filter values, or placeholders)."""
        structural = {
            "source_tables": {
                table: [
                    {"name": column["name"], "dtype": column["dtype"]}
                    for column in metadata["columns"]
                ]
                for table, metadata in catalog["source_tables"].items()
            },
            "source_domains": catalog["source_domains"],
            "structured_relationships": catalog["structured_relationships"],
            "measure_definitions": catalog["measure_definitions"],
            "filter_definitions": catalog["filter_definitions"],
            "grain_definitions": catalog["grain_definitions"],
            "quality_check_definitions": catalog["quality_check_definitions"],
            "executor_capabilities": catalog["executor_capabilities"],
        }
        encoded = json.dumps(
            structural,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _values(self, df: pd.DataFrame, column: str) -> list[Any]:
        values = df[column].dropna().unique().tolist()
        return sorted([self._json_scalar(v) for v in values], key=lambda x: str(x))

    def _json_scalar(self, value: Any) -> Any:
        if hasattr(value, "item"):
            return value.item()
        return value


# ── Module-level resolver (single source of truth for D1 table routing) ──────

# Catalog measure groups → source domain.
# Derived from DataCatalogBuilder._measures() structure; kept as a static
# constant so D1 and the CLI can resolve without building a full catalog object.
_EMISSIONS_MEASURES: frozenset[str] = frozenset(
    {"emissions_t", "scope2_location_t", "scope2_market_proxy_t"}
)
_KPI_MEASURES: frozenset[str] = frozenset({"value", "kpi_value", "co2_total_t"})
# energy_fact_measures minus emissions_t (emissions_t is an emission-domain signal)
_ENERGY_MEASURES: frozenset[str] = frozenset(
    {"energy_mwh", "co2_t", "amount_consumed_mwh", "co2e_t", "renewable_energy",
     "share_renewable", "electricity_mwh", "primary_energy_mwh", "secondary_energy_mwh"}
)


def resolve_source_domains(
    measures: list[str],
    filters: dict | None = None,
) -> list[str]:
    """Map approved measure names + filters to the set of D1 tables to build.

    Authority: catalog measure groups (see DataCatalogBuilder._measures).
    No R1, no suggestion, no guessing — derived from the DA1-approved content
    of the handoff (isolated memory).

    Args:
        measures: list of measure names from the approved handoff.
        filters: filter dict from the approved handoff (checks for "scope" key).

    Returns:
        Ordered, de-duplicated list of source-domain strings — e.g.
        ["emissions"], ["energy"], ["energy", "kpi"], etc.
        Falls back to ["energy"] when no signal is found.
    """
    names = frozenset(measures or [])
    flt = filters or {}
    domains: list[str] = []

    if _EMISSIONS_MEASURES & names or "scope" in flt:
        domains.append("emissions")
    if _KPI_MEASURES & names:
        domains.append("kpi")
    if _ENERGY_MEASURES & names:
        domains.append("energy")

    return list(dict.fromkeys(domains)) or ["energy"]
