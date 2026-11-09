"""RC1.1 -- Blind Change Matcher (pure LLM agent, UC4 document pipeline).

RC1.1 never sees RD2's deterministic candidates, hints, or scores -- its
payload carries only the full authoritative text of every 2025-amended and
2026-revised DR for one standard. This is the "blind pass" half of the
RC1.1/RC1.2 blind-pass pair (AGENTS.md): RC1.1 proposes its own old<->new
matching independently, so RC1.2 can compare two non-binding proposals against
the authoritative text instead of anchoring on RD2's grouping alone.

RC1.1 only matches -- it never assigns `change_status`. That decision belongs
entirely to RC1.2.
"""
from __future__ import annotations

import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from epoch_switch import config
from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import LLMClient, llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector
from epoch_switch.corpus.corpus_bundle import CorpusBundle

from ._payloads import _dr_payload
from .rc1_1_result import BlindMatch, RC11Result


class BlindChangeMatcherAgent(BaseAgent):
    agent_id = "RC1.1"
    can_fill = ["blind_change_matcher"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict | None = None

    @property
    def role_prompt_path(self):
        return Path(inspect.getfile(type(self))).parent / "RC1_1.md"

    def execute_matching(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        registry_id: str,
        bundle: CorpusBundle,
    ) -> Message:
        """CLI entrypoint -- one LLM batch per ESRS standard, run concurrently."""
        result = self.match(bundle=bundle)
        result.registry_id = registry_id
        payload = result.model_dump()
        evidence_store.write(f"rc1_1_blind_match_{envelope.case_id}", payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[f"rc1_1_blind_match_{envelope.case_id}"],
            confidence=0.7,
        )

    def match(self, *, bundle: CorpusBundle) -> RC11Result:
        standards: list[str] = list(bundle.standards.keys())

        system = self.role_prompt_path.read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.RC1_1_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)

        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        def _run_one(standard: str) -> list[BlindMatch]:
            sc = bundle.standards[standard]
            user_payload = {
                "standard": standard,
                "old_drs": [_dr_payload(dr) for dr in sc.old_drs],
                "new_drs": [_dr_payload(dr) for dr in sc.new_drs],
            }
            raw, _ = llm.complete_json(
                system,
                json.dumps(user_payload, ensure_ascii=False, indent=2),
                model=model,
                max_tokens=config.RC1_1_MAX_TOKENS,
                stream=True,
                provenance=prov,
            )
            return [BlindMatch.model_validate(row) for row in raw.get("rows", [])]

        with ThreadPoolExecutor(max_workers=min(config.RC1_1_MAX_CONCURRENCY, len(standards))) as pool:
            futures = {pool.submit(_run_one, standard): i for i, standard in enumerate(standards)}
            ordered: list[list[BlindMatch] | None] = [None] * len(standards)
            for future in futures:
                ordered[futures[future]] = future.result()

        rows = [row for batch in ordered if batch for row in batch]
        self.last_llm_provenance = prov.to_record()
        return RC11Result(registry_id="", rows=rows)

    def execute(self, capsule: str, envelope: CaseEnvelope, evidence_store: EvidenceStore) -> Message:
        raise NotImplementedError("RC1.1 uses execute_matching, not the generic execute() path.")

    def _parse_output(self, result: dict[str, Any], envelope: CaseEnvelope, duration_ms: int) -> Message:
        parsed = RC11Result.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
