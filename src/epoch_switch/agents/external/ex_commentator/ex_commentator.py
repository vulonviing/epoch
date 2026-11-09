"""ExternalCommentatorAgent -- one class, two stage instances (EX1, EX2).

EX1 runs once before every Stage-1 human gate; EX2 runs once before every
Stage-2 human gate; for every use case, tabular or document (AGENTS.md hard
rule). Both are role agents (like S1/F1) -- neither the P/T contract nor a
topology slot applies; they sit outside the topology entirely.

EX1/EX2 read exactly the same payload the human is about to see at that
gate, then use a web-search tool restricted to the official-institution
allowlist in ex_sources.py to produce a short, sourced commentary from a
perspective outside Siemens' own pipeline. The commentary is advisory only:
it is never consumed by any downstream agent and never re-opens or changes
the isolated memory (approved R1/DA1/R2 findings, or any promoted shelf). It
exists solely to inform the human's approve/reject decision at that gate.

Same class-per-two-instances pattern as PersonaAssessorAgent (P4/P5/P6):
``BaseAgent.role_prompt_path`` resolves from ``type(self)`` (this module's
directory) plus ``self.agent_id``, so each instance loads its own
EX1.md / EX2.md automatically.
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

from . import ex_sources
from .ex_result import ExternalCommentary

VALID_STAGE_IDS = ("EX1", "EX2")


class ExternalCommentatorAgent(BaseAgent):
    can_fill = ["external_commentator"]

    def __init__(self, stage_id: str, llm: LLMClient | None = None):
        if stage_id not in VALID_STAGE_IDS:
            raise ValueError(f"Unknown EX stage_id '{stage_id}', expected one of {VALID_STAGE_IDS}")
        self.agent_id = stage_id
        self._llm = llm
        self.last_llm_provenance: dict | None = None

    def execute_commentary(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        registry_id: str,
        gate_payload: dict[str, Any],
        demand: dict[str, Any] | None = None,
    ) -> Message:
        result = self.comment(registry_id=registry_id, gate_payload=gate_payload, demand=demand)
        payload = result.model_dump()
        evidence_key = f"{self.agent_id.lower()}_commentary_{envelope.case_id}"
        evidence_store.write(evidence_key, payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[evidence_key],
            confidence=0.5,
        )

    def comment(
        self,
        *,
        registry_id: str,
        gate_payload: dict[str, Any],
        demand: dict[str, Any] | None = None,
    ) -> ExternalCommentary:
        """Core commentary logic: one web-search-enabled LLM call.

        ``gate_payload`` is exactly what the human is about to review at this
        gate. ``demand`` (natural_request / expected_output / output_profile /
        assurance_profile) gives EX the same registry-demand orientation R1
        receives, so it can judge relevance without seeing raw scope.
        """
        system = self.role_prompt_path.read_text(encoding="utf-8") + "\n\n## Allowed institutions\n\n" + ex_sources.render_table()
        if self._llm is not None:
            llm, model = self._llm, config.EX_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)

        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        user = json.dumps(
            {
                "registry_id": registry_id,
                "stage_id": self.agent_id,
                "demand": demand or {},
                "gate_payload": gate_payload,
            },
            ensure_ascii=False,
            indent=2,
        )
        raw, _ = llm.complete_json_websearch(
            system,
            user,
            model=model,
            max_tokens=config.EX_MAX_TOKENS,
            max_uses=config.EX_WEB_MAX_USES,
            allowed_domains=ex_sources.allowed_domains(),
            provenance=prov,
        )
        result = ExternalCommentary.model_validate({**raw, "registry_id": registry_id, "stage_id": self.agent_id})
        self.last_llm_provenance = prov.to_record()
        return result

    def execute(self, capsule: str, envelope: CaseEnvelope, evidence_store: EvidenceStore) -> Message:
        raise NotImplementedError("ExternalCommentatorAgent uses execute_commentary, not the generic execute() path.")

    def _parse_output(self, result: dict[str, Any], envelope: CaseEnvelope, duration_ms: int) -> Message:
        parsed = ExternalCommentary.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
