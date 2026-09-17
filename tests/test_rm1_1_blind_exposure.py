"""RM1.1 -- blind exposure matcher: the single most important correctness
property in the blind-pass pair is that RM1.1's payload physically contains
no deterministic-index keys anywhere (AGENTS.md / plan decision table).
"""
from __future__ import annotations

import json
from pathlib import Path

from epoch_switch.agents.regulation.rm1_exposure_mapper import BlindExposureMatcherAgent
from epoch_switch.agents.regulation.rm1_exposure_mapper.rm1_1_result import RM11Result

REPO_ROOT = Path(__file__).resolve().parents[1]

DOCUMENT_SOURCES = {
    "baseline_dir": "regulations/esrs/baseline_2025_amended",
    "target_dir": "regulations/esrs/2026",
    "helper_dir": "regulations/comparison_helpers",
    "company_report_dir": "data/siemens_sustainability_2025",
    "standards": ["E1"],
}

_FORBIDDEN_KEYS = {
    "candidates",
    "basis",
    "title_match_score",
    "non_authoritative_hints",
    "index_candidate_sections",
    "deterministic_index_candidates",
    "deterministic_candidates",
}


def _no_forbidden_keys(obj) -> bool:
    if isinstance(obj, dict):
        if _FORBIDDEN_KEYS & obj.keys():
            return False
        return all(_no_forbidden_keys(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_no_forbidden_keys(v) for v in obj)
    return True


class FakeLLM:
    def __init__(self):
        self.calls = []

    def complete_json(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, "kwargs": kwargs})
        payload = json.loads(user)
        rows = [
            {
                "standard": payload["standard"],
                "dr_id": dr["dr_id"],
                "candidate_section_nos": [],
                "basis": "test",
                "match_uncertain": True,
                "confidence": 0.4,
            }
            for dr in payload["old_drs"]
        ]
        return {"rows": rows}, 1


def test_rm1_1_payload_carries_no_deterministic_index_keys() -> None:
    from epoch_switch.corpus.corpus_bundle import extract_corpus

    bundle = extract_corpus(DOCUMENT_SOURCES, REPO_ROOT)
    fake = FakeLLM()
    agent = BlindExposureMatcherAgent(llm=fake)
    result = agent.match(bundle=bundle)
    assert isinstance(result, RM11Result)
    for call in fake.calls:
        payload = json.loads(call["user"])
        assert _no_forbidden_keys(payload), f"forbidden key leaked into RM1.1 payload: {payload}"


def test_rm1_1_produces_one_row_per_old_dr() -> None:
    from epoch_switch.corpus.corpus_bundle import extract_corpus

    bundle = extract_corpus(DOCUMENT_SOURCES, REPO_ROOT)
    old = bundle.standards["E1"].old_drs
    agent = BlindExposureMatcherAgent(llm=FakeLLM())
    result = agent.match(bundle=bundle)
    assert {row.dr_id for row in result.rows} == {dr.dr_id for dr in old}
