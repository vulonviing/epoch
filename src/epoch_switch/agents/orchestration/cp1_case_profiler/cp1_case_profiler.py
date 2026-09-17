"""CP1 - pre-selection case profiling agent."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from epoch_switch import config
from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import LLMClient, llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector

from .case_profile import CaseProfile


class CaseProfilerAgent(BaseAgent):
    agent_id = "CP1"
    can_fill = ["case_profiler"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict[str, Any] | None = None

    def execute_preselection(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        approved_profile_context: dict[str, Any] | None = None,
        derived_case_facts: dict[str, Any] | None = None,
    ) -> Message:
        context = approved_profile_context or {}
        profile = self.profile(
            envelope,
            pipeline_family=context.get("pipeline_family", "tabular"),
            demand=context.get("demand"),
            approved_scope=context.get("approved_scope"),
            r2_in_scope_findings=context.get("r2_in_scope_findings"),
            document_evidence=context.get("document_evidence"),
            derived_case_facts=derived_case_facts or context.get("derived_case_facts"),
            data_quality=context.get("data_quality"),
        )
        result = profile.model_dump()
        self._apply_profile(envelope, profile)
        evidence_store.write(f"case_profile_{envelope.case_id}", result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=result,
            evidence_refs=[f"case_profile_{envelope.case_id}"],
            confidence=0.9,
        )

    def profile(
        self,
        envelope: CaseEnvelope,
        *,
        pipeline_family: str = "tabular",
        demand: dict[str, Any] | None = None,
        approved_scope: dict[str, Any] | None = None,
        r2_in_scope_findings: list[dict[str, Any]] | None = None,
        document_evidence: dict[str, Any] | None = None,
        derived_case_facts: dict[str, Any] | None = None,
        data_quality: dict[str, Any] | None = None,
    ) -> CaseProfile:
        system = self.role_prompt_path.read_text(encoding="utf-8")
        # Registry demand axis: what the user asked + how it must be delivered
        # (natural_request, expected_output, output_profile, assurance_profile).
        # Raw registry scope (site_filter, time_window, regulation_refs) is NOT
        # forwarded here — finalized scope lives in approved_scope.handoff_data_request
        # and r2_in_scope_findings.  The envelope is still the authoritative
        # source for case_id and _apply_profile writeback; its registry fields
        # are kept as a fallback identity for legacy/direct callers.
        user = {
            "pipeline_family": pipeline_family,
            "demand": demand,
            "r2_in_scope_findings": r2_in_scope_findings,
            "approved_scope": approved_scope,
            "document_evidence": document_evidence,
            "derived_case_facts": derived_case_facts,
            "data_quality": data_quality,
        }
        if self._llm is not None:
            llm, model = self._llm, config.CP1_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)
        raw, _ = llm.complete_json(
            system,
            json.dumps(user, ensure_ascii=False, indent=2),
            model=model,
            max_tokens=config.CP1_MAX_TOKENS,
            stream=True,  # max_tokens=32000 trips the Anthropic non-stream 10-min guard
            provenance=prov,
        )
        profile = CaseProfile.model_validate(raw)

        # Deterministic window-divergence limitation — guaranteed regardless of
        # whether the LLM included it.  Surfaces D1 time-window clamping as a
        # CP1 limitation so the human reviewer always sees the divergence from
        # the DA1-approved handoff ("DA1 approval is final" rule).
        if derived_case_facts and derived_case_facts.get("window_divergence"):
            wd = derived_case_facts["window_divergence"]
            note = wd.get("note") or (
                f"D1 time window clamped: "
                f"approved end {(wd.get('requested') or ['', '?'])[1]}, "
                f"effective end {(wd.get('effective') or ['', '?'])[1]}."
            )
            if note not in profile.limitations:
                profile.limitations.append(note)

        # Deterministic injection: deadline_proximity_days is computed by
        # derived_case_facts_builder, never left to LLM discretion.
        if derived_case_facts and "deadline_proximity_days" in derived_case_facts:
            profile.deadline_proximity_days = derived_case_facts["deadline_proximity_days"]

        self.last_llm_provenance = prov.to_record()
        return profile

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        return self.execute_preselection(envelope, evidence_store)

    def _parse_output(
        self,
        result: dict[str, Any],
        envelope: CaseEnvelope,
        duration_ms: int,
    ) -> Message:
        profile = CaseProfile.model_validate(result)
        self._apply_profile(envelope, profile)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=profile.model_dump(),
        )

    @staticmethod
    def _apply_profile(envelope: CaseEnvelope, profile: CaseProfile) -> None:
        explicit_deadline = envelope.deadline_proximity_days
        envelope.task_type = profile.task_type
        envelope.risk_level = profile.risk_level
        envelope.uncertainty = profile.uncertainty
        envelope.dual_method_required = profile.dual_method_required
        envelope.cluster_count = profile.cluster_count
        envelope.assurance_level = profile.assurance_level
        envelope.cross_functional_need = profile.cross_functional_need
        envelope.deadline_proximity_days = (
            explicit_deadline
            if explicit_deadline is not None
            else profile.deadline_proximity_days
        )
