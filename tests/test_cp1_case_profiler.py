from __future__ import annotations

import json

from epoch_switch.agents.orchestration.cp1_case_profiler import CaseProfilerAgent
from epoch_switch.core.envelope import CaseEnvelope
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.regulation_cli import _document_evidence_summary
from epoch_switch.usecases.registry import UC2


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.user = None

    def complete_json(self, system, user, **kwargs):
        self.user = json.loads(user)
        return self.response, 1


def envelope() -> CaseEnvelope:
    return CaseEnvelope.new(
        usecase_ref=UC2.id,
        natural_request=UC2.natural_request,
        regulation_refs=UC2.regulation_refs,
        regulation_sources=UC2.regulation_sources,
        site_filter=UC2.site_filter,
        time_window=UC2.time_window,
        expected_output_family=UC2.expected_output,
        regulation_profile={
            "findings": [
                {
                    "finding_id": "RF-001",
                    "requirement_type": "threshold",
                }
            ]
        },
    )


def response(**overrides):
    # UC2 is qualitative / structured_memo — use as the default output_profile
    result = {
        "task_type": "compliance_check",
        "risk_level": "high",
        "uncertainty": "low",
        "deadline_proximity_days": None,
        "assurance_artifact": "single_attestation",
        "dual_method_required": False,
        "cluster_count": 1,
        "assurance_level": "regulatory",
        "cross_functional_need": False,
        "output_profile": {
            "output_type": "qualitative",
            "form": "structured_memo",
            "description": "Interpretive compliance memo with article citations.",
        },
        "signal_sources": {
            "task_type": ["registry", "r2"],
            "risk_level": ["registry", "r2"],
            "uncertainty": ["r2"],
            "assurance_artifact": ["r2", "da1"],
            "dual_method_required": ["registry", "r2"],
            "cluster_count": ["r2", "da1"],
            "assurance_level": ["registry", "r2"],
            "cross_functional_need": ["registry", "r2"],
            "output_profile": ["registry"],
        },
        "limitations": ["No observed values were supplied."],
    }
    result.update(overrides)
    if result["deadline_proximity_days"] is not None:
        result["signal_sources"]["deadline_proximity_days"] = ["d1"]
    return result


def test_cp1_is_independent_and_accepts_approved_profile_context(tmp_path):
    fake = FakeLLM(response())
    agent = CaseProfilerAgent(fake)
    env = envelope()
    template = {
        "approved_scope": {
            "approved_field_ids": ["PF-001"],
            "approved_mappings": [{"field_id": "PF-001", "status": "mapped"}],
            "handoff_data_request": {"measures": ["energy_mwh"]},
        },
        "r2_in_scope_findings": [],
        "derived_case_facts": None,
    }

    message = agent.execute_preselection(
        env,
        EvidenceStore(tmp_path),
        approved_profile_context=template,
    )

    assert message.sender_agent == "CP1"
    assert env.task_type == "compliance_check"
    assert fake.user["approved_scope"] == template["approved_scope"]
    assert (tmp_path / "evidence_store.json").exists()


def test_cp1_passes_r2_in_scope_findings_to_llm(tmp_path):
    fake = FakeLLM(response())
    agent = CaseProfilerAgent(fake)
    findings = [
        {
            "r2_finding_id": "R2F-001",
            "statement": "Threshold obligation in scope.",
            "approved_field_ids": ["PF-001"],
        }
    ]
    context = {
        "approved_scope": {
            "approved_field_ids": ["PF-001"],
            "approved_mappings": [],
            "handoff_data_request": {},
        },
        "r2_in_scope_findings": findings,
        "derived_case_facts": None,
    }

    agent.execute_preselection(
        envelope(),
        EvidenceStore(tmp_path),
        approved_profile_context=context,
    )

    assert fake.user["r2_in_scope_findings"] == findings


def test_cp1_r2_signal_source_is_valid():
    """'r2' must be an accepted SignalSource label."""
    from epoch_switch.agents.orchestration.cp1_case_profiler.case_profile import (
        CaseProfile,
    )

    r2_response = dict(response())
    r2_response["signal_sources"]["task_type"] = ["registry", "r2"]

    profile = CaseProfile.model_validate(r2_response)
    assert "r2" in profile.signal_sources["task_type"]


def test_cp1_document_context_uses_rd3_summary_source(tmp_path) -> None:
    document_response = response()
    for signal in document_response["signal_sources"]:
        document_response["signal_sources"][signal] = ["rd3"]
    fake = FakeLLM(document_response)
    context = {
        "pipeline_family": "document",
        "demand": {"registry_id": "uc4", "output_profile": {}},
        "approved_scope": None,
        "r2_in_scope_findings": None,
        "document_evidence": {
            "standards": ["E1"],
            "rd3_summary": {
                "n_rows": 1,
                "n_mapping_uncertain": 0,
                "change_status_counts": {"Modified": 1},
                "reported_status_counts": {"reported": 1},
                "action_needed_counts": {"report_update_required": 1},
            },
        },
        "derived_case_facts": None,
        "data_quality": None,
    }

    agent = CaseProfilerAgent(fake)
    message = agent.execute_preselection(
        envelope(), EvidenceStore(tmp_path), approved_profile_context=context
    )

    assert fake.user["pipeline_family"] == "document"
    assert fake.user["approved_scope"] is None
    assert fake.user["document_evidence"] == context["document_evidence"]
    assert message.payload["signal_sources"]["task_type"] == ["rd3"]


def test_document_evidence_summary_preserves_complete_rd3_summary() -> None:
    summary = {
        "n_rows": 3,
        "n_orphan_2026": 1,
        "n_orphan_2025": 1,
        "n_new_report_required": 1,
        "n_report_rewrite_required": 0,
        "n_report_update_required": 0,
        "n_status_review_required": 1,
        "n_mapping_uncertain": 0,
        "change_status_counts": {"New": 1, "Retained": 2},
        "reported_status_counts": {"unclear": 1, "reported": 1, "": 1},
        "action_needed_counts": {"new_report_required": 1, "status_review_required": 1, "no_action": 1},
    }

    assert _document_evidence_summary({"summary": summary}) == {"rd3_summary": summary}

