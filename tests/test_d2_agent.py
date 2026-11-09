from __future__ import annotations

import pytest

from epoch_switch.agents.data.d2_missing_value import MissingValueAnalyst
from epoch_switch.agents.data.d2_missing_value.d2_missing_value import (
    RequirementPlan,
)
from epoch_switch.core.envelope import CaseEnvelope
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.usecases.registry import UC2


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, "kwargs": kwargs})
        return self.responses.pop(0), 1


def uc2_envelope() -> CaseEnvelope:
    return CaseEnvelope.new(
        usecase_ref=UC2.id,
        natural_request=UC2.natural_request,
        regulation_refs=UC2.regulation_refs,
        site_filter=UC2.site_filter,
        time_window=UC2.time_window,
        expected_output_family=UC2.expected_output_family,
        assessment_contract=UC2.assessment_contract.model_dump(),
        assurance_level="elevated",
    )


def requirement_plan():
    return {
        "claims": [
            {
                "claim_id": "enefg_8_applicability",
                "claim": "§8 threshold applicability",
                "core": True,
                "required_tables": ["dist_ie_energy_raw"],
                "required_fields": ["location_id", "fiscal_year", "fiscal_quarter",
                                    "amount_consumed_mwh"],
                "required_values": {},
                "required_grain": "site_year",
                "minimum_history_years": 1,
                "proxy_allowed": False,
            },
            {
                "claim_id": "enefg_16_applicability",
                "claim": "§16 threshold applicability",
                "core": True,
                "required_tables": ["dist_ie_energy_raw"],
                "required_fields": ["location_id", "fiscal_year", "fiscal_quarter",
                                    "amount_consumed_mwh"],
                "required_values": {},
                "required_grain": "site_year",
                "minimum_history_years": 0,
                "proxy_allowed": False,
            },
        ],
        "selected_checks": [
            "schema",
            "completeness",
            "duplicates",
            "consistency",
            "coverage",
            "energy_emissions_reconciliation",
        ],
        "sample_strata": ["normal", "missing", "inconsistent"],
        "planning_notes": [],
    }


def partial_verdict():
    return {
        "verdict": "partial",
        "evidence_completeness": 0.62,
        "max_severity": "high",
        "summary": "Threshold evidence is usable, but compliance evidence is absent.",
        "assessable_claims": ["enefg_8_applicability"],
        "blocked_claims": [
            {
                "claim_id": "enefg_16_applicability",
                "reason": "The annual threshold evidence is incomplete.",
                "missing_evidence": [
                    "complete annual energy evidence",
                ],
            }
        ],
        "observed_facts": [
            "The source-to-KPI profile contains site-weeks with masked missing components."
        ],
        "hypotheses": [
            {
                "issue_id": "dq_008",
                "probable_cause": "KPI derivation zero-fills missing source media rows.",
                "confidence": 0.95,
                "verification_step": "Inspect the KPI derivation transform.",
            }
        ],
        "remediation_actions": [
            {
                "issue_id": "dq_008",
                "immediate_containment": "Label affected KPI periods as incomplete.",
                "root_cause_check": "Review source null handling.",
                "permanent_fix": "Propagate completeness metadata into KPI rows.",
                "owner_role": "data_pipeline_owner",
                "verification_step": "Recompute and reconcile affected periods.",
            }
        ],
        "routing_recommendation": "restrict_claims",
    }


def test_d2_writes_structured_and_markdown_outputs(tmp_path):
    env = uc2_envelope()
    store = EvidenceStore(tmp_path)
    llm = FakeLLM([requirement_plan(), partial_verdict()])
    agent = MissingValueAnalyst(llm=llm)

    message = agent.execute("quality preflight", env, store)

    assert message.payload["verdict"] == "partial"
    assert message.confidence == 0.62
    assert len(llm.calls) == 2
    profile = store.read(f"data_quality_profile_{env.case_id}")
    assert profile["sample_manifest"]["selected_count"] == 60
    assert "samples" not in profile
    report = store.read(f"data_quality_report_{env.case_id}")
    assert "## Remediation Plan" in report
    assert "enefg_16_applicability" in report


def test_d2_reuses_preflight_without_calling_llm(tmp_path):
    env = uc2_envelope()
    store = EvidenceStore(tmp_path)
    store.write(f"data_quality_verdict_{env.case_id}", partial_verdict())
    llm = FakeLLM([])

    message = MissingValueAnalyst(llm=llm).execute("topology reuse", env, store)

    assert message.payload["preflight_reused"] is True
    assert llm.calls == []


def test_d2_repairs_one_invalid_llm_response(tmp_path):
    env = uc2_envelope()
    store = EvidenceStore(tmp_path)
    invalid_plan = {"claims": [], "selected_checks": []}
    llm = FakeLLM([invalid_plan, requirement_plan(), partial_verdict()])

    message = MissingValueAnalyst(llm=llm).execute("quality preflight", env, store)

    assert message.payload["verdict"] == "partial"
    assert len(llm.calls) == 3


