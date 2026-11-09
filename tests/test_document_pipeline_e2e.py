"""Phase 10+: end-to-end smoke test for build_document_pipeline (UC4), fully
mocked (no live LLM) -- exercises RC1.1 -> RC1.2 -> RM1.1 -> RM1.2 -> RD3 ->
[gate] -> CP1 -> TS1 -> P4/P5/P6 -> F2 -> [gate] and asserts every shelf ends
up promoted & linked, including the X.1 -> X.2 provenance chain.
"""
from __future__ import annotations

import json

from epoch_switch import config
from epoch_switch.agents.orchestration.cp1_case_profiler import CaseProfilerAgent
from epoch_switch.agents.orchestration.topology_selector import TopologySelectorAgent
from epoch_switch.agents.output.f2_impact_finalizer import ImpactFinalizerAgent
from epoch_switch.agents.output.f2_impact_finalizer import f2_profile_store
from epoch_switch.agents.output.persona_assessor import PersonaAssessorAgent
from epoch_switch.agents.output.persona_assessor import persona_profile_store
from epoch_switch.agents.regulation.rc1_change_classifier import BlindChangeMatcherAgent, ChangeClassifierAgent
from epoch_switch.agents.regulation.rc1_change_classifier import rc1_1_profile_store, rc1_profile_store
from epoch_switch.agents.regulation.rm1_exposure_mapper import BlindExposureMatcherAgent, ExposureMapperAgent
from epoch_switch.agents.regulation.rm1_exposure_mapper import rm1_1_profile_store, rm1_profile_store
from epoch_switch.agents.regulation.rd3_join import rd3_profile_store
from epoch_switch.agents.orchestration.cp1_case_profiler import cp1_profile_store
from epoch_switch.agents.orchestration.topology_selector import topology_selector_store
from epoch_switch.regulation_cli import build_document_pipeline, load_document_active_set
from epoch_switch.usecases.registry import UC4

DOC_SOURCES = {
    "baseline_dir": "regulations/esrs/baseline_2025_amended",
    "target_dir": "regulations/esrs/2026",
    "helper_dir": "regulations/comparison_helpers",
    "company_report_dir": "data/siemens_sustainability_2025",
    "standards": ["E1"],  # restrict to one standard to keep the smoke test fast
}


class RC11FakeLLM:
    """RC1.1 blind matcher -- never sees RD2's candidates."""

    def complete_json(self, system, user, **kwargs):
        payload = json.loads(user)
        rows = [
            {
                "standard": payload["standard"], "old_dr_id": dr["dr_id"], "new_dr_ids": [],
                "basis": "test", "match_uncertain": False, "confidence": 0.6,
            }
            for dr in payload["old_drs"]
        ]
        return {"rows": rows}, 1


class RC12FakeLLM:
    """RC1.2 -- sees RC1.1's blind rows plus RD2's deterministic candidates."""

    def complete_json(self, system, user, **kwargs):
        payload = json.loads(user)
        rows = []
        for c in payload["deterministic_candidates"]:
            old_ids = c["old_dr_ids"]
            new_ids = c["new_dr_ids"]
            status = "Removed" if not new_ids else ("New" if not old_ids else "Retained")
            rows.append({
                "standard": payload["standard"], "old_dr_ids": old_ids,
                "old_dr_titles": [], "new_dr_ids": new_ids, "new_dr_titles": [],
                "change_status": status, "change_description": "test",
                "old_paragraph_refs": [], "new_paragraph_refs": [],
                "mapping_uncertain": False, "citations": [], "confidence": 0.8,
                "blind_agreement": "agree", "deterministic_agreement": "agree", "divergence_note": "",
            })
        return {"rows": rows}, 1


class RM11FakeLLM:
    """RM1.1 blind exposure matcher -- never sees the deterministic index."""

    def complete_json(self, system, user, **kwargs):
        payload = json.loads(user)
        rows = [
            {
                "standard": payload["standard"], "dr_id": dr["dr_id"], "candidate_section_nos": [],
                "basis": "test", "match_uncertain": False, "confidence": 0.6,
            }
            for dr in payload["old_drs"]
        ]
        return {"rows": rows}, 1


