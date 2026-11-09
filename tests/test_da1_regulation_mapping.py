from __future__ import annotations

from epoch_switch.core.data_catalog import (
    DataCatalogBuilder,
)
from epoch_switch.agents.data.da1_request_planner.da1_data_request_planner import (
    DataRequestPlanner,
    normalize_mapping_report,
)
from epoch_switch.agents.data.da1_request_planner import da1_data_request_planner
from epoch_switch.regulation_cli import build_envelope
from epoch_switch.usecases.registry import UC2


def envelope_with_fields():
    envelope = build_envelope(UC2)
    envelope.regulation_profile = {
        "possible_fields": [
            {
                "field_id": "PF-001",
                "name": "annual_energy",
                "role": "measure",
                "priority": "core",
            },
            {
                "field_id": "PF-002",
                "name": "waste_heat_temperature",
                "role": "measure",
                "priority": "related",
            },
        ]
    }
    return envelope


def test_da1_reports_mapped_and_unmapped_without_failing():
    report = normalize_mapping_report(
        {
            "summary": "Mapping complete.",
            "field_mappings": [
                {
                    "field_id": "PF-001",
                    "catalog_targets": [
                        {"kind": "measure", "name": "energy_mwh"}
                    ],
                    "reason": "Annual energy is available.",
                },
                {
                    "field_id": "PF-002",
                    "catalog_targets": [],
                    "reason": "No thermal measure exists.",
                },
            ],
            "suggested_request": {
                "source_domain": "energy",
                "entity_grain": "site",
                "time_grain": "year",
                "measures": ["energy_mwh"],
                "filters": {"iso2_code": ["DE"]},
            },
        },
        envelope_with_fields(),
        DataCatalogBuilder().build(),
    )
    assert report["validation"]["ok"] is True
    assert [item["status"] for item in report["field_mappings"]] == [
        "mapped",
        "unmapped",
    ]
    assert report["validation"]["warnings"]


def test_da1_llm_receives_legacy_catalog_view(monkeypatch):
    class FakeLLM:
        def __init__(self):
            self.user = ""

        def complete_json(self, _system, user, **_kwargs):
            self.user = user
            return {
                "summary": "Mapped.",
                "field_mappings": [
                    {
                        "field_id": "PF-001",
                        "catalog_targets": [
                            {"kind": "measure", "name": "energy_mwh"}
                        ],
                        "reason": "Available.",
                    },
                    {
                        "field_id": "PF-002",
                        "catalog_targets": [],
                        "reason": "Unavailable.",
                    },
                ],
                "suggested_request": {
                    "source_domain": "energy",
                    "entity_grain": "site",
                    "time_grain": "year",
                    "measures": ["energy_mwh"],
                    "filters": {"iso2_code": ["DE"]},
                },
            }, 1

    fake = FakeLLM()
    monkeypatch.setattr(da1_data_request_planner, "llm_for_agent", lambda agent: (fake, "test-model"))

    DataRequestPlanner().plan(
        envelope=envelope_with_fields(),
        catalog=DataCatalogBuilder().build(),
        capsule="test",
    )

    assert '"catalog_version": "0.1"' in fake.user
    assert '"measure_definitions"' in fake.user
    assert '"source_domains"' in fake.user
    assert '"field_index"' not in fake.user
    assert '"executor_capabilities"' not in fake.user
    # requestable flag must be visible to DA1 (via the measures key in legacy view).
    assert '"requestable"' in fake.user


def test_da1_rejects_only_targets_that_do_not_exist_in_catalog():
    report = normalize_mapping_report(
        {
            "field_mappings": [
                {
                    "field_id": "PF-001",
                    "catalog_targets": [
                        {"kind": "measure", "name": "invented_measure"}
                    ],
                }
            ]
        },
        envelope_with_fields(),
        DataCatalogBuilder().build(),
    )
    assert report["validation"]["ok"] is False
    # core field with zero valid primary targets → hard error
    err = report["validation"]["errors"][0]
    assert "PF-001" in err and ("all primary catalog targets are invalid" in err or "do not exist" in err)