# ── execute_from_scope (regulation CLI path) ────────────────────────────────


HANDOFF = {
    "source_domain": "energy",
    "entity_grain": "site",
    "time_grain": "year",
    "time_window": {"start": "2025-01-06", "end": "2025-12-29"},
    "filters": {"iso2_code": ["DE"]},
    "measures": ["energy_mwh", "co2_t"],
    "quality_policy": "exclude_inconsistent",
}

REGISTRY_ID = UC2.id


def scope_plan():
    """A requirement plan whose claim IDs match in_scope_findings below.

    claim_id must equal r2_finding_id verbatim (R2F-NNN format).
    """
    return {
        "claims": [
            {
                "claim_id": "R2F-001",
                "claim": "§8 applies to sites > 7500 MWh",
                "core": True,
                "required_tables": ["dist_ie_energy_raw"],
                "required_fields": [
                    "dist_ie_energy_raw.amount_consumed_mwh",
                    "dist_ie_energy_raw.location_id",
                ],
                "required_values": {},
                "required_grain": "site_year",
                "minimum_history_years": 1,
                "proxy_allowed": False,
                "scope_rationale": (
                    "The approved energy_mwh measure resolves to quarterly "
                    "amount_consumed_mwh and the approved site grain resolves to "
                    "location_id in dist_ie_energy_raw."
                ),
            },
        ],
        "selected_checks": ["schema", "completeness", "coverage"],
        "sample_strata": ["normal"],
        "planning_notes": [],
    }


def pass_verdict():
    return {
        "verdict": "pass",
        "evidence_completeness": 0.95,
        "max_severity": "info",
        "summary": "All evidence available.",
        "assessable_claims": ["R2F-001"],
        "blocked_claims": [],
        "observed_facts": ["Complete annual energy coverage for DE sites."],
        "hypotheses": [],
        "remediation_actions": [],
        "routing_recommendation": "normal",
    }


IN_SCOPE_FINDINGS = [
    {
        "r2_finding_id": "R2F-001",
        "statement": "§8 applies to sites > 7500 MWh",
        "assessment_boundary": "annual energy consumption",
    }
]


def test_execute_from_scope_keys_outputs_by_registry_id(tmp_path):
    env = uc2_envelope()
    store = EvidenceStore(tmp_path)
    llm = FakeLLM([scope_plan(), pass_verdict()])
    agent = MissingValueAnalyst(llm=llm)

    message = agent.execute_from_scope(
        env,
        store,
        registry_id=REGISTRY_ID,
        in_scope_findings=IN_SCOPE_FINDINGS,
        handoff_data_request=HANDOFF,
        normalized_data_request=HANDOFF,
        data_product={},
    )

    assert message.payload["verdict"] == "pass"
    # Evidence store keys must use registry_id, NOT case_id
    profile = store.read(f"data_quality_profile_{REGISTRY_ID}")
    assert profile is not None
    verdict_stored = store.read(f"data_quality_verdict_{REGISTRY_ID}")
    assert verdict_stored is not None
    # Must NOT store under case_id
    assert store.read(f"data_quality_verdict_{env.case_id}") is None
    planner_user = llm.calls[0]["user"]
    assert "APPROVED HANDOFF DATA REQUEST" in planner_user
    assert '"entity_grain":"site"' in planner_user
    assert '"measures":[' in planner_user
    assert '"energy_mwh"' in planner_user
    assert '"catalog_version":"0.2"' in planner_user
    assert '"measure_definitions"' in planner_user
    assert '"executor_capabilities"' in planner_user
    assert '"schema_signature"' in planner_user

    adjudicator_user = llm.calls[1]["user"]
    assert "R2 IN-SCOPE FINDINGS (decision universe)" in adjudicator_user
    assert "APPROVED HANDOFF DATA REQUEST (decision universe)" in adjudicator_user
    assert "DECISION-EVIDENCE REFERENCES" in adjudicator_user
    assert '"fact_allowance_surrender"' not in adjudicator_user.split(
        "DECISION-EVIDENCE REFERENCES", 1
    )[1].split("REQUIREMENT PLAN", 1)[0]
    assert '"schema_signature"' in adjudicator_user
    assert '"sample_manifest"' in adjudicator_user
    assert llm.calls[0]["kwargs"]["model"]
    assert llm.calls[1]["kwargs"]["model"]