class RM12FakeLLM:
    """RM1.2 -- sees RM1.1's blind rows plus the deterministic index candidates."""

    def complete_json(self, system, user, **kwargs):
        payload = json.loads(user)
        candidates = payload["deterministic_index_candidates"]
        rows = [
            {
                "standard": payload["standard"], "dr_id": dr["dr_id"], "dr_title": dr["dr_title"],
                "reported_status": "reported" if candidates.get(dr["dr_id"]) else "unclear",
                "report_section_refs": candidates.get(dr["dr_id"], []),
                "materiality_note": "", "omission_note": "", "evidence_quotes": [], "confidence": 0.6,
                "blind_agreement": "agree", "index_agreement": "agree", "divergence_note": "",
            }
            for dr in payload["old_drs"]
        ]
        return {"rows": rows}, 1


def _cp1_response() -> dict:
    return {
        "task_type": "compliance_check", "risk_level": "low", "uncertainty": "low",
        "deadline_proximity_days": None, "assurance_artifact": "reconciliation_record",
        "dual_method_required": False, "cluster_count": 1, "assurance_level": "audit_ready",
        "cross_functional_need": False,
        "output_profile": {"output_type": "qualitative", "form": "structured_memo", "description": "test"},
        "signal_sources": {
            "task_type": ["registry", "rd3"], "risk_level": ["rd3"],
            "uncertainty": ["rd3"], "assurance_artifact": ["rd3", "human_review"],
            "dual_method_required": ["rd3"], "cluster_count": ["rd3"],
            "assurance_level": ["registry", "human_review"],
            "cross_functional_need": ["rd3"], "output_profile": ["registry"],
        },
        "limitations": [],
    }


def _ts1_response() -> dict:
    return {
        "topology_scores": {"Direct": 10, "Debate": 80, "Coalition": 10},
        "selected_topology_id": "Debate",
        "rationale": "Three independent risk-posture reads over shared evidence, reconciled by one synthesizer.",
        "alternatives": [
            {"topology_id": "Coalition", "why_not": "Readers share one domain."},
            {"topology_id": "Direct", "why_not": "Independent reads require reconciliation."},
        ],
        "signal_sources": {"selected_topology_id": ["cross_functional_need", "task_type"]},
        "bindings": [], "limitations": [],
    }


class CP1FakeLLM:
    def complete_json(self, system, user, **kwargs):
        return _cp1_response(), 1


class TS1FakeLLM:
    def complete_json(self, system, user, **kwargs):
        return _ts1_response(), 1


class PersonaFakeLLM:
    def complete_json(self, system, user, **kwargs):
        payload = json.loads(user)
        rows = [
            {
                "character_id": payload["character_id"], "standard": payload["standard"],
                "verdict_kind": "primary",
                "old_dr_ids": [f["old_dr_id"]] if f["old_dr_id"] else [], "new_dr_ids": f["new_dr_ids"],
                "recommended_action": "test", "effort_estimate": "low", "effort_rationale": "test",
                "risk_posture_note": "test", "rationale": "test", "citations": [],
                "siemens_evidence_quotes": [], "confidence": 0.6,
                "action_needed": f["action_needed"], "action_priority": "required",
            }
            for f in payload["findings"]
        ]
        return {"rows": rows}, 1


class F2FakeLLM:
    def complete_json(self, system, user, **kwargs):
        payload = json.loads(user)
        rows = [
            {
                "standard": c["standard"], "dr_id_2025": c["old_dr_id"],
                "requirement_2025": c["old_dr_title"],
                "dr_id_2026": (c["new_dr_ids"] or [""])[0],
                "requirement_2026": (c["new_dr_titles"] or [""])[0],
                "change_status": c["change_status"], "change_description": c["change_description"],
                "source_2025": [], "source_2026": [], "siemens_reported": "reported",
                "siemens_source_ref": [], "siemens_impact": "test", "confidence": 0.7,
                "confidence_note": "Test confidence.",
                "action_needed": c["action_needed"], "agreed_priority": "required",
                "agreed_effort": "medium", "effort_note": "test",
            }
            for c in payload["change_exposure_join"]
        ]
        return {
            "rows": rows, "executive_summary": ["test"], "top_impacts": [],
            "persona_divergence": [], "limitations": ["helper baseline mismatch"],
        }, 1


def _auto_approve(_payload):
    return "a"


