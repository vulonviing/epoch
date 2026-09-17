from __future__ import annotations

from copy import deepcopy

import pytest

from epoch_switch.agents.regulation.r2_scope_reviewer.r2_scope_reviewer import (
    RegulationScopeReviewer,
)


def r1_profile():
    return {
        "findings": [
            {
                "finding_id": "RF-001",
                "requirement_type": "threshold",
                "statement": "A threshold applies.",
                "source_ref": "REG-2",
                "source_file": "EnEfG.pdf",
                "page": 8,
                "evidence_excerpt": "more than 7.5 GWh",
            },
            {
                "finding_id": "RF-002",
                "requirement_type": "obligation",
                "statement": "Waste heat must be reused.",
                "source_ref": "REG-2",
                "source_file": "EnEfG.pdf",
                "page": 11,
                "evidence_excerpt": "reuse waste heat",
            },
        ],
        "possible_fields": [
            {
                "field_id": "PF-001",
                "role": "measure",
                "finding_refs": ["RF-001"],
            },
            {
                "field_id": "PF-002",
                "role": "measure",
                "finding_refs": ["RF-002"],
            },
        ],
    }


def approved_scope():
    return {
        "registry_id": "uc2_enefg_compliance",
        "approved_field_ids": ["PF-001"],
        "approved_mappings": [{"field_id": "PF-001"}],
        "human_decisions": [
            {"field_id": "PF-001", "decision": "approved"},
            {"field_id": "PF-002", "decision": "unresolved"},
        ],
        "handoff_data_request": {"approved_field_ids": ["PF-001"]},
    }


def valid_output():
    return {
        "schema_version": "1",
        "registry_id": "uc2_enefg_compliance",
        "summary": "Threshold applicability is assessable.",
        "in_scope_findings": [
            {
                "r2_finding_id": "R2F-001",
                "statement": "Assess the approved energy value against the threshold.",
                "assessment_boundary": "Applicability only; implementation is not assessed.",
                "requirement_type": "threshold",
                "r1_finding_refs": ["RF-001"],
                "approved_field_ids": ["PF-001"],
                "citations": [
                    {
                        "source_ref": "REG-2",
                        "source_file": "EnEfG.pdf",
                        "page": 8,
                        "evidence_excerpt": "more than 7.5 GWh",
                    }
                ],
            }
        ],
        "blocked_findings": [
            {
                "r1_finding_id": "RF-002",
                "reason": "The required waste-heat evidence is unresolved.",
                "related_field_ids": ["PF-002"],
            }
        ],
        "excluded_findings": [],
        "limitations": ["Compliance implementation cannot be assessed."],
    }


def validate(output):
    return RegulationScopeReviewer._validate(
        output,
        registry_snapshot={"id": "uc2_enefg_compliance"},
        r1_profile=r1_profile(),
        approved_mapping_scope=approved_scope(),
    )


def test_r2_validates_complete_partition_and_grounding():
    result = validate(valid_output())
    assert result["in_scope_findings"][0]["r1_finding_refs"] == ["RF-001"]
    assert result["blocked_findings"][0]["r1_finding_id"] == "RF-002"
    assert result["blocked_findings"][0]["related_field_ids"] == ["PF-002"]


def test_r2_turns_unclassified_r1_finding_into_blocked_warning():
    output = valid_output()
    output["blocked_findings"] = []
    result = validate(output)
    assert result["blocked_findings"][0]["r1_finding_id"] == "RF-002"
    assert "human review is required" in result["blocked_findings"][0]["reason"]


def test_r2_rejects_unapproved_field():
    output = valid_output()
    output["in_scope_findings"][0]["approved_field_ids"] = ["PF-002"]
    with pytest.raises(ValueError, match="unapproved fields"):
        validate(output)


def test_r2_normalizes_modified_citation_from_r1():
    output = deepcopy(valid_output())
    output["in_scope_findings"][0]["citations"][0]["page"] = 9
    result = validate(output)
    assert result["in_scope_findings"][0]["citations"][0]["page"] == 8


def test_r2_allows_partial_in_scope_and_blocked_finding():
    output = valid_output()
    output["in_scope_findings"][0]["r1_finding_refs"].append("RF-002")
    output["in_scope_findings"][0]["citations"].append(
        {
            "source_ref": "REG-2",
            "source_file": "EnEfG.pdf",
            "page": 11,
            "evidence_excerpt": "reuse waste heat",
        }
    )

    result = validate(output)

    assert result["blocked_findings"][0]["r1_finding_id"] == "RF-002"


def test_r2_allows_partial_in_scope_and_excluded_finding():
    output = valid_output()
    output["excluded_findings"] = [
        {
            "r1_finding_id": "RF-001",
            "reason": "A separate part of the bundled finding is out of scope.",
            "related_field_ids": ["PF-001"],
        }
    ]

    result = validate(output)

    assert result["excluded_findings"][0]["r1_finding_id"] == "RF-001"


def test_r2_scope_evidence_matrix_is_deterministic():
    matrix = RegulationScopeReviewer._scope_evidence_matrix(
        r1_profile(), approved_scope()
    )

    assert matrix == [
        {
            "r1_finding_id": "RF-001",
            "approved_linked_field_ids": ["PF-001"],
            "unapproved_linked_field_ids": [],
        },
        {
            "r1_finding_id": "RF-002",
            "approved_linked_field_ids": [],
            "unapproved_linked_field_ids": ["PF-002"],
        },
    ]



