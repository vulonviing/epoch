"""C1 — Divisional Attestor: domain-specialist slot of the Coalition topology.

C1 is a pure LLM agent. It receives the approved regulation boundary (R2
in-scope findings) and D3's deterministic per-division consolidation rows,
then attests each in-scope division's Scope 2 sub-total independently.

Execution model — one isolated call per division:
  1. D3's consolidation rows are grouped by `division`.
  2. One independent LLM call runs per division (ThreadPoolExecutor, one
     worker per division) -- **each call's payload contains only that
     division's rows**, never another division's. This is what makes "no
     division can produce or sign off another division's figure" real in
     code, not just a prompt instruction (mirrors P1's per-location batch
     isolation and P2/P3's per-site batch isolation).
  3. The division count is whatever D3 found -- not fixed in code. A third
     division showing up in the data produces a third independent call with
     no code change.

Input contract (isolated memory + data):
  - r2_in_scope_findings: approved R2 regulation boundary.
  - d3_payload: D3 ReconciliationResult (kind="consolidation" rows + meta).

Deliberately excluded from C1's input:
  - Any other division's rows, subtotal, or attestation (Coalition
    independence rule -- the synthesis coordinator is the first agent to
    see all divisions together).
  - CP1, DA1 handoff, raw registry demand / scope.

C1 does NOT consolidate across divisions or assess attestation coverage.
That is C2's job (synthesis coordinator).
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from epoch_switch import config
from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import LLMClient, llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector

from .c1_result import C1Result, DivisionAttestation


class DivisionalAttestorAgent(BaseAgent):
    agent_id = "C1"
    can_fill = ["domain_specialist"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict[str, Any] | None = None

    def execute_attestation(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        r2_in_scope_findings: list[dict[str, Any]],
        d3_payload: dict[str, Any],
    ) -> Message:
        """CLI entrypoint. Runs C1, writes evidence, returns a Message.

        No human review gate here -- C1 is an intermediate Coalition step.
        The human review gate fires only after the final agent (F1) produces
        the complete deliverable (AGENTS.md: Topology final-agent gate rule).
        """
        result = self.attest(
            r2_in_scope_findings=r2_in_scope_findings,
            d3_payload=d3_payload,
        )
        payload = result.model_dump()
        evidence_store.write(f"divisional_attestation_{envelope.case_id}", payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[f"divisional_attestation_{envelope.case_id}"],
            confidence=0.9,
        )

    def attest(
        self,
        *,
        r2_in_scope_findings: list[dict[str, Any]],
        d3_payload: dict[str, Any],
    ) -> C1Result:
        """Core attestation logic. One isolated LLM call per division found in D3."""
        system = self.role_prompt_path.read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.C1_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        all_rows: list[dict[str, Any]] = d3_payload.get("rows", [])

        # Group rows by division -- this is the whole of the independence rule:
        # each group below becomes exactly one isolated call.
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in all_rows:
            division = str(row.get("division") or "").strip() or "__no_division__"
            groups.setdefault(division, []).append(row)

        def _attest_one(division: str, division_rows: list[dict[str, Any]]) -> DivisionAttestation:
            user = json.dumps(
                {
                    "r2_in_scope_findings": r2_in_scope_findings,
                    "division": division,
                    "division_rows": [
                        {
                            "year": row.get("year", ""),
                            "subtotal_t": row.get("subtotal_t", 0),
                            "n_rows": row.get("n_rows", 0),
                        }
                        for row in division_rows
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            raw, _ = llm.complete_json(
                system,
                user,
                model=model,
                max_tokens=config.C1_MAX_TOKENS,
                stream=True,
                provenance=prov,
            )
            return DivisionAttestation.model_validate(raw)

        divisions = list(groups.items())
        max_workers = min(config.C1_MAX_CONCURRENCY, len(divisions)) if divisions else 1
        ordered: list[DivisionAttestation | None] = [None] * len(divisions)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {
                pool.submit(_attest_one, division, rows): idx
                for idx, (division, rows) in enumerate(divisions)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                ordered[idx] = future.result()

        self.last_llm_provenance = prov.to_record()
        return C1Result(
            registry_id=d3_payload.get("registry_id", ""),
            rows=[r for r in ordered if r is not None],
        )

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        """BaseAgent stub -- not used by the CLI."""
        raise NotImplementedError("Use execute_attestation via the CLI orchestrator.")

    def _parse_output(
        self,
        result: dict[str, Any],
        envelope: CaseEnvelope,
        duration_ms: int,
    ) -> Message:
        parsed = C1Result.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
