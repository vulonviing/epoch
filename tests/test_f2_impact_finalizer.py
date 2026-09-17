"""Phase 9: F2 -- final reconciliation of RC1 + RM1 + three persona reads."""
from __future__ import annotations

import json

from epoch_switch.agents.output.f2_impact_finalizer import ImpactFinalizerAgent
from epoch_switch.agents.output.f2_impact_finalizer.f2_result import (
    ESRSImpactReport,
    ImpactRow,
    build_standard_summary,
)

JOIN_ROWS = [
    {
        "standard": "E1",
        "old_dr_id": "E1-5",
        "old_dr_title": "Energy consumption and mix",
        "new_dr_ids": ["E1-7"],
        "new_dr_titles": ["Energy consumption and mix"],
        "change_status": "Renumbered",
        "change_description": "test",
        "old_paragraph_refs": ["35."],
        "new_paragraph_refs": ["25."],
        "citations": ["ESRS_E1_2025_amended.md E1-5"],
        "mapping_uncertain": False,
        "reported_status": "reported",
        "report_section_refs": ["2.2.6"],
        "materiality_note": "",
        "omission_note": "",
        "evidence_quotes": [],
        "orphan_side": "none",
        "action_needed": "report_update_required",
    }
]
PERSONA_RESULTS = {
    "P4": [{"character_id": "P4", "recommended_action": "no change needed"}],
    "P5": [{"character_id": "P5", "recommended_action": "light edit"}],
    "P6": [
        {"character_id": "P6", "verdict_kind": "primary", "recommended_action": "expand disclosure"},
        {
            "character_id": "P6",
            "verdict_kind": "supplemental",
            "old_dr_ids": ["E1-5"],
            "new_dr_ids": ["E1-7"],
            "recommended_action": "retain a separate assurance note",
            "siemens_evidence_quotes": ["Siemens report evidence"],
        },
    ],
}


class FakeLLM:
    def __init__(self):
        self.calls = []

    def complete_json(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, "kwargs": kwargs})
        payload = json.loads(user)
        rows = [
            {
                "standard": c["standard"],
                "dr_id_2025": c["old_dr_id"],
                "requirement_2025": c["old_dr_title"],
                "dr_id_2026": (c["new_dr_ids"] or [""])[0],
                "requirement_2026": (c["new_dr_titles"] or [""])[0],
                "change_status": c["change_status"],
                "change_description": c["change_description"],
                "source_2025": c["old_paragraph_refs"],
                "source_2026": c["new_paragraph_refs"],
                "siemens_reported": "reported",
                "siemens_source_ref": ["2.2.6"],
                "siemens_impact": "personas diverge: P4 vs P6",
                "confidence": 0.65,
                "confidence_note": "Personas disagree on the reporting response.",
                "action_needed": c["action_needed"],
                "agreed_priority": "required",
                "agreed_effort": "medium",
                "effort_note": "Personas converge on a bounded update.",
            }
            for c in payload["change_exposure_join"]
        ]
        return {
            "rows": rows,
            "executive_summary": ["test summary"],
            "top_impacts": [],
            "persona_divergence": ["E1-5: P4 says no change, P6 says expand"],
            "limitations": ["helper baseline mismatch"],
        }, 1


def test_f2_produces_one_row_per_rc1_finding_and_surfaces_divergence() -> None:
    agent = ImpactFinalizerAgent(llm=FakeLLM())
    result = agent.finalize(
        registry_id="uc4_esrs_2026_impact",
        join_rows=JOIN_ROWS,
        persona_results=PERSONA_RESULTS,
        demand={"natural_request": "test", "output_profile": {"form": "structured_memo"}},
    )
    assert isinstance(result, ESRSImpactReport)
    assert len(result.rows) == len(JOIN_ROWS)
    assert result.rows[0].dr_id_2025 == "E1-5"
    assert result.rows[0].dr_id_2026 == "E1-7"
    assert len(result.persona_divergence) == 1
    assert result.rows[0].confidence == 0.65
    assert result.rows[0].confidence_note
    sent = json.loads(agent._llm.calls[0]["user"])
    assert len(sent["persona_assessments"]["P6"]) == 2
    assert len(result.standard_summary) == 1
    assert result.standard_summary[0].standard == "E1"
    assert result.standard_summary[0].change_required is True


def _row(standard: str, action_needed: str, agreed_priority: str = "not_required") -> ImpactRow:
    return ImpactRow(
        standard=standard,
        change_status="Modified",
        change_description="test",
        confidence=0.5,
        confidence_note="test",
        action_needed=action_needed,
        agreed_priority=agreed_priority,
    )


def test_build_standard_summary_rolls_up_per_standard() -> None:
    rows = [
        _row("E1", "report_update_required", agreed_priority="required"),
        _row("E1", "no_action"),
        _row("E1", "new_report_required", agreed_priority="required_urgent"),
        _row("E2", "no_action"),
        _row("E2", "no_action"),
    ]
    summary = build_standard_summary(rows)
    assert [s.standard for s in summary] == ["E1", "E2"]

    e1 = summary[0]
    assert e1.change_required is True
    assert e1.n_rows_action_required == 2
    assert e1.n_rows_total == 3
    assert e1.highest_priority == "required_urgent"

    e2 = summary[1]
    assert e2.change_required is False
    assert e2.n_rows_action_required == 0
    assert e2.n_rows_total == 2
    assert e2.highest_priority == ""
