"""TS1 — Topology Selector agent.

Receives an approved CP1 case profile and emits a confidence distribution over
Λ = {Direct, Debate, Coalition}.  The CLI takes the argmax as the selected
topology and routes it through a human-approval gate before writing to the shelf.

Input: approved CP1 payload (dict), passed by the CLI — TS1 does NOT read any
shelf directly (AGENTS.md isolation rule: individual agents do not call another
agent's store module).

Output: TopologySelection — validated by Pydantic before any shelf write.
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
from epoch_switch.selection import topology_library

from .topology_selection import TopologySelection


class TopologySelectorAgent(BaseAgent):
    agent_id = "TS1"
    can_fill = ["topology_selector"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict[str, Any] | None = None

    def execute_selection(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        case_profile: dict[str, Any],
    ) -> Message:
        """CLI entrypoint — run selection and write an evidence record.

        Args:
            envelope:       Active case envelope (used for case_id labelling).
            evidence_store: Run-local evidence store for audit records.
            case_profile:   Approved CP1 payload (the full `payload` field from
                            the CP1 active shelf record, NOT the shelf record
                            itself).

        Returns a Message whose payload is the TopologySelection dict.
        """
        selection = self.select(case_profile)
        result = selection.model_dump()
        evidence_store.write(f"topology_selection_{envelope.case_id}", result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=result,
            evidence_refs=[f"topology_selection_{envelope.case_id}"],
            confidence=0.85,
        )

    def select(self, case_profile: dict[str, Any]) -> TopologySelection:
        """Run the LLM blueprint against the approved CP1 case profile.

        Reads TS1.md (via BaseAgent.role_prompt_path) as the system prompt.
        Validates the raw LLM output with TopologySelection (Pydantic).
        Verifies the selected_topology_id is a valid Λ member via topology_library.get().

        Raises:
            ValidationError: if the LLM output violates the TopologySelection contract.
            KeyError:         if selected_topology_id is not in Λ (should not occur
                              after schema validation, but checked explicitly).
        """
        system = self.role_prompt_path.read_text(encoding="utf-8")
        user_payload = {"case_profile": case_profile}
        if self._llm is not None:
            llm, model = self._llm, config.TS1_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)
        raw, _ = llm.complete_json(
            system,
            json.dumps(user_payload, ensure_ascii=False, indent=2),
            model=model,
            max_tokens=config.TS1_MAX_TOKENS,
            stream=True,  # max_tokens=32000 trips the Anthropic non-stream 10-min guard
            provenance=prov,
        )
        selection = TopologySelection.model_validate(raw)
        # Validate the selected id is in the global library (redundant after schema
        # validation against Literal, but makes the library the authority on Λ).
        topology_library.get(selection.selected_topology_id)
        self.last_llm_provenance = prov.to_record()
        return selection

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        """BaseAgent.execute override — thin wrapper for ad-hoc invocation."""
        return self.execute_selection(envelope, evidence_store, case_profile={})

    def _parse_output(
        self,
        result: dict[str, Any],
        envelope: CaseEnvelope,
        duration_ms: int,
    ) -> Message:
        """BaseAgent abstractmethod implementation."""
        selection = TopologySelection.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=selection.model_dump(),
        )
