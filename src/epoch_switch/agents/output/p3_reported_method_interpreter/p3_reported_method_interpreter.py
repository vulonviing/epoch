"""P3 — Reported-Method Interpreter: second parallel branch of the Debate chain.

P3 is a pure LLM agent (analyst_b slot in the Debate topology).  It reads the
approved R2 regulation boundary and the D3 dual-method computation results, then
produces a per-site-per-quarter regulatory interpretation from the **reported
(Method B) perspective** — grounded in the Art. 14(3) Scope 2 reporting obligation.

P3 runs concurrently with P2 (Energy-Method Interpreter).  Neither agent sees
the other's output before S1 (the Synthesizer) — this is the core Debate guarantee.

Input contract (isolated memory + data):
  - r2_in_scope_findings: approved R2 regulation boundary (only source of regulatory
    authority; raw registry scope is NOT forwarded).
  - d3_payload: D3 ReconciliationResult (dual-method rows + metadata).

Excluded from P3's input:
  - P2's output (Debate independence rule — S1 is the first to see both).
  - CP1, DA1 handoff, or raw registry demand / scope.

P3 does NOT produce the final deliverable.  That is F1's job (Finalizer).
"""
from __future__ import annotations

from typing import Any

from epoch_switch import config
from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import LLMClient, llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector

from epoch_switch.agents.output._debate_common import (
    MethodInterpretationRow,
    run_method_interpretation,
)
from .p3_result import P3Result


class ReportedMethodInterpreterAgent(BaseAgent):
    agent_id = "P3"
    can_fill = ["method_interpreter"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict[str, Any] | None = None

    def execute_interpretation(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        r2_in_scope_findings: list[dict[str, Any]],
        d3_payload: dict[str, Any],
    ) -> Message:
        """CLI entrypoint.  Runs P3, writes evidence, returns a Message.

        No human review gate here — P3 is an intermediate Debate step.
        The human review gate fires only after the final agent (O6) produces
        the complete deliverable (AGENTS.md: Topology final-agent gate rule).
        """
        result = self.interpret(
            r2_in_scope_findings=r2_in_scope_findings,
            d3_payload=d3_payload,
        )
        payload = result.model_dump()
        evidence_store.write(f"reported_method_interp_{envelope.case_id}", payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[f"reported_method_interp_{envelope.case_id}"],
            confidence=0.9,
        )

    def interpret(
        self,
        *,
        r2_in_scope_findings: list[dict[str, Any]],
        d3_payload: dict[str, Any],
    ) -> P3Result:
        """Core interpretation logic.

        Splits D3 rows into site-major batches, runs each concurrently with
        web-search grounding from the Method B (reported Scope 2) perspective.
        """
        system = self.role_prompt_path.read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.P3_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        rows = run_method_interpretation(
            llm=llm,
            system_prompt=system,
            r2_in_scope_findings=r2_in_scope_findings,
            d3_payload=d3_payload,
            row_model=MethodInterpretationRow,
            model=model,
            max_tokens=config.P3_MAX_TOKENS,
            batch_size=config.P3_BATCH_SIZE,
            max_concurrency=config.P3_MAX_CONCURRENCY,
            web_max_uses=config.P3_WEB_MAX_USES,
            provenance=prov,
        )

        self.last_llm_provenance = prov.to_record()
        return P3Result(
            registry_id=d3_payload.get("registry_id", ""),
            rows=rows,
        )

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        """BaseAgent stub — not used by the CLI."""
        raise NotImplementedError("Use execute_interpretation via the CLI orchestrator.")

    def _parse_output(
        self,
        result: dict[str, Any],
        envelope: CaseEnvelope,
        duration_ms: int,
    ) -> Message:
        parsed = P3Result.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
