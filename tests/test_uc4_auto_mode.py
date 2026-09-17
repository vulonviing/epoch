"""Verifies `epoch-regulation build --usecase 4 --auto` actually works:

build_command's --auto path calls build_profile_pipeline(**_AUTO_PROVIDERS),
which must (a) route pipeline_family="document" to build_document_pipeline,
(b) auto-approve both UC4 gates via the two new provider keys added to
_AUTO_PROVIDERS, and (c) never call typer.prompt (no interactive blocking).

get_llm() is monkeypatched at the point each agent module imported it (each
does `from epoch_switch.core.llm_client import get_llm`, binding its own
module-local name), since build_command's --auto path has no agent-injection
hooks -- this exercises the exact call path --auto takes.
"""
from __future__ import annotations

import json

import pytest

from epoch_switch.agents.orchestration.cp1_case_profiler import cp1_case_profiler, cp1_profile_store
from epoch_switch.agents.orchestration.topology_selector import topology_selector, topology_selector_store
from epoch_switch.agents.output.f2_impact_finalizer import f2_impact_finalizer, f2_profile_store
from epoch_switch.agents.output.persona_assessor import persona_assessor, persona_profile_store
from epoch_switch.agents.regulation.rc1_change_classifier import (
    rc1_1_blind_matcher, rc1_1_profile_store, rc1_change_classifier, rc1_profile_store,
)
from epoch_switch.agents.regulation.rm1_exposure_mapper import (
    rm1_1_blind_exposure, rm1_1_profile_store, rm1_exposure_mapper, rm1_profile_store,
)
from epoch_switch.agents.regulation.rd3_join import rd3_profile_store
import epoch_switch.regulation_cli as regulation_cli_module
from epoch_switch.regulation_cli import _AUTO_PROVIDERS, build_profile_pipeline
from epoch_switch.usecases.registry import UC4

DOC_SOURCES = {
    "baseline_dir": "regulations/esrs/baseline_2025_amended",
    "target_dir": "regulations/esrs/2026",
    "helper_dir": "regulations/comparison_helpers",
    "company_report_dir": "data/siemens_sustainability_2025",
    "standards": ["E5"],  # smallest standard: keeps the test fast
}


