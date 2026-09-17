"""Phase 6+: RM1.2 (LLM) -- deterministic report/index prep + FakeLLM mapping."""
from __future__ import annotations

import json
from pathlib import Path

from epoch_switch.agents.regulation.rm1_exposure_mapper import ExposureMapperAgent
from epoch_switch.agents.regulation.rm1_exposure_mapper.rm1_result import RM1Result

REPO_ROOT = Path(__file__).resolve().parents[1]

DOCUMENT_SOURCES = {
    "baseline_dir": "regulations/esrs/baseline_2025_amended",
    "target_dir": "regulations/esrs/2026",
    "helper_dir": "regulations/comparison_helpers",
    "company_report_dir": "data/siemens_sustainability_2025",
    "standards": ["E1"],
}


class FakeLLM:
    def __init__(self):
        self.calls = []

    def complete_json(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, "kwargs": kwargs})
        payload = json.loads(user)
        candidates = payload["deterministic_index_candidates"]
        rows = [
            {
                "standard": payload["standard"],
                "dr_id": dr["dr_id"],
                "dr_title": dr["dr_title"],
                "reported_status": "reported" if candidates.get(dr["dr_id"]) else "unclear",
                "report_section_refs": candidates.get(dr["dr_id"], []),
                "materiality_note": "",
                "omission_note": "",
                "evidence_quotes": [],
                "confidence": 0.6,
                "blind_agreement": "agree",
                "index_agreement": "agree",
                "divergence_note": "",
            }
            for dr in payload["old_drs"]
        ]
        return {"rows": rows}, 1


def test_rm1_produces_one_row_per_old_dr() -> None:
    from epoch_switch.corpus.corpus_bundle import extract_corpus

    bundle = extract_corpus(DOCUMENT_SOURCES, REPO_ROOT)
    old = bundle.standards["E1"].old_drs
    agent = ExposureMapperAgent(llm=FakeLLM())
    result = agent.map_exposure(bundle=bundle, blind_rows=[])
    assert isinstance(result, RM1Result)
    assert {row.dr_id for row in result.rows} == {dr.dr_id for dr in old}


def test_rm1_receives_topic_and_context_sections() -> None:
    from epoch_switch.corpus.corpus_bundle import extract_corpus

    bundle = extract_corpus(DOCUMENT_SOURCES, REPO_ROOT)
    fake = FakeLLM()
    agent = ExposureMapperAgent(llm=fake)
    agent.map_exposure(bundle=bundle, blind_rows=[])
    payload = json.loads(fake.calls[0]["user"])
    section_titles = {s["title"] for s in payload["report_sections"]}
    assert "Double materiality" in section_titles
    assert any("2.2" in s["section_no"] for s in payload["report_sections"])


def test_rm1_old_drs_carry_no_index_candidates_key() -> None:
    from epoch_switch.corpus.corpus_bundle import extract_corpus

    bundle = extract_corpus(DOCUMENT_SOURCES, REPO_ROOT)
    fake = FakeLLM()
    agent = ExposureMapperAgent(llm=fake)
    agent.map_exposure(bundle=bundle, blind_rows=[])
    payload = json.loads(fake.calls[0]["user"])
    assert "deterministic_index_candidates" in payload
    for dr in payload["old_drs"]:
        assert "index_candidate_sections" not in dr