def test_da1_does_not_add_fields_not_proposed_by_r1():
    report = normalize_mapping_report(
        {
            "field_mappings": [
                {"field_id": "PF-999", "catalog_targets": []}
            ]
        },
        envelope_with_fields(),
        DataCatalogBuilder().build(),
    )
    assert report["validation"]["ok"] is False
    assert "unknown possible field IDs" in report["validation"]["errors"][0]


def envelope_with_three_fields():
    """Envelope with three fields so we can test duplicate-measure detection."""
    envelope = build_envelope(UC2)
    envelope.regulation_profile = {
        "possible_fields": [
            {"field_id": "PF-001", "name": "monthly_emissions", "role": "measure", "priority": "core"},
            {"field_id": "PF-002", "name": "cumulative_emissions", "role": "measure", "priority": "core"},
            {"field_id": "PF-003", "name": "clean_electricity", "role": "measure", "priority": "related"},
        ]
    }
    return envelope


def test_duplicate_measure_target_without_filter_raises_warning():
    """Two fields both mapping to measure:co2_t with no filter → duplicate warning."""
    catalog = DataCatalogBuilder().build()
    report = normalize_mapping_report(
        {
            "summary": "Duplicate test.",
            "field_mappings": [
                {
                    "field_id": "PF-001",
                    "catalog_targets": [{"kind": "measure", "name": "co2_t"}],
                    "reason": "Monthly CO2.",
                },
                {
                    "field_id": "PF-002",
                    "catalog_targets": [{"kind": "measure", "name": "co2_t"}],
                    "reason": "Cumulative CO2.",
                },
                {
                    "field_id": "PF-003",
                    "catalog_targets": [],
                    "reason": "Unmapped.",
                },
            ],
            "suggested_request": {
                "source_domain": "energy",
                "entity_grain": "site",
                "time_grain": "year",
                "measures": ["co2_t"],
            },
        },
        envelope_with_three_fields(),
        catalog,
    )
    dup_warnings = [w for w in report["validation"]["warnings"] if "Duplicate measure" in w]
    assert dup_warnings, f"Expected a duplicate-measure warning, got: {report['validation']['warnings']}"
    assert "PF-001" in dup_warnings[0]
    assert "PF-002" in dup_warnings[0]


def test_same_measure_with_different_filters_is_not_a_duplicate():
    """Two fields mapping to co2_t but with different filters are NOT duplicates."""
    catalog = DataCatalogBuilder().build()
    report = normalize_mapping_report(
        {
            "summary": "Filtered variants test.",
            "field_mappings": [
                {
                    "field_id": "PF-001",
                    "catalog_targets": [
                        {
                            "kind": "measure",
                            "name": "co2_t",
                            "filters": [{"column": "media_name", "op": "==", "value": "Electricity"}],
                        }
                    ],
                    "reason": "CO2 from electricity only.",
                },
                {
                    "field_id": "PF-002",
                    "catalog_targets": [
                        {
                            "kind": "measure",
                            "name": "co2_t",
                            "filters": [{"column": "media_name", "op": "==", "value": "Natural Gas"}],
                        }
                    ],
                    "reason": "CO2 from natural gas only.",
                },
                {
                    "field_id": "PF-003",
                    "catalog_targets": [],
                    "reason": "Unmapped.",
                },
            ],
            "suggested_request": {
                "source_domain": "energy",
                "entity_grain": "site",
                "time_grain": "year",
                "measures": ["co2_t"],
            },
        },
        envelope_with_three_fields(),
        catalog,
    )
    dup_warnings = [w for w in report["validation"]["warnings"] if "Duplicate measure" in w]
    assert not dup_warnings, (
        f"Filtered variants wrongly flagged as duplicates: {dup_warnings}"
    )
