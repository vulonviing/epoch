"""RC1.1 -- blind change matcher: the single most important correctness
property in the blind-pass pair is that RC1.1's payload physically contains
no deterministic-candidate keys anywhere (AGENTS.md / plan decision table).
"""
from __future__ import annotations

import json
from pathlib import Path

from epoch_switch.agents.regulation.rc1_change_classifier import BlindChangeMatcherAgent
from epoch_switch.agents.regulation.rc1_change_classifier.rc1_1_result import RC11Result

REPO_ROOT = Path(__file__).resolve().parents[1]

_FORBIDDEN_KEYS = {
    "candidates",
    "basis",
    "title_match_score",
    "non_authoritative_hints",
    "index_candidate_sections",
    "deterministic_index_candidates",
    "deterministic_candidates",
}


def _document_sources(standards: list[str]) -> dict:
    return {
        "baseline_dir": "regulations/esrs/baseline_2025_amended",
        "target_dir": "regulations/esrs/2026",
        "helper_dir": "regulations/comparison_helpers",
        "company_report_dir": "data/siemens_sustainability_2025",
        "standards": standards,
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
                "old_dr_id": dr["dr_id"],
                "new_dr_ids": [],
                "basis": "test",
                "match_uncertain": True,
                "confidence": 0.4,
            }
            for dr in payload["old_drs"]
        ]
        return {"rows": rows}, 1


def test_rc1_1_payload_carries_no_deterministic_candidate_keys() -> None:
    from epoch_switch.corpus.corpus_bundle import extract_corpus, map_candidates

    bundle = map_candidates(extract_corpus(_document_sources(["E1"]), REPO_ROOT))
    fake = FakeLLM()
    agent = BlindChangeMatcherAgent(llm=fake)
    result = agent.match(bundle=bundle)
    assert isinstance(result, RC11Result)
    for call in fake.calls:
        payload = json.loads(call["user"])
        assert _no_forbidden_keys(payload), f"forbidden key leaked into RC1.1 payload: {payload}"


def test_rc1_1_batches_are_scoped_to_their_own_standard() -> None:
    from epoch_switch.corpus.corpus_bundle import extract_corpus, map_candidates

    bundle = map_candidates(extract_corpus(_document_sources(["E2", "E5"]), REPO_ROOT))
    agent = BlindChangeMatcherAgent(llm=FakeLLM())
    result = agent.match(bundle=bundle)
    standards_seen = {row.standard for row in result.rows}
    assert standards_seen == {"E2", "E5"}
