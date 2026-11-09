"""Deterministic case-scoped data-quality profiling for D2.

Batch 3: rewritten to profile quarterly data.
Source tables: dist_ie_energy_raw, dist_ie_energy_emissions_raw, regions,
bu_rc_groups, dist_ie_locations.  No weekly star-schema references.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from epoch_switch import config
from epoch_switch.core.envelope import CaseEnvelope


CORE_CHECKS = {
    "schema",
    "completeness",
    "duplicates",
    "referential_integrity",
    "validity",
    "consistency",
    "timeliness",
    "coverage",
    "energy_emissions_reconciliation",
    "energy_classification_partition",
}


COLUMN_POLICIES: dict[str, dict[str, Any]] = {
    "amount_consumed_mwh": {
        "nullable": True,
        "zero_policy": "allowed_but_review_when_co2_positive",
        "valid_range": [0, None],
        "fill_policy": "null_when_no_meter_reading",
    },
    "co2e_t": {
        "nullable": True,
        "zero_policy": "allowed_when_consumption_zero_or_no_factor",
        "valid_range": [0, None],
        "fill_policy": "null_when_no_source_data",
    },
    "renewable_energy": {
        "nullable": True,
        "zero_policy": "valid_for_non_renewable_media",
        "valid_range": [0, None],
        "fill_policy": "null_or_zero_when_not_applicable",
    },
    "share_renewable": {
        "nullable": True,
        "zero_policy": "valid_for_non_renewable_media",
        "valid_range": [0, 1],
        "fill_policy": "null_when_amount_consumed_mwh_is_null",
    },
    "co2_factor": {
        "nullable": True,
        "zero_policy": "allowed_for_renewable_electricity",
        "valid_range": [0, None],
        "fill_policy": "grid_factor_per_country_media",
    },
}


# Source table column sets.  Used by _table_profile schema check.
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "dist_ie_energy_raw": {
        "pk_energy", "report_id", "location_id", "location_name", "active",
        "country", "bu_rc_id", "bu_rc_name", "fiscal_year", "fiscal_quarter",
        "status", "energy_classification", "media_type", "amount_consumed_mwh",
        "renewable_energy", "share_renewable", "amount_consumed_gj", "co2_factor",
        "co2e_t", "costs",
    },
    "dist_ie_energy_emissions_raw": {
        "pk_energy", "report_id", "location_id", "location_name", "active",
        "country", "bu_rc_id", "bu_rc_name", "fiscal_year", "fiscal_quarter",
        "scope", "status", "media_type", "halo_name", "amount_consumed_mwh",
        "emissions_t", "co2_factor", "co2e_t",
    },
    "regions": {"country_code", "cdp_region", "country_name"},
    "bu_rc_groups": {"bu_rc_group", "bu_rc"},
    "dist_ie_locations": {
        "location_id", "location_name", "bu_rc_id", "bu_rc_name",
        "active", "country",
    },
}


@dataclass
class QualityProfileBundle:
    profile: dict[str, Any]
    samples: list[dict[str, Any]]


class DataQualityEngine:
    """Profile raw case-scope data without modifying or imputing it.

    Operates over the quarterly source tables.  All rows in source data
    carry status='Approved'; there are no is_missing/is_late/data_quality_flag
    columns.  Quarterly grain: fiscal_year (int) + fiscal_quarter (str Q1..Q4).
    """

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or config.DATA_DIR

    def execute(
        self,
        envelope: CaseEnvelope,
        requirement_plan: dict[str, Any],
        *,
        sample_limit: int,
        handoff: dict[str, Any] | None = None,
        data_product: dict[str, Any] | None = None,
    ) -> QualityProfileBundle:
        """Profile the case-scope data.

        When ``handoff`` is provided (regulation CLI mode), rows are scoped by
        the approved handoff time_window and filters rather than by
        ``envelope.site_filter`` / ``envelope.time_window``.  This ensures D2
        profiles exactly the same population D1 fetched, consistent with the
        "DA1 approval is final" boundary.

        When ``data_product`` is also provided, an additional reconciliation
        check compares D2's scoped row counts against D1's
        ``source_row_count_after_filters`` and emits an issue if they diverge.
        """
        frames = self._load_frames()
        if handoff is not None:
            scoped = self._scope_frames_from_handoff(frames, handoff)
        else:
            scoped = self._scope_frames(frames, envelope)

        issues: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []

        # Table profiles (fact tables).
        table_profiles = {
            "dist_ie_energy_raw": self._table_profile(
                "dist_ie_energy_raw", scoped["energy"],
                ["location_id", "fiscal_year", "fiscal_quarter", "media_type"],
                issues, candidates,
            ),
            "dist_ie_energy_emissions_raw": self._table_profile(
                "dist_ie_energy_emissions_raw", scoped["emissions"],
                ["location_id", "fiscal_year", "fiscal_quarter", "scope"],
                issues, candidates,
            ),
        }
        # Dimension profiles (reference tables — not scoped; full sets).
        dimension_profiles = {}
        for name, key in [
            ("regions", "country_code"),
            ("bu_rc_groups", "bu_rc"),
            ("dist_ie_locations", "location_id"),
        ]:
            dimension_profiles[name] = self._table_profile(
                name, frames[name], [key], issues, candidates,
            )

        self._energy_checks(scoped["energy"], issues, candidates)
        self._referential_checks(scoped, frames, issues, candidates)
        self._timeliness_checks(scoped["energy"], issues)

        # Derive effective time window: prefer handoff over envelope.
        if handoff and handoff.get("time_window"):
            tw_dict = handoff["time_window"]
            effective_tw: tuple[str, str] | None = (
                str(tw_dict.get("start", "")), str(tw_dict.get("end", ""))
            )
        else:
            effective_tw = None

        coverage = self._coverage_checks(
            scoped, envelope, issues, candidates, time_window_override=effective_tw
        )
        reconciliation = self._energy_emissions_reconciliation(
            scoped["energy"], scoped["emissions"], issues, candidates
        )
        if data_product is not None:
            self._d1_reconciliation_check(scoped, data_product, issues, candidates)

        requirement_assessment = self._evaluate_requirements(requirement_plan, frames, scoped)
        sample_seed = getattr(envelope, "usecase_ref", None) or envelope.case_id
        samples, manifest = self._balanced_sample(scoped, candidates, sample_limit, sample_seed)

        # Scope description.
        if handoff and handoff.get("time_window"):
            tw_scope = [
                str(handoff["time_window"].get("start", "")),
                str(handoff["time_window"].get("end", "")),
            ]
            filter_scope: dict[str, Any] = handoff.get("filters") or {}
        else:
            tw_scope = list(envelope.time_window)
            filter_scope = envelope.site_filter

        profile = {
            "profile_version": "2.0",
            "case_id": envelope.case_id,
            "scope": {
                "site_filter": filter_scope,
                "time_window": tw_scope,
                "scoping_mode": "handoff" if handoff is not None else "envelope",
                "energy_rows": int(len(scoped["energy"])),
                "emissions_rows": int(len(scoped["emissions"])),
                "site_count": int(scoped["energy"]["location_id"].nunique()),
            },
            "checks_executed": sorted(CORE_CHECKS),
            "column_policies": COLUMN_POLICIES,
            "tables": table_profiles,
            "dimensions": dimension_profiles,
            "coverage": coverage,
            "energy_emissions_reconciliation": reconciliation,
            "requirement_assessment": requirement_assessment,
            "issues": issues,
            "issue_counts": self._issue_counts(issues),
            "sample_manifest": manifest,
        }
        return QualityProfileBundle(profile=profile, samples=samples)

    # ── Frame loading ──────────────────────────────────────────────────────────

    def _load_frames(self) -> dict[str, pd.DataFrame]:
        """Read the five source CSVs and build enriched energy/emissions frames."""
        dd = self.data_dir
        energy_raw = pd.read_csv(dd / "dist_ie_energy_raw.csv")
        emissions_raw = pd.read_csv(dd / "dist_ie_energy_emissions_raw.csv")
        regions = pd.read_csv(dd / "regions.csv")
        bu_rc_groups = pd.read_csv(dd / "bu_rc_groups.csv")
        locations = pd.read_csv(dd / "dist_ie_locations.csv")

        # Normalise numeric fiscal_year.
        for df in (energy_raw, emissions_raw):
            if "fiscal_year" in df.columns:
                df["fiscal_year"] = pd.to_numeric(df["fiscal_year"], errors="coerce")

        # Enriched energy frame: join regions on country code, bu_rc_groups on bu_rc_name.
        # Use left_on/right_on (same as D1) so both "country" and "country_code"
        # are present in the enriched frame — the filter path needs country_code.
        energy = energy_raw.merge(
            regions,
            left_on="country", right_on="country_code", how="left",
        ).merge(
            bu_rc_groups.rename(columns={"bu_rc": "bu_rc_name_group"}),
            left_on="bu_rc_name", right_on="bu_rc_name_group", how="left",
        ).drop(columns=["bu_rc_name_group"], errors="ignore")

        # Enriched emissions frame: same joins.
        emissions = emissions_raw.merge(
            regions,
            left_on="country", right_on="country_code", how="left",
        ).merge(
            bu_rc_groups.rename(columns={"bu_rc": "bu_rc_name_group"}),
            left_on="bu_rc_name", right_on="bu_rc_name_group", how="left",
        ).drop(columns=["bu_rc_name_group"], errors="ignore")

        return {
            "dist_ie_energy_raw": energy_raw,
            "dist_ie_energy_emissions_raw": emissions_raw,
            "regions": regions,
            "bu_rc_groups": bu_rc_groups,
            "dist_ie_locations": locations,
            "energy": energy,
            "emissions": emissions,
        }

    # ── Scoping ────────────────────────────────────────────────────────────────

    def _scope_frames(
        self, frames: dict[str, pd.DataFrame], envelope: CaseEnvelope
    ) -> dict[str, pd.DataFrame]:
        """Scope by envelope.site_filter and envelope.time_window (legacy path).

        Time-window filtering uses the representative fiscal-period dates:
        Siemens FY = Oct–Sep; Q1=Nov15 (FY-1), Q2=Feb15, Q3=May15, Q4=Aug15.
        """
        start_str, end_str = envelope.time_window
        out: dict[str, pd.DataFrame] = {}
        filter_map = {
            "country": "country",
            "country_code": "country",
            "region": "cdp_region",
            "bu_rc": "bu_rc_group",
            "size_tier": "size_tier",
            "emea": "emea",
        }
        for name in ("energy", "emissions"):
            df = frames[name].copy()
            df = self._filter_by_time_window(df, start_str, end_str)
            for source, column in filter_map.items():
                if source not in envelope.site_filter:
                    continue
                if column not in df.columns:
                    continue
                values = envelope.site_filter[source]
                values = values if isinstance(values, list) else [values]
                df = df[df[column].isin(values)]
            out[name] = df.reset_index(drop=True)
        return out

    def _scope_frames_from_handoff(
        self, frames: dict[str, pd.DataFrame], handoff: dict[str, Any]
    ) -> dict[str, pd.DataFrame]:
        """Scope by approved handoff time_window and catalog-column filters."""
        tw = handoff.get("time_window") or {}
        start_str = str(tw.get("start") or "")
        end_str = str(tw.get("end") or "")
        filters: dict[str, Any] = handoff.get("filters") or {}
        out: dict[str, pd.DataFrame] = {}
        for name in ("energy", "emissions"):
            df = frames[name].copy()
            if start_str and end_str:
                df = self._filter_by_time_window(df, start_str, end_str)
            for column, values in filters.items():
                if column not in df.columns or values in (None, [], ""):
                    continue
                value_list = values if isinstance(values, list) else [values]
                df = df[df[column].isin(value_list)]
            out[name] = df.reset_index(drop=True)
        return out

    @staticmethod
    def _filter_by_time_window(
        df: pd.DataFrame, start_str: str, end_str: str
    ) -> pd.DataFrame:
        """Filter rows to the time window using representative fiscal-period dates.

        Siemens FY = Oct–Sep (starts in Oct of the previous calendar year).
        Representative mid-quarter dates:
          Q1 → Nov 15 of fiscal_year-1
          Q2 → Feb 15 of fiscal_year
          Q3 → May 15 of fiscal_year
          Q4 → Aug 15 of fiscal_year
        """
        if "fiscal_year" not in df.columns or "fiscal_quarter" not in df.columns:
            return df
        quarter_map = {"Q1": (-1, 11, 15), "Q2": (0, 2, 15), "Q3": (0, 5, 15), "Q4": (0, 8, 15)}
        start = pd.Timestamp(start_str)
        end = pd.Timestamp(end_str)

        def period_date(row: Any) -> pd.Timestamp | None:
            if pd.isna(row.fiscal_year) or pd.isna(row.fiscal_quarter):
                return None
            fy = int(row.fiscal_year)
            offset, month, day = quarter_map.get(str(row.fiscal_quarter), (0, 1, 1))
            return pd.Timestamp(fy + offset, month, day)

        periods = pd.Series(
            [period_date(r) for r in df[["fiscal_year", "fiscal_quarter"]].itertuples(index=False)],
            index=df.index,
        )
        mask = periods.notna() & (periods >= start) & (periods <= end)
        return df[mask].copy()

    # ── D1 reconciliation ──────────────────────────────────────────────────────

    def _d1_reconciliation_check(
        self,
        scoped: dict[str, pd.DataFrame],
        data_product: dict[str, Any],
        issues: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> None:
        """Emit an issue if D2's scoped row counts diverge from D1's fetched rows."""
        tables = (data_product or {}).get("tables", {})
        source_map = {"energy": "energy", "emissions": "emissions"}
        for d2_key, d1_key in source_map.items():
            d1_table = tables.get(d1_key, {})
            d1_count = d1_table.get("source_row_count_after_filters")
            if d1_count is None:
                continue
            d2_count = len(scoped.get(d2_key, pd.DataFrame()))
            if d1_count != d2_count:
                self._add_issue(
                    issues, "d1_d2_scope_mismatch", "high", d1_key,
                    abs(d1_count - d2_count),
                    {
                        "d1_source_row_count_after_filters": int(d1_count),
                        "d2_scoped_row_count": int(d2_count),
                        "description": (
                            "D2 scoped a different number of rows than D1 fetched. "
                            "Verify that handoff filters were applied consistently."
                        ),
                    },
                )

    # ── Table profile ──────────────────────────────────────────────────────────

    def _table_profile(
        self, name: str, df: pd.DataFrame, key_columns: list[str],
        issues: list[dict[str, Any]], candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        expected = EXPECTED_COLUMNS.get(name, set())
        missing_columns = sorted(expected - set(df.columns))
        extra_columns = sorted(set(df.columns) - expected)
        if missing_columns:
            self._add_issue(
                issues, "schema_missing_columns", "critical", name,
                len(missing_columns), {"columns": missing_columns},
            )

        null_counts = {col: int(count) for col, count in df.isna().sum().items() if count}
        for column, count in null_counts.items():
            sev = "high" if column in key_columns else "medium"
            self._add_issue(issues, "null_values", sev, name, count, {
                "column": column, "rate": count / max(1, len(df)),
            })
            self._collect_rows(candidates, df[df[column].isna()], "null_value", name)

        exact_duplicates = int(df.duplicated(keep=False).sum())
        valid_keys = [k for k in key_columns if k in df.columns]
        key_duplicates = int(df.duplicated(valid_keys, keep=False).sum()) if valid_keys else 0
        if exact_duplicates:
            self._add_issue(issues, "exact_duplicates", "medium", name, exact_duplicates, {})
            self._collect_rows(candidates, df[df.duplicated(keep=False)], "exact_duplicate", name)
        if key_duplicates:
            conflicting = (
                df[df.duplicated(valid_keys, keep=False)]
                .groupby(valid_keys, dropna=False)
                .filter(lambda g: len(g.drop_duplicates()) > 1)
            )
            severity = "high" if not conflicting.empty else "medium"
            self._add_issue(issues, "duplicate_business_keys", severity, name, key_duplicates, {
                "conflicting_rows": int(len(conflicting)), "key_columns": valid_keys,
            })
            self._collect_rows(
                candidates,
                conflicting if not conflicting.empty else df[df.duplicated(valid_keys, keep=False)],
                "duplicate_business_key", name,
            )

        return {
            "row_count": int(len(df)),
            "column_count": int(len(df.columns)),
            "missing_expected_columns": missing_columns,
            "extra_columns": extra_columns,
            "null_counts": null_counts,
            "null_rates": {col: count / max(1, len(df)) for col, count in null_counts.items()},
            "exact_duplicate_rows": exact_duplicates,
            "duplicate_business_key_rows": key_duplicates,
        }

    # ── Energy validity checks ─────────────────────────────────────────────────

    def _energy_checks(
        self,
        df: pd.DataFrame,
        issues: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> None:
        """Validity/consistency checks on the energy fact table."""
        src = "dist_ie_energy_raw"

        # Negative amounts.
        neg_cols = [c for c in ("amount_consumed_mwh", "co2e_t", "renewable_energy") if c in df.columns]
        if neg_cols:
            mask = pd.Series(False, index=df.index)
            for col in neg_cols:
                mask |= df[col].fillna(0) < 0
            self._record_mask(df[mask], "negative_values", "high", src, issues, candidates)

        # renewable_energy exceeds amount_consumed_mwh.
        if "renewable_energy" in df.columns and "amount_consumed_mwh" in df.columns:
            overrun = df[
                df["amount_consumed_mwh"].notna()
                & df["renewable_energy"].notna()
                & (df["renewable_energy"] > df["amount_consumed_mwh"])
            ]
            self._record_mask(overrun, "renewable_exceeds_consumption", "high", src, issues, candidates)

        # share_renewable out of [0, 1].
        if "share_renewable" in df.columns:
            invalid_share = df[
                df["share_renewable"].notna()
                & ((df["share_renewable"] < 0) | (df["share_renewable"] > 1))
            ]
            self._record_mask(invalid_share, "share_renewable_out_of_range", "medium", src, issues, candidates)

        # CO2 formula cross-check: co2e_t ≈ amount_consumed_mwh * co2_factor (MWh → kWh × kg/kWh → t).
        if all(c in df.columns for c in ("amount_consumed_mwh", "co2_factor", "co2e_t")):
            valid = (
                df["amount_consumed_mwh"].notna()
                & df["co2_factor"].notna()
                & df["co2e_t"].notna()
                & df["amount_consumed_mwh"].gt(0)
            )
            expected_co2 = df["amount_consumed_mwh"] * df["co2_factor"] / 1000.0  # kg/kWh → t/MWh
            mismatch = df[valid & (df["co2e_t"] - expected_co2).abs().gt(0.5)]
            self._record_mask(mismatch, "co2_formula_mismatch", "high", src, issues, candidates)

        # Status distribution (informational — all rows should be Approved).
        if "status" in df.columns and len(df):
            counts = df["status"].value_counts(dropna=False)
            non_approved = int((df["status"] != "Approved").sum())
            self._add_issue(
                issues, "status_distribution", "info", src, len(df),
                {
                    "counts": {str(k): int(v) for k, v in counts.items()},
                    "non_approved_rows": non_approved,
                },
            )

        self._energy_classification_partition_check(df, issues, candidates)

    def _energy_classification_partition_check(
        self,
        df: pd.DataFrame,
        issues: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> None:
        """Check that energy_classification partitions media_type with no overlap.

        Primary and Secondary Energy must be disjoint by media_type. If any
        media_type appears under both classifications, energy_mwh sums double-count
        physical deliveries and threshold tests (e.g. EnEfG §8/§16) are unreliable.
        """
        src = "dist_ie_energy_raw"
        if "media_type" not in df.columns or "energy_classification" not in df.columns:
            return

        # Check 1: each media_type maps to exactly one classification.
        mt_classes = (
            df.groupby("media_type", dropna=False)["energy_classification"]
            .apply(lambda s: s.dropna().unique().tolist())
        )
        crossing_types = [mt for mt, classes in mt_classes.items() if len(classes) > 1]
        if crossing_types:
            crossing_rows = df[df["media_type"].isin(crossing_types)]
            self._add_issue(
                issues, "energy_classification_partition_violation", "critical", src,
                len(crossing_types),
                {
                    "crossing_media_types": crossing_types,
                    "description": (
                        "These media_type values appear under more than one "
                        "energy_classification. energy_mwh will double-count."
                    ),
                },
            )
            self._collect_rows(
                candidates, crossing_rows,
                "energy_classification_partition_violation", src,
            )

        # Check 2: no (location, year, quarter, media_type) tuple in multiple classes.
        key_cols = ["location_id", "fiscal_year", "fiscal_quarter", "media_type"]
        if all(c in df.columns for c in key_cols):
            class_counts = df.groupby(key_cols, dropna=False)["energy_classification"].nunique()
            n_crossing = int((class_counts > 1).sum())
            if n_crossing:
                self._add_issue(
                    issues, "energy_classification_combo_violation", "critical", src,
                    n_crossing,
                    {
                        "description": (
                            "The same (location, year, quarter, media_type) tuple has rows "
                            "under multiple energy_classification values — row-level double-count."
                        ),
                    },
                )

    # ── Timeliness ─────────────────────────────────────────────────────────────

    def _timeliness_checks(
        self, df: pd.DataFrame, issues: list[dict[str, Any]]
    ) -> None:
        """Check source_last_updated / load_date freshness (informational)."""
        src = "dist_ie_energy_raw"
        for col in ("source_last_updated", "load_date"):
            if col not in df.columns:
                continue
            parsed = pd.to_datetime(df[col], errors="coerce")
            missing = int(parsed.isna().sum())
            if parsed.notna().any():
                self._add_issue(
                    issues, "timeliness_info", "info", src, len(df),
                    {
                        "column": col,
                        "latest": str(parsed.max()),
                        "oldest": str(parsed.min()),
                        "missing_dates": missing,
                    },
                )

    # ── Referential integrity ──────────────────────────────────────────────────

    def _referential_checks(
        self,
        scoped: dict[str, pd.DataFrame],
        frames: dict[str, pd.DataFrame],
        issues: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> None:
        """Check FK integrity against the real dim tables."""
        valid_locations = set(frames["dist_ie_locations"]["location_id"])
        valid_countries = set(frames["regions"]["country_code"])
        valid_bu_rc = set(frames["bu_rc_groups"]["bu_rc"])

        # Energy: orphan location_id.
        energy = scoped["energy"]
        if "location_id" in energy.columns:
            orphan_loc = energy[~energy["location_id"].isin(valid_locations)]
            self._record_mask(orphan_loc, "orphan_location_id", "critical", "energy", issues, candidates)

        # Energy: country not in regions.
        if "country" in energy.columns:
            orphan_country = energy[~energy["country"].isin(valid_countries)]
            self._record_mask(orphan_country, "orphan_country_code", "high", "energy", issues, candidates)

        # Energy: bu_rc_name not in bu_rc_groups.
        if "bu_rc_name" in energy.columns:
            orphan_bu = energy[~energy["bu_rc_name"].isin(valid_bu_rc)]
            self._record_mask(orphan_bu, "orphan_bu_rc_name", "medium", "energy", issues, candidates)

        # Emissions: same checks.
        emissions = scoped["emissions"]
        if "location_id" in emissions.columns:
            orphan_em_loc = emissions[~emissions["location_id"].isin(valid_locations)]
            self._record_mask(orphan_em_loc, "orphan_location_id", "critical", "emissions", issues, candidates)

    # ── Coverage ───────────────────────────────────────────────────────────────

    def _coverage_checks(
        self,
        scoped: dict[str, pd.DataFrame],
        envelope: CaseEnvelope,
        issues: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        *,
        time_window_override: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """Quarterly coverage: detect missing (fiscal_year, fiscal_quarter) pairs.

        For each location × media_type (energy) or location × scope (emissions),
        checks that every expected quarter in the window is present.
        """
        output: dict[str, Any] = {}
        tw = time_window_override or tuple(envelope.time_window)
        expected_quarters = self._expected_quarters(str(tw[0]), str(tw[1]))

        for src_name, df, group_keys, label in [
            ("energy", scoped["energy"], ["location_id", "media_type"], "dist_ie_energy_raw"),
            ("emissions", scoped["emissions"], ["location_id", "scope"], "dist_ie_energy_emissions_raw"),
        ]:
            valid_keys = [k for k in group_keys if k in df.columns]
            if not valid_keys or not expected_quarters:
                output[label] = {"missing_periods": 0, "affected_series": 0, "max_consecutive_missing_quarters": 0}
                continue
            gaps = 0
            max_consecutive = 0
            affected_series = 0
            for _, group in df.groupby(valid_keys, dropna=False):
                present = set(zip(
                    group["fiscal_year"].dropna().astype(int).tolist(),
                    group["fiscal_quarter"].dropna().tolist(),
                ))
                missing = expected_quarters - present
                if not missing:
                    continue
                gaps += len(missing)
                affected_series += 1
                max_run = self._max_consecutive_quarters(missing)
                max_consecutive = max(max_consecutive, max_run)
            output[label] = {
                "missing_periods": int(gaps),
                "affected_series": int(affected_series),
                "max_consecutive_missing_quarters": int(max_consecutive),
            }
            if gaps:
                sev = "high" if max_consecutive >= 2 else "medium"
                self._add_issue(issues, "coverage_gaps", sev, label, gaps, output[label])
        return output

    @staticmethod
    def _expected_quarters(start_str: str, end_str: str) -> set[tuple[int, str]]:
        """Enumerate (fiscal_year, fiscal_quarter) pairs covered by the window."""
        quarter_dates = {
            "Q1": (-1, 11, 15), "Q2": (0, 2, 15), "Q3": (0, 5, 15), "Q4": (0, 8, 15),
        }
        try:
            start = pd.Timestamp(start_str)
            end = pd.Timestamp(end_str)
        except Exception:
            return set()
        result: set[tuple[int, str]] = set()
        for fy in range(start.year - 1, end.year + 2):
            for q, (offset, month, day) in quarter_dates.items():
                try:
                    dt = pd.Timestamp(fy + offset, month, day)
                except Exception:
                    continue
                if start <= dt <= end:
                    result.add((fy, q))
        return result

    @staticmethod
    def _max_consecutive_quarters(
        missing: set[tuple[int, str]],
    ) -> int:
        """Return the length of the longest consecutive quarter run in missing."""
        quarter_order = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
        # Convert to a sortable integer offset (fy * 4 + quarter_index).
        offsets = sorted(
            (fy * 4 + quarter_order.get(q, 0) - 1)
            for fy, q in missing
            if q in quarter_order
        )
        if not offsets:
            return 0
        max_run = 1
        current = 1
        for i in range(1, len(offsets)):
            if offsets[i] == offsets[i - 1] + 1:
                current += 1
                max_run = max(max_run, current)
            else:
                current = 1
        return max_run

    # ── Energy↔emissions reconciliation ───────────────────────────────────────

    def _energy_emissions_reconciliation(
        self,
        energy: pd.DataFrame,
        emissions: pd.DataFrame,
        issues: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Cross-check energy and emissions on report_id.

        Both tables share report_id (100% join in the real data).  We compare:
        - co2e_t totals per report_id (may legitimately differ — distinct sources).
        - amount_consumed_mwh per report_id (should match for the same scope rows).

        Mismatches beyond a tolerance flag inconsistency between the two sources.
        """
        if "report_id" not in energy.columns or "report_id" not in emissions.columns:
            return {"status": "skipped", "reason": "report_id not in both frames"}

        en_agg = (
            energy.groupby("report_id", dropna=False)
            .agg(
                energy_co2e_t=("co2e_t", "sum"),
                energy_mwh=("amount_consumed_mwh", "sum"),
            )
            .reset_index()
        )
        em_agg = (
            emissions.groupby("report_id", dropna=False)
            .agg(
                emissions_co2e_t=("co2e_t", "sum"),
                emissions_mwh=("amount_consumed_mwh", "sum"),
            )
            .reset_index()
        )
        merged = en_agg.merge(em_agg, on="report_id", how="outer")
        compared = int(len(merged))

        # Rows only in energy (no matching emissions row).
        energy_only = int(merged["emissions_co2e_t"].isna().sum())
        # Rows only in emissions (no matching energy row).
        emissions_only = int(merged["energy_co2e_t"].isna().sum())

        # amount_consumed_mwh should agree within tolerance.
        both = merged.dropna(subset=["energy_mwh", "emissions_mwh"])
        mwh_delta = (both["energy_mwh"] - both["emissions_mwh"]).abs()
        mwh_mismatch = int((mwh_delta > 1.0).sum())  # >1 MWh delta is noteworthy
        max_mwh_delta = float(mwh_delta.max()) if len(mwh_delta) else 0.0

        if mwh_mismatch:
            mismatch_rows = both[mwh_delta > 1.0]
            self._add_issue(
                issues, "energy_emissions_mwh_mismatch", "medium",
                "energy_emissions_join", mwh_mismatch,
                {"max_mwh_delta": max_mwh_delta, "description": "amount_consumed_mwh differs between energy and emissions for the same report_id."},
            )
            self._collect_rows(candidates, mismatch_rows, "energy_emissions_mwh_mismatch", "energy_emissions_join")

        if energy_only:
            self._add_issue(
                issues, "energy_report_without_emissions", "info",
                "energy_emissions_join", energy_only,
                {"description": "Energy rows with no matching emissions record. Expected for some media types."},
            )
        if emissions_only:
            self._add_issue(
                issues, "emissions_report_without_energy", "info",
                "energy_emissions_join", emissions_only,
                {"description": "Emissions rows with no matching energy record."},
            )

        return {
            "report_ids_compared": compared,
            "energy_only_report_ids": energy_only,
            "emissions_only_report_ids": emissions_only,
            "mwh_mismatched_report_ids": mwh_mismatch,
            "max_mwh_delta": max_mwh_delta,
        }

    # ── Requirement evaluation ─────────────────────────────────────────────────

    @staticmethod
    def _resolve_field_reference(
        reference: Any,
        table_columns: dict[str, set[str]],
        candidate_tables: list[str],
    ) -> dict[str, Any]:
        """Resolve either ``column`` or ``table.column`` deterministically."""
        if not isinstance(reference, str) or not reference.strip():
            return {"status": "invalid"}
        normalized = reference.strip()
        if "." in normalized:
            parts = normalized.split(".")
            if len(parts) != 2 or not all(parts):
                return {"status": "invalid"}
            table, column = parts
            if table not in table_columns:
                return {"status": "missing_table", "table": table}
            if column not in table_columns[table]:
                return {"status": "missing_column"}
            return {"status": "available", "table": table, "column": column, "canonical": f"{table}.{column}"}
        search_tables = candidate_tables or list(table_columns)
        matches = [f"{t}.{normalized}" for t in search_tables if normalized in table_columns[t]]
        if len(matches) == 1:
            table, column = matches[0].split(".", 1)
            return {"status": "available", "table": table, "column": column, "canonical": matches[0]}
        if len(matches) > 1:
            return {"status": "ambiguous", "candidates": matches}
        return {"status": "missing_column"}

    def _evaluate_requirements(
        self,
        plan: dict[str, Any],
        frames: dict[str, pd.DataFrame],
        scoped: dict[str, pd.DataFrame],
    ) -> dict[str, Any]:
        """Assess requirement-plan claims against the source schema."""
        table_columns: dict[str, set[str]] = {
            "dist_ie_energy_raw": set(frames["dist_ie_energy_raw"].columns),
            "dist_ie_energy_emissions_raw": set(frames["dist_ie_energy_emissions_raw"].columns),
            "regions": set(frames["regions"].columns),
            "bu_rc_groups": set(frames["bu_rc_groups"].columns),
            "dist_ie_locations": set(frames["dist_ie_locations"].columns),
        }
        claims = []
        for claim in plan.get("claims", []):
            declared_tables = list(dict.fromkeys(claim.get("required_tables", [])))
            missing_tables = [t for t in declared_tables if t not in table_columns]
            required_tables = [t for t in declared_tables if t in table_columns]
            candidate_tables = required_tables or list(table_columns)
            missing_fields: list[Any] = []
            resolved_fields: dict[str, str] = {}
            ambiguous_fields: dict[str, list[str]] = {}

            def record_resolution(reference: Any) -> dict[str, Any]:
                resolution = self._resolve_field_reference(reference, table_columns, candidate_tables)
                if resolution["status"] == "available":
                    resolved_fields[str(reference)] = resolution["canonical"]
                elif resolution["status"] == "ambiguous":
                    ambiguous_fields[str(reference)] = resolution["candidates"]
                else:
                    if reference not in missing_fields:
                        missing_fields.append(reference)
                    missing_table = resolution.get("table")
                    if missing_table and missing_table not in missing_tables:
                        missing_tables.append(missing_table)
                return resolution

            for field in claim.get("required_fields", []):
                record_resolution(field)

            missing_values: dict[str, list[Any]] = {}
            for reference, required_values in (claim.get("required_values") or {}).items():
                if required_values in (None, [], ""):
                    continue
                resolution = record_resolution(reference)
                if resolution["status"] != "available":
                    continue
                table = resolution["table"]
                column = resolution["column"]
                frame = frames.get(table, pd.DataFrame())
                available_values: set[Any] = set(frame[column].dropna().unique().tolist()) if column in frame.columns else set()
                absent = [v for v in required_values if v not in available_values]
                if absent:
                    missing_values[reference] = absent

            # History years: count distinct fiscal_year values in the scoped energy frame.
            years = set(scoped["energy"]["fiscal_year"].dropna().astype(int)) if "fiscal_year" in scoped["energy"].columns else set()
            history_required = int(claim.get("minimum_history_years") or 0)
            history_ok = len(years) >= history_required
            proxy_required = bool(
                missing_tables or missing_fields or ambiguous_fields or missing_values or not history_ok
            )
            claims.append({
                "claim_id": claim.get("claim_id"),
                "claim": claim.get("claim"),
                "core": bool(claim.get("core", True)),
                "missing_tables": missing_tables,
                "missing_fields": missing_fields,
                "ambiguous_fields": ambiguous_fields,
                "resolved_fields": resolved_fields,
                "missing_values": missing_values,
                "history_years_available": len(years),
                "history_years_required": history_required,
                "history_ok": history_ok,
                "proxy_required": proxy_required,
                "proxy_allowed": bool(claim.get("proxy_allowed", False)),
                "structurally_available": not proxy_required,
            })
        return {"claims": claims}

    def _frame_key(self, table: str) -> str:
        return table

    # ── Sampling ───────────────────────────────────────────────────────────────

    def _balanced_sample(
        self,
        scoped: dict[str, pd.DataFrame],
        candidates: list[dict[str, Any]],
        limit: int,
        seed_text: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Deterministic balanced sampler.

        Collects issue-flagged candidates by category, then fills remaining
        slots from normal (non-flagged) energy rows.  Row hashes are SHA-256
        digests for determinism verification.
        """
        seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:8], 16)
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(record: dict[str, Any]) -> None:
            clean = self._json_record(record)
            digest = hashlib.sha256(
                json.dumps(clean, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]
            if digest in seen or len(selected) >= limit:
                return
            seen.add(digest)
            selected.append({**clean, "_row_hash": digest})

        categories: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            categories.setdefault(candidate["_sample_reason"], []).append(candidate)
        per_category = max(1, limit // max(1, len(categories) + 1))
        for reason in sorted(categories):
            frame = pd.DataFrame(categories[reason])
            if len(frame) > per_category:
                frame = frame.sample(per_category, random_state=seed)
            for row in frame.to_dict("records"):
                add(row)

        remaining = limit - len(selected)
        if remaining > 0 and not scoped["energy"].empty:
            normal = scoped["energy"].sample(
                min(remaining, len(scoped["energy"])), random_state=seed
            )
            for row in normal.to_dict("records"):
                row["_sample_reason"] = "normal_context"
                row["_sample_source"] = "dist_ie_energy_raw"
                add(row)

        manifest = {
            "limit": limit,
            "selected_count": len(selected),
            "strategy": "balanced_flag_issue_and_normal_context",
            "row_hashes": [row["_row_hash"] for row in selected],
            "reason_counts": {
                str(reason): int(count)
                for reason, count in pd.Series(
                    [row["_sample_reason"] for row in selected], dtype="object"
                ).value_counts().items()
            },
        }
        return selected, manifest

    # ── Utility ────────────────────────────────────────────────────────────────

    def _record_mask(
        self,
        rows: pd.DataFrame,
        issue_type: str,
        severity: str,
        source: str,
        issues: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> None:
        if rows.empty:
            return
        self._add_issue(issues, issue_type, severity, source, len(rows), {})
        self._collect_rows(candidates, rows, issue_type, source)

    def _collect_rows(
        self,
        candidates: list[dict[str, Any]],
        rows: pd.DataFrame,
        reason: str,
        source: str,
        max_rows: int = 40,
    ) -> None:
        for record in rows.head(max_rows).to_dict("records"):
            candidates.append({**self._json_record(record), "_sample_reason": reason, "_sample_source": source})

    def _add_issue(
        self,
        issues: list[dict[str, Any]],
        issue_type: str,
        severity: str,
        source: str,
        count: int,
        details: dict[str, Any],
    ) -> None:
        issues.append({
            "issue_id": f"dq_{len(issues) + 1:03d}",
            "issue_type": issue_type,
            "severity": severity,
            "source": source,
            "count": int(count),
            "details": details,
        })

    def _issue_counts(self, issues: list[dict[str, Any]]) -> dict[str, int]:
        counts = pd.Series([issue["severity"] for issue in issues], dtype="object")
        return {str(key): int(value) for key, value in counts.value_counts().items()}

    def _json_record(self, record: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for key, value in record.items():
            try:
                if pd.isna(value):
                    out[key] = None
                    continue
            except (TypeError, ValueError):
                pass
            if isinstance(value, pd.Timestamp):
                out[key] = value.isoformat()
            elif hasattr(value, "item"):
                out[key] = value.item()
            else:
                out[key] = value
        return out
