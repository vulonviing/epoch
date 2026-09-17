"""RC1.2 -- Regulatory Change Classifier (pure LLM agent, UC4 document pipeline).

RC1.2 is the reconciliation half of the RC1.1/RC1.2 blind-pass pair
(AGENTS.md). It receives two independent, non-binding proposals for how each
2025-amended DR relates to the 2026-revised standard -- RC1.1's blind match
(produced without ever seeing RD2's candidates) and RD2's deterministic
title-matching candidates -- plus the authoritative full-text DRs, and decides
the actual `change_status`. Neither proposal is binding: the authoritative
paragraph text can override either or both (regulations/README.md hard rule
on non-authoritative comparison-helper hints still applies to
`non_authoritative_hints`).
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
import inspect

from epoch_switch import config
from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import LLMClient, llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector
from epoch_switch.corpus.corpus_bundle import CorpusBundle

from ._payloads import _dr_payload
from .rc1_result import ChangeClassification, RC1Result


class ChangeClassifierAgent(BaseAgent):
    agent_id = "RC1.2"
    can_fill = ["change_classifier"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict | None = None

    @property
    def role_prompt_path(self) -> Path:
        return Path(inspect.getfile(type(self))).parent / "RC1_2.md"

    def execute_classification(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        registry_id: str,
        bundle: CorpusBundle,
        blind_rows: list[dict[str, Any]],
    ) -> Message:
        """CLI entrypoint -- one LLM batch per ESRS standard, run concurrently."""
        result = self.classify(bundle=bundle, blind_rows=blind_rows)
        result.registry_id = registry_id
        payload = result.model_dump()
        evidence_store.write(f"rc1_2_change_classification_{envelope.case_id}", payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[f"rc1_2_change_classification_{envelope.case_id}"],
            confidence=0.8,
        )

    def classify(self, *, bundle: CorpusBundle, blind_rows: list[dict[str, Any]]) -> RC1Result:
        standards: list[str] = list(bundle.standards.keys())

        system = self.role_prompt_path.read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.RC1_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)

        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        def _run_one(standard: str) -> list[ChangeClassification]:
            sc = bundle.standards[standard]
            candidates = sc.candidates
            standard_blind_rows = [row for row in blind_rows if row.get("standard") == standard]
            user_payload = {
                "standard": standard,
                "old_drs": [_dr_payload(dr) for dr in sc.old_drs],
                "new_drs": [_dr_payload(dr) for dr in sc.new_drs],
                "blind_matches": standard_blind_rows,
                "deterministic_candidates": [
                    {
                        "old_dr_ids": c.old_dr_ids,
                        "new_dr_ids": c.new_dr_ids,
                        "basis": c.basis,
                        "title_match_score": c.score,
                        "non_authoritative_hints": [
                            {"token": h.token, "text": h.text, "baseline_mismatch": h.baseline_mismatch}
                            for h in c.hints
                        ],
                    }
                    for c in candidates
                ],
            }
            raw, _ = llm.complete_json(
                system,
                json.dumps(user_payload, ensure_ascii=False, indent=2),
                model=model,
                max_tokens=config.RC1_MAX_TOKENS,
                stream=True,
                provenance=prov,
            )
            return [ChangeClassification.model_validate(row) for row in raw.get("rows", [])]

        with ThreadPoolExecutor(max_workers=min(config.RC1_MAX_CONCURRENCY, len(standards))) as pool:
            futures = {pool.submit(_run_one, standard): i for i, standard in enumerate(standards)}
            ordered: list[list[ChangeClassification] | None] = [None] * len(standards)
            for future in futures:
                ordered[futures[future]] = future.result()

        rows = [row for batch in ordered if batch for row in batch]
        self.last_llm_provenance = prov.to_record()
        return RC1Result(registry_id="", rows=rows)

    def execute(self, capsule: str, envelope: CaseEnvelope, evidence_store: EvidenceStore) -> Message:
        raise NotImplementedError("RC1.2 uses execute_classification, not the generic execute() path.")

    def _parse_output(self, result: dict[str, Any], envelope: CaseEnvelope, duration_ms: int) -> Message:
        parsed = RC1Result.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
