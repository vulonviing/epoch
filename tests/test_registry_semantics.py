from __future__ import annotations

from epoch_switch.usecases.registry import UC1, UC2, UC3, UC4


def test_registry_contains_requests_not_field_answer_keys():
    for seed in (UC1, UC2, UC3):
        dumped = seed.model_dump()
        assert set(dumped) == {
            "id",
            "natural_request",
            "regulation_refs",
            "regulation_sources",
            "site_filter",
            "time_window",
            "time_grain",
            "expected_output",
            "output_profile",
            "assurance_profile",
            "expected_topology",
            "threshold_parameters",
            "reduction_target",
            "computation_spec",
            "pipeline_family",
            "document_sources",
        }
        assert "assessment_contract" not in dumped


def test_uc1_keeps_user_request_without_prescribing_possible_fields():
    """UC1 (EnEfG Binary) carries threshold values — not field answer keys."""
    assert "7,500 MWh/yr" in UC1.natural_request
    assert "2,500 MWh/yr" in UC1.natural_request
    registry_json = UC1.model_dump_json()
    # Registry must not prescribe which operational fields R1 should discover.
    assert "annual_total_final_energy_consumption" not in registry_json
    assert "waste_heat_quantity" not in registry_json
    assert "co2_t" not in registry_json


def test_reduction_target_absent_for_all_active_usecases():
    """No active use case carries a reduction_target (KSG/trajectory use case removed)."""
    for seed in (UC1, UC2, UC3):
        assert seed.reduction_target is None, (
            f"{seed.id} should have reduction_target=None"
        )


def test_output_and_assurance_profiles_present_on_all_seeds():
    """Every seed must declare output_profile and assurance_profile (pi selector inputs)."""
    valid_topologies = {"Direct", "Debate", "Coalition"}
    for seed in (UC1, UC2, UC3):
        assert seed.output_profile is not None, f"{seed.id} missing output_profile"
        assert seed.assurance_profile is not None, f"{seed.id} missing assurance_profile"
        # expected_topology is eval anchor only — must be in the topology library Λ
        assert seed.expected_topology in valid_topologies, (
            f"{seed.id}: expected_topology '{seed.expected_topology}' not in Λ"
        )


def test_topology_library_covers_all_three_output_types():
    """One use case per output type; each maps to a distinct topology."""
    assert UC1.output_profile.output_type == "binary"
    assert UC1.expected_topology == "Direct"

    assert UC2.output_profile.output_type == "qualitative"
    assert UC2.expected_topology == "Debate"

    assert UC3.output_profile.output_type == "quantitative"
    assert UC3.expected_topology == "Coalition"


def test_assurance_levels_align_with_measurement_taxonomy():
    """Assurance levels follow the taxonomy: inform_only < law_ref < independently_verifiable."""
    assert UC1.assurance_profile.level == "inform_only"
    assert UC1.assurance_profile.envelope_assurance_level == "routine"

    assert UC2.assurance_profile.level == "law_reference_required"
    assert UC2.assurance_profile.law_reference_required is True
    assert UC2.assurance_profile.envelope_assurance_level == "elevated"

    assert UC3.assurance_profile.level == "independently_verifiable"
    assert UC3.assurance_profile.dual_method_required is False  # UC3 uses divisional attestation, not dual-method
    assert UC3.assurance_profile.human_signoff_required is True
    assert UC3.assurance_profile.envelope_assurance_level == "regulatory"

    assert UC4.assurance_profile.dual_method_required is False
    assert UC4.expected_topology == "Debate"