def test_execute_from_scope_reuses_cached_verdict(tmp_path):
    env = uc2_envelope()
    store = EvidenceStore(tmp_path)
    store.write(f"data_quality_verdict_{REGISTRY_ID}", pass_verdict())
    llm = FakeLLM([])

    message = MissingValueAnalyst(llm=llm).execute_from_scope(
        env,
        store,
        registry_id=REGISTRY_ID,
        in_scope_findings=IN_SCOPE_FINDINGS,
        handoff_data_request=HANDOFF,
        normalized_data_request=HANDOFF,
        data_product={},
    )

    assert message.payload["preflight_reused"] is True
    assert llm.calls == []


def test_execute_from_scope_includes_cp1_signal(tmp_path):
    env = uc2_envelope()
    store = EvidenceStore(tmp_path)
    llm = FakeLLM([scope_plan(), pass_verdict()])

    message = MissingValueAnalyst(llm=llm).execute_from_scope(
        env,
        store,
        registry_id=REGISTRY_ID,
        in_scope_findings=IN_SCOPE_FINDINGS,
        handoff_data_request=HANDOFF,
        normalized_data_request=HANDOFF,
        data_product={},
    )

    signal = message.payload.get("cp1_signal")
    assert signal is not None
    assert signal["evidence_assessable"] is True
    assert signal["evidence_completeness"] == 0.95
    assert signal["routing_recommendation"] == "normal"


def test_execute_from_scope_empty_findings_is_advisory(tmp_path):
    """Empty in_scope_findings → advisory mode: validation skipped, no raise."""
    env = uc2_envelope()
    store = EvidenceStore(tmp_path)
    # Plan claims an ID not in any findings — OK in advisory mode
    advisory_plan = {
        "claims": [
            {
                "claim_id": "arbitrary-claim",
                "claim": "Some claim",
                "core": True,
                "required_tables": [],
                "required_fields": [],
                "required_values": {},
                "minimum_history_years": 0,
                "proxy_allowed": False,
            }
        ],
        "selected_checks": ["schema"],
        "sample_strata": [],
        "planning_notes": [],
    }
    advisory_verdict = {
        **pass_verdict(),
        "assessable_claims": ["arbitrary-claim"],
    }
    llm = FakeLLM([advisory_plan, advisory_verdict])

    message = MissingValueAnalyst(llm=llm).execute_from_scope(
        env,
        store,
        registry_id=REGISTRY_ID,
        in_scope_findings=[],  # advisory mode
        handoff_data_request=HANDOFF,
        normalized_data_request=HANDOFF,
        data_product={},
    )
    # Should succeed without raising
    assert message.payload["verdict"] == "pass"


def test_execute_from_scope_stop_verdict_has_correct_routing(tmp_path):
    env = uc2_envelope()
    store = EvidenceStore(tmp_path)
    stop_verdict = {
        "verdict": "insufficient",
        "evidence_completeness": 0.10,
        "max_severity": "critical",
        "summary": "Critical data gaps; evidence is unusable.",
        "assessable_claims": [],
        "blocked_claims": [
            {
                "claim_id": "R2F-001",
                "reason": "No energy data available for DE sites.",
                "missing_evidence": ["annual energy coverage"],
            }
        ],
        "observed_facts": ["0 site-weeks in DE scope."],
        "hypotheses": [],
        "remediation_actions": [],
        "routing_recommendation": "stop",
    }
    llm = FakeLLM([scope_plan(), stop_verdict])

    message = MissingValueAnalyst(llm=llm).execute_from_scope(
        env,
        store,
        registry_id=REGISTRY_ID,
        in_scope_findings=IN_SCOPE_FINDINGS,
        handoff_data_request=HANDOFF,
        normalized_data_request=HANDOFF,
        data_product={},
    )

    assert message.payload["verdict"] == "insufficient"
    assert message.payload["routing_recommendation"] == "stop"
    signal = message.payload["cp1_signal"]
    assert signal["evidence_assessable"] is False
    assert signal["routing_recommendation"] == "stop"


def test_adjudicator_prompt_keeps_complete_profile_and_sample_payloads():
    env = uc2_envelope()
    llm = FakeLLM([pass_verdict()])
    agent = MissingValueAnalyst(llm=llm)
    profile = {
        "large_profile_block": "p" * 30000,
        "profile_tail_marker": "PROFILE_END",
    }
    samples = [
        {"large_sample_block": "s" * 22000},
        {"sample_tail_marker": "SAMPLE_END"},
    ]

    verdict = agent._adjudicate(
        env,
        RequirementPlan.model_validate(scope_plan()),
        profile,
        samples,
        catalog={"catalog_version": "0.2", "catalog_tail_marker": "CATALOG_END"},
        in_scope_findings=IN_SCOPE_FINDINGS,
        handoff_data_request=HANDOFF,
    )

    assert verdict.verdict == "pass"
    user = llm.calls[0]["user"]
    assert "CATALOG_END" in user
    assert "PROFILE_END" in user
    assert "SAMPLE_END" in user
