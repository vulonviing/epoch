"""PersonaAssessorAgent -- one class, three characters (P4/P5/P6).

A deliberate deviation from the one-class-per-agent-id convention used
elsewhere in this codebase: P4 (Conservative), P5 (Balanced), and P6
(Maximum-Assurance) differ only in prompt framing, not in code, so they are
three instances of this class with a different ``agent_id`` set per
instance. ``BaseAgent.role_prompt_path`` resolves from
``type(self)`` (this module's directory) plus ``self.agent_id``, so each
instance loads its own P4.md / P5.md / P6.md automatically.

The character shapes only ``recommended_action`` / ``effort_estimate`` /
``risk_posture_note`` / ``action_priority`` -- it must never re-litigate RC1's
``change_status``, RM1's ``reported_status``, or RD3's ``action_needed``, which
arrive as fixed input facts on each RD3 join row (see P4.md/P5.md/P6.md's
"isolated memory" section).
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from epoch_switch import config
from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import LLMClient, llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector

from .persona_result import PersonaResult, PersonaVerdict

VALID_CHARACTER_IDS = ("P4", "P5", "P6")


class PersonaAssessorAgent(BaseAgent):
    can_fill = ["persona_assessor"]

    def __init__(self, character_id: str, llm: LLMClient | None = None):
        if character_id not in VALID_CHARACTER_IDS:
            raise ValueError(f"Unknown persona character_id '{character_id}', expected one of {VALID_CHARACTER_IDS}")
        self.agent_id = character_id
        self._llm = llm
        self.last_llm_provenance: dict | None = None

    def execute_assessment(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        registry_id: str,
        join_rows: list[dict[str, Any]],
    ) -> Message:
        result = self.assess(registry_id=registry_id, join_rows=join_rows)
        payload = result.model_dump()
        evidence_key = f"{self.agent_id.lower()}_persona_assessment_{envelope.case_id}"
        evidence_store.write(evidence_key, payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[evidence_key],
            confidence=0.7,
        )

    def assess(
        self,
        *,
        registry_id: str,
        join_rows: list[dict[str, Any]],
    ) -> PersonaResult:
        """Batch by standard (E1..E5), one LLM call per standard.

        All three personas receive the same RD3 join rows for a given standard --
        see AGENTS.md's F1/P4-P6 input-boundary rule on identical topology-
        independent inputs.
        """
        standards = sorted({row["standard"] for row in join_rows})

        system = self.role_prompt_path.read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.PERSONA_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)

        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        def _run_one(standard: str) -> list[PersonaVerdict]:
            findings = [row for row in join_rows if row["standard"] == standard]
            user_payload = {"standard": standard, "character_id": self.agent_id, "findings": findings}
            raw, _ = llm.complete_json(
                system,
                json.dumps(user_payload, ensure_ascii=False, indent=2),
                model=model,
                max_tokens=config.PERSONA_MAX_TOKENS,
                stream=True,
                provenance=prov,
            )
            return [PersonaVerdict.model_validate(row) for row in raw.get("rows", [])]

        with ThreadPoolExecutor(max_workers=min(config.PERSONA_MAX_CONCURRENCY, max(len(standards), 1))) as pool:
            futures = {pool.submit(_run_one, standard): i for i, standard in enumerate(standards)}
            ordered: list[list[PersonaVerdict] | None] = [None] * len(standards)
            for future in futures:
                ordered[futures[future]] = future.result()

        rows = [row for batch in ordered if batch for row in batch]
        self.last_llm_provenance = prov.to_record()
        return PersonaResult(registry_id=registry_id, character_id=self.agent_id, rows=rows)

    def execute(self, capsule: str, envelope: CaseEnvelope, evidence_store: EvidenceStore) -> Message:
        raise NotImplementedError("PersonaAssessorAgent uses execute_assessment, not the generic execute() path.")

    def _parse_output(self, result: dict[str, Any], envelope: CaseEnvelope, duration_ms: int) -> Message:
        parsed = PersonaResult.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
