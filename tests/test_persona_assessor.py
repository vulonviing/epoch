"""Phase 8: PersonaAssessorAgent -- one class, three agent_id instances."""
from __future__ import annotations

import json

import pytest

from epoch_switch.agents.output.persona_assessor import PersonaAssessorAgent
from epoch_switch.agents.output.persona_assessor.persona_result import PersonaResult, PersonaVerdict

JOIN_ROWS = [
    {
        "standard": "E1",
        "old_dr_id": "E1-5",
        "old_dr_title": "Energy consumption and mix",
        "new_dr_ids": ["E1-7"],
        "new_dr_titles": ["Energy consumption and mix"],
        "change_status": "Renumbered",
        "change_description": "test",
        "old_paragraph_refs": [],
        "new_paragraph_refs": [],
        "citations": [],
        "mapping_uncertain": False,
        "reported_status": "reported",
        "report_section_refs": ["2.2.6"],
        "materiality_note": "",
        "omission_note": "",
        "evidence_quotes": ["Siemens reports its energy consumption."],
        "orphan_side": "none",
        "action_needed": "report_update_required",
    },
    {
        "standard": "E1",
        "old_dr_id": "",
        "old_dr_title": "",
        "new_dr_ids": ["E1-2"],
        "new_dr_titles": ["Identification of climate-related risks and scenario analysis"],
        "change_status": "New",
        "change_description": "test",
        "old_paragraph_refs": [],
        "new_paragraph_refs": [],
        "citations": [],
        "mapping_uncertain": False,
        "reported_status": "",
        "report_section_refs": [],
        "materiality_note": "",
        "omission_note": "",
        "evidence_quotes": [],
        "orphan_side": "no_2025_origin",
        "action_needed": "new_report_required",
    },
]


class FakeLLM:
    def __init__(self):
        self.calls = []

    def complete_json(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, "kwargs": kwargs})
        payload = json.loads(user)
        rows = [
            {
                "character_id": payload["character_id"],
                "verdict_kind": "primary",
                "standard": payload["standard"],
                "old_dr_ids": [f["old_dr_id"]] if f["old_dr_id"] else [],
                "new_dr_ids": f["new_dr_ids"],
                "recommended_action": "test action",
                "effort_estimate": "low",
                "effort_rationale": "test rationale",
                "risk_posture_note": "test",
                "rationale": "test",
                "citations": [],
                "siemens_evidence_quotes": f.get("evidence_quotes", [])[:1],
                "confidence": 0.6,
                "action_needed": f["action_needed"],
                "action_priority": "required",
            }
            for f in payload["findings"]
        ]
        return {"rows": rows}, 1


def test_rejects_unknown_character_id() -> None:
    with pytest.raises(ValueError):
        PersonaAssessorAgent("P9", llm=FakeLLM())


@pytest.mark.parametrize("character_id", ["P4", "P5", "P6"])
def test_each_character_loads_its_own_prompt_and_stamps_its_id(character_id: str) -> None:
    agent = PersonaAssessorAgent(character_id, llm=FakeLLM())
    assert agent.role_prompt_path.name == f"{character_id}.md"
    result = agent.assess(registry_id="uc4_esrs_2026_impact", join_rows=JOIN_ROWS)
    assert isinstance(result, PersonaResult)
    assert result.character_id == character_id
    assert len(result.rows) == len(JOIN_ROWS)
    assert all(row.character_id == character_id for row in result.rows)


def test_new_dr_finding_gets_empty_exposure_not_a_crash() -> None:
    fake = FakeLLM()
    agent = PersonaAssessorAgent("P5", llm=fake)
    agent.assess(registry_id="uc4_esrs_2026_impact", join_rows=JOIN_ROWS)
    payload = json.loads(fake.calls[0]["user"])
    new_dr_finding = next(f for f in payload["findings"] if f["change_status"] == "New")
    assert new_dr_finding["reported_status"] == ""


def test_persona_schema_supports_linked_supplemental_quote() -> None:
    row = PersonaVerdict(
        character_id="P6",
        verdict_kind="supplemental",
        standard="E1",
        old_dr_ids=["E1-5"],
        new_dr_ids=["E1-7"],
        recommended_action="Retain an assurance note.",
        effort_estimate="low",
        effort_rationale="test rationale",
        risk_posture_note="P6-only concern.",
        rationale="Grounded in the approved RM1 evidence.",
        siemens_evidence_quotes=["Siemens reports its energy consumption."],
        confidence=0.7,
    )
    assert row.verdict_kind == "supplemental"
    assert row.siemens_evidence_quotes
