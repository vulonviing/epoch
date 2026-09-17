from __future__ import annotations

from copy import deepcopy

import pytest

from epoch_switch.agents.data.d2_missing_value.d2_data_quality_engine import DataQualityEngine
from epoch_switch.core.envelope import CaseEnvelope
from epoch_switch.usecases.registry import UC1, UC2, UC3


def envelope(seed) -> CaseEnvelope:
    return CaseEnvelope.new(
        usecase_ref=seed.id,
        natural_request=seed.natural_request,
        regulation_refs=seed.regulation_refs,
        site_filter=deepcopy(seed.site_filter),
        time_window=seed.time_window,
        expected_output_family=seed.expected_output_family,
        assessment_contract=seed.assessment_contract.model_dump(),
        assurance_level="elevated",
    )


def test_uc2_energy_emissions_reconciliation_and_balanced_sample():
    """D2 profiles the energy + emissions tables; reconciliation on report_id."""
    env = envelope(UC2)
    plan = {
        "claims": [
            {
                "claim_id": "annual_energy_threshold",
                "claim": "Annual per-site energy threshold",
                "core": True,
                "required_tables": ["dist_ie_energy_raw"],
                "required_fields": ["location_id", "fiscal_year", "fiscal_quarter",
                                    "media_type", "amount_consumed_mwh"],
                "required_values": {},
                "minimum_history_years": 1,
                "proxy_allowed": False,
            }
        ]
    }

    first = DataQualityEngine().execute(env, plan, sample_limit=60)
    second = DataQualityEngine().execute(env, plan, sample_limit=60)

    # Real reconciliation: energy ↔ emissions on report_id.
    reconciliation = first.profile["energy_emissions_reconciliation"]
    assert "report_ids_compared" in reconciliation
    assert reconciliation["report_ids_compared"] > 0
    # All rows are Approved — scope has real energy rows.
    assert first.profile["scope"]["energy_rows"] > 0
    assert first.profile["sample_manifest"]["selected_count"] == 60
    assert (
        first.profile["sample_manifest"]["row_hashes"]
        == second.profile["sample_manifest"]["row_hashes"]
    )
    assert "normal_context" in first.profile["sample_manifest"]["reason_counts"]
    assert "samples" not in first.profile


def test_empty_list_filter_value_is_treated_as_no_constraint():
    """An approved filter target with no concrete catalog value (e.g. an
    unmapped site_name -> location_name filter) must not wipe out all rows.

    D1's _apply_filters already treats values in (None, [], "") as "no
    constraint"; D2's handoff-faithful scoping must match that behavior so
    the two agents profile the same population (AGENTS.md D1/D2 boundary).
    """
    env = envelope(UC2)
    plan = {"claims": []}

    handoff_with_empty_filter = {
        "time_window": {"start": "2025-01-06", "end": "2025-12-29"},
        "filters": {"iso2_code": ["DE"], "location_name": []},
    }
    handoff_without_extra_filter = {
        "time_window": {"start": "2025-01-06", "end": "2025-12-29"},
        "filters": {"iso2_code": ["DE"]},
    }

    with_empty = DataQualityEngine().execute(
        env, plan, sample_limit=10, handoff=handoff_with_empty_filter
    )
    without_extra = DataQualityEngine().execute(
        env, plan, sample_limit=10, handoff=handoff_without_extra_filter
    )

    assert with_empty.profile["scope"]["energy_rows"] > 0
    assert (
        with_empty.profile["scope"]["energy_rows"]
        == without_extra.profile["scope"]["energy_rows"]
    )
    assert (
        with_empty.profile["scope"]["emissions_rows"]
        == without_extra.profile["scope"]["emissions_rows"]
    )


