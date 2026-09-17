"""Deterministic pandas executor for D1 DataRequests.

Agent prompt: epoch_switch/agents/data/d1_loader/D1.md.
Request normalization: d1_loader/d1_data_request_planner.py.
Downstream: D2 quality preflight and derived_case_facts_builder.py.

Batch 2a: reads the energy schema (dist_ie_energy_raw.csv + dim joins).
Batch 2b: emissions scope join (dist_ie_energy_emissions_raw.csv),
          size_tier/emea derived dimensions materialised in-pipeline.
Batch 3: D2 quality engine rewrite (quarterly schema), kpi as derived sums.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from epoch_switch import config

# Filters keyed here trigger a clear ValueError instead of silently returning a wrong subset.
# location_type / performance_archetype removed in Batch 3: no source column, no use case.
_DERIVED_FILTER_FIELDS: frozenset[str] = frozenset()

# Middle-East countries present in the source data, bucketed under cdp_region="Asia Australia".
_EMEA_ME_COUNTRIES = frozenset({"AE", "SA", "TR"})

# Fiscal quarter → (fy_offset, month, day) for representative calendar date.
# Siemens FY = Oct–Sep.
_QUARTER_DATE: dict[str, tuple[int, int, int]] = {
    "Q1": (-1, 11, 15),   # Oct–Dec of FY-1
    "Q2": (0, 2, 15),     # Jan–Mar of FY
    "Q3": (0, 5, 15),     # Apr–Jun of FY
    "Q4": (0, 8, 15),     # Jul–Sep of FY
}

ETS2_FUELS = frozenset({"Natural Gas", "Heating Oil", "Liquid Gas", "Diesel", "Petrol"})


class DataRequestExecutor:
    """Execute a normalized DataRequest against the current CSV dataset."""

    def __init__(self, data_dir: Path | None = None, max_rows: int = 500):
        self.data_dir = data_dir or config.DATA_DIR
        self.max_rows = max_rows

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        # Route on catalog-derived source_domains list (set by normalize_data_request
        # from DA1-approved measures — never from R1's suggestion).
        # Fallback: legacy energy_and_kpi composite string → two-table list.
        _label = request.get("source_domain", "energy")
        if request.get("source_domains"):
            sources = list(request["source_domains"])
        elif _label == "energy_and_kpi":
            sources = ["energy", "kpi"]
        else:
            sources = [_label]
        tables: dict[str, Any] = {}

        # Lazy-loaded energy frame — reused for size_tier derivation across sources.
        energy_df: pd.DataFrame | None = None

        for src in sources:
            if src == "kpi":
                tables[src] = self._execute_for_source(src, self._load_kpi_frame(), request)
            elif src == "emissions":
                # Load energy frame for size_tier map (computed on full energy history).
                if energy_df is None:
                    energy_df = self._load_energy_frame()
                em_df = self._load_emissions_frame(energy_df=energy_df)
                tables["emissions"] = self._execute_for_source("emissions", em_df, request)
            else:  # "energy"
                if energy_df is None:
                    energy_df = self._load_energy_frame()
                tables["energy"] = self._execute_for_source("energy", energy_df, request)

        return {
            "product_id": request.get("request_id"),
            "request": request,
            "tables": tables,
            "summary": {
                name: {
                    "row_count": table["row_count"],
                    "column_count": len(table["columns"]),
                    "truncated": table["truncated"],
                    "columns": table["columns"],
                    "quality_summary": table.get("quality_summary", {}),
                }
                for name, table in tables.items()
            },
        }

    def _execute_for_source(
        self,
        source: str,
        df: pd.DataFrame,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        df = self._apply_time_window(
            df, request.get("time_window", {}), request.get("time_grain", "year")
        )
        df = self._apply_filters(df, request.get("filters", {}))
        quality_summary = self._quality_summary(df)
        df = self._apply_quality_policy(df, request.get("quality_policy", "exclude_inconsistent"))
        df = self._add_time_period(df, request.get("time_grain", "year"))
        df = self._add_derived_columns(df, source)

        group_cols = self._group_columns(
            request.get("entity_grain", "site"),
            request.get("time_grain", "year"),
            source=source,
        )
        group_cols = [col for col in group_cols if col in df.columns]
        measures = self._measures_for_source(source, request.get("measures") or [])
        measures = [m for m in measures if m in df.columns]
        if not measures:
            measures = ["row_count_marker"]
            df["row_count_marker"] = 1

        if group_cols:
            table = df.groupby(group_cols, dropna=False)[measures].sum(min_count=1).reset_index()
        else:
            values = {m: df[m].sum(min_count=1) for m in measures}
            table = pd.DataFrame([values])

        table = self._add_post_aggregation_metrics(table)
        table = table.where(pd.notna(table), None)
        data = table.head(self.max_rows).to_dict("records")

        return {
            "source": source,
            "row_count": int(len(table)),
            "source_row_count_after_filters": int(len(df)),
            "columns": table.columns.tolist(),
            "data": data,
            "preview": data[:10],
            "truncated": bool(len(table) > self.max_rows),
            "quality_summary": quality_summary,
        }

    # ── Frame loading ─────────────────────────────────────────────────────────

    def _load_energy_frame(self) -> pd.DataFrame:
        """Load energy fact + dim joins; materialise size_tier and emea columns.

        dist_ie_energy_raw already carries country, bu_rc_name, location_name as
        direct columns; regions adds cdp_region/country_name, bu_rc_groups adds
        bu_rc_group, and dist_ie_locations adds full address (city, address,
        zip_code, latitude, longitude) via the location_id key.
        """
        dd = self.data_dir
        energy = pd.read_csv(dd / "dist_ie_energy_raw.csv")

        regions = pd.read_csv(dd / "regions.csv")[["country_code", "country_name", "cdp_region"]]
        bu_rc_groups = pd.read_csv(dd / "bu_rc_groups.csv")[["bu_rc", "bu_rc_group"]]
        locations = pd.read_csv(dd / "dist_ie_locations.csv")[
            ["location_id", "city", "address", "zip_code", "latitude", "longitude"]
        ]

        energy = energy.merge(
            regions,
            left_on="country",
            right_on="country_code",
            how="left",
        )
        energy = energy.merge(
            bu_rc_groups,
            left_on="bu_rc_name",
            right_on="bu_rc",
            how="left",
        )
        energy = energy.merge(locations, on="location_id", how="left")

        # Add deterministic period dates for time-window filtering.
        energy["_period_date"] = energy.apply(
            lambda r: _fiscal_period_date(r["fiscal_year"], r["fiscal_quarter"]),
            axis=1,
        )

        # Materialise derived dimensions on the full frame (before any windowing).
        tier_map = self._compute_size_tier_map(energy)
        energy = self._add_derived_dimensions(energy, tier_map)
        return energy

    def _load_emissions_frame(self, energy_df: pd.DataFrame | None = None) -> pd.DataFrame:
        """Load emissions fact + dim joins; materialise the same derived dimensions.

        Uses energy_df (if provided) to compute the size_tier map so classifications
        are consistent between energy and emissions tables within a single request.
        Joins dist_ie_locations for full address attributes (city, address, zip_code,
        latitude, longitude) — same join performed in _load_energy_frame.
        """
        dd = self.data_dir
        em = pd.read_csv(dd / "dist_ie_energy_emissions_raw.csv")

        regions = pd.read_csv(dd / "regions.csv")[["country_code", "country_name", "cdp_region"]]
        bu_rc_groups = pd.read_csv(dd / "bu_rc_groups.csv")[["bu_rc", "bu_rc_group"]]
        locations = pd.read_csv(dd / "dist_ie_locations.csv")[
            ["location_id", "city", "address", "zip_code", "latitude", "longitude"]
        ]

        em = em.merge(
            regions,
            left_on="country",
            right_on="country_code",
            how="left",
        )
        em = em.merge(
            bu_rc_groups,
            left_on="bu_rc_name",
            right_on="bu_rc",
            how="left",
        )
        em = em.merge(locations, on="location_id", how="left")

        # Period dates for time-window filtering.
        em["_period_date"] = em.apply(
            lambda r: _fiscal_period_date(r["fiscal_year"], r["fiscal_quarter"]),
            axis=1,
        )

        # Compute size_tier from the energy frame (consistent definition across sources).
        tier_map = self._compute_size_tier_map(energy_df if energy_df is not None else em)
        em = self._add_derived_dimensions(em, tier_map)
        return em

    def _load_kpi_frame(self) -> pd.DataFrame:
        """KPI domain: derived view over the energy frame.

        value/kpi_value = amount_consumed_mwh (energy consumption total), with full
        media_type and location breakdown so callers can aggregate as needed.
        co2_total_t is added from the emissions frame where report_id joins.
        """
        energy_df = self._load_energy_frame()
        return energy_df

    # ── Derived dimension helpers ─────────────────────────────────────────────

    def _compute_size_tier_map(self, df: pd.DataFrame) -> dict[Any, str]:
        """Compute size_tier for each location_id from avg annual co2e_t.

        Tiers are quantile-based over the full provided frame (before time windowing):
          - > p75 → large
          - p25–p75 → medium
          - < p25 → small

        Returns a {location_id: tier} dict for vectorised column assignment.
        """
        if "location_id" not in df.columns or "co2e_t" not in df.columns:
            return {}
        group_cols = ["location_id"]
        if "fiscal_year" in df.columns:
            group_cols = ["location_id", "fiscal_year"]
        per_site_year = df.groupby(group_cols)["co2e_t"].sum()
        if "fiscal_year" in df.columns:
            per_site_mean = per_site_year.groupby("location_id").mean()
        else:
            per_site_mean = per_site_year
        if per_site_mean.empty:
            return {}
        p25 = float(per_site_mean.quantile(0.25))
        p75 = float(per_site_mean.quantile(0.75))

        def _tier(v: float) -> str:
            if v > p75:
                return "large"
            if v < p25:
                return "small"
            return "medium"

        return {loc_id: _tier(float(v)) for loc_id, v in per_site_mean.items()}

    def _add_derived_dimensions(
        self, df: pd.DataFrame, tier_map: dict[Any, str]
    ) -> pd.DataFrame:
        """Materialise emea and size_tier columns on the full (pre-window) frame.

        emea: True when the row's country is geographically in EMEA.
          = cdp_region ∈ {Europe, Africa}  OR  country ∈ {AE, SA, TR}
          (AE/SA/TR are bucketed under cdp_region="Asia Australia".)

        size_tier: large/medium/small per location_id from avg-annual-co2e_t quantiles.
          Computed over the full loaded frame; does not shift with the query window.
        """
        out = df.copy()

        # emea
        has_region = "cdp_region" in out.columns
        has_country = "country" in out.columns
        if has_region and has_country:
            out["emea"] = (
                out["cdp_region"].isin({"Europe", "Africa"})
                | out["country"].isin(_EMEA_ME_COUNTRIES)
            )
        elif has_region:
            out["emea"] = out["cdp_region"].isin({"Europe", "Africa"})
        else:
            out["emea"] = False

        # size_tier
        if tier_map and "location_id" in out.columns:
            out["size_tier"] = out["location_id"].map(tier_map).fillna("small")
        else:
            out["size_tier"] = "small"

        return out

    # ── Time window ───────────────────────────────────────────────────────────

    def _apply_time_window(
        self,
        df: pd.DataFrame,
        time_window: dict[str, Any],
        time_grain: str = "year",
    ) -> pd.DataFrame:
        start = time_window.get("start")
        end = time_window.get("end")
        if not start or not end:
            return df
        # Quarter grain: use the representative calendar date (_period_date) for
        # quarter-precise filtering. _period_date is materialised in both loader
        # methods (energy: lines 168-171, emissions: 204-207) using the same
        # monotonic FY-quarter → mid-month mapping as the catalog.  This lets a
        # registry time_window bracket exactly 3 quarters (e.g. FY2025 Q4 through
        # FY2026 Q2) without pulling in adjacent quarters.
        if time_grain == "quarter" and "_period_date" in df.columns:
            mask = (df["_period_date"] >= str(start)) & (df["_period_date"] <= str(end))
            return df[mask].copy()
        # Year grain (default): source data is fiscal-year native. Interpret the
        # approved window as fiscal-year bounds so FY2022 Q1 (representative Nov 2021)
        # is not cut at the start, and a partial next-FY quarter is not lost at the end.
        # Fall back to the representative-date calendar filter only when fiscal_year is
        # unavailable (e.g. weekly KPI frames).
        if "fiscal_year" in df.columns:
            start_year = int(str(start)[:4])
            end_year = int(str(end)[:4])
            fy = pd.to_numeric(df["fiscal_year"], errors="coerce")
            return df[fy.between(start_year, end_year)].copy()
        if "_period_date" not in df.columns:
            return df
        mask = (df["_period_date"] >= str(start)) & (df["_period_date"] <= str(end))
        return df[mask].copy()

    # ── Filters ───────────────────────────────────────────────────────────────

    def _apply_filters(self, df: pd.DataFrame, filters: dict[str, Any]) -> pd.DataFrame:
        out = df
        for column, values in (filters or {}).items():
            if values in (None, [], ""):
                continue
            if column in _DERIVED_FILTER_FIELDS:
                raise ValueError(
                    f"Filter '{column}' is a derived dimension not yet materialised. "
                    "Batch 2c will add it if required. "
                    "Remove this filter or contact the data team."
                )
            if column not in out.columns:
                # Genuinely unknown column — warn-and-skip (mirrors validator behaviour).
                continue
            value_list = values if isinstance(values, list) else [values]
            out = out[out[column].isin(value_list)]
        return out.copy()

    # ── Quality ───────────────────────────────────────────────────────────────

    def _quality_summary(self, df: pd.DataFrame) -> dict[str, Any]:
        if "status" not in df.columns:
            return {}
        return {"status": {str(k): int(v) for k, v in df["status"].value_counts().items()}}

    def _apply_quality_policy(self, df: pd.DataFrame, policy: str) -> pd.DataFrame:
        # All source rows are status=Approved; policy filtering is a no-op in Batch 2b.
        # Batch 2c will add is_missing / is_late logic from the quality engine.
        return df.copy()

    # ── Time period ───────────────────────────────────────────────────────────

    def _add_time_period(self, df: pd.DataFrame, time_grain: str) -> pd.DataFrame:
        out = df.copy()
        if time_grain == "quarter":
            out["time_period"] = (
                out["fiscal_year"].astype(str) + " " + out["fiscal_quarter"].astype(str)
            )
        elif time_grain == "multi_year":
            out["time_period"] = "multi_year"
        else:
            # "year" (default) and any unknown grain → fiscal year
            out["time_period"] = out["fiscal_year"].astype(str)
        return out

    # ── Derived columns ───────────────────────────────────────────────────────

    def _add_derived_columns(self, df: pd.DataFrame, source: str) -> pd.DataFrame:
        out = df.copy()

        if source == "kpi":
            # KPI derived measures: value/kpi_value = energy_consumption_total.
            out["value"] = out.get("amount_consumed_mwh", pd.Series(0.0, index=out.index)).fillna(0.0)
            out["kpi_value"] = out["value"]
            # co2_total_t: use energy co2e_t as a proxy (emissions table join not done here).
            out["co2_total_t"] = out.get("co2e_t", pd.Series(0.0, index=out.index)).fillna(0.0)
            return out

        if source == "emissions":
            # Emissions-branch: scope-labelled derived measures.
            co2e = out.get("co2e_t", pd.Series(0.0, index=out.index)).fillna(0.0)
            amount = out.get("amount_consumed_mwh", pd.Series(0.0, index=out.index)).fillna(0.0)
            co2_factor = out.get("co2_factor", pd.Series(0.0, index=out.index)).fillna(0.0)
            scope_col = out.get("scope", pd.Series("", index=out.index)).fillna("")
            is_scope2 = scope_col.eq("Scope 2")

            # emissions_t: passthrough (distinct from energy co2e_t — different source)
            if "emissions_t" not in out.columns:
                out["emissions_t"] = co2e
            out["scope2_location_t"] = co2e.where(is_scope2, 0.0)
            out["scope2_market_proxy_t"] = (amount * co2_factor).where(is_scope2, 0.0)
            return out

        # Energy branch.
        consumption = out.get("amount_consumed_mwh", pd.Series(0.0, index=out.index)).fillna(0.0)
        co2e = out.get("co2e_t", pd.Series(0.0, index=out.index)).fillna(0.0)
        renewable = out.get("renewable_energy", pd.Series(0.0, index=out.index)).fillna(0.0)
        is_electricity = (
            out["media_type"].eq("Electricity")
            if "media_type" in out.columns
            else pd.Series(False, index=out.index)
        )
        is_primary = (
            out["energy_classification"].eq("Primary Energy")
            if "energy_classification" in out.columns
            else pd.Series(False, index=out.index)
        )
        is_secondary = (
            out["energy_classification"].eq("Secondary Energy")
            if "energy_classification" in out.columns
            else pd.Series(False, index=out.index)
        )

        # Legacy alias names expected by _measures_for_source and downstream.
        out["energy_mwh"] = consumption
        out["co2_t"] = co2e
        out["electricity_mwh"] = consumption.where(is_electricity, 0.0)
        out["primary_energy_mwh"] = consumption.where(is_primary, 0.0)
        out["secondary_energy_mwh"] = consumption.where(is_secondary, 0.0)
        # share_renewable: pre-aggregation ratio (used as a raw column before groupby;
        # post-aggregation ratio is recomputed in _add_post_aggregation_metrics).
        denom = consumption.replace({0.0: None})
        out["share_renewable"] = renewable / denom
        return out

    # ── Group columns ─────────────────────────────────────────────────────────

    def _group_columns(
        self, entity_grain: str, time_grain: str, source: str = "energy"
    ) -> list[str]:
        entity_groups: dict[str, list[str]] = {
            "site": [
                "location_id",
                "location_name",
                "country",          # ISO2 — from energy table directly
                "country_code",     # ISO2 — from regions join (same values)
                "country_name",
                "cdp_region",
                "bu_rc_name",
                "bu_rc_group",
                # Full address — from dist_ie_locations join (constant per site).
                "city",
                "address",
                "zip_code",
                "latitude",
                "longitude",
            ],
            "country": ["country_code", "country_name", "cdp_region"],
            "region": ["cdp_region"],
            "bu_rc": ["bu_rc_name", "bu_rc_group"],
            "portfolio": [],
        }
        cols = entity_groups.get(entity_grain, ["location_id", "location_name"]).copy()
        if time_grain != "multi_year":
            cols.append("time_period")
        # Emissions source: always split by scope so rows are scope-labelled.
        if source == "emissions":
            cols.append("scope")
        # KPI source: split by media_type for energy breakdown visibility.
        if source == "kpi":
            cols.append("media_type")
        return cols

    # ── Measures ─────────────────────────────────────────────────────────────

    def _measures_for_source(self, source: str, measures: list[str]) -> list[str]:
        if source == "kpi":
            kpi_aliases: dict[str, str] = {
                "kpi_value": "value",
                "energy_consumption_total": "value",
            }
            kpi_allowed = {"value", "kpi_value", "co2_total_t"}
            normalized_kpi = [kpi_aliases.get(m, m) for m in measures]
            result = list(dict.fromkeys(m for m in normalized_kpi if m in kpi_allowed))
            return result if result else ["value"]  # default: energy consumption total

        if source == "emissions":
            aliases: dict[str, str] = {
                "co2e_t": "emissions_t",  # emissions co2e_t → total scope-labelled
            }
            allowed = {
                "emissions_t",
                "scope2_location_t",
                "scope2_market_proxy_t",
            }
            normalized = [aliases.get(m, m) for m in measures]
            return list(dict.fromkeys(m for m in normalized if m in allowed))

        # energy (default)
        aliases = {
            "consumption_mwh": "energy_mwh",
            "co2_equivalent_t": "co2_t",
            "amount_consumed_mwh": "energy_mwh",
            "co2e_t": "co2_t",
        }
        allowed = {
            "energy_mwh",
            "co2_t",
            "electricity_mwh",
            "primary_energy_mwh",
            "secondary_energy_mwh",
            "renewable_energy",
            "share_renewable",
        }
        normalized = [aliases.get(m, m) for m in measures]
        return list(dict.fromkeys(m for m in normalized if m in allowed))

    # ── Post-aggregation ──────────────────────────────────────────────────────

    def _add_post_aggregation_metrics(self, table: pd.DataFrame) -> pd.DataFrame:
        out = table.copy()
        if {"co2_t", "energy_mwh"}.issubset(out.columns):
            denominator = out["energy_mwh"].replace({0: pd.NA})
            out["weighted_co2_factor_kg_per_kwh"] = out["co2_t"] / denominator
        if {"renewable_energy", "energy_mwh"}.issubset(out.columns):
            denominator = out["energy_mwh"].replace({0: pd.NA})
            out["share_renewable_pct"] = (out["renewable_energy"] / denominator * 100).round(2)
        return out


# ── Module-level helpers ──────────────────────────────────────────────────────

def _fiscal_period_date(fiscal_year: Any, fiscal_quarter: Any) -> str:
    """Return a deterministic ISO date representative of the fiscal period."""
    fq = str(fiscal_quarter) if fiscal_quarter is not None else ""
    fy = int(fiscal_year) if fiscal_year is not None else 2023
    mapping = _QUARTER_DATE.get(fq)
    if mapping is None:
        return f"{fy}-06-15"
    fy_offset, month, day = mapping
    return f"{fy + fy_offset}-{month:02d}-{day:02d}"