class RoutingFakeLLM:
    """One fake LLM that recognizes which agent called it from the prompt."""

    def complete_json(self, system, user, **kwargs):
        payload = json.loads(user)
        if "old_drs" in payload and "new_drs" in payload and "deterministic_candidates" not in payload:  # RC1.1
            rows = [
                {
                    "standard": payload["standard"], "old_dr_id": dr["dr_id"], "new_dr_ids": [],
                    "basis": "test", "match_uncertain": False, "confidence": 0.6,
                }
                for dr in payload["old_drs"]
            ]
            return {"rows": rows}, 1
        if "deterministic_candidates" in payload:  # RC1.2
            rows = [
                {
                    "standard": payload["standard"],
                    "old_dr_ids": c["old_dr_ids"],
                    "old_dr_titles": [],
                    "new_dr_ids": c["new_dr_ids"],
                    "new_dr_titles": [],
                    "change_status": "Retained" if c["old_dr_ids"] and c["new_dr_ids"] else ("New" if not c["old_dr_ids"] else "Removed"),
                    "change_description": "test", "old_paragraph_refs": [], "new_paragraph_refs": [],
                    "mapping_uncertain": False, "citations": [], "confidence": 0.8,
                    "blind_agreement": "agree", "deterministic_agreement": "agree", "divergence_note": "",
                }
                for c in payload["deterministic_candidates"]
            ]
            return {"rows": rows}, 1
        if "old_drs" in payload and "report_sections" in payload and "deterministic_index_candidates" not in payload:  # RM1.1
            rows = [
                {
                    "standard": payload["standard"], "dr_id": dr["dr_id"], "candidate_section_nos": [],
                    "basis": "test", "match_uncertain": False, "confidence": 0.6,
                }
                for dr in payload["old_drs"]
            ]
            return {"rows": rows}, 1
        if "deterministic_index_candidates" in payload:  # RM1.2
            candidates = payload["deterministic_index_candidates"]
            rows = [
                {
                    "standard": payload["standard"], "dr_id": dr["dr_id"], "dr_title": dr["dr_title"],
                    "reported_status": "reported", "report_section_refs": candidates.get(dr["dr_id"], []),
                    "materiality_note": "", "omission_note": "", "evidence_quotes": [], "confidence": 0.6,
                    "blind_agreement": "agree", "index_agreement": "agree", "divergence_note": "",
                }
                for dr in payload["old_drs"]
            ]
            return {"rows": rows}, 1
        if "findings" in payload:  # persona
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
        if "change_exposure_join" in payload:  # F2
            rows = [
                {
                    "standard": c["standard"], "dr_id_2025": c["old_dr_id"],
                    "requirement_2025": c["old_dr_title"],
                    "dr_id_2026": (c["new_dr_ids"] or [""])[0], "requirement_2026": (c["new_dr_titles"] or [""])[0],
                    "change_status": c["change_status"], "change_description": c["change_description"],
                    "source_2025": [], "source_2026": [], "siemens_reported": "reported",
                    "siemens_source_ref": [], "siemens_impact": "test", "confidence": 0.7,
                    "confidence_note": "Test confidence.",
                    "action_needed": c["action_needed"], "agreed_priority": "required",
                    "agreed_effort": "medium", "effort_note": "test",
                }
                for c in payload["change_exposure_join"]
            ]
            return {"rows": rows, "executive_summary": ["test"], "top_impacts": [], "persona_divergence": [], "limitations": []}, 1
        if "demand" in payload and "approved_scope" in payload:  # CP1
            return {
                "task_type": "compliance_check", "risk_level": "low", "uncertainty": "low",
                "deadline_proximity_days": None, "assurance_artifact": "reconciliation_record",
                "dual_method_required": False, "cluster_count": 1, "assurance_level": "audit_ready",
                "cross_functional_need": False,
                "output_profile": {"output_type": "qualitative", "form": "structured_memo", "description": "t"},
                "signal_sources": {k: ["rd3"] for k in (
                    "task_type", "risk_level", "uncertainty", "assurance_artifact", "dual_method_required",
                    "cluster_count", "assurance_level", "cross_functional_need", "output_profile",
                )},
                "limitations": [],
            }, 1
        if "case_profile" in payload:  # TS1
            return {
                "topology_scores": {"Direct": 10, "Debate": 80, "Coalition": 10},
                "selected_topology_id": "Debate", "rationale": "test",
                "alternatives": [
                    {"topology_id": "Coalition", "why_not": "t"}, {"topology_id": "Direct", "why_not": "t"},
                ],
                "signal_sources": {"selected_topology_id": ["task_type"]}, "bindings": [], "limitations": [],
            }, 1
        raise AssertionError(f"RoutingFakeLLM got an unrecognized payload shape: {list(payload.keys())}")


def test_auto_mode_never_prompts_and_promotes_every_uc4_shelf(tmp_path, monkeypatch) -> None:
    fake = RoutingFakeLLM()
    for mod in (
        rc1_1_blind_matcher, rc1_change_classifier, rm1_1_blind_exposure, rm1_exposure_mapper,
        persona_assessor, f2_impact_finalizer, cp1_case_profiler, topology_selector,
    ):
        monkeypatch.setattr(mod, "llm_for_agent", lambda agent: (fake, "test-model"))

    for store, name in [
        (rc1_1_profile_store, "rc1_1"), (rc1_profile_store, "rc1"),
        (rm1_1_profile_store, "rm1_1"), (rm1_profile_store, "rm1"),
        (rd3_profile_store, "rd3"),
        (cp1_profile_store, "cp1"), (topology_selector_store, "ts"),
        (persona_profile_store, "persona"), (f2_profile_store, "f2"),
    ]:
        monkeypatch.setattr(store, "PROFILE_DIR", tmp_path / name)

    def _fail_if_prompted(*args, **kwargs):
        raise AssertionError("typer.prompt was called -- --auto is not fully bypassing interactive prompts")

    monkeypatch.setattr(regulation_cli_module.typer, "prompt", _fail_if_prompted)

    seed = UC4.model_copy(update={"document_sources": UC4.document_sources.model_copy(update=DOC_SOURCES)})

    active = build_profile_pipeline(
        seed=seed,
        experiment_dir=tmp_path / "run",
        refresh=False,
        **_AUTO_PROVIDERS,
    )

    assert active is not None
    for key in ("rc1_1", "rc1_2", "rm1_1", "rm1_2", "rd3", "cp1", "ts", "p4", "p5", "p6", "f2"):
        assert active[key] is not None, f"{key} shelf was not promoted under --auto"