@pytest.mark.parametrize(
    ("seed", "claim", "expected_missing"),
    [
        (
            # UC1: EnEfG Binary — EnMS certification table not in the source catalog
            UC1,
            {
                "claim_id": "enms_compliance",
                "claim": "EnMS compliance status",
                "core": True,
                "required_tables": ["fact_certification"],
                "required_fields": ["enms_status"],
            },
            {"fact_certification", "enms_status"},
        ),
        (
            # UC2: ETS1 Qualitative — allowance surrender table not in the source catalog
            UC2,
            {
                "claim_id": "surrender_gap",
                "claim": "Allowance surrender gap",
                "core": True,
                "required_tables": ["fact_allowance_surrender"],
                "required_fields": ["surrendered_t", "allocated_t"],
            },
            {"fact_allowance_surrender", "surrendered_t", "allocated_t"},
        ),
        (
            # UC3: CSRD Scope 2 Quantitative — residual mix factor not in the source catalog
            UC3,
            {
                "claim_id": "market_scope2",
                "claim": "Market-based Scope 2",
                "core": True,
                "required_tables": ["dim_country"],
                "required_fields": ["residual_mix_factor"],
            },
            {"residual_mix_factor"},
        ),
    ],
)
def test_known_use_case_evidence_gaps_are_structurally_visible(
    seed, claim, expected_missing
):
    claim.setdefault("required_values", {})
    claim.setdefault("minimum_history_years", 0)
    claim.setdefault("proxy_allowed", False)
    result = DataQualityEngine().execute(
        envelope(seed), {"claims": [claim]}, sample_limit=24
    )
    assessed = result.profile["requirement_assessment"]["claims"][0]
    missing = set(assessed["missing_tables"]) | set(assessed["missing_fields"])

    assert expected_missing <= missing
    assert assessed["structurally_available"] is False
    assert assessed["proxy_required"] is True


def test_uc3_scope2_energy_evidence_is_available():
    """The emissions table has the Scope 2 fields UC3 needs.

    UC3 (CSRD Scope 2 dual-method) requires co2e_t and co2_factor from the
    emissions table to compute scope2_location_t and scope2_market_proxy_t.
    This test verifies structural schema availability (table + column resolution),
    not runtime coverage.  minimum_history_years=0 avoids dependency on the scoped
    row count, which varies with the UC3 site_filter's EMEA mapping.
    """
    claim = {
        "claim_id": "scope2_emissions",
        "claim": "Scope 2 CO2 emissions",
        "core": True,
        "required_tables": ["dist_ie_energy_emissions_raw"],
        "required_fields": [
            "location_id",
            "fiscal_year",
            "fiscal_quarter",
            "co2e_t",
            "co2_factor",
            "amount_consumed_mwh",
        ],
        "required_values": {},
        "minimum_history_years": 0,  # schema check only — not runtime-coverage check
        "proxy_allowed": False,
    }
    result = DataQualityEngine().execute(
        envelope(UC3), {"claims": [claim]}, sample_limit=60
    )
    assessed = result.profile["requirement_assessment"]["claims"][0]

    assert assessed["missing_tables"] == []
    assert assessed["missing_fields"] == []
    assert assessed["missing_values"] == {}
    assert assessed["history_ok"] is True
    assert assessed["structurally_available"] is True


def test_qualified_field_references_resolve_against_the_named_table():
    """Fully-qualified table.column references resolve against the source schema."""
    claim = {
        "claim_id": "qualified_energy_fields",
        "claim": "Qualified energy evidence",
        "core": True,
        "required_tables": ["dist_ie_energy_raw", "dist_ie_locations"],
        "required_fields": [
            "dist_ie_energy_raw.location_id",
            "dist_ie_energy_raw.amount_consumed_mwh",
            "dist_ie_locations.location_id",
        ],
        "required_values": {},
        "minimum_history_years": 1,
        "proxy_allowed": False,
    }

    result = DataQualityEngine().execute(
        envelope(UC2), {"claims": [claim]}, sample_limit=24
    )
    assessed = result.profile["requirement_assessment"]["claims"][0]

    assert assessed["missing_fields"] == []
    assert assessed["ambiguous_fields"] == {}
    assert assessed["resolved_fields"] == {
        "dist_ie_energy_raw.location_id": "dist_ie_energy_raw.location_id",
        "dist_ie_energy_raw.amount_consumed_mwh": "dist_ie_energy_raw.amount_consumed_mwh",
        "dist_ie_locations.location_id": "dist_ie_locations.location_id",
    }
    assert assessed["structurally_available"] is True


def test_unqualified_field_resolves_when_only_one_required_table_contains_it():
    """An unqualified column resolves unambiguously when only one required table has it."""
    claim = {
        "claim_id": "unique_energy_field",
        "claim": "Unique energy evidence",
        "core": True,
        # media_type is in dist_ie_energy_raw but NOT in dist_ie_locations.
        "required_tables": ["dist_ie_energy_raw", "dist_ie_locations"],
        "required_fields": ["media_type"],
        "required_values": {},
        "minimum_history_years": 1,
        "proxy_allowed": False,
    }

    result = DataQualityEngine().execute(
        envelope(UC2), {"claims": [claim]}, sample_limit=24
    )
    assessed = result.profile["requirement_assessment"]["claims"][0]

    assert assessed["resolved_fields"] == {
        "media_type": "dist_ie_energy_raw.media_type"
    }
    assert assessed["ambiguous_fields"] == {}
    assert assessed["structurally_available"] is True