def test_document_pipeline_end_to_end_promotes_every_shelf(tmp_path, monkeypatch) -> None:
    # Redirect every shelf write into tmp_path (same convention as
    # test_regulation_cli.py) so this test never touches the real
    # registry_profiles/ directories shipped in the repo.
    monkeypatch.setattr(rc1_1_profile_store, "PROFILE_DIR", tmp_path / "rc1_1")
    monkeypatch.setattr(rc1_profile_store, "PROFILE_DIR", tmp_path / "rc1")
    monkeypatch.setattr(rm1_1_profile_store, "PROFILE_DIR", tmp_path / "rm1_1")
    monkeypatch.setattr(rm1_profile_store, "PROFILE_DIR", tmp_path / "rm1")
    monkeypatch.setattr(rd3_profile_store, "PROFILE_DIR", tmp_path / "rd3")
    monkeypatch.setattr(cp1_profile_store, "PROFILE_DIR", tmp_path / "cp1")
    monkeypatch.setattr(topology_selector_store, "PROFILE_DIR", tmp_path / "ts")
    monkeypatch.setattr(persona_profile_store, "PROFILE_DIR", tmp_path / "persona")
    monkeypatch.setattr(f2_profile_store, "PROFILE_DIR", tmp_path / "f2")

    experiment_dir = tmp_path / "uc4_smoke"
    experiment_dir.mkdir()

    seed = UC4.model_copy(update={"document_sources": UC4.document_sources.model_copy(update=DOC_SOURCES)})

    active = build_document_pipeline(
        seed=seed,
        experiment_dir=experiment_dir,
        refresh=False,
        rc1_1_agent=BlindChangeMatcherAgent(llm=RC11FakeLLM()),
        rc1_2_agent=ChangeClassifierAgent(llm=RC12FakeLLM()),
        rm1_1_agent=BlindExposureMatcherAgent(llm=RM11FakeLLM()),
        rm1_2_agent=ExposureMapperAgent(llm=RM12FakeLLM()),
        cp1_agent=CaseProfilerAgent(llm=CP1FakeLLM()),
        ts_agent=TopologySelectorAgent(llm=TS1FakeLLM()),
        persona_agent_factory=lambda cid: PersonaAssessorAgent(cid, llm=PersonaFakeLLM()),
        f2_agent=ImpactFinalizerAgent(llm=F2FakeLLM()),
        mapping_review_provider=_auto_approve,
        cp1_review_provider=_auto_approve,
        ts_review_provider=_auto_approve,
        f2_review_provider=_auto_approve,
    )

    assert active is not None
    for key in ("rc1_1", "rc1_2", "rm1_1", "rm1_2", "rd3", "cp1", "ts", "p4", "p5", "p6", "f2"):
        assert active[key] is not None, f"{key} shelf was not promoted"

    # provenance chain: X.2 must point at X.1, RD3 must point at both X.2 shelves
    rc1_active = rc1_profile_store.load_active(seed.id)
    assert set(rc1_active["input_refs"]) == {"rc1_1"}
    assert rc1_active["input_refs"]["rc1_1"]["artifact_id"] == active["rc1_1"]["artifact_id"]

    rm1_active = rm1_profile_store.load_active(seed.id)
    assert set(rm1_active["input_refs"]) == {"rm1_1"}
    assert rm1_active["input_refs"]["rm1_1"]["artifact_id"] == active["rm1_1"]["artifact_id"]

    rd3_active = rd3_profile_store.load_active(seed.id)
    assert set(rd3_active["input_refs"]) == {"rc1_2", "rm1_2"}
    assert rd3_active["input_refs"]["rc1_2"]["artifact_id"] == active["rc1_2"]["artifact_id"]
    assert rd3_active["input_refs"]["rm1_2"]["artifact_id"] == active["rm1_2"]["artifact_id"]

    # provenance chain: F2's input_refs must point at the promoted upstream records
    f2_active = f2_profile_store.load_active(seed.id)
    assert set(f2_active["input_refs"]) == {"rd3", "p4", "p5", "p6", "demand"}
    assert f2_active["input_refs"]["rd3"]["artifact_id"] == active["rd3"]["artifact_id"]
    assert f2_active["input_refs"]["p4"]["artifact_id"] == active["p4"]["artifact_id"]
    assert f2_active["input_refs"]["demand"]["registry_id"] == seed.id

    # persona shelves are kept separate per character
    p5_active = persona_profile_store.load_active("P5", seed.id)
    assert p5_active["payload"]["character_id"] == "P5"

    ts_active = topology_selector_store.load_active(seed.id)
    assert ts_active["payload"]["selected_topology_id"] == "Debate"
