"""RM1.2 -- Siemens Exposure Mapper (pure LLM agent, UC4 document pipeline).

RM1.2 is the reconciliation half of the RM1.1/RM1.2 blind-pass pair
(AGENTS.md). It receives two independent, non-binding proposals for which
Siemens report sections address each 2025-amended DR -- RM1.1's blind match
(produced without ever seeing the deterministic index) and the deterministic
ESRS-index candidate-section lookup -- plus the full topic report text, and
decides whether/how Siemens actually reported that requirement. Neither
proposal is binding; the deterministic index in particular is documented as a
low-precision lead (corpus/README.md), so a low `index_agreement` is expected,
not a red flag.
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

from .rm1_result import ExposureRecord, RM1Result


class ExposureMapperAgent(BaseAgent):
    agent_id = "RM1.2"
    can_fill = ["exposure_mapper"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict | None = None

    @property
    def role_prompt_path(self) -> Path:
        return Path(inspect.getfile(type(self))).parent / "RM1_2.md"

    def execute_mapping(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        registry_id: str,
        bundle: CorpusBundle,
        blind_rows: list[dict[str, Any]],
    ) -> Message:
        """CLI entrypoint -- one LLM batch per ESRS standard, run concurrently."""
        result = self.map_exposure(bundle=bundle, blind_rows=blind_rows)
        result.registry_id = registry_id
        payload = result.model_dump()
        evidence_store.write(f"rm1_2_exposure_map_{envelope.case_id}", payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[f"rm1_2_exposure_map_{envelope.case_id}"],
            confidence=0.75,
        )

    def map_exposure(self, *, bundle: CorpusBundle, blind_rows: list[dict[str, Any]]) -> RM1Result:
        standards: list[str] = list(bundle.standards.keys())
        all_sections = bundle.report_sections
        context_sections = [s for s in all_sections if s.topic == "00_context"]
        index_candidates = bundle.index_candidates

        system = self.role_prompt_path.read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.RM1_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)

        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        def _run_one(standard: str) -> list[ExposureRecord]:
            old_drs = bundle.standards[standard].old_drs
            topic_sections = [s for s in all_sections if s.topic == standard]
            standard_blind_rows = [row for row in blind_rows if row.get("standard") == standard]
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
                "blind_matches": standard_blind_rows,
                "deterministic_index_candidates": {
                    dr.dr_id: index_candidates.get(dr.dr_id, []) for dr in old_drs
                },
            }
            raw, _ = llm.complete_json(
                system,
                json.dumps(user_payload, ensure_ascii=False, indent=2),
                model=model,
                max_tokens=config.RM1_MAX_TOKENS,
                stream=True,
                provenance=prov,
            )
            return [ExposureRecord.model_validate(row) for row in raw.get("rows", [])]

        with ThreadPoolExecutor(max_workers=min(config.RM1_MAX_CONCURRENCY, len(standards))) as pool:
            futures = {pool.submit(_run_one, standard): i for i, standard in enumerate(standards)}
            ordered: list[list[ExposureRecord] | None] = [None] * len(standards)
            for future in futures:
                ordered[futures[future]] = future.result()

        rows = [row for batch in ordered if batch for row in batch]
        self.last_llm_provenance = prov.to_record()
        return RM1Result(registry_id="", rows=rows)

    def execute(self, capsule: str, envelope: CaseEnvelope, evidence_store: EvidenceStore) -> Message:
        raise NotImplementedError("RM1.2 uses execute_mapping, not the generic execute() path.")

    def _parse_output(self, result: dict[str, Any], envelope: CaseEnvelope, duration_ms: int) -> Message:
        parsed = RM1Result.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