def test_unqualified_field_is_ambiguous_across_required_tables():
    """An unqualified column that appears in multiple required tables is flagged ambiguous."""
    claim = {
        "claim_id": "ambiguous_location",
        "claim": "Ambiguous location evidence",
        "core": True,
        # location_id exists in both dist_ie_energy_raw and dist_ie_locations.
        "required_tables": ["dist_ie_energy_raw", "dist_ie_locations"],
        "required_fields": ["location_id"],
        "required_values": {},
        "minimum_history_years": 1,
        "proxy_allowed": False,
    }

    result = DataQualityEngine().execute(
        envelope(UC2), {"claims": [claim]}, sample_limit=24
    )
    assessed = result.profile["requirement_assessment"]["claims"][0]

    assert assessed["missing_fields"] == []
    assert assessed["ambiguous_fields"] == {
        "location_id": [
            "dist_ie_energy_raw.location_id",
            "dist_ie_locations.location_id",
        ]
    }
    assert assessed["proxy_required"] is True
    assert assessed["structurally_available"] is False


def test_wrong_qualified_field_reference_is_missing():
    """Qualified references to non-existent columns/tables are reported as missing."""
    claim = {
        "claim_id": "wrong_qualified_field",
        "claim": "Wrong qualified evidence",
        "core": True,
        # dist_ie_energy_raw IS a real table but "not_a_column" does not exist in it.
        # not_a_table doesn't exist at all.
        "required_tables": ["dist_ie_energy_raw"],
        "required_fields": [
            "dist_ie_energy_raw.not_a_column",
            "not_a_table.location_id",
        ],
        "required_values": {},
        "minimum_history_years": 1,
        "proxy_allowed": False,
    }

    result = DataQualityEngine().execute(
        envelope(UC2), {"claims": [claim]}, sample_limit=24
    )
    assessed = result.profile["requirement_assessment"]["claims"][0]

    assert assessed["missing_fields"] == [
        "dist_ie_energy_raw.not_a_column",
        "not_a_table.location_id",
    ]
    assert assessed["missing_tables"] == ["not_a_table"]
    assert assessed["resolved_fields"] == {}
    assert assessed["structurally_available"] is False


def test_qualified_required_value_reference_uses_the_named_table():
    """A qualified required_values entry resolves against the named real table."""
    claim = {
        "claim_id": "qualified_quarter_value",
        "claim": "Quarterly fiscal evidence",
        "core": True,
        "required_tables": ["dist_ie_energy_raw"],
        "required_fields": ["dist_ie_energy_raw.fiscal_quarter"],
        "required_values": {
            # Q1 is a real value in dist_ie_energy_raw.fiscal_quarter
            "dist_ie_energy_raw.fiscal_quarter": ["Q1"]
        },
        "minimum_history_years": 1,
        "proxy_allowed": False,
    }

    result = DataQualityEngine().execute(
        envelope(UC2), {"claims": [claim]}, sample_limit=24
    )
    assessed = result.profile["requirement_assessment"]["claims"][0]

    assert assessed["missing_fields"] == []
    assert assessed["missing_values"] == {}
    assert assessed["resolved_fields"] == {
        "dist_ie_energy_raw.fiscal_quarter": "dist_ie_energy_raw.fiscal_quarter"
    }
    assert assessed["structurally_available"] is True


def test_empty_required_values_entry_does_not_require_the_named_field():
    """An empty list in required_values is a no-op — not a blocking condition."""
    claim = {
        "claim_id": "empty_required_values",
        "claim": "Annual energy evidence",
        "core": True,
        "required_tables": ["dist_ie_energy_raw"],
        "required_fields": ["amount_consumed_mwh"],
        # Empty list for media_type means "no constraint", not "media_type must be missing"
        "required_values": {"media_type": []},
        "minimum_history_years": 1,
        "proxy_allowed": False,
    }

    result = DataQualityEngine().execute(
        envelope(UC2), {"claims": [claim]}, sample_limit=24
    )
    assessed = result.profile["requirement_assessment"]["claims"][0]

    assert "media_type" not in assessed["missing_fields"]
    assert assessed["missing_values"] == {}
    assert assessed["structurally_available"] is True
