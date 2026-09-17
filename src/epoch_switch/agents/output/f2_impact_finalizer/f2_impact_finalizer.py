"""F2 -- ESRS Impact Finalizer (UC4 document pipeline's terminal agent).

Hybrid agent: a single LLM call, no batching (same pattern as F1 -- the
whole-portfolio comparison table is assembled from already-verified RC1/RM1
facts and three already-independent persona reads, so there is nothing left
to parallelize), followed by a deterministic per-standard rollup
(`build_standard_summary`) with no LLM involvement -- counting rows is not
an LLM task. Holds the second (and final) UC4 human-review gate.
"""
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

from .f2_result import ESRSImpactReport, build_standard_summary


class ImpactFinalizerAgent(BaseAgent):
    agent_id = "F2"
    can_fill = ["esrs_impact_finalizer"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict | None = None

    def execute_finalization(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        registry_id: str,
        join_rows: list[dict[str, Any]],
        persona_results: dict[str, list[dict[str, Any]]],
        demand: dict[str, Any],
    ) -> Message:
        result = self.finalize(
            registry_id=registry_id,
            join_rows=join_rows,
            persona_results=persona_results,
            demand=demand,
        )
        payload = result.model_dump()
        evidence_key = f"f2_impact_report_{envelope.case_id}"
        evidence_store.write(evidence_key, payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[evidence_key],
            confidence=0.8,
        )

    def finalize(
        self,
        *,
        registry_id: str,
        join_rows: list[dict[str, Any]],
        persona_results: dict[str, list[dict[str, Any]]],
        demand: dict[str, Any],
    ) -> ESRSImpactReport:
        system = self.role_prompt_path.read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.F2_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)
        user_payload = {
            "change_exposure_join": join_rows,
            "persona_assessments": persona_results,  # {"P4": [...], "P5": [...], "P6": [...]}
            "demand": demand,
        }
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)
        raw, _ = llm.complete_json(
            system,
            json.dumps(user_payload, ensure_ascii=False, indent=2),
            model=model,
            max_tokens=config.F2_MAX_TOKENS,
            stream=True,
            provenance=prov,
        )
        raw.setdefault("registry_id", registry_id)
        report = ESRSImpactReport.model_validate(raw)
        report.standard_summary = build_standard_summary(report.rows)
        self.last_llm_provenance = prov.to_record()
        return report

    def execute(self, capsule: str, envelope: CaseEnvelope, evidence_store: EvidenceStore) -> Message:
        raise NotImplementedError("F2 uses execute_finalization, not the generic execute() path.")

    def _parse_output(self, result: dict[str, Any], envelope: CaseEnvelope, duration_ms: int) -> Message:
        parsed = ESRSImpactReport.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
