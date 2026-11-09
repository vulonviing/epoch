"""C2 — Coalition Synthesis Coordinator: convergence step of the Coalition chain.

C2 is a pure LLM agent (synthesis_coordinator slot in the Coalition
topology, per topology_library.py's COALITION spec). It is the first agent
to see every division's C1 data-volume report together -- this is the
Coalition convergence point, structurally analogous to S1's role in the
Debate chain but consolidating N independently-assessed sub-results instead
of reconciling two rival reads.

C2 does NOT re-assess any division's figure or overrule data_volume_status
-- that decision belongs to the division that made it (Coalition
independence rule, carried forward from C1). It consolidates, flags
data-volume coverage gaps, and assesses portfolio-wide readiness.

Input contract:
  - c1_result: C1's per-division attestations (all divisions, together).
  - r2_in_scope_findings: approved R2 regulation boundary.
  - d3_payload: D3 ReconciliationResult (for the deterministic portfolio
    total as a cross-check).

Excluded from C2's input:
  - CP1, DA1 handoff, raw registry demand / scope.
"""
from __future__ import annotations

import json
from typing import Any

from epoch_switch import config
from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import LLMClient, llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector

from .c2_result import C2Result


class CoalitionSynthesizerAgent(BaseAgent):
    agent_id = "C2"
    can_fill = ["synthesis_coordinator"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict[str, Any] | None = None

    def execute_consolidation(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        c1_result: dict[str, Any],
        r2_in_scope_findings: list[dict[str, Any]],
        d3_payload: dict[str, Any],
    ) -> Message:
        """CLI entrypoint. Runs C2, writes evidence, returns a Message.

        No human review gate here -- C2 is an intermediate Coalition step.
        The human review gate fires only after the final agent (F1) produces
        the complete deliverable (AGENTS.md: Topology final-agent gate rule).
        """
        result = self.consolidate(
            c1_result=c1_result,
            r2_in_scope_findings=r2_in_scope_findings,
            d3_payload=d3_payload,
        )
        payload = result.model_dump()
        evidence_store.write(f"coalition_consolidation_{envelope.case_id}", payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[f"coalition_consolidation_{envelope.case_id}"],
            confidence=0.9,
        )

    def consolidate(
        self,
        *,
        c1_result: dict[str, Any],
        r2_in_scope_findings: list[dict[str, Any]],
        d3_payload: dict[str, Any],
    ) -> C2Result:
        """Core consolidation logic. Single LLM call; no batching, no web search."""
        system = self.role_prompt_path.read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.C2_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        user = json.dumps(
            {
                "r2_in_scope_findings": r2_in_scope_findings,
                "c1_rows": c1_result.get("rows", []),
                "deterministic_portfolio_total_t": d3_payload.get("portfolio_total_t", 0),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw, _ = llm.complete_json(
            system,
            user,
            model=model,
            max_tokens=config.C2_MAX_TOKENS,
            stream=True,
            provenance=prov,
        )
        raw.setdefault("registry_id", d3_payload.get("registry_id", ""))
        self.last_llm_provenance = prov.to_record()
        return C2Result.model_validate(raw)

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        """BaseAgent stub -- not used by the CLI."""
        raise NotImplementedError("Use execute_consolidation via the CLI orchestrator.")

    def _parse_output(
        self,
        result: dict[str, Any],
        envelope: CaseEnvelope,
        duration_ms: int,
    ) -> Message:
        parsed = C2Result.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
