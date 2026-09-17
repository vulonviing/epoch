"""RM1.1 -- Blind Exposure Matcher (pure LLM agent, UC4 document pipeline).

RM1.1 never sees the deterministic ESRS-index candidate-section lookup -- its
payload carries only each 2025-amended DR's id/title/preview plus the full
Siemens report section text for the topic. This is the "blind pass" half of
the RM1.1/RM1.2 blind-pass pair (AGENTS.md): RM1.1 proposes its own
DR-to-section matching independently, so RM1.2 can compare two non-binding
proposals against the report text instead of anchoring on the index alone.

RM1.1 only matches sections -- it never assigns `reported_status`. That
decision belongs entirely to RM1.2.
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

from .rm1_1_result import BlindExposureMatch, RM11Result


class BlindExposureMatcherAgent(BaseAgent):
    agent_id = "RM1.1"
    can_fill = ["blind_exposure_matcher"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict | None = None

    @property
    def role_prompt_path(self) -> Path:
        return Path(inspect.getfile(type(self))).parent / "RM1_1.md"

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
        evidence_store.write(f"rm1_1_blind_exposure_{envelope.case_id}", payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[f"rm1_1_blind_exposure_{envelope.case_id}"],
            confidence=0.7,
        )

    def match(self, *, bundle: CorpusBundle) -> RM11Result:
        standards: list[str] = list(bundle.standards.keys())
        all_sections = bundle.report_sections
        context_sections = [s for s in all_sections if s.topic == "00_context"]

        system = self.role_prompt_path.read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.RM1_1_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)

        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        def _run_one(standard: str) -> list[BlindExposureMatch]:
            old_drs = bundle.standards[standard].old_drs
            topic_sections = [s for s in all_sections if s.topic == standard]
            user_payload = {
                "standard": standard,
                "old_drs": [
                    {
                        "dr_id": dr.dr_id,
                        "dr_title": dr.dr_title,
                        "paragraphs_preview": [p.text[:200] for p in dr.paragraphs[:3]],
                    }
                    for dr in old_drs
                ],
                "report_sections": [
                    {"section_no": s.section_no, "title": s.title, "text": s.text}
                    for s in (*topic_sections, *context_sections)
                ],
            }
            raw, _ = llm.complete_json(
                system,
                json.dumps(user_payload, ensure_ascii=False, indent=2),
                model=model,
                max_tokens=config.RM1_1_MAX_TOKENS,
                stream=True,
                provenance=prov,
            )
            return [BlindExposureMatch.model_validate(row) for row in raw.get("rows", [])]

        with ThreadPoolExecutor(max_workers=min(config.RM1_1_MAX_CONCURRENCY, len(standards))) as pool:
            futures = {pool.submit(_run_one, standard): i for i, standard in enumerate(standards)}
            ordered: list[list[BlindExposureMatch] | None] = [None] * len(standards)
            for future in futures:
                ordered[futures[future]] = future.result()

        rows = [row for batch in ordered if batch for row in batch]
        self.last_llm_provenance = prov.to_record()
        return RM11Result(registry_id="", rows=rows)

    def execute(self, capsule: str, envelope: CaseEnvelope, evidence_store: EvidenceStore) -> Message:
        raise NotImplementedError("RM1.1 uses execute_matching, not the generic execute() path.")

    def _parse_output(self, result: dict[str, Any], envelope: CaseEnvelope, duration_ms: int) -> Message:
        parsed = RM11Result.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
